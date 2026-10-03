import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from pydub import AudioSegment

from config.settings import settings
from services import log_service

VOICES = ("station", "hosts")
EDGES = ("in", "out")
EXTENSIONS = {".ogg", ".wav", ".flac", ".mp3"}
FILLER_TTS_TYPES = {"impulse", "interlude"}
FRAME_MS = 10
SPEECH_FLOOR_DB = 35.0


@dataclass
class Blip:
    audio: AudioSegment
    attack_ms: int
    release_ms: int


_library: Optional[Dict[Tuple[str, str], List[Blip]]] = None


def envelope_db(audio: AudioSegment) -> np.ndarray:
    samples = np.array(audio.get_array_of_samples(), dtype=np.float32).reshape(-1, audio.channels).mean(axis=1)
    samples /= float(1 << (8 * audio.sample_width - 1))
    hop = max(1, int(audio.frame_rate * FRAME_MS / 1000))
    frames = samples[:len(samples) // hop * hop].reshape(-1, hop)
    if frames.size == 0:
        return np.full(1, -120.0)
    return 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)


def _blip(audio: AudioSegment) -> Blip:
    env = envelope_db(audio)
    peak = int(env.argmax())
    loud = np.nonzero(env >= env[peak] - settings.BLIPS_RELEASE_DB)[0]
    release = int(loud[-1]) + 1 if loud.size else peak + 1
    return Blip(audio, peak * FRAME_MS, min(len(audio), release * FRAME_MS))


def as_blip(audio: AudioSegment) -> Optional[Blip]:
    audio = audio.set_sample_width(2)
    if len(audio) == 0 or audio.max_dBFS == float("-inf"):
        return None
    return _blip(audio.apply_gain(settings.BLIPS_PEAK_DBFS - audio.max_dBFS))


def _load(path: Path) -> Optional[Blip]:
    try:
        return as_blip(AudioSegment.from_file(path))
    except Exception as e:
        log_service.warning(f"[BLIPS] {path.name} unreadable: {type(e).__name__}: {e}")
        return None


def library() -> Dict[Tuple[str, str], List[Blip]]:
    global _library
    if _library is None:
        loaded: Dict[Tuple[str, str], List[Blip]] = {}
        for voice in VOICES:
            for edge in EDGES:
                folder = settings.BLIPS_DIR / voice / edge
                files = sorted(p for p in folder.iterdir()
                               if p.is_file() and p.suffix.lower() in EXTENSIONS) if folder.is_dir() else []
                loaded[(voice, edge)] = [blip for blip in map(_load, files) if blip is not None]
        _library = loaded
        log_service.system("Blips: " + ", ".join(f"{voice} {edge} {len(loaded[(voice, edge)])}"
                                                 for voice in VOICES for edge in EDGES))
    return _library


def voice_for(tts_type: str) -> Optional[str]:
    if not settings.BLIPS_ENABLED or tts_type in FILLER_TTS_TYPES:
        return None
    return "hosts"


def pick(voice: Optional[str], edge: str) -> Optional[Blip]:
    if not voice or not settings.BLIPS_ENABLED:
        return None
    options = library().get((voice, edge)) or []
    return random.choice(options) if options else None


def speech_bounds(audio: AudioSegment) -> Tuple[int, int]:
    env = envelope_db(audio)
    voiced = np.nonzero(env >= env.max() - SPEECH_FLOOR_DB)[0]
    if voiced.size == 0 or env.max() < -80:
        return len(audio), len(audio)
    return int(voiced[0]) * FRAME_MS, min(len(audio), (int(voiced[-1]) + 1) * FRAME_MS)


def _like(voice: AudioSegment, audio: AudioSegment) -> AudioSegment:
    return audio.set_frame_rate(voice.frame_rate).set_channels(voice.channels).set_sample_width(voice.sample_width)


def _base(voice: AudioSegment, duration_ms: int) -> AudioSegment:
    return _like(voice, AudioSegment.silent(duration=duration_ms, frame_rate=voice.frame_rate))


def mix_in(blip: Blip, voice: AudioSegment) -> Tuple[AudioSegment, int]:
    lead, _end = speech_bounds(voice)
    voice_at = max(0, blip.release_ms - lead)
    sound = _like(voice, blip.audio)
    base = _base(voice, max(len(sound), voice_at + len(voice)))
    return base.overlay(sound).overlay(voice, position=voice_at), voice_at


def mix_out(voice: AudioSegment, blip: Blip) -> AudioSegment:
    _lead, end = speech_bounds(voice)
    blip_at = max(0, end - blip.attack_ms)
    sound = _like(voice, blip.audio)
    base = _base(voice, max(len(voice), blip_at + len(sound)))
    return base.overlay(voice).overlay(sound, position=blip_at)
