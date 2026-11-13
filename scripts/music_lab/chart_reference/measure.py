import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy import signal

sys.path.insert(0, r"E:\AI_RADIO\server")
from services.audio_headroom import source_cutoff_hz  # noqa: E402
from services.audio_master_service import REFERENCE_GRID, modern_master_target_db  # noqa: E402

ROOT = Path(r"E:\scratch\plair\reference_masters")
RATE = 44100
REPORT_HZ = (63, 125, 250, 500, 1000, 2000, 4000, 8000, 10000, 12500, 16000, 18000, 20000)


def decode(path):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-ac", "2", "-ar", str(RATE), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64).reshape(-1, 2).T


def density_db(x):
    freqs, _, spec = signal.stft(x.mean(axis=0), RATE, nperseg=4096, noverlap=2048)
    power = 10 * np.log10(np.mean(np.abs(spec) ** 2, axis=1) + 1e-30)
    grid = np.interp(np.log2(REFERENCE_GRID), np.log2(np.maximum(freqs, 1.0)), power)
    from scipy.ndimage import gaussian_filter1d
    per_octave = len(REFERENCE_GRID) / np.log2(REFERENCE_GRID[-1] / REFERENCE_GRID[0])
    smooth = gaussian_filter1d(grid, per_octave / 3 / 2.355, mode="nearest")
    align = (REFERENCE_GRID >= 500) & (REFERENCE_GRID <= 2000)
    return smooth - smooth[align].mean()


def main():
    index = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))
    curves, cutoffs = [], []
    for track_id, info in index.items():
        x = decode(ROOT / "previews" / info["file"])
        if x.shape[1] < RATE * 10:
            continue
        cutoffs.append(source_cutoff_hz(x, RATE))
        curves.append(density_db(x))
    curves = np.array(curves)
    target = modern_master_target_db(REFERENCE_GRID)
    target = target - target[(REFERENCE_GRID >= 500) & (REFERENCE_GRID <= 2000)].mean()
    at = lambda c: [float(np.interp(np.log2(hz), np.log2(REFERENCE_GRID), c)) for hz in REPORT_HZ]
    print(f"{len(curves)} previews | encoder cutoff: median {np.median(cutoffs):.0f} Hz, lowest {np.min(cutoffs):.0f} Hz, "
          f"{np.mean(np.array(cutoffs) >= 19500) * 100:.0f}% reach 19.5 kHz or more")
    print("Hz           " + "".join(f"{hz:>8}" for hz in REPORT_HZ))
    print("our curve    " + "".join(f"{v:+8.1f}" for v in at(target)[:11]) + "     (none)")
    print("charts median" + "".join(f"{v:+8.1f}" for v in at(np.median(curves, axis=0))))
    print("charts 10%   " + "".join(f"{v:+8.1f}" for v in at(np.percentile(curves, 10, axis=0))))
    print("charts 90%   " + "".join(f"{v:+8.1f}" for v in at(np.percentile(curves, 90, axis=0))))
    print("charts minus our curve" + "".join(f"{a - b:+8.1f}" for a, b in zip(at(np.median(curves, axis=0))[:11], at(target)[:11])))
    np.save(ROOT / "chart_curves.npy", curves)


if __name__ == "__main__":
    main()
