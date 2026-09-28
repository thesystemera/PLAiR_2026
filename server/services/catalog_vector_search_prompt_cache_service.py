from typing import Optional
from pydantic import BaseModel, Field
from services.base_prompt_cache_service import BasePromptCacheService
from config import settings

class CategoryWeights(BaseModel):
    song_title: float = Field(ge=0.0, le=1.0, description="Weight for specific song title matches")
    primary_genre: float = Field(ge=0.0, le=1.0)
    secondary_genres: float = Field(ge=0.0, le=1.0)
    mood: float = Field(ge=0.0, le=1.0)
    primary_artist: float = Field(ge=0.0, le=1.0)
    similar_artists: float = Field(ge=0.0, le=1.0)
    style: float = Field(ge=0.0, le=1.0)
    theme: float = Field(ge=0.0, le=1.0)
    vocal: float = Field(ge=0.0, le=1.0)
    lyrics: float = Field(ge=0.0, le=1.0)

class QueryIntentAnalysis(BaseModel):
    intent_category: str = Field(
        description="Primary category detected: song, genre, mood, artist, style, theme, vocal, lyrics, or general"
    )
    category_weights: CategoryWeights = Field(
        description="Weight distribution across categories (must sum to 1.0)"
    )
    cleaned_query: str = Field(
        description="Cleaned search terms with category prefixes removed"
    )
    confidence: float = Field(
        description="Confidence score 0.0-1.0 for this analysis",
        ge=0.0,
        le=1.0
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Brief explanation of the analysis (optional, for debugging)"
    )

class CatalogVectorSearchPromptCacheService(BasePromptCacheService):
    table_name = "query_intent_cache"
    stats_task_name = "query_intent_cache_stats"
    ready_message = "✓ Cache Service Ready"
    log_channel = "vector_music"
    analysis_model = QueryIntentAnalysis
    weights_model = CategoryWeights

    def _json_cache_dir(self):
        return settings.QUERY_CACHE_DIR

    def _insert_cache_row(self, conn, params: tuple):
        c = conn.cursor()
        try:
            c.execute("INSERT INTO query_intent_cache VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                      "ON CONFLICT (query_hash) DO NOTHING",
                      params)
            conn.commit()
        except Exception:
            pass

    def _stats_database_entry(self):
        return "database", "ai_radio_embeddings (PostgreSQL)"

    def _build_system_prompt(self) -> str:
        return """You are a music search intent analyzer for an AI-powered radio station.

Your job is to analyze user search queries and determine:
1. What category they're searching for.
2. The optimal weight distribution across ALL 10 categories (must sum to 1.0).
3. Clean search terms with any category prefixes removed.

Categories (10 total):
- song_title: Specific song names or tracks.
- primary_genre: The main musical genre (rock, jazz, hip-hop, etc.).
- secondary_genres: Related sub-genres.
- mood: Emotional feeling (happy, sad, energetic, chill).
- primary_artist: The main artist being searched for.
- similar_artists: Artists with similar sound/style.
- style: Production style, sound design.
- theme: Lyrical themes, topics.
- vocal: Vocal style, delivery.
- lyrics: Specific lyrical content or quotes.

Important Instructions:
- **Dynamic Weighting:** Do NOT use hardcoded presets. Analyze the nuance of the request.
- **Sum to 1.0:** Weights must sum to exactly 1.0.
- **Cleaned Query:** Remove prefixes like "Genre:" or "Play". Keep natural language if relevant.
- **Confidence:** Rate 0.0-1.0 based on query clarity.

Examples of Logic:
- If user asks "Play 'Midnight City'", heavily weight `song_title`.
- If user asks "Fast paced rock music", split weight between `primary_genre` and `mood`.
- If user asks "Something that sounds like Daft Punk", split between `primary_artist` and `similar_artists`.
- If user asks "Songs about space travel", weight `theme` heavily.
"""

    def _build_user_prompt(self, query: str) -> str:
        return f"""Analyze this music search query:

"{query}"

Determine the user's intent, optimal category weights, and cleaned search terms."""

catalog_vector_search_prompt_cache_service = CatalogVectorSearchPromptCacheService()
