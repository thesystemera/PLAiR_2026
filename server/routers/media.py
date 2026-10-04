import aiofiles
import asyncio
import json
import os
import re
import threading
from collections import OrderedDict
from fastapi import APIRouter, HTTPException, Depends, Header, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from typing import Optional

from services.youtube_clip_service import get_youtube_clip_service
from services.artwork_thumbnail_service import THUMBNAIL_SIZES, ensure_thumbnail
from services.normal_map_service import ensure_track_normal
from services.device_settings_service import settings_for, valid_kind
from services import log_service
from database import User
from config import settings
from service_registry import services
from routers.deps import get_cached_current_user, require_admin

ALLOWED_STREAM_BITRATES = {"128k", "192k", "256k"}

SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
SAFE_CLIP_NAME = re.compile(r"[A-Za-z0-9_-]{1,160}\.mp4")

router = APIRouter()

STREAM_PURPOSES = {"play": "playing", "download": "downloading for offline"}
RANGE_START = re.compile(r"bytes=(\d+)-")
JSON_FILE_CACHE_MAX = 32
_json_file_lock = threading.Lock()
_json_file_cache: "OrderedDict[tuple, bytes]" = OrderedDict()


def _render_json_file(path) -> bytes:
    stat = os.stat(path)
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _json_file_lock:
        body = _json_file_cache.get(key)
        if body is not None:
            _json_file_cache.move_to_end(key)
            return body
    with open(path, 'r', encoding='utf-8') as f:
        body = JSONResponse(json.load(f)).body
    with _json_file_lock:
        _json_file_cache[key] = body
        while len(_json_file_cache) > JSON_FILE_CACHE_MAX:
            _json_file_cache.popitem(last=False)
    return body


async def _json_file_response(path, missing_detail: str) -> Response:
    try:
        body = await asyncio.to_thread(_render_json_file, path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=missing_detail)
    return Response(content=body, media_type="application/json")


def _listener_label(request: Request, current_user: Optional[User]) -> str:
    device_id = request.query_params.get("device_id") or None
    if current_user:
        log_service.remember_user(current_user.id, current_user.username)
        return log_service.who(user_id=current_user.id, device_id=device_id)
    session_id = services.websocket_service.session_for_device(device_id) if services.websocket_service and device_id else None
    return log_service.who(session_id or request.query_params.get("guest_id") or "unknown", device_id)


def _log_stream_start(request: Request, track_id: str, bitrate: str, range_header: Optional[str],
                      current_user: Optional[User]):
    if request.method == "HEAD":
        return
    match = RANGE_START.match(range_header or "")
    if match and int(match.group(1)) > 0:
        return
    purpose = STREAM_PURPOSES.get(request.query_params.get("purpose") or "play", "playing")
    track = services.catalog_service.get_track(track_id) if services.catalog_service else None
    params = (track or {}).get("generation_params", {})
    title = f"'{params.get('title', track_id)}' by {params.get('artist_name') or 'unknown'}"
    log_service.playback(f"[STREAM] {_listener_label(request, current_user)} is {purpose} {title} ({bitrate})")

@router.get("/api/stream/{track_id}")
async def stream_track(
        track_id: str,
        range_header: Optional[str] = Header(None, alias="range")
):
    assert services.catalog_service is not None
    assert services.media_streaming_service is not None
    audio_path = services.catalog_service.get_audio_path(track_id)
    if audio_path is None:
        raise HTTPException(status_code=404, detail="Audio file not found")

    return await services.media_streaming_service.stream_file(
        file_path=audio_path,
        range_header=range_header,
        media_type="audio/mpeg",
        extra_headers={
            "X-Audio-Format": "mp3",
            "X-Audio-Bitrate": "192k"
        }
    )

@router.get("/api/stream/{track_id}/opus")
async def stream_opus_track(
        track_id: str,
        range_header: Optional[str] = Header(None, alias="range"),
        bitrate: Optional[str] = None,
        current_user: Optional[User] = Depends(get_cached_current_user),
):
    assert services.media_streaming_service is not None
    if bitrate not in ALLOWED_STREAM_BITRATES:
        bitrate = services.media_streaming_service.resolve_bitrate(current_user)

    if current_user:
        log_service.api(f"Streaming Opus {track_id} → {bitrate} for {current_user.username}")
    else:
        log_service.api(f"Streaming Opus {track_id} → {bitrate} for guest")

    assert services.catalog_service is not None
    assert services.transcoding_service is not None
    mp3_path = services.catalog_service.get_audio_path(track_id)
    if mp3_path is None:
        raise HTTPException(status_code=404, detail="Audio file not found")
    wav_path = settings.WAV_DIR / f"{track_id}.wav"

    opus_path = await services.transcoding_service.get_or_create_opus(
        track_id=track_id,
        wav_path=wav_path if await asyncio.to_thread(wav_path.exists) else None,
        mp3_path=mp3_path,
        bitrate=bitrate
    )

    if not opus_path or not opus_path.exists():
        log_service.error(f"Failed to get Opus file at {bitrate} for {track_id}")
        raise HTTPException(status_code=404, detail="Audio file not available")

    return await services.media_streaming_service.stream_file(  # type: ignore
        file_path=opus_path,
        range_header=range_header,
        media_type="audio/opus",
        extra_headers={
            "X-Audio-Format": "opus",
            "X-Audio-Bitrate": bitrate
        }
    )

@router.api_route("/api/stream/{track_id}/webm", methods=["GET", "HEAD"])
async def stream_webm_track(
        request: Request,
        track_id: str,
        range_header: Optional[str] = Header(None, alias="range"),
        bitrate: Optional[str] = None,
        current_user: Optional[User] = Depends(get_cached_current_user),
):
    assert services.media_streaming_service is not None
    if bitrate not in ALLOWED_STREAM_BITRATES:
        bitrate = services.media_streaming_service.resolve_bitrate(current_user)

    _log_stream_start(request, track_id, bitrate, range_header, current_user)

    assert services.catalog_service is not None
    assert services.transcoding_service is not None
    mp3_path = services.catalog_service.get_audio_path(track_id)
    if mp3_path is None:
        raise HTTPException(status_code=404, detail="Audio file not found")
    wav_path = settings.WAV_DIR / f"{track_id}.wav"

    webm_path = await services.transcoding_service.get_or_create_webm(
        track_id=track_id,
        wav_path=wav_path if await asyncio.to_thread(wav_path.exists) else None,
        mp3_path=mp3_path,
        bitrate=bitrate
    )

    if not webm_path or not webm_path.exists():
        log_service.error(f"Failed to get WebM file at {bitrate} for {track_id}")
        raise HTTPException(status_code=404, detail="Audio file not available")

    if request.method == "HEAD":
        file_size = webm_path.stat().st_size
        return Response(
            content=None,
            headers={
                "Content-Length": str(file_size),
                "Content-Type": "audio/webm",
                "Accept-Ranges": "bytes",
                "X-Audio-Format": "webm",
                "X-Audio-Bitrate": bitrate
            }
        )

    return await services.media_streaming_service.stream_file(  # type: ignore
        file_path=webm_path,
        range_header=range_header,
        media_type="audio/webm",
        extra_headers={
            "X-Audio-Format": "webm",
            "X-Audio-Bitrate": bitrate
        }
    )

@router.get("/api/artwork/{track_id}")
async def get_artwork(track_id: str):
    assert services.catalog_service is not None
    artwork_path = services.catalog_service.get_artwork_path(track_id)

    if not artwork_path or not artwork_path.exists():
        raise HTTPException(status_code=404, detail="Artwork not found")

    return FileResponse(
        artwork_path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=31536000"
        }
    )

@router.get("/api/artwork/{track_id}/thumb/{size}")
async def get_artwork_thumbnail(track_id: str, size: int):
    if not SAFE_ID.fullmatch(track_id) or size not in THUMBNAIL_SIZES:
        raise HTTPException(status_code=404, detail="Artwork not found")
    assert services.catalog_service is not None
    artwork_path = services.catalog_service.get_artwork_path(track_id)
    if not artwork_path or not artwork_path.exists():
        raise HTTPException(status_code=404, detail="Artwork not found")

    try:
        thumb_path = await ensure_thumbnail(track_id, artwork_path, size)
    except Exception as e:
        log_service.error(f"Artwork thumbnail failed for {track_id} ({size}px): {e}")
        thumb_path = artwork_path

    return FileResponse(
        thumb_path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=31536000"
        }
    )

@router.get("/api/artwork/{track_id}/depth/thumb/{size}")
async def get_artwork_depth_thumbnail(track_id: str, size: int):
    if not SAFE_ID.fullmatch(track_id) or size not in THUMBNAIL_SIZES:
        raise HTTPException(status_code=404, detail="Depth map not found")
    enriched_path = settings.ARTWORK_ENRICHED_DIR / f"{track_id}.jpeg"
    if not enriched_path.exists():
        raise HTTPException(status_code=404, detail="Depth map not found")

    try:
        thumb_path = await ensure_thumbnail(track_id, enriched_path, size, variant="depth")
    except Exception as e:
        log_service.error(f"Depth thumbnail failed for {track_id} ({size}px): {e}")
        raise HTTPException(status_code=404, detail="Depth map not found")

    return FileResponse(
        thumb_path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=31536000"
        }
    )

@router.get("/api/artwork/{track_id}/normal")
async def get_artwork_normal_map(track_id: str):
    if not SAFE_ID.fullmatch(track_id):
        raise HTTPException(status_code=404, detail="Normal map not found")
    normal_path = await ensure_track_normal(track_id)
    if not normal_path:
        raise HTTPException(status_code=404, detail="Normal map not found")
    return FileResponse(normal_path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000"})

@router.get("/api/artwork/{track_id}/normal/thumb/{size}")
async def get_artwork_normal_thumbnail(track_id: str, size: int):
    if not SAFE_ID.fullmatch(track_id) or size not in THUMBNAIL_SIZES:
        raise HTTPException(status_code=404, detail="Normal map not found")
    normal_path = await ensure_track_normal(track_id)
    if not normal_path:
        raise HTTPException(status_code=404, detail="Normal map not found")
    thumb_path = await ensure_thumbnail(track_id, normal_path, size, variant="normal")
    return FileResponse(thumb_path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000"})

@router.api_route("/api/artwork/{track_id}/enriched", methods=["GET", "HEAD"])
async def get_enriched_artwork(track_id: str):
    assert services.catalog_service is not None
    enriched_path = settings.ARTWORK_ENRICHED_DIR / f"{track_id}.jpeg"

    if not enriched_path.exists():
        artwork_path = services.catalog_service.get_artwork_path(track_id)  # type: ignore
        if not artwork_path or not artwork_path.exists():
            raise HTTPException(status_code=404, detail="Artwork not found")
        enriched_path = artwork_path

    return FileResponse(
        enriched_path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=31536000",
            "X-Artwork-Type": "enriched" if enriched_path.parent.name == "artwork_enriched" else "standard"
        }
    )

@router.get("/api/audio-features/{track_id}")
async def get_audio_features(track_id: str):
    if not SAFE_ID.fullmatch(track_id):
        raise HTTPException(status_code=404, detail="Audio features not found")
    return await _json_file_response(settings.AUDIOFEATURES_DIR / f"{track_id}.json", "Audio features not found")

@router.get("/api/video-clips/{track_id}")
async def get_video_clips(track_id: str, current_user: Optional[User] = Depends(get_cached_current_user),
                          x_device_kind: Optional[str] = Header(None)):
    if not SAFE_ID.fullmatch(track_id):
        raise HTTPException(status_code=400, detail="Invalid track id")
    if not current_user or not settings_for(current_user, valid_kind(x_device_kind))["videoClipsEnabled"]:
        return {"clips": [], "keywords": [], "reason": "video_clips_disabled"}
    youtube_service = get_youtube_clip_service()
    return await youtube_service.get_clips_for_track(track_id)  # type: ignore

@router.get("/api/video-clip-file/{filename}")
async def get_video_clip_file(filename: str):
    if not SAFE_CLIP_NAME.fullmatch(filename):
        raise HTTPException(status_code=404, detail="Clip not found")
    clip_path = settings.YOUTUBE_CLIPS_DIR / filename

    if not clip_path.exists():
        raise HTTPException(status_code=404, detail="Clip not found")

    return FileResponse(clip_path, media_type="video/mp4")

@router.get("/api/lyric-timestamps/{track_id}")
async def get_lyric_timestamps(track_id: str):
    if not SAFE_ID.fullmatch(track_id):
        raise HTTPException(status_code=404, detail="Lyric timestamps not found")
    return await _json_file_response(settings.LYRIC_TIMESTAMPS_DIR / f"{track_id}.json", "Lyric timestamps not found")

@router.post("/api/lyric-timestamps/{track_id}/generate")
async def generate_lyric_timestamps(track_id: str, _admin: User = Depends(require_admin)):
    assert services.catalog_service is not None
    assert services.orchestrator is not None
    track = services.catalog_service.get_track(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    metadata_path = settings.METADATA_DIR / f"{track_id}.json"
    if not metadata_path.exists():
        raise HTTPException(status_code=404, detail="Track metadata not found")

    async with aiofiles.open(metadata_path, 'r', encoding='utf-8') as f:
        metadata = json.loads(await f.read())

    lyrics = metadata.get("generation_params", {}).get("prompt", "")
    if not lyrics:
        raise HTTPException(status_code=400, detail="Track has no lyrics")

    mp3_path = settings.AUDIO_DIR / f"{track_id}.mp3"
    if not mp3_path.exists():
        raise HTTPException(status_code=404, detail="MP3 file not found")

    log_service.api(f"Generating lyric timestamps for {track_id} via orchestrator")

    result = await services.orchestrator.lyrics.generate_timestamps(  # type: ignore
        track_id=track_id,
        audio_path=mp3_path,
        metadata=metadata,
        save=True
    )

    if not result:
        raise HTTPException(status_code=500, detail="Failed to generate lyric timestamps")

    return {
        "status": "success",
        "track_id": track_id,
        "timestamps": result
    }
