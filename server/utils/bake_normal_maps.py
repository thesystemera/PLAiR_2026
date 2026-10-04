import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2

from config import settings
from services.normal_map_service import bake_from_side_by_side, is_fresh, track_normal_path


def bake(source: str) -> str:
    cv2.setNumThreads(1)
    source_path = Path(source)
    target = track_normal_path(source_path.stem)
    try:
        bake_from_side_by_side(source_path, target)
        return ""
    except Exception as e:
        return f"{source_path.stem}: {e}"


def main():
    sources = sorted(settings.ARTWORK_ENRICHED_DIR.glob("*.jpeg"))
    todo = [str(path) for path in sources if not is_fresh(track_normal_path(path.stem), path)]
    print(f"{len(sources)} covers, {len(todo)} need normal maps")
    if not todo:
        return
    track_normal_path("x").parent.mkdir(parents=True, exist_ok=True)
    workers = max(1, min(8, (os.cpu_count() or 2) // 2))
    started = time.perf_counter()
    failed = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for done, error in enumerate(pool.map(bake, todo, chunksize=8), 1):
            if error:
                failed.append(error)
            if done % 200 == 0 or done == len(todo):
                print(f"{done}/{len(todo)} in {time.perf_counter() - started:.0f} s")
    for error in failed:
        print(f"failed {error}")


if __name__ == "__main__":
    main()
