import argparse, asyncio, json, os, shutil, subprocess, sys, time
from pathlib import Path

os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")
sys.path.insert(0, r"E:\AI_RADIO\server")
import numpy as np, soundfile as sf, pyloudnorm as pyln  # noqa: E402
from scipy import signal  # noqa: E402
from config import settings  # noqa: E402

OUT = Path(os.environ.get("CHAIN_AB_OUT", r"E:\AI_RADIO\upscale_ab\chain_ab")); OUT.mkdir(parents=True, exist_ok=True)
BANDS = ((60, 250), (250, 1000), (1000, 2000), (2000, 6000), (6000, 16000))


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


async def render(track_id, engines):
    from services.audio_headroom import mix_stems_to_file
    from services.audio_resonance import suppress_resonances
    from services.audio_stage_registry import resolve_bandwidth_stage, resolve_separation_model
    from services.audio_sonic_master_service import SonicMasterService, SUNO_SONIC_SETTINGS
    from services.audio_vocal_enhance_service import AudioVocalEnhanceService
    meta = json.loads((settings.CATALOG_DIR / "metadata" / f"{track_id}.json").read_text(encoding="utf-8"))
    params = meta.get("generation_params") or {}
    title = "".join(c if c.isalnum() or c in " &'" else " " for c in params.get("title", track_id)).strip()
    work = OUT / "_work" / track_id[:8]
    work.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    master = engines["master"]

    sonic_wav = OUT / "_sonic" / f"{track_id}.wav"
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
        print(f"  {title}: Apollo done at {time.monotonic() - started:.0f} s", flush=True)

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
        print(f"  {title}: vocal path done at {time.monotonic() - started:.0f} s", flush=True)

        premaster = work / "premaster.wav"
        if not await master.correct_audio(source, premaster):
            raise RuntimeError("corrective EQ failed")
        if "sonic" not in engines:
            engines["sonic"] = SonicMasterService()
            engines["sonic"].configure(**SUNO_SONIC_SETTINGS)
            await engines["sonic"].initialize()
        if not await engines["sonic"].enhance_audio(premaster, sonic_wav):
            raise RuntimeError("SonicMaster failed")
        print(f"  {title}: SonicMaster done at {time.monotonic() - started:.0f} s", flush=True)

    off_wav, on_wav = work / "off.wav", work / "on.wav"
    await master.master_audio_with_report(sonic_wav, off_wav, target_lufs=-16.0, correct=False, tone=True, hiss=True, resonance=False)
    _, info = await master.master_audio_with_report(sonic_wav, on_wav, target_lufs=-16.0, correct=False, tone=True, hiss=True, resonance=True)
    a, rate = levelled(off_wav)
    b, _ = levelled(on_wav)
    n = min(len(a), len(b))
    write_mp3(f"{title} - A - new chain, filter OFF", a, rate)
    write_mp3(f"{title} - C - new chain, resonance filter ON", b, rate)
    before, _ = sf.read(str(sonic_wav), dtype="float64", always_2d=True)
    after, _ = suppress_resonances(before.T, rate)
    removed = (before - after.T) * 10 ** ((-16 - pyln.Meter(rate).integrated_loudness(before)) / 20)
    write_mp3(f"{title} - difference C (what the resonance filter removed, +12 dB)", removed * 4.0, rate)
    old_master = settings.ENHANCED_WAV_DIR / f"{track_id}.wav"
    old = None
    if old_master.exists():
        old, old_rate = levelled(old_master)
        write_mp3(f"{title} - 0 - master before today (version {meta.get('master_chain_version') or 'unknown'})", old, old_rate)

    ring = info.get("resonance") or {}
    print(f"{title}: done in {time.monotonic() - started:.0f} s | old master chain v{meta.get('master_chain_version')} | resonance filter: average cut "
          f"{ring.get('mean_cut_db', 0):.2f} dB at 2-8 kHz, over 1 dB in {ring.get('cut_over_1db_percent', 0):.1f}% of it, deepest {ring.get('max_cut_db', 0):.1f} dB, "
          f"removed {ring.get('removed_db', 0):.1f} dB under the music | by band " + ", ".join(f"{k} {v:.2f}" for k, v in (ring.get('mean_cut_by_band_db') or {}).items()))
    print("  C (resonance filter ON) minus A (OFF): " + " ".join(f"{lo}-{hi} Hz {v:+.2f} dB;" for (lo, hi), v in zip(BANDS, band_db(b[:n], rate) - band_db(a[:n], rate))))
    if old is not None:
        m = min(len(old), n)
        print("  A (new chain, OFF) minus old master: " + " ".join(f"{lo}-{hi} Hz {v:+.2f} dB;" for (lo, hi), v in zip(BANDS, band_db(a[:m], rate) - band_db(old[:m], rate))), flush=True)
    shutil.rmtree(work, ignore_errors=True)


async def main(ids):
    from services.audio_master_service import AudioMasterService
    engines = {"master": AudioMasterService()}
    for track_id in ids:
        try:
            await render(track_id, engines)
        except Exception as error:
            print(f"{track_id}: FAILED: {error}", flush=True)


parser = argparse.ArgumentParser()
parser.add_argument("ids", nargs="+")
asyncio.run(main(parser.parse_args().ids))
