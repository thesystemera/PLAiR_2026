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
from config import settings
from services.audio_headroom import (
    MAX_LIMITER_REDUCTION_DB,
    TRUE_PEAK_CEILING_DBTP,
    db_to_linear,
    limit_true_peak,
    linear_to_db,
    peak_envelope,
    write_float_wav,
    write_pcm16_dithered,
)

MASTER_TARGET_LUFS = -16.0
SAFETY_ROLLOFF_ORDER = 3
MIN_MEASURABLE_LUFS = -70.0
RUMBLE_CUT_HZ = 25.0
RESONANCE_MIN_PROMINENCE_DB = 6.0
RESONANCE_MIN_PRESENCE = 0.9
RESONANCE_NEIGHBOURHOOD_BINS = 12
TONAL_BANDS = {"body": (300.0, 2000.0), "presence": (2000.0, 6000.0), "air": (6000.0, 15700.0)}
TONAL_TOLERANCE_DB = {"presence": 4.0, "air": 6.0}
TONAL_CORRECTION_SHARE = 0.5
TONAL_MAX_CORRECTION_DB = 3.0


def commercial_target_db(freqs: np.ndarray) -> np.ndarray:
    x = 1.0 + 60.0 * np.log2(np.maximum(freqs, 1.0) / 30.0)
    return -0.000183 * x ** 2 + 0.0213 * x - 16.735

class AudioMasterService(SingletonService):

    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.target_lufs = MASTER_TARGET_LUFS
        self.true_peak_ceiling_db = TRUE_PEAK_CEILING_DBTP
        self.use_perceptual_weighting = True
        self.master_wet_mix = 1.0
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
        rolloff_hz = settings.MASTER_SAFETY_ROLLOFF_HZ
        if rolloff_hz <= 0 or rolloff_hz >= nyquist * 0.99:
            return data

        warped = np.tan(np.pi * rolloff_hz / rate) / (np.sqrt(2.0) - 1.0) ** (1.0 / (2 * SAFETY_ROLLOFF_ORDER))
        cutoff = rate / np.pi * np.arctan(warped)

        sos = signal.butter(SAFETY_ROLLOFF_ORDER, cutoff, 'low', fs=rate, output='sos')
        return signal.sosfiltfilt(sos, data, axis=-1)

    @staticmethod
    def _apply_rumble_cut(data: np.ndarray, rate: int) -> np.ndarray:
        sos = signal.butter(2, RUMBLE_CUT_HZ, 'high', fs=rate, output='sos')
        return signal.sosfiltfilt(sos, data, axis=-1)

    @staticmethod
    def _resonance_presence(frames_db: np.ndarray, peak_bins: np.ndarray) -> np.ndarray:
        presence = np.zeros(len(peak_bins))
        half = RESONANCE_NEIGHBOURHOOD_BINS
        for i, b in enumerate(peak_bins):
            lo, hi = max(0, b - half), min(frames_db.shape[0], b + half + 1)
            neighbours = np.concatenate([frames_db[lo:max(lo, b - 2)], frames_db[min(hi, b + 3):hi]], axis=0)
            if neighbours.shape[0] == 0:
                continue
            prominence = frames_db[b] - np.median(neighbours, axis=0)
            presence[i] = float(np.mean(prominence >= RESONANCE_MIN_PROMINENCE_DB))
        return presence

    @staticmethod
    def _apply_bell_boost(data: np.ndarray, rate: int, freq: float, gain_db: float, q: float = 1.0) -> np.ndarray:
        if abs(gain_db) < 1e-5:
            return data

        w0 = 2 * np.pi * freq / rate
        alpha = np.sin(w0) / (2 * q)
        amp = 10 ** ((gain_db / 2) / 40)

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
        amp = 10 ** ((gain_db / 2) / 40)
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
    def _analyze_multiband_tonality(power_db: np.ndarray, freqs: np.ndarray) -> Dict[str, Any]:
        def band_deviation(lo: float, hi: float) -> float:
            points = np.geomspace(lo, hi, 48)
            measured = np.interp(points, freqs, power_db)
            return float(np.mean(measured - commercial_target_db(points)))

        deviation = {name: band_deviation(*edges) for name, edges in TONAL_BANDS.items()}
        result: Dict[str, Any] = {"presence": 0.0, "air": 0.0, "stats": {}}
        for name in ("presence", "air"):
            relative = deviation[name] - deviation["body"]
            excess = np.sign(relative) * max(0.0, abs(relative) - TONAL_TOLERANCE_DB[name])
            result[name] = float(np.clip(-excess * TONAL_CORRECTION_SHARE, -TONAL_MAX_CORRECTION_DB, TONAL_MAX_CORRECTION_DB))
            result["stats"][f"{name}_vs_target_db"] = relative
        return result

    def _apply_adaptive_notch_filter(self, data: np.ndarray, rate: int, wet_mix: Optional[float] = None) -> Tuple[np.ndarray, Dict[str, Any]]:
        if data.ndim > 1:
            mono_data = librosa.to_mono(data)
        else:
            mono_data = data

        n_fft = 4096
        hop_length = 1024
        q_factor = 50.0
        max_notches = 4
        info = {"notched": False, "notches": [], "tonal_fixes": [], "analysis": {}, "debug": {}}

        data = self._apply_rumble_cut(data, rate)

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
        frame_rms = np.sqrt(np.mean(stft_magnitude ** 2, axis=0))
        loud_frames = frame_rms >= np.mean(frame_rms)
        power_db = 10.0 * np.log10(np.mean(stft_magnitude[:, loud_frames] ** 2, axis=1) + 1e-20)

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
        candidates = [final_notch_indices[idx] for idx in range(top_n)]
        frames_db = librosa.amplitude_to_db(stft_magnitude[:, active_frames_mask] if np.any(active_frames_mask) else stft_magnitude)
        presence = self._resonance_presence(frames_db, np.array([peaks[c] for c in candidates], dtype=int))
        info["debug"]["candidates"] = [
            {"fc": float(freqs[peaks[c]]), "prom": float(prominences[c]), "presence": round(float(presence[i]), 3)}
            for i, c in enumerate(candidates)
        ]

        for i, peak_idx in enumerate(candidates):
            peak_bin = peaks[peak_idx]
            fc = freqs[peak_bin]

            if fc < 50 or fc > 15000:
                continue

            prominence = prominences[peak_idx]
            if prominence < RESONANCE_MIN_PROMINENCE_DB or presence[i] < RESONANCE_MIN_PRESENCE:
                continue
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

        tonal_result = self._analyze_multiband_tonality(power_db, freqs)
        if "stats" in tonal_result:
            info["analysis"] = tonal_result["stats"]

        if abs(tonal_result.get("presence", 0)) > 0.01:
            gain = tonal_result["presence"]
            filtered_data = self._apply_bell_boost(filtered_data, rate, freq=3500, gain_db=gain, q=1.0)
            action = "Boost" if gain > 0 else "Cut"
            info["tonal_fixes"].append(f"Presence {action}: {gain:+.1f}dB @ 3.5kHz")

        if abs(tonal_result.get("air", 0)) > 0.01:
            gain = tonal_result["air"]
            filtered_data = self._apply_high_shelf(filtered_data, rate, freq=6000, gain_db=gain)
            action = "Boost" if gain > 0 else "Cut"
            info["tonal_fixes"].append(f"Air {action}: {gain:+.1f}dB @ 6kHz")

        wet = self.master_wet_mix if wet_mix is None else float(np.clip(wet_mix, 0.0, 1.0))
        dry = 1.0 - wet
        filtered_data = data * dry + filtered_data * wet

        return filtered_data, info

    def _correct_audio_sync(self, input_path: Path, output_path: Path, wet_mix: Optional[float] = None) -> Tuple[Optional[Path], dict]:
        try:
            data, rate = sf.read(str(input_path), dtype='float32', always_2d=True)
            corrected, info = self._apply_adaptive_notch_filter(data.T.astype(np.float64), rate, wet_mix)
            write_float_wav(output_path, corrected, rate)
            return output_path, info
        except Exception as e:
            log_service.error(f"Corrective EQ failed: {e}")
            return None, {"error": str(e)}

    def _master_audio_sync(
            self,
            input_path: Path,
            output_path: Path,
            target_lufs: float,
            wet_mix: Optional[float] = None,
            correct: bool = True
    ) -> Tuple[Optional[Path], dict]:
        try:
            data, rate = sf.read(str(input_path), dtype='float32', always_2d=True)
            data = data.T.astype(np.float64)

            info: Dict[str, Any] = {}
            if correct:
                data, master_info = self._apply_adaptive_notch_filter(data, rate, wet_mix)
                info = master_info.copy()

            meter = pyln.Meter(rate)
            loudness = float(meter.integrated_loudness(data.T))
            measurable = np.isfinite(loudness) and loudness > MIN_MEASURABLE_LUFS

            gain_db = target_lufs - loudness if measurable else 0.0
            normalized = data * db_to_linear(gain_db)

            envelope = peak_envelope(normalized)
            pre_limit_peak_db = linear_to_db(float(envelope.max()) if envelope.size else 0.0)
            excess_db = pre_limit_peak_db - self.true_peak_ceiling_db
            trim_db = max(0.0, excess_db - MAX_LIMITER_REDUCTION_DB)
            if trim_db > 0.0:
                trim = db_to_linear(-trim_db)
                normalized *= trim
                envelope *= trim

            limited, limiter_info = limit_true_peak(
                normalized, rate, ceiling_db=self.true_peak_ceiling_db, envelope=envelope
            )

            info["original_loudness"] = loudness
            info["target_lufs"] = target_lufs
            info["applied_gain_db"] = gain_db - trim_db
            info["loudness_trim_db"] = trim_db
            info["pre_limit_true_peak_db"] = pre_limit_peak_db
            info["limiter"] = limiter_info
            info["output_true_peak_db"] = limiter_info["output_true_peak_db"]
            info["output_loudness"] = float(meter.integrated_loudness(limited.T))

            write_pcm16_dithered(output_path, limited, rate)

            return output_path, info

        except Exception as e:
            import traceback
            log_service.error(f"Mastering failed: {e}")
            log_service.error(f"Traceback: {traceback.format_exc()}")
            return None, {"error": str(e)}

    async def master_audio(
            self,
            input_path: Path,
            output_path: Path = None,  # type: ignore
            target_lufs: float = None,  # type: ignore
            progress_callback=None,
            wet_mix: Optional[float] = None,
            correct: bool = True,
    ) -> Optional[Path]:
        result, _info = await self.master_audio_with_report(
            input_path, output_path, target_lufs, progress_callback, wet_mix, correct
        )
        return result

    @staticmethod
    def _log_corrections(info: Dict[str, Any], wet_mix: float):
        if info.get("analysis"):
            stats = info["analysis"]
            log_service.upscaling(
                f"  [SPECTRUM ANALYSIS] vs commercial-master average: "
                f"Presence {stats['presence_vs_target_db']:+.1f}dB | Air {stats['air_vs_target_db']:+.1f}dB"
            )
        if info.get("notches"):
            log_service.upscaling(
                f"  [SURGICAL EQ] Removed {len(info['notches'])} resonant peaks/whistles (Wet Mix: {wet_mix:.2f})")
        if info.get("tonal_fixes"):
            log_service.upscaling(f"  [TONAL SHAPING] {', '.join(info['tonal_fixes'])}")
        else:
            log_service.upscaling("  [TONAL SHAPING] Spectral balance is within range (No EQ needed)")

    async def correct_audio(self, input_path: Path, output_path: Path, wet_mix: Optional[float] = None) -> Optional[Path]:
        effective_wet_mix = self.master_wet_mix if wet_mix is None else float(np.clip(wet_mix, 0.0, 1.0))
        result, info = await asyncio.to_thread(self._correct_audio_sync, input_path, output_path, effective_wet_mix)
        if result:
            self._log_corrections(info, effective_wet_mix)
        return result

    async def master_audio_with_report(
            self,
            input_path: Path,
            output_path: Path = None,  # type: ignore
            target_lufs: float = None,  # type: ignore
            progress_callback=None,
            wet_mix: Optional[float] = None,
            correct: bool = True,
    ) -> Tuple[Optional[Path], Dict[str, Any]]:
        effective_wet_mix = self.master_wet_mix if wet_mix is None else float(np.clip(wet_mix, 0.0, 1.0))
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
            target_lufs,
            effective_wet_mix,
            correct
        )

        if not result:
            log_service.error(f"Final Master failed: {info.get('error', 'Unknown error')}")
            return None, info

        if correct:
            self._log_corrections(info, effective_wet_mix)

        limiter = info.get("limiter", {})
        if limiter.get("passes"):
            log_service.upscaling(
                f"  [LIMITER] True-peak limiting: max {limiter.get('max_reduction_db', 0.0):.1f}dB on "
                f"{limiter.get('limited_percent', 0.0):.2f}% of samples"
            )
        else:
            log_service.upscaling("  [LIMITER] Clean normalization (Headroom available)")
        if info.get("loudness_trim_db", 0.0) > 0.0:
            log_service.upscaling(
                f"  [LOUDNESS] Held {info['loudness_trim_db']:.1f}dB below {target_lufs} LUFS to avoid over-limiting"
            )
        log_service.upscaling(
            f"  [OUTPUT] {info.get('output_loudness', float('nan')):.1f} LUFS | "
            f"true peak {info.get('output_true_peak_db', 0.0):.2f} dBTP (ceiling {self.true_peak_ceiling_db:.1f})"
        )

        log_service.upscaling(f"Final Master complete: {output_path.name}")
        return result, info

    @staticmethod
    def _analyze_loudness_sync(audio_path: Path) -> Optional[float]:
        data, rate = sf.read(str(audio_path), dtype='float32')
        meter = pyln.Meter(rate)
        loudness = meter.integrated_loudness(data)
        return float(loudness)

    async def unload(self):
        log_service.upscaling("Audio Master service unloaded")
