import random
from typing import Any, Dict, List, Optional, Set
from config import settings
from services import listener_plays, log_service
from services.user_data_cache_service import user_data_cache
from services.analytics_service import analytics_service

PLAYLIST_MODES = frozenset([
    "favorites", "discovery",
    "top_hits_all", "top_hits_week", "top_hits_day",
])

def is_playlist_mode(mode: str) -> bool:
    return mode in PLAYLIST_MODES

async def _get_user_preferences(user_id: Optional[int] = None, session_id: Optional[str] = None):
    from services.listener_filters import excluded_ids
    excluded = await excluded_ids(user_id, session_id)
    if not user_id:
        return {"likes": set(), "super_likes": set(), "bans": excluded}
    try:
        prefs = await user_data_cache.get_preferences(user_id)
        return {**prefs, "bans": excluded}
    except Exception as e:
        log_service.error(f"Error getting user preferences: {e}")
        return {"likes": set(), "super_likes": set(), "bans": excluded}

def _title(track: Dict[str, Any]) -> str:
    return ((track.get("generation_params") or {}).get("title") or "").strip().lower()

def _recent(history: List[Dict]) -> List[Dict]:
    return history[-settings.QUEUE_NO_REPEAT_SONGS:] if settings.QUEUE_NO_REPEAT_SONGS > 0 else []

def _deduplicate_by_title(
    results: List[Dict],
    existing_ids: Set[str],
    existing_titles: Set[str],
    needed: int,
) -> List[Dict]:

    title_groups: Dict[str, List[Dict]] = {}
    for t in results:
        t_title = t.get("generation_params", {}).get("title", "").strip().lower()
        key = t_title if t_title else "untitled"
        title_groups.setdefault(key, []).append(t)

    out: List[Dict] = []
    processed_titles: Set[str] = set()

    for t in results:
        if len(out) >= needed:
            break
        t_title = t.get("generation_params", {}).get("title", "").strip().lower()

        if not t_title:
            if t["id"] not in existing_ids:
                out.append(t)
                existing_ids.add(t["id"])
        elif t_title not in processed_titles:
            winner = random.choice(title_groups[t_title])
            if winner["id"] not in existing_ids and t_title not in existing_titles:
                out.append(winner)
                existing_ids.add(winner["id"])
                existing_titles.add(t_title)
            processed_titles.add(t_title)

    return out

class PlaybackPopulationService:

    def __init__(self, catalog_service, vector_search_service):
        self.catalog = catalog_service
        self.vector_search = vector_search_service
        log_service.system("PlaybackPopulationService initialized")

    async def fill_queue(
        self,
        *,
        radio_mode: str,
        queue: List[Dict],
        history: List[Dict],
        queue_size: int,
        seed_track: Optional[Dict[str, Any]] = None,
        user_id: Optional[int] = None,
        session_id: str = "",
    ) -> List[Dict]:

        if len(queue) >= queue_size:
            return []

        needed = queue_size - len(queue)
        prefs = await _get_user_preferences(user_id, session_id)
        heard = list(queue) + _recent(history)
        existing_ids = {t["id"] for t in heard}

        if is_playlist_mode(radio_mode):
            new_tracks = await self._fill_playlist(
                radio_mode, needed, prefs, existing_ids, session_id, user_id,
            )
        else:
            recent = (queue or history)[-settings.SEED_CONTEXT_SONGS:]
            anchors = ([seed_track] if seed_track else []) + [t for t in recent if t is not seed_track]
            new_tracks = await self._mode_picks(anchors, radio_mode, needed, prefs, heard, existing_ids, session_id)

        if len(new_tracks) < needed:
            remaining = needed - len(new_tracks)
            fallback = self._fill_random_fallback(remaining, prefs["bans"], existing_ids, session_id)
            new_tracks.extend(fallback)

        if len(new_tracks) < needed:
            log_service.warning(
                f"{log_service.who(session_id)}: {radio_mode} queue only partly filled ({len(new_tracks)}/{needed}) - "
                f"catalog may be small or exhausted"
            )
        else:
            log_service.detail(f"{log_service.who(session_id)}: {radio_mode} queue filled with {len(new_tracks)} "
                               f"track(s)", "playback")

        return new_tracks

    async def seed_fill(
        self,
        *,
        seed_track: Dict[str, Any],
        category: str,
        needed: int,
        queue: List[Dict],
        history: List[Dict],
        user_id: Optional[int] = None,
        session_id: str = "",
    ) -> List[Dict]:

        prefs = await _get_user_preferences(user_id, session_id)
        heard = list(queue) + _recent(history) + [seed_track]
        existing_ids = {t["id"] for t in heard}
        step = max(1, settings.SEED_CONTEXT_SONGS)
        new_tracks: List[Dict] = []
        while len(new_tracks) < needed:
            anchors = [seed_track] + new_tracks[-step:]
            picks = await self._mode_picks(anchors, category, min(step, needed - len(new_tracks)), prefs,
                                           heard + new_tracks, existing_ids, session_id)
            if not picks:
                break
            new_tracks.extend(picks)
        log_service.detail(f"{log_service.who(session_id)}: seed fill returned {len(new_tracks)} tracks", "playback")
        return new_tracks

    async def _mode_picks(
        self,
        anchors: List[Dict],
        mode: str,
        needed: int,
        prefs: Dict,
        heard: List[Dict],
        existing_ids: Set[str],
        session_id: str,
    ) -> List[Dict]:
        if not self.vector_search or not anchors or needed <= 0:
            return []
        existing_titles = {_title(t) for t in heard if _title(t)}

        def fresh(track: Dict[str, Any]) -> bool:
            return track.get("id") not in existing_ids and _title(track) not in existing_titles

        try:
            results = await self.vector_search.similar(
                anchors, mode, n_results=len(self.catalog.tracks), banned_ids=prefs["bans"], keep=fresh,
            )
        except Exception as e:
            log_service.error(f"{log_service.who(session_id)}: {mode} seed search failed: {e}")
            return []

        return _deduplicate_by_title(results, existing_ids, existing_titles, needed)

    async def _fill_playlist(
        self,
        mode: str,
        needed: int,
        prefs: Dict,
        existing_ids: Set[str],
        session_id: str,
        user_id: Optional[int],
    ) -> List[Dict]:
        if mode.startswith("top_hits_"):
            return await self._fill_top_hits(mode, needed, prefs["bans"], existing_ids, session_id)
        if mode == "favorites":
            return await self._fill_favorites(needed, prefs, existing_ids, session_id, user_id)
        if mode == "discovery":
            return await self._fill_discovery(needed, prefs, existing_ids, session_id, user_id)
        return []

    async def _fill_top_hits(
        self, mode: str, needed: int, banned_ids: Set[str],
        existing_ids: Set[str], session_id: str,
    ) -> List[Dict]:
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

    async def _fill_favorites(
        self, needed: int, prefs: Dict,
        existing_ids: Set[str], session_id: str, user_id: Optional[int],
    ) -> List[Dict]:
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

    async def _fill_discovery(
        self, needed: int, prefs: Dict,
        existing_ids: Set[str], session_id: str, user_id: Optional[int],
    ) -> List[Dict]:
        loved = set(prefs["likes"]) | set(prefs["super_likes"])
        target_favs = needed // 2 + (needed % 2 if random.random() < 0.5 else 0)
        target_discovery = needed - target_favs

        favorites_added = await self._fill_favorites(target_favs, prefs, existing_ids, session_id, user_id)

        if len(favorites_added) < target_favs:
            target_discovery += target_favs - len(favorites_added)
            if target_discovery > 0:
                log_service.detail(f"{log_service.who(session_id)}: favorites exhausted, filling with discovery", "playback")

        discovery_added = []
        if target_discovery > 0 and self.vector_search and loved:
            seeds = [self.catalog.get_track(tid) for tid in random.sample(sorted(loved), min(len(loved), 4))]
            try:
                discovery_added = await self.vector_search.similar(
                    [t for t in seeds if t], "all", n_results=target_discovery, banned_ids=prefs["bans"],
                    keep=lambda t: t.get("id") not in existing_ids,
                )
                existing_ids.update(t["id"] for t in discovery_added)
            except Exception as e:
                log_service.error(f"{log_service.who(session_id)}: Discovery search failed: {e}")

        combined = favorites_added + discovery_added
        random.shuffle(combined)
        return combined

    def _fill_random_fallback(
        self,
        needed: int,
        banned_ids: Set[str],
        existing_ids: Set[str],
        session_id: str,
    ) -> List[Dict]:
        if not self.catalog or not self.catalog.tracks:
            return []

        log_service.detail(
            f"{log_service.who(session_id)}: falling back to random catalog for {needed} tracks", "playback"
        )

        all_ids = list(self.catalog.tracks.keys())
        random.shuffle(all_ids)

        new_tracks = []
        for tid in all_ids:
            if len(new_tracks) >= needed:
                break
            if tid not in existing_ids and tid not in banned_ids:
                track = self.catalog.get_track(tid)
                if track:
                    new_tracks.append(track)
                    existing_ids.add(tid)

        return new_tracks