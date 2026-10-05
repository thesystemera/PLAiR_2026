import json, sys
from pathlib import Path

import numpy as np
from scipy import signal
from scipy.ndimage import median_filter, uniform_filter1d

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(r"E:\AI_RADIO\upscale_ab\decoder_fingerprint")
R = 48000
NFFT = 65536
ENV_RATE = 2000
ENV_NFFT = 16384
SPEC_KINDS = ["mid_log", "mid_pow", "side_log"]
ENV_KINDS = ["env0", "env1", "env2"]
ENV_LABELS = ["1-4 kHz", "4-8 kHz", "8-16 kHz"]


def load(name, kind):
    ok = np.array(json.loads((OUT / name / "files.json").read_text(encoding="utf-8"))["ok"])
    return np.load(OUT / name / f"{kind}.npy", mmap_mode="r")[ok].astype(np.float32)


def consensus(a, song_width, curve_width):
    a = a - uniform_filter1d(a, song_width, axis=1, mode="nearest")
    med = np.median(a, axis=0)
    share = np.mean(a > 3.0, axis=0)
    resid = med - median_filter(med, size=curve_width, mode="nearest")
    return resid, share


def robust_sigma(resid, width):
    return 1.4826 * median_filter(np.abs(resid), size=width, mode="nearest") + 1e-6


def cents_from_note(f):
    c = 1200 * np.log2(np.maximum(f, 1e-9) / 440.0)
    return ((c + 50) % 100) - 50


def peaks_of(resid, freqs, lo, hi, min_db, sigma_width=2001):
    z = resid / robust_sigma(resid, sigma_width)
    idx = signal.find_peaks(resid, height=min_db, distance=3)[0]
    idx = idx[(freqs[idx] >= lo) & (freqs[idx] <= hi) & (z[idx] >= 6)]
    return idx, z


def spacing(resid, freqs, lo, hi, step):
    m = (freqs >= lo) & (freqs <= hi)
    v = resid[m] - resid[m].mean()
    spec = np.abs(np.fft.rfft(v * np.hanning(len(v))))
    quef = np.fft.rfftfreq(len(v), d=step)
    keep = (quef > 1 / 3000) & (quef < 1 / 8)
    order = np.argsort(spec[keep])[::-1][:6]
    floor = np.median(spec[keep])
    return [(1 / quef[keep][i], spec[keep][i] / floor) for i in order]


def describe(name, kind, freqs, resid, share, lo, hi, min_db, control=None, limit=40):
    idx, z = peaks_of(resid, freqs, lo, hi, min_db)
    print(f"\n{name} {kind}: {len(idx)} consensus peaks >= {min_db} dB between {lo} and {hi} Hz")
    order = idx[np.argsort(resid[idx])[::-1]][:limit]
    for i in sorted(order):
        j0, j1 = max(0, i - 1), i + 2
        line = f"  {freqs[i]:9.2f} Hz  +{resid[i]:.2f} dB  z {z[i]:5.1f}  songs>3dB {100 * share[j0:j1].max():4.1f}%  note {cents_from_note(freqs[i]):+5.1f} c"
        if control is not None:
            line += f"  control {control[j0:j1].max():+.2f} dB"
        print(line)
    return idx


def main():
    f = np.fft.rfftfreq(NFFT, 1 / R)
    fe = np.fft.rfftfreq(ENV_NFFT, 1 / ENV_RATE)
    have_control = (OUT / "control" / "files.json").exists()
    target = sys.argv[1] if len(sys.argv) > 1 else "suno"
    fig, axes = plt.subplots(len(SPEC_KINDS) + len(ENV_KINDS), 1, figsize=(18, 22))
    for ax, kind in zip(axes, SPEC_KINDS):
        resid, share = consensus(load(target, kind), 401, 301)
        cres = consensus(load("control", kind), 401, 301)[0] if have_control else None
        idx = describe(target, kind, f, resid, share, 30, 20000, 0.3, cres)
        print("  regular spacings 4-16 kHz (Hz, x floor):", ", ".join(f"{s:.1f} ({k:.1f}x)" for s, k in spacing(resid, f, 4000, 16000, f[1])))
        print("  regular spacings 0.2-4 kHz (Hz, x floor):", ", ".join(f"{s:.1f} ({k:.1f}x)" for s, k in spacing(resid, f, 200, 4000, f[1])))
        ax.plot(f, resid, lw=0.5, label=f"{target} (median of songs)")
        if cres is not None:
            ax.plot(f, cres - 3, lw=0.5, label="MP3 control (pink noise), -3 dB offset")
        ax.plot(f[idx], resid[idx], "r.", ms=4)
        ax.set_xlim(0, 20000); ax.set_ylim(-5, 5); ax.set_title(f"{kind}: narrow features every song shares"); ax.legend(loc="upper right"); ax.grid(alpha=0.3)
        np.save(OUT / f"{target}_{kind}_consensus.npy", np.stack([f, resid, share]))
    for ax, kind, label in zip(axes[len(SPEC_KINDS):], ENV_KINDS, ENV_LABELS):
        resid, share = consensus(load(target, kind), 201, 151)
        cres = consensus(load("control", kind), 201, 151)[0] if have_control else None
        idx = describe(target, f"envelope {label}", fe, resid, share, 15, 990, 0.3, cres)
        ax.plot(fe, resid, lw=0.5, label=f"{target} (median of songs)")
        if cres is not None:
            ax.plot(fe, cres - 3, lw=0.5, label="MP3 control, -3 dB offset")
        ax.plot(fe[idx], resid[idx], "r.", ms=4)
        ax.set_xlim(0, 1000); ax.set_ylim(-5, 5); ax.set_title(f"level modulation of the {label} band"); ax.legend(loc="upper right"); ax.grid(alpha=0.3)
        np.save(OUT / f"{target}_{kind}_consensus.npy", np.stack([fe, resid, share]))
    fig.tight_layout()
    fig.savefig(OUT / f"{target}_fingerprint.png", dpi=110)
    print("\nplot:", OUT / f"{target}_fingerprint.png")


if __name__ == "__main__":
    main()
