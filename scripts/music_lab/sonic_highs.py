import asyncio, json, os, subprocess, sys, time
from pathlib import Path

os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"] = "0"
sys.path.insert(0, r"E:\AI_RADIO\server")
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np, soundfile as sf, pyloudnorm as pyln  # noqa: E402
from scipy import signal  # noqa: E402
from scipy.ndimage import uniform_filter1d  # noqa: E402
from config import settings  # noqa: E402
from services.audio_headroom import spectrally_balanced_blend  # noqa: E402
from services.audio_master_service import reference_deviation_db, REFERENCE_GRID, REFERENCE_TOLERANCE_DB  # noqa: E402
import decoder_lock  # noqa: E402

OUT = Path(r"E:\AI_RADIO\upscale_ab\sonic_highs")
SECONDS = 45
MARGIN = 6
BLEND = 0.33
CROSSOVERS = (3000, 6000)
CROSSOVER_OCTAVES = 0.4
TOP_HZ = 17000
TOP_OCTAVES = 0.15
TONE_SMOOTH_OCTAVES = 1 / 6
TONE_MAX_DB = 12.0
TAPS = 4095
ENVELOPE_NFFT = 2048
ENVELOPE_HOP = 512
ENVELOPE_FRAMES = 3
ENVELOPE_BANDS_PER_OCTAVE = 3
ENVELOPE_MAX_DB = 15.0
BANDS = [(60, 250), (250, 2000), (2000, 4000), (4000, 8000), (8000, 12000), (12000, 16000)]
POINTS = [125, 250, 500, 1000, 2000, 4000, 8000, 10000, 12500, 16000]


def vocal_window(tid):
    v, r = sf.read(settings.CATALOG_DIR / "demucs_stems" / tid / "roformer" / "vocals.wav", dtype="float32", always_2d=True)
    n = len(v) // r
    level = np.array([np.sqrt(np.mean(v[i * r:(i + 1) * r] ** 2)) for i in range(n)])
    active = level > 0.25 * level.max()
    return int(np.argmax([active[i:i + SECONDS].mean() for i in range(MARGIN, n - SECONDS - MARGIN)])) + MARGIN


def crossover(rate, fc):
    lo, hi = fc * 2 ** (-CROSSOVER_OCTAVES / 2), fc * 2 ** (CROSSOVER_OCTAVES / 2)
    f = np.linspace(0, rate / 2, 4097)
    t = np.clip(np.log2(np.maximum(f, 1.0) / lo) / np.log2(hi / lo), 0, 1)
    low = signal.firwin2(TAPS, f, np.cos(np.pi / 2 * t), fs=rate)
    high = signal.firwin2(TAPS, f, np.sin(np.pi / 2 * t), fs=rate)
    return low, high


def fir(x, taps):
    return signal.oaconvolve(x, taps[np.newaxis, :], mode="full", axes=-1)[:, (len(taps) - 1) // 2:(len(taps) - 1) // 2 + x.shape[1]]


def highs_only(dry, wet, rate, fc):
    low, high = crossover(rate, fc)
    return fir(dry, low) + fir(wet, high)


def match_tone(x, ref, rate, fmin):
    f, px = signal.welch(x, rate, nperseg=4096)
    _, pr = signal.welch(ref, rate, nperseg=4096)
    ratio = 10 * np.log10((pr.mean(0) + 1e-30) / (px.mean(0) + 1e-30))
    logf = np.log2(np.maximum(f, 1.0))
    smooth = np.array([ratio[np.abs(logf - c) <= TONE_SMOOTH_OCTAVES / 2].mean() for c in logf])
    smooth = np.clip(np.where(f >= fmin / 2, smooth, 0.0), -TONE_MAX_DB, TONE_MAX_DB)
    return fir(x, signal.firwin2(TAPS, f, 10 ** (smooth / 20), fs=rate))


def band_from_wet(dry, wet, rate, fc):
    shaped = match_tone(match_envelope(wet, dry, rate, fc), dry, rate, fc)
    low, high = crossover(rate, fc)
    lo, hi = TOP_HZ * 2 ** (-TOP_OCTAVES / 2), TOP_HZ * 2 ** (TOP_OCTAVES / 2)
    f = np.linspace(0, rate / 2, 4097)
    t = np.clip(np.log2(np.maximum(f, 1.0) / lo) / np.log2(hi / lo), 0, 1)
    below = signal.firwin2(TAPS, f, np.cos(np.pi / 2 * t), fs=rate)
    above = signal.firwin2(TAPS, f, np.sin(np.pi / 2 * t), fs=rate)
    return fir(dry, low) + fir(fir(shaped, high), below) + fir(dry, above)


def match_envelope(wet, dry, rate, fmin):
    f, _, W = signal.stft(wet, rate, nperseg=ENVELOPE_NFFT, noverlap=ENVELOPE_NFFT - ENVELOPE_HOP)
    _, _, D = signal.stft(dry, rate, nperseg=ENVELOPE_NFFT, noverlap=ENVELOPE_NFFT - ENVELOPE_HOP)
    edges = fmin * 2 ** (np.arange(-2, int(np.log2(rate / 2 / fmin) * ENVELOPE_BANDS_PER_OCTAVE) + 2) / ENVELOPE_BANDS_PER_OCTAVE)
    edges = edges[edges < rate / 2]
    centres = np.sqrt(edges[:-1] * edges[1:])
    gains = np.zeros((wet.shape[0], len(centres), W.shape[-1]))
    limit = ENVELOPE_MAX_DB
    for b, (lo, hi) in enumerate(zip(edges[:-1], edges[1:])):
        m = (f >= lo) & (f < hi)
        ew = uniform_filter1d((np.abs(W[:, m]) ** 2).sum(1), ENVELOPE_FRAMES, axis=-1, mode="nearest")
        ed = uniform_filter1d((np.abs(D[:, m]) ** 2).sum(1), ENVELOPE_FRAMES, axis=-1, mode="nearest")
        floor = 1e-9 * max(float(ed.max()), 1e-20)
        gains[:, b] = np.clip(10 * np.log10((ed + floor) / (ew + floor)), -limit, limit)
    position = np.interp(np.log2(np.maximum(f, 1.0)), np.log2(centres), np.arange(len(centres)))
    lower = np.floor(position).astype(int).clip(0, len(centres) - 2)
    frac = (position - lower)[None, :, None]
    per_bin = gains[:, lower] * (1 - frac) + gains[:, lower + 1] * frac
    _, y = signal.istft(W * 10 ** (per_bin / 20), rate, nperseg=ENVELOPE_NFFT, noverlap=ENVELOPE_NFFT - ENVELOPE_HOP)
    out = np.zeros_like(wet)
    n = min(out.shape[1], y.shape[1])
    out[:, :n] = y[:, :n]
    return out


def band_levels(x, rate):
    f, p = signal.welch(x.mean(0), rate, nperseg=8192)
    return np.array([10 * np.log10(p[(f >= lo) & (f < hi)].sum() + 1e-30) for lo, hi in BANDS])


def band_coherence(a, b, rate):
    f, c = signal.coherence(a.mean(0), b.mean(0), rate, nperseg=4096)
    return [float(c[(f >= lo) & (f < hi)].mean()) for lo, hi in BANDS]


def frame_levels(x, rate, lo, hi):
    y = signal.sosfiltfilt(signal.butter(6, [lo, hi], "bandpass", fs=rate, output="sos"), x.mean(0))
    hop = rate // 10
    n = len(y) // hop
    return 10 * np.log10((y[:n * hop].reshape(n, hop) ** 2).mean(1) + 1e-12)


def frame_change(x, ref, rate, lo, hi):
    a, b = frame_levels(ref, rate, lo, hi), frame_levels(x, rate, lo, hi)
    loud = a > np.percentile(a, 20)
    d = b - a
    return 100 * np.mean(d[loud] < -3), float(np.median(d[~loud]))


def stereo_coherence(x, rate, lo=4000, hi=12000):
    f, c = signal.coherence(x[0], x[1], rate, nperseg=2048)
    return float(c[(f >= lo) & (f < hi)].mean())


def to_lufs(x, rate, target=-16.0):
    return x * 10 ** ((target - pyln.Meter(rate).integrated_loudness(x.T)) / 20)


async def render_wet(tid, start):
    OUT.mkdir(parents=True, exist_ok=True)
    wet_path = OUT / f"_{tid}_{start}_wet.wav"
    dry_path = OUT / f"_{tid}_{start}_dry.wav"
    x, r = sf.read(settings.PREMASTER_WAV_DIR / f"{tid}.wav", dtype="float32", always_2d=True, start=(start - MARGIN) * 44100, frames=(SECONDS + 2 * MARGIN) * 44100)
    sf.write(dry_path, x, r, subtype="FLOAT")
    if not wet_path.exists():
        import torch
        from services.audio_sonic_master_service import SonicMasterService, SUNO_SONIC_SETTINGS
        sonic = SonicMasterService()
        sonic.configure(**SUNO_SONIC_SETTINGS)
        await sonic.initialize()
        t0 = time.time()
        ok = await sonic.enhance_audio(dry_path, wet_path, wet_mix=1.0)
        print(f"SonicMaster 100%: {time.time() - t0:.1f} s for {SECONDS + 2 * MARGIN} s of audio, peak GPU {torch.cuda.max_memory_allocated() / 2 ** 30:.1f} GB, ok={ok}", flush=True)
        await sonic.unload()
    return dry_path, wet_path


async def main(tid):
    from services.audio_master_service import AudioMasterService
    master = AudioMasterService()
    meta = json.loads((settings.CATALOG_DIR / "metadata" / f"{tid}.json").read_text(encoding="utf-8"))
    name = "".join(c if c.isalnum() else "_" for c in (meta.get("generation_params") or {}).get("title", tid)).strip("_")[:24]
    start = vocal_window(tid)
    print(f"{name}: {SECONDS} s from {start} s", flush=True)
    dry_path, wet_path = await render_wet(tid, start)
    dry, rate = sf.read(dry_path, dtype="float64", always_2d=True)
    wet, _ = sf.read(wet_path, dtype="float64", always_2d=True)
    dry = dry.T
    wet = wet.T[:, :dry.shape[1]]
    print("SonicMaster's own output vs its input, per band " + " ".join(f"{lo}-{hi}" for lo, hi in BANDS))
    print("   level dB   " + " ".join(f"{v:+7.2f}" for v in band_levels(wet, rate) - band_levels(dry, rate)))
    print("   coherence  " + " ".join(f"{v:7.2f}" for v in band_coherence(wet, dry, rate)))
    blend, _ = spectrally_balanced_blend(wet, dry, BLEND, rate, max_boost_db=3.5)
    variants = {"no_sonic": dry, "v4_blend33": blend, "full_wet": wet}
    for fc in CROSSOVERS:
        variants[f"highs_{fc // 1000}k"] = highs_only(dry, wet, rate, fc)
        variants[f"highs_{fc // 1000}k_env"] = band_from_wet(dry, wet, rate, fc)
    cut = slice(MARGIN * rate, (MARGIN + SECONDS) * rate)
    finals = {}
    suno_tmp = OUT / f"_{tid}_suno.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", str(start), "-t", str(SECONDS), "-i", str(settings.CATALOG_DIR / "mp3" / f"{tid}.mp3"),
                    "-ar", str(rate), "-c:a", "pcm_f32le", str(suno_tmp)], check=True)
    finals["Suno"] = to_lufs(sf.read(suno_tmp, dtype="float64", always_2d=True)[0].T, rate)
    suno_tmp.unlink()
    for tag, x in variants.items():
        src, dst = OUT / f"_{tid}_{tag}_in.wav", OUT / f"_{tid}_{tag}_out.wav"
        sf.write(src, x.T, rate, subtype="FLOAT")
        await master.master_audio_with_report(src, dst, target_lufs=-16.0, correct=False, tone=True, hiss=True)
        finals[tag] = to_lufs(sf.read(dst, dtype="float64", always_2d=True)[0].T[:, cut], rate)
        src.unlink()
        dst.unlink()
    order = ["Suno", "v4_blend33"] + [f"highs_{fc // 1000}k_env" for fc in CROSSOVERS] + [f"highs_{fc // 1000}k" for fc in CROSSOVERS] + ["full_wet", "no_sonic"]
    files = {}
    for i, tag in enumerate(order, 1):
        files[tag] = OUT / f"{name}_{i}_{tag}.flac"
        sf.write(files[tag], np.clip(finals[tag], -1, 1).T, rate, subtype="PCM_16")
    base = band_levels(finals["no_sonic"], rate)
    print("\nfinal files at -16 LUFS. band levels: dB vs the chain without SonicMaster. frames: 100 ms frames vs the chain without SonicMaster")
    print("%-15s" % "" + " ".join(f"{lo}-{hi}".rjust(10) for lo, hi in BANDS) + "   lock  ripple  tones  L/R | 0.25-2k: >3dB down, quiet | 2-8k: >3dB down, quiet | 8-16k: >3dB down, quiet")
    for tag in order:
        x = finals[tag]
        s = decoder_lock.score(files[tag])
        fc = " | ".join("%5.1f%% %+5.1f dB" % frame_change(x, finals["no_sonic"], rate, lo, hi) for lo, hi in ((250, 2000), (2000, 8000), (8000, 16000)))
        print("%-15s" % tag + " ".join(f"{v:+10.2f}" for v in band_levels(x, rate) - base)
              + f"   {s['lock']:.3f}  {s['ripple_12_17']:4.1f}%  {s['comb_db']:+.1f}  {stereo_coherence(x, rate):.2f} | {fc}")
    print()
    for tag in order:
        dev = reference_deviation_db(finals[tag], rate)
        print("%-15s industry " % tag + " ".join("%6.1f%s" % (v, "*" if abs(v) > REFERENCE_TOLERANCE_DB else " ")
                                                  for v in (float(np.interp(np.log2(k), np.log2(REFERENCE_GRID), dev)) for k in POINTS)))
    print("points: " + " ".join(str(p) for p in POINTS))
    dry_path.unlink()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
