import json, sys
from pathlib import Path

sys.path.insert(0, r"E:\AI_RADIO\scripts\music_lab")
import numpy as np, soundfile as sf, subprocess  # noqa: E402
from scipy import signal  # noqa: E402
from scipy.ndimage import uniform_filter1d  # noqa: E402
from suno_profile import MP3, R  # noqa: E402

OUT = Path(r"E:\AI_RADIO\upscale_ab\notch_test")
REPORT = json.loads((OUT / "notch_hypothesis.json").read_text(encoding="utf-8"))
NFFT, HOP, KEEP_ABOVE_DB, MAX_CUT_DB = 8192, 1024, 6.0, 8.0


def load(tid, start, seconds=20):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(max(0, start - 8)), "-t", str(seconds), "-i", str(MP3 / f"{tid}.mp3"),
                          "-ar", str(R), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64).reshape(-1, 2).T


def tame(x, targets):
    f, _, S = signal.stft(x, R, nperseg=NFFT, noverlap=NFFT - HOP)
    mag = np.abs(S).mean(0)
    gain = np.ones_like(mag)
    bin_hz = f[1] - f[0]
    for fr, width in targets:
        c = int(round(fr / bin_hz)); half = max(1, int(np.ceil(width / 2 / bin_hz)) + 1)
        lo, hi = c - half, c + half + 1
        neigh = np.concatenate([mag[max(0, lo - 20):lo], mag[hi:hi + 20]], axis=0)
        floor = np.median(neigh, axis=0) * 10 ** (KEEP_ABOVE_DB / 20)
        g = np.clip(floor[None, :] / (mag[lo:hi] + 1e-12), 10 ** (-MAX_CUT_DB / 20), 1.0)
        gain[lo:hi] = np.minimum(gain[lo:hi], uniform_filter1d(g, 5, axis=1))
    _, y = signal.istft(S * gain[None], R, nperseg=NFFT, noverlap=NFFT - HOP)
    y = y[:, :x.shape[1]]
    cut_db = 20 * np.log10(np.maximum(gain, 1e-6))
    rows = gain[np.any(gain < 0.999, axis=1)]
    return y, float(cut_db.min()), float(np.mean(rows < 0.89) * 100) if rows.size else 0.0


for tid, song in REPORT.items():
    peaks = song["scan"][:3]
    if not peaks:
        continue
    start = peaks[0]["loudest_at_s"]
    x = load(tid, start)
    y, deepest, share = tame(x, [(p["f"], p["width_hz"]) for p in peaks])
    name = "".join(c if c.isalnum() else "_" for c in song["title"])
    sf.write(OUT / f"{name}_1_raw.flac", np.clip(x, -1, 1).T, R, subtype="PCM_16")
    sf.write(OUT / f"{name}_2_ringing_tamed.flac", np.clip(y, -1, 1).T, R, subtype="PCM_16")
    removed = x - y
    sf.write(OUT / f"{name}_3_removed_only_plus12dB.flac", np.clip(removed * 4, -1, 1).T, R, subtype="PCM_16")
    rel = 20 * np.log10(np.sqrt((removed ** 2).mean()) / np.sqrt((x ** 2).mean()))
    freqs = ", ".join("%.0f Hz" % p["f"] for p in peaks)
    print(f"{song['title']}: tamed {freqs} | deepest cut {deepest:.1f} dB, "
          f"cutting more than 1 dB in {share:.0f}% of frames on those frequencies | removed signal {rel:.1f} dB under the music")
