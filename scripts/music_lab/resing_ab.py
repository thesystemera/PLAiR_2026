"""The re-sung lead vocal A/B: the vocal now (Lew's vocal Apollo) against the lead re-sung into its own voice by Seed-VC's
singing model, then Lew's vocal Apollo as well; vocal stems only, sung parts only.

The vocal stem is split into lead and backing (Mel-Roformer karaoke by aufr33/viperx). Only the lead's middle is re-sung,
phrase by phrase so the joins fall in pauses; a phrase over 18 s is cut at its quietest moment and the two renders
overlap there. The lead's sides, the backing vocals and the pauses stay as they were.
Renders into upscale_ab/vocal_ab next to the vocal A/B, never the catalog.
"""
import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import vocal_ab as v
import numpy as np
import soundfile as sf
from decoder_lock import line as lock_line, score as lock_score

v.LOG = v.OUT / "resing_ab.log"
SEPARATOR_MODELS = Path(r"E:\scratch\plair\separator_models")
KARAOKE = "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"
SEEDVC = Path(r"E:\scratch\plair\seedvc")
RUNNER = v.ROOT / "scripts" / "voice_restoration" / "seedvc_dir.py"
B_NAME = "vocals_resung_lew.wav"
B_LABEL = "lead re-sung in its own voice (Seed-VC, loudness of the original), then Lew's vocal Apollo"
PHRASE_BELOW_TOP_DB = 45
PHRASE_EDGE_S = 0.3
MAX_PHRASE_S = 18.0
CUT_FROM_S = 10.0
OVERLAP_S = 0.5
REFERENCE_S = 10
EDGE_FADE_S = 0.02
WHISPER_MODEL = "large-v3-turbo"
ENVELOPE_S = 0.05
MAX_LIFT_DB = 6.0


def work_dir(track_id):
    return v.OUT / "_resing" / track_id[:8]


def split_lead(track_id, box):
    folder = v.stem_dir(track_id)
    if (folder / "lead.wav").exists():
        return
    import logging
    out_dir = v.OUT / "_resing" / "_karaoke"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    if "separator" not in box:
        from audio_separator.separator import Separator
        box["separator"] = Separator(log_level=logging.WARNING, model_file_dir=str(SEPARATOR_MODELS), output_dir=str(out_dir), output_format="WAV")
        box["separator"].load_model(model_filename=KARAOKE)
    started = time.perf_counter()
    produced = box["separator"].separate(str(folder / "vocals.wav"))
    lead_file = next(out_dir / Path(name).name for name in produced if "(Vocals)" in name)
    vocals, rate = sf.read(str(folder / "vocals.wav"), dtype="float64", always_2d=True)
    lead, _ = sf.read(str(lead_file), dtype="float64", always_2d=True)
    n = min(len(vocals), len(lead))
    sf.write(str(folder / "lead.wav"), lead[:n].astype(np.float32), rate, subtype="FLOAT")
    sf.write(str(folder / "backing.wav"), (vocals[:n] - lead[:n]).astype(np.float32), rate, subtype="FLOAT")
    shutil.rmtree(out_dir)
    db = lambda x: 10 * np.log10((x ** 2).mean() + 1e-20)
    v.say(f"  lead/backing split in {time.perf_counter() - started:.0f} s: lead {db(lead[:n]):.1f} dB, backing {db(vocals[:n] - lead[:n]):.1f} dB")


def cut_long(x, start, end, rate):
    if (end - start) / rate <= MAX_PHRASE_S:
        return [(start, end)]
    hop = rate // 10
    lo, hi = start + int(CUT_FROM_S * rate), start + int(MAX_PHRASE_S * rate)
    frames = (x[lo:lo + (hi - lo) // hop * hop].reshape(-1, hop) ** 2).mean(axis=1)
    cut = lo + int(np.argmin(frames)) * hop + hop // 2
    return [(start, cut)] + cut_long(x, cut, end, rate)


def phrases(mid, rate):
    pad = int(OVERLAP_S * rate)
    plan = []
    for start, end in v.sung_spans(mid, rate, PHRASE_BELOW_TOP_DB, PHRASE_EDGE_S):
        cuts = cut_long(mid, start, end, rate)
        pieces = [(max(start, s - pad) if k else s, min(end, e + pad) if k < len(cuts) - 1 else e) for k, (s, e) in enumerate(cuts)]
        plan.append(((start, end), pieces, [c[1] for c in cuts[:-1]]))
    return plan


def reference(mid, rate):
    hop = rate // 10
    k = len(mid) // hop
    level = 10 * np.log10((mid[:k * hop].reshape(k, hop) ** 2).mean(axis=1) + 1e-20)
    sung = (level > np.percentile(level, 90) - 25).astype(float)
    width = REFERENCE_S * 10
    start = int(np.argmax(np.convolve(sung, np.ones(width), "valid")))
    return mid[start * hop:(start + width) * hop], start / 10


def resing(track_id):
    folder, work = v.stem_dir(track_id), work_dir(track_id)
    target = folder / "lead_mid_resung.wav"
    lead, rate = sf.read(str(folder / "lead.wav"), dtype="float64", always_2d=True)
    mid = lead.mean(axis=1)
    plan = phrases(mid, rate)
    if target.exists():
        return plan
    pieces_dir = work / "phrases"
    shutil.rmtree(pieces_dir, ignore_errors=True)
    pieces_dir.mkdir(parents=True)
    ref, ref_at = reference(mid, rate)
    sf.write(str(work / "reference.wav"), ref.astype(np.float32), rate, subtype="FLOAT")
    names = []
    for outer, pieces, _ in plan:
        for s, e in pieces:
            name = f"{len(names):03d}"
            sf.write(str(pieces_dir / f"{name}.wav"), mid[s:e].astype(np.float32), rate, subtype="FLOAT")
            names.append((name, s, e))
    env = dict(os.environ, HF_HOME=str(Path(r"E:\scratch\plair\hf")), PYTHONPATH=str(SEEDVC / "extra"), PYTHONIOENCODING="utf-8")
    started = time.perf_counter()
    with open(work / "seedvc.log", "w", encoding="utf-8") as log:
        result = subprocess.run([sys.executable, str(RUNNER), str(pieces_dir), "--repo", str(SEEDVC / "seed-vc"), "--reference", str(work / "reference.wav")],
                                stdout=log, stderr=subprocess.STDOUT, env=env)
    if result.returncode:
        raise RuntimeError(f"Seed-VC failed, see {work / 'seedvc.log'}")
    sung = sum(e - s for _, s, e in names) / rate
    peaks = re.findall(r"GPU peak ([\d.]+) GB", (work / "seedvc.log").read_text(encoding="utf-8", errors="replace"))
    v.say(f"  Seed-VC: {len(names)} phrases, {sung:.0f} s of singing in {time.perf_counter() - started:.0f} s, GPU peak {max(map(float, peaks or ['0'])):.1f} GB, "
          f"reference {ref_at:.0f}-{ref_at + REFERENCE_S:.0f} s of the lead")
    out = mid.copy()
    edge = int(EDGE_FADE_S * rate)
    pad = int(OVERLAP_S * rate)
    index = {name: (s, e) for name, s, e in names}
    order = iter(sorted(index))
    for (start, end), pieces, cuts in plan:
        buf = np.zeros(end - start)
        for k, (s, e) in enumerate(pieces):
            got, got_rate = sf.read(str(pieces_dir / f"{next(order)}_vc.wav"), dtype="float64")
            if got_rate != rate:
                raise RuntimeError(f"Seed-VC wrote {got_rate} Hz, expected {rate}")
            got = np.pad(got[:e - s], (0, max(0, (e - s) - len(got))))
            weight = np.ones(e - s)
            if k:
                weight[:2 * pad] = np.linspace(0, 1, 2 * pad)
            if k < len(pieces) - 1:
                weight[-2 * pad:] = np.linspace(1, 0, 2 * pad)
            buf[s - start:e - start] += got * weight
        ramp = np.ones(end - start)
        ramp[:edge] = np.linspace(0, 1, edge)
        ramp[-edge:] = np.minimum(ramp[-edge:], np.linspace(1, 0, edge))
        out[start:end] = mid[start:end] * (1 - ramp) + buf * ramp
    hop = rate // 10
    k = len(mid) // hop
    power = (mid[:k * hop].reshape(k, hop) ** 2).mean(axis=1)
    loud = power >= np.percentile(power, 70)
    gain = np.sqrt(power[loud].mean() / max((out[:k * hop].reshape(k, hop) ** 2).mean(axis=1)[loud].mean(), 1e-20))
    sf.write(str(target), (out * gain).astype(np.float32), rate, subtype="FLOAT")
    cut_levels = [10 * np.log10(power[min(c // hop, k - 1)] + 1e-20) - 10 * np.log10(np.percentile(power, 90)) for _, _, cuts in plan for c in cuts]
    v.say(f"  re-sung lead level matched by {20 * np.log10(gain):+.1f} dB; {len(cut_levels)} long phrases cut"
          + (f" at {', '.join(f'{c:+.0f}' for c in cut_levels)} dB against the loud singing" if cut_levels else ""))
    return plan


def follow_loudness(track_id):
    from scipy.ndimage import uniform_filter1d
    folder = v.stem_dir(track_id)
    target = folder / "lead_mid_shaped.wav"
    if target.exists():
        return
    lead, rate = sf.read(str(folder / "lead.wav"), dtype="float64", always_2d=True)
    resung, _ = sf.read(str(folder / "lead_mid_resung.wav"), dtype="float64")
    n = min(len(lead), len(resung))
    mid, resung = lead[:n].mean(axis=1), resung[:n]
    width = int(ENVELOPE_S * rate)
    source_power = uniform_filter1d(mid ** 2, width)
    output_power = uniform_filter1d(resung ** 2, width)
    gain = np.minimum(np.sqrt(source_power / np.maximum(output_power, 1e-12)), 10 ** (MAX_LIFT_DB / 20))
    shaped = resung * gain
    sf.write(str(target), shaped.astype(np.float32), rate, subtype="FLOAT")
    hop = rate // 10
    k = n // hop
    db = lambda x: 10 * np.log10((x[:k * hop].reshape(k, hop) ** 2).mean(axis=1) + 1e-20)
    level = db(mid)
    top = np.percentile(level, 90)
    sung, quiet = level > top - 20, level <= top - 40
    pause = lambda x: 10 * np.log10(np.mean(10 ** (db(x)[quiet] / 10)))
    v.say(f"  loudness contour put back from the lead (50 ms): follows the lead at r = {np.corrcoef(level[sung], db(resung)[sung])[0, 1]:.2f} before, "
          f"{np.corrcoef(level[sung], db(shaped)[sung])[0, 1]:.2f} after; pauses {pause(resung):.0f} dBFS before, {pause(shaped):.0f} after (lead {pause(mid):.0f})")


async def build_b(track_id):
    from services.audio_vocal_enhance_service import AudioVocalEnhanceService
    folder = v.stem_dir(track_id)
    if (folder / B_NAME).exists():
        return
    lead, rate = sf.read(str(folder / "lead.wav"), dtype="float64", always_2d=True)
    backing, _ = sf.read(str(folder / "backing.wav"), dtype="float64", always_2d=True)
    resung, _ = sf.read(str(folder / "lead_mid_shaped.wav"), dtype="float64")
    n = min(len(lead), len(backing), len(resung))
    side = (lead[:n, 0] - lead[:n, 1]) / 2 if lead.shape[1] > 1 else np.zeros(n)
    vocal = np.stack([resung[:n] + side, resung[:n] - side], axis=1) + backing[:n]
    sf.write(str(folder / "vocals_resung.wav"), vocal.astype(np.float32), rate, subtype="FLOAT")
    enhancer = AudioVocalEnhanceService()
    await enhancer.initialize()
    better = await enhancer.enhance_vocals(folder / "vocals_resung.wav", work_dir(track_id) / B_NAME)
    await enhancer.unload()
    if not better:
        raise RuntimeError("Lew's vocal Apollo failed on the re-sung vocal")
    shutil.move(str(better), str(folder / B_NAME))


def pitch_and_shape(track_id):
    import librosa
    folder = v.stem_dir(track_id)
    lead, rate = sf.read(str(folder / "lead.wav"), dtype="float64", always_2d=True)
    resung, _ = sf.read(str(folder / "lead_mid_shaped.wav"), dtype="float64")
    n = min(len(lead), len(resung))
    a = librosa.resample(lead[:n].mean(axis=1), orig_sr=rate, target_sr=22050)
    b = librosa.resample(resung[:n], orig_sr=rate, target_sr=22050)
    hop = 2205
    k = min(len(a), len(b)) // hop
    la = 10 * np.log10((a[:k * hop].reshape(k, hop) ** 2).mean(axis=1) + 1e-20)
    lb = 10 * np.log10((b[:k * hop].reshape(k, hop) ** 2).mean(axis=1) + 1e-20)
    sung = la > np.percentile(la, 90) - 20
    shape_r = float(np.corrcoef(la[sung], lb[sung])[0, 1])
    fa = librosa.yin(a, fmin=70, fmax=1000, sr=22050, frame_length=2048, hop_length=hop)[:k]
    fb = librosa.yin(b, fmin=70, fmax=1000, sr=22050, frame_length=2048, hop_length=hop)[:k]
    cents = np.abs(1200 * np.log2(fb[sung] / fa[sung]))
    cents = np.minimum(cents, np.abs(cents - 1200))
    v.say(f"  pitch: re-sung lead against the lead, median {np.median(cents):.0f} cents apart, {np.mean(cents > 50) * 100:.0f}% of sung moments over 50 cents; "
          f"loudness contour follows the original at r = {shape_r:.2f}")


def words(track_id, box):
    from faster_whisper import WhisperModel
    if "whisper" not in box:
        box["whisper"] = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="int8")
    folder = v.stem_dir(track_id)
    texts = []
    for name in ("vocals_lew.wav", B_NAME):
        segments, _ = box["whisper"].transcribe(str(folder / name), beam_size=5, vad_filter=False, condition_on_previous_text=False)
        texts.append(re.findall(r"[a-z']+", " ".join(s.text for s in segments).lower()))
    a, b = texts
    dist = np.arange(len(b) + 1)
    for i, word in enumerate(a, 1):
        prev, dist[0] = dist.copy(), i
        for j, other in enumerate(b, 1):
            dist[j] = min(prev[j] + 1, dist[j - 1] + 1, prev[j - 1] + (word != other))
    v.say(f"  words: Whisper hears {len(a)} words in A and {len(b)} in B; {dist[-1]} edits apart ({dist[-1] / max(len(a), 1) * 100:.0f}% of A's words)")


async def render(track_id, box):
    meta = json.loads((v.settings.CATALOG_DIR / "metadata" / f"{track_id}.json").read_text(encoding="utf-8"))
    title = "".join(c if c.isalnum() or c in " &'" else " " for c in (meta.get("generation_params") or {}).get("title", track_id)).strip()
    started = time.perf_counter()
    v.say(f"\n{title} ({track_id[:8]})")
    await v.split(track_id)
    split_lead(track_id, box)
    resing(track_id)
    follow_loudness(track_id)
    await build_b(track_id)
    v.compare_vocals(track_id, B_NAME)
    folder = v.stem_dir(track_id)
    for label, name in (("vocal stem (raw)", "vocals.wav"), ("A: now (Lew)", "vocals_lew.wav"), ("B: re-sung + Lew", B_NAME)):
        v.say("  " + lock_line(label, lock_score(folder / name)))
    pitch_and_shape(track_id)
    words(track_id, box)
    v.write_vocals(track_id, title, B_NAME, B_LABEL)
    v.say(f"  done in {time.perf_counter() - started:.0f} s")


async def main(ids):
    v.OUT.mkdir(parents=True, exist_ok=True)
    v.say(f"re-sung lead A/B, {time.strftime('%Y-%m-%d %H:%M')}")
    box = {}
    for track_id in ids:
        try:
            await render(track_id, box)
        except Exception as error:
            v.say(f"{track_id}: FAILED: {error!r}")
    v.say("\nall done")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ids", nargs="+")
    asyncio.run(main(parser.parse_args().ids))
