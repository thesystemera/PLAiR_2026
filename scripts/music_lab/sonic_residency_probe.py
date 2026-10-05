import asyncio, os, subprocess, sys, threading, time
from pathlib import Path

os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"] = sys.argv[1] if len(sys.argv) > 1 else "0"
sys.path.insert(0, r"E:\AI_RADIO\server")
import numpy as np, soundfile as sf, torch  # noqa: E402

SRC = Path(r"D:\catalog\premaster_wav\21955a90ec313b6f9c5bf542276eeec9.wav")
TMP = Path(r"E:\AI_RADIO\upscale_ab\_probe"); TMP.mkdir(parents=True, exist_ok=True)
GPU = int(os.environ["CUDA_VISIBLE_DEVICES"])


def shared_mb():
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          f"(Get-Counter '\\GPU Process Memory(pid_{os.getpid()}*)\\Shared Usage').CounterSamples | Measure-Object CookedValue -Sum | % Sum"],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0) / 2 ** 20


def gpu_used_mb():
    out = subprocess.run(["nvidia-smi", "--query-gpu=index,memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout
    return {int(a): int(b) for a, b in (l.split(",") for l in out.strip().splitlines())}[GPU]


async def timed_sonic(sonic, label):
    peak = {"shared": 0.0, "used": 0}
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            peak["shared"] = max(peak["shared"], shared_mb()); peak["used"] = max(peak["used"], gpu_used_mb())
            time.sleep(1.0)
    t = threading.Thread(target=sample, daemon=True); t.start()
    t0 = time.time()
    await sonic.enhance_audio(TMP / "slice.wav", TMP / f"out_{label}.wav")
    dt = time.time() - t0
    stop.set(); t.join()
    print(f"{label}: SonicMaster {dt:.1f} s for 60 s of audio ({dt / 60:.2f} s per audio second) | "
          f"GPU {GPU} peak {peak['used']} MiB used | this process in shared system memory: {peak['shared']:.0f} MiB", flush=True)


async def main():
    x, r = sf.read(SRC, dtype="float32", always_2d=True)
    sf.write(TMP / "slice.wav", x[r * 60:r * 120], r, subtype="FLOAT")
    from services.audio_sonic_master_service import SonicMasterService, SUNO_SONIC_SETTINGS
    sonic = SonicMasterService(); sonic.configure(**SUNO_SONIC_SETTINGS); await sonic.initialize()
    print(f"free before: {torch.cuda.mem_get_info()[0] / 2 ** 20:.0f} MiB", flush=True)
    await timed_sonic(sonic, "alone")
    from services.audio_apollo_service import AudioApolloService
    from services.audio_roformer_service import AudioRoformerService
    from services.audio_vocal_enhance_service import AudioVocalEnhanceService
    others = [AudioApolloService(), AudioRoformerService(), AudioVocalEnhanceService()]
    for s in others:
        await s.initialize()
    await AudioApolloService().process_audio(TMP / "slice.wav", TMP / "apollo_warm.wav")
    print(f"free with Apollo, RoFormer, Lew resident: {torch.cuda.mem_get_info()[0] / 2 ** 20:.0f} MiB", flush=True)
    await timed_sonic(sonic, "with_others")
    for s in others + [sonic]:
        await s.unload()
    for p in TMP.glob("*"):
        p.unlink()
    TMP.rmdir()

asyncio.run(main())
