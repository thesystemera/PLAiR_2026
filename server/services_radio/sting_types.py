import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
from pydub import AudioSegment

from config.settings import settings
from services_radio import station_ids
from services_radio.sting_library import OUTPUT_RATE

DUCK_RAMP_MS = 140
TAIL_FADE_MS = 220
HIT_BUTTON_S = 1.3
VOICE_OVER_SWEEP_AT_MS = 260
LOGO_VOICE_AT_MS = 380


@dataclass
class StingContext:
    session_id: str
    now: float
    local: Optional[datetime]
    city: Optional[str]
    max_len_s: float
    midtrack: bool
    rng: random.Random
    recent_ids: object = None


@dataclass
class StingRender:
    kind: str
    label: str
    audio: AudioSegment
    marks: List[Tuple[int, Dict]] = field(default_factory=list)
    text: str = ""
    voice_s: float = 0.0
    parts: List[str] = field(default_factory=list)


def voice_intensities() -> Dict:
    return {"jess": None, "leo": None, "computer": None, "station": settings.STATION_PROCESS_MIX}


def to_output(audio: AudioSegment) -> AudioSegment:
    return audio.set_frame_rate(OUTPUT_RATE).set_channels(2).set_sample_width(2)


def trim_to(audio: AudioSegment, max_ms: int) -> AudioSegment:
    if len(audio) <= max_ms:
        return audio
    fade = min(TAIL_FADE_MS, max_ms // 3)
    return audio[:max_ms].fade_out(fade)


def _samples(audio: AudioSegment) -> np.ndarray:
    return np.array(audio.get_array_of_samples(), dtype=np.float32).reshape(-1, 2) / 32768.0


def _segment(samples: np.ndarray) -> AudioSegment:
    pcm = np.round(np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
    return AudioSegment(pcm.tobytes(), frame_rate=OUTPUT_RATE, sample_width=2, channels=2)


def overlay_voice(bed: AudioSegment, voice: AudioSegment, voice_at_ms: int, duck_db: float) -> AudioSegment:
    bed = to_output(bed)
    voice = to_output(voice)
    voice_at_ms = max(0, voice_at_ms)
    total_ms = max(len(bed), voice_at_ms + len(voice))
    total = int(round(total_ms * OUTPUT_RATE / 1000))
    bed_samples = np.zeros((total, 2), dtype=np.float32)
    raw_bed = _samples(bed)
    bed_samples[:len(raw_bed)] = raw_bed[:total]
    gain = np.ones(total, dtype=np.float32)
    duck = 10 ** (duck_db / 20.0)
    start = int(voice_at_ms * OUTPUT_RATE / 1000)
    end = start + int(len(voice) * OUTPUT_RATE / 1000)
    ramp = int(DUCK_RAMP_MS * OUTPUT_RATE / 1000)
    lead = max(0, start - ramp)
    gain[lead:start] = np.linspace(1.0, duck, start - lead)
    gain[start:end] = duck
    tail_end = min(total, end + ramp)
    gain[end:tail_end] = np.linspace(duck, 1.0, max(0, tail_end - end))
    mixed = bed_samples * gain[:, None]
    raw_voice = _samples(voice)
    stop = min(total, start + len(raw_voice))
    mixed[start:stop] += raw_voice[:stop - start]
    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > 0.97:
        mixed *= 0.97 / peak
    return _segment(mixed)


def append_button(voice: AudioSegment, button: AudioSegment, overlap_ms: int = 120) -> AudioSegment:
    voice = to_output(voice)
    button = to_output(button)
    return voice.append(button, crossfade=min(overlap_ms, len(voice) // 2, len(button) // 2))


def voice_marks(voice_at_ms: int, voice_ms: int, total_ms: int) -> List[Tuple[int, Dict]]:
    marks = [(0, {})] if voice_at_ms > 0 else []
    marks.append((voice_at_ms, voice_intensities()))
    if voice_at_ms + voice_ms < total_ms:
        marks.append((voice_at_ms + voice_ms, {}))
    return marks


class StingType:
    kind = ""
    label = ""
    weight = 1.0
    min_window_s = 2.0
    midtrack_ok = False
    voice = False

    def ready(self, kit, ctx: StingContext) -> bool:
        return True

    def build(self, kit, ctx: StingContext) -> Optional[StingRender]:
        raise NotImplementedError


_REGISTRY: Dict[str, StingType] = {}


def register(sting_type: StingType) -> StingType:
    _REGISTRY[sting_type.kind] = sting_type
    return sting_type


def get(kind: str) -> Optional[StingType]:
    return _REGISTRY.get(kind)


def types(disabled: frozenset = frozenset()) -> List[StingType]:
    return [t for kind, t in _REGISTRY.items() if kind not in disabled]


def candidates(disabled: frozenset = frozenset(), midtrack: bool = False) -> List[tuple]:
    return [(t.kind, t.weight, t.min_window_s) for t in types(disabled) if not midtrack or t.midtrack_ok]


def station_line_voice(kit, ctx: StingContext, short_only: bool, max_ms: int) -> Optional[Tuple[AudioSegment, str]]:
    hour = ctx.local.hour if ctx.local is not None else None
    city = station_ids.clean_city(ctx.city)
    picked = station_ids.pick_line(hour, city, ctx.recent_ids, kit.voice_cached, ctx.rng, short_only=short_only)
    if picked is None:
        return None
    line, text = picked
    voice = kit.station_voice(text, category="station_id", city=city if line.needs_city else None,
                              max_ms=max_ms, allow_near=True)
    if voice is None:
        return None
    audio, spoken = voice
    if ctx.recent_ids is not None:
        ctx.recent_ids.append(line.key)
    return audio, spoken


class StationIdSting(StingType):
    kind = "station_id"
    label = "Station ID"
    weight = 3.0
    min_window_s = 2.2
    midtrack_ok = True
    voice = True

    def ready(self, kit, ctx):
        return kit.any_voice_cached("station_id")

    def build(self, kit, ctx):
        max_ms = int(ctx.max_len_s * 1000)
        voice = station_line_voice(kit, ctx, short_only=ctx.midtrack or ctx.max_len_s < 3.0, max_ms=max_ms)
        if voice is None:
            return None
        audio, text = voice
        audio = to_output(audio)
        return StingRender(self.kind, self.label, audio, voice_marks(0, len(audio), len(audio)), text,
                           len(audio) / 1000)


class SweeperSting(StingType):
    kind = "sweeper"
    label = "Sweeper"
    weight = 2.0
    min_window_s = 3.0
    voice = True

    def ready(self, kit, ctx):
        return kit.any_voice_cached("station_id") and bool(kit.library.stings({"riser"}) or kit.library.sfx())

    def build(self, kit, ctx):
        max_ms = int(ctx.max_len_s * 1000)
        voice = station_line_voice(kit, ctx, short_only=True, max_ms=max_ms - 400)
        if voice is None:
            return None
        audio, text = voice
        risers = [s for s in kit.library.stings({"riser"}) if s.hit_s * 1000 >= len(audio) + 200
                  and s.duration_s * 1000 <= max_ms]
        beds = risers if risers and ctx.rng.random() < 0.6 else kit.library.sfx()
        bed_sting = kit.library.pick(beds or risers, ctx.session_id, ctx.rng)
        if bed_sting is None:
            return None
        bed = kit.library.audio(bed_sting)
        if bed is None:
            return None
        if bed_sting.kind == "riser":
            voice_at = max(0, int(bed_sting.hit_s * 1000) - len(audio) - 120)
        else:
            voice_at = VOICE_OVER_SWEEP_AT_MS
        mixed = overlay_voice(bed, audio, voice_at, settings.STINGS_BED_UNDER_VOICE_DB)
        mixed = trim_to(mixed, max_ms)
        return StingRender(self.kind, self.label, mixed, voice_marks(voice_at, len(audio), len(mixed)), text,
                           len(audio) / 1000, [bed_sting.sting_id])


class MusicalSting(StingType):
    kind = "musical"
    label = "Sting"
    weight = 2.0
    min_window_s = 1.5

    def ready(self, kit, ctx):
        return bool(kit.library.stings())

    def build(self, kit, ctx):
        max_ms = int(ctx.max_len_s * 1000)
        pool = [s for s in kit.library.stings({"hit", "ending", "logo", "riser"}) if s.duration_s * 1000 <= max_ms + 1500]
        sting = kit.library.pick(pool, ctx.session_id, ctx.rng)
        if sting is None:
            return None
        audio = kit.library.audio(sting)
        if audio is None:
            return None
        audio = trim_to(to_output(audio), max_ms)
        return StingRender(self.kind, sting.title or self.label, audio, [(0, {})], "", 0.0, [sting.sting_id])


class LogoIdSting(StingType):
    kind = "logo_id"
    label = "Sonic logo"
    weight = 1.5
    min_window_s = 4.0
    voice = True

    def ready(self, kit, ctx):
        return kit.any_voice_cached("station_id") and bool(kit.library.stings({"logo", "hit"}))

    def build(self, kit, ctx):
        max_ms = int(ctx.max_len_s * 1000)
        pool = [s for s in kit.library.stings({"logo", "hit"}) if s.voice_over]
        sting = kit.library.pick(pool, ctx.session_id, ctx.rng)
        if sting is None:
            return None
        bed = kit.library.audio(sting)
        if bed is None:
            return None
        voice = station_line_voice(kit, ctx, short_only=True, max_ms=max_ms - LOGO_VOICE_AT_MS)
        if voice is None:
            return None
        audio, text = voice
        mixed = trim_to(overlay_voice(bed, audio, LOGO_VOICE_AT_MS, settings.STINGS_BED_UNDER_VOICE_DB), max_ms)
        return StingRender(self.kind, self.label, mixed, voice_marks(LOGO_VOICE_AT_MS, len(audio), len(mixed)),
                           text, len(audio) / 1000, [sting.sting_id])


class TimeCheckSting(StingType):
    kind = "time_check"
    label = "Time check"
    weight = 0.0
    min_window_s = 2.8
    midtrack_ok = True
    voice = True

    def ready(self, kit, ctx):
        if ctx.local is None:
            return False
        return kit.clock_ready(ctx.local.hour, ctx.local.minute)

    def build(self, kit, ctx):
        if ctx.local is None:
            return None
        max_ms = int(ctx.max_len_s * 1000)
        voice = kit.clock_voice(ctx.local.hour, ctx.local.minute, ctx.rng)
        if voice is None:
            return None
        audio, reading = voice
        audio = to_output(audio)
        if len(audio) > max_ms:
            return None
        marks = voice_marks(0, len(audio), len(audio))
        pips = kit.library.pips() if ctx.local.minute == 0 else []
        pip_sting = kit.library.pick(pips, ctx.session_id, ctx.rng) if pips else None
        pip_audio = kit.library.audio(pip_sting) if pip_sting is not None else None
        if pip_audio is not None and len(pip_audio) + len(audio) <= max_ms:
            lead = to_output(pip_audio)
            audio = lead.append(audio, crossfade=min(80, len(lead) // 2, len(audio) // 2))
            marks = voice_marks(len(lead) - 80, len(audio) - len(lead) + 80, len(audio))
            return StingRender(self.kind, self.label, audio, marks, reading.spoken, len(audio) / 1000,
                               [pip_sting.sting_id] + reading.texts)
        return StingRender(self.kind, self.label, audio, marks, reading.spoken, len(audio) / 1000, reading.texts)


for _sting_type in (StationIdSting(), SweeperSting(), MusicalSting(), LogoIdSting(), TimeCheckSting()):
    register(_sting_type)
