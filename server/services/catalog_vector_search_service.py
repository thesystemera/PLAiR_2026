import asyncio
import re
import numpy as np
from typing import List, Dict, Any, Optional, Tuple, Union
from services import log_service

class CatalogVectorSearchService:

    def __init__(self, vector_db_service, catalog_service=None, prompt_cache_service=None):
        self.vector_db = vector_db_service
        self.catalog = catalog_service
        self.prompt_cache_service = prompt_cache_service
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

        current_annoy_index = (
            self.vector_db.annoy_index_tracks_1
            if self.vector_db.current_index == 1
            else self.vector_db.annoy_index_tracks_2
        )

        def _safe_search(vector_data, num_items):
            with self.vector_db.index_lock:
                if current_annoy_index.get_n_items() == 0:
                    return []
                return current_annoy_index.get_nns_by_vector(vector_data, num_items)

        if isinstance(query, list):
            import json
            conn = self.catalog._get_connection()
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

                    category_embeddings = {}
                    for cat in ["song_title", "primary_genre", "secondary_genres", "mood", "primary_artist",
                                "similar_artists", "style", "theme", "vocal", "lyrics"]:
                        cache = getattr(self.vector_db, f"{cat}_embeddings")
                        category_embeddings[cat] = self.vector_db.get_or_create_embedding(
                            category_texts[cat], f"{cat}_embeddings", cache
                        )

                    combined = self.vector_db._create_weighted_embedding(category_embeddings)
                    context_vectors.append(combined)
                    found_ids.add(track_id)

            if not context_vectors:
                conn.close()
                return []

            weights = np.linspace(0.5, 1.0, len(context_vectors))
            average_vector = np.average(context_vectors, axis=0, weights=weights)

            nearest_ids = await asyncio.to_thread(_safe_search, average_vector, n_results * 3)

            results = []
            exclude_ids = (banned_ids or set()) | found_ids

            for annoy_idx in nearest_ids:
                rowid = annoy_idx + 1
                
                if hasattr(self.vector_db, '_track_rowid_cache') and rowid in self.vector_db._track_rowid_cache:
                    track_id = self.vector_db._track_rowid_cache[rowid]
                    if track_id in exclude_ids:
                        continue
                    track = self.vector_db._track_metadata_cache.get(rowid, {}).copy()
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

            conn.close()
            return results

        try:
            log_service.vector_music(f"🔍 Searching: '{query}'")

            if use_ai_analysis and self.prompt_cache_service:
                ai_analysis = await self.prompt_cache_service.analyze_query(query)
                if ai_analysis:
                    intent_category = ai_analysis.intent_category
                    query_weights = ai_analysis.category_weights.model_dump()
                    cleaned_query = ai_analysis.cleaned_query
                    log_service.system(f"🤖 AI Intent: {intent_category} (confidence: {ai_analysis.confidence:.2f})")
                else:
                    log_service.warning("AI analysis failed, falling back to keyword detection")
                    intent_category, query_weights, cleaned_query = self._detect_query_intent(query)
            else:
                intent_category, query_weights, cleaned_query = self._detect_query_intent(query)

            log_service.vector_music(f"  📊 Category weights: {query_weights}")

            query_embedding = self.vector_db._generate_embedding(cleaned_query)

            if cleaned_query != query:
                log_service.vector_music(f"  Cleaned query: '{cleaned_query}'")

            nearest_ids = await asyncio.to_thread(_safe_search, query_embedding, n_results * 2)

            if not nearest_ids:
                if current_annoy_index.get_n_items() == 0:
                    log_service.error("Annoy index is still empty after rebuild!")
                return []

            log_service.vector_music(
                f"  Found {len(nearest_ids)} candidates, re-ranking with intent weights..."
            )

            track_results = []
            import json
            conn = self.catalog._get_connection()
            c = conn.cursor()

            for annoy_idx in nearest_ids:
                rowid = annoy_idx + 1
                track = None
                track_id = None
                
                if hasattr(self.vector_db, '_track_rowid_cache') and rowid in self.vector_db._track_rowid_cache:
                    track_id = self.vector_db._track_rowid_cache[rowid]
                    track = self.vector_db._track_metadata_cache.get(rowid, {}).copy()
                
                if track is None:
                    c.execute("SELECT track_id, metadata_json FROM tracks WHERE rowid = %s", (rowid,))
                    result = c.fetchone()
                    if not result:
                        continue
                    track_id, metadata_json = result
                    track = json.loads(metadata_json)

                if banned_ids and track_id in banned_ids:
                    continue

                params = track.get("generation_params", {})
                if instrumental is not None and params.get("instrumental", False) != instrumental:
                    continue
                if vocal_gender is not None and vocal_gender != "none":
                    if params.get("vocal_gender") != vocal_gender:
                        continue

                category_texts = self.vector_db._extract_category_texts(track)

                category_embeddings = {
                    "song_title": self.vector_db.get_or_create_embedding(
                        category_texts["song_title"], "song_title_embeddings", self.vector_db.song_title_embeddings
                    ),
                    "primary_genre": self.vector_db.get_or_create_embedding(
                        category_texts["primary_genre"], "primary_genre_embeddings",
                        self.vector_db.primary_genre_embeddings
                    ),
                    "secondary_genres": self.vector_db.get_or_create_embedding(
                        category_texts["secondary_genres"], "secondary_genres_embeddings",
                        self.vector_db.secondary_genres_embeddings
                    ),
                    "mood": self.vector_db.get_or_create_embedding(
                        category_texts["mood"], "mood_embeddings", self.vector_db.mood_embeddings
                    ),
                    "primary_artist": self.vector_db.get_or_create_embedding(
                        category_texts["primary_artist"], "primary_artist_embeddings",
                        self.vector_db.primary_artist_embeddings
                    ),
                    "similar_artists": self.vector_db.get_or_create_embedding(
                        category_texts["similar_artists"], "similar_artists_embeddings",
                        self.vector_db.similar_artists_embeddings
                    ),
                    "style": self.vector_db.get_or_create_embedding(
                        category_texts["style"], "style_embeddings", self.vector_db.style_embeddings
                    ),
                    "theme": self.vector_db.get_or_create_embedding(
                        category_texts["theme"], "theme_embeddings", self.vector_db.theme_embeddings
                    ),
                    "vocal": self.vector_db.get_or_create_embedding(
                        category_texts["vocal"], "vocal_embeddings", self.vector_db.vocal_embeddings
                    ),
                    "lyrics": self.vector_db.get_or_create_embedding(
                        category_texts["lyrics"], "lyrics_embeddings", self.vector_db.lyrics_embeddings
                    )
                }

                reweighted_embedding = self.vector_db._create_weighted_embedding(
                    category_embeddings, query_weights
                )
                reranked_similarity = np.dot(query_embedding, reweighted_embedding)

                track_result = track.copy()
                track_result['similarity_score'] = float(reranked_similarity)
                track_result['intent_category'] = intent_category
                track_result['match_weights'] = query_weights

                track_results.append(track_result)

            conn.close()

            track_results.sort(key=lambda x: x.get('similarity_score', 0), reverse=True)

            if track_results:
                log_service.vector_music("  🎯 Top 5 matches:")
                for i, track in enumerate(track_results[:5], 1):
                    params = track.get('generation_params', {})
                    derived = track.get('derived_tags', {})
                    title = params.get('title', 'Unknown')
                    artist = derived.get('inspired_artist', params.get('artist_name', 'Unknown'))
                    genre = derived.get('primary_genre', 'Unknown')
                    score = track.get('similarity_score', 0)
                    log_service.vector_music(
                        f"    {i}. [{score:.3f}] {title} - {artist} ({genre})"
                    )

            track_results = track_results[:n_results]

            log_service.vector_music(
                f"✓ Returning {len(track_results)} results for '{query}' (intent: {intent_category})"
            )
            return track_results

        except Exception as e:
            log_service.error(f"Vector search error: {str(e)}")
            import traceback
            log_service.error(f"Traceback: {traceback.format_exc()}")
            return []

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
            log_service.system("🎯 Query intent: SONG TITLE")

        elif detected_category == "genre":
            weights = {
                "primary_genre": 0.50, "secondary_genres": 0.15, "mood": 0.12,
                "primary_artist": 0.08, "similar_artists": 0.05, "style": 0.07,
                "vocal": 0.02, "theme": 0.01, "lyrics": 0.00, "song_title": 0.00
            }
            log_service.system("🎯 Query intent: GENRE")

        elif detected_category == "mood":
            weights = {
                "mood": 0.50, "primary_genre": 0.12, "secondary_genres": 0.08, "style": 0.12,
                "theme": 0.08, "primary_artist": 0.04, "similar_artists": 0.03,
                "vocal": 0.02, "lyrics": 0.01, "song_title": 0.00
            }
            log_service.system("🎯 Query intent: MOOD")

        elif detected_category == "artist":
            weights = {
                "primary_artist": 0.50, "similar_artists": 0.15, "primary_genre": 0.10,
                "secondary_genres": 0.08, "style": 0.10, "mood": 0.04,
                "vocal": 0.02, "theme": 0.01, "lyrics": 0.00, "song_title": 0.00
            }
            log_service.system("🎯 Query intent: ARTIST")

        elif detected_category == "style":
            weights = {
                "style": 0.45, "primary_genre": 0.20, "secondary_genres": 0.10, "mood": 0.12,
                "primary_artist": 0.05, "similar_artists": 0.03, "vocal": 0.03,
                "theme": 0.01, "lyrics": 0.01, "song_title": 0.00
            }
            log_service.system("🎯 Query intent: STYLE")

        elif detected_category == "theme":
            weights = {
                "theme": 0.45, "lyrics": 0.22, "mood": 0.15,
                "primary_genre": 0.06, "secondary_genres": 0.04, "style": 0.04,
                "primary_artist": 0.02, "similar_artists": 0.01, "vocal": 0.01, "song_title": 0.00
            }
            log_service.system("🎯 Query intent: THEME")

        elif detected_category == "vocal":
            weights = {
                "vocal": 0.45, "style": 0.18, "primary_genre": 0.12, "secondary_genres": 0.08,
                "mood": 0.10, "primary_artist": 0.03, "similar_artists": 0.02,
                "theme": 0.01, "lyrics": 0.01, "song_title": 0.00
            }
            log_service.system("🎯 Query intent: VOCAL")

        elif detected_category == "secondary_genres":
            weights = {
                "secondary_genres": 0.50, "primary_genre": 0.25, "mood": 0.10,
                "style": 0.08, "primary_artist": 0.03, "similar_artists": 0.02,
                "vocal": 0.01, "theme": 0.01, "lyrics": 0.00, "song_title": 0.00
            }
            log_service.system("🎯 Query intent: SECONDARY GENRES")

        elif detected_category == "similar_artists":
            weights = {
                "similar_artists": 0.50, "primary_artist": 0.25, "primary_genre": 0.10,
                "secondary_genres": 0.06, "style": 0.06, "mood": 0.02,
                "vocal": 0.01, "theme": 0.00, "lyrics": 0.00, "song_title": 0.00
            }
            log_service.system("🎯 Query intent: SIMILAR ARTISTS")

        elif detected_category == "instrumental":
            weights = {
                "style": 0.35, "primary_genre": 0.25, "secondary_genres": 0.15, "mood": 0.15,
                "primary_artist": 0.05, "similar_artists": 0.03, "theme": 0.01,
                "vocal": 0.01, "lyrics": 0.00, "song_title": 0.00
            }
            log_service.system("🎯 Query intent: INSTRUMENTAL")

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