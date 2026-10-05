import subprocess, sys
from pathlib import Path
sys.path.insert(0, r"E:\AI_RADIO\server")
import numpy as np, soundfile as sf, pyloudnorm as pyln
from scipy import signal
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from services.audio_master_service import reference_deviation_db, REFERENCE_GRID, REFERENCE_TOLERANCE_DB as TOL

name, tid, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
suno = out / f"{name}_suno.wav"
if not suno.exists():
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", rf"D:\catalog\mp3\{tid}.mp3", "-ar", "44100", "-c:a", "pcm_f32le", str(suno)], check=True)
versions = [("Suno raw", suno, "0.4"), ("v3 live", out / f"{name}_v3_live.wav", "tab:orange"), ("v4 new chain", Path(rf"D:\catalog\master_wav\{tid}.wav"), "tab:blue")]
PTS = [63, 125, 250, 500, 1000, 2000, 4000, 8000, 10000, 12500, 16000]


def lvl(x, r):
    return x * 10 ** ((-16 - pyln.Meter(r).integrated_loudness(x.T)) / 20)


print(f"{name}: vs industry average (No.1 singles 2000-2010), * = outside +/-{TOL:.0f} dB")
print("%-14s" % "Hz" + "".join("%7s" % (f"{p // 1000}k" if p >= 1000 else p) for p in PTS))
fig, ax = plt.subplots(figsize=(14, 6))
ax.fill_between(REFERENCE_GRID, -TOL, TOL, color="0.85", label=f"industry ±{TOL:.0f} dB")
tops = []
for tag, p, c in versions:
    x, r = sf.read(p, dtype="float64", always_2d=True)
    d = reference_deviation_db(x.T, r)
    ax.semilogx(REFERENCE_GRID, d, lw=1.8, color=c, label=tag)
    print("%-14s" % tag + "".join("%6.1f%s" % (v, "*" if abs(v) > TOL else " ") for v in (np.interp(np.log2(k), np.log2(REFERENCE_GRID), d) for k in PTS)))
    f, _, S = signal.stft(lvl(x.T, r).mean(0), r, nperseg=8192, noverlap=6144)
    pw = 10 * np.log10((np.abs(S) ** 2).mean(1) + 1e-30); body = pw[(f >= 500) & (f <= 2000)].mean()
    tops.append((tag, [pw[(f >= k * .98) & (f <= k * 1.02)].mean() - body for k in (15000, 16000, 17000, 17500, 18000, 19000, 20000)]))
    sf.write(out / f"{name}_{tag.split()[0]}.flac", np.clip(lvl(x.T, r).T, -1, 1), r, subtype="PCM_16")
print("\nTop end, dB vs the song's own 500-2000 Hz")
print("%-14s" % "Hz" + "".join("%7s" % k for k in ("15k", "16k", "17k", "17.5k", "18k", "19k", "20k")))
for tag, v in tops:
    print("%-14s" % tag + "".join("%+7.1f" % x for x in v))
ax.axhline(0, color="k"); ax.set_xlim(40, 20000); ax.set_ylim(-15, 15); ax.grid(True, which="both", alpha=.3)
ax.set_title(f"{name} vs industry average"); ax.set_ylabel("dB vs industry"); ax.set_xlabel("Hz"); ax.legend()
fig.tight_layout(); fig.savefig(out / f"{name}_vs_industry.png", dpi=80)
