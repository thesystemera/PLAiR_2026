"""python fillcheck_diff.py <dir> - compares every *_old.png with its *_new.png from fillcheck.mjs (or any pair saved
with those names): largest channel difference, and how many pixels differ by more than 1 and more than 3 levels."""
import glob
import os
import sys

import numpy as np
from PIL import Image

folder = sys.argv[1] if len(sys.argv) > 1 else 'E:/scratch/plair/fillcheck'
for old_path in sorted(glob.glob(os.path.join(folder, '*_old.png'))):
    new_path = old_path.replace('_old.png', '_new.png')
    if not os.path.exists(new_path):
        continue
    old = np.asarray(Image.open(old_path).convert('RGB'), dtype=np.int16)
    new = np.asarray(Image.open(new_path).convert('RGB'), dtype=np.int16)
    if old.shape != new.shape:
        print(f'{os.path.basename(old_path)}: sizes differ {old.shape} vs {new.shape}')
        continue
    diff = np.abs(old - new).max(axis=2)
    total = diff.size
    print(f'{os.path.basename(old_path)[:-8]:28s} {old.shape[1]}x{old.shape[0]}  max diff {int(diff.max()):3d}  '
          f'>1: {int((diff > 1).sum()):6d} ({100 * (diff > 1).sum() / total:.3f}%)  >3: {int((diff > 3).sum()):6d}  '
          f'mean {diff.mean():.4f}')
    if diff.max() > 1:
        ys, xs = np.nonzero(diff > 1)
        print(f'    differing area x {xs.min()}-{xs.max()}, y {ys.min()}-{ys.max()}')
        Image.fromarray(np.clip(diff * 16, 0, 255).astype(np.uint8)).save(old_path.replace('_old.png', '_diff_x16.png'))
