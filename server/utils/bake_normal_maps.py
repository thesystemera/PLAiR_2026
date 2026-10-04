import os

os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from services.normal_map_service import ensure_track_normal, is_fresh, set_bake_parallelism, track_normal_path
from services.suno_artwork_enrichment_service import artwork_enrichment_service

PARALLEL = 4


async def main():
    await artwork_enrichment_service.initialize()
    if not artwork_enrichment_service.available:
        print("depth model unavailable")
        return
    sources = sorted(settings.ARTWORK_DIR.glob("*.jpeg"))
    todo = [path.stem for path in sources if not is_fresh(track_normal_path(path.stem), path)]
    print(f"{len(sources)} covers, {len(todo)} need normal maps")
    set_bake_parallelism(PARALLEL)
    started = time.perf_counter()
    failed = []
    for start in range(0, len(todo), 100):
        batch = todo[start:start + 100]
        results = await asyncio.gather(*(ensure_track_normal(track_id) for track_id in batch), return_exceptions=True)
        failed += [f"{track_id}: {result}" for track_id, result in zip(batch, results) if isinstance(result, Exception) or result is None]
        print(f"{start + len(batch)}/{len(todo)} in {time.perf_counter() - started:.0f} s")
    for error in failed:
        print(f"failed {error}")


if __name__ == "__main__":
    asyncio.run(main())
