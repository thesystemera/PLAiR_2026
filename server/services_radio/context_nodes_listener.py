"""Context nodes: the listener (profile, local time, favourites, bans), the conversation and the weather."""
from typing import Optional
from services_radio.conversation_service import get_conversation_history
from services_radio.context_node_registry import node_registry
from services_radio import context_service
from services_radio.dj_prompt_helper_service import wrap_untrusted
from services import log_service
from database.models import User


@node_registry.register(
    "user_basic",
    "User's name and location only",
    cost="low"
)
async def get_user_basic(user: Optional[User] = None, session_id: Optional[str] = None, listener_location=None,
                         **_) -> str:
    listener = listener_location or await context_service.listener_location(user, session_id)
    place = listener.address or listener.place
    if user is None:
        if not place:
            return "Listener: Guest (Unknown Location)"
        if listener.source == "timezone":
            place = f"{place} (approximate, from their device's timezone)"
        header = f"Listener: Guest\nListener Location: {place}"
    else:
        header = f"Listener Location: {place or 'Unknown location'}"

    if not listener.description:
        return header
    return (
        f"{'Listener: Guest' + chr(10) if user is None else ''}"
        f"Listener is around: {wrap_untrusted('google_maps', listener.description)} "
        "(street-level area from their device, via Google Maps; fine to mention the street or neighbourhood "
        "casually, never an exact address)"
    )


@node_registry.register(
    "user_local_time",
    "Current time in user's timezone (HH:MM AM/PM)",
    cost="low"
)
async def get_user_local_time(user: Optional[User] = None, listener_timezone: Optional[str] = None, **_) -> str:
    return context_service.format_user_time_str(user, listener_timezone)


@node_registry.register(
    "user_persona",
    "User's generated personality profile",
    cost="medium"
)
async def get_user_persona(user: Optional[User] = None, **_) -> str:
    if not user or user.persona is None:
        return "LISTENER PERSONA: Guest Listener (Unknown Profile)"

    return f"LISTENER PERSONA:\n{user.persona}"


@node_registry.register(
    "user_profile",
    "User's full profile description",
    cost="high"
)
async def get_user_profile(user: Optional[User] = None, **_) -> str:
    if not user or user.profile is None:
        return "LISTENER PROFILE: Guest Listener (Unknown Profile)"

    return f"LISTENER PROFILE:\n{user.profile}"


@node_registry.register(
    "shoutout_interests",
    "User's shoutout interests and discovery topics",
    cost="free"
)
async def get_shoutout_interests(user: Optional[User] = None, **_) -> str:
    if not user or user.shoutout_interests is None:
        return "LISTENER SHOUTOUT INTERESTS: None yet"

    return f"LISTENER SHOUTOUT INTERESTS:\n{user.shoutout_interests}"


@node_registry.register(
    "user_favorite_artists",
    "User's top 5-7 favorite artists",
    cost="medium"
)
async def get_user_favorite_artists(
    user_id: Optional[int] = None,
    async_session_maker=None,
    catalog_service=None,
    **_
) -> str:
    if not user_id or not async_session_maker or not catalog_service:
        return "LISTENER'S FAVORITE ARTISTS: None (Guest)"

    async with async_session_maker() as db:
        return await context_service.get_user_favorites(user_id, db, catalog_service)


@node_registry.register(
    "user_banned_tracks",
    "Tracks the user has banned",
    cost="low"
)
async def get_user_banned_tracks(
    user_id: Optional[int] = None,
    async_session_maker=None,
    catalog_service=None,
    **_
) -> str:
    if not user_id or not async_session_maker or not catalog_service:
        return "BANNED SONGS: None (Guest)"

    async with async_session_maker() as db:
        return await context_service.get_user_banned(user_id, db, catalog_service)


@node_registry.register(
    "conversation_last_turn",
    "Just the most recent exchange",
    cost="low"
)
async def get_conversation_last_turn(
    user_id: Optional[int] = None,
    session_id: Optional[str] = None,
    async_session_maker=None,
    **_
) -> str:
    try:
        if user_id and async_session_maker:
            async with async_session_maker() as db:
                history = await get_conversation_history(
                    user_id=user_id,
                    db=db,
                    format_type='text',
                    limit=1
                )
            if history:
                return f"LAST EXCHANGE:\n{history}"
            return "LAST EXCHANGE: None"

        elif session_id:
            history = await get_conversation_history(
                temp_user_id=session_id,
                format_type='text',
                limit=1
            )
            if history:
                return f"LAST EXCHANGE:\n{history}"
            return "LAST EXCHANGE: None (Guest - no history yet)"

        return "LAST EXCHANGE: No session"
    except Exception as e:
        log_service.error(f"[NODE] Error getting last conversation: {e}")
        return "LAST EXCHANGE: Error"


@node_registry.register(
    "conversation_recent",
    "Last 3 exchanges",
    cost="medium"
)
async def get_conversation_recent(
    user_id: Optional[int] = None,
    session_id: Optional[str] = None,
    async_session_maker=None,
    **_
) -> str:
    try:
        if user_id and async_session_maker:
            async with async_session_maker() as db:
                history = await get_conversation_history(
                    user_id=user_id,
                    db=db,
                    format_type='text',
                    limit=3
                )
            if history:
                return f"CONVERSATION HISTORY:\n{history}"
            return "CONVERSATION HISTORY: None"

        elif session_id:
            history = await get_conversation_history(
                temp_user_id=session_id,
                format_type='text',
                limit=3
            )
            if history:
                return f"CONVERSATION HISTORY:\n{history}"
            return "CONVERSATION HISTORY: None (Guest - no history yet)"

        return "CONVERSATION HISTORY: No session"
    except Exception as e:
        log_service.error(f"[NODE] Error getting conversation history: {e}")
        return "CONVERSATION HISTORY: Error"


@node_registry.register(
    "weather_current",
    "Current weather condition only",
    cost="low"
)
async def get_weather_current(
    user_id: Optional[int] = None,
    async_session_maker=None,
    listener_location=None,
    dj_service=None,
    **_
) -> str:
    stored = "CURRENT WEATHER: Unknown"
    if user_id and async_session_maker:
        async with async_session_maker() as db:
            stored = await context_service.get_db_weather(user_id, db)
    if not stored.endswith("Unknown"):
        return stored
    web_service = getattr(dj_service, "web_service", None)
    coords = listener_location.coords if listener_location is not None else None
    if coords and web_service is not None:
        try:
            live = await web_service.retrieve_weather_data(coords[0], coords[1], "current")
        except Exception as e:
            log_service.warning(f"[Context] Live weather for the listener failed: {type(e).__name__}: {e}")
            live = None
        if live:
            return f"CURRENT WEATHER: {live}"
    return stored if user_id else "CURRENT WEATHER: Unknown (Guest)"
