import asyncio
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
import soundfile as sf
import numpy as np
import pyloudnorm as pyln
import librosa
from scipy import signal
from services import log_service
from services.base_service import SingletonService

class AudioMasterService(SingletonService):

    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.target_lufs = -14.0
        self.use_perceptual_weighting = True
        self.master_wet_mix = 0.75
        self._initialized = True

    def configure(self, target_lufs: float = None, use_perceptual_weighting: bool = None, master_wet_mix: float = None):  # type: ignore
        if target_lufs is not None:
            self.target_lufs = target_lufs
        if use_perceptual_weighting is not None:
            self.use_perceptual_weighting = use_perceptual_weighting
        if master_wet_mix is not None:
            self.master_wet_mix = np.clip(master_wet_mix, 0.0, 1.0)

    async def initialize(self):
        mode = "perceptual weighting" if self.use_perceptual_weighting else "raw prominence"
        log_service.upscaling(
            f"Audio Master service initialized (notch mode: {mode}, wet mix: {self.master_wet_mix:.1f})")

    @staticmethod
    def _apply_perceptual_weighting(freqs: np.ndarray, prominences: np.ndarray) -> np.ndarray:
        f1 = 20.60
        f2 = 107.7
        f3 = 737.9
        f4 = 12194.0

        f_safe = np.where(freqs == 0, 1e-6, freqs)
        f_sq = f_safe ** 2

        a_db = (
                20 * np.log10(f4 ** 2 * f_sq ** 2)
                - 20 * np.log10((f_sq + f1 ** 2) * (f_sq + f4 ** 2))
                - 10 * np.log10((f_sq + f2 ** 2) * (f_sq + f3 ** 2))
                + 2.0
        )

        weighted_prominences_db = prominences + a_db
        return 10 ** (weighted_prominences_db / 20.0)

    @staticmethod
    def _apply_safety_rolloff(data: np.ndarray, rate: int) -> np.ndarray:
        nyquist = rate / 2
        cutoff = 18000

        if cutoff >= nyquist:
            return data

        sos = signal.butter(6, cutoff, 'low', fs=rate, output='sos')
        return signal.sosfilt(sos, data, axis=-1)

    @staticmethod
    def _apply_bell_boost(data: np.ndarray, rate: int, freq: float, gain_db: float, q: float = 1.0) -> np.ndarray:
        if abs(gain_db) < 1e-5:
            return data

        w0 = 2 * np.pi * freq / rate
        alpha = np.sin(w0) / (2 * q)
        amp = 10 ** (gain_db / 40)

        b0 = 1 + alpha * amp
        b1 = -2 * np.cos(w0)
        b2 = 1 - alpha * amp
        a0 = 1 + alpha / amp
        a1 = -2 * np.cos(w0)
        a2 = 1 - alpha / amp

        b = np.array([b0, b1, b2]) / a0
        a = np.array([1.0, a1 / a0, a2 / a0])

        return signal.filtfilt(b, a, data, axis=-1)

    @staticmethod
    def _apply_high_shelf(data: np.ndarray, rate: int, freq: float, gain_db: float) -> np.ndarray:
        if abs(gain_db) < 1e-5:
            return data

        w0 = 2 * np.pi * freq / rate
        amp = 10 ** (gain_db / 40)
        q_val = 0.71
        alpha = np.sin(w0) / (2 * q_val)

        b0 = amp * ((amp + 1) + (amp - 1) * np.cos(w0) + 2 * np.sqrt(amp) * alpha)
        b1 = -2 * amp * ((amp - 1) + (amp + 1) * np.cos(w0))
        b2 = amp * ((amp + 1) + (amp - 1) * np.cos(w0) - 2 * np.sqrt(amp) * alpha)
        a0 = (amp + 1) - (amp - 1) * np.cos(w0) + 2 * np.sqrt(amp) * alpha
        a1 = 2 * ((amp - 1) - (amp + 1) * np.cos(w0))
        a2 = (amp + 1) - (amp - 1) * np.cos(w0) - 2 * np.sqrt(amp) * alpha

        b = np.array([b0, b1, b2]) / a0
        a = np.array([1.0, a1 / a0, a2 / a0])

        return signal.filtfilt(b, a, data, axis=-1)

    @staticmethod
    def _soft_clip(data: np.ndarray, threshold: float = 0.95) -> np.ndarray:
        return np.where(
            np.abs(data) > threshold,
            np.sign(data) * (threshold + (1 - threshold) * np.tanh((np.abs(data) - threshold) / (1 - threshold))),
            data
        )

    @staticmethod
    def _analyze_multiband_tonality(avg_spectrum_db: np.ndarray, freqs: np.ndarray) -> Dict[str, Any]:
        body_mask = (freqs >= 300) & (freqs <= 2000)
        pres_mask = (freqs > 2000) & (freqs <= 6000)
        air_mask = (freqs > 6000) & (freqs <= 16000)

        if not bool(np.any(body_mask)) or not bool(np.any(pres_mask)) or not bool(np.any(air_mask)):
            return {}

        body_db = float(np.mean(avg_spectrum_db[body_mask]))
        pres_db = float(np.mean(avg_spectrum_db[pres_mask]))
        air_db = float(np.mean(avg_spectrum_db[air_mask]))

        target_pres = body_db - 6.5
        target_air = body_db - 11.0

        pres_deficit = target_pres - pres_db
        air_deficit = target_air - air_db

        result = {
            "presence": 0.0,
            "air": 0.0,
            "stats": {
                "body_db": body_db,
                "pres_db": pres_db,
                "air_db": air_db,
                "pres_deficit": pres_deficit,
                "air_deficit": air_deficit
            }
        }

        threshold = 1.5
        max_boost = 4.0
        max_cut = 4.0

        if abs(pres_deficit) > threshold:
            result["presence"] = float(np.clip(pres_deficit, -max_cut, max_boost))

        if abs(air_deficit) > threshold:
            result["air"] = float(np.clip(air_deficit, -max_cut, max_boost))

        return result

    def _apply_adaptive_notch_filter(self, data: np.ndarray, rate: int) -> Tuple[np.ndarray, Dict[str, Any]]:
        original_data = data.copy()

        if data.ndim > 1:
            mono_data = librosa.to_mono(data)
        else:
            mono_data = data

        n_fft = 4096
        hop_length = 1024
        q_factor = 50.0
        max_notches = 4
        info = {"notched": False, "notches": [], "tonal_fixes": [], "analysis": {}, "debug": {}}

        stft_result = librosa.stft(mono_data, n_fft=n_fft, hop_length=hop_length)
        stft_magnitude = np.abs(stft_result)

        frame_energies = np.sqrt(np.mean(stft_magnitude ** 2, axis=0))
        energy_threshold = np.percentile(frame_energies, 60)
        active_frames_mask = frame_energies >= energy_threshold

        if np.any(active_frames_mask):
            avg_spectrum = np.mean(stft_magnitude[:, active_frames_mask], axis=1)
        else:
            avg_spectrum = np.mean(stft_magnitude, axis=1)

        avg_spectrum_db = librosa.amplitude_to_db(avg_spectrum, ref=np.max)
        freqs = librosa.fft_frequencies(sr=rate, n_fft=n_fft)

        peaks, properties = signal.find_peaks(avg_spectrum_db,
                                              prominence=1.0,
                                              height=-60,
                                              wlen=50)

        freqs_at_peaks = freqs[peaks]
        prominences = properties['prominences']

        high_freq_mask = freqs_at_peaks >= 5000
        high_freq_indices = np.where(high_freq_mask)[0]
        highest_hf_index = -1
        highest_hf_prominence = -float('inf')

        if high_freq_indices.size > 0:
            hf_prominences = prominences[high_freq_indices]
            max_hf_prominence_idx = np.argmax(hf_prominences)
            highest_hf_index = high_freq_indices[max_hf_prominence_idx]
            highest_hf_prominence = hf_prominences[max_hf_prominence_idx]

        if self.use_perceptual_weighting:
            weighted_prominences = self._apply_perceptual_weighting(freqs_at_peaks, prominences)
            sorted_indices = np.argsort(weighted_prominences)[::-1]
            top_candidates_indices = list(sorted_indices[:4])

            if highest_hf_index != -1 and highest_hf_prominence >= 6.0:
                if highest_hf_index not in top_candidates_indices:
                    top_candidates_indices[3] = highest_hf_index
                    info["debug"]["hybrid_hf_forced"] = {
                        "freq": float(freqs_at_peaks[highest_hf_index]),
                        "prom": float(highest_hf_prominence)
                    }
            final_notch_indices = np.array(top_candidates_indices)
        else:
            sorted_indices = np.argsort(prominences)[::-1]
            final_notch_indices = sorted_indices[:max_notches]

        filtered_data = data.copy()
        top_n = min(max_notches, len(final_notch_indices)) if peaks.size > 0 else 0

        for idx in range(top_n):
            peak_idx = final_notch_indices[idx]
            peak_bin = peaks[peak_idx]
            fc = freqs[peak_bin]

            if fc < 50 or fc > 15000:
                continue

            prominence = prominences[peak_idx]
            gain_db = -(2.0 + min(prominence, 12.0) * 0.8)

            b_notch, a_notch = signal.iirnotch(fc, q_factor, rate)
            sos = signal.tf2sos(b_notch, a_notch)

            filtered_deep = signal.sosfilt(sos, filtered_data, axis=-1)
            reduction_linear = 10 ** (gain_db / 20.0)
            filtered_data = filtered_data - (filtered_data - filtered_deep) * (1.0 - reduction_linear)

            info["notches"].append({"fc": fc, "gain": gain_db, "prom": prominence})

        if len(info["notches"]) > 0:
            info["notched"] = True

        filtered_data = self._apply_safety_rolloff(filtered_data, rate)

        tonal_result = self._analyze_multiband_tonality(avg_spectrum_db, freqs)
        if "stats" in tonal_result:
            info["analysis"] = tonal_result["stats"]

        if abs(tonal_result.get("presence", 0)) > 0.01:
            gain = tonal_result["presence"]
            filtered_data = self._apply_bell_boost(filtered_data, rate, freq=3500, gain_db=gain, q=1.0)
            action = "Boost" if gain > 0 else "Cut"
            info["tonal_fixes"].append(f"Presence {action}: {gain:+.1f}dB @ 3.5kHz")

        if abs(tonal_result.get("air", 0)) > 0.01:
            gain = tonal_result["air"]
            filtered_data = self._apply_high_shelf(filtered_data, rate, freq=8000, gain_db=gain)
            action = "Boost" if gain > 0 else "Cut"
            info["tonal_fixes"].append(f"Air {action}: {gain:+.1f}dB @ 8kHz")

        wet = self.master_wet_mix
        dry = 1.0 - wet
        filtered_data = original_data * dry + filtered_data * wet

        return filtered_data, info

    def _master_audio_sync(
            self,
            input_path: Path,
            output_path: Path,
            target_lufs: float
    ) -> Tuple[Optional[Path], dict]:
        try:
            data, rate = sf.read(str(input_path))

            if data.ndim > 1:
                data = data.T

            data, master_info = self._apply_adaptive_notch_filter(data, rate)

            info = master_info.copy()

            meter = pyln.Meter(rate)
            data_for_meter = data.T if data.ndim > 1 else data
            loudness = meter.integrated_loudness(data_for_meter)

            gain_db = target_lufs - loudness
            gain_linear = 10 ** (gain_db / 20.0)

            normalized = data * gain_linear

            info["original_loudness"] = loudness
            info["target_lufs"] = target_lufs
            info["applied_gain_db"] = gain_db

            pre_clip_peak = np.abs(normalized).max()
            if pre_clip_peak > 0.98:
                info["soft_clipping_engaged"] = True
                normalized = self._soft_clip(normalized, threshold=0.95)
            else:
                info["soft_clipping_engaged"] = False

            clipped = np.clip(normalized, -1.0, 1.0)
            info["safety_hard_clip"] = not np.array_equal(normalized, clipped)

            output_path.parent.mkdir(parents=True, exist_ok=True)

            to_save = clipped.T if clipped.ndim > 1 else clipped
            sf.write(str(output_path), to_save, rate, subtype='PCM_16')

            return output_path, info

        except Exception as e:
            import traceback
            traceback.print_exc()
            return None, {"error": str(e)}

    async def master_audio(
            self,
            input_path: Path,
            output_path: Path = None,  # type: ignore
            target_lufs: float = None,  # type: ignore
            progress_callback=None,
    ) -> Optional[Path]:
        if output_path is None:
            output_path = input_path

        if target_lufs is None:
            target_lufs = self.target_lufs

        log_service.upscaling(f"Final Master: Processing {input_path.name}")

        if progress_callback:
            await progress_callback("Master")

        result, info = await asyncio.to_thread(
            self._master_audio_sync,
            input_path,
            output_path,
            target_lufs
        )

        if not result:
            log_service.error(f"Final Master failed: {info.get('error', 'Unknown error')}")
            return None

        if "analysis" in info:
            stats = info["analysis"]
            log_service.upscaling("  [SPECTRUM ANALYSIS]")
            log_service.upscaling(
                f"    Measured Energy: Body {stats['body_db']:.1f}dB | "
                f"Presence {stats['pres_db']:.1f}dB | "
                f"Air {stats['air_db']:.1f}dB"
            )
            log_service.upscaling(
                f"    Pink Noise Deviation: Presence {stats['pres_deficit']:+.1f}dB | "
                f"Air {stats['air_deficit']:+.1f}dB"
            )

        if info.get("notched"):
            notches = info.get("notches", [])
            if len(notches) > 0:
                log_service.upscaling(
                    f"  [SURGICAL EQ] Removed {len(notches)} resonant peaks/whistles (Wet Mix: {self.master_wet_mix:.1f})")

        if info.get("tonal_fixes"):
            log_service.upscaling("  [TONAL SHAPING]")
            for fix in info["tonal_fixes"]:
                log_service.upscaling(f"    -> {fix}")
        else:
            log_service.upscaling("  [TONAL SHAPING] Spectral balance is optimal (No EQ needed)")

        if info.get("soft_clipping_engaged"):
            log_service.upscaling(f"  [SOFT CLIPPER] Engaged to preserve transients while hitting {target_lufs} LUFS")
        else:
            log_service.upscaling("  [LIMITER] Clean normalization (Headroom available)")

        log_service.upscaling(f"Final Master complete: {output_path.name}")
        return result

    @staticmethod
    def _analyze_loudness_sync(audio_path: Path) -> Optional[float]:
        data, rate = sf.read(str(audio_path))
        meter = pyln.Meter(rate)
        data_for_meter = data.T if data.ndim > 1 else data
        loudness = meter.integrated_loudness(data_for_meter)
        return float(loudness)

    async def unload(self):
        log_service.upscaling("Audio Master service unloaded")