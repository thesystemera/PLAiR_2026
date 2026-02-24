import numpy as np
from pydub import AudioSegment
from pedalboard import Pedalboard, Reverb, Gain, HighShelfFilter, LowShelfFilter  # type: ignore
from noise import pnoise1
from typing import Optional
import io

from config.settings import settings
from services import log_service

class AudioProcessingService:
    def __init__(self):
        pass

    def process_audio(
            self,
            audio_input,
            audio_process_mix: float = 1.0,
            previous_segment_end_mix: Optional[float] = None,
            next_segment_start_mix: Optional[float] = None,
            speaker: Optional[str] = None
    ) -> AudioSegment:
        log_service.tts_processing(f"Processing audio: Speaker={speaker}, Mix={audio_process_mix:.2f}")

        if isinstance(audio_input, str):
            audio = AudioSegment.from_file(audio_input, format="mp3")
        elif isinstance(audio_input, bytes):
            audio = AudioSegment.from_file(io.BytesIO(audio_input), format="mp3")
        elif isinstance(audio_input, AudioSegment):
            audio = audio_input
        else:
            raise ValueError("Invalid audio input type")

        if audio.channels == 1:
            audio = audio.set_channels(2)

        FADE_DURATION = 100
        audio = audio.fade_in(FADE_DURATION).fade_out(FADE_DURATION)

        SEGMENT_LENGTH_MS = 200
        CROSSFADE_RATIO = 0.5
        CROSSFADE_MS = int(SEGMENT_LENGTH_MS * CROSSFADE_RATIO)
        NOISE_SCALE = 0.3
        VARIATION_AMOUNT = 0.1

        PAN_NOISE_SCALE = 0.02
        BASE_PAN_POSITIONS = {
            'terry': -0.1,
            'shaquille': 0.1,
            'computer': 0
        }
        PAN_VARIATION = 0.08

        effective_segment_length = SEGMENT_LENGTH_MS - CROSSFADE_MS
        num_segments = (len(audio) // effective_segment_length) + 1

        mix_noise_values = np.array([pnoise1(i * NOISE_SCALE, octaves=1) for i in range(num_segments)])
        mix_noise_values = (mix_noise_values - np.min(mix_noise_values)) / (
                np.max(mix_noise_values) - np.min(mix_noise_values)) * 2 - 1

        pan_noise_values = np.array(
            [pnoise1(i * PAN_NOISE_SCALE, octaves=2, persistence=0.3, base=42) for i in range(num_segments)])
        pan_noise_values = (pan_noise_values - np.min(pan_noise_values)) / (
                np.max(pan_noise_values) - np.min(pan_noise_values)) * 2 - 1

        base_values = np.full(num_segments, audio_process_mix)
        if previous_segment_end_mix is not None or next_segment_start_mix is not None:
            if previous_segment_end_mix is not None:
                ramp_length = num_segments // 3
                ramp = np.cos(np.linspace(np.pi, 2 * np.pi, ramp_length)) * 0.5 + 0.5
                base_values[:ramp_length] = previous_segment_end_mix + (
                        audio_process_mix - previous_segment_end_mix) * ramp

            if next_segment_start_mix is not None:
                ramp_length = num_segments // 3
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

        log_service.tts_processing(f"Audio processing complete. Duration: {len(final_audio)}ms")
        return final_audio