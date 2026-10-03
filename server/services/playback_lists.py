import random
from typing import Dict, List, Optional, Set

from services import listener_plays, log_service
from services.analytics_service import analytics_service
from services.catalog_aspects import aspects_for

LIST_MODES = frozenset([
    "favorites", "discovery",
    "top_hits_all", "top_hits_week", "top_hits_day",
])


def is_list_mode(mode: str) -> bool:
    return mode in LIST_MODES


class ListFills:
    """The listener's own lists and the station's charts: favorites (likes and super likes, weighted by listening),
    discovery (half favorites, half new songs near them - the mode that drifts) and the top hits."""

    def __init__(self, catalog_service, vector_search_service):
        self.catalog = catalog_service
        self.vector_search = vector_search_service

    async def fill(self, mode: str, needed: int, prefs: Dict, existing_ids: Set[str], session_id: str,
                   user_id: Optional[int]) -> List[Dict]:
        if mode.startswith("top_hits_"):
            return await self._top_hits(mode, needed, prefs["bans"], existing_ids, session_id)
        if mode == "favorites":
            return await self._favorites(needed, prefs, existing_ids, session_id, user_id)
        if mode == "discovery":
            return await self._discovery(needed, prefs, existing_ids, session_id, user_id)
        return []

    async def _top_hits(self, mode: str, needed: int, banned_ids: Set[str], existing_ids: Set[str],
                        session_id: str) -> List[Dict]:
        period = mode.replace("top_hits_", "")

        try:
            top_hits = await analytics_service.get_top_hits(
                period=period, limit=needed + len(existing_ids) + len(banned_ids) + 10,
            )
        except Exception as e:
            log_service.error(f"{log_service.who(session_id)}: failed to fetch {period} top hits: {e}")
            return []

        if not top_hits:
            log_service.throttled(f"no_top_hits:{period}", f"No {period} top hits available - queues fall back to other sources")
            return []

        new_tracks = []
        for hit in top_hits:
            if len(new_tracks) >= needed:
                break
            tid = hit["track_id"]
            if tid not in existing_ids and tid not in banned_ids:
                track = self.catalog.get_track(tid)
                if track:
                    new_tracks.append(track)
                    existing_ids.add(tid)

        random.shuffle(new_tracks)
        log_service.detail(f"{log_service.who(session_id)}: added {len(new_tracks)} tracks from {period} top hits", "playback")
        return new_tracks

    async def _favorites(self, needed: int, prefs: Dict, existing_ids: Set[str], session_id: str,
                         user_id: Optional[int]) -> List[Dict]:
        loved = await listener_plays.loved_tracks(user_id, session_id)
        weights = {item.track_id: item.weight for item in loved
                   if item.track_id not in existing_ids and item.track_id not in prefs["bans"]
                   and self.catalog and self.catalog.get_track(item.track_id)}

        new_tracks = []
        for chosen_id in listener_plays.weighted_pick(weights, needed):
            new_tracks.append(self.catalog.get_track(chosen_id))
            existing_ids.add(chosen_id)
        log_service.detail(f"{log_service.who(session_id)}: favorites added {len(new_tracks)}/{needed} tracks", "playback")
        return new_tracks

    async def _discovery(self, needed: int, prefs: Dict, existing_ids: Set[str], session_id: str,
                         user_id: Optional[int]) -> List[Dict]:
        loved = set(prefs["likes"]) | set(prefs["super_likes"])
        target_favs = needed // 2 + (needed % 2 if random.random() < 0.5 else 0)
        target_discovery = needed - target_favs

        favorites_added = await self._favorites(target_favs, prefs, existing_ids, session_id, user_id)

        if len(favorites_added) < target_favs:
            target_discovery += target_favs - len(favorites_added)
            if target_discovery > 0:
                log_service.detail(f"{log_service.who(session_id)}: favorites exhausted, filling with discovery", "playback")

        discovery_added = []
        if target_discovery > 0 and self.vector_search and loved:
            seeds = [self.catalog.get_track(tid) for tid in random.sample(sorted(loved), min(len(loved), 4))]
            try:
                discovery_added = await self.vector_search.station(
                    aspects_for("all", self.vector_search.aspects), None, [t for t in seeds if t],
                    n_results=target_discovery, banned_ids=prefs["bans"],
                    keep=lambda t: t.get("id") not in existing_ids,
                )
                existing_ids.update(t["id"] for t in discovery_added)
            except Exception as e:
                log_service.error(f"{log_service.who(session_id)}: Discovery search failed: {e}")

        combined = favorites_added + discovery_added
        random.shuffle(combined)
        return combined
