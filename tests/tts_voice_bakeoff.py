import os
import re
import statistics as st
import sys
import time
from collections import Counter
from difflib import SequenceMatcher

os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "server"))

import numpy as np
import pyloudnorm
import requests
from faster_whisper import WhisperModel

from config.settings import settings

VOICES = ["leo", "tara", "leah", "jess", "mia", "zoe", "dan", "zac"]
LINES = [
    "Alright, alright, we hear ya, no falling asleep on our watch!",
    "Tomorrow's looking a bit soggy, so grab a brolly before you head out.",
    "Kicking it off with some seriously good Radiohead, straight from the vault.",
    "Aaron Chen is at the Civic on Friday, and there's an open mic at Snatch on Thursday.",
    "Oh hell yeah, that track is absolute gold, one of the best this week.",
    "We don't carry any Britney in the vault, but Mariah's got you covered.",
    "Skipping this one right now, let's find you something with a bit more bite.",
    "That's your nine o'clock bulletin, now back to the music.",
]
RATE = 24000
whisper = WhisperModel("large-v3-turbo", device="cuda", compute_type="int8")
meter = pyloudnorm.Meter(RATE)


def words(text):
    return re.findall(r"[a-z0-9']+", text.lower())


def render(text, voice):
    started = time.perf_counter()
    r = requests.post(f"{settings.TTS_SERVER_URL}/tts", json={"text": text, "voice": voice}, timeout=120)
    r.raise_for_status()
    pcm = np.frombuffer(r.content, dtype=np.int16).astype(np.float32) / 32768.0
    return pcm, time.perf_counter() - started


print(f"{'voice':6} {'words/s':>8} {'LUFS':>6} {'heard':>6} {'loops':>6} {'cut':>4} {'RTF':>5}")
for voice in VOICES:
    rates, loud, recalls, loops, cuts, rtfs = [], [], [], 0, 0, []
    for line in LINES:
        audio, took = render(line, voice)
        seconds = len(audio) / RATE
        if seconds < 0.3:
            cuts += 1
            continue
        rtfs.append(took / seconds)
        loud.append(meter.integrated_loudness(audio))
        segments, _ = whisper.transcribe(audio, language="en", beam_size=1)
        heard = words(" ".join(s.text for s in segments))
        wanted = words(line)
        rates.append(len(wanted) / seconds)
        matcher = SequenceMatcher(None, wanted, heard)
        recalls.append(sum(b.size for b in matcher.get_matching_blocks()) / len(wanted))
        wanted_grams = Counter(zip(wanted, wanted[1:], wanted[2:]))
        heard_grams = Counter(zip(heard, heard[1:], heard[2:]))
        if len(heard) > len(wanted) + 3 and any(n >= 2 and wanted_grams[g] < 2 for g, n in heard_grams.items()
                                                if g in wanted_grams):
            loops += 1
        if not set(wanted[-2:]) & set(heard[-4:]):
            cuts += 1
    print(f"{voice:6} {st.median(rates):8.2f} {st.median(loud):6.1f} {st.median(recalls) * 100:5.0f}% "
          f"{loops:6d} {cuts:4d} {st.median(rtfs):5.2f}", flush=True)
