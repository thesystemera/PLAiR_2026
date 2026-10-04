from typing import Dict, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from routers.deps import get_session_info
from service_registry import services
from services import log_service
from services.device_settings_service import pick_valid, playing_setting, settings_for, stored_for, with_changes
from services.user_data_cache_service import user_data_cache

router = APIRouter()


class SettingsUpdate(BaseModel):
    settings: Dict[str, Union[bool, str]]


def _require(session: dict):
    if not session["user"]:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not session["device_kind"]:
        raise HTTPException(status_code=400, detail="Missing X-Device-Kind header")
    return session["user"], session["device_kind"]


@router.get("/api/settings")
async def get_settings(session: dict = Depends(get_session_info)):
    user, kind = _require(session)
    return {"kind": kind, "settings": settings_for(user, kind), "stored": stored_for(user, kind) is not None}


@router.put("/api/settings")
async def save_settings(update: SettingsUpdate, session: dict = Depends(get_session_info),
                        db: AsyncSession = Depends(get_db)):
    user, kind = _require(session)
    changes = pick_valid(update.settings)
    if changes:
        user.device_settings = with_changes(user, kind, changes)
        await db.commit()
        await user_data_cache.invalidate_user(int(user.id))
        log_service.api(f"Settings for {log_service.who(session['session_id'], session['device_id'])} ({kind}): "
                        + ", ".join(f"{key}={value}" for key, value in changes.items()))
        if services.websocket_service is not None:
            await services.websocket_service.broadcast_to_session(
                session["session_id"], {"type": "device_settings_updated", "data": {"kind": kind, "settings": changes}})
        if "ttsMuted" in changes and services.radio_mode_service is not None:
            services.radio_mode_service.note_tts_muted(
                session["session_id"], bool(playing_setting(user, session["session_id"], "ttsMuted")))
    return {"kind": kind, "settings": settings_for(user, kind), "stored": True}
