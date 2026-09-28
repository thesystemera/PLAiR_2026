from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from database import get_db, User
from routers.deps import get_current_user
from service_registry import services
from services.preferences_service import preferences_service
from services_radio import radio_segments
from services_radio.music_beds import music_beds
from services_radio.radio_schedule import RadioPrefs, feature_intervals

router = APIRouter()


def _options() -> Dict[str, Any]:
    return {
        "feature_intervals_min": list(feature_intervals()),
        "stings_outside_radio_mode": bool(settings.STINGS_ENABLED and settings.STINGS_OUTSIDE_RADIO_MODE),
        "segments": [
            {"kind": segment.kind, "label": segment.label, "pref": segment.pref,
             "clock_minutes": list(segment.clock_minutes or [])}
            for segment in radio_segments.segments()
        ],
    }


@router.get("/api/radio-mode")
async def get_radio_mode(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not current_user:
        return {"settings": RadioPrefs().to_dict(), "persisted": False, "options": _options()}
    prefs = await preferences_service.get_radio_settings(int(current_user.id), db)
    return {"settings": prefs, "persisted": True, "options": _options()}


@router.put("/api/radio-mode")
async def update_radio_mode(
        updates: Dict[str, Any] = Body(...),
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    prefs = await preferences_service.set_radio_settings(int(current_user.id), updates, db)
    if services.radio_mode_service is not None:
        await services.radio_mode_service.set_user_prefs(
            int(current_user.id), prefs, getattr(current_user, "timezone", None),
            bool(getattr(current_user, "tts_muted", False)))
    return {"settings": prefs, "persisted": True, "options": _options()}


@router.get("/api/music-beds/{bed_id}")
async def get_music_bed(bed_id: str, format: Optional[str] = Query(None, pattern="^(mp3|opus)$")):
    bed = music_beds.get(bed_id)
    if bed is None:
        raise HTTPException(status_code=404, detail="Music bed not found")
    path, media_type = bed.file_for(format)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Music bed not found")
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})
