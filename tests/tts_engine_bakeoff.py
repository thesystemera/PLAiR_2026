import argparse
import json
import re
import statistics as st
import threading
import time
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import pyloudnorm
import requests
import soundfile as sf

RATE = 24000
OUT = Path("D:/tts_candidates/bakeoff")

LINES = [
    ("line", "Alright, alright, we hear ya, no falling asleep on our watch!"),
    ("line", "Kicking it off with some seriously good Radiohead, straight from the vault."),
    ("line", "Aaron Chen is at the Civic on Friday, and there's an open mic at Snatch on Thursday."),
    ("line", "Wet Denim's doing their Water For Dogs EP release at Whammy on the third, if you want some proper indie noise."),
    ("line", "Tomorrow's looking a bit soggy, so grab a brolly before you head out."),
    ("line", "Um, I think, uh, there's some good gigs happening this week."),
    ("number", "It's forty-six minutes past nine, and this is Play Air."),
    ("number", "That's your nine o'clock bulletin, now back to the music."),
    ("number", "Doors at half past seven, tickets are twenty-five bucks."),
    ("short", "We're doing bloody fantastic, mate!"),
    ("short", "Hold tight."),
    ("short", "Yeah!"),
    ("short", "Oh."),
    ("short", "Right."),
    ("short", "Woah, woah,"),
    ("vocal", "Mm-hmm."),
    ("vocal", "Ha! Ha ha, oh man."),
    ("vocal", "Hmm."),
    ("vocal", "Ugh."),
    ("vocal", "Ooh!"),
    ("vocal", "Heh heh, nice one."),
    ("vocal", "Ah ha ha ha, no way, w-w-wait, what?"),
    ("breath", "..hff"),
    ("breath", "..mm"),
    ("breath", "..ah"),
]


def words(text):
    return re.findall(r"[a-z0-9']+", text.lower())


def stream_post(url, body, timeout=180):
    started = time.perf_counter()
    first = None
    chunks = []
    with requests.post(url, json=body, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        for chunk in r.iter_content(chunk_size=4096):
            if chunk and first is None:
                first = time.perf_counter() - started
            chunks.append(chunk)
    raw = b"".join(chunks)
    raw = raw[: len(raw) // 2 * 2]
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return pcm, first or 0.0, time.perf_counter() - started


def render(kind, url, voice, text, instruct=None):
    if kind == "orpheus":
        return stream_post(f"{url}/tts", {"text": text, "voice": voice})
    if kind == "qwen":
        body = {"input": text, "voice": voice, "language": "English", "response_format": "pcm"}
        if instruct:
            body["instructions"] = instruct
        return stream_post(f"{url}/v1/audio/speech", body)
    if kind == "chatterbox":
        return stream_post(f"{url}/tts", {"text": text, "voice": voice})
    raise ValueError(kind)


def score(whisper, meter, audio, line_kind, text):
    seconds = len(audio) / RATE
    row = {"seconds": round(seconds, 2)}
    if seconds < 0.15:
        row["empty"] = True
        return row
    try:
        row["lufs"] = round(meter.integrated_loudness(audio), 1) if seconds >= 0.4 else None
    except ValueError:
        row["lufs"] = None
    segments, _ = whisper.transcribe(audio, language="en", beam_size=1)
    heard_text = " ".join(s.text for s in segments).strip()
    row["heard"] = heard_text
    if line_kind in ("line", "number", "short"):
        wanted, heard = words(text), words(heard_text)
        matcher = SequenceMatcher(None, wanted, heard)
        row["recall"] = round(sum(b.size for b in matcher.get_matching_blocks()) / max(1, len(wanted)), 2)
        row["words_per_s"] = round(len(wanted) / seconds, 2)
        grams_w = Counter(zip(wanted, wanted[1:], wanted[2:]))
        grams_h = Counter(zip(heard, heard[1:], heard[2:]))
        row["loop"] = len(heard) > len(wanted) + 3 and any(
            n >= 2 and grams_w[g] < 2 for g, n in grams_h.items() if g in grams_w)
        row["cut"] = bool(wanted) and not set(wanted[-2:]) & set(heard[-4:])
        row["extra_words"] = max(0, len(heard) - len(wanted))
    return row


def run_voice(group, kind, url, voice, whisper, meter, instruct=None, label=None):
    folder = OUT / group / (label or voice)
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    render(kind, url, voice, "Warming up.", instruct)
    for i, (line_kind, text) in enumerate(LINES, 1):
        audio, ttfa, took = render(kind, url, voice, text, instruct)
        sf.write(folder / f"{i:02d}.wav", audio, RATE, subtype="PCM_16")
        row = {"n": i, "kind": line_kind, "text": text, "ttfa": round(ttfa, 3), "took": round(took, 3)}
        row.update(score(whisper, meter, audio, line_kind, text))
        if row["seconds"] > 0:
            row["rtf"] = round(took / row["seconds"], 2)
        rows.append(row)
        print(f"  {i:02d} {line_kind:6} {row['seconds']:5.2f}s ttfa {ttfa:5.2f} rtf {row.get('rtf', 0):4.2f} "
              f"| {row.get('heard', '')[:60]}", flush=True)
    return rows


def concurrency(kind, url, voice, instruct=None):
    text = LINES[3][1]
    results = []

    def one():
        results.append(render(kind, url, voice, text, instruct))

    started = time.perf_counter()
    threads = [threading.Thread(target=one) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - started
    audio_s = sum(len(a) / RATE for a, _, _ in results)
    return {"two_streams_audio_per_s": round(audio_s / wall, 2),
            "two_streams_ttfa": [round(f, 3) for _, f, _ in results]}


def summarise(rows):
    speech = [r for r in rows if r["kind"] in ("line", "number")]
    def med(key, pool):
        vals = [r[key] for r in pool if r.get(key) is not None]
        return round(st.median(vals), 2) if vals else None
    return {
        "rtf": med("rtf", speech),
        "ttfa": med("ttfa", speech),
        "words_per_s": med("words_per_s", speech),
        "lufs": med("lufs", speech),
        "recall": med("recall", speech + [r for r in rows if r["kind"] == "short"]),
        "loops": sum(1 for r in rows if r.get("loop")),
        "cuts": sum(1 for r in rows if r.get("cut")),
        "short_extra_words": sum(r.get("extra_words", 0) for r in rows if r["kind"] == "short"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="append", required=True,
                        help="group:kind:url:voice[:instruct] e.g. qwen-preset:qwen:http://127.0.0.1:8092:ryan")
    parser.add_argument("--no-concurrency", action="store_true")
    args = parser.parse_args()

    from faster_whisper import WhisperModel
    whisper = WhisperModel("large-v3-turbo", device="cuda", compute_type="int8")
    meter = pyloudnorm.Meter(RATE)

    results_path = OUT / "results.json"
    results = json.loads(results_path.read_text()) if results_path.exists() else {}
    for spec in args.run:
        parts = spec.split(":")
        group, kind = parts[0], parts[1]
        url = ":".join(parts[2:5])
        voice = parts[5]
        instruct = ":".join(parts[6:]) or None
        label = voice if not instruct else f"{voice}-instruct"
        print(f"== {group} / {label}", flush=True)
        rows = run_voice(group, kind, url, voice, whisper, meter, instruct, label)
        entry = {"group": group, "kind": kind, "voice": voice, "instruct": instruct,
                 "summary": summarise(rows), "rows": rows}
        if not args.no_concurrency:
            entry["summary"].update(concurrency(kind, url, voice, instruct))
        print("  ", entry["summary"], flush=True)
        results[f"{group}/{label}"] = entry
        results_path.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
