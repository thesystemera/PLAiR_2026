import json
import os
import random
import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import soundfile as sf
from pydub import AudioSegment

from config.settings import settings
from services import log_service

STING_ID = re.compile(r"[A-Za-z0-9_-]{1,96}")
STING_EXTENSIONS = {".flac", ".wav", ".mp3", ".ogg"}
STING_KINDS = {"hit", "riser", "ending", "logo", "sweep"}
MANIFEST_RECHECK_S = 30.0
MAX_STING_BYTES = 8 * 1024 * 1024
AUDIO_CACHE_MAX = 48
SFX_MAX_S = 6.0
SFX_MIN_S = 0.8
RECENT_PER_SESSION = 6
MAX_SESSIONS = 2000
OUTPUT_RATE = 48000


@dataclass(frozen=True)
class Sting:
    sting_id: str
    path: Path
    kind: str
    title: str = ""
    duration_s: float = 0.0
    hit_s: float = 0.0
    voice_over: bool = False
    family: str = ""
    source: str = "suno"


def _float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_manifest(data, base_dir: Path) -> List[Sting]:
    entries = data.get("stings") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return []
    root = base_dir.resolve()
    stings, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        sting_id = str(entry.get("id") or "")
        kind = str(entry.get("kind") or "").lower()
        name = entry.get("file")
        if not STING_ID.fullmatch(sting_id) or sting_id in seen or kind not in STING_KINDS or not isinstance(name, str):
            continue
        path = (root / name).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() not in STING_EXTENSIONS:
            continue
        try:
            if not path.is_file() or path.stat().st_size > MAX_STING_BYTES:
                continue
        except OSError:
            continue
        duration = max(0.0, _float(entry.get("duration_s")))
        stings.append(Sting(
            sting_id=sting_id, path=path, kind=kind, title=str(entry.get("title") or "")[:120],
            duration_s=duration, hit_s=min(duration, max(0.0, _float(entry.get("hit_s")))),
            voice_over=bool(entry.get("voice_over")), family=str(entry.get("family") or "")[:40],
        ))
        seen.add(sting_id)
    return stings


def read_audio(path: Path) -> Optional[AudioSegment]:
    try:
        samples, rate = sf.read(path, dtype="int16", always_2d=True)
    except (OSError, RuntimeError, sf.LibsndfileError):
        try:
            return AudioSegment.from_file(path).set_frame_rate(OUTPUT_RATE).set_channels(2)
        except Exception as e:
            log_service.warning(f"[STINGS] Unreadable sting audio {path.name}: {type(e).__name__}: {e}")
            return None
    channels = samples.shape[1]
    audio = AudioSegment(samples.tobytes(), frame_rate=rate, sample_width=2, channels=channels)
    if channels == 1:
        audio = audio.set_channels(2)
    return audio.set_frame_rate(OUTPUT_RATE) if rate != OUTPUT_RATE else audio


def _sfx_title(path: Path) -> str:
    try:
        from mutagen.id3 import ID3
        tags = ID3(path)
        frame = tags.get("TIT2")
        return str(frame.text[0]) if frame and frame.text else ""
    except Exception:
        return ""


class StingLibrary:
    def __init__(self, base_dir: Optional[Path] = None, sfx_dir: Optional[Path] = None):
        self.base_dir = Path(base_dir or settings.STINGS_DIR)
        self.sfx_dir = Path(sfx_dir or (settings.AUDIO_EFFECT_DIR / "computer"))
        self._stings: Dict[str, Sting] = {}
        self._mtime: Optional[float] = None
        self._checked_at = 0.0
        self._sfx: Optional[List[Sting]] = None
        self._pips: List[Sting] = []
        self._audio: "OrderedDict[str, AudioSegment]" = OrderedDict()
        self._recent: "OrderedDict[str, deque]" = OrderedDict()

    @property
    def manifest_path(self) -> Path:
        return self.base_dir / "manifest.json"

    def _refresh(self, force: bool = False):
        now = time.monotonic()
        if not force and now - self._checked_at < MANIFEST_RECHECK_S:
            return
        self._checked_at = now
        try:
            mtime = self.manifest_path.stat().st_mtime
        except OSError:
            self._stings, self._mtime = {}, None
            return
        if mtime == self._mtime and not force:
            return
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log_service.warning(f"[STINGS] Sting manifest unreadable: {type(e).__name__}: {e}")
            self._stings, self._mtime = {}, mtime
            return
        self._stings = {s.sting_id: s for s in parse_manifest(data, self.base_dir)}
        self._mtime = mtime
        self._audio.clear()
        log_service.announcer(f"[STINGS] Musical stings loaded: {len(self._stings)}")

    def stings(self, kinds: Optional[set] = None, max_s: Optional[float] = None) -> List[Sting]:
        self._refresh()
        return [s for s in self._stings.values()
                if (kinds is None or s.kind in kinds) and (max_s is None or s.duration_s <= max_s)]

    def scan_sfx(self) -> List[Sting]:
        titles = settings.STINGS_SFX_TITLES
        pips_title = settings.STINGS_SFX_PIPS_TITLE
        found, pips = [], []
        try:
            names = sorted(os.listdir(self.sfx_dir))
        except OSError:
            names = []
        for name in names:
            if not name.lower().endswith(".mp3"):
                continue
            path = self.sfx_dir / name
            title = _sfx_title(path).strip()
            if not title or (title.lower() not in titles and title.lower() != pips_title):
                continue
            try:
                info = sf.info(path)
                duration = float(info.duration)
            except Exception:
                continue
            if not SFX_MIN_S <= duration <= SFX_MAX_S:
                continue
            sting = Sting(sting_id=f"sfx_{path.stem[:36]}", path=path, kind="sweep", title=title,
                          duration_s=duration, hit_s=0.0, voice_over=True, family="sfx", source="sfx")
            (pips if title.lower() == pips_title else found).append(sting)
        self._sfx = found
        self._pips = pips
        log_service.announcer(f"[STINGS] Sweeper beds from the sound-effects cache: {len(found)}")
        return found

    def sfx(self) -> List[Sting]:
        return list(self._sfx or [])

    def pips(self) -> List[Sting]:
        return list(self._pips)

    def sfx_ready(self) -> bool:
        return self._sfx is not None

    def audio(self, sting: Sting) -> Optional[AudioSegment]:
        cached = self._audio.get(sting.sting_id)
        if cached is not None:
            self._audio.move_to_end(sting.sting_id)
            return cached
        audio = read_audio(sting.path)
        if audio is None:
            return None
        if sting.source == "sfx":
            from services_radio.tts_processing_service import loudness_normalize
            audio = loudness_normalize(audio, settings.STINGS_TARGET_LUFS - 2.0, peak_ceiling_db=-3.0)
        self._audio[sting.sting_id] = audio
        while len(self._audio) > AUDIO_CACHE_MAX:
            self._audio.popitem(last=False)
        return audio

    def pick(self, candidates: List[Sting], session_id: Optional[str],
             rng: Optional[random.Random] = None) -> Optional[Sting]:
        if not candidates:
            return None
        rng = rng or random
        recent = self._recent.get(session_id or "") or deque(maxlen=RECENT_PER_SESSION)

        def rank(sting: Sting) -> tuple:
            used = list(recent).index(sting.sting_id) + 1 if sting.sting_id in recent else 0
            return used, rng.random()

        chosen = min(candidates, key=rank)
        recent = deque((s for s in recent if s != chosen.sting_id), maxlen=RECENT_PER_SESSION)
        recent.append(chosen.sting_id)
        key = session_id or ""
        self._recent.pop(key, None)
        self._recent[key] = recent
        while len(self._recent) > MAX_SESSIONS:
            self._recent.popitem(last=False)
        return chosen

    def forget_session(self, session_id: str):
        self._recent.pop(session_id, None)


sting_library = StingLibrary()
