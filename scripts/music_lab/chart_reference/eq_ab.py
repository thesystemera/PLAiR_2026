import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy import signal

sys.path.insert(0, r"E:\AI_RADIO\server")
sys.path.insert(0, r"E:\scratch\plair\reference_masters")
from services import audio_master_service as ams  # noqa: E402
from measure import density_db  # noqa: E402

ROOT = Path(r"E:\scratch\plair\reference_masters")
OUT = ROOT / "eq_ab"
OUT.mkdir(exist_ok=True)
SONIC = Path(r"E:\AI_RADIO\upscale_ab\chain_ab\_sonic")
CATALOG = Path("D:/catalog")
OLD = {"MODERN_MASTER_HZ": np.array([63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 10000.0, 12500.0, 16000.0]),
       "MODERN_MASTER_DB": np.array([22.8, 15.5, 10.6, 5.5, 0.0, -4.1, -9.1, -16.2, -18.2, -22.5, -30.8]),
       "REFERENCE_RANGE_HZ": (40.0, 16000.0)}
NEW = {name: getattr(ams, name) for name in OLD}
REPORT = (4000, 8000, 12500, 16000, 17000, 18000, 19000)


def use(values):
    for name, value in values.items():
        setattr(ams, name, value)


def flip(a, b, rate, seconds=5):
    n = min(len(a), len(b))
    seg, fade = seconds * rate, int(0.010 * rate)
    gain = np.zeros(n)
    for k in range(0, n, seg):
        if (k // seg) % 2 == 1:
            gain[k:k + seg] = 1.0
    kernel = np.hanning(2 * fade + 1)
    gain = np.convolve(gain, kernel / kernel.sum(), mode="same")[:, None]
    out = a[:n] * (1 - gain) + b[:n] * gain
    t = np.arange(int(0.07 * rate)) / rate
    for k in range(0, n - len(t), seg):
        out[k:k + len(t)] += (0.04 * np.hanning(len(t)) * np.sin(2 * np.pi * (1760.0 if (k // seg) % 2 == 1 else 440.0) * t))[:, None]
    return out


def quiet_top(x, rate):
    sos = signal.butter(6, 16000, "highpass", fs=rate, output="sos")
    y = signal.sosfiltfilt(sos, x.mean(axis=1))
    hop = rate // 10
    k = len(y) // hop
    level = 10 * np.log10((y[:k * hop].reshape(k, hop) ** 2).mean(axis=1) + 1e-20)
    full = 10 * np.log10((x[:k * hop].mean(axis=1).reshape(k, hop) ** 2).mean(axis=1) + 1e-20)
    return float(np.median(level[full < np.percentile(full, 10)]))


async def main():
    master = ams.AudioMasterService()
    chart = np.median(np.load(ROOT / "chart_curves_all.npy"), axis=0)
    at = lambda c, hz: float(np.interp(np.log2(hz), np.log2(ams.REFERENCE_GRID), c))
    for sonic in sorted(SONIC.glob("*_v6.wav")):
        track_id = sonic.stem.split("_")[0]
        title = json.loads((CATALOG / "metadata" / f"{track_id}.json").read_text(encoding="utf-8"))["generation_params"]["title"]
        files = {}
        for label, values in (("old", OLD), ("new", NEW)):
            use(values)
            out = OUT / f"_{track_id[:8]}_{label}.wav"
            _, info = await master.master_audio_with_report(sonic, out, target_lufs=-16.0, correct=False, tone=True, hiss=True, resonance=True)
            x, rate = sf.read(str(out), dtype="float64", always_2d=True)
            files[label] = (x * 10 ** ((-16 - pyln.Meter(rate).integrated_loudness(x)) / 20), info)
        use(NEW)
        (a, info_a), (b, info_b) = files["old"], files["new"]
        print(f"\n{title}")
        print(f"  EQ moves, old target: {', '.join(info_a.get('tonal_fixes') or ['none'])}")
        print(f"  EQ moves, new target: {', '.join(info_b.get('tonal_fixes') or ['none'])}")
        for label, x in (("old", a), ("new", b)):
            curve = density_db(x.T)
            print(f"  {label} vs today's charts: " + "  ".join(f"{hz // 1000}k {at(curve, hz) - at(chart, hz):+.1f}" for hz in REPORT))
        print(f"  16-20 kHz in the quietest tenth of the song: new minus old {quiet_top(b, rate) - quiet_top(a, rate):+.1f} dB")
        name = "".join(c if c.isalnum() or c in " &'" else " " for c in title).strip()
        for label, x in ((f"{name} - A - old EQ target (paper, to 16 kHz)", a), (f"{name} - B - new EQ target (today's charts, 40 Hz to 19 kHz)", b),
                         (f"{name} - flip A and B every 5 s (low beep old, high beep new)", flip(a, b, rate))):
            sf.write(str(OUT / f"{label}.flac"), np.clip(x, -1, 1), rate, subtype="PCM_16")
        for label in ("old", "new"):
            (OUT / f"_{track_id[:8]}_{label}.wav").unlink()


asyncio.run(main())
