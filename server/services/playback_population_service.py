import random
from typing import Dict, List, Optional, Any, Set
from services import log_service
from services.user_data_cache_service import user_data_cache
from services.analytics_service import analytics_service

PLAYLIST_MODES = frozenset([
    "favorites", "discovery",
    "top_hits_all", "top_hits_week", "top_hits_day",
])

SEED_MODES = frozenset([
    "all", "primary_genre", "secondary_genres", "mood",
    "primary_artist", "similar_artists",
    "style", "theme", "lyrics", "vocal",
])

def is_playlist_mode(mode: str) -> bool:
    return mode in PLAYLIST_MODES

def is_seed_mode(mode: str) -> bool:
    return mode in SEED_MODES

async def _get_user_preferences(user_id: Optional[int] = None):
    if not user_id:
        return {"likes": set(), "super_likes": set(), "bans": set()}
    try:
        return await user_data_cache.get_preferences(user_id)
    except Exception as e:
        log_service.error(f"Error getting user preferences: {e}")
        return {"likes": set(), "super_likes": set(), "bans": set()}

def _build_category_query(track: Dict[str, Any], category: str) -> str:
    params = track.get("generation_params", {}) or {}
    derived = track.get("derived_tags", {}) or {}

    queries = {
        "primary_genre":    f"Genre: {derived.get('primary_genre', '')}",
        "secondary_genres": f"Genre: {' '.join(derived.get('secondary_genres') or [])}",
        "mood":             f"Mood: {' '.join(derived.get('mood_keywords') or [])}",
        "primary_artist":   f"Artist: {derived.get('inspired_artist') or params.get('artist_name', '')}",
        "style":            f"Style: {params.get('style_canonical') or params.get('style', '')}",
        "theme":            f"Theme: {(derived.get('lyrical_interpretation') or '')[:200]}",
        "vocal":            f"Vocal: {' '.join(derived.get('vocal_style_keywords') or [])}",
        "lyrics":           f"Lyrics: {(params.get('prompt') or '')[:200]}",
    }

    if category in queries:
        result = queries[category]
        return result if result.split(": ", 1)[1].strip() else _generic_query(track)

    return _generic_query(track)

def _build_similar_artists_queries(track: Dict[str, Any]) -> List[str]:
    derived = track.get("derived_tags", {}) or {}
    similar = derived.get("similar_artists") or []
    if isinstance(similar, str):
        similar = [a.strip() for a in similar.split(",") if a.strip()]
    return [f"Artist: {name}" for name in similar if name]

def _generic_query(track: Dict[str, Any]) -> str:
    params = track.get("generation_params", {}) or {}
    track_info = track.get("track_info", {}) or {}

    parts = []
    if params.get("style"):
        parts.append(f"Style: {params['style']}")
    if params.get("title"):
        parts.append(f"Title: {params['title']}")
    if track_info.get("tags"):
        parts.append(f"Tags: {track_info['tags']}")
    return " | ".join(parts) if parts else "music"

def _apply_preference_boost(tracks: List[Dict], likes: Set[str], super_likes: Set[str]):
    for t in tracks:
        score = t.get("similarity_score", 0)
        tid = t["id"]
        if tid in super_likes:
            score += 0.3
        elif tid in likes:
            score += 0.15
        t["_recommendation_score"] = score

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
        user_id: Optional[int] = None,
        session_id: str = "",
    ) -> List[Dict]:

        if len(queue) >= queue_size:
            return []

        needed = queue_size - len(queue)
        prefs = await _get_user_preferences(user_id)
        existing_ids = {t["id"] for t in queue} | {t["id"] for t in history[-20:]}

        if is_playlist_mode(radio_mode):
            new_tracks = await self._fill_playlist(
                radio_mode, needed, prefs, existing_ids, session_id,
            )
        else:
            new_tracks = await self._fill_seed(
                radio_mode, needed, prefs, existing_ids,
                queue, history, session_id,
            )

        if len(new_tracks) < needed:
            remaining = needed - len(new_tracks)
            fallback = self._fill_random_fallback(remaining, prefs["bans"], existing_ids, session_id)
            new_tracks.extend(fallback)

        if len(new_tracks) < needed:
            log_service.warning(
                f"[{session_id}] Queue partially filled: {len(new_tracks)}/{needed}. "
                f"Catalog may be small or exhausted."
            )
        else:
            log_service.success(f"[{session_id}] Queue filled: {len(new_tracks)}/{needed} tracks")

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

        prefs = await _get_user_preferences(user_id)
        exclude_ids = {t["id"] for t in queue} | {t["id"] for t in history[-10:]}

        if category == "similar_artists":
            results = await self._search_similar_artists(seed_track, needed, prefs["bans"])
        else:
            query = _build_category_query(seed_track, category)
            log_service.playback(f"[{session_id}] Seed fill — category: {category}, query: {query[:100]}...")
            try:
                results = await self.vector_search.search(
                    query=query, n_results=needed + 10, banned_ids=prefs["bans"],
                )
            except Exception as e:
                log_service.error(f"[{session_id}] Vector search failed during seed fill: {e}")
                results = []

        new_tracks = []
        for t in results:
            if t["id"] in exclude_ids:
                continue
            _apply_preference_boost([t], prefs["likes"], prefs["super_likes"])
            new_tracks.append(t)
            exclude_ids.add(t["id"])
            if len(new_tracks) >= needed:
                break

        log_service.success(f"[{session_id}] Seed fill returned {len(new_tracks)} tracks")
        return new_tracks

    async def _fill_playlist(
        self,
        mode: str,
        needed: int,
        prefs: Dict,
        existing_ids: Set[str],
        session_id: str,
    ) -> List[Dict]:
        if mode.startswith("top_hits_"):
            return await self._fill_top_hits(mode, needed, prefs["bans"], existing_ids, session_id)
        if mode == "favorites":
            return self._fill_favorites(needed, prefs, existing_ids, session_id)
        if mode == "discovery":
            return await self._fill_discovery(needed, prefs, existing_ids, session_id)
        return []

    async def _fill_top_hits(
        self, mode: str, needed: int, banned_ids: Set[str],
        existing_ids: Set[str], session_id: str,
    ) -> List[Dict]:
        period = mode.replace("top_hits_", "")
        log_service.playback(f"[{session_id}] Filling queue with {period} top hits")

        try:
            top_hits = await analytics_service.get_top_hits(period=period, limit=needed + 10)
        except Exception as e:
            log_service.error(f"[{session_id}] Failed to fetch top hits: {e}")
            return []

        if not top_hits:
            log_service.error(f"[{session_id}] No top hits available for period: {period}")
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
        log_service.success(f"[{session_id}] Added {len(new_tracks)} tracks from {period} top hits")
        return new_tracks

    def _fill_favorites(
        self, needed: int, prefs: Dict,
        existing_ids: Set[str], session_id: str,
    ) -> List[Dict]:
        likes = list(prefs["likes"])
        super_likes = list(prefs["super_likes"])
        banned_ids = prefs["bans"]

        weighted_pool = super_likes * 3 + likes
        valid = [tid for tid in weighted_pool if tid not in existing_ids and tid not in banned_ids]

        new_tracks = []
        attempts = 0
        while len(new_tracks) < needed and attempts < 100 and valid:
            attempts += 1
            chosen_id = random.choice(valid)
            valid = [x for x in valid if x != chosen_id]

            if chosen_id in existing_ids:
                continue

            track = self.catalog.get_track(chosen_id) if self.catalog else None
            if track:
                new_tracks.append(track)
                existing_ids.add(chosen_id)

        random.shuffle(new_tracks)
        log_service.playback(f"[{session_id}] Favorites: added {len(new_tracks)}/{needed} tracks")
        return new_tracks

    async def _fill_discovery(
        self, needed: int, prefs: Dict,
        existing_ids: Set[str], session_id: str,
    ) -> List[Dict]:
        likes = list(prefs["likes"])
        super_likes = list(prefs["super_likes"])
        banned_ids = prefs["bans"]

        target_favs = needed // 2
        target_discovery = needed - target_favs

        if needed % 2 == 1:
            if random.random() < 0.5:
                target_favs += 1
            else:
                target_discovery += 1

        favorites_added = self._fill_favorites(target_favs, prefs, existing_ids, session_id)

        if len(favorites_added) < target_favs:
            target_discovery += target_favs - len(favorites_added)
            if target_discovery > 0:
                log_service.playback(f"[{session_id}] Favorites exhausted, redirecting to discovery")

        discovery_added = []
        if target_discovery > 0 and self.vector_search:
            all_favs = list(set(likes + super_likes))
            if all_favs:
                seed_ids = random.sample(all_favs, min(len(all_favs), 4))
                try:
                    results = await self.vector_search.search(
                        query=seed_ids, n_results=target_discovery + 10, banned_ids=banned_ids,
                    )
                    for track in results:
                        if track["id"] not in existing_ids:
                            discovery_added.append(track)
                            existing_ids.add(track["id"])
                            if len(discovery_added) >= target_discovery:
                                break
                except Exception as e:
                    log_service.error(f"[{session_id}] Discovery search failed: {e}")

        combined = favorites_added + discovery_added
        random.shuffle(combined)
        return combined

    async def _fill_seed(
        self,
        mode: str,
        needed: int,
        prefs: Dict,
        existing_ids: Set[str],
        queue: List[Dict],
        history: List[Dict],
        session_id: str,
    ) -> List[Dict]:
        context = queue[-3:] if queue else (history[-3:] if history else [])
        context_ids = [t["id"] for t in context]

        existing_titles: Set[str] = set()
        for t in queue + history[-20:]:
            title = t.get("generation_params", {}).get("title")
            if title:
                existing_titles.add(title.strip().lower())

        if not self.vector_search or not context_ids:
            return []

        if mode == "similar_artists":
            seed_track = context[-1] if context else None
            if seed_track:
                results = await self._search_similar_artists(seed_track, needed + 15, prefs["bans"])
                _apply_preference_boost(results, prefs["likes"], prefs["super_likes"])
                return _deduplicate_by_title(results, existing_ids, existing_titles, needed)
            return []

        try:
            results = await self.vector_search.search(
                query=context_ids, n_results=needed + 15, banned_ids=prefs["bans"],
            )
        except Exception as e:
            log_service.error(f"[{session_id}] Seed mode search failed: {e}")
            return []

        _apply_preference_boost(results, prefs["likes"], prefs["super_likes"])
        return _deduplicate_by_title(results, existing_ids, existing_titles, needed)

    async def _search_similar_artists(
        self,
        seed_track: Dict[str, Any],
        n_results: int,
        banned_ids: Set[str],
    ) -> List[Dict]:

        queries = _build_similar_artists_queries(seed_track)
        if not queries:
            derived = seed_track.get("derived_tags", {}) or {}
            artist = derived.get("inspired_artist", "")
            if artist:
                queries = [f"Artist: {artist}"]
            else:
                return []

        seen_ids: Set[str] = set()
        merged: List[Dict] = []
        per_artist = max(3, n_results // len(queries) + 2)

        for query in queries:
            try:
                results = await self.vector_search.search(
                    query=query, n_results=per_artist, banned_ids=banned_ids,
                )
                for t in results:
                    if t["id"] not in seen_ids:
                        merged.append(t)
                        seen_ids.add(t["id"])
            except Exception as e:
                log_service.error(f"Similar artist search failed for '{query}': {e}")

        merged.sort(key=lambda t: t.get("similarity_score", 0), reverse=True)
        return merged[:n_results]

    def _fill_random_fallback(
        self,
        needed: int,
        banned_ids: Set[str],
        existing_ids: Set[str],
        session_id: str,
    ) -> List[Dict]:
        if not self.catalog or not self.catalog.tracks:
            return []

        log_service.warning(
            f"[{session_id}] Falling back to random catalog for {needed} tracks"
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