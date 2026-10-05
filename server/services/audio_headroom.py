from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import soundfile as sf
from scipy import signal
from scipy.ndimage import maximum_filter1d

TRUE_PEAK_CEILING_DBTP = -1.5
MAX_LIMITER_REDUCTION_DB = 6.0
TRUE_PEAK_OVERSAMPLE = 4
LIMITER_LOOKAHEAD_MS = 3.0
LIMITER_RELEASE_MS = 80.0
LIMITER_MARGIN_DB = 0.1
PCM16_LSB = 1.0 / 32768.0
PCM16_MAX = 32767.0 / 32768.0
_ENVELOPE_BLOCK = 1 << 18
_ENVELOPE_PAD = 64


def db_to_linear(value_db: float) -> float:
    return float(10.0 ** (value_db / 20.0))


def linear_to_db(value: float) -> float:
    return float(20.0 * np.log10(max(float(value), 1e-12)))


def as_channels_first(data: np.ndarray) -> np.ndarray:
    return data[np.newaxis, :] if data.ndim == 1 else data


def peak_envelope(data: np.ndarray, oversample: int = TRUE_PEAK_OVERSAMPLE) -> np.ndarray:
    x = as_channels_first(data)
    length = x.shape[1]
    envelope = np.max(np.abs(x), axis=0).astype(np.float64)
    if length == 0 or oversample <= 1:
        return envelope
    for start in range(0, length, _ENVELOPE_BLOCK):
        stop = min(length, start + _ENVELOPE_BLOCK)
        lo = max(0, start - _ENVELOPE_PAD)
        hi = min(length, stop + _ENVELOPE_PAD)
        upsampled = signal.resample_poly(x[:, lo:hi], oversample, 1, axis=1)
        segment = np.max(np.abs(upsampled), axis=0)
        first = (start - lo) * oversample
        block = segment[first:first + (stop - start) * oversample].reshape(stop - start, oversample)
        np.maximum(envelope[start:stop], block.max(axis=1), out=envelope[start:stop])
    return envelope


def true_peak(data: np.ndarray, oversample: int = TRUE_PEAK_OVERSAMPLE) -> float:
    envelope = peak_envelope(data, oversample)
    return float(envelope.max()) if envelope.size else 0.0


def _gain_reduction_curve(envelope: np.ndarray, rate: int, threshold: float,
                          lookahead_ms: float, release_ms: float) -> np.ndarray:
    length = envelope.shape[0]
    required = np.zeros(length, dtype=np.float64)
    over = envelope > threshold
    required[over] = 20.0 * np.log10(envelope[over] / threshold)

    window = max(3, int(round(lookahead_ms * rate / 1000.0)) | 1)
    padded = np.concatenate([required, np.zeros(window, dtype=np.float64)])
    held = maximum_filter1d(padded, size=window, mode='constant', cval=0.0)[window // 2: window // 2 + length]

    log_decay = -1.0 / max(release_ms / 1000.0 * rate, 1.0)
    index = np.arange(length, dtype=np.float64)
    with np.errstate(divide='ignore'):
        log_held = np.log(held)
    released = np.exp(np.maximum.accumulate(log_held - index * log_decay) + index * log_decay)

    cumulative = np.concatenate([[0.0], np.cumsum(released)])
    ends = np.arange(1, length + 1)
    starts = np.maximum(0, ends - window)
    return (cumulative[ends] - cumulative[starts]) / window


def limit_true_peak(
        data: np.ndarray,
        rate: int,
        ceiling_db: float = TRUE_PEAK_CEILING_DBTP,
        envelope: Optional[np.ndarray] = None,
        lookahead_ms: float = LIMITER_LOOKAHEAD_MS,
        release_ms: float = LIMITER_RELEASE_MS,
        max_passes: int = 3
) -> Tuple[np.ndarray, Dict[str, Any]]:
    x = as_channels_first(np.asarray(data, dtype=np.float64))
    ceiling = db_to_linear(ceiling_db)
    threshold = db_to_linear(ceiling_db - LIMITER_MARGIN_DB)
    env = peak_envelope(x) if envelope is None else envelope
    info: Dict[str, Any] = {
        "input_true_peak_db": linear_to_db(float(env.max()) if env.size else 0.0),
        "max_reduction_db": 0.0,
        "limited_percent": 0.0,
        "passes": 0,
        "final_trim_db": 0.0,
    }
    total_reduction = np.zeros(x.shape[1], dtype=np.float64)
    for _ in range(max_passes):
        if not env.size or float(env.max()) <= ceiling:
            break
        reduction = _gain_reduction_curve(env, rate, threshold, lookahead_ms, release_ms)
        x = x * (10.0 ** (-reduction / 20.0))[np.newaxis, :]
        total_reduction += reduction
        info["passes"] += 1
        env = peak_envelope(x)

    peak = float(env.max()) if env.size else 0.0
    if peak > ceiling:
        trim = ceiling / peak
        x = x * trim
        info["final_trim_db"] = linear_to_db(trim)
        peak = ceiling

    if x.shape[1]:
        info["max_reduction_db"] = float(total_reduction.max())
        info["limited_percent"] = float(np.mean(total_reduction > 0.1) * 100.0)
    info["output_true_peak_db"] = linear_to_db(peak)
    return x, info


def tpdf_dither_pcm16(data: np.ndarray, seed: int = 0) -> np.ndarray:
    x = as_channels_first(np.asarray(data, dtype=np.float64))
    rng = np.random.default_rng(seed)
    noise = (rng.random(x.shape) - rng.random(x.shape)) * PCM16_LSB
    return np.clip(x + noise, -1.0, PCM16_MAX).astype(np.float32)


def write_pcm16_dithered(path: Path, data: np.ndarray, rate: int):
    x = tpdf_dither_pcm16(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), x.T, rate, subtype='PCM_16')


def write_float_wav(path: Path, data: np.ndarray, rate: int):
    x = as_channels_first(np.asarray(data, dtype=np.float32))
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), x.T, rate, subtype='FLOAT')


def _average_psd(data: np.ndarray, rate: int, nperseg: int) -> Tuple[np.ndarray, np.ndarray]:
    freqs, psd = signal.welch(as_channels_first(data), fs=rate, nperseg=nperseg, axis=-1)
    return freqs, np.mean(np.atleast_2d(psd), axis=0)


def _octave_smooth(freqs: np.ndarray, values: np.ndarray, fraction: float) -> np.ndarray:
    smoothed = values.copy()
    cumulative = np.concatenate([[0.0], np.cumsum(values)])
    half = 2.0 ** (fraction / 2.0)
    for i in range(1, freqs.size):
        lo = int(np.searchsorted(freqs, freqs[i] / half, side='left'))
        hi = int(np.searchsorted(freqs, freqs[i] * half, side='right'))
        smoothed[i] = (cumulative[hi] - cumulative[lo]) / max(hi - lo, 1)
    return smoothed


def spectrally_balanced_blend(
        wet: np.ndarray,
        dry: np.ndarray,
        wet_mix: float,
        rate: int,
        max_boost_db: float = 3.5,
        numtaps: int = 2047
) -> Tuple[np.ndarray, Dict[str, Any]]:
    w = float(np.clip(wet_mix, 0.0, 1.0))
    wet = as_channels_first(np.asarray(wet, dtype=np.float64))
    dry = as_channels_first(np.asarray(dry, dtype=np.float64))
    blended = w * wet + (1.0 - w) * dry
    info: Dict[str, Any] = {"compensation_max_db": 0.0, "compensation_hf_db": 0.0}
    length = blended.shape[1]
    nperseg = 4096
    if w <= 0.001 or w >= 0.999 or length < nperseg * 4:
        return blended, info

    freqs, p_wet = _average_psd(wet, rate, nperseg)
    _, p_dry = _average_psd(dry, rate, nperseg)
    _, p_mix = _average_psd(blended, rate, nperseg)
    target = w * p_wet + (1.0 - w) * p_dry
    floor = max(float(np.max(target)) * 1e-10, 1e-20)
    valid = (p_mix > floor) & (target > floor) & (freqs >= 20.0)
    gain = np.ones_like(freqs)
    gain[valid] = np.sqrt(target[valid] / p_mix[valid])
    gain = np.clip(gain, 1.0, db_to_linear(max_boost_db))
    gain = _octave_smooth(freqs, gain, 1.0 / 3.0)
    gain[0] = 1.0

    hf = freqs >= 4000.0
    info["compensation_max_db"] = linear_to_db(float(gain.max()))
    info["compensation_hf_db"] = linear_to_db(float(np.mean(gain[hf]))) if np.any(hf) else 0.0
    if info["compensation_max_db"] < 0.05:
        return blended, info

    nyquist = rate / 2.0
    grid = np.concatenate([freqs / nyquist, [1.0]]) if freqs[-1] < nyquist else freqs / nyquist
    gains = np.concatenate([gain, [gain[-1]]]) if freqs[-1] < nyquist else gain
    fir = signal.firwin2(numtaps, grid, gains)
    delay = (numtaps - 1) // 2
    filtered = signal.oaconvolve(blended, fir[np.newaxis, :], mode='full', axes=-1)
    return filtered[:, delay:delay + length], info


SOURCE_CUTOFF_DROP_DB = 15.0
SOURCE_CUTOFF_MARGIN_HZ = 300.0
SOURCE_CROSSOVER_TAPS = 8191


def source_cutoff_hz(data: np.ndarray, rate: int) -> float:
    freqs, power = _average_psd(as_channels_first(data), rate, 8192)
    power_db = 10.0 * np.log10(power + 1e-30)
    reference = np.median(power_db[(freqs >= 12000.0) & (freqs <= 16000.0)])
    below = np.where((freqs > 14000.0) & (power_db < reference - SOURCE_CUTOFF_DROP_DB))[0]
    return float(freqs[below[0]]) if below.size else rate / 2.0


def keep_source_below_cutoff(source: np.ndarray, restored: np.ndarray, rate: int) -> Tuple[np.ndarray, float]:
    source = as_channels_first(source)
    restored = as_channels_first(restored)
    length = min(source.shape[1], restored.shape[1])
    source, restored = source[:, :length], restored[:, :length]
    cutoff = source_cutoff_hz(source, rate)
    if cutoff >= rate / 2.0 * 0.98:
        return restored, cutoff
    lowpass = signal.firwin(SOURCE_CROSSOVER_TAPS, cutoff - SOURCE_CUTOFF_MARGIN_HZ, window=("kaiser", 10.0), fs=rate)
    delay = (SOURCE_CROSSOVER_TAPS - 1) // 2

    def low(x: np.ndarray) -> np.ndarray:
        return signal.oaconvolve(x, lowpass[np.newaxis, :], mode="full", axes=-1)[:, delay:delay + length]

    return low(source) + restored - low(restored), cutoff


def mix_stems_to_file(vocals_path: Path, instrumentals_path: Path, output_path: Path) -> Dict[str, Any]:
    vocals, vocals_rate = sf.read(str(vocals_path), dtype='float32', always_2d=True)
    instrumentals, rate = sf.read(str(instrumentals_path), dtype='float32', always_2d=True)
    vocals = vocals.T
    instrumentals = instrumentals.T
    if vocals_rate != rate:
        divisor = int(np.gcd(int(rate), int(vocals_rate)))
        vocals = signal.resample_poly(vocals, rate // divisor, vocals_rate // divisor, axis=1)
    channels = max(vocals.shape[0], instrumentals.shape[0])
    if vocals.shape[0] < channels:
        vocals = np.repeat(vocals[:1], channels, axis=0)
    if instrumentals.shape[0] < channels:
        instrumentals = np.repeat(instrumentals[:1], channels, axis=0)
    length = min(vocals.shape[1], instrumentals.shape[1])
    mixed = vocals[:, :length] + instrumentals[:, :length]
    write_float_wav(output_path, mixed, rate)
    return {
        "rate": int(rate),
        "vocals_rate": int(vocals_rate),
        "resampled": vocals_rate != rate,
        "length_mismatch_s": abs(vocals.shape[1] - instrumentals.shape[1]) / float(rate),
    }
