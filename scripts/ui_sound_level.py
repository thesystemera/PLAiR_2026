"""Loudest 100 ms of an interface sound (K-weighted, like a sound level meter's fast setting), for client/src/lib/uiSounds.js.

Usage: E:/AI_RADIO/.venv/Scripts/python.exe scripts/ui_sound_level.py client/public/audio/interface/txt.mp3 [...]
"""
import math
import subprocess
import sys

import numpy as np
import pyloudnorm as pyln
from scipy.signal import lfilter

RATE = 48000
WINDOW_S = 0.1


def decode(path):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-f', 'f32le', '-ac', '2', '-ar', str(RATE), '-'], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).astype(np.float64)


def loudest_window(samples):
    weighted = samples
    for stage in pyln.Meter(RATE)._filters.values():
        weighted = lfilter(stage.b, stage.a, weighted, axis=0)
    power = np.concatenate([[0.0], np.cumsum((weighted ** 2).sum(axis=1))])
    width = int(WINDOW_S * RATE)
    mean = (power[width:] - power[:-width]) / width
    return -0.691 + 10 * math.log10(max(float(mean.max()), 1e-12))


for path in sys.argv[1:]:
    samples = decode(path)
    print(f'{loudest_window(samples):6.1f}  peak {20 * math.log10(float(np.abs(samples).max())):6.1f}  {path}')
