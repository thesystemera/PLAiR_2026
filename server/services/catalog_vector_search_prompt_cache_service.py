import time
import os
import json
import psycopg2
import hashlib
import numpy as np
import aiofiles
from datetime import datetime
from typing import Dict, Optional
from pydantic import BaseModel, Field
from services.base_service import SingletonService
from services import log_service
from services.ai_service import AIService
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

class CatalogVectorSearchPromptCacheService(SingletonService):
    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.ai_service: Optional[AIService] = None
        self.vector_db_service = None
        self.json_dir = str(settings.QUERY_CACHE_DIR)
        self.query_cache: Dict[str, Dict] = {}
        self.exact_hits = 0
        self.semantic_hits = 0
        self.gemini_calls = 0
        self._initialized = True

    def _get_connection(self):
        return psycopg2.connect(settings.EMBEDDINGS_DATABASE_URL)

    def _initialize_database(self):
        conn = self._get_connection()
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS query_intent_cache (
                query_hash TEXT PRIMARY KEY,
                query_text TEXT,
                embedding BYTEA,
                intent_category TEXT,
                weights_json TEXT,
                confidence REAL,
                cleaned_query TEXT,
                reasoning TEXT,
                created_at REAL,
                times_reused INTEGER DEFAULT 0,
                last_used REAL
            )
        ''')
        conn.commit()
        conn.close()

    async def initialize(self, ai_service: AIService, vector_db_service):
        self.ai_service = ai_service
        self.vector_db_service = vector_db_service

        os.makedirs(self.json_dir, exist_ok=True)

        self._initialize_database()
        self._load_cache_from_disk()

        abs_json_path = os.path.abspath(self.json_dir)

        log_service.vector_music("✓ Cache Service Ready")
        log_service.vector_music("  📂 Database:   PostgreSQL (ai_radio_embeddings)")
        log_service.vector_music(f"  📂 JSON Cache: {abs_json_path}")

    def _load_cache_from_disk(self):
        start_time = time.perf_counter()

        conn = self._get_connection()
        c = conn.cursor()
        c.execute("SELECT query_hash, query_text, embedding, intent_category, weights_json, "
                  "confidence, cleaned_query, reasoning, created_at, times_reused, last_used "
                  "FROM query_intent_cache")

        rows = c.fetchall()
        conn.close()

        for row in rows:
            query_hash, query_text, embedding_blob, intent_category, weights_json, \
                confidence, cleaned_query, reasoning, created_at, times_reused, last_used = row

            embedding = np.frombuffer(bytes(embedding_blob), dtype=np.float32)
            weights = json.loads(weights_json)

            self.query_cache[query_hash] = {
                "query_text": query_text,
                "embedding": embedding,
                "intent_category": intent_category,
                "weights": weights,
                "confidence": confidence,
                "cleaned_query": cleaned_query,
                "reasoning": reasoning,
                "created_at": created_at,
                "times_reused": times_reused,
                "last_used": last_used
            }

        elapsed = time.perf_counter() - start_time
        log_service.vector_music(
            f"✓ Loaded {len(self.query_cache)} cached queries ({elapsed:.2f}s)"
        )

    def _hash_query(self, query: str) -> str:
        return hashlib.md5(query.lower().strip().encode()).hexdigest()

    async def analyze_query(
            self,
            query: str,
            use_cache: bool = True,
            similarity_threshold: float = 0.95
    ) -> Optional[QueryIntentAnalysis]:

        if not query or not query.strip():
            return None

        query = query.strip()
        query_hash = self._hash_query(query)

        log_service.vector_music(f"🧠 Checking Intent Cache for: '{query}'")

        if use_cache and query_hash in self.query_cache:
            cached = self.query_cache[query_hash]
            self._update_cache_stats(query_hash, "exact")
            self.exact_hits += 1

            log_service.vector_music(
                f"  ✅ [CACHE HIT - EXACT] Reusing analysis for '{query}'\n"
                f"     → Intent: {cached['intent_category']} | Reused: {cached['times_reused']}x"
            )

            return self._cached_to_analysis(cached)

        if use_cache and self.vector_db_service:
            query_embedding = self.vector_db_service._generate_embedding(query)

            best_match = None
            best_similarity = 0.0

            for cache_hash, cached in self.query_cache.items():
                similarity = float(np.dot(query_embedding, cached["embedding"]))
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = (cache_hash, cached)

            if best_match:
                cache_hash, cached = best_match

                if best_similarity >= similarity_threshold:
                    self._update_cache_stats(cache_hash, "semantic")
                    self.semantic_hits += 1

                    log_service.vector_music(
                        f"  ✅ [CACHE HIT - SEMANTIC] Found similar query: '{cached['query_text']}'\n"
                        f"     → Similarity: {best_similarity:.4f} (>= Threshold {similarity_threshold})\n"
                        f"     → Intent: {cached['intent_category']}"
                    )
                    return self._cached_to_analysis(cached)
                else:
                    log_service.vector_music(
                        f"  ⚠️ [CACHE NEAR-MISS] Closest match: '{cached['query_text']}' ({best_similarity:.4f})\n"
                        f"     → Ignored because score < {similarity_threshold}"
                    )

        self.gemini_calls += 1
        log_service.vector_music(f"  🤖 [CACHE MISS - NEW LLM CALL] Sending '{query}' to Gemini...")

        analysis = await self._call_gemini_for_analysis(query)

        if analysis and self.vector_db_service:
            await self._save_to_cache(query, analysis)
            self._log_cache_performance()

        return analysis

    def _cached_to_analysis(self, cached: Dict) -> QueryIntentAnalysis:
        return QueryIntentAnalysis(
            intent_category=cached["intent_category"],
            category_weights=CategoryWeights(**cached["weights"]),
            cleaned_query=cached["cleaned_query"],
            confidence=cached["confidence"],
            reasoning=cached.get("reasoning")
        )

    def _update_cache_stats(self, query_hash: str, _match_type: str):
        if query_hash in self.query_cache:
            cached = self.query_cache[query_hash]
            cached["times_reused"] += 1
            cached["last_used"] = time.time()

            conn = self._get_connection()
            c = conn.cursor()
            c.execute("UPDATE query_intent_cache SET times_reused = %s, last_used = %s "
                      "WHERE query_hash = %s",
                      (cached["times_reused"], cached["last_used"], query_hash))
            conn.commit()
            conn.close()

    async def _save_to_cache(self, query: str, analysis: QueryIntentAnalysis):
        query_hash = self._hash_query(query)

        if self.vector_db_service:
            query_embedding = self.vector_db_service._generate_embedding(query)
        else:
            return

        weights_dict = analysis.category_weights.model_dump()
        weights_json = json.dumps(weights_dict)
        current_time = time.time()

        self.query_cache[query_hash] = {
            "query_text": query,
            "embedding": query_embedding,
            "intent_category": analysis.intent_category,
            "weights": weights_dict,
            "confidence": analysis.confidence,
            "cleaned_query": analysis.cleaned_query,
            "reasoning": analysis.reasoning,
            "created_at": current_time,
            "times_reused": 0,
            "last_used": current_time
        }

        conn = self._get_connection()
        c = conn.cursor()
        try:
            c.execute("INSERT INTO query_intent_cache VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                      "ON CONFLICT (query_hash) DO NOTHING",
                      (query_hash, query, query_embedding.tobytes(), analysis.intent_category,
                       weights_json, analysis.confidence, analysis.cleaned_query,
                       analysis.reasoning, current_time, 0, current_time))
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

        await self._save_json_backup(query, analysis, query_hash)

        log_service.vector_music(
            f"  💾 Cached new analysis for '{query}' (Category: {analysis.intent_category})"
        )

    async def _save_json_backup(self, query: str, analysis: QueryIntentAnalysis, query_hash: str):
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{timestamp}_{query_hash[:8]}.json"
        filepath = os.path.join(self.json_dir, filename)

        data = {
            "timestamp": timestamp,
            "query": query,
            "query_hash": query_hash,
            "analysis": {
                "intent_category": analysis.intent_category,
                "category_weights": analysis.category_weights.model_dump(),
                "cleaned_query": analysis.cleaned_query,
                "confidence": analysis.confidence,
                "reasoning": analysis.reasoning
            }
        }

        try:
            async with aiofiles.open(filepath, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(data, indent=2, ensure_ascii=False))
            log_service.vector_music(f"  📄 Saved JSON backup: {filepath}")
        except Exception as e:
            log_service.warning(f"Failed to save JSON cache backup: {e}")

    def _log_cache_performance(self):
        total = self.exact_hits + self.semantic_hits + self.gemini_calls
        if total > 0 and total % 5 == 0:
            hit_rate = ((self.exact_hits + self.semantic_hits) / total) * 100
            log_service.vector_music(
                f"📊 Cache Stats: {hit_rate:.1f}% Hit Rate "
                f"(Exact: {self.exact_hits}, Semantic: {self.semantic_hits}, LLM Calls: {self.gemini_calls})"
            )

    async def _call_gemini_for_analysis(self, query: str) -> Optional[QueryIntentAnalysis]:
        if not self.ai_service or not self.ai_service.gemini_configured:
            log_service.error("AI service not configured for query analysis")
            return None

        system_instruction = self._build_system_prompt()
        user_prompt = self._build_user_prompt(query)

        try:
            result = await self.ai_service.call_gemini_structured(
                prompt=user_prompt,
                response_schema=QueryIntentAnalysis,
                model=settings.GEMINI_DJ_MODEL,
                temperature=0.3,
                system_instruction=system_instruction
            )

            if result:
                return QueryIntentAnalysis(**result)

            return None

        except Exception as e:
            log_service.error(f"Gemini query analysis error: {str(e)}")
            return None

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

    def get_cache_stats(self) -> Dict:
        total_requests = self.exact_hits + self.semantic_hits + self.gemini_calls
        exact_rate = (self.exact_hits / total_requests * 100) if total_requests > 0 else 0
        semantic_rate = (self.semantic_hits / total_requests * 100) if total_requests > 0 else 0
        combined_rate = exact_rate + semantic_rate

        popular_queries = sorted(
            [(v["query_text"], v["times_reused"]) for v in self.query_cache.values()],
            key=lambda x: x[1],
            reverse=True
        )[:10]

        return {
            "total_cached_queries": len(self.query_cache),
            "exact_hits": self.exact_hits,
            "semantic_hits": self.semantic_hits,
            "gemini_calls": self.gemini_calls,
            "exact_hit_rate": exact_rate,
            "semantic_hit_rate": semantic_rate,
            "combined_hit_rate": combined_rate,
            "popular_queries": popular_queries,
            "database": "ai_radio_embeddings (PostgreSQL)",
            "json_dir": self.json_dir
        }

catalog_vector_search_prompt_cache_service = CatalogVectorSearchPromptCacheService()