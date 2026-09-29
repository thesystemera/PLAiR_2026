import base64
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np

FINGERPRINT_SECONDS = 120
MAX_SHIFT_FRAMES = 48
MIN_OVERLAP_FRAMES = 60
SAME_SONG_SIMILARITY = 0.8


def fingerprint(path: Path) -> Optional[np.ndarray]:
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-t", str(FINGERPRINT_SECONDS), "-ac", "1",
         "-f", "chromaprint", "-fp_format", "raw", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if result.returncode != 0 or len(result.stdout) < 4 * MIN_OVERLAP_FRAMES:
        return None
    return np.frombuffer(result.stdout[:len(result.stdout) // 4 * 4], dtype="<u4").copy()


def encode(fp: np.ndarray) -> str:
    return base64.b64encode(fp.astype("<u4").tobytes()).decode("ascii")


def decode(text: Optional[str]) -> Optional[np.ndarray]:
    if not text:
        return None
    try:
        return np.frombuffer(base64.b64decode(text), dtype="<u4").copy()
    except (ValueError, TypeError):
        return None


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    best = 0.0
    for shift in range(-MAX_SHIFT_FRAMES, MAX_SHIFT_FRAMES + 1):
        x = a[max(0, shift):]
        y = b[max(0, -shift):]
        n = min(len(x), len(y))
        if n < MIN_OVERLAP_FRAMES:
            continue
        differing = np.unpackbits(np.bitwise_xor(x[:n], y[:n]).view(np.uint8)).sum()
        best = max(best, 1.0 - differing / (32.0 * n))
    return best
