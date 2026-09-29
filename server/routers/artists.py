from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from database import User
from routers.deps import get_current_user
from service_registry import services
from services import artist_profile_service as artists

router = APIRouter()


class ArtistRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=artists.NAME_MAX)
    bio: Optional[str] = Field(None, max_length=artists.BIO_MAX)
    links: Optional[List[str]] = Field(None, max_length=artists.LINKS_MAX)


def _require_user(user: Optional[User]) -> User:
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _tracks_for_profile(profile_id: int) -> list:
    catalog = services.catalog_service
    if catalog is None:
        return []
    return [t for t in catalog.tracks.values() if t.get("artist_profile_id") == profile_id]


@router.get("/api/user/music/setup")
async def upload_setup(current_user: User = Depends(get_current_user)):
    user = _require_user(current_user)
    mine = await artists.list_for_user(int(user.id))  # type: ignore
    for profile in mine:
        profile["track_count"] = len(_tracks_for_profile(profile["id"]))
    return {"artists": mine, "last_artist_profile_id": getattr(user, "last_artist_profile_id", None),
            "upload_enhance": bool(getattr(user, "upload_enhance", False))}


@router.post("/api/artists")
async def create_artist(request: ArtistRequest, current_user: User = Depends(get_current_user)):
    user = _require_user(current_user)
    try:
        return await artists.create(int(user.id), request.name or "", request.bio or "", request.links)  # type: ignore
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/api/artists/{profile_id}")
async def update_artist(profile_id: int, request: ArtistRequest, current_user: User = Depends(get_current_user)):
    user = _require_user(current_user)
    try:
        before = await artists.get(profile_id)
        profile = await artists.update(int(user.id), profile_id, request.model_dump(exclude_none=True))  # type: ignore
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    retagged = 0
    if before and before["name"] != profile["name"] and services.human_music_upload_service is not None:
        retagged = await services.human_music_upload_service.retag_artist(profile)
    return {**profile, "tracks_updated": retagged}


@router.delete("/api/artists/{profile_id}")
async def delete_artist(profile_id: int, current_user: User = Depends(get_current_user)):
    user = _require_user(current_user)
    count = len(_tracks_for_profile(profile_id))
    if count:
        raise HTTPException(status_code=400,
                            detail=f"This artist still has {count} track{'s' if count != 1 else ''}. "
                                   "Move or delete them first.")
    try:
        await artists.delete(int(user.id), profile_id)  # type: ignore
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "success"}


@router.get("/api/artists/{slug}")
async def get_artist_page(slug: str):
    profile = await artists.get_by_slug(slug)
    if profile is None:
        raise HTTPException(status_code=404, detail="Artist not found")
    tracks = sorted(_tracks_for_profile(profile["id"]), key=lambda t: t.get("created_at", ""), reverse=True)
    public = {k: v for k, v in profile.items() if k != "owner_user_id"}
    return {**public, "tracks": [
        {"id": t.get("id"), "title": (t.get("generation_params") or {}).get("title"),
         "genre": (t.get("derived_tags") or {}).get("primary_genre"), "created_at": t.get("created_at"),
         "has_artwork": services.catalog_service.has_artwork(t.get("id")) if services.catalog_service else False}
        for t in tracks if t.get("visibility", "public") == "public"
    ]}
