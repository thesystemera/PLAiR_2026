from contextvars import ContextVar
from typing import Optional

from config import settings
from service_registry import services

DEPTHS = tuple(settings.SEGMENT_DEPTHS) or ("standard",)
DEFAULT_DEPTH = "standard" if "standard" in DEPTHS else DEPTHS[0]
DEPTH_PARAMETER = {
    "type": "string", "enum": list(DEPTHS),
    "description": "How much the listener wants, judged from their words: " + ", ".join(
        f"{name} (about {seconds} s on air)" for name, seconds in settings.SEGMENT_DEPTHS.items())
    + f". Default {DEFAULT_DEPTH}."}

segment_depth: ContextVar[Optional[str]] = ContextVar("segment_depth", default=None)


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
    words = int(seconds * settings.TALK_WORDS_PER_SECOND)
    return (f"LENGTH: the listener asked for the {chosen} version: {seconds} seconds on air. HARD LIMIT: no more "
            f"than {words} spoken words in total across both hosts (tags and cues don't count) - about "
            f"{max(2, round(words / LINE_WORDS))} short host lines of around {LINE_WORDS} words each. Count as you "
            f"write and end the segment when you get there. The example dialogue above shows the style, not the "
            f"length. {DEPTH_STYLE.get(chosen, '')}".strip())


def clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


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
            "on_air": f"The song has just started; its vocals come in about {int(vocals)} seconds in. Anything you "
                      "say now goes out over its intro, so land it before the vocals."}
