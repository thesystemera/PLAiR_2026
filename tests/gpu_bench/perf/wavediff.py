"""python wavediff.py <before.png> <after.png> : compares two waveshot.mjs captures of the same song column by column,
leaving out the columns between and around the two playheads. Prints the difference for the played and unplayed parts."""
import json
import sys

import numpy as np
from PIL import Image

before_path, after_path = sys.argv[1], sys.argv[2]
before = np.asarray(Image.open(before_path).convert('RGB')).astype(np.int16)
after = np.asarray(Image.open(after_path).convert('RGB')).astype(np.int16)
info_before = json.load(open(before_path.replace('.png', '.json')))
info_after = json.load(open(after_path.replace('.png', '.json')))
height = min(before.shape[0], after.shape[0])
width = min(before.shape[1], after.shape[1])
before, after = before[:height, :width], after[:height, :width]
print(f"sizes {before.shape[1]}x{before.shape[0]}; before: {info_before['kind']} ({info_before['count']} elements, played {info_before['played']:.3f}); after: {info_after['kind']} ({info_after['count']} elements, played {info_after['played']:.3f})")
low = min(info_before['played'], info_after['played']) * width
high = max(info_before['played'], info_after['played']) * width
margin = 14
edge = 6
for name, start, end in (('played part', edge, int(low) - margin), ('unplayed part', int(high) + margin, width - edge)):
    if end - start < 8:
        print(f'{name}: too narrow to compare')
        continue
    diff = np.abs(before[:, start:end] - after[:, start:end])
    per_pixel = diff.max(axis=2)
    print(f"{name}: columns {start}-{end}, mean difference {diff.mean():.2f} levels, 99th percentile {np.percentile(per_pixel, 99):.0f}, max {per_pixel.max()}, pixels off by more than 8: {(per_pixel > 8).mean() * 100:.2f}%")
