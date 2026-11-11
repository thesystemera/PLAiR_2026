from typing import Any, Dict, Tuple

import numpy as np
from scipy import signal
from scipy.ndimage import uniform_filter1d

from services.audio_headroom import as_channels_first

RESONANCE_FFT = 4096
RESONANCE_HOP = 1024
RESONANCE_FINE_OCTAVES = 1.0 / 12.0
RESONANCE_BROAD_OCTAVES = 2.0 / 3.0
RESONANCE_FADE_IN_HZ = (1000.0, 2000.0)
RESONANCE_FADE_OUT_HZ = (8000.0, 12000.0)
RESONANCE_NORMAL_PERCENTILE = 75.0
RESONANCE_NORMAL_SMOOTH_OCTAVES = 1.0 / 3.0
RESONANCE_STRENGTH = 0.8
RESONANCE_MAX_CUT_DB = 6.0
RESONANCE_MAX_AMOUNT = 2.0
RESONANCE_TIME_FRAMES = 3
RESONANCE_LOUD_RANGE_DB = 30.0
RESONANCE_BAND_RANGE_DB = 25.0
RESONANCE_REPORT_HZ = ((1000, 2000), (2000, 4000), (4000, 8000), (8000, 12000))


def _octave_average(values: np.ndarray, freqs: np.ndarray, octaves: float) -> np.ndarray:
    half = 2.0 ** (octaves / 2.0)
    low = np.searchsorted(freqs, freqs / half, side="left")
    high = np.maximum(np.searchsorted(freqs, freqs * half, side="right"), low + 1)
    total = np.concatenate([np.zeros((1, values.shape[1])), np.cumsum(values, axis=0)], axis=0)
    return (total[high] - total[low]) / (high - low)[:, np.newaxis]


def _working_range(freqs: np.ndarray) -> np.ndarray:
    safe = np.maximum(freqs, 1.0)
    rise = np.clip(np.log2(safe / RESONANCE_FADE_IN_HZ[0]) / np.log2(RESONANCE_FADE_IN_HZ[1] / RESONANCE_FADE_IN_HZ[0]), 0.0, 1.0)
    fall = np.clip(np.log2(RESONANCE_FADE_OUT_HZ[1] / safe) / np.log2(RESONANCE_FADE_OUT_HZ[1] / RESONANCE_FADE_OUT_HZ[0]), 0.0, 1.0)
    return rise * fall


def suppress_resonances(audio: np.ndarray, rate: int, amount: float = 1.0) -> Tuple[np.ndarray, Dict[str, Any]]:
    x = as_channels_first(np.asarray(audio, dtype=np.float64))
    freqs, _, spec = signal.stft(x, rate, nperseg=RESONANCE_FFT, noverlap=RESONANCE_FFT - RESONANCE_HOP)
    power = (np.abs(spec) ** 2).mean(axis=0) + 1e-20
    fine = _octave_average(power, freqs, RESONANCE_FINE_OCTAVES)
    broad = _octave_average(power, freqs, RESONANCE_BROAD_OCTAVES)
    excess = uniform_filter1d(10.0 * np.log10(fine / broad), RESONANCE_TIME_FRAMES, axis=1, mode="nearest")

    weight = _working_range(freqs)
    active = weight > 0.0
    frame_level = 10.0 * np.log10(power[active].mean(axis=0))
    loud = frame_level >= np.percentile(frame_level, 95) - RESONANCE_LOUD_RANGE_DB
    broad_db = 10.0 * np.log10(broad)
    usual = np.percentile(broad_db[:, loud], 90, axis=1) if loud.any() else broad_db.max(axis=1)
    present = broad_db >= (usual - RESONANCE_BAND_RANGE_DB)[:, np.newaxis]

    normal = np.percentile(excess[:, loud], RESONANCE_NORMAL_PERCENTILE, axis=1) if loud.any() else np.zeros(len(freqs))
    normal = _octave_average(normal[:, np.newaxis], freqs, RESONANCE_NORMAL_SMOOTH_OCTAVES)[:, 0]
    cut = np.clip((excess - normal[:, np.newaxis]) * RESONANCE_STRENGTH, 0.0, RESONANCE_MAX_CUT_DB) * weight[:, np.newaxis]
    cut[~present] = 0.0
    cut[:, ~loud] = 0.0
    cut = _octave_average(cut, freqs, RESONANCE_FINE_OCTAVES) * float(np.clip(amount, 0.0, RESONANCE_MAX_AMOUNT))

    _, y = signal.istft(spec * (10.0 ** (-cut / 20.0))[np.newaxis], rate, nperseg=RESONANCE_FFT, noverlap=RESONANCE_FFT - RESONANCE_HOP)
    y = y[:, :x.shape[1]]
    if y.shape[1] < x.shape[1]:
        y = np.pad(y, ((0, 0), (0, x.shape[1] - y.shape[1])))

    core = (freqs >= RESONANCE_FADE_IN_HZ[1]) & (freqs <= RESONANCE_FADE_OUT_HZ[0])
    touched = cut[core][:, loud] if loud.any() else np.zeros((1, 1))
    removed = x - y
    bands = {}
    for low, high in RESONANCE_REPORT_HZ:
        rows = cut[(freqs >= low) & (freqs < high)][:, loud]
        bands[f"{low}-{high}"] = float(rows.mean()) if rows.size else 0.0
    return y, {
        "mean_cut_db": float(touched.mean()),
        "cut_over_1db_percent": float(np.mean(touched > 1.0) * 100.0),
        "cut_over_3db_percent": float(np.mean(touched > 3.0) * 100.0),
        "max_cut_db": float(cut.max()) if cut.size else 0.0,
        "mean_cut_by_band_db": bands,
        "removed_db": float(10.0 * np.log10((removed ** 2).mean() / ((x ** 2).mean() + 1e-20) + 1e-20)),
    }
