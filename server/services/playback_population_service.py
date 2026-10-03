import random
from typing import Any, Dict, List, Optional, Set

from config import settings
from services import log_service
from services.catalog_aspects import Aspect, aspects_for
from services.playback_lists import ListFills, is_list_mode
from services.user_data_cache_service import user_data_cache


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
    """Fills a station's queue. Lists (favorites, discovery, top hits) come from ListFills; every other mode is a
    station built from aspects: one aspect (a pure seed) or a weighted blend, matched to the seed (a song, or words)
    and the last few songs, each counted equally."""

    def __init__(self, catalog_service, vector_search_service):
        self.catalog = catalog_service
        self.vector_search = vector_search_service
        self.lists = ListFills(catalog_service, vector_search_service)
        log_service.system("PlaybackPopulationService initialized")

    def station_aspects(self, mode: str, blend: Optional[List[Dict[str, Any]]]) -> List[Aspect]:
        if blend:
            return [Aspect(item["category"], float(item.get("weight") or 1.0), item.get("words") or None)
                    for item in blend]
        return aspects_for(mode, self.vector_search.aspects) if self.vector_search else []

    async def fill_queue(
        self,
        *,
        radio_mode: str,
        queue: List[Dict],
        history: List[Dict],
        queue_size: int,
        seed_track: Optional[Dict[str, Any]] = None,
        blend: Optional[List[Dict[str, Any]]] = None,
        user_id: Optional[int] = None,
        session_id: str = "",
    ) -> List[Dict]:

        if len(queue) >= queue_size:
            return []

        needed = queue_size - len(queue)
        prefs = await _get_user_preferences(user_id, session_id)
        heard = list(queue) + _recent(history)
        existing_ids = {t["id"] for t in heard}

        if is_list_mode(radio_mode):
            new_tracks = await self.lists.fill(radio_mode, needed, prefs, existing_ids, session_id, user_id)
        else:
            seed_id = seed_track.get("id") if seed_track else None
            recent = [t for t in (queue or history)[-settings.SEED_CONTEXT_SONGS:] if t.get("id") != seed_id]
            new_tracks = await self._station_picks(self.station_aspects(radio_mode, blend), seed_track, recent,
                                                   needed, prefs, heard, existing_ids, session_id)

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
        seed_track: Optional[Dict[str, Any]],
        category: str,
        needed: int,
        queue: List[Dict],
        history: List[Dict],
        blend: Optional[List[Dict[str, Any]]] = None,
        user_id: Optional[int] = None,
        session_id: str = "",
    ) -> List[Dict]:

        prefs = await _get_user_preferences(user_id, session_id)
        heard = list(queue) + _recent(history) + ([seed_track] if seed_track else [])
        existing_ids = {t["id"] for t in heard}
        aspects = self.station_aspects(category, blend)
        step = max(1, settings.SEED_CONTEXT_SONGS)
        new_tracks: List[Dict] = []
        while len(new_tracks) < needed:
            picks = await self._station_picks(aspects, seed_track, new_tracks[-step:],
                                              min(step, needed - len(new_tracks)), prefs, heard + new_tracks,
                                              existing_ids, session_id)
            if not picks:
                break
            new_tracks.extend(picks)
        log_service.detail(f"{log_service.who(session_id)}: seed fill returned {len(new_tracks)} tracks", "playback")
        return new_tracks

    async def _station_picks(
        self,
        aspects: List[Aspect],
        seed_track: Optional[Dict[str, Any]],
        recent: List[Dict],
        needed: int,
        prefs: Dict,
        heard: List[Dict],
        existing_ids: Set[str],
        session_id: str,
    ) -> List[Dict]:
        anchored = seed_track or recent or any(aspect.words for aspect in aspects)
        if not self.vector_search or not aspects or needed <= 0 or not anchored:
            return []
        existing_titles = {_title(t) for t in heard if _title(t)}

        def fresh(track: Dict[str, Any]) -> bool:
            return track.get("id") not in existing_ids and _title(track) not in existing_titles

        try:
            results = await self.vector_search.station(
                aspects, seed_track, recent, n_results=len(self.catalog.tracks), banned_ids=prefs["bans"],
                keep=fresh,
            )
        except Exception as e:
            log_service.error(f"{log_service.who(session_id)}: station search failed "
                              f"({', '.join(a.category for a in aspects)}): {e}")
            return []

        return _deduplicate_by_title(results, existing_ids, existing_titles, needed)

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
