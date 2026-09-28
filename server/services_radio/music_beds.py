import json
import random
import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config.settings import settings
from services import log_service

BED_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
BED_EXTENSIONS = {".mp3": "audio/mpeg", ".opus": "audio/ogg", ".ogg": "audio/ogg", ".webm": "audio/webm",
                  ".m4a": "audio/mp4"}
TYPE_ALIASES = {
    "news": {"news", "bulletin"},
    "city": {"city", "weather"},
    "local": {"local", "gigs", "events", "places"},
    "community": {"community", "shoutouts"},
    "trivia": {"trivia", "feature", "features"},
}
FALLBACK_TYPES = {"feature", "features", "any", "general"}
MANIFEST_RECHECK_S = 30.0
RECENT_PER_SESSION = 4
MAX_SESSIONS = 2000
DEFAULT_BED_GAIN = 0.35


@dataclass(frozen=True)
class MusicBed:
    bed_id: str
    path: Path
    opus_path: Optional[Path] = None
    title: str = ""
    moods: tuple = ()
    segment_types: frozenset = field(default_factory=frozenset)
    duration_s: float = 0.0
    loudness_lufs: Optional[float] = None
    loop_start_s: float = 0.0
    loop_end_s: float = 0.0

    @property
    def media_type(self) -> str:
        return BED_EXTENSIONS.get(self.path.suffix.lower(), "application/octet-stream")

    def file_for(self, fmt: Optional[str]) -> tuple[Path, str]:
        if fmt == "opus" and self.opus_path is not None:
            return self.opus_path, BED_EXTENSIONS.get(self.opus_path.suffix.lower(), "audio/ogg")
        return self.path, self.media_type

    def gain(self, target_lufs: float) -> float:
        if self.loudness_lufs is None:
            return DEFAULT_BED_GAIN
        return round(max(0.05, min(1.0, 10 ** ((target_lufs - self.loudness_lufs) / 20.0))), 3)

    def payload(self, target_lufs: float) -> dict:
        loop_end = self.loop_end_s if self.loop_end_s > self.loop_start_s else self.duration_s
        return {
            "id": self.bed_id,
            "url": f"/api/music-beds/{self.bed_id}",
            "url_opus": f"/api/music-beds/{self.bed_id}?format=opus" if self.opus_path is not None else None,
            "title": self.title,
            "loop_start_s": round(self.loop_start_s, 6),
            "loop_end_s": round(loop_end, 6) if loop_end else 0,
            "gain": self.gain(target_lufs),
        }


def _float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bed_file(root: Path, name, max_bytes: int) -> Optional[Path]:
    if not isinstance(name, str) or not name:
        return None
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(root) or candidate.suffix.lower() not in BED_EXTENSIONS:
        return None
    try:
        if not candidate.is_file() or candidate.stat().st_size > max_bytes:
            return None
    except OSError:
        return None
    return candidate


def _loop_seconds(entry: dict, sample_key: str, seconds_key: str) -> float:
    rate = _float(entry.get("sample_rate"))
    sample = entry.get(sample_key)
    if rate > 0 and isinstance(sample, (int, float)) and not isinstance(sample, bool) and sample >= 0:
        return float(sample) / rate
    return max(0.0, _float(entry.get(seconds_key)))


def parse_manifest(data, base_dir: Path, max_bytes: int) -> list[MusicBed]:
    entries = data.get("beds") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return []
    root = base_dir.resolve()
    beds = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        bed_id = str(entry.get("id") or "")
        if not BED_ID.fullmatch(bed_id) or bed_id in seen:
            continue
        candidate = _bed_file(root, entry.get("file"), max_bytes)
        opus = _bed_file(root, entry.get("file_opus"), max_bytes)
        if candidate is None:
            candidate, opus = opus, None
        if candidate is None:
            continue
        loudness = entry.get("loudness_lufs")
        beds.append(MusicBed(
            bed_id=bed_id,
            path=candidate,
            opus_path=opus,
            title=str(entry.get("title") or "")[:120],
            moods=tuple(str(m).lower() for m in (entry.get("moods") or []) if isinstance(m, str))[:12],
            segment_types=frozenset(str(t).lower() for t in (entry.get("segment_types") or []) if isinstance(t, str)),
            duration_s=max(0.0, _float(entry.get("duration_s"))),
            loudness_lufs=None if loudness is None else _float(loudness, -14.0),
            loop_start_s=_loop_seconds(entry, "loop_start_sample", "loop_start_s"),
            loop_end_s=_loop_seconds(entry, "loop_end_sample", "loop_end_s"),
        ))
        seen.add(bed_id)
    return beds


class MusicBedLibrary:
    def __init__(self, base_dir: Optional[Path] = None, max_bytes: Optional[int] = None):
        self.base_dir = Path(base_dir or settings.MUSIC_BEDS_DIR)
        self.max_bytes = max_bytes or settings.RADIO_BED_MAX_BYTES
        self._beds: dict[str, MusicBed] = {}
        self._mtime: Optional[float] = None
        self._checked_at = 0.0
        self._recent: "OrderedDict[str, deque]" = OrderedDict()

    @property
    def manifest_path(self) -> Path:
        return self.base_dir / "manifest.json"

    def _refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._checked_at < MANIFEST_RECHECK_S:
            return
        self._checked_at = now
        try:
            mtime = self.manifest_path.stat().st_mtime
        except OSError:
            if self._beds:
                log_service.announcer("[RADIO] Music bed manifest gone - talk breaks run without beds")
            self._beds, self._mtime = {}, None
            return
        if mtime == self._mtime and not force:
            return
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log_service.warning(f"[RADIO] Music bed manifest unreadable: {type(e).__name__}: {e}")
            self._beds, self._mtime = {}, mtime
            return
        self._beds = {bed.bed_id: bed for bed in parse_manifest(data, self.base_dir, self.max_bytes)}
        self._mtime = mtime
        log_service.announcer(f"[RADIO] Music beds loaded: {len(self._beds)}")

    def beds(self) -> list[MusicBed]:
        self._refresh()
        return list(self._beds.values())

    def get(self, bed_id: str) -> Optional[MusicBed]:
        if not BED_ID.fullmatch(bed_id or ""):
            return None
        self._refresh()
        return self._beds.get(bed_id)

    def pick(self, kind: str, session_id: Optional[str], moods: tuple = ()) -> Optional[MusicBed]:
        if not settings.RADIO_BEDS_ENABLED:
            return None
        beds = self.beds()
        if not beds:
            return None
        wanted = TYPE_ALIASES.get(kind, {kind})
        matching = [bed for bed in beds if bed.segment_types & wanted]
        if not matching:
            matching = [bed for bed in beds if bed.segment_types & FALLBACK_TYPES or not bed.segment_types]
        if not matching:
            return None
        recent = self._recent.get(session_id or "") or deque(maxlen=RECENT_PER_SESSION)
        wanted_moods = {m.lower() for m in moods}

        def rank(bed: MusicBed) -> tuple:
            used = list(recent).index(bed.bed_id) + 1 if bed.bed_id in recent else 0
            mood_hits = len(wanted_moods & set(bed.moods))
            return used, -mood_hits, random.random()

        chosen = min(matching, key=rank)
        recent = deque((b for b in recent if b != chosen.bed_id), maxlen=RECENT_PER_SESSION)
        recent.append(chosen.bed_id)
        key = session_id or ""
        self._recent.pop(key, None)
        self._recent[key] = recent
        while len(self._recent) > MAX_SESSIONS:
            self._recent.popitem(last=False)
        return chosen


music_beds = MusicBedLibrary()
