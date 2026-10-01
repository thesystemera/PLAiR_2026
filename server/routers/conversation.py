from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from database import get_db
from services import listener_timeline
from services_radio.conversation_service import get_conversation_history, save_conversation_to_database, save_temp_conversation
from service_registry import services
from routers.deps import get_session_info

router = APIRouter()

TIMELINE_MAX_ENTRIES = 150


@router.get("/api/timeline")
async def get_timeline_endpoint(session: dict = Depends(get_session_info), hours: float = 6, limit: int = 100,
                                kinds: Optional[str] = None):
    user = session["user"]
    minutes = max(1.0, min(hours * 60, float(settings.DJ_TIMELINE_MAX_MINUTES)))
    span = listener_timeline.window(datetime.now(timezone.utc), from_minutes=minutes)
    wanted = [kind for kind in (kinds or "").split(",") if kind in listener_timeline.TIMELINE_KINDS]
    entries, not_shown = await listener_timeline.timeline(
        user.id if user else None, session["session_id"], kinds=wanted or listener_timeline.TIMELINE_KINDS, span=span,
        limit=max(1, min(limit, TIMELINE_MAX_ENTRIES)))
    return {
        "entries": [{"id": entry.id, "kind": entry.kind, "label": entry.label, "at": entry.at.isoformat(),
                     "ended": entry.ended.isoformat(), "outcome": entry.outcome} for entry in entries],
        "hours": round(minutes / 60, 2),
        "not_shown": not_shown,
    }


@router.get("/api/timeline/entry")
async def get_timeline_entry_endpoint(id: str, session: dict = Depends(get_session_info)):
    user = session["user"]
    detail = await listener_timeline.talk_detail(user.id if user else None, session["session_id"], id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Not found")
    return detail

@router.get("/api/conversation")
async def get_conversation_history_endpoint(
        session: dict = Depends(get_session_info),
        db: AsyncSession = Depends(get_db),
        format_type: str = 'json',
        limit: int = 10
):
    user = session["user"]
    user_id = user.id if user else None
    session_id = session["session_id"]

    temp_user_id = None if user else session_id

    history = await get_conversation_history(
        user_id=user_id,
        temp_user_id=temp_user_id,
        db=db if user else None,
        format_type=format_type,
        limit=limit
    )

    emoji = services.tts_generation_service.paralanguage_emoji if services.tts_generation_service else None
    if emoji and format_type == 'json':
        for item in history:
            if item.get("type") == "bot":
                item["content"] = await emoji.render(item["content"])

    return {
        "conversations": history if format_type == 'json' else [],
        "text": history if format_type == 'text' else "",
        "count": len(history) if format_type == 'json' else 0
    }

@router.post("/api/conversation")
async def save_conversation_endpoint(
        user_input: Optional[str] = None,
        bot_response: Optional[str] = None,
        commands: Optional[str] = None,
        info: Optional[str] = None,
        warning: Optional[str] = None,
        error: Optional[str] = None,
        audio_file_path: Optional[str] = None,
        message_type: str = 'interactive',
        session: dict = Depends(get_session_info),
        db: AsyncSession = Depends(get_db)
):
    user = session["user"]
    user_id = user.id if user else None
    session_id = session["session_id"]

    if user_id:
        conversation = await save_conversation_to_database(
            user_id=user_id,
            db=db,
            user_input=user_input,
            bot_response=bot_response,
            commands=commands,
            info=info,
            warning=warning,
            error=error,
            audio_file_path=audio_file_path,
            message_type=message_type
        )

        history = await get_conversation_history(user_id=user_id, db=db, format_type='json', limit=1)
        if history:
            assert services.websocket_service is not None
            await services.websocket_service.broadcast_to_session(session_id, {
                "type": "conversation_update",
                "data": {"latest": history[-1] if history else None}
            })

        return {"status": "success", "id": conversation.id if conversation else None}
    else:
        if user_input and bot_response:
            save_temp_conversation(session_id, user_input, bot_response)

        return {"status": "success", "id": None, "note": "Saved to temp storage"}
