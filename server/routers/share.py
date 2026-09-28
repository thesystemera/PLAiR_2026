import aiofiles
import html
import json
import re
import secrets
import time
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, File, UploadFile, Form
from fastapi.responses import FileResponse, HTMLResponse
from typing import Optional

from services import log_service
from database import User
from config import settings
from service_registry import services
from routers.deps import get_current_user

router = APIRouter()

SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MP4_SIGNATURE_OFFSET = 4
MP4_HEADER_BYTES = 12

def _is_safe_id(value: str) -> bool:
    return bool(value) and bool(SAFE_ID_PATTERN.match(value))

def _share_video_dir() -> Path:
    path = settings.CATALOG_DIR / "share_videos"
    path.mkdir(parents=True, exist_ok=True)
    return path

def _share_video_paths(share_id: str) -> tuple[Path, Path]:
    if not _is_safe_id(share_id):
        raise HTTPException(status_code=404, detail="Share video not found")
    base_dir = _share_video_dir()
    return base_dir / f"{share_id}.mp4", base_dir / f"{share_id}.json"

def _public_origin() -> str:
    return settings.PUBLIC_BASE_URL

def _share_page_csp() -> str:
    origin = _public_origin()
    return (
        f"default-src 'none'; media-src 'self' {origin}; img-src 'self' {origin}; style-src 'unsafe-inline'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )

def _track_exists(track_id: str) -> bool:
    return bool(services.catalog_service and services.catalog_service.get_track(track_id))

@router.post("/api/share/video")
async def upload_share_video(
        file: UploadFile = File(...),
        track_id: str = Form(..., max_length=128),
        title: Optional[str] = Form(None, max_length=300),
        artist: Optional[str] = Form(None, max_length=300),
        current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not _is_safe_id(track_id) or not _track_exists(track_id):
        raise HTTPException(status_code=400, detail="Invalid track")

    content_type = (file.content_type or "").lower()
    filename = file.filename or "share.mp4"
    if content_type and content_type != "video/mp4":
        raise HTTPException(status_code=400, detail="Share video must be MP4")
    if not filename.lower().endswith(".mp4"):
        raise HTTPException(status_code=400, detail="Share video must be MP4")

    share_id = f"{int(time.time())}_{secrets.token_urlsafe(8)}"
    video_path, metadata_path = _share_video_paths(share_id)
    max_size = settings.MAX_SHARE_VIDEO_BYTES
    size = 0
    header = b""

    try:
        async with aiofiles.open(video_path, "wb") as out_file:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                if len(header) < MP4_HEADER_BYTES:
                    header += chunk[:MP4_HEADER_BYTES - len(header)]
                    if len(header) >= MP4_HEADER_BYTES and header[MP4_SIGNATURE_OFFSET:MP4_SIGNATURE_OFFSET + 4] != b"ftyp":
                        raise HTTPException(status_code=400, detail="Share video must be MP4")
                size += len(chunk)
                if size > max_size:
                    raise HTTPException(status_code=413, detail="Share video is too large")
                await out_file.write(chunk)

        if size < 1024:
            raise HTTPException(status_code=400, detail="Share video is empty")

        metadata = {
            "share_id": share_id,
            "track_id": track_id,
            "title": title or "PLAiR Track",
            "artist": artist or "Unknown Artist",
            "user_id": int(current_user.id),  # type: ignore
            "created_at": int(time.time()),
            "size": size
        }
        async with aiofiles.open(metadata_path, "w", encoding="utf-8") as meta_file:
            await meta_file.write(json.dumps(metadata, indent=2))

        log_service.upload(
            f"[ShareVideo] Saved share MP4: id={share_id}, track={track_id}, size={size / (1024 * 1024):.1f}MB"
        )
        origin = _public_origin()
        return {
            "share_id": share_id,
            "page_url": f"{origin}/api/share/video/{share_id}/page",
            "mp4_url": f"{origin}/api/share/video/{share_id}.mp4",
            "size": size
        }
    except HTTPException:
        if video_path.exists():
            video_path.unlink(missing_ok=True)
        if metadata_path.exists():
            metadata_path.unlink(missing_ok=True)
        raise
    except Exception as e:
        if video_path.exists():
            video_path.unlink(missing_ok=True)
        if metadata_path.exists():
            metadata_path.unlink(missing_ok=True)
        log_service.error(f"Share video upload failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to save share video")

@router.get("/api/share/video/{share_id}.mp4")
@router.get("/share/video/{share_id}.mp4")
async def get_shared_video_file(share_id: str):
    video_path, _ = _share_video_paths(share_id)
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Share video not found")
    return FileResponse(video_path, media_type="video/mp4", headers={"X-Content-Type-Options": "nosniff"})

@router.get("/api/share/video/{share_id}/page", response_class=HTMLResponse)
@router.get("/share/video/{share_id}", response_class=HTMLResponse)
async def get_shared_video_page(share_id: str):
    video_path, metadata_path = _share_video_paths(share_id)
    if not video_path.exists() or not metadata_path.exists():
        raise HTTPException(status_code=404, detail="Share video not found")

    try:
        async with aiofiles.open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.loads(await f.read())
    except Exception:
        metadata = {}

    def esc(value: str) -> str:
        return html.escape(value, quote=True)

    title = str(metadata.get("title") or "PLAiR Track")
    artist = str(metadata.get("artist") or "Unknown Artist")
    track_id = str(metadata.get("track_id") or "")
    safe_track_id = track_id if _is_safe_id(track_id) else ""
    origin = _public_origin()
    mp4_url = esc(f"{origin}/api/share/video/{share_id}.mp4")
    artwork_url = esc(f"{origin}/api/artwork/{safe_track_id}") if safe_track_id else ""
    home_url = esc(f"{origin}/")
    escaped_title = esc(title)
    escaped_artist = esc(artist)
    image_meta = f'\n  <meta property="og:image" content="{artwork_url}">' if artwork_url else ""
    poster_attr = f' poster="{artwork_url}"' if artwork_url else ""

    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title} - PLAiR Video</title>
  <meta property="og:type" content="video.other">
  <meta property="og:title" content="{escaped_title}">
  <meta property="og:description" content="Watch {escaped_title} by {escaped_artist} on PLAiR.">
  <meta property="og:video" content="{mp4_url}">
  <meta property="og:video:secure_url" content="{mp4_url}">
  <meta property="og:video:type" content="video/mp4">{image_meta}
  <style>
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #05070a; color: white; font-family: system-ui, sans-serif; }}
    main {{ width: min(420px, 92vw); }}
    video {{ width: 100%; border-radius: 8px; background: black; }}
    h1 {{ font-size: 20px; margin: 16px 0 4px; }}
    p {{ color: #a7adb8; margin: 0 0 16px; }}
    a {{ color: white; }}
  </style>
</head>
<body>
  <main>
    <video src="{mp4_url}" controls playsinline{poster_attr}></video>
    <h1>{escaped_title}</h1>
    <p>{escaped_artist}</p>
    <a href="{home_url}">Open PLAiR</a>
  </main>
</body>
</html>"""
    return HTMLResponse(
        content=page,
        headers={
            "Content-Security-Policy": _share_page_csp(),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin-when-cross-origin"
        }
    )
