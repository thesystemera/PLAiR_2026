import asyncio
import base64
import binascii
import re
import time
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Header
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from services.whisper_dual_service import whisper_dual_service
from services.preferences_service import preferences_service
from services import log_service
from services.user_content_database_service import (public_shoutout, public_shoutouts, track_ref,
                                                     KIND_SHOUTOUT, KIND_REPLY, KIND_REVIEW)
from services.community_engagement import community_engagement
from database import get_db, User
from config import settings
from service_registry import services
from routers.deps import get_current_user, RateLimit, enforce_rate_limit
from security_middleware import is_valid_guest_id
from services_radio import listener_location as location_resolver
from routers.schemas import ShoutoutSearchRequest, PreferenceRequest, DirectReplyUploadRequest, CommunityTextRequest

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
        guest_id = x_guest_id if current_user is None and x_guest_id and is_valid_guest_id(x_guest_id) else None
        user_location = None
        if current_user is not None or guest_id:
            user_location = (await location_resolver.resolve(current_user, guest_id, geocode=False)).coords

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

def _user_data(user: User) -> dict:
    return {
        "user_id": int(user.id),  # type: ignore
        "username": user.username,
        "location": getattr(user, "location", None) or "Unknown",
        "latitude": float(str(user.latitude)) if getattr(user, "latitude", None) is not None else None,
        "longitude": float(str(user.longitude)) if getattr(user, "longitude", None) is not None else None,
    }


async def _ingest_recording(user: User, audio_b64: str):
    try:
        audio_bytes = base64.b64decode(audio_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Invalid audio data")

    assert services.user_content_service is not None
    timestamp = str(int(time.time()))
    webm_path = await services.user_content_service.save_audio_file(int(user.id), timestamp, audio_bytes)  # type: ignore
    if not webm_path:
        raise HTTPException(status_code=500, detail="Failed to save audio file")

    result = await whisper_dual_service.transcribe_quality(audio_bytes)
    if not result or not (result.get("text") or "").strip():
        raise HTTPException(status_code=400, detail="Couldn't hear any words in that recording")

    metadata = {
        "full_transcription": result["text"].strip(),
        "word_level_transcription": result.get("words", []),
        "transcription_metadata": {
            "language": result.get("language", "en"),
            "language_probability": result.get("language_probability", 1.0),
            "duration": result.get("duration", 0),
        },
        "user_data": _user_data(user),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await services.user_content_service.save_metadata_file(int(user.id), timestamp, metadata)  # type: ignore
    return webm_path


async def _save_item(user: User, kind: str, *, audio: Optional[str] = None, text: Optional[str] = None,
                     parent_id: Optional[str] = None, track: Optional[dict] = None) -> dict:
    content = services.user_content_service
    assert content is not None and services.websocket_service is not None
    common = dict(enhancement_service=services.user_content_speech_enhancement_service,
                  ai_service=services.ai_service, vector_db_service=services.user_content_vector_db_service,
                  broadcast_callback=services.websocket_service.broadcast_content_updated,
                  parent_id=parent_id, track=track)
    if audio is not None:
        webm_path = await _ingest_recording(user, audio)
        item = await content.create_voice_item(int(user.id), kind, webm_path, **common)  # type: ignore
    else:
        item = await content.create_text_item(int(user.id), kind, text or "", _user_data(user), **common)  # type: ignore
    if not item:
        raise HTTPException(status_code=400, detail=f"Couldn't save that {kind}")
    target = f" to {parent_id}" if parent_id else (f" on {track.get('title')}" if track else "")
    log_service.listener(
        f"{log_service.who(user_id=user.id)}: {'recorded' if audio is not None else 'typed'} a {kind}{target}")
    return {"status": "success", "id": item["id"], "kind": kind, "transcription": item.get("full_transcription", "")}


def _require_parent(parent_id: str):
    assert services.user_content_service is not None
    problem = services.user_content_service.parent_problem(parent_id)
    if problem:
        raise HTTPException(status_code=404 if parent_id not in services.user_content_service.shoutouts else 400,
                            detail=problem)


def _require_track(track_id: str) -> dict:
    assert services.catalog_service is not None
    reference = track_ref(services.catalog_service.get_track(track_id))
    if not reference:
        raise HTTPException(status_code=404, detail="Track not found")
    return reference


@router.get("/api/user_content/shoutouts/{shoutout_id}/replies")
async def get_shoutout_replies(
        shoutout_id: str,
        sort_by: str = "popularity",
        db: AsyncSession = Depends(get_db)
):
    _require_parent(shoutout_id)
    assert services.user_content_service is not None
    replies = services.user_content_service.get_replies(shoutout_id)
    if sort_by == "popularity":
        replies = await community_engagement.rank(replies)
    if replies:
        replies = await services.user_content_service.enrich_shoutout_results(replies, db)  # type: ignore

    return {
        "replies": public_shoutouts(replies),
        "count": len(replies),
        "parent_id": shoutout_id
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
    _require_parent(parent_id)
    return {**await _save_item(current_user, KIND_REPLY, audio=request.audio, parent_id=parent_id),
            "parent_id": parent_id}


@router.post("/api/user_content/shoutouts/{parent_id}/reply/text")
async def type_shoutout_reply(
        parent_id: str,
        request: CommunityTextRequest,
        current_user: User = Depends(get_current_user),
        _rate_limit=Depends(RateLimit("dj_talk"))
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    _require_parent(parent_id)
    return {**await _save_item(current_user, KIND_REPLY, text=request.text, parent_id=parent_id),
            "parent_id": parent_id}


@router.get("/api/user/community")
async def get_my_community_items(
        current_user: User = Depends(get_current_user)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    assert services.user_content_service is not None
    mine = services.user_content_service.items_by_user(int(current_user.id))  # type: ignore
    return {"shoutouts": public_shoutouts(mine[KIND_SHOUTOUT]), "replies": public_shoutouts(mine[KIND_REPLY]),
            "reviews": public_shoutouts(mine[KIND_REVIEW])}


@router.get("/api/tracks/{track_id}/reviews")
async def get_track_reviews(
        track_id: str,
        db: AsyncSession = Depends(get_db)
):
    _require_track(track_id)
    assert services.user_content_service is not None
    reviews = await community_engagement.rank(services.user_content_service.reviews_for_track(track_id))
    if reviews:
        reviews = await services.user_content_service.enrich_shoutout_results(reviews, db)  # type: ignore
    return {"reviews": public_shoutouts(reviews), "count": len(reviews), "track_id": track_id}


@router.post("/api/tracks/{track_id}/reviews/upload")
async def upload_track_review(
        track_id: str,
        request: DirectReplyUploadRequest,
        current_user: User = Depends(get_current_user),
        _rate_limit=Depends(RateLimit("transcribe"))
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return await _save_item(current_user, KIND_REVIEW, audio=request.audio, track=_require_track(track_id))


@router.post("/api/tracks/{track_id}/reviews/text")
async def type_track_review(
        track_id: str,
        request: CommunityTextRequest,
        current_user: User = Depends(get_current_user),
        _rate_limit=Depends(RateLimit("dj_talk"))
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return await _save_item(current_user, KIND_REVIEW, text=request.text, track=_require_track(track_id))

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
