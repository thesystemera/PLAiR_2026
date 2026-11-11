import argparse, asyncio, os, subprocess, sys
from pathlib import Path

sys.path.insert(0, r"E:\AI_RADIO\server")
import numpy as np, soundfile as sf, pyloudnorm as pyln  # noqa: E402
from scipy import signal  # noqa: E402
from scipy.ndimage import median_filter, uniform_filter1d  # noqa: E402
from services.audio_headroom import spectrally_balanced_blend, write_float_wav  # noqa: E402

OUT = Path(os.environ.get("BLEND_AB_OUT", r"E:\AI_RADIO\upscale_ab\blend_ab"))
WORK = OUT / "_work"
WORK.mkdir(parents=True, exist_ok=True)
FFT, HOP = 2048, 512
WET = 0.5
BANDS = ((60, 250), (250, 1000), (1000, 2000), (2000, 4000), (4000, 8000), (8000, 16000))
FLICKER_BANDS = ((250, 1000), (1000, 2000), (2000, 4000), (4000, 8000))


def frequency_blend(wet, dry, rate, w=WET):
    out = np.zeros_like(dry)
    for ch in range(dry.shape[0]):
        _, _, W = signal.stft(wet[ch].astype(np.float32), rate, nperseg=FFT, noverlap=FFT - HOP)
        _, _, D = signal.stft(dry[ch].astype(np.float32), rate, nperseg=FFT, noverlap=FFT - HOP)
        level = np.sqrt(w * np.abs(W) ** 2 + (1.0 - w) * np.abs(D) ** 2)
        lead = w * np.abs(W) * W + (1.0 - w) * np.abs(D) * D
        _, y = signal.istft(level * lead / (np.abs(lead) + 1e-12), rate, nperseg=FFT, noverlap=FFT - HOP)
        out[ch] = y[:dry.shape[1]] if y.shape[0] >= dry.shape[1] else np.pad(y, (0, dry.shape[1] - y.shape[0]))
        del W, D, level, lead
    return out


def mono_db_bands(x, rate):
    f, p = signal.welch(x.mean(axis=0), rate, nperseg=8192)
    return np.array([10 * np.log10(p[(f >= lo) & (f < hi)].mean() + 1e-20) for lo, hi in BANDS])


def flicker(signals, reference, rate):
    def spec(x):
        f, _, s = signal.stft(x[0], rate, nperseg=4096, noverlap=3072, boundary=None, padded=False)
        return f, 20 * np.log10(np.abs(s) + 1e-12)
    f, d = spec(reference)
    loud = d.max(axis=0) > np.percentile(d.max(axis=0), 30)
    held = (d - median_filter(d, size=(21, 1), mode="nearest") >= 8.0) & loud[None, :]
    rows = {}
    for name, x in signals.items():
        _, s = spec(x)
        n = min(s.shape[1], d.shape[1])
        out = []
        for lo, hi in FLICKER_BANDS:
            band = (f >= lo) & (f < hi)
            avg = 10 * np.log10(uniform_filter1d(10 ** (s[band][:, :n] / 10), 13, axis=1, mode="nearest"))
            out.append(float(np.mean((s[band][:, :n] - avg)[held[band][:, :n]] < -6) * 100))
        rows[name] = out
    return rows


def hit_heights(signals, reference, rate):
    hop = int(0.010 * rate)

    def env(x, lo, hi):
        y = signal.sosfiltfilt(signal.butter(4, [lo, hi], "bandpass", fs=rate, output="sos"), x.mean(axis=0))
        k = len(y) // hop
        return 10 * np.log10((y[:k * hop].reshape(k, hop) ** 2).mean(axis=1) + 1e-14)
    full = env(reference, 40, 16000)
    rise = full[3:] - np.maximum.reduce([full[2:-1], full[1:-2], full[:-3]])
    cand = np.where((rise > 6) & (full[3:] > np.percentile(full, 60)))[0] + 3
    hits = [c for i, c in enumerate(cand) if i == 0 or c - cand[i - 1] > 10]
    rows = {}
    for name, x in signals.items():
        row = []
        for lo, hi in ((60, 250), (250, 1000), (1000, 4000), (4000, 16000)):
            e = env(x, lo, hi)
            row.append(float(np.median([e[h:h + 3].max() - np.mean(e[max(0, h - 11):h - 1]) for h in hits if 12 < h < len(e) - 3])))
        rows[name] = row
    return rows, len(hits)


def levelled(path):
    x, rate = sf.read(str(path), dtype="float64", always_2d=True)
    return x * 10 ** ((-16 - pyln.Meter(rate).integrated_loudness(x)) / 20), rate


def write_mp3(name, x, rate):
    wav = WORK / "_mp3.wav"
    sf.write(str(wav), np.clip(x, -1, 1), rate, subtype="PCM_16")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "320k", str(OUT / f"{name}.mp3")], check=True)
    wav.unlink()


def flip(a, b, rate, seconds=5):
    n = min(len(a), len(b))
    seg, fade = seconds * rate, int(0.010 * rate)
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


async def main(title, wet_path, dry_path):
    from services.audio_master_service import AudioMasterService
    wet, rate = sf.read(wet_path, dtype="float64", always_2d=True)
    dry, dry_rate = sf.read(dry_path, dtype="float64", always_2d=True)
    assert rate == dry_rate
    n = min(len(wet), len(dry))
    wet, dry = wet[:n].T, dry[:n].T

    a_blend, _ = spectrally_balanced_blend(wet, dry, WET, rate, max_boost_db=3.5)
    b_blend = frequency_blend(wet, dry, rate)
    a_path, b_path = WORK / f"{title}_A_blend.wav", WORK / f"{title}_B_blend.wav"
    write_float_wav(a_path, a_blend, rate)
    write_float_wav(b_path, b_blend, rate)
    print("blends written", flush=True)

    signals = {"Suno source (SonicMaster input)": dry, "SonicMaster alone": wet, "A current blend": a_blend, "B frequency blend": b_blend}
    print("\nheld notes dropping more than 6 dB below their own 0.3 s average (% of the time), bands " + str(FLICKER_BANDS))
    for name, row in flicker(signals, dry, rate).items():
        print(f"  {name:34s} " + "  ".join(f"{v:5.1f}%" for v in row))
    rows, count = hit_heights(signals, dry, rate)
    print(f"\ndrum-hit height over the 100 ms before it (dB, {count} hits), bands 60-250, 250-1k, 1-4k, 4-16k")
    for name, row in rows.items():
        print(f"  {name:34s} " + "  ".join(f"{v:5.1f}" for v in row))
    print("\nB minus A before mastering, by band " + str(BANDS) + ": " + " ".join(f"{v:+.2f}" for v in mono_db_bands(b_blend, rate) - mono_db_bands(a_blend, rate)))

    master = AudioMasterService()
    finals = {}
    for label, src in (("A", a_path), ("B", b_path)):
        done = WORK / f"{title}_{label}_master.wav"
        await master.master_audio_with_report(src, done, target_lufs=-16.0, correct=False, tone=True, hiss=True, resonance=True)
        finals[label] = levelled(done)[0]
    a, b = finals["A"], finals["B"]
    m = min(len(a), len(b))
    print("final masters, B minus A by band: " + " ".join(f"{v:+.2f}" for v in mono_db_bands(b[:m].T, rate) - mono_db_bands(a[:m].T, rate)))
    hop = rate // 10
    k = m // hop
    frames = lambda x: 10 * np.log10((x[:k * hop].reshape(k, hop, -1) ** 2).mean(axis=(1, 2)) + 1e-12)
    fa, fb = frames(a), frames(b)
    d = (fb - fa)[fa > fa.max() - 50]
    quiet = fa < np.percentile(fa, 10)
    print(f"per 100 ms, B minus A: median {np.median(d):+.2f} dB, 5-95% {np.percentile(d, 5):+.2f} to {np.percentile(d, 95):+.2f}, "
          f"more than 3 dB lower {np.mean(d < -3) * 100:.2f}% of frames, quietest tenth {np.median((fb - fa)[quiet]):+.2f} dB")
    write_mp3(f"{title} - A - current blend", a, rate)
    write_mp3(f"{title} - B - frequency-by-frequency blend", b, rate)
    write_mp3(f"{title} - flip A and B every 5 s (low beep A, high beep B)", flip(a, b, rate), rate)
    print("files written", flush=True)


parser = argparse.ArgumentParser()
parser.add_argument("title")
parser.add_argument("wet", help="pure SonicMaster render (100% wet)")
parser.add_argument("dry", help="the premaster SonicMaster was given")
args = parser.parse_args()
asyncio.run(main(args.title, args.wet, args.dry))
