import json

import numpy as np
from scipy.ndimage import median_filter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import decoder_fingerprint_report as rep

OUT = rep.OUT
SURROUND_BINS = 41


def curve(name):
    a = rep.load(name, "mid_log")
    return a.shape[0], np.median(a - median_filter(a, size=(1, SURROUND_BINS), mode="nearest"), axis=0)


def scores(path, wanted):
    return [x for x in json.loads((OUT / path).read_text(encoding="utf-8")) if "error" not in x and wanted(x["file"].replace("\\", "/"))]


def main():
    f = np.fft.rfftfreq(rep.NFFT, 1 / rep.R)
    n, suno = curve("suno")
    nc, control = curve("control")
    sample = scores("sample100.json", lambda p: "control_src" not in p)
    plain = scores("sample100.json", lambda p: "control_src" in p)
    stage = lambda key: float(np.mean([x["lock"] for x in scores("stages.json", lambda p: f"/{key}/" in p and "user_" not in p)]))
    real = scores("stages.json", lambda p: "user_" in p)
    fig, ax = plt.subplots(3, 1, figsize=(11, 12))
    m = (f >= 7700) & (f <= 16300)
    ax[0].plot(f[m] / 1000, suno[m], lw=0.7, color="#c0392b", label=f"Suno, median of {n} songs")
    ax[0].plot(f[m] / 1000, control[m] - 3, lw=0.7, color="#555", label=f"plain MP3 at the same settings ({nc} noise files), shifted down 3 dB")
    ax[0].set_ylim(-5, 11)
    ax[0].set_xlabel("kHz")
    ax[0].set_title("Every Suno song has the same narrow tones, exactly 200 Hz apart")
    m = (f >= 20) & (f <= 420)
    ax[1].plot(f[m], suno[m], lw=0.9, color="#c0392b", label="Suno")
    ax[1].plot(f[m], control[m] - 3, lw=0.9, color="#555", label="plain MP3, shifted down 3 dB")
    for hz in range(50, 401, 50):
        ax[1].axvline(hz, color="#2980b9", lw=0.5, alpha=0.5)
    ax[1].set_xlabel("Hz")
    ax[1].set_title("Low end: sharp tones at 50 and 100 Hz (blue = multiples of 50 Hz); the broad bumps are musical notes, all songs share A440 tuning")
    for a in ax[:2]:
        a.set_ylabel("dB above the surrounding 30 Hz")
        a.legend(loc="upper right")
    bins = np.linspace(0, 0.21, 43)
    ax[2].hist([x["lock"] for x in sample], bins=bins, color="#c0392b", alpha=0.85, label=f"raw Suno, {len(sample)} random songs")
    ax[2].hist([x["lock_free"] for x in sample], bins=bins, color="#999", alpha=0.8, label="the same songs measured off the decoder grid (chance)")
    top = ax[2].get_ylim()[1]
    marks = [(float(np.mean([x["lock"] for x in plain])), "plain MP3", "#555", 0.95), (stage("sonic_wav"), "4 test songs after SonicMaster 33% (= v4 master)", "#27ae60", 0.95),
             (stage("mp3"), "the same 4 songs raw", "#c0392b", 0.75)]
    if real:
        marks.append((real[0]["lock"], "real music (one upload)", "#8e44ad", 0.85))
    for value, label, colour, height in marks:
        ax[2].axvline(value, color=colour, lw=2, ls="--")
        ax[2].text(value + 0.002, top * height, label, color=colour, fontsize=9)
    ax[2].set_xlabel("decoder lock score (how strongly the highs move in step with Suno's 20 ms frames)")
    ax[2].set_ylabel("songs")
    ax[2].legend(loc="center right")
    ax[2].set_title("Lock score: every song far above chance; SonicMaster roughly halves it")
    for a in ax:
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "suno_decoder_fingerprint.png", dpi=120)
    print("saved", OUT / "suno_decoder_fingerprint.png")


if __name__ == "__main__":
    main()
