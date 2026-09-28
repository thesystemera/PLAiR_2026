from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from services.preferences_service import preferences_service
from database import get_db, User
from service_registry import services
from routers.deps import get_current_user
from routers.schemas import PreferenceRequest

router = APIRouter()

@router.post("/api/tracks/{track_id}/preference")
async def set_track_preference(
        track_id: str,
        request: PreferenceRequest,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        assert preferences_service is not None
        assert services.websocket_service is not None
        result = await preferences_service.set_track_preference(
            int(current_user.id),  # type: ignore
            track_id,
            request.preference_type,
            db,
            playback_service=services.playback_service,  # type: ignore
            broadcast_callback=services.websocket_service.broadcast_preference_change
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/api/tracks/{track_id}/preference")
async def remove_track_preference(
        track_id: str,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    assert preferences_service is not None
    assert services.websocket_service is not None
    result = await preferences_service.remove_track_preference(
        int(current_user.id),  # type: ignore
        track_id,
        db,
        playback_service=services.playback_service,  # type: ignore
        broadcast_callback=services.websocket_service.broadcast_preference_change
    )
    return result

@router.get("/api/user/preferences")
async def get_user_preferences(
        current_user: User = Depends(get_current_user),
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    assert preferences_service is not None
    return await preferences_service.get_enriched_track_preferences(
        int(current_user.id),  # type: ignore
        services.catalog_service
    )
