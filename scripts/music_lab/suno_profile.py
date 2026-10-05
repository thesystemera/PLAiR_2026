import json, subprocess, sys
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, r"E:\AI_RADIO\server")
import numpy as np  # noqa: E402
from scipy import signal  # noqa: E402
from scipy.ndimage import median_filter, gaussian_filter1d  # noqa: E402

MP3 = Path(r"D:\catalog\mp3")
META = Path(r"D:\catalog\metadata")
OUT = Path(r"E:\AI_RADIO\upscale_ab\suno_profile"); OUT.mkdir(parents=True, exist_ok=True)
R = 44100
SECONDS = 180
BANDS = [(250, 1000), (1000, 2000), (2000, 4000), (4000, 8000), (8000, 12000), (12000, 16000)]
NOTE_TOL_CENTS = 20
HARMONICS = range(2, 11)


def decode(path):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-t", str(SECONDS), "-i", str(path), "-ac", "1", "-ar", str(R), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64)


def cents_off(freqs, tuning_cents=0.0):
    c = 1200 * np.log2(np.asarray(freqs) / 440.0) - tuning_cents
    return ((c + 50) % 100) - 50


def song_tuning(freqs, weights):
    ang = 2 * np.pi * cents_off(freqs) / 100
    return float(np.angle(np.sum(weights * np.exp(1j * ang))) * 100 / (2 * np.pi))


def classify(peaks, tuning):
    f = np.array([p[0] for p in peaks])
    on_note = np.abs(cents_off(f, tuning)) <= NOTE_TOL_CENTS
    kind = np.where(on_note, "note", "off").astype(object)
    for i in np.where(~on_note)[0]:
        for j in np.where(on_note & (f < f[i]))[0]:
            ratio = f[i] / f[j]
            k = round(ratio)
            if k in HARMONICS and abs(1200 * np.log2(ratio / k)) <= NOTE_TOL_CENTS:
                kind[i] = "harmonic"
                break
    return kind


def comb_delay(ltas_db, f):
    band = (f >= 1000) & (f <= 12000)
    lin_f = np.linspace(1000, 12000, 4096)
    spec = np.interp(lin_f, f[band], ltas_db[band])
    spec = spec - gaussian_filter1d(spec, 40)
    ceps = np.abs(np.fft.rfft(spec * np.hanning(len(spec))))
    quef = np.fft.rfftfreq(len(spec), d=(lin_f[1] - lin_f[0]))
    m = (quef >= 0.0002) & (quef <= 0.005)
    i = np.argmax(ceps[m])
    return float(quef[m][i] * 1000), float(ceps[m][i] / (np.median(ceps[m]) + 1e-12))


def profile(track_id):
    try:
        from services.audio_master_service import REFERENCE_GRID, modern_master_target_db, _grid_smooth, REFERENCE_ALIGN_HZ
        from services.audio_headroom import SOURCE_CUTOFF_DROP_DB
        x = decode(MP3 / f"{track_id}.mp3")
        if len(x) < R * 20:
            return None
        f, _, S = signal.stft(x, R, nperseg=8192, noverlap=4096)
        p = np.abs(S) ** 2
        ltas_all = 10 * np.log10(p.mean(1) + 1e-30)
        measured = np.interp(np.log2(REFERENCE_GRID), np.log2(np.maximum(f, 1.0)), ltas_all)
        dev = _grid_smooth(measured, 1 / 3) - modern_master_target_db(REFERENCE_GRID)
        align = (REFERENCE_GRID >= REFERENCE_ALIGN_HZ[0]) & (REFERENCE_GRID <= REFERENCE_ALIGN_HZ[1])
        dev -= dev[align].mean()

        ref = np.median(ltas_all[(f >= 12000) & (f <= 16000)])
        below = np.where((f > 14000) & (ltas_all < ref - SOURCE_CUTOFF_DROP_DB))[0]
        cutoff = float(f[below[0]]) if below.size else R / 2

        e = p.sum(0); loud = e >= np.percentile(e, 40)
        ltas = 10 * np.log10(p[:, loud].mean(1) + 1e-30)
        prom = ltas - median_filter(ltas, size=41, mode="nearest")
        cand = signal.find_peaks(prom, height=6.0, distance=4)[0]
        cand = cand[(f[cand] >= 60) & (f[cand] <= 16000)]
        frames_db = 10 * np.log10(p[:, loud] + 1e-30)
        peaks = []
        for b in cand:
            lo, hi = max(0, b - 20), min(len(f), b + 21)
            neigh = np.concatenate([frames_db[lo:b - 2], frames_db[b + 3:hi]], axis=0)
            presence = float(np.mean(frames_db[b] - np.median(neigh, axis=0) >= 6.0))
            if presence < 0.5:
                continue
            a, c0, c = ltas[b - 1], ltas[b], ltas[b + 1]
            shift = 0.5 * (a - c) / (a - 2 * c0 + c) if (a - 2 * c0 + c) != 0 else 0.0
            peaks.append([float(f[b] + shift * (f[1] - f[0])), float(prom[b]), presence])
        tuning = song_tuning([q[0] for q in peaks], np.array([q[1] for q in peaks])) if peaks else 0.0
        kinds = classify(peaks, tuning) if peaks else []
        for q, k in zip(peaks, kinds):
            q.append(k)

        f2, _, S2 = signal.stft(x, R, nperseg=2048, noverlap=1024)
        a2 = np.abs(S2); db = 20 * np.log10(a2 + 1e-12)
        loud2 = db.max(0) > -80
        holes, rng = [], []
        for lo, hi in BANDS:
            m = (f2 >= lo) & (f2 < hi)
            d = db[m][:, loud2]
            holes.append(float(np.mean(d < np.median(d, axis=0) - 20)))
            env = 10 * np.log10((a2[m][:, loud2] ** 2).sum(0) + 1e-20)
            rng.append(float(np.percentile(env, 95) - np.percentile(env, 10)))
        delay_ms, comb_strength = comb_delay(ltas, f)
        return {"id": track_id, "dev": dev.astype(np.float32).tolist(), "cutoff": cutoff, "tuning_cents": tuning,
                "peaks": peaks, "holes": holes, "range": rng, "comb_ms": delay_ms, "comb_strength": comb_strength}
    except Exception as exc:
        return {"id": track_id, "error": str(exc)}


def init():
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        pass


if __name__ == "__main__":
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    ids = [p.stem for p in MP3.glob("*.mp3")
           if (META / f"{p.stem}.json").exists() and json.loads((META / f"{p.stem}.json").read_text(encoding="utf-8")).get("uploaded_by_user_id") is None]
    done = OUT / "profiles_v2.jsonl"
    have = {json.loads(l)["id"] for l in done.read_text(encoding="utf-8").splitlines() if l.strip()} if done.exists() else set()
    todo = [i for i in ids if i not in have]
    print(f"{len(ids)} Suno tracks, {len(todo)} to profile with {workers} workers", flush=True)
    with Pool(workers, initializer=init) as pool, open(done, "a", encoding="utf-8") as fh:
        for n, res in enumerate(pool.imap_unordered(profile, todo, chunksize=4), 1):
            if res:
                fh.write(json.dumps(res) + "\n")
            if n % 250 == 0:
                print(f"{n}/{len(todo)}", flush=True)
    print("done", flush=True)
