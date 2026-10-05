import json, subprocess, sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import scipy.fft
from scipy import signal

MP3 = Path(r"D:\catalog\mp3")
META = Path(r"D:\catalog\metadata")
OUT = Path(r"E:\AI_RADIO\upscale_ab\decoder_fingerprint")
R = 48000
SECONDS = 180
NFFT = 65536
BINS = NFFT // 2 + 1
ENV_BANDS = [(1000, 4000), (4000, 8000), (8000, 16000)]
ENV_RATE = 2000
ENV_NFFT = 16384
ENV_BINS = ENV_NFFT // 2 + 1
SILENCE_POWER = 1e-7
KINDS = ["mid_pow", "mid_log", "side_log", "env0", "env1", "env2"]


def decode(path):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-t", str(SECONDS), "-i", str(path), "-ac", "2", "-ar", str(R), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)
    return (x[:, 0] + x[:, 1]) * 0.5, (x[:, 0] - x[:, 1]) * 0.5


def spectra(x):
    hop = NFFT // 2
    win = signal.get_window("hann", NFFT).astype(np.float32)
    psum = np.zeros(BINS)
    lsum = np.zeros(BINS)
    used = 0
    for i in range(1 + (len(x) - NFFT) // hop):
        seg = x[i * hop:i * hop + NFFT]
        if float(np.mean(seg * seg)) < SILENCE_POWER:
            continue
        p = np.abs(scipy.fft.rfft(seg * win)).astype(np.float64) ** 2
        psum += p
        lsum += 10 * np.log10(p + 1e-20)
        used += 1
    if not used:
        return None, None
    return 10 * np.log10(psum / used + 1e-20), lsum / used


def envelope_spectrum(x, lo, hi):
    sos = signal.butter(6, [lo, hi], "bandpass", fs=R, output="sos")
    env = signal.resample_poly(signal.sosfilt(sos, x.astype(np.float64)) ** 2, 1, R // ENV_RATE)
    _, _, S = signal.stft(env, ENV_RATE, nperseg=ENV_NFFT, noverlap=ENV_NFFT // 2, detrend="constant", boundary=None, padded=False)
    p = np.abs(S) ** 2
    keep = p.sum(0) > np.median(p.sum(0)) * 1e-4
    return (10 * np.log10(p[:, keep] + 1e-30)).mean(1)


def centred(v):
    return (v - np.mean(v[np.isfinite(v)])).astype(np.float16)


def measure(path):
    try:
        mid, side = decode(path)
        if len(mid) < R * 20:
            return None
        mid_pow, mid_log = spectra(mid)
        _, side_log = spectra(side)
        if mid_pow is None:
            return None
        if side_log is None:
            side_log = np.zeros(BINS)
        out = {"mid_pow": centred(mid_pow), "mid_log": centred(mid_log), "side_log": centred(side_log)}
        for n, (lo, hi) in enumerate(ENV_BANDS):
            out[f"env{n}"] = centred(envelope_spectrum(mid, lo, hi))
        return out
    except Exception as exc:
        return {"error": f"{path}: {exc}"}


def job(item):
    return item[0], measure(item[1])


def init():
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass


def collect(name, paths, workers):
    folder = OUT / name
    folder.mkdir(parents=True, exist_ok=True)
    maps = {k: np.lib.format.open_memmap(folder / f"{k}.npy", mode="w+", dtype=np.float16,
                                         shape=(len(paths), ENV_BINS if k.startswith("env") else BINS)) for k in KINDS}
    ok = np.zeros(len(paths), dtype=bool)
    print(f"{name}: {len(paths)} files, {workers} workers", flush=True)
    with Pool(workers, initializer=init) as pool:
        for n, (i, res) in enumerate(pool.imap_unordered(job, list(enumerate(paths)), chunksize=2), 1):
            if res and "error" in res:
                print(res["error"], flush=True)
            elif res:
                for k in KINDS:
                    maps[k][i] = res[k]
                ok[i] = True
            if n % 250 == 0:
                print(f"{n}/{len(paths)}", flush=True)
    for m in maps.values():
        m.flush()
    (folder / "files.json").write_text(json.dumps({"files": [str(p) for p in paths], "ok": ok.tolist()}), encoding="utf-8")
    print(f"{name}: {int(ok.sum())} measured", flush=True)


def suno_paths():
    return [p for p in sorted(MP3.glob("*.mp3"))
            if (META / f"{p.stem}.json").exists() and json.loads((META / f"{p.stem}.json").read_text(encoding="utf-8")).get("uploaded_by_user_id") is None]


def make_controls(count):
    folder = OUT / "control_src"
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for seed in range(count):
        mp3 = folder / f"pink_{seed:02d}.mp3"
        if not mp3.exists():
            rng = np.random.default_rng(seed)
            n = R * SECONDS
            shape = 1 / np.sqrt(np.maximum(np.fft.rfftfreq(n, 1 / R), 20.0))
            chans = []
            for _ in range(2):
                x = np.fft.irfft(np.fft.rfft(rng.standard_normal(n)) * shape, n)
                chans.append(x / np.sqrt(np.mean(x * x)) * 0.1)
            common = chans[0]
            stereo = np.stack([common + 0.5 * chans[1], common - 0.5 * chans[1]], axis=1).astype(np.float32)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "f32le", "-ar", str(R), "-ac", "2", "-i", "-",
                            "-c:a", "libmp3lame", "-q:a", "2", str(mp3)], input=stereo.tobytes(), check=True)
        paths.append(mp3)
    return paths


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "suno"
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    if mode == "suno":
        collect("suno", suno_paths(), workers)
    elif mode == "control":
        collect("control", make_controls(24), workers)
    elif mode == "folder":
        collect(sys.argv[3], sorted(Path(sys.argv[4]).glob(sys.argv[5] if len(sys.argv) > 5 else "*.wav")), workers)
