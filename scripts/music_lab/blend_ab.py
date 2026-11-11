import argparse, asyncio, json, os, subprocess, sys
from pathlib import Path

os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")
sys.path.insert(0, r"E:\AI_RADIO\server")
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np, soundfile as sf, pyloudnorm as pyln  # noqa: E402
from scipy import signal  # noqa: E402
from scipy.ndimage import median_filter, uniform_filter1d  # noqa: E402
from config import settings  # noqa: E402
from services.audio_headroom import _average_psd, _octave_smooth, spectrally_balanced_blend, write_float_wav  # noqa: E402

OUT = Path(os.environ.get("BLEND_AB_OUT", r"E:\AI_RADIO\upscale_ab\blend_ab"))
WORK = OUT / "_work"
WORK.mkdir(parents=True, exist_ok=True)
FFT, HOP = 2048, 512
WET = 0.5
BANDS = ((60, 250), (250, 1000), (1000, 2000), (2000, 4000), (4000, 8000), (8000, 16000))
NOTE_BANDS = ((250, 1000), (1000, 2000), (2000, 4000), (4000, 8000))
ANALYSIS_TOP_HZ = 8500.0


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


def uncancelled_blend(wet, dry, rate, w=WET):
    out = np.zeros_like(dry)
    for ch in range(dry.shape[0]):
        _, _, W = signal.stft(wet[ch].astype(np.float32), rate, nperseg=FFT, noverlap=FFT - HOP)
        _, _, D = signal.stft(dry[ch].astype(np.float32), rate, nperseg=FFT, noverlap=FFT - HOP)
        mixed = w * W + (1.0 - w) * D
        unrelated = np.sqrt((w * np.abs(W)) ** 2 + ((1.0 - w) * np.abs(D)) ** 2)
        lift = np.maximum(1.0, unrelated / (np.abs(mixed) + 1e-12))
        _, y = signal.istft(mixed * lift, rate, nperseg=FFT, noverlap=FFT - HOP)
        out[ch] = y[:dry.shape[1]] if y.shape[0] >= dry.shape[1] else np.pad(y, (0, dry.shape[1] - y.shape[0]))
        del W, D, mixed, unrelated, lift
    return compensate(out, wet, dry, rate, w)


def note_hit_blend(wet, dry, rate, w=WET):
    import librosa
    specs = []
    for ch in range(dry.shape[0]):
        _, _, W = signal.stft(wet[ch].astype(np.float32), rate, nperseg=FFT, noverlap=FFT - HOP)
        _, _, D = signal.stft(dry[ch].astype(np.float32), rate, nperseg=FFT, noverlap=FFT - HOP)
        specs.append((W, D))
    total = sum(np.abs(W) + np.abs(D) for W, D in specs)
    notes, hits = librosa.decompose.hpss(total, kernel_size=31, mask=True)
    del total
    out = np.zeros_like(dry)
    for ch, (W, D) in enumerate(specs):
        Wn, Dn = W * notes, D * notes
        level = np.sqrt(w * np.abs(Wn) ** 2 + (1.0 - w) * np.abs(Dn) ** 2)
        lead = w * np.abs(Wn) * Wn + (1.0 - w) * np.abs(Dn) * Dn
        mixed = level * lead / (np.abs(lead) + 1e-12) + (w * W + (1.0 - w) * D) * hits
        _, y = signal.istft(mixed, rate, nperseg=FFT, noverlap=FFT - HOP)
        out[ch] = y[:dry.shape[1]] if y.shape[0] >= dry.shape[1] else np.pad(y, (0, dry.shape[1] - y.shape[0]))
        del Wn, Dn, level, lead, mixed
    del specs
    return compensate(out, wet, dry, rate, w)


def frequency_blend_at(wet, dry, rate, fft, hop, w=WET):
    out = np.zeros_like(dry)
    for ch in range(dry.shape[0]):
        _, _, W = signal.stft(wet[ch].astype(np.float32), rate, nperseg=fft, noverlap=fft - hop)
        _, _, D = signal.stft(dry[ch].astype(np.float32), rate, nperseg=fft, noverlap=fft - hop)
        level = np.sqrt(w * np.abs(W) ** 2 + (1.0 - w) * np.abs(D) ** 2)
        lead = w * np.abs(W) * W + (1.0 - w) * np.abs(D) * D
        _, y = signal.istft(level * lead / (np.abs(lead) + 1e-12), rate, nperseg=fft, noverlap=fft - hop)
        out[ch] = y[:dry.shape[1]] if y.shape[0] >= dry.shape[1] else np.pad(y, (0, dry.shape[1] - y.shape[0]))
    return out


HIT_FFT, HIT_HOP = 256, 64


def note_hit_blend_sharp(wet, dry, rate, w=WET):
    import librosa
    specs = []
    for ch in range(dry.shape[0]):
        _, _, W = signal.stft(wet[ch].astype(np.float32), rate, nperseg=FFT, noverlap=FFT - HOP)
        _, _, D = signal.stft(dry[ch].astype(np.float32), rate, nperseg=FFT, noverlap=FFT - HOP)
        specs.append((W, D))
    total = sum(np.abs(W) + np.abs(D) for W, D in specs)
    notes, hits = librosa.decompose.hpss(total, kernel_size=31, mask=True)
    del total
    out = np.zeros_like(dry)
    wet_hits, dry_hits = np.zeros_like(dry), np.zeros_like(dry)

    def back(spec):
        _, y = signal.istft(spec, rate, nperseg=FFT, noverlap=FFT - HOP)
        return y[:dry.shape[1]] if y.shape[0] >= dry.shape[1] else np.pad(y, (0, dry.shape[1] - y.shape[0]))

    for ch, (W, D) in enumerate(specs):
        Wn, Dn = W * notes, D * notes
        level = np.sqrt(w * np.abs(Wn) ** 2 + (1.0 - w) * np.abs(Dn) ** 2)
        lead = w * np.abs(Wn) * Wn + (1.0 - w) * np.abs(Dn) * Dn
        out[ch] = back(level * lead / (np.abs(lead) + 1e-12))
        wet_hits[ch], dry_hits[ch] = back(W * hits), back(D * hits)
        del Wn, Dn, level, lead
    del specs
    out += frequency_blend_at(wet_hits, dry_hits, rate, HIT_FFT, HIT_HOP, w)
    return compensate(out, wet, dry, rate, w)


def compensate(blended, wet, dry, rate, w, max_boost_db=3.5, numtaps=2047):
    freqs, p_wet = _average_psd(wet, rate, 4096)
    _, p_dry = _average_psd(dry, rate, 4096)
    _, p_mix = _average_psd(blended, rate, 4096)
    target = w * p_wet + (1.0 - w) * p_dry
    floor = max(float(np.max(target)) * 1e-10, 1e-20)
    valid = (p_mix > floor) & (target > floor) & (freqs >= 20.0)
    gain = np.ones_like(freqs)
    gain[valid] = np.sqrt(target[valid] / p_mix[valid])
    gain = _octave_smooth(freqs, np.clip(gain, 1.0, 10 ** (max_boost_db / 20)), 1.0 / 3.0)
    gain[0] = 1.0
    nyquist = rate / 2.0
    grid, gains = (np.concatenate([freqs / nyquist, [1.0]]), np.concatenate([gain, [gain[-1]]])) if freqs[-1] < nyquist else (freqs / nyquist, gain)
    fir = signal.firwin2(numtaps, grid, gains)
    delay = (numtaps - 1) // 2
    return signal.oaconvolve(blended, fir[np.newaxis, :], mode="full", axes=-1)[:, delay:delay + blended.shape[1]]


def mono_db_bands(x, rate):
    f, p = signal.welch(x.mean(axis=0), rate, nperseg=8192)
    return np.array([10 * np.log10(p[(f >= lo) & (f < hi)].mean() + 1e-20) for lo, hi in BANDS])


def note_measures(signals, reference, rate):
    def spec(x):
        f, _, s = signal.stft(x, rate, nperseg=4096, noverlap=3072, boundary=None, padded=False)
        keep = f <= ANALYSIS_TOP_HZ
        return f[keep], 10 * np.log10((np.abs(s[:, keep]) ** 2).mean(axis=0) + 1e-20)
    f, d = spec(reference)
    loud = d.max(axis=0) > np.percentile(d.max(axis=0), 30)
    held = (d - median_filter(d, size=(21, 1), mode="nearest") >= 8.0) & loud[None, :]
    flat_band = (f >= 2000) & (f < 8000)
    rows = {}
    for name, x in signals.items():
        _, s = spec(x)
        n = min(s.shape[1], d.shape[1])
        s, h = s[:, :n], held[:, :n]
        height = s - median_filter(s, size=(21, 1), mode="nearest")
        avg = 10 * np.log10(uniform_filter1d(10 ** (s / 10), 13, axis=1, mode="nearest"))
        drops, heights = [], []
        for lo, hi in NOTE_BANDS:
            band = (f >= lo) & (f < hi)
            drops.append(float(np.mean((s[band] - avg[band])[h[band]] < -6) * 100))
            heights.append(float(np.mean(height[band][h[band]])))
        p = 10 ** (s[flat_band][:, loud[:n]] / 10)
        flatness = 10 * np.log10(np.exp(np.mean(np.log(p), axis=0)) / np.mean(p, axis=0))
        rows[name] = {"drops": drops, "heights": heights, "flatness": float(np.median(flatness))}
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


def stereo_coherence(x, rate):
    f, pl = signal.welch(x[0], rate, nperseg=4096)
    _, pr = signal.welch(x[1], rate, nperseg=4096)
    _, c = signal.csd(x[0], x[1], rate, nperseg=4096)
    out = []
    for lo, hi in ((2000, 6000), (6000, 12000)):
        band = (f >= lo) & (f < hi)
        out.append(float(np.abs(c[band].sum()) / np.sqrt(pl[band].sum() * pr[band].sum())))
    return out


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


async def pure_sonic(track_id, engines):
    path = WORK / f"{track_id}_sonic_pure.wav"
    if path.exists():
        return path
    from services.audio_sonic_master_service import SonicMasterService, SUNO_SONIC_SETTINGS
    if "sonic" not in engines:
        engines["sonic"] = SonicMasterService()
        engines["sonic"].configure(**SUNO_SONIC_SETTINGS)
        await engines["sonic"].initialize()
    if not await engines["sonic"].enhance_audio(settings.PREMASTER_WAV_DIR / f"{track_id}.wav", path, wet_mix=1.0):
        raise RuntimeError("SonicMaster failed")
    return path


async def compare(track_id, engines):
    import decoder_lock
    meta = json.loads((settings.CATALOG_DIR / "metadata" / f"{track_id}.json").read_text(encoding="utf-8"))
    title = "".join(c if c.isalnum() or c in " &'" else " " for c in (meta.get("generation_params") or {}).get("title", track_id)).strip()
    wet, rate = sf.read(str(await pure_sonic(track_id, engines)), dtype="float64", always_2d=True)
    dry, dry_rate = sf.read(str(settings.PREMASTER_WAV_DIR / f"{track_id}.wav"), dtype="float64", always_2d=True)
    assert rate == dry_rate
    n = min(len(wet), len(dry))
    wet, dry = wet[:n].T, dry[:n].T
    a_blend, _ = spectrally_balanced_blend(wet, dry, WET, rate, max_boost_db=3.5)
    b_blend = frequency_blend(wet, dry, rate)
    c_blend = note_hit_blend_sharp(wet, dry, rate)
    a_path, b_path, c_path = WORK / f"{title}_A_blend.wav", WORK / f"{title}_B_blend.wav", WORK / f"{title}_C2_blend.wav"
    write_float_wav(a_path, a_blend, rate)
    write_float_wav(b_path, b_blend, rate)
    write_float_wav(c_path, c_blend, rate)

    master = engines["master"]
    finals, lock = {}, {}
    for label, src in (("A", a_path), ("B", b_path), ("C2", c_path)):
        done = WORK / f"{title}_{label}_master.wav"
        if not done.exists():
            await master.master_audio_with_report(src, done, target_lufs=-16.0, correct=False, tone=True, hiss=True, resonance=True)
        finals[label] = levelled(done)[0]
        lock[label] = decoder_lock.score(done)
    a, b, c = finals["A"], finals["B"], finals["C2"]
    m = min(len(a), len(b), len(c))
    write_mp3(f"{title} - C2 - steady notes, sharp hits", c, rate)
    write_mp3(f"{title} - flip A and C2 every 5 s (low beep A, high beep C2)", flip(a, c, rate), rate)

    signals = {"Suno (SonicMaster input)": dry, "SonicMaster alone": wet, "A current blend": a_blend, "B frequency blend": b_blend,
               "C2 steady notes, sharp hits": c_blend}
    notes = note_measures(signals, dry, rate)
    hits, count = hit_heights(signals, dry, rate)
    hop = rate // 10
    k = m // hop
    frames = lambda x: 10 * np.log10((x[:k * hop].reshape(k, hop, -1) ** 2).mean(axis=(1, 2)) + 1e-12)
    fa, fb = frames(a), frames(c)
    d = (fb - fa)[fa > fa.max() - 50]
    print(f"\n=== {title}")
    print("  held notes, bands 250-1k / 1-2k / 2-4k / 4-8k:")
    print(f"    {'':26s} {'drops >6 dB (% of time)':>32s} | {'stand above the gaps (dB)':>32s} | flatness 2-8 kHz")
    for name, r in notes.items():
        print(f"    {name:26s} " + " ".join(f"{v:6.1f}%" for v in r["drops"]) + "  | " + " ".join(f"{v:6.2f}" for v in r["heights"]) + f"  | {r['flatness']:+.2f} dB")
    print(f"  drum-hit height ({count} hits), 60-250 / 250-1k / 1-4k / 4-16k: " + "; ".join(f"{name} " + " ".join(f"{v:.1f}" for v in hits[name]) for name in ("Suno (SonicMaster input)", "SonicMaster alone", "A current blend", "B frequency blend", "C2 steady notes, sharp hits")))
    print("  stereo coherence 2-6k / 6-12k: A " +" ".join(f"{v:.2f}" for v in stereo_coherence(a_blend, rate)) + " | B " + " ".join(f"{v:.2f}" for v in stereo_coherence(b_blend, rate))
          + " | Suno " + " ".join(f"{v:.2f}" for v in stereo_coherence(dry, rate)))
    print(f"  Suno decoder fingerprint (lock, lower is better): A {lock['A'].get('lock', float('nan')):.3f} | B {lock['B'].get('lock', float('nan')):.3f} | C2 {lock['C2'].get('lock', float('nan')):.3f}")
    print("  final masters, C2 minus A by band " + " ".join(f"{lo}-{hi}: {v:+.2f}" for (lo, hi), v in zip(BANDS, mono_db_bands(c[:m].T, rate) - mono_db_bands(a[:m].T, rate))))
    print(f"  per 100 ms, C2 minus A: median {np.median(d):+.2f} dB, more than 3 dB lower in {np.mean(d < -3) * 100:.2f}% of frames", flush=True)


async def main(ids):
    from services.audio_master_service import AudioMasterService
    engines = {"master": AudioMasterService()}
    for track_id in ids:
        try:
            await compare(track_id, engines)
        except Exception as error:
            print(f"{track_id}: FAILED: {error}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A = today's waveform blend, B = frequency-by-frequency blend, same SonicMaster render and master")
    parser.add_argument("ids", nargs="+", help="track ids with a premaster in PREMASTER_WAV_DIR")
    asyncio.run(main(parser.parse_args().ids))
