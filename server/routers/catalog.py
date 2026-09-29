import asyncio
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import HTMLResponse
from typing import Optional

from services.user_data_cache_service import user_data_cache
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
        human: bool = False,
        current_user: User = Depends(get_current_user),
):
    banned_ids = None
    if current_user:
        banned_ids = await user_data_cache.get_banned_ids(int(current_user.id))  # type: ignore

    assert services.catalog_service is not None
    tracks, filtered_total = await asyncio.to_thread(
        services.catalog_service.get_all_tracks,
        skip,
        limit,
        sort_by,
        order,
        genre,
        banned_ids,
        human
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
