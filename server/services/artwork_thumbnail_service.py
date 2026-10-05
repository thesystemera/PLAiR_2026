import asyncio
from typing import Optional
import os
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

from config import settings

PACK_SIZES = (256, 512, 768, 1024)
PACK_QUALITY = 90
PACK_BACKFILL_PARALLEL = 2
THUMBNAIL_DIR: Path = settings.CATALOG_DIR / "artwork_thumbs"

_generation_slots = asyncio.Semaphore(2)
_inflight: dict[tuple[str, str, int], asyncio.Future] = {}


def _is_fresh(target: Path, source: Path) -> bool:
    try:
        return target.stat().st_mtime >= source.stat().st_mtime
    except FileNotFoundError:
        return False


def pack_path(track_id: str, size: int) -> Path:
    return THUMBNAIL_DIR / "pack" / str(size) / f"{track_id}.jpeg"


def render_pack(color_source: Path, depth_source: Optional[Path], normal_source: Optional[Path], target: Path, size: int, depth_side_by_side: bool) -> None:
    """Colour on the left; on the right the normal's x in red, depth in green, the normal's y in blue (flat until the maps exist)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.stem}.{uuid.uuid4().hex}.tmp")
    try:
        with Image.open(color_source) as image:
            image.draft("RGB", (size, size))
            color = image.convert("RGB")
        color.thumbnail((size, size), Image.Resampling.LANCZOS)
        width, height = color.size
        depth = np.full((height, width), 128, np.uint8)
        if depth_source:
            with Image.open(depth_source) as image:
                if depth_side_by_side:
                    image.draft("RGB", (size * 2, size))
                    full_width, full_height = image.size
                    image = image.crop((full_width // 2, 0, full_width, full_height))
                depth = np.asarray(image.convert("L").resize((width, height), Image.Resampling.LANCZOS))
        normal = np.full((height, width, 3), 128, np.uint8)
        if normal_source:
            with Image.open(normal_source) as image:
                normal = np.asarray(image.convert("RGB").resize((width, height), Image.Resampling.LANCZOS))
        packed = Image.new("RGB", (width * 2, height))
        packed.paste(color, (0, 0))
        packed.paste(Image.fromarray(np.dstack((normal[..., 1], depth, normal[..., 2]))), (width, 0))
        packed.save(temp, "JPEG", quality=PACK_QUALITY, subsampling=0, optimize=True)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


async def ensure_pack(key: str, target: Path, sources: tuple[Optional[Path], ...], size: int, depth_side_by_side: bool) -> Path:
    color_source, depth_source, normal_source = sources
    present = [source for source in sources if source]
    if all(_is_fresh(target, source) for source in present):
        return target
    pending = _inflight.get(("pack", key, size))
    if pending is None:
        async def run() -> None:
            async with _generation_slots:
                if not all(_is_fresh(target, source) for source in present):
                    await asyncio.to_thread(render_pack, color_source, depth_source, normal_source, target, size, depth_side_by_side)
        pending = asyncio.ensure_future(run())
        _inflight[("pack", key, size)] = pending
        pending.add_done_callback(lambda _f: _inflight.pop(("pack", key, size), None))
    await asyncio.shield(pending)
    return target


def stale_track_packs(tracks: list[tuple[str, Path]]) -> list[tuple[Path, tuple[Path, Optional[Path], Optional[Path]], int]]:
    from services.normal_map_service import track_normal_path
    jobs = []
    for track_id, artwork in tracks:
        enriched = settings.ARTWORK_ENRICHED_DIR / f"{track_id}.jpeg"
        normal = track_normal_path(track_id)
        sources = (artwork, enriched if enriched.exists() else None, normal if normal.exists() else None)
        present = [source for source in sources if source]
        for size in PACK_SIZES:
            target = pack_path(track_id, size)
            if not all(_is_fresh(target, source) for source in present):
                jobs.append((target, sources, size))
    return jobs


async def backfill_track_packs(tracks: list[tuple[str, Path]], parallel: int = PACK_BACKFILL_PARALLEL) -> int:
    """Renders every missing or stale cover pack ahead of time, so no cover waits for one on screen."""
    jobs = await asyncio.to_thread(stale_track_packs, tracks)
    slots = asyncio.Semaphore(parallel)

    async def render(job) -> bool:
        target, (color, depth, normal), size = job
        async with slots:
            try:
                await asyncio.to_thread(render_pack, color, depth, normal, target, size, True)
                return True
            except Exception:
                return False

    results = await asyncio.gather(*(render(job) for job in jobs))
    return sum(results)
