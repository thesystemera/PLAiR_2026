import numpy as np
import random
import soundfile as sf
from pydub import AudioSegment
from pedalboard import Pedalboard, Reverb, Gain, HighShelfFilter, LowShelfFilter  # type: ignore
from noise import pnoise1
import asyncio
from typing import List, Optional, Tuple, Union
import io

from config.settings import settings
from services import log_service

STATION_PAD_MS = 110
PERLIN_REPEAT = 1024
MOTION_CHUNK_MS = 100
MIX_NOISE_PEAK = 0.95
PAN_NOISE_SCALE = 0.02
PAN_NOISE_PEAK = 0.5
AMBIENCE_PAN_SPREAD = 0.4
FADE_FLOOR = 10 ** (-120 / 20)
AUDIBLE_FRAME_MS = 10
AUDIBLE_FLOOR_DBFS = -60.0


def level_sound_effect(audio: AudioSegment) -> AudioSegment:
    if len(audio) == 0 or audio.dBFS == float('-inf'):
        return audio
    gain = min(settings.TTS_SFX_TARGET_DBFS - audio.dBFS, settings.TTS_SFX_MAX_PEAK_DBFS - audio.max_dBFS)
    return audio.apply_gain(gain)


def audible_bounds(audio: AudioSegment) -> Tuple[int, int]:
    hop = int(audio.frame_rate * AUDIBLE_FRAME_MS / 1000)
    samples = np.array(audio.get_array_of_samples(), dtype=np.float32).reshape((-1, audio.channels)).mean(axis=1)
    frames = len(samples) // hop
    if frames == 0:
        return 0, len(audio)
    rms = np.sqrt(np.mean(samples[:frames * hop].reshape(frames, hop) ** 2, axis=1)) / 32768.0
    audible = np.where(20 * np.log10(rms + 1e-9) > AUDIBLE_FLOOR_DBFS)[0]
    if len(audible) == 0:
        return 0, len(audio)
    return int(audible[0]) * AUDIBLE_FRAME_MS, min(len(audio), (int(audible[-1]) + 1) * AUDIBLE_FRAME_MS)


def _ms_frames(ms: float, rate: int) -> int:
    return int(ms * rate / 1000.0)


def _length_ms(frames: int, rate: int) -> int:
    return round(1000 * frames / rate)


def _ms_slice(samples: np.ndarray, rate: int, start_ms: float, end_ms: float) -> np.ndarray:
    length = _length_ms(len(samples), rate)
    start = _ms_frames(min(start_ms, length), rate)
    end = _ms_frames(min(end_ms, length), rate)
    piece = samples[start:end]
    if len(piece) < end - start:
        piece = np.concatenate([piece, np.zeros((end - start - len(piece), samples.shape[1]), dtype=samples.dtype)])
    return piece


def crossfade_join(pieces: List[np.ndarray], rate: int, crossfade_ms: int) -> np.ndarray:
    joined = pieces[0].astype(np.int64)
    for piece in pieces[1:]:
        piece = piece.astype(np.int64)
        joined_ms = _length_ms(len(joined), rate)
        fade_ms = min(_length_ms(len(piece), rate), joined_ms, crossfade_ms)
        if fade_ms <= 0:
            joined = np.concatenate([joined, piece])
            continue
        head = _ms_slice(joined, rate, 0, joined_ms - fade_ms)
        tail = _ms_slice(joined, rate, joined_ms - fade_ms, joined_ms)
        fade_frames = len(tail)
        steps = np.arange(fade_frames, dtype=np.float64)[:, None] / fade_frames
        fade_out = np.floor(tail * (1.0 + (FADE_FLOOR - 1.0) * steps))
        fade_in = np.floor(_ms_slice(piece, rate, 0, fade_ms) * (FADE_FLOOR + (1.0 - FADE_FLOOR) * steps))
        overlap = np.clip(fade_out + fade_in, -32768, 32767).astype(np.int64)
        joined = np.concatenate([head, overlap, _ms_slice(piece, rate, fade_ms, _length_ms(len(piece), rate))])
    return joined.astype(np.int16)

def decode_mp3(source: Union[str, bytes]) -> AudioSegment:
    try:
        samples, sample_rate = sf.read(io.BytesIO(source) if isinstance(source, bytes) else source, dtype="int16")
        channels = 1 if samples.ndim == 1 else samples.shape[1]
        return AudioSegment(data=samples.tobytes(), sample_width=2, frame_rate=sample_rate, channels=channels)
    except (sf.LibsndfileError, RuntimeError):
        return AudioSegment.from_file(io.BytesIO(source) if isinstance(source, bytes) else source, format="mp3")

def _segment_to_float(audio: AudioSegment) -> np.ndarray:
    samples = np.array(audio.get_array_of_samples()).astype(np.float32) / float(1 << (8 * audio.sample_width - 1))
    return samples.reshape((-1, audio.channels))


def _float_to_segment(samples: np.ndarray, frame_rate: int) -> AudioSegment:
    samples = np.clip(samples, -1.0, 1.0)
    pcm = np.round(samples * 32767.0).astype(np.int16)
    return AudioSegment(pcm.tobytes(), frame_rate=frame_rate, sample_width=2, channels=samples.shape[1])


def loudness_normalize(audio: AudioSegment, target_lufs: float, peak_ceiling_db: float = -1.0) -> AudioSegment:
    if len(audio) == 0:
        return audio
    import pyloudnorm as pyln
    samples = _segment_to_float(audio)
    block = min(0.4, max(0.05, len(samples) / audio.frame_rate / 2))
    try:
        loudness = pyln.Meter(audio.frame_rate, block_size=block).integrated_loudness(samples)
    except ValueError:
        loudness = float('-inf')
    if not np.isfinite(loudness):
        return audio
    gain = 10 ** ((target_lufs - loudness) / 20.0)
    peak = float(np.max(np.abs(samples))) * gain
    ceiling = 10 ** (peak_ceiling_db / 20.0)
    if peak > ceiling:
        gain *= ceiling / peak
    return _float_to_segment(samples * gain, audio.frame_rate)


def station_treatment(audio: AudioSegment, config: Optional[dict] = None) -> AudioSegment:
    from pedalboard import Bitcrush, Compressor, Delay, HighpassFilter, LowpassFilter, PeakFilter
    if len(audio) == 0:
        return audio
    cfg = config or settings.AUDIO_EFFECT_CONFIG['station']
    rate = audio.frame_rate
    mono = _segment_to_float(audio).mean(axis=1)
    band = Pedalboard([
        HighpassFilter(cutoff_frequency_hz=cfg['highpass_hz']),
        LowpassFilter(cutoff_frequency_hz=cfg['lowpass_hz']),
        PeakFilter(cutoff_frequency_hz=cfg['presence_hz'], gain_db=cfg['presence_db'], q=0.9),
    ])(mono[None, :], rate)[0]
    t = np.arange(len(band)) / rate
    ring = band * np.sin(2 * np.pi * cfg['ring_mod_hz'] * t)
    voiced = band * (1 - cfg['ring_mod_mix']) + ring * cfg['ring_mod_mix']
    comb = Pedalboard([Delay(delay_seconds=cfg['comb_delay_s'], feedback=cfg['comb_feedback'], mix=1.0)])(
        voiced[None, :].astype(np.float32), rate)[0]
    voiced = voiced * (1 - cfg['comb_mix']) + comb * cfg['comb_mix']
    crushed = Pedalboard([Bitcrush(bit_depth=cfg['crush_bits'])])(voiced[None, :].astype(np.float32), rate)[0]
    voiced = voiced * (1 - cfg['crush_mix']) + crushed * cfg['crush_mix']
    stereo = np.stack([voiced, voiced]).astype(np.float32)
    tail = Pedalboard([
        Compressor(threshold_db=cfg['compressor_threshold_db'], ratio=cfg['compressor_ratio'], attack_ms=2.0,
                   release_ms=90.0),
        Delay(delay_seconds=cfg['slap_delay_s'], feedback=0.0, mix=cfg['slap_mix']),
        Reverb(room_size=cfg['reverb_room_size'], damping=0.6, wet_level=cfg['reverb_wet'],
               dry_level=1.0 - cfg['reverb_wet'], width=cfg['reverb_width']),
    ])
    processed = tail(stereo, rate).T
    peak = float(np.max(np.abs(processed))) if processed.size else 0.0
    if peak > 0.98:
        processed = processed * (0.98 / peak)
    return _float_to_segment(processed, rate)


def motion_chunks(length_ms: int) -> int:
    return -(-length_ms // MOTION_CHUNK_MS)


def movement_curve(chunks: int, mix: float, previous_mix: Optional[float], next_mix: Optional[float]) -> np.ndarray:
    start = mix if previous_mix is None else (previous_mix + mix) / 2
    end = mix if next_mix is None else (mix + next_mix) / 2
    if chunks < 3:
        return np.linspace(start, end, chunks)
    curve = np.full(chunks, mix)
    ramp_length = chunks // 3
    ease = np.cos(np.linspace(np.pi, 2 * np.pi, ramp_length)) * 0.5 + 0.5
    curve[:ramp_length] = start + (mix - start) * ease
    curve[-ramp_length:] = mix + (end - mix) * ease
    return curve


class MotionTrack:
    def __init__(self):
        self.mix_offset = random.uniform(0, PERLIN_REPEAT)
        self.pan_offset = random.uniform(0, PERLIN_REPEAT)
        self._last_end: Optional[asyncio.Future] = None

    def slot(self) -> "MotionSlot":
        previous = self._last_end
        self._last_end = asyncio.get_running_loop().create_future()
        return MotionSlot(self, previous, self._last_end)


class MotionSlot:
    def __init__(self, track: MotionTrack, previous: Optional[asyncio.Future], end: asyncio.Future):
        self.track = track
        self.previous = previous
        self.end = end

    async def noise(self) -> Tuple[float, float, int]:
        start = 0 if self.previous is None else await asyncio.shield(self.previous)
        return self.track.mix_offset, self.track.pan_offset, start

    def advance(self, start: int, chunks: int):
        if not self.end.done():
            self.end.set_result(start + chunks)

    def release(self):
        if self.end.done():
            return
        if self.previous is None:
            self.end.set_result(0)
        elif self.previous.done():
            self.end.set_result(self.previous.result())
        else:
            self.previous.add_done_callback(lambda previous: self.release())


def mic_distance_response(mix: float, voiced: bool, cfg: dict):
    near, far, reference = cfg['near_m'], cfg['far_m'], cfg['reference_m']
    distance = near * (far / near) ** mix
    direct_db = min(-20 * np.log10(distance / reference), cfg['max_near_boost_db'])
    bass_until = cfg['proximity_bass_until_m']
    bass_db = cfg['proximity_bass_db'] * float(np.clip(np.log(bass_until / distance) / np.log(bass_until / near), 0, 1))         if voiced else 0.0
    air_db = cfg['air_loss_db'] * float(np.clip(np.log(distance / reference) / np.log(far / reference), 0, 1))
    return float(direct_db), bass_db, air_db


class AudioProcessingService:
    def __init__(self):
        pass

    def process_station(self, audio: AudioSegment, audio_process_mix: Optional[float] = None) -> AudioSegment:
        if len(audio) == 0:
            return audio
        treated = station_treatment(audio)
        pad = AudioSegment.silent(duration=STATION_PAD_MS, frame_rate=treated.frame_rate).set_channels(treated.channels)
        mix = settings.STATION_PROCESS_MIX if audio_process_mix is None else audio_process_mix
        processed = self.process_audio(pad + treated + pad, mix, speaker='station')
        return loudness_normalize(processed, settings.STATION_VOICE_TARGET_LUFS)

    def process_audio(
            self,
            audio_input,
            audio_process_mix: float = 1.0,
            previous_segment_end_mix: Optional[float] = None,
            next_segment_start_mix: Optional[float] = None,
            speaker: Optional[str] = None,
            noise: Optional[Tuple[float, float, int]] = None
    ) -> AudioSegment:
        log_service.detail(f"Processing audio: Speaker={speaker}, Mix={audio_process_mix:.2f}", "tts_processing")

        if isinstance(audio_input, (str, bytes)):
            audio = decode_mp3(audio_input)
        elif isinstance(audio_input, AudioSegment):
            audio = audio_input
        else:
            raise ValueError("Invalid audio input type")

        if len(audio) == 0:
            return audio

        host_gain_db = settings.VOICE_PREFERENCES.get(speaker, {}).get('gain_db', 0.0)
        if host_gain_db:
            audio = audio.apply_gain(host_gain_db)
        if audio.channels == 1:
            audio = audio.set_channels(2)

        FADE_DURATION = min(100, len(audio))
        audio = audio.fade_in(FADE_DURATION).fade_out(FADE_DURATION)

        SEGMENT_LENGTH_MS = 200
        CROSSFADE_RATIO = 0.5
        CROSSFADE_MS = int(SEGMENT_LENGTH_MS * CROSSFADE_RATIO)
        NOISE_SCALE = 0.3
        VARIATION_AMOUNT = 0.1

        BASE_PAN_POSITIONS = {
            'jess': -0.1,
            'leo': 0.1,
            'computer': 0
        }
        PAN_VARIATION = 0.08

        effective_segment_length = SEGMENT_LENGTH_MS - CROSSFADE_MS
        num_segments = motion_chunks(len(audio))

        mix_offset, pan_offset, noise_start = noise or (
            random.uniform(0, PERLIN_REPEAT), random.uniform(0, PERLIN_REPEAT), 0)
        positions = noise_start + np.arange(num_segments)
        mix_noise_values = np.clip(np.array(
            [pnoise1(mix_offset + i * NOISE_SCALE, octaves=1) for i in positions]) / MIX_NOISE_PEAK, -1.0, 1.0)

        pan_noise_values = np.clip(np.array(
            [pnoise1(pan_offset + i * PAN_NOISE_SCALE, octaves=2, persistence=0.3, base=42)
             for i in positions]) / PAN_NOISE_PEAK, -1.0, 1.0)

        base_values = movement_curve(num_segments, audio_process_mix, previous_segment_end_mix,
                                     next_segment_start_mix)
        variation_range = np.minimum(VARIATION_AMOUNT, np.minimum(base_values, 1 - base_values) * 0.5)
        mix_values = base_values + (mix_noise_values * variation_range)
        mix_values = np.clip(mix_values, 0.0, 1.0)

        if speaker == 'computer' and audio_process_mix <= 0:
            pan_values = np.zeros(num_segments)
        else:
            base_pan = BASE_PAN_POSITIONS.get(speaker, 0) if speaker is not None else 0
            if speaker == 'computer':
                base_pan = random.uniform(-AMBIENCE_PAN_SPREAD, AMBIENCE_PAN_SPREAD)
            pan_values = base_pan + (pan_noise_values * PAN_VARIATION)

        cfg = settings.AUDIO_EFFECT_CONFIG['proximity']
        headroom = 10 ** (-cfg['max_near_boost_db'] / 20)
        voiced = speaker != 'computer'
        rate = audio.frame_rate
        source = np.array(audio.get_array_of_samples(), dtype=np.int16).reshape((-1, 2))
        processed_segments = []
        for i in range(num_segments):
            start_ms = i * effective_segment_length
            end_ms = min(start_ms + SEGMENT_LENGTH_MS, len(audio))
            samples = _ms_slice(source, rate, start_ms, end_ms).astype(np.float32) / 32768.0

            pan_position = pan_values[i]
            samples[:, 0] *= np.cos((pan_position + 1) * np.pi / 4)
            samples[:, 1] *= np.sin((pan_position + 1) * np.pi / 4)

            direct_db, bass_db, air_db = mic_distance_response(mix_values[i], voiced, cfg)
            room = cfg['room']
            board = Pedalboard([
                Gain(gain_db=cfg['output_trim_db'] + cfg['max_near_boost_db']),
                LowShelfFilter(cutoff_frequency_hz=cfg['proximity_bass_hz'], gain_db=bass_db),
                HighShelfFilter(cutoff_frequency_hz=cfg['air_loss_hz'], gain_db=air_db),
                Reverb(room_size=room['room_size'], damping=room['damping'], width=room['width'],
                       wet_level=room['wet_level'] * headroom,
                       dry_level=room['dry_level'] * headroom * 10 ** (direct_db / 20)),
            ])
            processed_segments.append(np.clip(board(samples, sample_rate=rate), -1.0, 1.0))

        mixed = crossfade_join([(segment * 32767).astype(np.int16) for segment in processed_segments],
                               rate, CROSSFADE_MS).astype(np.float32) / 32768.0
        final_audio = _float_to_segment(mixed, rate)
        log_service.detail(f"Audio processing complete. Duration: {len(final_audio)}ms", "tts_processing")
        return final_audio
