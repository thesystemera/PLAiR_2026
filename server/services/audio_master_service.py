import asyncio
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
import soundfile as sf
import numpy as np
import pyloudnorm as pyln
from scipy import signal
from scipy.ndimage import gaussian_filter1d
from services import log_service
from services.base_service import SingletonService
from config import settings
from services.audio_resonance import suppress_resonances
from services.audio_headroom import (
    MAX_LIMITER_REDUCTION_DB,
    TRUE_PEAK_CEILING_DBTP,
    db_to_linear,
    limit_true_peak,
    linear_to_db,
    peak_envelope,
    tape_hiss,
    write_float_wav,
    write_pcm16_dithered,
)

MASTER_TARGET_LUFS = -16.0
SAFETY_ROLLOFF_ORDER = 3
MIN_MEASURABLE_LUFS = -70.0
RUMBLE_CUT_HZ = 25.0
MODERN_MASTER_HZ = np.array([63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 10000.0, 12500.0, 16000.0])
MODERN_MASTER_DB = np.array([22.8, 15.5, 10.6, 5.5, 0.0, -4.1, -9.1, -16.2, -18.2, -22.5, -30.8])
REFERENCE_ALIGN_HZ = (500.0, 2000.0)
REFERENCE_RANGE_HZ = (40.0, 16000.0)
REFERENCE_TOLERANCE_DB = 3.0
REFERENCE_MAX_CORRECTION_DB = 10.0
REFERENCE_GRID = np.geomspace(20.0, 22000.0, 600)
REFERENCE_ITERATIONS = 24
REFERENCE_EQ_TAPS = 8191
REFERENCE_REPORT_HZ = (63, 125, 250, 500, 1000, 2000, 4000, 8000, 10000, 12500, 16000)


def modern_master_target_db(freqs: np.ndarray) -> np.ndarray:
    return np.interp(np.log2(np.maximum(freqs, 1.0)), np.log2(MODERN_MASTER_HZ), MODERN_MASTER_DB)


def _grid_smooth(values: np.ndarray, octave_fraction: float) -> np.ndarray:
    bins_per_octave = len(REFERENCE_GRID) / np.log2(REFERENCE_GRID[-1] / REFERENCE_GRID[0])
    return gaussian_filter1d(values, bins_per_octave * octave_fraction / 2.355, mode="nearest")


def reference_deviation_db(data: np.ndarray, rate: int) -> np.ndarray:
    mono = data.mean(axis=0) if data.ndim > 1 else data
    freqs, _, spectrum = signal.stft(mono, rate, nperseg=4096, noverlap=2048)
    power_db = 10.0 * np.log10(np.mean(np.abs(spectrum) ** 2, axis=1) + 1e-30)
    measured = np.interp(np.log2(REFERENCE_GRID), np.log2(np.maximum(freqs, 1.0)), power_db)
    deviation = _grid_smooth(measured, 1.0 / 3.0) - modern_master_target_db(REFERENCE_GRID)
    align = (REFERENCE_GRID >= REFERENCE_ALIGN_HZ[0]) & (REFERENCE_GRID <= REFERENCE_ALIGN_HZ[1])
    return deviation - deviation[align].mean()


def reference_eq_curve(deviation: np.ndarray) -> np.ndarray:
    log_grid = np.log2(REFERENCE_GRID)
    low, high = REFERENCE_RANGE_HZ
    window = np.clip((log_grid - np.log2(low * 0.75)) / np.log2(1 / 0.75), 0, 1)
    window *= np.clip((np.log2(high * 1.0625) - log_grid) / np.log2(1.0625), 0, 1)
    correction = np.zeros_like(deviation)
    for _ in range(REFERENCE_ITERATIONS):
        residual = deviation + _grid_smooth(_grid_smooth(correction, 1.0 / 6.0) * window, 1.0 / 3.0)
        excess = np.sign(residual) * np.maximum(0.0, np.abs(residual) - REFERENCE_TOLERANCE_DB)
        correction = np.clip(correction - excess, -REFERENCE_MAX_CORRECTION_DB, REFERENCE_MAX_CORRECTION_DB)
    return np.clip(_grid_smooth(correction, 1.0 / 6.0) * window, -REFERENCE_MAX_CORRECTION_DB, REFERENCE_MAX_CORRECTION_DB)

class AudioMasterService(SingletonService):

    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.target_lufs = MASTER_TARGET_LUFS
        self.true_peak_ceiling_db = TRUE_PEAK_CEILING_DBTP
        self.master_wet_mix = 1.0
        self._initialized = True

    async def initialize(self):
        log_service.upscaling(
            f"Audio Master service initialized (resonance filter at {settings.MASTER_RESONANCE_AMOUNT:.0%}, "
            f"EQ blend {self.master_wet_mix:.1f})")

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
    def _apply_reference_eq(data: np.ndarray, rate: int, info: Dict[str, Any]) -> np.ndarray:
        deviation = reference_deviation_db(data, rate)
        curve = reference_eq_curve(deviation)
        in_range = (REFERENCE_GRID >= REFERENCE_RANGE_HZ[0]) & (REFERENCE_GRID <= REFERENCE_RANGE_HZ[1])
        outside = np.maximum(0.0, np.abs(deviation) - REFERENCE_TOLERANCE_DB) * in_range
        info["analysis"] = {"outside_band_db": float(outside.max())}
        moves = [(hz, float(np.interp(np.log2(hz), np.log2(REFERENCE_GRID), curve))) for hz in REFERENCE_REPORT_HZ]
        info["tonal_fixes"] = [f"{hz // 1000}k {gain:+.1f}dB" if hz >= 1000 else f"{hz} {gain:+.1f}dB"
                               for hz, gain in moves if abs(gain) >= 0.5]
        if np.max(np.abs(curve)) < 0.25:
            return data
        linear = np.linspace(0.0, rate / 2.0, 8193)
        gains = 10 ** (np.interp(np.log2(np.maximum(linear, 1.0)), np.log2(REFERENCE_GRID), curve) / 20.0)
        fir = signal.firwin2(REFERENCE_EQ_TAPS, linear, gains, fs=rate)
        delay = (REFERENCE_EQ_TAPS - 1) // 2
        filtered = signal.oaconvolve(data, fir[np.newaxis, :], mode="full", axes=-1)
        return filtered[:, delay:delay + data.shape[-1]]

    def _correct_and_tone(self, data: np.ndarray, rate: int, wet_mix: Optional[float] = None,
                          correct: bool = True, tone: bool = True) -> Tuple[np.ndarray, Dict[str, Any]]:
        info: Dict[str, Any] = {"tonal_fixes": [], "analysis": {}}
        if correct:
            data = self._apply_rumble_cut(data, rate)
        filtered = self._apply_safety_rolloff(data, rate) if correct else data
        if tone:
            filtered = self._apply_reference_eq(filtered, rate, info)

        wet = self.master_wet_mix if wet_mix is None else float(np.clip(wet_mix, 0.0, 1.0))
        return data * (1.0 - wet) + filtered * wet, info

    def _correct_audio_sync(self, input_path: Path, output_path: Path, wet_mix: Optional[float] = None) -> Tuple[Optional[Path], dict]:
        try:
            data, rate = sf.read(str(input_path), dtype='float32', always_2d=True)
            corrected, info = self._correct_and_tone(data.T.astype(np.float64), rate, wet_mix, correct=True, tone=False)
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
            correct: bool = True,
            tone: bool = False,
            hiss: bool = False,
            resonance: bool = False
    ) -> Tuple[Optional[Path], dict]:
        try:
            data, rate = sf.read(str(input_path), dtype='float32', always_2d=True)
            data = data.T.astype(np.float64)

            info: Dict[str, Any] = {}
            if resonance and settings.MASTER_RESONANCE_AMOUNT > 0:
                data, info["resonance"] = suppress_resonances(data, rate, settings.MASTER_RESONANCE_AMOUNT)
            if correct or tone:
                data, master_info = self._correct_and_tone(data, rate, wet_mix, correct=correct, tone=correct or tone)
                info.update(master_info)

            meter = pyln.Meter(rate)
            loudness = float(meter.integrated_loudness(data.T))
            measurable = np.isfinite(loudness) and loudness > MIN_MEASURABLE_LUFS

            gain_db = target_lufs - loudness if measurable else 0.0
            normalized = data * db_to_linear(gain_db)
            if hiss and settings.MASTER_TAPE_HISS_DBFS < 0:
                normalized = normalized + tape_hiss(normalized.shape[0], normalized.shape[1], rate, settings.MASTER_TAPE_HISS_DBFS)
                info["tape_hiss_dbfs"] = settings.MASTER_TAPE_HISS_DBFS

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
            tone: bool = False,
            hiss: bool = False,
            resonance: bool = False,
    ) -> Optional[Path]:
        result, _info = await self.master_audio_with_report(
            input_path, output_path, target_lufs, progress_callback, wet_mix, correct, tone, hiss, resonance
        )
        return result

    @staticmethod
    def _log_corrections(info: Dict[str, Any]):
        resonance = info.get("resonance")
        if resonance is not None:
            log_service.upscaling(
                f"  [RESONANCE FILTER] at {settings.MASTER_RESONANCE_AMOUNT:.0%}: average cut {resonance['mean_cut_db']:.2f}dB at 2-8kHz, over 1dB in "
                f"{resonance['cut_over_1db_percent']:.1f}% of it, deepest {resonance['max_cut_db']:.1f}dB ("
                + ", ".join(f"{band}Hz {cut:.2f}dB" for band, cut in resonance["mean_cut_by_band_db"].items()) + ")"
            )
        if info.get("analysis"):
            log_service.upscaling(
                f"  [SPECTRUM ANALYSIS] vs modern-master average: furthest outside the "
                f"+/-{REFERENCE_TOLERANCE_DB:.0f}dB band by {info['analysis']['outside_band_db']:.1f}dB"
            )
        if info.get("tonal_fixes"):
            log_service.upscaling(f"  [REFERENCE EQ] {', '.join(info['tonal_fixes'])}")
        elif info.get("analysis"):
            log_service.upscaling("  [REFERENCE EQ] Within the band of modern masters (No EQ needed)")

    async def correct_audio(self, input_path: Path, output_path: Path, wet_mix: Optional[float] = None) -> Optional[Path]:
        effective_wet_mix = self.master_wet_mix if wet_mix is None else float(np.clip(wet_mix, 0.0, 1.0))
        result, info = await asyncio.to_thread(self._correct_audio_sync, input_path, output_path, effective_wet_mix)
        if result:
            self._log_corrections(info)
        return result

    async def master_audio_with_report(
            self,
            input_path: Path,
            output_path: Path = None,  # type: ignore
            target_lufs: float = None,  # type: ignore
            progress_callback=None,
            wet_mix: Optional[float] = None,
            correct: bool = True,
            tone: bool = False,
            hiss: bool = False,
            resonance: bool = False,
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
            correct,
            tone,
            hiss,
            resonance
        )

        if not result:
            log_service.error(f"Final Master failed: {info.get('error', 'Unknown error')}")
            return None, info

        if correct or tone or resonance:
            self._log_corrections(info)

        if "tape_hiss_dbfs" in info:
            log_service.upscaling(f"  [TAPE HISS] Steady hiss at {info['tape_hiss_dbfs']:.0f} dBFS")
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
