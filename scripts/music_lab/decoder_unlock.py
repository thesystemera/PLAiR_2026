import subprocess

import numpy as np
from scipy.ndimage import uniform_filter1d

R = 48000
FRAME = 960
NFFT = 3840
HOP = 960
BIN_HZ = R / NFFT
COMB_RHO = 0.98
SHIFT_STEP_HZ = 200
SHIFT_MAX_HZ = 9600
SMOOTH_BINS = 5
NOISE_OFFSET_BINS = 3


def decode(path, start=0.0, seconds=None):
    cmd = ["ffmpeg", "-loglevel", "error", "-ss", str(start)]
    if seconds:
        cmd += ["-t", str(seconds)]
    raw = subprocess.run(cmd + ["-i", str(path), "-ac", "2", "-ar", str(R), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).astype(np.float64)


def comb_pass(frames, rho):
    out = np.empty_like(frames)
    gain = (1 + rho) / 2
    prev_in = np.zeros(frames.shape[1])
    prev_out = np.zeros(frames.shape[1])
    for k in range(frames.shape[0]):
        prev_out = gain * (frames[k] - prev_in) + rho * prev_out
        prev_in = frames[k]
        out[k] = prev_out
    return out


def remove_comb(x, rho=COMB_RHO):
    n = len(x)
    lead = FRAME * 100
    padded = np.concatenate([x[lead:0:-1], x, x[-2:-lead - 2:-1], np.zeros((-(n + 2 * lead)) % FRAME)])
    frames = padded.reshape(-1, FRAME)
    y = comb_pass(comb_pass(frames, rho)[::-1, ::-1], rho)[::-1, ::-1]
    return y.reshape(-1)[lead:lead + n]


def window():
    return np.hanning(NFFT + 1)[:-1]


def stft(x):
    n = 1 + (len(x) - NFFT) // HOP
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]
    return np.fft.rfft((x[idx] * window()).astype(np.float32), axis=1).astype(np.complex64)


def istft(X, length):
    w = window()
    frames = np.fft.irfft(X, NFFT, axis=1) * w
    out = np.zeros(length)
    norm = np.zeros(length)
    for k in range(frames.shape[0]):
        out[k * HOP:k * HOP + NFFT] += frames[k]
        norm[k * HOP:k * HOP + NFFT] += w * w
    return out / np.maximum(norm, 1e-3)


def smooth_complex(v, bins):
    return uniform_filter1d(v.real, bins, mode="nearest") + 1j * uniform_filter1d(v.imag, bins, mode="nearest")


def shifts():
    return [int(round(hz / BIN_HZ)) for hz in range(SHIFT_STEP_HZ, SHIFT_MAX_HZ + 1, SHIFT_STEP_HZ)]


def pair_terms(X, s, power, smooth_bins):
    cross = smooth_complex((X[:, s:] * np.conj(X[:, :-s])).sum(0).astype(np.complex128), smooth_bins)
    return cross, power[s:], power[:-s]


def image_transfer(X, smooth_bins=SMOOTH_BINS):
    power = uniform_filter1d((np.abs(X) ** 2).sum(0).astype(np.float64), smooth_bins, mode="nearest")
    grid = shifts()
    noise = []
    for s in grid[3::8]:
        cross, a, b = pair_terms(X, s + NOISE_OFFSET_BINS, power, smooth_bins)
        noise.append(np.mean(np.abs(cross) ** 2 / (a * b + 1e-30)))
    noise = float(np.mean(noise))
    out = {}
    for s in grid:
        cross, a, b = pair_terms(X, s, power, smooth_bins)
        keep = np.maximum(0.0, 1.0 - noise / (np.abs(cross) ** 2 / (a * b + 1e-30) + 1e-30))
        out[s] = (keep * cross / (a + b + 1e-30)).astype(np.complex64)
    return out


def cancel_images(X, transfer):
    Y = X.copy()
    for s, c in transfer.items():
        Y[:, s:] -= c * X[:, :-s]
        Y[:, :-s] -= np.conj(c) * X[:, s:]
    return Y


def unlock(x, smooth_bins=SMOOTH_BINS, comb=True, images=True):
    y = remove_comb(x) if comb else x.copy()
    if images:
        n = len(y)
        pad = np.concatenate([np.zeros(NFFT), y, np.zeros(NFFT + (-n) % HOP)])
        X = stft(pad)
        y = istft(cancel_images(X, image_transfer(X, smooth_bins)), len(pad))[NFFT:NFFT + n]
    return y
