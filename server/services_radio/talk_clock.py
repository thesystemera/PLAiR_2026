import re
import time
from contextvars import ContextVar
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import settings
from database.models import TalkPace
from service_registry import services
from services import log_service
from services.task_utils import spawn

DEPTHS = tuple(settings.SEGMENT_DEPTHS) or ("standard",)
DEFAULT_DEPTH = "standard" if "standard" in DEPTHS else DEPTHS[0]
DEPTH_PARAMETER = {
    "type": "string", "enum": list(DEPTHS),
    "description": "How much the listener wants, judged from their words: " + ", ".join(
        f"{name} (about {seconds} s on air)" for name, seconds in settings.SEGMENT_DEPTHS.items())
    + f". Default {DEFAULT_DEPTH}."}

segment_depth: ContextVar[Optional[str]] = ContextVar("segment_depth", default=None)


_TAG = re.compile(r"\[[^\]]*\]")
PACE_KINDS = ("chat", "announcer", "segment")
PACE_BOUNDS = (1.0, 3.5)
PACE_MIN_WORDS = 25
PACE_MIN_SECONDS = 5.0


class PaceMeter:
    def __init__(self):
        self.pace: dict[str, float] = {}
        self.samples: dict[str, int] = {}
        self._session_maker = None
        self._saved_at = time.monotonic()

    def value(self, kind: str) -> float:
        if settings.TALK_PACE_ADAPTIVE and self.samples.get(kind, 0) >= settings.TALK_PACE_MIN_SAMPLES:
            return self.pace[kind]
        return settings.TALK_WORDS_PER_SECOND

    def note(self, kind: str, words: int, seconds: float) -> None:
        if not settings.TALK_PACE_ADAPTIVE or kind not in PACE_KINDS:
            return
        if words < PACE_MIN_WORDS or seconds < PACE_MIN_SECONDS:
            return
        sample = min(PACE_BOUNDS[1], max(PACE_BOUNDS[0], words / seconds))
        count = self.samples.get(kind, 0)
        weight = max(settings.TALK_PACE_SMOOTHING, 1.0 / (count + 1))
        self.pace[kind] = self.pace.get(kind, sample) + weight * (sample - self.pace.get(kind, sample))
        self.samples[kind] = count + 1
        if self._session_maker is not None and time.monotonic() - self._saved_at >= settings.TALK_PACE_SAVE_S:
            self._saved_at = time.monotonic()
            spawn(self.save(), name="talk_pace_save")

    async def load(self, session_maker) -> None:
        self._session_maker = session_maker
        async with session_maker() as db:
            rows = (await db.execute(select(TalkPace))).scalars().all()
        for row in rows:
            if row.kind in PACE_KINDS:
                self.pace[row.kind], self.samples[row.kind] = row.words_per_second, row.samples
        if rows:
            log_service.system("Talk pace: " + self.summary())

    async def save(self) -> None:
        if self._session_maker is None or not self.pace:
            return
        stmt = pg_insert(TalkPace).values([{"kind": kind, "words_per_second": value,
                                            "samples": self.samples.get(kind, 0)}
                                           for kind, value in self.pace.items()])
        stmt = stmt.on_conflict_do_update(index_elements=["kind"], set_={
            "words_per_second": stmt.excluded.words_per_second, "samples": stmt.excluded.samples,
            "updated_at": stmt.excluded.updated_at})
        try:
            async with self._session_maker() as db:
                await db.execute(stmt)
                await db.commit()
        except Exception as e:
            log_service.warning(f"Talk pace: could not save ({type(e).__name__}: {e})")
            return
        log_service.system("Talk pace: " + self.summary())

    def summary(self) -> str:
        return ", ".join(f"{kind} {self.pace[kind]:.2f} words/s ({self.samples.get(kind, 0)} streams)"
                         for kind in PACE_KINDS if kind in self.pace) or "no measurements yet"


meter = PaceMeter()


def pace(kind: str = "segment") -> float:
    return meter.value(kind)


def words_for(seconds: float, kind: str = "segment") -> int:
    return int(seconds * pace(kind))


def spoken_words(script: str) -> int:
    from services_radio.dj_content_bank import spoken_text
    return len(_TAG.sub(" ", spoken_text(script or "")).split())


def depth() -> str:
    chosen = segment_depth.get()
    return chosen if chosen in DEPTHS else DEFAULT_DEPTH


LINE_WORDS = 12
DEPTH_STYLE = {
    "brief": "Only the headline facts, then out.",
    "detailed": "Go through the material properly: every item worth airing gets its own exchange, with its "
                "specifics (names, numbers, places, what happens next).",
}


def length_line() -> str:
    chosen = depth()
    seconds = settings.SEGMENT_DEPTHS[chosen]
    words = words_for(seconds)
    return (f"LENGTH: the listener asked for the {chosen} version: {seconds} seconds on air. HARD LIMIT: no more "
            f"than {words} spoken words in total across both hosts (tags and cues don't count) - about "
            f"{max(2, round(words / LINE_WORDS))} short host lines of around {LINE_WORDS} words each. Count as you "
            f"write and end the segment when you get there. The example dialogue above shows the style, not the "
            f"length. {DEPTH_STYLE.get(chosen, '')}".strip())


def clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


def room(seconds: float) -> str:
    return f"At the pace you talk, that is room for about {words_for(seconds, 'chat')} spoken words in total."


async def vocals_at_s(track_id: Optional[str]) -> Optional[float]:
    orchestrator = services.orchestrator
    if not track_id or track_id == "N/A" or orchestrator is None:
        return None
    timing = await orchestrator.lyrics.load_timestamps(track_id)
    if not timing or (timing.get("alignment_score") or 0) < settings.TALK_ALIGNMENT_MIN:
        return None
    starts = [line.get("start") for line in (timing or {}).get("lyrics") or []
              if isinstance(line.get("start"), (int, float))]
    return float(min(starts)) if starts else None


async def started_note(track_id: Optional[str]) -> dict:
    vocals = await vocals_at_s(track_id)
    if vocals is None:
        return {}
    if vocals < settings.TALK_INTRO_MIN_S:
        return {"vocals_start_s": round(vocals, 1),
                "on_air": "The song has just started and its vocals come in almost at once: a line at most, or let "
                          "it play."}
    return {"vocals_start_s": round(vocals, 1),
            "on_air": f"The song has just started; its vocals come in about {int(vocals)} seconds in. {room(vocals)} "
                      "That counts the line you already said with this call: say only what still fits, or stop "
                      "there and let the song play."}
