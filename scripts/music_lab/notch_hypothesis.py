import json, random, sys
from pathlib import Path

sys.path.insert(0, r"E:\AI_RADIO\server")
sys.path.insert(0, r"E:\AI_RADIO\scripts\music_lab")
import numpy as np  # noqa: E402
from scipy import signal  # noqa: E402
from scipy.ndimage import median_filter  # noqa: E402
from suno_profile import decode, MP3, META, R  # noqa: E402

OUT = Path(r"E:\AI_RADIO\upscale_ab\notch_test"); OUT.mkdir(parents=True, exist_ok=True)
WINDOW_S, NFFT, HOP = 3.0, 16384, 4096
MIN_PROM_DB, MIN_PERSIST = 6.0, 0.3


def cents(f):
    c = 1200 * np.log2(f / 440.0)
    return ((c + 50) % 100) - 50


def current_gate(x):
    from services.audio_master_service import AudioMasterService
    _, info = AudioMasterService()._apply_adaptive_notch_filter(np.stack([x, x]), R, 1.0, notch=True, tone=False)
    return info["debug"]["candidates"], info["notches"]


def scan(x):
    f, t, S = signal.stft(x, R, nperseg=NFFT, noverlap=NFFT - HOP)
    db = 10 * np.log10(np.abs(S) ** 2 + 1e-20)
    per_win = int(WINDOW_S * R / HOP)
    wins = []
    for start in range(0, db.shape[1] - per_win + 1, per_win):
        seg = db[:, start:start + per_win]
        if seg.max() < -60:
            continue
        lt = 10 * np.log10(np.mean(10 ** (seg / 10), axis=1) + 1e-20)
        prom = lt - median_filter(lt, size=61, mode="nearest")
        idx = signal.find_peaks(prom, height=MIN_PROM_DB, distance=3)[0]
        idx = idx[(f[idx] >= 40) & (f[idx] <= 16000)]
        found = []
        for b in idx:
            a, c0, c = lt[b - 1], lt[b], lt[b + 1]
            sh = 0.5 * (a - c) / (a - 2 * c0 + c) if (a - 2 * c0 + c) != 0 else 0.0
            half = np.where(lt[max(0, b - 10):b + 11] >= c0 - 3)[0]
            found.append((float(f[b] + sh * (f[1] - f[0])), float(prom[b]), float(len(half) * (f[1] - f[0]))))
        wins.append((start * HOP / R, found))
    tracks = []
    for wi, (t0, found) in enumerate(wins):
        for fr, pr, bw in found:
            for tr in tracks:
                if abs(1200 * np.log2(fr / tr["f"])) <= 15 and tr["last"] < wi:
                    tr["hits"].append((t0, pr, bw)); tr["last"] = wi; tr["f"] = (tr["f"] * (len(tr["hits"]) - 1) + fr) / len(tr["hits"])
                    break
            else:
                tracks.append({"f": fr, "hits": [(t0, pr, bw)], "last": wi})
    n = max(1, len(wins))
    res = []
    for tr in tracks:
        persist = len(tr["hits"]) / n
        if persist < MIN_PERSIST:
            continue
        prs = [h[1] for h in tr["hits"]]
        res.append({"f": tr["f"], "persist": persist, "prom_med": float(np.median(prs)), "prom_max": float(np.max(prs)),
                    "width_hz": float(np.median([h[2] for h in tr["hits"]])), "cents": float(cents(tr["f"])),
                    "loudest_at_s": tr["hits"][int(np.argmax(prs))][0]})
    return sorted(res, key=lambda r: -r["persist"] * r["prom_med"]), n


if __name__ == "__main__":
    ids = [p.stem for p in MP3.glob("*.mp3") if (META / f"{p.stem}.json").exists()
           and json.loads((META / f"{p.stem}.json").read_text(encoding="utf-8")).get("uploaded_by_user_id") is None]
    random.seed(int(sys.argv[1]) if len(sys.argv) > 1 else 5)
    report = {}
    for tid in random.sample(ids, 6):
        meta = json.loads((META / f"{tid}.json").read_text(encoding="utf-8"))
        title = (meta.get("generation_params") or {}).get("title", tid)
        x = decode(MP3 / f"{tid}.mp3")
        cands, notches = current_gate(x)
        found, nwin = scan(x)
        print(f"\n== {title} ({tid[:8]}), {nwin} windows of {WINDOW_S:.0f} s")
        print("   current notch stage, top candidates (needs >= 6 dB in the song average AND >= 90% of loud frames):")
        for c in cands:
            why = [w for w, bad in (("average too low", c["prom"] < 6), ("not steady enough", c["presence"] < 0.9),
                                    ("outside 50-15k", not 50 <= c["fc"] <= 15000)) if bad]
            print(f"     {c['fc']:8.1f} Hz  {c['prom']:4.1f} dB avg  present {100 * c['presence']:3.0f}%  -> {'NOTCHED' if not why else ', '.join(why)}")
        print(f"   time-resolved scan: narrow peaks >= {MIN_PROM_DB:.0f} dB holding a fixed frequency in >= {100 * MIN_PERSIST:.0f}% of windows:")
        for r in found[:8]:
            tag = "on a note" if abs(r["cents"]) <= 20 else "OFF-NOTE"
            print(f"     {r['f']:8.1f} Hz  in {100 * r['persist']:3.0f}% of the song  median {r['prom_med']:4.1f} dB (max {r['prom_max']:4.1f})  "
                  f"width {r['width_hz']:4.1f} Hz  {tag} ({r['cents']:+.0f} c)  loudest at {r['loudest_at_s']:.0f} s")
        report[tid] = {"title": title, "current": cands, "scan": found[:8]}
    (OUT / "notch_hypothesis.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
