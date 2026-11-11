import argparse, asyncio, json, os, subprocess, sys, time
from pathlib import Path

os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")
sys.path.insert(0, r"E:\AI_RADIO\server")
import numpy as np, soundfile as sf, pyloudnorm as pyln  # noqa: E402
from scipy import signal  # noqa: E402
from config import settings  # noqa: E402

OUT = Path(os.environ.get("CHAIN_AB_OUT", r"E:\AI_RADIO\upscale_ab\chain_ab")); OUT.mkdir(parents=True, exist_ok=True)
BANDS = ((60, 250), (250, 1000), (1000, 2000), (2000, 6000), (6000, 16000))
FLIP_S = 5


def levelled(path):
    x, rate = sf.read(str(path), dtype="float64", always_2d=True)
    return x * 10 ** ((-16 - pyln.Meter(rate).integrated_loudness(x)) / 20), rate


def band_db(x, rate):
    freqs, p = signal.welch(x.mean(axis=1), rate, nperseg=8192)
    return np.array([10 * np.log10(p[(freqs >= lo) & (freqs < hi)].mean() + 1e-20) for lo, hi in BANDS])


def write_mp3(name, x, rate):
    wav = OUT / f"_{abs(hash(name))}.wav"
    sf.write(str(wav), np.clip(x, -1, 1), rate, subtype="PCM_16")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "320k", str(OUT / f"{name}.mp3")], check=True)
    wav.unlink()


def flip(a, b, rate):
    n = min(len(a), len(b))
    seg, fade = FLIP_S * rate, int(0.010 * rate)
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


async def render(track_id, engines):
    from services.audio_headroom import mix_stems_to_file
    from services.audio_stage_registry import resolve_bandwidth_stage, resolve_separation_model
    from services.audio_sonic_master_service import SonicMasterService, SUNO_SONIC_SETTINGS
    from services.audio_vocal_enhance_service import AudioVocalEnhanceService
    meta = json.loads((settings.CATALOG_DIR / "metadata" / f"{track_id}.json").read_text(encoding="utf-8"))
    params = meta.get("generation_params") or {}
    title = "".join(c if c.isalnum() or c in " &'" else " " for c in params.get("title", track_id)).strip()
    before = settings.ENHANCED_WAV_DIR / f"{track_id}.wav"
    version = meta.get("master_chain_version")
    if not before.exists():
        raise RuntimeError("no master on file to compare with")
    work = OUT / "_work" / track_id[:8]
    work.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    master = engines["master"]

    sonic_wav = OUT / "_sonic" / f"{track_id}_v{settings.MASTER_CHAIN_VERSION}.wav"
    sonic_wav.parent.mkdir(parents=True, exist_ok=True)
    if not sonic_wav.exists():
        decoded = work / "decoded.wav"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(settings.CATALOG_DIR / "mp3" / f"{track_id}.mp3"), "-map", "0:a:0", "-vn",
                        "-ac", "2", "-ar", "44100", "-c:a", "pcm_f32le", "-f", "wav", str(decoded)], check=True)
        source = decoded
        apollo, _ = resolve_bandwidth_stage()
        if apollo is not None:
            await apollo.initialize()
            restored = await apollo.process_audio(decoded, work / "apollo.wav")
            await apollo.unload()
            if not restored:
                raise RuntimeError("Apollo failed")
            source = restored
        if settings.ENABLE_VOCAL_ENHANCEMENT and not params.get("instrumental", False):
            separation, _ = resolve_separation_model()
            await separation.initialize()
            stems = await separation.separate_stems(source, work / "stems")
            await separation.unload()
            if stems:
                vocal = AudioVocalEnhanceService()
                await vocal.initialize()
                better = await vocal.enhance_vocals(stems["vocals"], work / "stems" / "vocals_enhanced.wav") or stems["vocals"]
                await vocal.unload()
                mixed = work / "stems" / "vocal_mix.wav"
                await asyncio.to_thread(mix_stems_to_file, better, stems["no_vocals"], mixed)
                source = mixed
        premaster = work / "premaster.wav"
        if not await master.correct_audio(source, premaster):
            raise RuntimeError("corrective EQ failed")
        if "sonic" not in engines:
            engines["sonic"] = SonicMasterService()
            engines["sonic"].configure(**SUNO_SONIC_SETTINGS)
            await engines["sonic"].initialize()
        if not await engines["sonic"].enhance_audio(premaster, sonic_wav):
            raise RuntimeError("SonicMaster failed")

    now_wav = work / "now.wav"
    await master.master_audio_with_report(sonic_wav, now_wav, target_lufs=-16.0, correct=False, tone=True, hiss=True, resonance=True)
    old, rate = levelled(before)
    new, _ = levelled(now_wav)
    n = min(len(old), len(new))
    label = f"version {version}" if version else "unstamped, before 3 Oct"
    write_mp3(f"{title} - 1 - before today ({label})", old, rate)
    write_mp3(f"{title} - 2 - now (version {settings.MASTER_CHAIN_VERSION})", new, rate)
    write_mp3(f"{title} - flip before and now every {FLIP_S} s (low beep before, high beep now)", flip(old, new, rate), rate)
    print(f"{title}: done in {time.monotonic() - started:.0f} s | before = {label}")
    print("  now minus before by band: " + " ".join(f"{lo}-{hi} Hz {v:+.2f} dB;" for (lo, hi), v in zip(BANDS, band_db(new[:n], rate) - band_db(old[:n], rate))), flush=True)
    now_wav.unlink(missing_ok=True)
    for leftover in sorted(work.rglob("*"), reverse=True):
        leftover.unlink() if leftover.is_file() else leftover.rmdir()
    work.rmdir()


async def main(ids):
    from services.audio_master_service import AudioMasterService
    engines = {"master": AudioMasterService()}
    for track_id in ids:
        try:
            await render(track_id, engines)
        except Exception as error:
            print(f"{track_id}: FAILED: {error}", flush=True)


parser = argparse.ArgumentParser(description="The master on file against the current chain, rendered from the Suno MP3 into upscale_ab (never the catalog)")
parser.add_argument("ids", nargs="+")
asyncio.run(main(parser.parse_args().ids))
