import asyncio, os, sys, time
from pathlib import Path
os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"] = sys.argv[1] if len(sys.argv) > 1 else "0"
sys.path.insert(0, r"E:\AI_RADIO\server")
import soundfile as sf, torch  # noqa: E402
SRC = Path(r"D:\catalog\mp3\21955a90ec313b6f9c5bf542276eeec9.mp3")
TMP = Path(r"E:\AI_RADIO\upscale_ab\_probe"); TMP.mkdir(parents=True, exist_ok=True)
GB = 2 ** 30


async def main():
    import subprocess
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", "60", "-t", "60", "-i", str(SRC), "-ar", "44100", "-c:a", "pcm_f32le", str(TMP / "s.wav")], check=True)
    from services.audio_apollo_service import AudioApolloService
    from services.audio_roformer_service import AudioRoformerService
    from services.audio_vocal_enhance_service import AudioVocalEnhanceService
    from services.audio_lyrical_timestamp_service import LyricalTimestampService
    for name, svc, run in (
            ("Apollo", AudioApolloService(), lambda s: s.process_audio(TMP / "s.wav", TMP / "a.wav")),
            ("RoFormer", AudioRoformerService(), lambda s: s.separate_stems(TMP / "s.wav", TMP / "stems")),
            ("Lew vocal", AudioVocalEnhanceService(), lambda s: s.enhance_vocals(TMP / "stems" / "vocals.wav", TMP / "v.wav")),
            ("Whisper (lyrics service)", LyricalTimestampService(), None)):
        base = torch.cuda.memory_allocated()
        await svc.initialize()
        weights = (torch.cuda.memory_allocated() - base) / GB
        line = f"{name}: resident {weights:.2f} GB"
        if run:
            torch.cuda.reset_peak_memory_stats(); t0 = time.time()
            await run(svc)
            line += f", peak during run +{(torch.cuda.max_memory_allocated() - torch.cuda.memory_allocated()) / GB:.2f} GB, {time.time() - t0:.1f} s for 60 s"
        print(line, flush=True)
    print(f"all resident: {torch.cuda.memory_allocated() / GB:.2f} GB allocated, {torch.cuda.memory_reserved() / GB:.2f} GB reserved", flush=True)
    import shutil; shutil.rmtree(TMP)

asyncio.run(main())
