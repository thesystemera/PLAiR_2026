import json, subprocess, sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from scipy import signal
from scipy.ndimage import median_filter

R = 48000
FRAME = 960
UNLOCKED = 947
SECONDS = 240
LINE_NFFT = 65536
LOCK_NFFT = 3840
LOCK_BIN_HZ = R / LOCK_NFFT
LOCK_SPACINGS = (1600, 3200, 4800)
LOCK_BAND = (4000, 12000)
RIPPLE_BANDS = {"ripple_6_12": (6000, 12000), "ripple_12_17": (12000, 17000)}


def decode(path, seconds=SECONDS):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-t", str(seconds), "-i", str(path), "-ac", "2", "-ar", str(R), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).astype(np.float64)


def typical_spectrum(x):
    hop = LINE_NFFT // 2
    win = signal.get_window("hann", LINE_NFFT)
    total, used = np.zeros(LINE_NFFT // 2 + 1), 0
    for i in range(1 + (len(x) - LINE_NFFT) // hop):
        seg = x[i * hop:i * hop + LINE_NFFT]
        if np.mean(seg * seg) < 1e-7:
            continue
        total += 10 * np.log10(np.abs(np.fft.rfft(seg * win)) ** 2 + 1e-20)
        used += 1
    return total / max(used, 1)


def comb_height(x, lo=8000, hi=16000, step=200):
    spec = typical_spectrum(x)
    local = spec - median_filter(spec, size=41, mode="nearest")
    bin_hz = R / LINE_NFFT
    return float(np.mean([local[int(round(hz / bin_hz)) - 1:int(round(hz / bin_hz)) + 2].max() for hz in range(lo, hi + 1, step)]))


def fold(x, period):
    n = len(x) // period
    return x[:n * period].reshape(n, period)


def pattern_level(x):
    rms = np.sqrt(np.mean(x * x)) + 1e-12
    locked = fold(x, FRAME).mean(0)
    free = fold(x, UNLOCKED).mean(0)
    return float(20 * np.log10(np.sqrt(np.mean(locked ** 2)) / rms)), float(20 * np.log10(np.sqrt(np.mean(free ** 2)) / rms))


def energy_ripple(x, lo, hi):
    e = signal.sosfiltfilt(signal.butter(8, [lo, hi], "bandpass", fs=R, output="sos"), x) ** 2
    out = []
    for period in (FRAME, UNLOCKED):
        p = fold(e, period)
        w = p.mean(1, keepdims=True)
        keep = w[:, 0] > np.percentile(w, 30)
        out.append(float((p[keep] / w[keep]).mean(0).std() * 100))
    return out


def frames(x, hop):
    n = 1 + (len(x) - LOCK_NFFT) // hop
    idx = np.arange(LOCK_NFFT)[None, :] + hop * np.arange(n)[:, None]
    return np.fft.rfft((x[idx] * np.hanning(LOCK_NFFT)).astype(np.float32), axis=1).astype(np.complex64)


def lock(x):
    f = np.fft.rfftfreq(LOCK_NFFT, 1 / R)
    bins = np.arange(len(f))
    out = []
    for hop in (FRAME, UNLOCKED):
        X = frames(x, hop)
        P = (np.abs(X) ** 2).sum(0)
        vals = []
        for spacing in LOCK_SPACINGS:
            s = int(round(spacing / LOCK_BIN_HZ))
            c = np.abs((X[:, s:] * np.conj(X[:, :-s])).sum(0)) / np.sqrt(P[s:] * P[:-s] + 1e-30)
            sel = (bins[:-s] % 4 == 2) & (f[:-s] >= LOCK_BAND[0]) & (f[:-s] < LOCK_BAND[1])
            vals.append(c[sel].mean())
        out.append(float(np.mean(vals)))
    return out


def score(path):
    try:
        x = decode(path)
        if len(x) < R * 30:
            return {"file": str(path), "error": "too short"}
        rows = []
        for ch in range(2):
            v = x[:, ch]
            row = {"comb_db": comb_height(v)}
            row["pattern_db"], row["pattern_free_db"] = pattern_level(v)
            for name, (lo, hi) in RIPPLE_BANDS.items():
                row[name], row[name + "_free"] = energy_ripple(v, lo, hi)
            row["lock"], row["lock_free"] = lock(v)
            rows.append(row)
        out = {k: float(np.mean([r[k] for r in rows])) for k in rows[0]}
        out["file"] = str(path)
        return out
    except Exception as exc:
        return {"file": str(path), "error": str(exc)}


def line(label, s):
    if "error" in s:
        return f"{label:28s} {s['error']}"
    return (f"{label:28s} comb {s['comb_db']:+5.2f} dB | 20 ms pattern {s['pattern_db']:6.1f} dB (free {s['pattern_free_db']:6.1f}) | "
            f"ripple 6-12k {s['ripple_6_12']:4.1f}% (free {s['ripple_6_12_free']:3.1f})  12-17k {s['ripple_12_17']:4.1f}% (free {s['ripple_12_17_free']:3.1f}) | "
            f"lock {s['lock']:.3f} (free {s['lock_free']:.3f})")


def init():
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass


if __name__ == "__main__":
    args = sys.argv[1:]
    workers = 4
    save = None
    if args and args[0] == "--workers":
        workers, args = int(args[1]), args[2:]
    if args and args[0] == "--save":
        save, args = Path(args[1]), args[2:]
    results = []
    with Pool(workers, initializer=init) as pool:
        for s in pool.imap(score, args):
            results.append(s)
            print(line(Path(s["file"]).parent.name[:10] + "/" + Path(s["file"]).stem[:16], s), flush=True)
    if save:
        save.parent.mkdir(parents=True, exist_ok=True)
        save.write_text(json.dumps(results, indent=1), encoding="utf-8")
