import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from models_global import run_on_gpu_executor
from services import log_service
from services.catalog_aspects import Aspect, AspectRanker
from services.catalog_names import NameLookup
from services.catalog_vocals import VOCALS, vocals_of
from services.catalog_vector_database_service import CatalogVectorDatabaseService
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
NAME_LOOKUPS = {"primary_artist": "artist", "song_title": "title"}
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


class CatalogVectorSearchService:
    """The catalog's three ways in: free-text search (AI or keyword weights over every aspect), name lookup
    (artists and titles by spelling, catalog_names) and stations built from aspects (catalog_aspects)."""

    def __init__(self, vector_db_service, catalog_service=None, prompt_cache_service=None):
        self.vector_db = vector_db_service
        self.catalog = catalog_service
        self.prompt_cache_service = prompt_cache_service
        self.semantic = SemanticSearch(vector_db_service, prompt_cache_service)
        self.names = NameLookup(vector_db_service)
        self.aspects = AspectRanker(vector_db_service, self.names)
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

            def keep(track: Dict[str, Any]) -> bool:
                if only_ids is not None and track.get("id") not in only_ids:
                    return False
                return not (wanted and vocals_of(track) != wanted)

            allowed = self.allowed(set(), banned_ids, keep)
            if set(query_weights) == {intent_category} and intent_category in NAME_LOOKUPS:
                found = await run_on_gpu_executor(self.names.find, NAME_LOOKUPS[intent_category], cleaned_query,
                                                  n_results, allowed)
            elif set(query_weights) == {intent_category}:
                found = await run_on_gpu_executor(self.aspects.rank, [Aspect(intent_category, words=cleaned_query)],
                                                  None, [], n_results, allowed)
            else:
                found = [{**match.meta, 'similarity_score': match.similarity} for match in
                         await self.semantic.search(cleaned_query, n=n_results, keep=allowed, weights=query_weights)]
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

    def allowed(self, exclude_ids: set, banned_ids: Optional[set],
                keep: Optional[Callable[[Dict[str, Any]], bool]] = None) -> Callable[[Dict[str, Any]], bool]:
        hidden = self.catalog.hidden_ids

        def allowed(track: Dict[str, Any]) -> bool:
            track_id = track.get("id")
            return (track_id not in exclude_ids and track_id not in hidden
                    and not (banned_ids and track_id in banned_ids) and (keep is None or keep(track)))
        return allowed

    async def station(self, aspects: List[Aspect], seed: Optional[Dict[str, Any]], recent: List[Dict[str, Any]],
                      n_results: int, banned_ids: Optional[set] = None,
                      keep: Optional[Callable[[Dict[str, Any]], bool]] = None) -> List[Dict[str, Any]]:
        if not aspects or not self.catalog or not self.catalog.tracks:
            return []
        exclude = {t.get("id") for t in recent} | ({seed.get("id")} if seed else set())
        return await run_on_gpu_executor(self.aspects.rank, aspects, seed, recent, n_results,
                                         self.allowed(exclude, banned_ids, keep))

    async def closest_names(self, field: str, text: str, how_many: int) -> List[Tuple[str, float]]:
        return await run_on_gpu_executor(self.names.closest, field, text, how_many)

    async def warm(self) -> None:
        await run_on_gpu_executor(self.aspects.warm)

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
                if re.search(r'\b(' + '|'.join(phrases) + r')\b', query_lower):
                    category = category_name
                    cleaned_query = self._clean_natural_query(query, list(phrases))
                    break

        log_service.detail(f"🎯 Query intent: {category or 'general'}", "vector_music")
        return category or "general", CatalogVectorDatabaseService.weights_for(category or ""), cleaned_query

    async def add_track(self, track_id: str):
        log_service.vector_music(f"New track {track_id} added - background task will rebuild index")