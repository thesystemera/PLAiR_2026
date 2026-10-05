import json, sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, r"E:\AI_RADIO\server")
import numpy as np  # noqa: E402
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from services.audio_master_service import REFERENCE_GRID, REFERENCE_TOLERANCE_DB as TOL  # noqa: E402

OUT = Path(r"E:\AI_RADIO\upscale_ab\suno_profile")
SRC = OUT / "profiles_v2.jsonl"
BANDS = ["250-1k", "1-2k", "2-4k", "4-8k", "8-12k", "12-16k"]
PTS = [63, 125, 250, 500, 1000, 2000, 4000, 8000, 10000, 12500, 16000]

rows = [json.loads(l) for l in SRC.read_text(encoding="utf-8").splitlines() if l.strip()]
rows = [r for r in rows if "error" not in r]
n = len(rows)
dev = np.array([r["dev"] for r in rows])
at = lambda curve, hz: float(np.interp(np.log2(hz), np.log2(REFERENCE_GRID), curve))
p10, p50, p90 = (np.percentile(dev, q, axis=0) for q in (10, 50, 90))

print(f"== {n} raw Suno songs vs the modern-master average (dB, aligned 500-2000 Hz)")
print("%-8s" % "Hz" + "".join("%7s" % (f"{p // 1000}k" if p >= 1000 else p) for p in PTS))
for name, c in (("median", p50), ("10%", p10), ("90%", p90)):
    print("%-8s" % name + "".join("%+7.1f" % at(c, p) for p in PTS))

tun = np.array([r["tuning_cents"] for r in rows])
print(f"\n== tuning vs A440: median {np.nanmedian(tun):+.0f} cents, {np.mean(np.abs(tun) <= 10) * 100:.0f}% within ±10 cents")

peaks = [(p[0], p[1], r["id"]) for r in rows for p in r["peaks"] if p[3] == "off"]
kinds = Counter(p[3] for r in rows for p in r["peaks"])
total = sum(kinds.values())
print(f"\n== stationary peaks: {total} ({total / n:.1f} per song): notes {100 * kinds['note'] / total:.0f}%, "
      f"harmonics of notes {100 * kinds['harmonic'] / total:.0f}%, NOT music {100 * kinds['off'] / total:.0f}%")
songs_with_off = len({p[2] for p in peaks})
print(f"   songs with at least one non-musical stationary peak: {songs_with_off} ({100 * songs_with_off / n:.0f}%)")
f_off = np.array([p[0] for p in peaks])
edges = np.exp(np.linspace(np.log(60), np.log(16000), 1600))
hist = np.zeros(len(edges) - 1, dtype=int)
for r in rows:
    fr = [p[0] for p in r["peaks"] if p[3] == "off"]
    idx = np.unique(np.digitize(fr, edges) - 1)
    idx = idx[(idx >= 0) & (idx < len(hist))]
    hist[idx] += 1
order = [i for i in np.argsort(hist)[::-1] if hist[i] >= 3][:15]
print("   recurring non-musical peaks (same frequency in several songs = Suno, not the song):")
for i in sorted(order, key=lambda k: edges[k]):
    c = (edges[i] * edges[i + 1]) ** 0.5
    pr = [p[1] for p in peaks if edges[i] <= p[0] < edges[i + 1]]
    print(f"   {c:8.1f} Hz  in {hist[i]:3d} songs ({100 * hist[i] / n:.1f}%), typical height {np.median(pr):.1f} dB")
names = ("<250", "250-1k", "1-4k", "4-8k", "8k+")
by_band = Counter(names[i] for i in np.digitize(f_off, [250, 1000, 4000, 8000]))
print("   where they sit: " + ", ".join(f"{k} {v}" for k, v in by_band.most_common()))

comb = np.array([r["comb_ms"] for r in rows]); cs = np.array([r["comb_strength"] for r in rows])
strong = cs >= 8
print(f"\n== comb filtering (flange signature): strong in {np.mean(strong) * 100:.0f}% of songs")
if strong.any():
    h, e = np.histogram(comb[strong], bins=np.arange(0.2, 5.01, 0.1))
    top = np.argsort(h)[::-1][:5]
    print("   most common delays: " + ", ".join(f"{e[i]:.1f}-{e[i + 1]:.1f} ms ({h[i]} songs)" for i in top))

cut = np.array([r["cutoff"] for r in rows])
holes = np.array([r["holes"] for r in rows]) * 100
rng = np.array([r["range"] for r in rows])
print(f"\n== MP3 cutoff: median {np.median(cut):.0f} Hz, 10-90% {np.percentile(cut, 10):.0f}-{np.percentile(cut, 90):.0f} Hz")
print("== per band            " + "".join("%9s" % b for b in BANDS))
print("holes >20 dB (mean %)  " + "".join("%9.1f" % v for v in holes.mean(0)))
print("envelope range (dB)    " + "".join("%9.1f" % v for v in np.median(rng, 0)))

fig, ax = plt.subplots(3, 1, figsize=(14, 15))
ax[0].fill_between(REFERENCE_GRID, -TOL, TOL, color="0.85", label="industry ±3 dB")
ax[0].fill_between(REFERENCE_GRID, p10, p90, color="tab:red", alpha=0.2, label="Suno 10-90% of songs")
ax[0].semilogx(REFERENCE_GRID, p50, color="tab:red", lw=2, label=f"Suno median ({n} songs)")
ax[0].axhline(0, color="k"); ax[0].set_xlim(40, 20000); ax[0].set_ylim(-20, 15); ax[0].grid(True, which="both", alpha=.3); ax[0].legend()
ax[0].set_title("Raw Suno vs the modern-master average"); ax[0].set_ylabel("dB vs industry")
ax[1].semilogx((edges[:-1] * edges[1:]) ** 0.5, 100 * hist / n, lw=1); ax[1].set_xlim(60, 16000); ax[1].grid(True, which="both", alpha=.3)
ax[1].set_title("Non-musical stationary peaks (notes and their harmonics removed): % of songs at each frequency"); ax[1].set_ylabel("% of songs")
ax[2].hist(comb[strong], bins=np.arange(0.2, 5.01, 0.1), color="tab:purple"); ax[2].set_xlabel("comb delay (ms)")
ax[2].set_title("Comb-filter delay in songs with a strong comb (a pile-up = a shared Suno flange)")
fig.tight_layout(); fig.savefig(OUT / "suno_profile.png", dpi=80)
print(f"\nplot: {OUT / 'suno_profile.png'}")
