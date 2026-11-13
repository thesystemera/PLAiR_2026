import json
import random
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, r"E:\AI_RADIO\server")
sys.path.insert(0, r"E:\scratch\plair\reference_masters")
from services.audio_headroom import source_cutoff_hz  # noqa: E402
from services.audio_master_service import REFERENCE_GRID, modern_master_target_db  # noqa: E402
from measure import RATE, REPORT_HZ, decode, density_db  # noqa: E402

ROOT = Path(r"E:\scratch\plair\reference_masters")
CATALOG = Path("D:/catalog")
WINDOW_S = 30


def loudest(x, seconds=WINDOW_S):
    size = seconds * RATE
    if x.shape[1] <= size:
        return x
    hop = RATE
    energy = np.array([(x[:, s:s + size] ** 2).mean() for s in range(0, x.shape[1] - size, hop)])
    start = int(np.argmax(energy)) * hop
    return x[:, start:start + size]


def at(curve):
    return [float(np.interp(np.log2(hz), np.log2(REFERENCE_GRID), curve)) for hz in REPORT_HZ]


def main(count):
    index = json.loads((ROOT / "index.json").read_text(encoding="utf-8"))
    charts, cutoffs = [], []
    for info in index.values():
        x = decode(ROOT / "previews" / info["file"])
        if x.shape[1] < RATE * 10:
            continue
        cut = source_cutoff_hz(x, RATE)
        cutoffs.append(cut)
        if cut >= 19000:
            charts.append(density_db(x))
    charts = np.array(charts)

    ids = []
    for p in (CATALOG / "metadata").glob("*.json"):
        m = json.loads(p.read_text(encoding="utf-8"))
        if m.get("master_chain_version") == 6 and (CATALOG / "mp3" / f"{p.stem}.mp3").exists() and (CATALOG / "master_wav" / f"{p.stem}.wav").exists():
            ids.append(p.stem)
    random.seed(11)
    ours, suno = [], []
    for track_id in random.sample(ids, min(count, len(ids))):
        master, _ = sf.read(str(CATALOG / "master_wav" / f"{track_id}.wav"), dtype="float64", always_2d=True)
        ours.append(density_db(loudest(master.T)))
        suno.append(density_db(loudest(decode(CATALOG / "mp3" / f"{track_id}.mp3"))))
    ours, suno = np.array(ours), np.array(suno)

    target = modern_master_target_db(REFERENCE_GRID)
    target = target - target[(REFERENCE_GRID >= 500) & (REFERENCE_GRID <= 2000)].mean()
    print(f"chart previews: {len(index)} downloaded, {len(charts)} reach 19 kHz and are used (encoder cutoff median {np.median(cutoffs):.0f} Hz)")
    print(f"our masters (version 6) and their Suno originals: {len(ours)} songs, loudest {WINDOW_S} s of each (previews are mostly the hook)")
    print("level against 500-2000 Hz, median (dB)")
    print("Hz               " + "".join(f"{hz:>8}" for hz in REPORT_HZ))
    rows = (("charts now", np.median(charts, axis=0)), ("our EQ target", None), ("Suno", np.median(suno, axis=0)), ("ours (v6)", np.median(ours, axis=0)))
    for label, curve in rows:
        values = at(target)[:11] if curve is None else at(curve)
        print(f"{label:16s} " + "".join(f"{v:+8.1f}" for v in values) + ("     (none)     (none)" if curve is None else ""))
    print("\nagainst the charts (dB, + = brighter than today's charts)")
    for label, curve in (("our EQ target", None), ("Suno", np.median(suno, axis=0)), ("ours (v6)", np.median(ours, axis=0))):
        values = at(target)[:11] if curve is None else at(curve)
        print(f"{label:16s} " + "".join(f"{v - c:+8.1f}" for v, c in zip(values, at(np.median(charts, axis=0)))))
    print("\ncharts spread 10% / 90%: " + " ".join(f"{a:+.0f}/{b:+.0f}" for a, b in zip(at(np.percentile(charts, 10, axis=0)), at(np.percentile(charts, 90, axis=0)))))
    np.save(ROOT / "chart_curves.npy", charts)


main(int(sys.argv[1]) if len(sys.argv) > 1 else 60)
