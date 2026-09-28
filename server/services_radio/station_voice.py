import asyncio
import hashlib
import json
import os
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional

import numpy as np
import soundfile as sf
from pydub import AudioSegment

from config.settings import settings
from services import log_service
from services import usage_tracking

VOICE = "station"
INDEX_FILE = "index.json"
AUDIO_CACHE_MAX = 256
TRIM_THRESHOLD_DB = -42.0
TRIM_PAD_MS = 12
TRIM_FADE_MS = 6
MIN_PART_S = 0.12
TOKENS_PER_AUDIO_S = 86
SLUG = re.compile(r"[^a-z0-9]+")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def voice_settings() -> dict:
    return settings.VOICE_PREFERENCES[VOICE]


def clip_key(text: str) -> str:
    voice = voice_settings()
    raw = f"{voice['orpheus_voice']}|{voice['temperature']}|{settings.STATION_VOICE_VERSION}|{normalize_text(text)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def expected_max_seconds(text: str) -> float:
    words = max(1, len(re.findall(r"[A-Za-z0-9']+", text)))
    return 1.2 + 0.55 * words


def expected_min_seconds(text: str) -> float:
    words = max(1, len(re.findall(r"[A-Za-z0-9']+", text)))
    return max(MIN_PART_S, 0.12 * words)


def trim_silence(samples: np.ndarray, rate: int) -> np.ndarray:
    if samples.size == 0:
        return samples
    peak = float(np.max(np.abs(samples)))
    if peak <= 0:
        return samples[:0]
    window = max(1, int(rate * 0.01))
    frames = len(samples) // window
    if frames == 0:
        return samples
    rms = np.sqrt(np.mean(samples[:frames * window].reshape(frames, window) ** 2, axis=1))
    level = 20 * np.log10(rms / peak + 1e-12)
    loud = np.nonzero(level > TRIM_THRESHOLD_DB)[0]
    if loud.size == 0:
        return samples[:0]
    pad = int(rate * TRIM_PAD_MS / 1000)
    start = max(0, loud[0] * window - pad)
    end = min(len(samples), (loud[-1] + 1) * window + pad)
    trimmed = samples[start:end].astype(np.float32).copy()
    fade = min(len(trimmed) // 2, int(rate * TRIM_FADE_MS / 1000))
    if fade > 0:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        trimmed[:fade] *= ramp
        trimmed[-fade:] *= ramp[::-1]
    return trimmed


def validate_take(text: str, samples: np.ndarray, rate: int) -> Optional[str]:
    duration = len(samples) / rate if rate else 0.0
    if duration < expected_min_seconds(text):
        return f"too short ({duration:.2f}s)"
    if duration > expected_max_seconds(text):
        return f"too long ({duration:.2f}s)"
    if float(np.max(np.abs(samples))) < 0.02:
        return "silent"
    return None


@dataclass
class StationClip:
    key: str
    text: str
    category: str
    file: str
    duration_s: float
    city: Optional[str] = None
    created_at: float = 0.0
    seed: Optional[int] = None

    def to_dict(self) -> dict:
        return {"key": self.key, "text": self.text, "category": self.category, "file": self.file,
                "duration_s": round(self.duration_s, 3), "city": self.city, "created_at": self.created_at,
                "seed": self.seed}


class StationClipStore:
    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = Path(base_dir or settings.STATION_AUDIO_DIR)
        self._clips: Dict[str, StationClip] = {}
        self._loaded = False
        self._audio: "OrderedDict[str, AudioSegment]" = OrderedDict()

    @property
    def voice_dir(self) -> Path:
        return self.base_dir / voice_settings()["orpheus_voice"]

    @property
    def index_path(self) -> Path:
        return self.voice_dir / INDEX_FILE

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        clips = {}
        for entry in (data.get("clips") or []) if isinstance(data, dict) else []:
            try:
                clip = StationClip(key=str(entry["key"]), text=str(entry["text"]), category=str(entry["category"]),
                                   file=str(entry["file"]), duration_s=float(entry.get("duration_s") or 0.0),
                                   city=entry.get("city"), created_at=float(entry.get("created_at") or 0.0),
                                   seed=entry.get("seed"))
            except (KeyError, TypeError, ValueError):
                continue
            if clip.key == clip_key(clip.text) and (self.voice_dir / clip.file).is_file():
                clips[clip.key] = clip
        self._clips = clips

    def reload(self):
        self._loaded = False
        self._audio.clear()
        self._load()

    def _save_index(self):
        self.voice_dir.mkdir(parents=True, exist_ok=True)
        payload = {"voice": voice_settings()["orpheus_voice"], "version": settings.STATION_VOICE_VERSION,
                   "clips": [clip.to_dict() for clip in sorted(list(self._clips.values()), key=lambda c: c.text)]}
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        os.replace(tmp, self.index_path)

    def get(self, text: str) -> Optional[StationClip]:
        self._load()
        clip = self._clips.get(clip_key(text))
        if clip is not None and not (self.voice_dir / clip.file).is_file():
            self._clips.pop(clip.key, None)
            self._audio.pop(clip.key, None)
            return None
        return clip

    def has(self, text: str) -> bool:
        return self.get(text) is not None

    def clips(self, category: Optional[str] = None) -> List[StationClip]:
        self._load()
        return [clip for clip in list(self._clips.values()) if category is None or clip.category == category]

    def missing(self, texts) -> List[str]:
        return [text for text in texts if not self.has(text)]

    def save(self, text: str, category: str, samples: np.ndarray, rate: int, city: Optional[str] = None,
             seed: Optional[int] = None) -> StationClip:
        self._load()
        key = clip_key(text)
        slug = SLUG.sub("_", normalize_text(text).lower()).strip("_")[:40] or "clip"
        name = f"{category}_{slug}_{key}.flac"
        self.voice_dir.mkdir(parents=True, exist_ok=True)
        sf.write(self.voice_dir / name, samples, rate, subtype="PCM_16")
        clip = StationClip(key=key, text=normalize_text(text), category=category, file=name,
                           duration_s=len(samples) / rate, city=city, created_at=time.time(), seed=seed)
        self._clips[key] = clip
        self._audio.pop(key, None)
        self._save_index()
        return clip

    def audio(self, clip: StationClip) -> Optional[AudioSegment]:
        cached = self._audio.get(clip.key)
        if cached is not None:
            self._audio.move_to_end(clip.key)
            return cached
        try:
            samples, rate = sf.read(self.voice_dir / clip.file, dtype="int16")
        except (OSError, RuntimeError, sf.LibsndfileError) as e:
            log_service.warning(f"[STINGS] Station clip unreadable, dropping {clip.file}: {e}")
            self._clips.pop(clip.key, None)
            return None
        if samples.ndim > 1:
            samples = samples[:, 0]
        segment = AudioSegment(samples.tobytes(), frame_rate=rate, sample_width=2, channels=1)
        self._audio[clip.key] = segment
        while len(self._audio) > AUDIO_CACHE_MAX:
            self._audio.popitem(last=False)
        return segment


@dataclass(order=True)
class _RenderJob:
    priority: int
    order: int
    text: str = field(compare=False)
    category: str = field(compare=False)
    city: Optional[str] = field(compare=False, default=None)


class StationRenderer:
    def __init__(self, store: StationClipStore, generation_service=None, clock=time.time,
                 verifier: Optional[Callable[[str, np.ndarray, int], Awaitable[Optional[bool]]]] = None):
        self.store = store
        self.generation = generation_service
        self.verifier = verifier
        self.clock = clock
        self._jobs: Dict[str, _RenderJob] = {}
        self._order = 0
        self._wake = asyncio.Event()
        self._failures: Dict[str, int] = {}
        self._task: Optional[asyncio.Task] = None
        self._started_at = 0.0
        self.rendered = 0
        self.gpu_seconds = 0.0

    def pending(self) -> int:
        return len(self._jobs)

    def request(self, text: str, category: str, city: Optional[str] = None, urgent: bool = False) -> bool:
        text = normalize_text(text)
        if not text or self.store.has(text):
            return False
        if self._failures.get(clip_key(text), 0) >= settings.STINGS_RENDER_MAX_ATTEMPTS:
            return False
        key = clip_key(text)
        existing = self._jobs.get(key)
        priority = 0 if urgent else 1
        if existing is not None:
            if priority < existing.priority:
                existing.priority = priority
            return True
        self._order += 1
        self._jobs[key] = _RenderJob(priority, self._order, text, category, city)
        self._wake.set()
        return True

    def start(self, delay_s: float = 0.0):
        if self._task is None or self._task.done():
            from services.task_utils import spawn
            self._started_at = self.clock() + delay_s
            self._task = spawn(self._loop(delay_s), name="station-voice-renderer")

    async def stop(self):
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.wait({self._task}, timeout=2.0)

    async def _loop(self, delay_s: float):
        if delay_s > 0:
            await asyncio.sleep(delay_s)
        usage_tracking.bind(usage_tracking.system_subject("station_voice"))
        while True:
            if not self._jobs:
                self._wake.clear()
                await self._wake.wait()
                continue
            job = min(self._jobs.values())
            try:
                await self.render(job.text, job.category, job.city)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_service.warning(f"[STINGS] Station voice render failed for '{job.text}': {type(e).__name__}: {e}")
            finally:
                self._jobs.pop(clip_key(job.text), None)
            await asyncio.sleep(settings.STINGS_PRERENDER_SPACING_S)

    async def render(self, text: str, category: str, city: Optional[str] = None) -> Optional[StationClip]:
        if self.generation is None:
            return None
        existing = self.store.get(text)
        if existing is not None:
            return existing
        key = clip_key(text)
        attempts = self._failures.get(key, 0)
        while attempts < settings.STINGS_RENDER_MAX_ATTEMPTS:
            seed = settings.STATION_VOICE_SEED + attempts
            options = dict(voice_settings())
            options["seed"] = seed
            options["max_tokens"] = int(expected_max_seconds(text) * TOKENS_PER_AUDIO_S) + 40
            async with self.generation.background_slot():
                started = time.perf_counter()
                pcm = await self.generation.generate_pcm(text, options, priority="low",
                                                         feature=f"stings.station_voice.{category}")
                self.gpu_seconds += time.perf_counter() - started
            attempts += 1
            if pcm is None:
                break
            rate = settings.TTS_SAMPLE_RATE
            samples = np.frombuffer(pcm[:len(pcm) - len(pcm) % 2], dtype=np.int16).astype(np.float32) / 32768.0
            samples = trim_silence(samples, rate)
            problem = validate_take(text, samples, rate)
            if problem is None and self.verifier is not None:
                verdict = await self.verifier(text, samples, rate)
                if verdict is False:
                    problem = "heard a different number"
            if problem:
                log_service.warning(f"[STINGS] Station take rejected for '{text}' (seed {seed}): {problem}")
                self._failures[key] = attempts
                continue
            clip = await asyncio.to_thread(self.store.save, text, category, samples, rate, city, seed)
            self.rendered += 1
            self._failures.pop(key, None)
            log_service.tts_generation(f"[STINGS] Station voice cached '{text}' ({clip.duration_s:.2f}s)")
            return clip
        self._failures[key] = max(attempts, self._failures.get(key, 0))
        return None
