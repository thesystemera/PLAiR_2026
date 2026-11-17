"""python covershot_diff.py <dir a> <dir b> - compares the covers two covershot.mjs runs saved (same names): per cover the
mean and max difference per channel and the share of pixels off by more than 0, 2 and 8 levels."""
import json
import sys
from pathlib import Path

import numpy as np


def load(folder, name):
    meta = json.loads((folder / f'{name}.json').read_text())
    data = np.frombuffer((folder / f'{name}.rgba').read_bytes(), dtype=np.uint8)
    return meta['title'], data.reshape(meta['height'], meta['width'], 4).astype(np.int16)


def main():
    a, b = Path(sys.argv[1]), Path(sys.argv[2])
    names = sorted(p.stem for p in a.glob('*.rgba') if (b / p.name).exists())
    if not names:
        print('no covers in common')
        return
    for name in names:
        title, first = load(a, name)
        _, second = load(b, name)
        if first.shape != second.shape:
            print(f'{title}: sizes differ {first.shape} vs {second.shape}')
            continue
        diff = np.abs(first[..., :3] - second[..., :3])
        worst = diff.max(axis=2)
        print(f'{title[:40]:40s} mean {diff.mean():.4f}  max {diff.max():3d}  '
              f'pixels off >0: {100 * (worst > 0).mean():.3f}%  >2: {100 * (worst > 2).mean():.3f}%  >8: {100 * (worst > 8).mean():.3f}%')


if __name__ == '__main__':
    main()
