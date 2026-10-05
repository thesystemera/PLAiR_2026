import asyncio, os, sys, time
from pathlib import Path

os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"] = sys.argv[1] if len(sys.argv) > 1 else "0"
sys.path.insert(0, r"E:\AI_RADIO\server")
import soundfile as sf, torch  # noqa: E402

SRC = Path(r"D:\catalog\premaster_wav\21955a90ec313b6f9c5bf542276eeec9.wav")
TMP = Path(r"E:\AI_RADIO\upscale_ab\_probe"); TMP.mkdir(parents=True, exist_ok=True)
GB = 2 ** 30


async def main():
    x, r = sf.read(SRC, dtype="float32", always_2d=True)
    sf.write(TMP / "slice.wav", x[r * 60:r * 90], r, subtype="FLOAT")
    from services.audio_sonic_master_service import SonicMasterService, SUNO_SONIC_SETTINGS
    sonic = SonicMasterService(); sonic.configure(**SUNO_SONIC_SETTINGS); await sonic.initialize()
    print(f"after load: allocated {torch.cuda.memory_allocated() / GB:.2f} GB, reserved {torch.cuda.memory_reserved() / GB:.2f} GB, "
          f"peak {torch.cuda.max_memory_allocated() / GB:.2f} GB", flush=True)
    vae, model = sonic.vae, sonic.model
    stages = {}
    enc, dec, flow = vae.encode, vae.decode, model.inference_flow

    def wrap(name, fn):
        def inner(*a, **k):
            torch.cuda.synchronize(); torch.cuda.reset_peak_memory_stats(); base = torch.cuda.memory_allocated(); t0 = time.time()
            out = fn(*a, **k)
            torch.cuda.synchronize()
            s = stages.setdefault(name, [0.0, 0.0, 0])
            s[0] = max(s[0], (torch.cuda.max_memory_allocated() - base) / GB); s[1] += time.time() - t0; s[2] += 1
            return out
        return inner
    vae.encode, vae.decode, model.inference_flow = wrap("vae encode", enc), wrap("vae decode", dec), wrap("diffusion 20 steps", flow)
    for run in (1, 2):
        stages.clear(); torch.cuda.reset_peak_memory_stats(); t0 = time.time()
        await sonic.enhance_audio(TMP / "slice.wav", TMP / f"out{run}.wav")
        print(f"run {run}: {time.time() - t0:.1f} s for 30 s | reserved {torch.cuda.memory_reserved() / GB:.2f} GB | "
              + " | ".join(f"{k} +{v[0]:.2f} GB {v[1]:.1f} s x{v[2]}" for k, v in stages.items()), flush=True)
    await sonic.unload()
    for p in TMP.glob("*"):
        p.unlink()
    TMP.rmdir()

asyncio.run(main())
