import subprocess, sys
from pathlib import Path

import numpy as np
from scipy import signal
from scipy.ndimage import uniform_filter1d

R = 48000
NFFT = 2048
HOP = 512
PHASE_FRAMES = 8
COMB_FRAMES = 16
SHARED_COHERENCE = 0.7
BANDS = [(500, 1000), (1000, 2000), (2000, 4000), (4000, 8000), (8000, 12000), (12000, 16000)]
COMB_BAND = (1000, 12000)
COMB_MS = (0.25, 8.0)
COMB_STRONG = 6.0
BIN_HZ = R / NFFT


def decode(path, start=0.0, seconds=180.0):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(start), "-t", str(seconds), "-i", str(path),
                          "-ac", "2", "-ar", str(R), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).astype(np.float64)
    return x[:, 0], x[:, 1]


def stft(x):
    f, _, S = signal.stft(x, R, nperseg=NFFT, noverlap=NFFT - HOP, boundary=None, padded=False)
    return f, S


def smooth(a, frames):
    if np.iscomplexobj(a):
        return uniform_filter1d(a.real, frames, axis=1) + 1j * uniform_filter1d(a.imag, frames, axis=1)
    return uniform_filter1d(a, frames, axis=1)


def coherence(SL, SR, frames):
    pl, pr = smooth(np.abs(SL) ** 2, frames), smooth(np.abs(SR) ** 2, frames)
    return smooth(SL * np.conj(SR), frames) / np.sqrt(pl * pr + 1e-30), pl + pr


def loud_frames(energy):
    e = energy.sum(0)
    return e > np.percentile(e, 30)


def stereo_phase(f, gamma, energy):
    loud = loud_frames(energy)
    rows = []
    for lo, hi in BANDS:
        m = (f >= lo) & (f < hi)
        g, w = gamma[m][:, loud], energy[m][:, loud]
        mag = np.abs(g)
        shared = mag > SHARED_COHERENCE
        ws = w * shared
        ang = np.degrees(np.abs(np.angle(g)))
        lag = PHASE_FRAMES
        step = np.degrees(np.abs(np.angle(g[:, lag:] * np.conj(g[:, :-lag]))))
        wb = np.minimum(w[:, lag:], w[:, :-lag]) * (shared[:, lag:] & shared[:, :-lag])
        rows.append({"band": f"{lo}-{hi}", "coherence": float((w * mag).sum() / w.sum()),
                     "shared_share": float(ws.sum() / w.sum()),
                     "shared_phase_deg": float((ws * ang).sum() / (ws.sum() + 1e-30)),
                     "phase_wander_deg": float((wb * step).sum() / (wb.sum() + 1e-30))})
    return rows


def delay_comb(gamma, energy, f):
    m = (f >= COMB_BAND[0]) & (f <= COMB_BAND[1])
    v = gamma.real[m]
    v = v - uniform_filter1d(v, max(3, int(4000 / BIN_HZ)), axis=0, mode="nearest")
    spec = np.abs(np.fft.rfft(v * np.hanning(v.shape[0])[:, None], axis=0))
    quef = np.fft.rfftfreq(v.shape[0], d=BIN_HZ) * 1000
    keep = (quef >= COMB_MS[0]) & (quef <= COMB_MS[1])
    spec, quef = spec[keep], quef[keep]
    loud = loud_frames(energy)
    strength = (spec.max(0) / (np.median(spec, axis=0) + 1e-12))[loud]
    delay = quef[spec.argmax(0)][loud]
    strong = strength >= COMB_STRONG
    return {"strength_median": float(np.median(strength)), "strength_p90": float(np.percentile(strength, 90)),
            "strong_share": float(strong.mean()), "delay_ms_median": float(np.median(delay[strong])) if strong.any() else None}


def measure(L, Rr):
    f, SL = stft(L)
    _, SR = stft(Rr)
    gamma, energy = coherence(SL, SR, PHASE_FRAMES)
    out = {"stereo_phase": stereo_phase(f, gamma, energy)}
    out["stereo_comb"] = delay_comb(*coherence(SL, SR, COMB_FRAMES), f)
    return out


def flanger(x, depth=0.7, lo_ms=0.4, hi_ms=2.5, rate_hz=0.25, phase=0.0):
    n = np.arange(len(x))
    pos = n - (lo_ms + (hi_ms - lo_ms) * (0.5 + 0.5 * np.sin(2 * np.pi * rate_hz * n / R + phase))) * R / 1000
    i0 = np.clip(np.floor(pos).astype(int), 0, len(x) - 2)
    frac = pos - np.floor(pos)
    return (x + depth * ((1 - frac) * x[i0] + frac * x[i0 + 1])) / (1 + depth)


def line(label, m):
    p = m["stereo_phase"]
    phase = " ".join(f"{r['shared_phase_deg']:4.1f}/{r['phase_wander_deg']:4.1f}" for r in p)
    coh = " ".join(f"{r['coherence']:.2f}" for r in p)
    c = m["stereo_comb"]
    return (f"{label:34s} coherence {coh} | shared phase/wander deg {phase} | "
            f"channel delay comb {c['strength_median']:.1f}/{c['strength_p90']:.1f} strong {100 * c['strong_share']:.0f}%")


if __name__ == "__main__":
    for path in sys.argv[1:]:
        print(line(Path(path).stem[:34], measure(*decode(path))), flush=True)
