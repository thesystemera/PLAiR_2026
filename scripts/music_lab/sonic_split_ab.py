import asyncio, json, os, subprocess, sys, time
from pathlib import Path

os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"] = "0"
sys.path.insert(0, r"E:\AI_RADIO\server")
sys.path.insert(0, r"E:\AI_RADIO\scripts\music_lab")
os.chdir(r"E:\AI_RADIO\server")
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import pyloudnorm as pyln  # noqa: E402
from scipy import signal  # noqa: E402
from config import settings  # noqa: E402

OUT = Path(os.environ.get("SONIC_SPLIT_OUT", r"E:\scratch\plair\sonic_split"))
LISTEN = OUT / "listen"
SONIC = OUT / "_sonic"
BANDS = ((60, 250), (250, 1000), (1000, 2000), (2000, 4000), (4000, 8000), (8000, 12000), (12000, 16000), (16000, 20000))


def levelled(path):
    x, rate = sf.read(str(path), dtype="float64", always_2d=True)
    return x * 10 ** ((-16 - pyln.Meter(rate).integrated_loudness(x)) / 20), rate


def band_db(x, rate):
    f, p = signal.welch(x.mean(axis=1), rate, nperseg=8192)
    return np.array([10 * np.log10(p[(f >= lo) & (f < hi)].mean() + 1e-20) for lo, hi in BANDS])


def frame_db(x, rate):
    hop = int(rate * 0.1)
    n = len(x) // hop
    return 20 * np.log10(np.sqrt((x[:n * hop].reshape(n, hop, -1) ** 2).mean(axis=(1, 2))) + 1e-9)


def write_mp3(name, x, rate):
    wav = OUT / "_tmp.wav"
    sf.write(str(wav), np.clip(x, -1, 1), rate, subtype="PCM_16")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "320k", str(LISTEN / f"{name}.mp3")], check=True)
    wav.unlink()
    return LISTEN / f"{name}.mp3"


async def render(tid, engines):
    from services.audio_headroom import mix_stems_to_file
    from chain_ab import flip, FLIP_S
    import decoder_lock
    master, sonic = engines["master"], engines["sonic"]
    meta = json.loads((settings.CATALOG_DIR / "metadata" / f"{tid}.json").read_text(encoding="utf-8"))
    title = "".join(c if c.isalnum() or c in " &'" else " " for c in (meta.get("generation_params") or {}).get("title", tid)).strip()
    stems = settings.CATALOG_DIR / "demucs_stems" / tid / "roformer"
    vocal, music = stems / "vocals_enhanced.wav", stems / "no_vocals.wav"
    work = OUT / "_work" / tid[:8]
    work.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    whole_sonic = SONIC / f"{tid}_whole.wav"
    if not whole_sonic.exists():
        mix_stems_to_file(vocal, music, work / "mix.wav")
        await master.correct_audio(work / "mix.wav", work / "mix_pre.wav")
        assert await sonic.enhance_audio(work / "mix_pre.wav", whole_sonic), "SonicMaster failed (whole mix)"
    music_sonic = SONIC / f"{tid}_music.wav"
    if not music_sonic.exists():
        await master.correct_audio(music, work / "music_pre.wav")
        assert await sonic.enhance_audio(work / "music_pre.wav", music_sonic), "SonicMaster failed (music)"
    await master.correct_audio(vocal, work / "vocal_pre.wav")
    mix_stems_to_file(work / "vocal_pre.wav", music_sonic, work / "split_mix.wav")

    now_wav, split_wav = work / "now.wav", work / "split.wav"
    await master.master_audio_with_report(whole_sonic, now_wav, target_lufs=-16.0, correct=False, tone=True, hiss=True, resonance=True)
    await master.master_audio_with_report(work / "split_mix.wav", split_wav, target_lufs=-16.0, correct=False, tone=True, hiss=True, resonance=True)
    lock_now, lock_split = decoder_lock.score(now_wav), decoder_lock.score(split_wav)

    a, rate = levelled(now_wav)
    b, _ = levelled(split_wav)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    files = [write_mp3(f"{title} - 1 - now (SonicMaster on the whole mix)", a, rate),
             write_mp3(f"{title} - 2 - SonicMaster on the music only, vocal Apollo only", b, rate),
             write_mp3(f"{title} - 3 - flip every {FLIP_S} s (low beep now, high beep music only)", flip(a, b, rate), rate)]

    da, db = frame_db(a, rate), frame_db(b, rate)
    live = da > np.percentile(da, 99) - 40
    d = (db - da)[live]
    bands = band_db(b, rate) - band_db(a, rate)
    report = {"title": title, "seconds": n / rate, "render_s": time.monotonic() - started, "bands_split_minus_now": bands.round(2).tolist(),
              "cut6": float((d < -6).mean() * 100), "boost6": float((d > 6).mean() * 100), "median": float(np.median(d)),
              "lock_now": lock_now.get("lock"), "lock_split": lock_split.get("lock"),
              "ripple_now": lock_now.get("ripple_12_17"), "ripple_split": lock_split.get("ripple_12_17"),
              "files": [str(f) for f in files]}
    (OUT / f"report_{tid[:8]}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"DONE {title} in {report['render_s']:.0f} s | music-only minus now, dB: "
          + " ".join(f"{lo / 1000:g}-{hi / 1000:g}k {v:+.1f}" for (lo, hi), v in zip(BANDS, bands))
          + f" | per 100 ms: {report['cut6']:.1f}% down >6 dB, {report['boost6']:.1f}% up >6 dB, median {report['median']:+.1f} dB"
          + f" | Suno fingerprint now {report['lock_now']:.3f}, music-only {report['lock_split']:.3f}", flush=True)
    for leftover in sorted(work.rglob("*"), reverse=True):
        leftover.unlink() if leftover.is_file() else leftover.rmdir()
    work.rmdir()


async def main(ids):
    from services.audio_master_service import AudioMasterService
    from services.audio_sonic_master_service import SonicMasterService, SUNO_SONIC_SETTINGS
    LISTEN.mkdir(parents=True, exist_ok=True)
    SONIC.mkdir(parents=True, exist_ok=True)
    sonic = SonicMasterService()
    sonic.configure(**SUNO_SONIC_SETTINGS)
    await sonic.initialize()
    print(f"SonicMaster ready: {sonic.wet_mix:.0%} wet, {sonic.num_inference_steps} steps, prompt '{sonic.prompt}'", flush=True)
    engines = {"master": AudioMasterService(), "sonic": sonic}
    for tid in ids:
        try:
            await render(tid, engines)
        except Exception as error:
            print(f"FAILED {tid}: {error!r}", flush=True)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
