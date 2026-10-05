import sys, json
import numpy as np, soundfile as sf, pyloudnorm as pyln
from scipy import signal
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

R = 48000


def load(path, t0, dur):
    a, r = sf.read(path, dtype="float64", always_2d=True)
    if r != R:
        a = signal.resample_poly(a, R, r, axis=0)
    return a[int(t0 * R):int((t0 + dur) * R)]


def align(o, x):
    n = min(len(o), len(x), 10 * R)
    a, b = o[:n].mean(1), x[:n].mean(1)
    c = signal.correlate(b, a, mode="full", method="fft")
    lag = int(np.argmax(c)) - (n - 1)
    lag = lag if abs(lag) < R // 2 else 0
    if lag > 0:
        x = x[lag:]
    elif lag < 0:
        x = np.vstack([np.zeros((-lag, x.shape[1])), x])
    m = min(len(o), len(x))
    return o[:m], x[:m], lag


def spec(m, nfft, hop):
    f, t, S = signal.stft(m, R, nperseg=nfft, noverlap=nfft - hop, boundary=None, padded=False)
    return f, t, np.abs(S) ** 2


def band_db(Po, Px, f, lo, hi):
    k = (f >= lo) & (f < hi)
    return 10 * np.log10(Px[k].sum() / max(Po[k].sum(), 1e-20) + 1e-20)


def flatness(P, f, lo, hi):
    k = (f >= lo) & (f < hi)
    p = P[k] + 1e-20
    fl = np.exp(np.log(p).mean(0)) / p.mean(0)
    w = P[k].sum(0)
    return float((fl * w).sum() / max(w.sum(), 1e-20))


def main():
    orig, ot0, res, rt0, dur, out, label = sys.argv[1], float(sys.argv[2]), sys.argv[3], float(sys.argv[4]), float(sys.argv[5]), sys.argv[6], sys.argv[7]
    o, x, lag = align(load(orig, ot0, dur), load(res, rt0, dur))
    meter = pyln.Meter(R)
    lo_, lx_ = meter.integrated_loudness(o), meter.integrated_loudness(x)
    po_, px_ = 20 * np.log10(np.abs(o).max()), 20 * np.log10(np.abs(x).max())
    x = x * 10 ** ((lo_ - lx_) / 20)
    mo, mx = o.mean(1), x.mean(1)

    f, t, So = spec(mo, 4096, 1024)
    _, _, Sx = spec(mx, 4096, 1024)
    bands = [(100, 500), (500, 2000), (2000, 4000), (4000, 8000), (8000, 12000), (12000, 16000), (16000, 18000), (18000, 22000)]
    bdb = [band_db(So, Sx, f, a, b) for a, b in bands]

    fc, tc, Co = spec(mo, 8192, 4800)
    _, _, Cx = spec(mx, 8192, 4800)
    centers = 1000 * 2 ** (np.arange(-14, 14) / 3)
    centers = centers[(centers >= 50) & (centers <= 20000)]
    edges = [(c / 2 ** (1 / 6), c * 2 ** (1 / 6)) for c in centers]
    Bo = np.array([Co[(fc >= a) & (fc < b)].sum(0) for a, b in edges])
    Bx = np.array([Cx[(fc >= a) & (fc < b)].sum(0) for a, b in edges])
    ok = np.array([((fc >= a) & (fc < b)).any() for a, b in edges])
    Bo, Bx = Bo[ok], Bx[ok]
    active = 10 * np.log10(Bo + 1e-20) > 10 * np.log10(Bo.max()) - 60
    d = 10 * np.log10((Bx + 1e-20) / (Bo + 1e-20))[active]
    cut6, cut12 = 100 * (d < -6).mean(), 100 * (d < -12).mean()
    add6 = 100 * (d > 6).mean()

    fo, fx = flatness(So, f, 8000, 22000), flatness(Sx, f, 8000, 22000)

    ref = So.max()
    Do, Dx = 10 * np.log10(So / ref + 1e-20), 10 * np.log10(Sx / ref + 1e-20)
    fig, ax = plt.subplots(2, 1, figsize=(16, 9), sharex=True)
    for a_, D, name in ((ax[0], Do, "ORIGINAL"), (ax[1], Dx, label)):
        im = a_.pcolormesh(t, f / 1000, D, vmin=-100, vmax=0, cmap="magma", shading="auto")
        a_.set_ylim(0, 22); a_.set_ylabel("kHz"); a_.set_title(name)
    ax[1].set_xlabel("s (from %.0f s)" % ot0)
    fig.colorbar(im, ax=ax, label="dB (same scale)")
    fig.savefig(out + "_spectrogram.png", dpi=90); plt.close(fig)

    k = f <= 8000
    diff = 10 * np.log10((Sx[k] + 1e-20) / (So[k] + 1e-20))
    diff[Do[k] < -90] = 0
    fig, a_ = plt.subplots(1, 1, figsize=(16, 6))
    im = a_.pcolormesh(t, f[k] / 1000, np.clip(diff, -18, 18), vmin=-18, vmax=18, cmap="RdBu", shading="auto")
    a_.set_ylabel("kHz"); a_.set_xlabel("s (from %.0f s)" % ot0)
    a_.set_title("%s minus ORIGINAL, 0-8 kHz (red = removed, blue = added)" % label)
    fig.colorbar(im, ax=a_, label="dB")
    fig.savefig(out + "_changemap.png", dpi=90); plt.close(fig)

    names = ["0.1-0.5", "0.5-2", "2-4", "4-8", "8-12", "12-16", "16-18", "18-22"]
    r = {
        "label": label, "lag_samples": lag,
        "loudness": "ORIGINAL %.1f LUFS, peak %.1f dBFS | %s %.1f LUFS, peak %.1f dBFS" % (lo_, po_, label, lx_, px_),
        "bands": "level-matched: " + ", ".join("%s %+.1f" % (n, v) for n, v in zip(names, bdb)),
        "gating": "cells cut >6 dB %.1f%%, >12 dB %.1f%% (added >6 dB %.1f%%)" % (cut6, cut12, add6),
        "flatness_8_22k": "ORIGINAL %.3f, %s %.3f (lower = more tonal)" % (fo, label, fx),
    }
    json.dump(r, open(out + "_numbers.json", "w"), indent=1)
    for v in r.values():
        print(v)


main()
