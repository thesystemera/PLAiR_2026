import re

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Literal, Optional

from services.analytics_service import analytics_service
from database import User
from service_registry import services
from routers.deps import RateLimit, get_session_info
from security_middleware import is_valid_guest_id
from services.rate_limit_service import rate_limit_service
from config import settings

router = APIRouter()

SHOUTOUT_ID_PATTERN = re.compile(r"\d{1,12}_\d{1,20}")

@router.get("/api/analytics/top-hits")
async def get_top_hits(period: str = "all", limit: int = 50):
    assert services.catalog_service is not None
    if period not in ["all", "week", "day"]:
        raise HTTPException(status_code=400, detail="Invalid period. Use 'all', 'week', or 'day'")
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="Limit must be between 1 and 200")

    hidden = services.catalog_service.hidden_ids
    hits = await analytics_service.get_top_hits(period=period, limit=min(200, limit + len(hidden)))

    enriched_hits = []
    for hit in hits:
        if hit["track_id"] in hidden or len(enriched_hits) >= limit:
            continue
        track = services.catalog_service.get_track(hit["track_id"])  # type: ignore
        if track:
            enriched_hits.append({
                **hit,
                "track": track
            })

    return {"top_hits": enriched_hits, "period": period, "count": len(enriched_hits)}

@router.get("/api/analytics/track/{track_id}")
async def get_track_analytics(track_id: str):
    stats = await analytics_service.get_track_stats(track_id)
    if not stats:
        return {
            "track_id": track_id,
            "total_plays": 0,
            "unique_listeners": 0,
            "likes": 0,
            "superlikes": 0,
            "bans": 0,
            "popularity_score": 0
        }
    return stats

@router.get("/api/analytics/shoutout/{shoutout_id}")
async def get_shoutout_analytics(shoutout_id: str):
    stats = await analytics_service.get_shoutout_stats(shoutout_id)
    if not stats:
        return {
            "shoutout_id": shoutout_id,
            "total_plays": 0,
            "unique_listeners": 0,
            "likes": 0,
            "superlikes": 0,
            "bans": 0,
            "popularity_score": 0
        }
    return stats

class ShoutoutPlayRequest(BaseModel):
    shoutout_id: str = Field(..., min_length=1, max_length=200)
    event_type: Literal["play", "complete", "skip"] = "play"
    duration_ms: Optional[int] = Field(None, ge=0, le=24 * 60 * 60 * 1000)
    completion_pct: Optional[float] = Field(None, ge=0, le=100)
    skip_reason: Optional[str] = Field(None, max_length=100)

@router.post("/api/analytics/shoutout/play")
async def log_shoutout_play(
        request: ShoutoutPlayRequest,
        current_user: Optional[User] = Depends(RateLimit("shoutout_play")),
        session_info: dict = Depends(get_session_info)
):
    session_id = session_info["session_id"]
    if current_user is None and not is_valid_guest_id(session_id):
        raise HTTPException(status_code=403, detail="Invalid guest id")

    assert services.user_content_service is not None
    if not SHOUTOUT_ID_PATTERN.fullmatch(request.shoutout_id) or not services.user_content_service.get_shoutout(request.shoutout_id):
        raise HTTPException(status_code=404, detail="Shoutout not found")

    dedupe_key = f"shoutout_play:{session_id}:{request.shoutout_id}:{request.event_type}"
    if not rate_limit_service.claim_once(dedupe_key, settings.SHOUTOUT_PLAY_DEDUPE_HOURS * 3600):
        return {"status": "duplicate", "shoutout_id": request.shoutout_id}

    await analytics_service.log_play_event(
        user_id=int(current_user.id) if current_user is not None else None,  # type: ignore
        track_id=request.shoutout_id,
        session_id=session_id,
        device_id=session_info.get("device_id"),
        event_type=request.event_type,
        skip_reason=request.skip_reason,
        duration_ms=request.duration_ms,
        completion_pct=request.completion_pct
    )

    return {"status": "logged", "shoutout_id": request.shoutout_id}
