import asyncio
import base64
import binascii
import re
import time
from fastapi import APIRouter, HTTPException, Depends, Header
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from services.whisper_dual_service import whisper_dual_service
from services.preferences_service import preferences_service
from services import log_service
from services.user_content_database_service import public_shoutout, public_shoutouts
from database import get_db, User
from config import settings
from service_registry import services
from routers.deps import get_current_user, RateLimit, enforce_rate_limit
from security_middleware import is_valid_guest_id
from services_radio.listener_location import guest_locations
from routers.schemas import ShoutoutSearchRequest, PreferenceRequest, DirectReplyUploadRequest

router = APIRouter()

SHOUTOUT_AUDIO_FILENAME = re.compile(r"[A-Za-z0-9_-]{1,64}\.mp3")

@router.delete("/api/user_content/shoutouts/{shoutout_id}")
async def delete_shoutout(
        shoutout_id: str,
        current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    assert services.user_content_service is not None
    if '_' not in shoutout_id:
        timestamp = shoutout_id.replace('.json', '').replace('.mp3', '')
        shoutout_id = f"{current_user.id}_{timestamp}"
    else:
        parts = shoutout_id.split('_', 1)
        if str(parts[0]) != str(current_user.id):
            raise HTTPException(status_code=403, detail="You can only delete your own shoutouts")

    success = await asyncio.to_thread(services.user_content_service.delete_shoutout, shoutout_id)

    if not success:
        raise HTTPException(status_code=404, detail="Shoutout not found")

    return {"status": "success", "deleted": [shoutout_id]}

@router.get("/api/user_content/shoutouts/audio/{user_id}/{filename}")
async def get_shoutout_audio(
        user_id: int,
        filename: str,
        range_header: Optional[str] = Header(None, alias="range")
):
    assert services.media_streaming_service is not None
    if user_id < 0 or not SHOUTOUT_AUDIO_FILENAME.fullmatch(filename):
        raise HTTPException(status_code=404, detail="Shoutout audio not found")

    user_shoutouts_dir = (settings.USERS_DIR / str(user_id) / "shoutouts").resolve()
    audio_path = (user_shoutouts_dir / filename).resolve()
    if audio_path.parent != user_shoutouts_dir or not audio_path.is_file():
        raise HTTPException(status_code=404, detail="Shoutout audio not found")

    return await services.media_streaming_service.stream_file(
        file_path=audio_path,
        range_header=range_header,
        media_type="audio/mpeg",
        extra_headers={
            "X-Content-Type": "shoutout",
            "X-Audio-Format": "mp3"
        }
    )

@router.get("/api/user_content/shoutouts/{shoutout_id}")
async def get_shoutout(
        shoutout_id: str,
        db: AsyncSession = Depends(get_db)
):
    assert services.user_content_service is not None
    shoutout = await asyncio.to_thread(services.user_content_service.get_enriched_shoutout, shoutout_id)
    if not shoutout:
        raise HTTPException(status_code=404, detail="Shoutout not found")

    enriched = await services.user_content_service.enrich_shoutout_results([shoutout], db)  # type: ignore
    return public_shoutout(enriched[0] if enriched else shoutout)

@router.post("/api/user_content/shoutouts/search")
async def search_shoutouts(
        request: ShoutoutSearchRequest,
        current_user: Optional[User] = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
        _rate_limit=Depends(RateLimit("search")),
        x_guest_id: Optional[str] = Header(None)
):
    assert services.user_content_vector_search_service is not None
    assert services.user_content_service is not None
    user_id = int(current_user.id) if current_user else "guest"  # type: ignore
    use_ai_analysis = bool(request.use_ai_analysis) and current_user is not None
    if use_ai_analysis:
        enforce_rate_limit("search_ai_user", f"user:{user_id}")


    try:
        user_location = None
        if current_user and hasattr(current_user, 'latitude') and hasattr(current_user, 'longitude'):
            lat = current_user.latitude
            lon = current_user.longitude
            if lat is not None and lon is not None:
                user_location = (float(str(lat)), float(str(lon)))
        elif current_user is None and x_guest_id and is_valid_guest_id(x_guest_id):
            guest = guest_locations.get(x_guest_id)
            user_location = guest.coords if guest is not None else None

        results = await services.user_content_vector_search_service.search(
            query=request.query,
            n_results=request.n_results or 20,
            content_type='shoutout',
            user_location=user_location,
            use_ai_analysis=use_ai_analysis
        )

        results = public_shoutouts(await services.user_content_service.enrich_shoutout_results(results, db))  # type: ignore

        who = log_service.who(user_id=current_user.id) if current_user else (x_guest_id or "guest")[:14]
        log_service.listener(f"{who}: searched shoutouts for \"{request.query}\" -> {len(results)} result(s)")

        return {
            'results': results,
            'count': len(results)
        }

    except Exception as e:
        log_service.error(f"Shoutout search error: {str(e)}")
        import traceback
        log_service.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Search failed")

@router.get("/api/user_content/shoutouts/{shoutout_id}/replies")
async def get_shoutout_replies(
        shoutout_id: str,
        sort_by: str = "popularity",
        db: AsyncSession = Depends(get_db)
):
    assert services.user_content_service is not None
    parent = services.user_content_service.get_shoutout(shoutout_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Shoutout not found")

    if not await asyncio.to_thread(services.user_content_service.is_root_shoutout, shoutout_id):
        raise HTTPException(status_code=400, detail="Cannot get replies of a reply")

    replies = await asyncio.to_thread(services.user_content_service.get_replies, shoutout_id, sort_by)

    if replies:
        replies = await services.user_content_service.enrich_shoutout_results(replies, db)  # type: ignore

    return {
        "replies": public_shoutouts(replies),
        "count": len(replies),
        "parent_id": shoutout_id
    }

@router.post("/api/user_content/shoutouts/{parent_id}/reply")
async def create_shoutout_reply(
        parent_id: str,
        current_user: User = Depends(get_current_user),
        _rate_limit=Depends(RateLimit("transcribe"))
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    assert services.user_content_service is not None
    parent = services.user_content_service.get_shoutout(parent_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Parent shoutout not found")

    if not await asyncio.to_thread(services.user_content_service.is_root_shoutout, parent_id):
        raise HTTPException(status_code=400, detail="Cannot reply to a reply - only root shoutouts can receive replies")

    assert services.websocket_service is not None
    success, transcription = await services.user_content_service.process_shoutout_upload(
        user_id=int(current_user.id),  # type: ignore
        enhancement_service=services.user_content_speech_enhancement_service,
        gemini_service=services.ai_service,
        vector_db_service=None,
        broadcast_callback=services.websocket_service.broadcast_content_updated,
        parent_id=parent_id
    )

    if not success:
        raise HTTPException(status_code=400, detail="Failed to process reply. Make sure you've recorded audio first.")

    log_service.listener(f"{log_service.who(user_id=current_user.id)}: replied to shoutout {parent_id}")

    return {
        "status": "success",
        "parent_id": parent_id,
        "transcription": transcription
    }

@router.post("/api/user_content/shoutouts/{parent_id}/reply/upload")
async def upload_shoutout_reply(
        parent_id: str,
        request: DirectReplyUploadRequest,
        current_user: User = Depends(get_current_user),
        _rate_limit=Depends(RateLimit("transcribe"))
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    assert services.user_content_service is not None
    parent = services.user_content_service.get_shoutout(parent_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Parent shoutout not found")

    if not await asyncio.to_thread(services.user_content_service.is_root_shoutout, parent_id):
        raise HTTPException(status_code=400, detail="Cannot reply to a reply - only root shoutouts can receive replies")

    try:
        audio_bytes = base64.b64decode(request.audio, validate=True)
    except (binascii.Error, ValueError) as e:
        log_service.error(f"Failed to decode audio: {e}")
        raise HTTPException(status_code=400, detail="Invalid audio data")

    timestamp = str(int(time.time()))

    webm_path = await services.user_content_service.save_audio_file(int(current_user.id), timestamp, audio_bytes)  # type: ignore
    if not webm_path:
        raise HTTPException(status_code=500, detail="Failed to save audio file")

    transcription_result = None
    try:
        transcription_result = await whisper_dual_service.transcribe_quality(audio_bytes)
        full_transcription = transcription_result.get("text", "").strip() if transcription_result else ""
        words = transcription_result.get("words", []) if transcription_result else []
        duration = transcription_result.get("duration", 0) if transcription_result else 0
    except Exception as e:
        log_service.error(f"Transcription failed: {e}")
        full_transcription = ""
        words = []
        duration = 0

    from datetime import datetime, timezone
    metadata = {
        "full_transcription": full_transcription,
        "word_level_transcription": words,
        "transcription_metadata": {
            "language": transcription_result.get("language", "en") if transcription_result else "en",
            "language_probability": transcription_result.get("language_probability", 1.0) if transcription_result else 1.0,
            "duration": duration
        },
        "user_data": {
            "user_id": int(current_user.id),  # type: ignore
            "username": current_user.username,
            "location": current_user.location if hasattr(current_user, 'location') else "Unknown",
            "latitude": float(str(current_user.latitude)) if hasattr(current_user, 'latitude') and current_user.latitude is not None else None,
            "longitude": float(str(current_user.longitude)) if hasattr(current_user, 'longitude') and current_user.longitude is not None else None,
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    await services.user_content_service.save_metadata_file(int(current_user.id), timestamp, metadata)  # type: ignore

    assert services.websocket_service is not None
    success, transcription = await services.user_content_service.process_shoutout_upload(
        user_id=int(current_user.id),  # type: ignore
        enhancement_service=services.user_content_speech_enhancement_service,
        gemini_service=services.ai_service,
        vector_db_service=None,
        broadcast_callback=services.websocket_service.broadcast_content_updated,
        parent_id=parent_id,
        webm_path=webm_path
    )

    if not success:
        raise HTTPException(status_code=400, detail="Failed to process reply audio")

    log_service.listener(f"{log_service.who(user_id=current_user.id)}: uploaded a voice reply to shoutout {parent_id}")

    return {
        "status": "success",
        "parent_id": parent_id,
        "transcription": transcription
    }

@router.post("/api/shoutouts/{shoutout_id}/preference")
async def set_shoutout_preference(
        shoutout_id: str,
        request: PreferenceRequest,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        assert preferences_service is not None
        assert services.websocket_service is not None
        result = await preferences_service.set_shoutout_preference(
            int(current_user.id),  # type: ignore
            shoutout_id,
            request.preference_type,
            db,
            broadcast_callback=services.websocket_service.broadcast_preference_change
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/api/shoutouts/{shoutout_id}/preference")
async def remove_shoutout_preference(
        shoutout_id: str,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    assert preferences_service is not None
    assert services.websocket_service is not None
    result = await preferences_service.remove_shoutout_preference(
        int(current_user.id),  # type: ignore
        shoutout_id,
        db,
        broadcast_callback=services.websocket_service.broadcast_preference_change
    )
    return result

@router.get("/api/user/shoutout-preferences")
async def get_user_shoutout_preferences(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    assert preferences_service is not None
    assert services.user_content_service is not None
    return await preferences_service.get_enriched_shoutout_preferences(
        int(current_user.id),  # type: ignore
        services.user_content_service,
        db
    )
