import asyncio
from fastapi import APIRouter, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse
from typing import Optional

from services import log_service
from database import User
from service_registry import services
from routers.deps import get_current_user

router = APIRouter()

@router.get("/api/catalog/tracks")
async def get_catalog_tracks(
        skip: int = 0,
        limit: int = 100,
        sort_by: str = "created_at",
        order: str = "desc",
        genre: Optional[str] = None,
        current_user: User = Depends(get_current_user),
        x_guest_id: Optional[str] = Header(None),
):
    from services.listener_filters import excluded_ids
    from security_middleware import is_valid_guest_id
    user_id = int(current_user.id) if current_user else None  # type: ignore
    guest = x_guest_id if x_guest_id and is_valid_guest_id(x_guest_id) else None
    banned_ids = await excluded_ids(user_id, str(user_id) if user_id else guest)

    assert services.catalog_service is not None
    tracks, filtered_total = await asyncio.to_thread(
        services.catalog_service.get_all_tracks,
        skip,
        limit,
        sort_by,
        order,
        genre,
        banned_ids
    )

    for track in tracks:
        track["has_artwork"] = services.catalog_service.has_artwork(track["id"])  # type: ignore

    log_service.debug(
        f"[API] /catalog/tracks: skip={skip}, limit={limit}, genre={genre}, returned={len(tracks)}, total={filtered_total}")

    return {
        "tracks": tracks,
        "total_tracks": filtered_total,
        "skip": skip,
        "limit": limit
    }

@router.get("/api/catalog/stats")
async def get_catalog_stats(genre: Optional[str] = None):
    assert services.catalog_service is not None
    return services.catalog_service.get_stats(genre)

@router.get("/api/user_content/stats")
async def get_user_content_stats(category: Optional[str] = None):
    assert services.user_content_service is not None
    return services.user_content_service.get_stats(category)

@router.get("/api/catalog/genres")
async def get_catalog_genres():
    assert services.catalog_service is not None
    return {"genres": services.catalog_service.get_all_genres()}

@router.get("/api/track/{track_id}")
async def get_track(track_id: str):
    assert services.catalog_service is not None
    track = services.catalog_service.get_track(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    track["has_artwork"] = services.catalog_service.has_artwork(track_id)  # type: ignore
    return track

@router.get("/track/{track_id}", response_class=HTMLResponse)
async def get_track_page(track_id: str):
    assert services.catalog_service is not None
    assert services.opengraph_service is not None
    track = services.catalog_service.get_track(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    gen_params = track.get("generation_params", {})
    derived_tags = track.get("derived_tags", {})

    title = gen_params.get("title", "Unknown Track")
    artist = gen_params.get("artist_name") or derived_tags.get("inspired_artist", "Unknown Artist")
    genre = derived_tags.get("primary_genre")

    html = services.opengraph_service.render_track_page(
        track_id=track_id,
        title=title,
        artist=artist,
        genre=genre,
    )

    return HTMLResponse(content=html)
