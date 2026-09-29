import asyncio
import re
import numpy as np
from typing import List, Dict, Any, Optional, Tuple, Union
from services import log_service
from services.semantic_source import SemanticSearch

class CatalogVectorSearchService:

    def __init__(self, vector_db_service, catalog_service=None, prompt_cache_service=None):
        self.vector_db = vector_db_service
        self.catalog = catalog_service
        self.prompt_cache_service = prompt_cache_service
        self.semantic = SemanticSearch(vector_db_service, prompt_cache_service)
        log_service.vector_music("✓ CatalogVectorSearchService initialized")

    async def search(
            self,
            query: Union[str, List[str]],
            n_results: int = 10,
            instrumental: Optional[bool] = None,
            vocal_gender: Optional[str] = None,
            use_ai_analysis: bool = False,
            banned_ids: Optional[set] = None
    ) -> List[Dict[str, Any]]:

        if not self.catalog or not self.catalog.tracks:
            log_service.warning("No catalog available for search")
            return []

        current_annoy_index = self.vector_db.current_annoy_index()

        def _safe_search(vector_data, num_items):
            with self.vector_db.index_lock:
                if current_annoy_index.get_n_items() == 0:
                    return []
                return current_annoy_index.get_nns_by_vector(vector_data, num_items)

        if isinstance(query, list):
            return await asyncio.to_thread(
                self._search_by_track_ids_sync, query, n_results, banned_ids, _safe_search
            )

        try:
            log_service.detail(f"🔍 Searching: '{query}'", "vector_music")
            intent_category, query_weights, cleaned_query = await self._intent(query, use_ai_analysis)
            log_service.detail(f"  📊 Category weights: {query_weights}", "vector_music")

            hidden = self.catalog.hidden_ids if self.catalog is not None else set()

            def keep(track: Dict[str, Any]) -> bool:
                if banned_ids and track.get("id") in banned_ids:
                    return False
                if track.get("id") in hidden:
                    return False
                params = track.get("generation_params", {})
                if instrumental is not None and params.get("instrumental", False) != instrumental:
                    return False
                if vocal_gender is not None and vocal_gender != "none" and params.get("vocal_gender") != vocal_gender:
                    return False
                return True

            found = await self.semantic.search(cleaned_query, n=n_results, keep=keep, weights=query_weights)
            track_results = []
            for match in found:
                result = match.meta.copy()
                result['similarity_score'] = match.similarity
                result['intent_category'] = intent_category
                result['match_weights'] = query_weights
                track_results.append(result)

            if track_results:
                log_service.detail("  🎯 Top 5 matches:", "vector_music")
                for i, track in enumerate(track_results[:5], 1):
                    params = track.get('generation_params', {})
                    derived = track.get('derived_tags', {})
                    log_service.detail(
                        f"    {i}. [{track['similarity_score']:.3f}] {params.get('title', 'Unknown')} - "
                        f"{derived.get('inspired_artist', params.get('artist_name', 'Unknown'))} "
                        f"({derived.get('primary_genre', 'Unknown')})", "vector_music")
            log_service.detail(
                f"✓ Returning {len(track_results)} results for '{query}' (intent: {intent_category})", "vector_music"
            )
            return track_results

        except Exception as e:
            log_service.error(f"Vector search error: {str(e)}")
            import traceback
            log_service.error(f"Traceback: {traceback.format_exc()}")
            return []

    async def _intent(self, query: str, use_ai_analysis: bool) -> Tuple[str, Dict[str, float], str]:
        if use_ai_analysis and self.prompt_cache_service:
            ai_analysis = await self.prompt_cache_service.analyze_query(query)
            if ai_analysis:
                log_service.detail(f"🤖 AI Intent: {ai_analysis.intent_category} "
                                   f"(confidence: {ai_analysis.confidence:.2f})", "vector_music")
                return ai_analysis.intent_category, ai_analysis.category_weights.model_dump(), ai_analysis.cleaned_query
            log_service.warning("AI analysis failed, falling back to keyword detection")
        intent, weights, cleaned = self._detect_query_intent(query)
        return intent, weights, cleaned if cleaned.strip() else query

    def _search_by_track_ids_sync(self, query: List[str], n_results: int, banned_ids, _safe_search) -> List[Dict[str, Any]]:
        conn = self.catalog._get_connection()
        try:
            return self._search_by_track_ids_with_conn(conn, query, n_results, banned_ids, _safe_search)
        finally:
            conn.close()

    def _search_by_track_ids_with_conn(self, conn, query: List[str], n_results: int, banned_ids, _safe_search) -> List[Dict[str, Any]]:
        import json
        c = conn.cursor()

        context_vectors = []
        found_ids = set()

        for track_id in query:
            c.execute("SELECT rowid, metadata_json FROM tracks WHERE track_id = %s", (track_id,))
            result = c.fetchone()
            if result:
                rowid, metadata_json = result
                track = json.loads(metadata_json)
                category_texts = self.vector_db._extract_category_texts(track)

                combined = self.vector_db._create_weighted_embedding(
                    self.vector_db.get_category_embeddings(category_texts)
                )
                context_vectors.append(combined)
                found_ids.add(track_id)

        if not context_vectors:
            return []

        weights = np.linspace(0.5, 1.0, len(context_vectors))
        average_vector = np.average(context_vectors, axis=0, weights=weights)

        nearest_ids = _safe_search(average_vector, n_results * 3)

        results = []
        exclude_ids = (banned_ids or set()) | found_ids | (self.catalog.hidden_ids if self.catalog is not None else set())

        for annoy_idx in nearest_ids:
            rowid = annoy_idx + 1

            track_id, track = self.vector_db.lookup_cached_row(rowid)
            if track_id is not None:
                if track_id in exclude_ids:
                    continue
                if track:
                    track['similarity_score'] = 0.95
                    results.append(track)
                    if len(results) >= n_results:
                        break
                    continue

            c.execute("SELECT track_id, metadata_json FROM tracks WHERE rowid = %s", (rowid,))
            result = c.fetchone()
            if not result:
                continue
            track_id, metadata_json = result
            if track_id in exclude_ids:
                continue
            track = json.loads(metadata_json)
            track['similarity_score'] = 0.95
            results.append(track)
            if len(results) >= n_results:
                break

        return results

    def _clean_natural_query(self, query: str, trigger_patterns: list) -> str:
        cleaned = query.lower()

        for pattern in trigger_patterns:
            cleaned = re.sub(r'\b' + pattern + r'\b', '', cleaned, flags=re.IGNORECASE)

        filler_words = ['the', 'of', 'a', 'an', 'play', 'find', 'search', 'show', 'me', 'some']
        for filler in filler_words:
            cleaned = re.sub(r'^' + filler + r'\s+', '', cleaned)
            cleaned = re.sub(r'\s+' + filler + r'$', '', cleaned)
            cleaned = re.sub(r'\s+' + filler + r'\s+', ' ', cleaned)

        cleaned = ' '.join(cleaned.split())

        return cleaned.strip()

    def _detect_query_intent(self, query: str) -> Tuple[str, Dict[str, float], str]:

        query_lower = query.lower()
        detected_category = None
        cleaned_query = query

        if query_lower.startswith("genre:"):
            detected_category = "genre"
            cleaned_query = query[6:].strip()
        elif query_lower.startswith("mood:") or query_lower.startswith("vibe:"):
            detected_category = "mood"
            cleaned_query = query[5:].strip()
        elif query_lower.startswith("artist:"):
            detected_category = "artist"
            cleaned_query = query[7:].strip()
        elif query_lower.startswith("style:"):
            detected_category = "style"
            cleaned_query = query[6:].strip()
        elif query_lower.startswith("theme:"):
            detected_category = "theme"
            cleaned_query = query[6:].strip()
        elif query_lower.startswith("lyrics:"):
            detected_category = "lyrics"
            cleaned_query = query[7:].strip()
        elif query_lower.startswith("vocal:"):
            detected_category = "vocal"
            cleaned_query = query[6:].strip()
        elif query_lower.startswith("song:") or query_lower.startswith("track:") or query_lower.startswith("title:"):
            detected_category = "song_title"
            cleaned_query = query.split(":", 1)[1].strip()
        elif query_lower.startswith("subgenre:") or query_lower.startswith("sub-genre:") or query_lower.startswith("secondary genre:"):
            detected_category = "secondary_genres"
            cleaned_query = query.split(":", 1)[1].strip()
        elif query_lower.startswith("similar artist:") or query_lower.startswith("similar artists:"):
            detected_category = "similar_artists"
            cleaned_query = query.split(":", 1)[1].strip()
        elif query_lower.startswith("instrumental:"):
            detected_category = "instrumental"
            cleaned_query = query[13:].strip()

        elif re.search(r'\b(genre|genres|type of music|kind of music|music like)\b', query_lower):
            detected_category = "genre"
            cleaned_query = self._clean_natural_query(query, ['genre', 'genres', 'type of music', 'kind of music', 'music like'])
        elif re.search(r'\b(mood|vibe|vibes|feeling|atmosphere|energy)\b', query_lower):
            detected_category = "mood"
            cleaned_query = self._clean_natural_query(query, ['mood', 'vibe', 'vibes', 'feeling', 'atmosphere', 'energy'])
        elif re.search(r'\b(artist|artists|band|singer|musician|similar to|like|sounds like|reminds me of)\b',
                       query_lower):
            detected_category = "artist"
            cleaned_query = self._clean_natural_query(query, ['artist', 'artists', 'band', 'singer', 'musician', 'similar to', 'reminds me of'])
        elif re.search(r'\b(style|production|sound|produced|recorded)\b', query_lower):
            detected_category = "style"
            cleaned_query = self._clean_natural_query(query, ['style', 'production', 'sound', 'produced', 'recorded'])
        elif re.search(r'\b(theme|about|story|narrative|lyrics about|message|meaning)\b', query_lower):
            detected_category = "theme"
            cleaned_query = self._clean_natural_query(query, ['theme', 'about', 'story', 'narrative', 'lyrics about', 'message', 'meaning'])
        elif re.search(r'\b(vocal|vocals|voice|sung|singing)\b', query_lower):
            detected_category = "vocal"
            cleaned_query = self._clean_natural_query(query, ['vocal', 'vocals', 'voice', 'sung', 'singing'])
        elif re.search(r'\b(song|track|title|named|called|play the song|play the track)\b', query_lower):
            detected_category = "song_title"
            cleaned_query = self._clean_natural_query(query, ['song', 'track', 'title', 'named', 'called', 'play the song', 'play the track'])
        elif re.search(r'\b(subgenre|subgenres|sub-genre|sub-genres|secondary genre)\b', query_lower):
            detected_category = "secondary_genres"
            cleaned_query = self._clean_natural_query(query, ['subgenre', 'subgenres', 'sub-genre', 'sub-genres', 'secondary genre'])
        elif re.search(r'\b(similar artist|similar artists|sounds like)\b', query_lower):
            detected_category = "similar_artists"
            cleaned_query = self._clean_natural_query(query, ['similar artist', 'similar artists', 'sounds like'])
        elif re.search(r'\b(instrumental|instrumentals|no vocals|without vocals)\b', query_lower):
            detected_category = "instrumental"
            cleaned_query = self._clean_natural_query(query, ['instrumental', 'instrumentals', 'no vocals', 'without vocals'])

        if detected_category == "song_title":
            weights = {
                "song_title": 0.60, "primary_artist": 0.15, "lyrics": 0.10,
                "primary_genre": 0.05, "secondary_genres": 0.02, "mood": 0.03,
                "style": 0.02, "theme": 0.02, "similar_artists": 0.00, "vocal": 0.01
            }
            log_service.detail("🎯 Query intent: SONG TITLE", "vector_music")

        elif detected_category == "genre":
            weights = {
                "primary_genre": 0.50, "secondary_genres": 0.15, "mood": 0.12,
                "primary_artist": 0.08, "similar_artists": 0.05, "style": 0.07,
                "vocal": 0.02, "theme": 0.01, "lyrics": 0.00, "song_title": 0.00
            }
            log_service.detail("🎯 Query intent: GENRE", "vector_music")

        elif detected_category == "mood":
            weights = {
                "mood": 0.50, "primary_genre": 0.12, "secondary_genres": 0.08, "style": 0.12,
                "theme": 0.08, "primary_artist": 0.04, "similar_artists": 0.03,
                "vocal": 0.02, "lyrics": 0.01, "song_title": 0.00
            }
            log_service.detail("🎯 Query intent: MOOD", "vector_music")

        elif detected_category == "artist":
            weights = {
                "primary_artist": 0.50, "similar_artists": 0.15, "primary_genre": 0.10,
                "secondary_genres": 0.08, "style": 0.10, "mood": 0.04,
                "vocal": 0.02, "theme": 0.01, "lyrics": 0.00, "song_title": 0.00
            }
            log_service.detail("🎯 Query intent: ARTIST", "vector_music")

        elif detected_category == "style":
            weights = {
                "style": 0.45, "primary_genre": 0.20, "secondary_genres": 0.10, "mood": 0.12,
                "primary_artist": 0.05, "similar_artists": 0.03, "vocal": 0.03,
                "theme": 0.01, "lyrics": 0.01, "song_title": 0.00
            }
            log_service.detail("🎯 Query intent: STYLE", "vector_music")

        elif detected_category == "theme":
            weights = {
                "theme": 0.45, "lyrics": 0.22, "mood": 0.15,
                "primary_genre": 0.06, "secondary_genres": 0.04, "style": 0.04,
                "primary_artist": 0.02, "similar_artists": 0.01, "vocal": 0.01, "song_title": 0.00
            }
            log_service.detail("🎯 Query intent: THEME", "vector_music")

        elif detected_category == "vocal":
            weights = {
                "vocal": 0.45, "style": 0.18, "primary_genre": 0.12, "secondary_genres": 0.08,
                "mood": 0.10, "primary_artist": 0.03, "similar_artists": 0.02,
                "theme": 0.01, "lyrics": 0.01, "song_title": 0.00
            }
            log_service.detail("🎯 Query intent: VOCAL", "vector_music")

        elif detected_category == "secondary_genres":
            weights = {
                "secondary_genres": 0.50, "primary_genre": 0.25, "mood": 0.10,
                "style": 0.08, "primary_artist": 0.03, "similar_artists": 0.02,
                "vocal": 0.01, "theme": 0.01, "lyrics": 0.00, "song_title": 0.00
            }
            log_service.detail("🎯 Query intent: SECONDARY GENRES", "vector_music")

        elif detected_category == "similar_artists":
            weights = {
                "similar_artists": 0.50, "primary_artist": 0.25, "primary_genre": 0.10,
                "secondary_genres": 0.06, "style": 0.06, "mood": 0.02,
                "vocal": 0.01, "theme": 0.00, "lyrics": 0.00, "song_title": 0.00
            }
            log_service.detail("🎯 Query intent: SIMILAR ARTISTS", "vector_music")

        elif detected_category == "instrumental":
            weights = {
                "style": 0.35, "primary_genre": 0.25, "secondary_genres": 0.15, "mood": 0.15,
                "primary_artist": 0.05, "similar_artists": 0.03, "theme": 0.01,
                "vocal": 0.01, "lyrics": 0.00, "song_title": 0.00
            }
            log_service.detail("🎯 Query intent: INSTRUMENTAL", "vector_music")

        else:
            weights = {
                "primary_genre": 0.20, "secondary_genres": 0.10, "mood": 0.20,
                "primary_artist": 0.15, "similar_artists": 0.10, "style": 0.12,
                "vocal": 0.07, "theme": 0.04, "lyrics": 0.02, "song_title": 0.00
            }
            log_service.system("🎯 Query intent: GENERAL")

        return (detected_category or "general", weights, cleaned_query)

    async def add_track(self, track_id: str):
        log_service.vector_music(f"New track {track_id} added - background task will rebuild index")