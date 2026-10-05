import asyncio
import os
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

from config import settings

THUMBNAIL_SIZES = (256, 512, 768)
THUMBNAIL_QUALITY = 82
DEPTH_THUMBNAIL_QUALITY = 90
PACK_QUALITY = 90
THUMBNAIL_DIR: Path = settings.CATALOG_DIR / "artwork_thumbs"

_generation_slots = asyncio.Semaphore(2)
_inflight: dict[tuple[str, str, int], asyncio.Future] = {}


def thumbnail_path(track_id: str, size: int, variant: str = "artwork") -> Path:
    if variant in ("depth", "normal"):
        return THUMBNAIL_DIR / variant / str(size) / f"{track_id}.jpeg"
    return THUMBNAIL_DIR / str(size) / f"{track_id}.jpeg"


def _is_fresh(target: Path, source: Path) -> bool:
    try:
        return target.stat().st_mtime >= source.stat().st_mtime
    except FileNotFoundError:
        return False


def _render_thumbnail(source: Path, target: Path, size: int, variant: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.stem}.{uuid.uuid4().hex}.tmp")
    try:
        with Image.open(source) as image:
            if variant == "depth":
                image.draft("RGB", (size * 2, size))
                width, height = image.size
                image = image.crop((width // 2, 0, width, height)).convert("L")
                quality = DEPTH_THUMBNAIL_QUALITY
            elif variant == "normal":
                image = image.convert("RGB")
                image.thumbnail((size, size), Image.Resampling.BILINEAR)
                image.save(temp, "JPEG", quality=DEPTH_THUMBNAIL_QUALITY, subsampling=0)
                os.replace(temp, target)
                return
            else:
                image.draft("RGB", (size, size))
                image = image.convert("RGB")
                quality = THUMBNAIL_QUALITY
            image.thumbnail((size, size), Image.Resampling.LANCZOS)
            image.save(temp, "JPEG", quality=quality, optimize=True)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


async def _generate(source: Path, target: Path, size: int, variant: str) -> None:
    async with _generation_slots:
        if not _is_fresh(target, source):
            await asyncio.to_thread(_render_thumbnail, source, target, size, variant)


async def ensure_thumbnail(track_id: str, source: Path, size: int, variant: str = "artwork") -> Path:
    if size not in THUMBNAIL_SIZES:
        raise ValueError(f"Unsupported thumbnail size: {size}")
    target = thumbnail_path(track_id, size, variant)
    if _is_fresh(target, source):
        return target

    key = (variant, track_id, size)
    pending = _inflight.get(key)
    if pending is None:
        pending = asyncio.ensure_future(_generate(source, target, size, variant))
        _inflight[key] = pending
        pending.add_done_callback(lambda _f: _inflight.pop(key, None))
    await asyncio.shield(pending)
    return target


def pack_path(track_id: str, size: int) -> Path:
    return THUMBNAIL_DIR / "pack" / str(size) / f"{track_id}.jpeg"


def render_pack(color_source: Path, depth_source: Path, normal_source: Path, target: Path, size: int, depth_side_by_side: bool) -> None:
    """Colour on the left; on the right the normal's x in red, depth in green, the normal's y in blue."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.stem}.{uuid.uuid4().hex}.tmp")
    try:
        with Image.open(color_source) as image:
            image.draft("RGB", (size, size))
            color = image.convert("RGB")
        color.thumbnail((size, size), Image.Resampling.LANCZOS)
        width, height = color.size
        with Image.open(depth_source) as image:
            if depth_side_by_side:
                image.draft("RGB", (size * 2, size))
                full_width, full_height = image.size
                image = image.crop((full_width // 2, 0, full_width, full_height))
            depth = np.asarray(image.convert("L").resize((width, height), Image.Resampling.LANCZOS))
        with Image.open(normal_source) as image:
            normal = np.asarray(image.convert("RGB").resize((width, height), Image.Resampling.LANCZOS))
        packed = Image.new("RGB", (width * 2, height))
        packed.paste(color, (0, 0))
        packed.paste(Image.fromarray(np.dstack((normal[..., 1], depth, normal[..., 2]))), (width, 0))
        packed.save(temp, "JPEG", quality=PACK_QUALITY, subsampling=0, optimize=True)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


async def ensure_pack(key: str, target: Path, sources: tuple[Path, ...], size: int, depth_side_by_side: bool) -> Path:
    color_source, depth_source, normal_source = sources
    if all(_is_fresh(target, source) for source in sources):
        return target
    pending = _inflight.get(("pack", key, size))
    if pending is None:
        async def run() -> None:
            async with _generation_slots:
                if not all(_is_fresh(target, source) for source in sources):
                    await asyncio.to_thread(render_pack, color_source, depth_source, normal_source, target, size, depth_side_by_side)
        pending = asyncio.ensure_future(run())
        _inflight[("pack", key, size)] = pending
        pending.add_done_callback(lambda _f: _inflight.pop(("pack", key, size), None))
    await asyncio.shield(pending)
    return target
