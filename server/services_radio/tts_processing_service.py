import numpy as np
import soundfile as sf
from pydub import AudioSegment
from pedalboard import Pedalboard, Reverb, Gain, HighShelfFilter, LowShelfFilter  # type: ignore
from noise import pnoise1
from typing import Optional, Union
import io

from config.settings import settings
from services import log_service

STATION_PAD_MS = 110

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
            speaker: Optional[str] = None
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

        if audio.channels == 1:
            audio = audio.set_channels(2)

        FADE_DURATION = min(100, len(audio))
        audio = audio.fade_in(FADE_DURATION).fade_out(FADE_DURATION)

        SEGMENT_LENGTH_MS = 200
        CROSSFADE_RATIO = 0.5
        CROSSFADE_MS = int(SEGMENT_LENGTH_MS * CROSSFADE_RATIO)
        NOISE_SCALE = 0.3
        VARIATION_AMOUNT = 0.1

        PAN_NOISE_SCALE = 0.02
        BASE_PAN_POSITIONS = {
            'jess': -0.1,
            'leo': 0.1,
            'computer': 0
        }
        PAN_VARIATION = 0.08

        effective_segment_length = SEGMENT_LENGTH_MS - CROSSFADE_MS
        num_segments = -(-len(audio) // effective_segment_length)

        def normalize(values):
            span = np.max(values) - np.min(values)
            if span <= 0:
                return np.zeros_like(values)
            return (values - np.min(values)) / span * 2 - 1

        mix_noise_values = normalize(np.array([pnoise1(i * NOISE_SCALE, octaves=1) for i in range(num_segments)]))

        pan_noise_values = normalize(np.array(
            [pnoise1(i * PAN_NOISE_SCALE, octaves=2, persistence=0.3, base=42) for i in range(num_segments)]))

        base_values = np.full(num_segments, audio_process_mix)
        ramp_length = num_segments // 3
        if ramp_length > 0:
            if previous_segment_end_mix is not None:
                ramp = np.cos(np.linspace(np.pi, 2 * np.pi, ramp_length)) * 0.5 + 0.5
                base_values[:ramp_length] = previous_segment_end_mix + (
                        audio_process_mix - previous_segment_end_mix) * ramp

            if next_segment_start_mix is not None:
                ramp = np.cos(np.linspace(0, np.pi, ramp_length)) * 0.5 + 0.5
                base_values[-ramp_length:] = audio_process_mix + (next_segment_start_mix - audio_process_mix) * ramp

        variation_range = min(VARIATION_AMOUNT, np.min(base_values) * 0.5, (1 - np.max(base_values)) * 0.5)
        mix_values = base_values + (mix_noise_values * variation_range)
        mix_values = np.clip(mix_values, 0.0, 1.0)

        base_pan = BASE_PAN_POSITIONS.get(speaker, 0) if speaker is not None else 0
        pan_values = base_pan + (pan_noise_values * PAN_VARIATION)

        processed_segments = []
        for i in range(num_segments):
            start_ms = i * effective_segment_length
            end_ms = min(start_ms + SEGMENT_LENGTH_MS, len(audio))
            segment = audio[start_ms:end_ms]

            samples = np.array(segment.get_array_of_samples()).astype(np.float32) / 32768.0  # type: ignore
            if segment.channels == 2:  # type: ignore
                samples = samples.reshape((-1, 2))  # type: ignore

            segment_mix = mix_values[i]
            pan_position = pan_values[i]

            if samples.shape[1] == 2:
                left_gain = np.cos((pan_position + 1) * np.pi / 4)
                right_gain = np.sin((pan_position + 1) * np.pi / 4)
                samples[:, 0] *= left_gain
                samples[:, 1] *= right_gain

            board = Pedalboard()

            min_gain_db = settings.AUDIO_EFFECT_CONFIG['global']['gain']['min_gain_db']
            max_gain_db = settings.AUDIO_EFFECT_CONFIG['global']['gain']['max_gain_db']
            gain_db = min_gain_db + (max_gain_db - min_gain_db) * segment_mix
            board.append(Gain(gain_db=gain_db))

            max_cut = settings.AUDIO_EFFECT_CONFIG['global']['eq']['high_shelf_cut_max']
            high_shelf_cut = max_cut * segment_mix
            board.append(HighShelfFilter(cutoff_frequency_hz=5000, gain_db=high_shelf_cut))

            max_boost = settings.AUDIO_EFFECT_CONFIG['global']['eq']['low_shelf_boost_max']
            low_shelf_boost = max_boost * (1 - segment_mix)
            board.append(LowShelfFilter(cutoff_frequency_hz=100, gain_db=low_shelf_boost))

            reverb_settings = settings.AUDIO_EFFECT_CONFIG['global']['reverb']
            reverb_wet_level = reverb_settings['wet_level'] * segment_mix
            reverb_dry_level = reverb_settings['dry_level'] * (1 - segment_mix)
            board.append(Reverb(
                room_size=reverb_settings['room_size'],
                damping=reverb_settings['damping'],
                wet_level=reverb_wet_level,
                dry_level=reverb_dry_level
            ))

            processed_segment = board(samples, sample_rate=segment.frame_rate)  # type: ignore

            if processed_segment.size == 0:
                log_service.error("Processing returned empty segment")
                return AudioSegment.empty()

            max_abs_value = np.max(np.abs(processed_segment))
            if max_abs_value > 1.0:
                processed_segment = processed_segment / max_abs_value

            processed_segment = (processed_segment * 32767).astype(np.int16)
            processed_segment = AudioSegment(
                processed_segment.tobytes(),  # type: ignore
                frame_rate=segment.frame_rate,  # type: ignore
                sample_width=2,
                channels=2
            )

            processed_segments.append(processed_segment)

        final_audio = processed_segments[0]
        for segment in processed_segments[1:]:
            segment_length = len(segment)
            previous_length = len(final_audio)
            crossfade_ms = min(segment_length, previous_length, CROSSFADE_MS)
            final_audio = final_audio.append(segment, crossfade=crossfade_ms)

        log_service.detail(f"Audio processing complete. Duration: {len(final_audio)}ms", "tts_processing")
        return final_audio
