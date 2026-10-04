import asyncio
import os
import uuid
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from config import settings

NORMAL_MAP_SIZE = 1024
NORMAL_MAP_QUALITY = 92
GRADIENT_STRENGTH = 0.5
DEPTH_SMOOTH_PX_AT_512 = 1.0
DETAIL_STRENGTH = 0.85 * 4.0

_SOBEL_X = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], np.float32)
_SOBEL_Y = _SOBEL_X.T.copy()
_LAPLACIAN = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], np.float32)
_DETAIL = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]], np.float32) / 8.0
_GRAY = np.array([0.114, 0.587, 0.299], np.float32)


def _filter(image, kernel):
    return cv2.filter2D(image, cv2.CV_32F, kernel, borderType=cv2.BORDER_REPLICATE)


def _photo_detail(bgr, depth):
    gray = (bgr.astype(np.float32) * _GRAY).sum(2) / 255.0
    edges = np.sqrt(_filter(gray, _SOBEL_X) ** 2 + _filter(gray, _SOBEL_Y) ** 2) * 0.8
    details = np.abs(_filter(gray, _DETAIL))
    features = (edges + details * 0.9) * (1.0 + depth * 0.28)
    features = np.power(np.maximum(features, 0.0), 0.45)
    thickness = 0.6 + 1.9 * depth
    scaled = features * (thickness / 2.5)
    result = scaled.copy()
    for radius in (1, 2, 3):
        weight = np.clip(thickness - (radius - 1), 0.0, 1.0)
        grown = cv2.dilate(scaled, np.ones((radius * 2 + 1, radius * 2 + 1), np.uint8))
        result = np.maximum(result, grown * weight)
    return np.clip(result, 0.0, 1.0)


def normal_map(bgr, depth_u8):
    depth = depth_u8.astype(np.float32) / 255.0
    sigma = DEPTH_SMOOTH_PX_AT_512 * depth.shape[1] / 512.0
    smooth = cv2.GaussianBlur(depth, (0, 0), sigma)
    span = max(float(smooth.max() - smooth.min()), 1e-6)
    depth255 = (smooth - float(smooth.min())) / span * 255.0

    gx = _filter(depth255, _SOBEL_X) * GRADIENT_STRENGTH
    gy = _filter(depth255, _SOBEL_Y) * GRADIENT_STRENGTH
    length = np.sqrt(gx * gx + gy * gy + 1.0)
    gx, gy, gz = gx / length * 0.5, gy / length * 0.5, 0.5 / length

    edge = _photo_detail(bgr, depth)
    edge = edge / max(float(edge.max()), 1e-6)
    curvature = _filter(depth255, _LAPLACIAN)
    direction = np.where(np.abs(curvature) < 0.75, 1.0, np.sign(curvature))
    strength = edge * DETAIL_STRENGTH * direction
    gx = gx + _filter(edge, _SOBEL_X) * strength
    gy = gy + _filter(edge, _SOBEL_Y) * strength

    length = np.maximum(np.sqrt(gx * gx + gy * gy + gz * gz), 1e-6)
    normals = np.dstack((gz / length, -gy / length, -gx / length))
    return np.clip(normals * 127.5 + 127.5, 0, 255).astype(np.uint8)


def _write_jpeg(target, image):
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.stem}.{uuid.uuid4().hex}.tmp.jpg")
    try:
        params = [cv2.IMWRITE_JPEG_QUALITY, NORMAL_MAP_QUALITY, cv2.IMWRITE_JPEG_SAMPLING_FACTOR, cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444]
        if not cv2.imwrite(str(temp), image, params):
            raise OSError(f"could not write {target.name}")
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def _fit(image, size, interpolation):
    height, width = image.shape[:2]
    scale = min(1.0, size / max(height, width))
    if scale >= 1.0:
        return image
    return cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=interpolation)


def bake_from_side_by_side(source: Path, target: Path) -> None:
    sbs = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if sbs is None:
        raise OSError(f"could not read {source.name}")
    half = sbs.shape[1] // 2
    color = _fit(sbs[:, :half], NORMAL_MAP_SIZE, cv2.INTER_AREA)
    depth = _fit(cv2.cvtColor(sbs[:, half:half * 2], cv2.COLOR_BGR2GRAY), NORMAL_MAP_SIZE, cv2.INTER_AREA)
    _write_jpeg(target, normal_map(color, depth))


def bake_from_pair(color_path: Path, depth_path: Path, target: Path) -> None:
    color = cv2.imread(str(color_path), cv2.IMREAD_COLOR)
    depth = cv2.imread(str(depth_path), cv2.IMREAD_GRAYSCALE)
    if color is None or depth is None:
        raise OSError(f"could not read {color_path.name} / {depth_path.name}")
    depth = cv2.resize(depth, (color.shape[1], color.shape[0]), interpolation=cv2.INTER_AREA)
    _write_jpeg(target, normal_map(_fit(color, NORMAL_MAP_SIZE, cv2.INTER_AREA), _fit(depth, NORMAL_MAP_SIZE, cv2.INTER_AREA)))


def is_fresh(target: Path, *sources: Path) -> bool:
    try:
        built = target.stat().st_mtime
        return all(built >= source.stat().st_mtime for source in sources)
    except FileNotFoundError:
        return False


NORMALS_DIR: Path = settings.CATALOG_DIR / "artwork_normals"
_bake_slots = asyncio.Semaphore(2)
_inflight: dict[str, asyncio.Future] = {}


def track_normal_path(track_id: str) -> Path:
    return NORMALS_DIR / f"{track_id}.jpeg"


async def _ensure(key: str, target: Path, sources: tuple[Path, ...], bake) -> Optional[Path]:
    if not all(source.exists() for source in sources):
        return None
    if is_fresh(target, *sources):
        return target
    pending = _inflight.get(key)
    if pending is None:
        async def run():
            async with _bake_slots:
                if not is_fresh(target, *sources):
                    await asyncio.to_thread(bake)
        pending = asyncio.ensure_future(run())
        _inflight[key] = pending
        pending.add_done_callback(lambda _f: _inflight.pop(key, None))
    await asyncio.shield(pending)
    return target


async def ensure_track_normal(track_id: str) -> Optional[Path]:
    source = settings.ARTWORK_ENRICHED_DIR / f"{track_id}.jpeg"
    target = track_normal_path(track_id)
    return await _ensure(f"track:{track_id}", target, (source,), lambda: bake_from_side_by_side(source, target))


async def ensure_pair_normal(key: str, color_path: Path, depth_path: Path, target: Path) -> Optional[Path]:
    return await _ensure(key, target, (color_path, depth_path), lambda: bake_from_pair(color_path, depth_path, target))
