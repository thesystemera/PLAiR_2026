"""The vocal step A/B: the vocal now (Lew's vocal Apollo) against the one-two punch tried on the DJs' voices on 3 Oct
(Resemble Enhance, then ClearVoice super-resolution), each through the rest of the current chain.

Resemble Enhance is mono, so it works on the vocal's middle and the stem keeps its own sides (its reverb and width).
Renders into upscale_ab/vocal_ab, never the catalog; stems and renders are kept there for further variants.
"""
import argparse
import asyncio
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "upscale_ab" / "vocal_ab"
os.environ["CHAIN_AB_OUT"] = str(OUT)
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np  # noqa: E402
import pyloudnorm as pyln  # noqa: E402
import soundfile as sf  # noqa: E402
from scipy import ndimage, signal  # noqa: E402
from chain_ab import BANDS, band_db, flip, levelled, write_mp3  # noqa: E402
from config import settings  # noqa: E402

RUNNER = ROOT / "scripts" / "voice_restoration" / "resemble_enhance_dir.py"
WEIGHTS = Path(r"E:\scratch\plair\resemble_enhance")
NOW_SONIC = ROOT / "upscale_ab" / "chain_ab" / "_sonic"
RESEMBLE = OUT / "_resemble"
LOG = OUT / "vocal_ab.log"
RATE = 44100
NOW = "vocal now (Lew's vocal Apollo)"
PUNCH = "vocal one-two punch (Resemble Enhance then ClearVoice)"
PUNCH_VOCAL = "one-two punch (Resemble Enhance then ClearVoice)"
VOCAL_BANDS = ((100, 500), (500, 2000), (2000, 6000), (6000, 12000), (12000, 16000), (16000, 20000))
BUCKETS = (("loud", 0, 6), ("soft", 6, 20), ("tails and breaths", 20, 40))
MAX_LAG_S = 0.05
SUNG_BELOW_TOP_DB = 30
KEEP_GAP_S = 1.0
EDGE_S = 0.25
CLEARVOICE_BELOW_TOP_DB = 45
CLEARVOICE_EDGE_S = 0.5


def say(text):
    print(text, flush=True)
    with LOG.open("a", encoding="utf-8") as log:
        log.write(text + "\n")


def stem_dir(track_id):
    return OUT / "_stems" / track_id[:8]


def to_mono(x):
    return x.mean(axis=1) if x.ndim == 2 else x


def lufs_levelled(x, rate, target=-16.0):
    return x * 10 ** ((target - pyln.Meter(rate).integrated_loudness(x)) / 20)


async def split(track_id):
    from services.audio_stage_registry import resolve_bandwidth_stage, resolve_separation_model
    from services.audio_vocal_enhance_service import AudioVocalEnhanceService
    folder = stem_dir(track_id)
    if (folder / "vocals_lew.wav").exists():
        return
    work = folder / "_work"
    work.mkdir(parents=True, exist_ok=True)
    decoded = work / "decoded.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(settings.CATALOG_DIR / "mp3" / f"{track_id}.mp3"), "-map", "0:a:0", "-vn",
                    "-ac", "2", "-ar", str(RATE), "-c:a", "pcm_f32le", "-f", "wav", str(decoded)], check=True)
    source = decoded
    apollo, _ = resolve_bandwidth_stage()
    if apollo is not None:
        await apollo.initialize()
        restored = await apollo.process_audio(decoded, work / "apollo.wav")
        await apollo.unload()
        if not restored:
            raise RuntimeError("Apollo failed")
        source = restored
    separation, _ = resolve_separation_model()
    await separation.initialize()
    stems = await separation.separate_stems(source, work / "stems")
    await separation.unload()
    if not stems:
        raise RuntimeError("stem split failed")
    shutil.copy(stems["vocals"], folder / "vocals.wav")
    shutil.copy(stems["no_vocals"], folder / "no_vocals.wav")
    vocal = AudioVocalEnhanceService()
    await vocal.initialize()
    better = await vocal.enhance_vocals(folder / "vocals.wav", work / "vocals_lew.wav")
    await vocal.unload()
    if not better:
        raise RuntimeError("Lew's vocal Apollo failed")
    shutil.copy(better, folder / "vocals_lew.wav")
    shutil.rmtree(work)


def resemble(track_id):
    RESEMBLE.mkdir(parents=True, exist_ok=True)
    done = RESEMBLE / f"{track_id[:8]}_re.wav"
    if done.exists():
        return done
    x, rate = sf.read(str(stem_dir(track_id) / "vocals.wav"), dtype="float32", always_2d=True)
    mid = RESEMBLE / f"{track_id[:8]}.wav"
    sf.write(str(mid), x.mean(axis=1), rate, subtype="FLOAT")
    result = subprocess.run([sys.executable, str(RUNNER), str(RESEMBLE), "--weights", str(WEIGHTS), "--chunk", "10"],
                            capture_output=True, text=True, errors="replace")
    mid.unlink()
    lines = [line for line in result.stdout.splitlines() if " s of audio in " in line]
    if result.returncode or not done.exists():
        raise RuntimeError("Resemble Enhance failed: " + result.stderr[-800:])
    say("  Resemble Enhance: " + (lines[-1] if lines else "done"))
    return done


def sung_spans(x, rate, below_top_db, edge_s):
    hop = rate // 10
    k = len(x) // hop
    level = 10 * np.log10((x[:k * hop].reshape(k, hop) ** 2).mean(axis=1) + 1e-20)
    sung = level > np.percentile(level, 90) - below_top_db
    sung = ndimage.binary_closing(sung, structure=np.ones(int(KEEP_GAP_S * 10) + 1))
    padded = ndimage.binary_dilation(sung, structure=np.ones(2 * int(edge_s * 10) + 1)).astype(int)
    starts = np.flatnonzero(np.diff(np.concatenate([[0], padded])) == 1)
    ends = np.flatnonzero(np.diff(np.concatenate([padded, [0]])) == -1) + 1
    return [(int(s) * hop, min(int(e) * hop, len(x))) for s, e in zip(starts, ends)]


def clearvoice(track_id, model_box):
    target = stem_dir(track_id) / "mid_punch.wav"
    if target.exists():
        return target
    import torch
    if "model" not in model_box:
        from services.audio_clearvoice_service import ClearVoice
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            model_box["model"] = ClearVoice(task="speech_super_resolution", model_names=[settings.TTS_UPSCALE_MODEL])
    started = time.perf_counter()
    torch.cuda.reset_peak_memory_stats()
    x, rate = sf.read(str(RESEMBLE / f"{track_id[:8]}_re.wav"), dtype="float32")
    wide = x.copy()
    temp = RESEMBLE / f"_{track_id[:8]}_piece.wav"
    ramp = np.linspace(0.0, 1.0, int(0.020 * rate), dtype=np.float32)
    spans = sung_spans(x, rate, CLEARVOICE_BELOW_TOP_DB, CLEARVOICE_EDGE_S)
    for start, end in spans:
        piece = x[start:end]
        sf.write(str(temp), np.pad(piece, (0, max(0, int(0.25 * rate) - len(piece)))), rate)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            got = np.asarray(model_box["model"](input_path=str(temp), online_write=False), dtype=np.float32).squeeze()[:len(piece)]
        got = np.pad(got, (0, len(piece) - len(got)))
        weight = np.ones(len(piece), dtype=np.float32)
        weight[:len(ramp)] = ramp[:len(piece)]
        weight[-len(ramp):] = np.minimum(weight[-len(ramp):], ramp[::-1][-len(piece):])
        wide[start:end] = piece * (1 - weight) + got * weight
    temp.unlink(missing_ok=True)
    sf.write(str(target), signal.resample_poly(wide, 147, 160).astype(np.float32), RATE, subtype="FLOAT")
    hop = rate // 10
    k = len(x) // hop
    high = signal.sosfiltfilt(signal.butter(4, 8000, "highpass", fs=rate, output="sos"), wide)
    quiet = 10 * np.log10((x[:k * hop].reshape(k, hop) ** 2).mean(axis=1) + 1e-20) < -80
    loudest_gap = 10 * np.log10((high[:k * hop].reshape(k, hop) ** 2).mean(axis=1)[quiet].max() + 1e-20) if quiet.any() else float("nan")
    say(f"  ClearVoice: {sum(e - s for s, e in spans) / rate:.0f} s of sung parts out of {len(x) / rate:.0f} s in {time.perf_counter() - started:.1f} s, "
        f"GPU peak {torch.cuda.max_memory_allocated() / 2 ** 30:.1f} GB; loudest above 8 kHz in the silent gaps {loudest_gap:.0f} dBFS")
    return target


def lags_ms(reference, moved, rate):
    window, max_lag = 10 * rate, int(MAX_LAG_S * rate)
    found = []
    for start in range(0, min(len(reference), len(moved)) - window, window):
        a, b = reference[start:start + window], moved[start:start + window]
        if np.sqrt((a ** 2).mean()) < 1e-3:
            continue
        corr = signal.correlate(b, a, mode="full", method="fft")
        centre = len(a) - 1
        found.append((int(np.argmax(corr[centre - max_lag:centre + max_lag + 1])) - max_lag) / rate * 1000)
    return np.array(found)


def build_punch(track_id):
    folder = stem_dir(track_id)
    raw, rate = sf.read(str(folder / "vocals.wav"), dtype="float64", always_2d=True)
    mid = raw.mean(axis=1)
    side = (raw[:, 0] - raw[:, 1]) / 2 if raw.shape[1] > 1 else np.zeros(len(raw))
    new, _ = sf.read(str(folder / "mid_punch.wav"), dtype="float64")
    lags = lags_ms(mid, new, rate)
    shift = int(round(np.median(lags) / 1000 * rate)) if len(lags) else 0
    new = np.roll(new, -shift)
    n = min(len(mid), len(new))
    mid, side, new = mid[:n], side[:n], new[:n]
    hop = rate // 10
    k = n // hop
    power = (mid[:k * hop].reshape(k, hop) ** 2).mean(axis=1)
    loud = power >= np.percentile(power, 70)
    gain = np.sqrt(power[loud].mean() / max((new[:k * hop].reshape(k, hop) ** 2).mean(axis=1)[loud].mean(), 1e-20))
    out = np.stack([new * gain + side, new * gain - side], axis=1)
    sf.write(str(folder / "vocals_punch.wav"), out.astype(np.float32), rate, subtype="FLOAT")
    return {"lag_ms": float(np.median(lags)) if len(lags) else 0.0, "drift_ms": float(np.ptp(lags)) if len(lags) else 0.0,
            "gain_db": float(20 * np.log10(gain))}


async def sonic(track_id, vocal_name, target, engines):
    from services.audio_headroom import mix_stems_to_file
    from services.audio_sonic_master_service import SUNO_SONIC_SETTINGS, SonicMasterService
    if target.exists():
        return target
    folder = stem_dir(track_id)
    mixed, premaster = folder / "_mix.wav", folder / "_premaster.wav"
    await asyncio.to_thread(mix_stems_to_file, folder / vocal_name, folder / "no_vocals.wav", mixed)
    if not await engines["master"].correct_audio(mixed, premaster):
        raise RuntimeError("corrective EQ failed")
    if "sonic" not in engines:
        engines["sonic"] = SonicMasterService()
        engines["sonic"].configure(**SUNO_SONIC_SETTINGS)
        await engines["sonic"].initialize()
    target.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    if not await engines["sonic"].enhance_audio(premaster, target):
        raise RuntimeError("SonicMaster failed")
    say(f"  SonicMaster ({vocal_name}): {time.perf_counter() - started:.0f} s")
    mixed.unlink(missing_ok=True)
    premaster.unlink(missing_ok=True)
    return target


def band_power_db(x, rate, frames):
    hop = rate // 10
    k = len(frames)
    values = []
    for lo, hi in VOCAL_BANDS:
        sos = signal.butter(4, [lo, min(hi, rate / 2 * 0.98)], "bandpass", fs=rate, output="sos")
        y = signal.sosfiltfilt(sos, x)
        power = (y[:k * hop].reshape(k, hop) ** 2).mean(axis=1)
        values.append(10 * np.log10(power[frames].mean() + 1e-20))
    return np.array(values)


def compare_vocals(track_id, b_name="vocals_punch.wav"):
    folder = stem_dir(track_id)
    raw, rate = sf.read(str(folder / "vocals.wav"), dtype="float64", always_2d=True)
    lew, _ = sf.read(str(folder / "vocals_lew.wav"), dtype="float64", always_2d=True)
    punch, _ = sf.read(str(folder / b_name), dtype="float64", always_2d=True)
    n = min(len(raw), len(lew), len(punch))
    raw, lew, punch = to_mono(raw[:n]), to_mono(lew[:n]), to_mono(punch[:n])
    hop = rate // 10
    k = n // hop
    level = 10 * np.log10((lew[:k * hop].reshape(k, hop) ** 2).mean(axis=1) + 1e-20)
    top = np.percentile(level, 90)
    say("  vocal alone, B minus A by band (dB), same moments for both; raw stem minus A in brackets:")
    say("    " + " " * 18 + "".join(f"{f'{lo // 1000 if lo >= 1000 else lo}-{hi // 1000}k':>14}" for lo, hi in VOCAL_BANDS))
    for name, lo_db, hi_db in BUCKETS:
        frames = (level <= top - lo_db) & (level > top - hi_db) if lo_db else level > top - hi_db
        if frames.sum() < 20:
            continue
        a, b, r = (band_power_db(x, rate, frames) for x in (lew, punch, raw))
        say(f"    {name:18s}" + "".join(f"{f'{d:+.1f} ({e:+.1f})':>14}" for d, e in zip(b - a, r - a)) + f"   {frames.sum() / 10:.0f} s")
    return rate, lew, punch


def sung_parts(track_id, b_name="vocals_punch.wav"):
    folder = stem_dir(track_id)
    a, rate = sf.read(str(folder / "vocals_lew.wav"), dtype="float64", always_2d=True)
    b, rate_b = sf.read(str(folder / b_name), dtype="float64", always_2d=True)
    if rate != rate_b:
        raise RuntimeError(f"vocal rates differ: {rate} and {rate_b}")
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    spans = sung_spans(a.mean(axis=1), rate, SUNG_BELOW_TOP_DB, EDGE_S)
    fade = np.hanning(2 * int(0.010 * rate))
    ramp_in, ramp_out = fade[:len(fade) // 2, None], fade[len(fade) // 2:, None]
    parts = {"a": [], "b": []}
    for start, end in spans:
        for key, x in (("a", a), ("b", b)):
            piece = x[start:end].copy()
            piece[:len(ramp_in)] *= ramp_in
            piece[-len(ramp_out):] *= ramp_out
            parts[key].append(piece)
    kept = sum(e - s for s, e in spans) / rate
    return rate, lufs_levelled(np.concatenate(parts["a"]), rate), lufs_levelled(np.concatenate(parts["b"]), rate), kept, n / rate


def write_vocals(track_id, title, b_name="vocals_punch.wav", b_label=PUNCH_VOCAL):
    rate, a, b, kept, total = sung_parts(track_id, b_name)
    write_mp3(f"{title} - vocal A - now (Lew's vocal Apollo)", a, rate)
    write_mp3(f"{title} - vocal B - {b_label}", b, rate)
    write_mp3(f"{title} - vocal flip A and B every 5 s (low beep A, high beep B)", flip(a, b, rate), rate)
    say(f"  vocal files: the sung parts only, {kept:.0f} s of {total:.0f} s, both at the same loudness")


async def render(track_id, engines, model_box, vocals_only):
    meta = json.loads((settings.CATALOG_DIR / "metadata" / f"{track_id}.json").read_text(encoding="utf-8"))
    params = meta.get("generation_params") or {}
    if params.get("instrumental"):
        say(f"{track_id}: instrumental, skipped")
        return
    title = "".join(c if c.isalnum() or c in " &'" else " " for c in params.get("title", track_id)).strip()
    started = time.perf_counter()
    say(f"\n{title} ({track_id[:8]})")
    await split(track_id)
    resemble(track_id)
    clearvoice(track_id, model_box)
    info = build_punch(track_id)
    say(f"  punch vocal against the stem: offset {info['lag_ms']:+.1f} ms (spread across the song {info['drift_ms']:.1f} ms), level matched by {info['gain_db']:+.1f} dB")
    compare_vocals(track_id)
    write_vocals(track_id, title)
    if vocals_only:
        say(f"  done in {time.perf_counter() - started:.0f} s")
        return

    now_sonic = NOW_SONIC / f"{track_id}_v6.wav"
    if not now_sonic.exists():
        now_sonic = await sonic(track_id, "vocals_lew.wav", OUT / "_sonic" / f"{track_id}_now.wav", engines)
    punch_sonic = await sonic(track_id, "vocals_punch.wav", OUT / "_sonic" / f"{track_id}_punch.wav", engines)
    masters = {}
    for label, source in (("now", now_sonic), ("punch", punch_sonic)):
        out = stem_dir(track_id) / f"_master_{label}.wav"
        await engines["master"].master_audio_with_report(source, out, target_lufs=-16.0, correct=False, tone=True, hiss=True, resonance=True)
        masters[label], _ = levelled(out)
        out.unlink()
    a, b = masters["now"], masters["punch"]
    n = min(len(a), len(b))
    write_mp3(f"{title} - A - {NOW}", a, RATE)
    write_mp3(f"{title} - B - {PUNCH}", b, RATE)
    write_mp3(f"{title} - flip A and B every 5 s (low beep A now, high beep B punch)", flip(a, b, RATE), RATE)
    say("  full song, B minus A by band (dB): " + " ".join(f"{lo}-{hi} Hz {v:+.2f};" for (lo, hi), v in zip(BANDS, band_db(b[:n], RATE) - band_db(a[:n], RATE))))
    say(f"  done in {time.perf_counter() - started:.0f} s")


async def main(ids, vocals_only):
    from services.audio_master_service import AudioMasterService
    OUT.mkdir(parents=True, exist_ok=True)
    say(f"vocal A/B, master chain version {settings.MASTER_CHAIN_VERSION}, {time.strftime('%Y-%m-%d %H:%M')}")
    engines, model_box = {"master": AudioMasterService()}, {}
    for track_id in ids:
        try:
            await render(track_id, engines, model_box, vocals_only)
        except Exception as error:
            say(f"{track_id}: FAILED: {error}")
    say("\nall done")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ids", nargs="+")
    parser.add_argument("--vocals-only", action="store_true", help="stop after the vocal files (no SonicMaster, no full mix)")
    args = parser.parse_args()
    asyncio.run(main(args.ids, args.vocals_only))
