import asyncio
import os
import uuid
from pathlib import Path

from PIL import Image

from config import settings

THUMBNAIL_SIZES = (256, 512, 768)
THUMBNAIL_QUALITY = 82
THUMBNAIL_DIR: Path = settings.CATALOG_DIR / "artwork_thumbs"

_generation_slots = asyncio.Semaphore(2)
_inflight: dict[tuple[str, int], asyncio.Future] = {}


def thumbnail_path(track_id: str, size: int) -> Path:
    return THUMBNAIL_DIR / str(size) / f"{track_id}.jpeg"


def _is_fresh(target: Path, source: Path) -> bool:
    try:
        return target.stat().st_mtime >= source.stat().st_mtime
    except FileNotFoundError:
        return False


def _render_thumbnail(source: Path, target: Path, size: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.stem}.{uuid.uuid4().hex}.tmp")
    try:
        with Image.open(source) as image:
            image.draft("RGB", (size, size))
            image = image.convert("RGB")
            image.thumbnail((size, size), Image.Resampling.LANCZOS)
            image.save(temp, "JPEG", quality=THUMBNAIL_QUALITY, optimize=True)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


async def _generate(source: Path, target: Path, size: int) -> None:
    async with _generation_slots:
        if not _is_fresh(target, source):
            await asyncio.to_thread(_render_thumbnail, source, target, size)


async def ensure_thumbnail(track_id: str, source: Path, size: int) -> Path:
    if size not in THUMBNAIL_SIZES:
        raise ValueError(f"Unsupported thumbnail size: {size}")
    target = thumbnail_path(track_id, size)
    if _is_fresh(target, source):
        return target

    key = (track_id, size)
    pending = _inflight.get(key)
    if pending is None:
        pending = asyncio.ensure_future(_generate(source, target, size))
        _inflight[key] = pending
        pending.add_done_callback(lambda _f: _inflight.pop(key, None))
    await asyncio.shield(pending)
    return target
