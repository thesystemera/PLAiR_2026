import asyncio, json, os, sys
from pathlib import Path

os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
sys.path.insert(0, r"E:\AI_RADIO\server")
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np, soundfile as sf, pyloudnorm as pyln  # noqa: E402
from scipy import signal  # noqa: E402
from config import settings  # noqa: E402
from services.audio_headroom import spectrally_balanced_blend, write_float_wav  # noqa: E402
import decoder_lock  # noqa: E402

OUT = Path(r"E:\AI_RADIO\upscale_ab\sonic_wet"); OUT.mkdir(parents=True, exist_ok=True)
WETS = (0.33, 0.5, 1.0)
BANDS = ((60, 250), (250, 1000), (1000, 4000), (4000, 8000), (8000, 12000), (12000, 16000))


def read(path, rate=None):
    x, r = sf.read(path, dtype="float64", always_2d=True)
    if rate and r != rate:
        x = signal.resample_poly(x, rate, r, axis=0)
        r = rate
    return x, r


def level(x, r):
    return x * 10 ** ((-16 - pyln.Meter(r).integrated_loudness(x)) / 20)


def band_db(x, r):
    f, p = signal.welch(x.mean(axis=1), r, nperseg=8192)
    return [10 * np.log10(p[(f >= lo) & (f < hi)].mean() + 1e-20) for lo, hi in BANDS]


def quiet_db(x, r):
    hop = int(r * 0.1)
    n = len(x) // hop
    rms = np.sqrt((x[:n * hop].reshape(n, hop, -1) ** 2).mean(axis=(1, 2)) + 1e-12)
    db = 20 * np.log10(rms)
    return float(np.percentile(db[db > -70], 10))


async def main(ids):
    from services.audio_sonic_master_service import SonicMasterService, SUNO_SONIC_SETTINGS
    from services.audio_master_service import AudioMasterService
    sonic = SonicMasterService()
    sonic.configure(**{**SUNO_SONIC_SETTINGS, "wet_mix": 1.0})
    await sonic.initialize()
    master = AudioMasterService()
    rows = []
    for tid in ids:
        meta = json.loads((settings.CATALOG_DIR / "metadata" / f"{tid}.json").read_text(encoding="utf-8"))
        name = "".join(c if c.isalnum() else "_" for c in (meta.get("generation_params") or {}).get("title", tid)).strip("_")[:28]
        premaster = settings.PREMASTER_WAV_DIR / f"{tid}.wav"
        pure = OUT / f"_{tid}_pure.wav"
        if not pure.exists():
            ok = await sonic.enhance_audio(premaster, pure, wet_mix=1.0)
            if not ok:
                print(f"{name}: SonicMaster failed"); continue
        wet, r = read(pure)
        dry, _ = read(premaster, r)
        n = min(len(wet), len(dry))
        wet, dry = wet[:n].T, dry[:n].T
        suno_wav = OUT / f"_{tid}_suno.wav"
        os.system(f'ffmpeg -loglevel error -y -i "{settings.CATALOG_DIR / "mp3" / (tid + ".mp3")}" -ar 44100 -c:a pcm_f32le "{suno_wav}"')
        files = [(f"{name}_0_Suno_raw.flac", suno_wav)]
        for w in WETS:
            blended, _ = spectrally_balanced_blend(wet, dry, w, r, max_boost_db=3.5 if settings.SONIC_MASTER_BLEND_COMPENSATION else 0.0)
            mix = OUT / f"_{tid}_{int(w * 100)}.wav"
            write_float_wav(mix, blended, r)
            done = OUT / f"_{tid}_{int(w * 100)}_master.wav"
            await master.master_audio_with_report(mix, done, target_lufs=-16.0, correct=False, tone=True, hiss=True)
            label = {0.33: "1_current_SonicMaster_33", 0.5: "2_February_SonicMaster_50", 1.0: "3_SonicMaster_100"}[w]
            files.append((f"{name}_{label}.flac", done))
            mix.unlink()
        for fname, src in files:
            x, rr = read(src)
            y = np.clip(level(x, rr), -1, 1)
            sf.write(OUT / fname, y, rr, subtype="PCM_16")
            rows.append((fname, band_db(y, rr), quiet_db(y, rr), decoder_lock.score(OUT / fname)))
        for _, src in files:
            Path(src).unlink(missing_ok=True)
    ref = {}
    print("%-50s" % "file (bands dB vs Suno raw)" + "".join("%9s" % f"{lo // 1000 if lo >= 1000 else lo}-{hi // 1000}k" for lo, hi in BANDS) + "  quiet10%  lock   12-17k ripple")
    for fname, bands, quiet, lk in rows:
        song = fname.split("_0_")[0] if "_0_" in fname else None
        if song:
            ref = {"bands": bands, "quiet": quiet}
        print("%-50s" % fname[:50] + "".join("%9.1f" % (b - a) for a, b in zip(ref["bands"], bands)) +
              "%9.1f" % (quiet - ref["quiet"]) + "  %.3f" % lk.get("lock", float("nan")) + "  %5.1f%%" % lk.get("ripple_12_17", float("nan")))

asyncio.run(main(sys.argv[1:]))
