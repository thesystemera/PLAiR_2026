import asyncio, os, sys, time
from pathlib import Path
os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"] = sys.argv[1] if len(sys.argv) > 1 else "0"
sys.path.insert(0, r"E:\AI_RADIO\server")
import numpy as np, soundfile as sf, torch  # noqa: E402
from config import settings  # noqa: E402
SRC = Path(r"D:\catalog\premaster_wav\21955a90ec313b6f9c5bf542276eeec9.wav")
TMP = Path(r"E:\AI_RADIO\upscale_ab\_probe"); TMP.mkdir(parents=True, exist_ok=True)


async def main():
    x, r = sf.read(SRC, dtype="float32", always_2d=True)
    sf.write(TMP / "slice.wav", x[r * 60:r * 100 + 777], r, subtype="FLOAT")
    from services.audio_sonic_master_service import SonicMasterService, SUNO_SONIC_SETTINGS
    sonic = SonicMasterService(); sonic.configure(**SUNO_SONIC_SETTINGS); await sonic.initialize()
    print("precision:", sonic.precision, "| device:", torch.cuda.get_device_name(0), flush=True)
    outs = {}
    for tile in (0, 10):
        settings.SONIC_MASTER_VAE_TILE_S = tile
        torch.cuda.reset_peak_memory_stats(); t0 = time.time()
        await sonic.enhance_audio(TMP / "slice.wav", TMP / f"t{tile}.wav")
        outs[tile] = sf.read(TMP / f"t{tile}.wav", dtype="float64")[0]
        print(f"tile {tile or 'off'}: {time.time() - t0:.1f} s for 40 s, peak {torch.cuda.max_memory_allocated() / 2 ** 30:.2f} GB", flush=True)
    d = outs[10] - outs[0]
    print("tiled minus full: %.0f dB under the music" % (20 * np.log10(np.sqrt((d ** 2).mean()) / np.sqrt((outs[0] ** 2).mean()) + 1e-15)))
    await sonic.unload()
    for p in TMP.glob("*"):
        p.unlink()
    TMP.rmdir()

asyncio.run(main())
