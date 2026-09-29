import aiofiles
import hashlib
import json
import time
import uuid
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, File, UploadFile, Form
from typing import Optional

from services.track_artwork_service import track_artwork_service
from services import artist_profile_service as artists
from services import log_service
from database import User
from config import settings
from service_registry import services
from routers.deps import get_session_info, get_current_user, RateLimit, read_upload_limited

router = APIRouter()


async def _remember_enhance(user_id: int, enabled: bool):
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is not None:
            user.upload_enhance = enabled
            await db.commit()

@router.post("/api/user/music/upload")
async def upload_user_music(
        file: UploadFile = File(...),
        title: Optional[str] = Form(None, max_length=120),
        artist_profile_id: Optional[int] = Form(None),
        enable_upscaling: Optional[bool] = Form(None),
        upload_id: Optional[str] = Form(None, max_length=64),
        current_user: User = Depends(get_current_user),
        session: dict = Depends(get_session_info),
        _rate_limit=Depends(RateLimit("ai_upload"))
):

    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not services.human_music_upload_service:
        raise HTTPException(status_code=503, detail="Upload service not available")

    session_id = session["session_id"]
    if not upload_id or not all(ch.isalnum() or ch in "-_" for ch in upload_id) or len(upload_id) < 8:
        upload_id = uuid.uuid4().hex

    async def progress_callback(event: dict):
        assert services.websocket_service is not None
        await services.websocket_service.broadcast_to_session(session_id, {
            "type": "upload_progress",
            "data": event
        })

    filename = file.filename or "upload.mp3"
    content_type = file.content_type

    assert services.human_music_upload_service is not None
    suffix = Path(filename).suffix.lower()
    if suffix not in services.human_music_upload_service.supported_formats:
        supported = ", ".join(sorted(services.human_music_upload_service.supported_formats))
        raise HTTPException(status_code=400, detail=f"Unsupported format: {suffix}. Supported: {supported}")

    is_video_upload = services.human_music_upload_service._is_video_upload(filename)
    max_upload_size = services.human_music_upload_service.max_upload_size_for(filename)

    staging_dir = settings.USERS_DIR / "_upload_staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_path = staging_dir / f"{current_user.id}_{int(time.time() * 1000)}{suffix}.upload"
    upload_size = 0
    digest = hashlib.sha256()

    try:
        async with aiofiles.open(staging_path, "wb") as out_file:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break

                upload_size += len(chunk)
                if upload_size > max_upload_size:
                    max_mb = max_upload_size / (1024 * 1024)
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Maximum size: {max_mb / 1024:.0f}GB for video uploads"
                        if is_video_upload
                        else f"File too large. Maximum size: {max_mb:.0f}MB for audio uploads"
                    )

                digest.update(chunk)
                await out_file.write(chunk)
    except HTTPException:
        if staging_path.exists():
            staging_path.unlink()
        raise
    except Exception as e:
        if staging_path.exists():
            staging_path.unlink()
        log_service.error(f"Upload read failed for user {current_user.id}: {e}")
        raise HTTPException(status_code=400, detail="Failed to read uploaded file")

    if upload_size < 1024:
        if staging_path.exists():
            staging_path.unlink()
        raise HTTPException(status_code=400, detail="File too small to be valid media")

    if enable_upscaling is None:
        enable_upscaling = bool(getattr(current_user, "upload_enhance", False))
    elif enable_upscaling != bool(getattr(current_user, "upload_enhance", False)):
        await _remember_enhance(int(current_user.id), enable_upscaling)  # type: ignore
    artist = await artists.resolve_for_upload(current_user, artist_profile_id)

    upload_size_mb = upload_size / (1024 * 1024)
    log_service.upload(
        f"[Upload] Received file: user={current_user.id}, "
        f"name='{filename}', type='{content_type or 'unknown'}', "
        f"size={upload_size_mb:.1f}MB"
    )

    try:
        success, message, metadata = await services.human_music_upload_service.process_upload(
            user_id=int(current_user.id),  # type: ignore
            filename=filename,
            upload_path=staging_path,
            file_size=upload_size,
            content_sha256=digest.hexdigest(),
            upload_id=upload_id,
            user_title=title,
            artist=artist,
            content_type=content_type,
            enable_upscaling=enable_upscaling,
            progress_callback=progress_callback
        )
    finally:
        if staging_path.exists():
            staging_path.unlink()

    if not success:
        raise HTTPException(status_code=400, detail=message)

    assert metadata is not None
    source_quality = metadata.get("source_quality", {})
    quality_tier = source_quality.get("quality_tier", "unknown") if source_quality else "unknown"

    audio_features = {}
    if metadata.get("audio_features_extracted"):
        features_path = settings.AUDIOFEATURES_DIR / f"{metadata.get('id')}.json"
        if features_path.exists():
            try:
                async with aiofiles.open(features_path, 'r') as f:
                    audio_features = json.loads(await f.read())
            except (json.JSONDecodeError, IOError):
                pass

    track_id = str(metadata.get("id")) if metadata.get("id") else ""
    return {
        "status": "success",
        "message": message,
        "track_id": track_id,
        "upload_id": upload_id,
        "duplicate": bool(metadata.get("duplicate_upload")),
        "metadata": {
            "title": metadata.get("generation_params", {}).get("title"),
            "artist": metadata.get("generation_params", {}).get("artist_name") or metadata.get("track_info", {}).get("artist"),
            "artist_profile_id": metadata.get("artist_profile_id"),
            "enhance_requested": bool(metadata.get("enhance_requested")),
            "embedded_tags": metadata.get("embedded_tags") or {},
            "style": metadata.get("generation_params", {}).get("style"),
            "primary_genre": metadata.get("derived_tags", {}).get("primary_genre"),
            "secondary_genres": metadata.get("derived_tags", {}).get("secondary_genres", []),
            "mood_keywords": metadata.get("derived_tags", {}).get("mood_keywords", []),
            "similar_artists": metadata.get("derived_tags", {}).get("similar_artists", []),
            "vocal_style_keywords": metadata.get("derived_tags", {}).get("vocal_style_keywords", []),
            "duration_ms": metadata.get("track_info", {}).get("duration"),
            "has_lyrics": metadata.get("transcribed_lyrics") is not None,
            "transcribed_lyrics": metadata.get("transcribed_lyrics"),
            "lyrical_interpretation": metadata.get("derived_tags", {}).get("lyrical_interpretation"),
            "has_artwork": services.catalog_service.has_artwork(track_id) if services.catalog_service else False,
            "artwork_generated": metadata.get("artwork_generated", False),
            "artwork_enriched": metadata.get("artwork_enriched", False),
            "artwork_prompt": metadata.get("artwork_prompt"),
            "source_quality": {
                "tier": quality_tier,
                "sample_rate": source_quality.get("sample_rate"),
                "bit_depth": source_quality.get("bit_depth"),
                "is_lossless": source_quality.get("is_lossless"),
                "bandwidth_utilization": source_quality.get("bandwidth_utilization"),
                "processing_notes": source_quality.get("processing_notes", "")
            } if source_quality else None,
            "audio_features": {
                "tempo": audio_features.get("tempo"),
                "key": audio_features.get("key"),
                "mode": audio_features.get("mode"),
                "energy": audio_features.get("energy"),
                "danceability": audio_features.get("danceability"),
            } if audio_features else None,
            "mastering_applied": metadata.get("mastering_applied", False),
            "mastering_blend": metadata.get("mastering_blend", 70),
            "mastering_blend_used": metadata.get("mastering_blend_used"),
            "enhancement_applied": metadata.get("enhancement_applied", False),
            "mix_analysis": metadata.get("mix_analysis"),
            "sonic_master_prompt": metadata.get("sonic_master_prompt"),
            "sonic_master_blend": metadata.get("sonic_master_blend", 0),
            "sonic_master_applied": metadata.get("sonic_master_applied", False),
            "sonic_master_blend_used": metadata.get("sonic_master_blend_used"),
            "video_search_terms": metadata.get("video_search_terms") or metadata.get("derived_tags", {}).get("video_search_terms", []),
            "source_media_type": metadata.get("source_media_type", "audio"),
            "audio_extracted_from_video": metadata.get("audio_extracted_from_video", False),
            "artwork_generation_deferred": metadata.get("artwork_generation_deferred", False),
        }
    }

@router.get("/api/user/music/tracks")
async def get_user_tracks(
        skip: int = 0,
        limit: int = 50,
        current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not services.human_music_upload_service:
        raise HTTPException(status_code=503, detail="Upload service not available")

    assert services.human_music_upload_service is not None
    tracks = await services.human_music_upload_service.get_user_tracks(
        user_id=int(current_user.id),  # type: ignore
        skip=skip,
        limit=limit
    )

    return {
        "tracks": tracks,
        "count": len(tracks)
    }

@router.put("/api/user/music/tracks/{track_id}")
async def update_user_track(
        track_id: str,
        updates: dict,
        current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not services.human_music_upload_service:
        raise HTTPException(status_code=503, detail="Upload service not available")

    artist = None
    if updates.get("artist_profile_id") is not None:
        try:
            artist = await artists.get(int(updates["artist_profile_id"]))
        except (TypeError, ValueError):
            artist = None
        if artist is None or artist["owner_user_id"] != current_user.id:
            raise HTTPException(status_code=400, detail="Pick one of your own artists")

    success, message = await services.human_music_upload_service.update_track_metadata(
        user_id=int(current_user.id),  # type: ignore
        track_id=track_id,
        updates=updates,
        artist=artist
    )

    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {"status": "success", "message": message}

@router.delete("/api/user/music/tracks/{track_id}")
async def delete_user_track(
        track_id: str,
        current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not services.human_music_upload_service:
        raise HTTPException(status_code=503, detail="Upload service not available")

    success, message = await services.human_music_upload_service.delete_user_track(
        user_id=int(current_user.id),  # type: ignore
        track_id=track_id
    )

    if not success:
        raise HTTPException(status_code=400, detail=message)

    return {"status": "success", "message": message}

@router.post("/api/user/music/tracks/{track_id}/artwork")
async def upload_track_artwork(
        track_id: str,
        file: UploadFile = File(...),
        current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        contents = await read_upload_limited(file, settings.MAX_ARTWORK_UPLOAD_BYTES)
        result = await track_artwork_service.upload_artwork(
            track_id=track_id,
            user_id=int(current_user.id),  # type: ignore
            file_contents=contents,
            original_filename=file.filename or "artwork.jpg",
            enrich=True
        )
        return result
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log_service.error(f"Artwork upload error: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload artwork")

@router.delete("/api/user/music/tracks/{track_id}/artwork")
async def delete_track_artwork(
        track_id: str,
        current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        result = await track_artwork_service.delete_artwork(
            track_id=track_id,
            user_id=int(current_user.id)  # type: ignore
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        log_service.error(f"Artwork delete error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete artwork")

@router.post("/api/user/music/tracks/{track_id}/artwork/generate")
async def generate_track_artwork(
        track_id: str,
        current_user: User = Depends(get_current_user),
        _rate_limit=Depends(RateLimit("ai_upload"))
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        result = await track_artwork_service.generate_artwork(track_id, int(current_user.id))  # type: ignore
        return result
    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg.lower():
            raise HTTPException(status_code=404, detail=error_msg)
        elif "only generate artwork for your own" in error_msg.lower():
            raise HTTPException(status_code=403, detail=error_msg)
        elif "already has artwork" in error_msg.lower():
            raise HTTPException(status_code=400, detail=error_msg)
        raise HTTPException(status_code=400, detail=error_msg)
    except Exception as e:
        log_service.error(f"Artwork generation error: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate artwork")
