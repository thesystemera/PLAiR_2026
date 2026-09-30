from typing import Optional, Set

from services.user_data_cache_service import user_data_cache

MUSIC_SOURCES = ("both", "human", "ai")
SEARCH_SCOPES = ("catalog", "favourites", "super_likes")


def music_source(session_id: Optional[str]) -> str:
    from service_registry import services
    radio = services.radio_mode_service
    return radio.music_source(session_id) if radio is not None and session_id else "both"


def source_excluded_ids(source: str) -> Set[str]:
    from service_registry import services
    catalog = services.catalog_service
    if catalog is None or source == "both":
        return set()
    if source == "human":
        return set(catalog.tracks) - catalog.human_ids
    return set(catalog.human_ids)


async def scope_ids(user_id: Optional[int], within: Optional[str]) -> Optional[Set[str]]:
    if within not in ("favourites", "super_likes"):
        return None
    prefs = await user_data_cache.get_preferences(user_id)
    liked = set(prefs.get("super_likes") or ())
    return liked if within == "super_likes" else liked | set(prefs.get("likes") or ())


async def excluded_ids(user_id: Optional[int], session_id: Optional[str]) -> Set[str]:
    from service_registry import services
    bans = await user_data_cache.get_banned_ids(user_id) if user_id else set()
    hidden = getattr(services.catalog_service, "hidden_ids", set())
    session = session_id or (str(user_id) if user_id else None)
    return set(bans) | set(hidden) | source_excluded_ids(music_source(session))
