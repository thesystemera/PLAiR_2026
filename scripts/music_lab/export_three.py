import asyncio, json, subprocess, sys
from pathlib import Path
sys.path.insert(0, r"E:\AI_RADIO\server")
import numpy as np, soundfile as sf, pyloudnorm as pyln  # noqa: E402
from config import settings  # noqa: E402
from services.audio_master_service import reference_deviation_db, REFERENCE_GRID, REFERENCE_TOLERANCE_DB as TOL  # noqa: E402

OUT = Path(r"E:\AI_RADIO\upscale_ab\v4_three_way"); OUT.mkdir(parents=True, exist_ok=True)
PTS = [63, 125, 250, 500, 1000, 2000, 4000, 8000, 10000, 12500, 16000]


def level(x, r):
    return x * 10 ** ((-16 - pyln.Meter(r).integrated_loudness(x.T)) / 20)


async def main(ids):
    from services.audio_master_service import AudioMasterService
    master = AudioMasterService()
    print("%-34s" % "dB vs industry (* = outside ±3)" + "".join("%7s" % (f"{p // 1000}k" if p >= 1000 else p) for p in PTS))
    for tid in ids:
        meta = json.loads((settings.CATALOG_DIR / "metadata" / f"{tid}.json").read_text(encoding="utf-8"))
        name = "".join(c if c.isalnum() else "_" for c in (meta.get("generation_params") or {}).get("title", tid)).strip("_")[:28]
        sonic = settings.SONIC_WAV_DIR / f"{tid}.wav"
        with_hiss = OUT / f"_{tid}_hiss.wav"
        no_hiss = OUT / f"_{tid}_nohiss.wav"
        suno = OUT / f"_{tid}_suno.wav"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(settings.CATALOG_DIR / "mp3" / f"{tid}.mp3"),
                        "-ar", "44100", "-c:a", "pcm_f32le", str(suno)], check=True)
        await master.master_audio_with_report(sonic, no_hiss, target_lufs=-16.0, correct=False, tone=True, hiss=False)
        await master.master_audio_with_report(sonic, with_hiss, target_lufs=-16.0, correct=False, tone=True, hiss=True)
        for i, (tag, path) in enumerate((("Suno", suno), ("v4_no_hiss", no_hiss), ("v4_tape_hiss", with_hiss)), 1):
            x, r = sf.read(path, dtype="float64", always_2d=True)
            y = level(x.T, r)
            sf.write(OUT / f"{name}_{i}_{tag}.flac", np.clip(y, -1, 1).T, r, subtype="PCM_16")
            if tag != "v4_tape_hiss":
                d = reference_deviation_db(x.T, r)
                print("%-34s" % f"{name[:20]} {tag}" + "".join("%6.1f%s" % (v, "*" if abs(v) > TOL else " ")
                                                              for v in (float(np.interp(np.log2(k), np.log2(REFERENCE_GRID), d)) for k in PTS)))
        suno.unlink(); no_hiss.unlink(); with_hiss.unlink()

asyncio.run(main(sys.argv[1:]))
