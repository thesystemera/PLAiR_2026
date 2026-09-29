import asyncio
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
from services.task_utils import spawn
from database import User
from config import settings
from service_registry import services
from routers.deps import get_session_info, get_current_user, RateLimit, read_upload_limited

router = APIRouter()
UPLOAD_JOBS: dict = {}
UPLOAD_JOB_TTL_S = 3600


async def _remember_rights(user_id: int):
    from datetime import datetime, timezone
    from database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is not None and user.upload_rights_confirmed_at is None:
            user.upload_rights_confirmed_at = datetime.now(timezone.utc)
            await db.commit()


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
        rights_confirmed: bool = Form(False),
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

    if getattr(current_user, "upload_rights_confirmed_at", None) is None:
        if not rights_confirmed:
            staging_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="Please confirm you own this music or have the rights to share it")
        await _remember_rights(int(current_user.id))  # type: ignore

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

    job = {"upload_id": upload_id, "user_id": int(current_user.id), "filename": filename, "status": "running",  # type: ignore
           "stage": "Queued", "percent": 0, "started_at": time.time(), "finished_at": None, "result": None,
           "error": None, "task": None}
    _prune_jobs()
    UPLOAD_JOBS[upload_id] = job
    job["task"] = spawn(_run_upload_job(job, session_id, staging_path, dict(
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
    )), name=f"upload:{upload_id}")
    return {"status": "processing", "upload_id": upload_id}


def _job_view(job: dict) -> dict:
    return {k: v for k, v in job.items() if k not in ("task", "user_id")}


def _prune_jobs():
    now = time.time()
    for key, job in list(UPLOAD_JOBS.items()):
        if job["finished_at"] and now - job["finished_at"] > UPLOAD_JOB_TTL_S:
            UPLOAD_JOBS.pop(key, None)


async def _run_upload_job(job: dict, session_id: str, staging_path: Path, upload_args: dict):
    final_event: dict = {}

    async def announce(event: dict):
        if services.websocket_service is not None:
            await services.websocket_service.broadcast_to_session(session_id, {"type": "upload_progress", "data": event})

    async def progress_callback(event: dict):
        job["stage"], job["percent"] = event.get("stage", job["stage"]), event.get("percent", job["percent"])
        if event.get("status") in ("done", "failed"):
            final_event.update(event)
            return
        await announce(event)

    try:
        assert services.human_music_upload_service is not None
        success, message, metadata = await services.human_music_upload_service.process_upload(
            progress_callback=progress_callback, **upload_args)
        if success and metadata is not None:
            job["result"] = await _upload_result(message, metadata, job["upload_id"])
            job["status"] = "done"
        else:
            job["status"], job["error"] = "failed", message
        await announce({**final_event, "upload_id": job["upload_id"], "status": job["status"],
                        "message": message, "track_id": (job["result"] or {}).get("track_id")})
    except asyncio.CancelledError:
        job["status"], job["error"] = "cancelled", "Upload cancelled"
        if services.websocket_service is not None:
            await services.websocket_service.broadcast_to_session(session_id, {"type": "upload_progress", "data": {
                "upload_id": job["upload_id"], "stage": "Cancelled", "percent": job["percent"], "status": "cancelled"}})
    except Exception as e:
        log_service.error(f"[Upload] Job {job['upload_id']} crashed: {e}")
        job["status"], job["error"] = "failed", "Upload processing failed unexpectedly - please try again"
    finally:
        job["finished_at"] = time.time()
        staging_path.unlink(missing_ok=True)


@router.get("/api/user/music/uploads")
async def list_upload_jobs(current_user: User = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    _prune_jobs()
    mine = [_job_view(j) for j in UPLOAD_JOBS.values() if j["user_id"] == current_user.id]
    return {"uploads": sorted(mine, key=lambda j: j["started_at"], reverse=True)}


@router.get("/api/user/music/uploads/{upload_id}")
async def get_upload_job(upload_id: str, current_user: User = Depends(get_current_user)):
    job = UPLOAD_JOBS.get(upload_id)
    if not current_user or job is None or job["user_id"] != current_user.id:
        raise HTTPException(status_code=404, detail="Upload not found")
    return _job_view(job)


@router.delete("/api/user/music/uploads/{upload_id}")
async def cancel_upload_job(upload_id: str, current_user: User = Depends(get_current_user)):
    job = UPLOAD_JOBS.get(upload_id)
    if not current_user or job is None or job["user_id"] != current_user.id:
        raise HTTPException(status_code=404, detail="Upload not found")
    task = job.get("task")
    if job["status"] == "running" and task is not None and not task.done():
        task.cancel()
        log_service.upload(f"[Upload] {log_service.who(user_id=current_user.id)} cancelled {upload_id}")
    return {"status": "cancelling" if job["status"] == "running" else job["status"], "upload_id": upload_id}


async def _upload_result(message: str, metadata: dict, upload_id: str) -> dict:
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
            "visibility": metadata.get("visibility", "public"),
            "explicit": bool(metadata.get("explicit")),
            "ai_assisted": bool(metadata.get("ai_assisted")),
            "style": metadata.get("generation_params", {}).get("style"),
            "primary_genre": metadata.get("derived_tags", {}).get("primary_genre"),
            "secondary_genres": metadata.get("derived_tags", {}).get("secondary_genres", []),
            "mood_keywords": metadata.get("derived_tags", {}).get("mood_keywords", []),
            "similar_artists": metadata.get("derived_tags", {}).get("similar_artists", []),
            "vocal_style_keywords": metadata.get("derived_tags", {}).get("vocal_style_keywords", []),
            "duration_ms": metadata.get("track_info", {}).get("duration"),
            "has_lyrics": bool(metadata.get("transcribed_lyrics")),
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
