import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from models_global import run_on_gpu_executor
from services import log_service
from services.catalog_vocals import VOCALS, vocals_of
from services.catalog_vector_database_service import TAG_LISTS, CatalogVectorDatabaseService
from services.semantic_source import SemanticSearch

QUERY_PREFIXES = {
    "song": "song_title", "track": "song_title", "title": "song_title",
    "genre": "primary_genre",
    "subgenre": "secondary_genres", "sub-genre": "secondary_genres", "secondary genre": "secondary_genres",
    "mood": "mood", "vibe": "mood",
    "artist": "primary_artist",
    "similar artist": "similar_artists", "similar artists": "similar_artists",
    "style": "style", "theme": "theme", "lyrics": "lyrics", "vocal": "vocal",
    "instrumental": "vocal",
}
LABEL_CATEGORIES = set(TAG_LISTS) | {"primary_genre", "primary_artist"}
NAME_CATEGORIES = {"primary_artist", "similar_artists"}
NATURAL_PHRASES = (
    ("secondary_genres", ("subgenre", "subgenres", "sub-genre", "sub-genres", "secondary genre")),
    ("primary_genre", ("genre", "genres", "type of music", "kind of music", "music like")),
    ("mood", ("mood", "vibe", "vibes", "feeling", "atmosphere", "energy")),
    ("similar_artists", ("similar artist", "similar artists", "sounds like")),
    ("primary_artist", ("artist", "artists", "band", "singer", "musician", "similar to", "like", "reminds me of")),
    ("style", ("style", "production", "sound", "produced", "recorded")),
    ("theme", ("theme", "about", "story", "narrative", "lyrics about", "message", "meaning")),
    ("vocal", ("vocal", "vocals", "voice", "sung", "singing")),
    ("song_title", ("song", "track", "title", "named", "called", "play the song", "play the track")),
    ("vocal", ("instrumental", "instrumentals", "no vocals", "without vocals")),
)


@dataclass
class TagIndex:
    metas: List[Dict[str, Any]]
    rows: np.ndarray
    starts: np.ndarray
    counts: np.ndarray
    vectors: np.ndarray


def _unit(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return np.divide(vectors, norms, out=np.zeros_like(vectors), where=norms > 0)


class CatalogVectorSearchService:

    def __init__(self, vector_db_service, catalog_service=None, prompt_cache_service=None):
        self.vector_db = vector_db_service
        self.catalog = catalog_service
        self.prompt_cache_service = prompt_cache_service
        self.semantic = SemanticSearch(vector_db_service, prompt_cache_service)
        self._tag_source = None
        self._tag_indexes: Dict[str, Optional[TagIndex]] = {}
        log_service.vector_music("✓ CatalogVectorSearchService initialized")

    async def search(
            self,
            query: str,
            n_results: int = 10,
            use_ai_analysis: bool = False,
            banned_ids: Optional[set] = None,
            only_ids: Optional[set] = None,
            vocals: Optional[str] = None
    ) -> List[Dict[str, Any]]:

        if not self.catalog or not self.catalog.tracks:
            log_service.warning("No catalog available for search")
            return []

        try:
            log_service.detail(f"🔍 Searching: '{query}'", "vector_music")
            intent_category, query_weights, cleaned_query, understood = await self._intent(query, use_ai_analysis)
            log_service.detail(f"  📊 Category weights: {query_weights}", "vector_music")
            wanted = next((v for v in (vocals, understood.get("vocals")) if v in VOCALS), None)
            if wanted:
                log_service.detail(f"  🎚️ Vocals filter: {wanted}", "vector_music")

            hidden = self.catalog.hidden_ids if self.catalog is not None else set()

            def keep(track: Dict[str, Any]) -> bool:
                if banned_ids and track.get("id") in banned_ids:
                    return False
                if only_ids is not None and track.get("id") not in only_ids:
                    return False
                if track.get("id") in hidden:
                    return False
                if wanted and vocals_of(track) != wanted:
                    return False
                return True

            if set(query_weights) == {intent_category}:
                texts = cleaned_query.split(",") if intent_category in LABEL_CATEGORIES else [cleaned_query]
                found = await self.near_texts(texts, intent_category, n_results, keep=keep)
            else:
                found = [{**match.meta, 'similarity_score': match.similarity} for match in
                         await self.semantic.search(cleaned_query, n=n_results, keep=keep, weights=query_weights)]
            track_results = [{**track, 'intent_category': intent_category, 'match_weights': query_weights}
                             for track in found]

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

    async def _intent(self, query: str, use_ai_analysis: bool) -> Tuple[str, Dict[str, float], str, Dict[str, Any]]:
        if use_ai_analysis and self.prompt_cache_service:
            ai_analysis = await self.prompt_cache_service.analyze_query(query)
            if ai_analysis:
                log_service.detail(f"🤖 AI Intent: {ai_analysis.intent_category} "
                                   f"(confidence: {ai_analysis.confidence:.2f})", "vector_music")
                filters = {"vocals": getattr(ai_analysis, "vocals", None)}
                return (ai_analysis.intent_category, ai_analysis.category_weights.model_dump(),
                        ai_analysis.cleaned_query, filters)
            log_service.warning("AI analysis failed, falling back to keyword detection")
        intent, weights, cleaned = self._detect_query_intent(query)
        return intent, weights, cleaned if cleaned.strip() else query, {}

    def _tag_index(self, category: str) -> Optional[TagIndex]:
        source = self.vector_db._metadata_cache
        if self._tag_source is not source:
            self._tag_source, self._tag_indexes = source, {}
        if category not in self._tag_indexes:
            metas = list(source.values())
            rows, starts, texts = [], [], []
            for row, meta in enumerate(metas):
                tags = self.vector_db.category_tags(meta).get(category) or []
                if tags:
                    rows.append(row)
                    starts.append(len(texts))
                    texts.extend(tags)
            if not texts:
                self._tag_indexes[category] = None
                return None
            vectors = _unit(np.array(self.vector_db.ensure_embeddings(category, texts), dtype=np.float32))
            counts = np.diff(np.array(starts + [len(texts)]))
            self._tag_indexes[category] = TagIndex(metas, np.array(rows), np.array(starts), counts, vectors)
        return self._tag_indexes[category]

    def _tag_scores(self, category: str, anchors: List[List[str]]) -> Optional[np.ndarray]:
        index = self._tag_index(category)
        anchors = [tags for tags in anchors if tags]
        if index is None or not anchors:
            return None
        total = np.zeros(len(index.rows), dtype=np.float32)
        for tags in anchors:
            anchor = _unit(np.array(self.vector_db.ensure_embeddings(category, tags), dtype=np.float32))
            similarity = anchor @ index.vectors.T
            forward = np.maximum.reduceat(similarity, index.starts, axis=1).mean(axis=0)
            backward = np.add.reduceat(similarity.max(axis=0), index.starts) / index.counts
            total += (forward + backward) / 2
        scores = np.full(len(index.metas), np.nan, dtype=np.float32)
        scores[index.rows] = total / len(anchors)
        return scores

    def _rank_tags(self, categories: List[str], anchors_for: Callable[[str], List[List[str]]], n_results: int,
                   allowed: Callable[[Dict[str, Any]], bool]) -> List[Dict[str, Any]]:
        per_category = [scores for scores in (self._tag_scores(c, anchors_for(c)) for c in categories)
                        if scores is not None]
        if not per_category:
            return []
        metas = list(self._tag_source.values())
        stacked = np.vstack(per_category)
        present = (~np.isnan(stacked)).sum(axis=0)
        scores = np.where(present > 0, np.nansum(stacked, axis=0) / np.maximum(present, 1), np.nan)
        ranked = [(float(scores[i]), meta) for i, meta in enumerate(metas)
                  if not np.isnan(scores[i]) and allowed(meta)]
        ranked.sort(key=lambda row: row[0], reverse=True)
        return [{**meta, 'similarity_score': score} for score, meta in ranked[:n_results]]

    def _rank_names(self, category: str, anchors: List[Dict[str, List[str]]], n_results: int,
                    allowed: Callable[[Dict[str, Any]], bool]) -> List[Dict[str, Any]]:
        def names(tags: Dict[str, List[str]], key: str) -> set:
            return {name.lower() for name in tags.get(key) or []}

        artists = set().union(*(names(t, "primary_artist") for t in anchors))
        similar = set().union(*(names(t, "similar_artists") for t in anchors)) - artists
        ranked = []
        for meta in self.vector_db._metadata_cache.values():
            if not allowed(meta):
                continue
            tags = self.vector_db.category_tags(meta)
            own, scene = names(tags, "primary_artist"), names(tags, "similar_artists")
            if category == "similar_artists" and own & artists:
                continue
            key = (bool(own & similar), len(scene & similar) / len(similar) if similar else 0.0)
            if category == "primary_artist":
                key = (bool(own & artists),) + key
            if any(key):
                ranked.append((key, meta))
        ranked.sort(key=lambda row: row[0], reverse=True)
        return [{**meta, 'similarity_score': float(sum(key) / len(key))} for key, meta in ranked[:n_results]]

    def _categories(self, category: str) -> List[str]:
        if category == "all":
            return [c for c in self.vector_db.categories if c != "song_title"]
        return [category]

    def _allowed(self, exclude_ids: set, banned_ids: Optional[set],
                 keep: Optional[Callable[[Dict[str, Any]], bool]]) -> Callable[[Dict[str, Any]], bool]:
        hidden = self.catalog.hidden_ids

        def allowed(track: Dict[str, Any]) -> bool:
            track_id = track.get("id")
            return (track_id not in exclude_ids and track_id not in hidden
                    and not (banned_ids and track_id in banned_ids) and (keep is None or keep(track)))
        return allowed

    async def similar(self, tracks: List[Dict[str, Any]], category: str, n_results: int,
                      banned_ids: Optional[set] = None,
                      keep: Optional[Callable[[Dict[str, Any]], bool]] = None) -> List[Dict[str, Any]]:
        if not tracks or not self.catalog or not self.catalog.tracks:
            return []
        allowed = self._allowed({t.get("id") for t in tracks}, banned_ids, keep)
        tags = [self.vector_db.category_tags(t) for t in tracks]

        def run():
            if category in NAME_CATEGORIES:
                return self._rank_names(category, tags, n_results, allowed)
            return self._rank_tags(self._categories(category), lambda c: [t.get(c) or [] for t in tags],
                                   n_results, allowed)

        return await run_on_gpu_executor(run)

    async def near_texts(self, texts: List[str], category: str, n_results: int,
                         banned_ids: Optional[set] = None,
                         keep: Optional[Callable[[Dict[str, Any]], bool]] = None) -> List[Dict[str, Any]]:
        texts = [text.strip() for text in texts if text and text.strip()]
        if not texts or not self.catalog or not self.catalog.tracks:
            return []
        allowed = self._allowed(set(), banned_ids, keep)

        def run():
            return self._rank_tags([category], lambda _: [texts], n_results, allowed)

        return await run_on_gpu_executor(run)

    async def warm(self) -> None:
        for category in self.vector_db.categories:
            await run_on_gpu_executor(self._tag_index, category)

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
        head, sep, rest = query.partition(":")
        category = QUERY_PREFIXES.get(head.strip().lower()) if sep else None
        cleaned_query = rest.strip() if category else query

        if category is None:
            query_lower = query.lower()
            for category_name, phrases in NATURAL_PHRASES:
                if re.search(r'(' + '|'.join(phrases) + r')', query_lower):
                    category = category_name
                    cleaned_query = self._clean_natural_query(query, list(phrases))
                    break

        log_service.detail(f"🎯 Query intent: {category or 'general'}", "vector_music")
        return category or "general", CatalogVectorDatabaseService.weights_for(category or ""), cleaned_query

    async def add_track(self, track_id: str):
        log_service.vector_music(f"New track {track_id} added - background task will rebuild index")