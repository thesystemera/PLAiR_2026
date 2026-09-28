import asyncio
import time
import os
import json
import hashlib
import numpy as np
import aiofiles
from datetime import datetime
from typing import Any, Dict, Optional, Tuple, Type
from pydantic import BaseModel
from services.base_service import SingletonService
from database.pg_pool import get_pooled_connection
from models_global import run_on_gpu_executor
from services.task_utils import spawn
from services import log_service
from services import usage_tracking
from services.ai_service import AIService
from services.llm_router import LLM_LIVE
from config import settings


class BasePromptCacheService(SingletonService):
    table_name: str = ""
    stats_task_name: str = ""
    ready_message: str = ""
    log_channel: str = ""
    analysis_model: Type[BaseModel]
    weights_model: Type[BaseModel]

    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.ai_service: Optional[AIService] = None
        self.vector_db_service = None
        self.json_dir = str(self._json_cache_dir())
        self.query_cache: Dict[str, Dict] = {}
        self.exact_hits = 0
        self.semantic_hits = 0
        self.gemini_calls = 0
        self._initialized = True

    def _json_cache_dir(self):
        raise NotImplementedError

    def _log(self, message: str):
        log_service.detail(message, self.log_channel)

    def _insert_cache_row(self, conn, params: tuple):
        raise NotImplementedError

    def _stats_database_entry(self) -> Tuple[str, Any]:
        raise NotImplementedError

    def _build_system_prompt(self) -> str:
        raise NotImplementedError

    def _build_user_prompt(self, query: str) -> str:
        raise NotImplementedError

    def _get_connection(self):
        return get_pooled_connection(settings.EMBEDDINGS_DATABASE_URL)

    def _initialize_database(self):
        conn = self._get_connection()
        c = conn.cursor()
        c.execute(f'''
            CREATE TABLE IF NOT EXISTS {self.table_name} (
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

        self._log(self.ready_message)
        self._log("  📂 Database:   PostgreSQL (ai_radio_embeddings)")
        self._log(f"  📂 JSON Cache: {abs_json_path}")

    def _load_cache_from_disk(self):
        start_time = time.perf_counter()

        conn = self._get_connection()
        c = conn.cursor()
        c.execute("SELECT query_hash, query_text, embedding, intent_category, weights_json, "
                  "confidence, cleaned_query, reasoning, created_at, times_reused, last_used "
                  f"FROM {self.table_name}")

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
        getattr(log_service, self.log_channel)(
            f"✓ Loaded {len(self.query_cache)} cached queries ({elapsed:.2f}s)"
        )

    @staticmethod
    def _hash_query(query: str) -> str:
        return hashlib.md5(query.lower().strip().encode()).hexdigest()

    async def analyze_query(
            self,
            query: str,
            use_cache: bool = True,
            similarity_threshold: float = 0.95
    ):

        if not query or not query.strip():
            return None

        query = query.strip()
        query_hash = self._hash_query(query)

        self._log(f"🧠 Checking Intent Cache for: '{query}'")

        if use_cache and query_hash in self.query_cache:
            cached = self.query_cache[query_hash]
            self._update_cache_stats(query_hash, "exact")
            self.exact_hits += 1

            self._log(
                f"  ✅ [CACHE HIT - EXACT] Reusing analysis for '{query}'\n"
                f"     → Intent: {cached['intent_category']} | Reused: {cached['times_reused']}x"
            )

            return self._cached_to_analysis(cached)

        if use_cache and self.vector_db_service:
            query_embedding = await run_on_gpu_executor(self.vector_db_service._generate_embedding, query)

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

                    self._log(
                        f"  ✅ [CACHE HIT - SEMANTIC] Found similar query: '{cached['query_text']}'\n"
                        f"     → Similarity: {best_similarity:.4f} (>= Threshold {similarity_threshold})\n"
                        f"     → Intent: {cached['intent_category']}"
                    )
                    return self._cached_to_analysis(cached)
                else:
                    self._log(
                        f"  ⚠️ [CACHE NEAR-MISS] Closest match: '{cached['query_text']}' ({best_similarity:.4f})\n"
                        f"     → Ignored because score < {similarity_threshold}"
                    )

        self.gemini_calls += 1
        self._log(f"  🤖 [CACHE MISS - NEW LLM CALL] Sending '{query}' to Gemini...")

        analysis = await self._call_gemini_for_analysis(query)

        if analysis and self.vector_db_service:
            await self._save_to_cache(query, analysis)
            self._log_cache_performance()

        return analysis

    def _cached_to_analysis(self, cached: Dict):
        return self.analysis_model(
            intent_category=cached["intent_category"],
            category_weights=self.weights_model(**cached["weights"]),
            cleaned_query=cached["cleaned_query"],
            confidence=cached["confidence"],
            reasoning=cached.get("reasoning")
        )

    def _update_cache_stats(self, query_hash: str, _match_type: str):
        usage_tracking.record_cache_hit(self.table_name)
        if query_hash in self.query_cache:
            cached = self.query_cache[query_hash]
            cached["times_reused"] += 1
            cached["last_used"] = time.time()

            spawn(
                asyncio.to_thread(self._persist_cache_stats, query_hash, cached["times_reused"], cached["last_used"]),
                name=self.stats_task_name
            )

    def _persist_cache_stats(self, query_hash: str, times_reused: int, last_used: float):
        conn = self._get_connection()
        try:
            c = conn.cursor()
            c.execute(f"UPDATE {self.table_name} SET times_reused = %s, last_used = %s "
                      "WHERE query_hash = %s",
                      (times_reused, last_used, query_hash))
            conn.commit()
        finally:
            conn.close()

    async def _save_to_cache(self, query: str, analysis):
        query_hash = self._hash_query(query)

        if self.vector_db_service:
            query_embedding = await run_on_gpu_executor(self.vector_db_service._generate_embedding, query)
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

        params = (query_hash, query, query_embedding.tobytes(), analysis.intent_category,
                  weights_json, analysis.confidence, analysis.cleaned_query,
                  analysis.reasoning, current_time, 0, current_time)

        def _insert():
            conn = self._get_connection()
            try:
                self._insert_cache_row(conn, params)
            finally:
                conn.close()

        await asyncio.to_thread(_insert)

        await self._save_json_backup(query, analysis, query_hash)

        self._log(
            f"  💾 Cached new analysis for '{query}' (Category: {analysis.intent_category})"
        )

    async def _save_json_backup(self, query: str, analysis, query_hash: str):
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
            self._log(f"  📄 Saved JSON backup: {filepath}")
        except Exception as e:
            log_service.warning(f"Failed to save JSON cache backup: {e}")

    def _log_cache_performance(self):
        total = self.exact_hits + self.semantic_hits + self.gemini_calls
        if total > 0 and total % 5 == 0:
            hit_rate = ((self.exact_hits + self.semantic_hits) / total) * 100
            self._log(
                f"📊 Cache Stats: {hit_rate:.1f}% Hit Rate "
                f"(Exact: {self.exact_hits}, Semantic: {self.semantic_hits}, LLM Calls: {self.gemini_calls})"
            )

    async def _call_gemini_for_analysis(self, query: str):
        if not self.ai_service or not self.ai_service.gemini_configured:
            log_service.error("AI service not configured for query analysis")
            return None

        system_instruction = self._build_system_prompt()
        user_prompt = self._build_user_prompt(query)

        try:
            result = await self.ai_service.call_gemini_structured(
                prompt=user_prompt,
                response_schema=self.analysis_model,
                model=settings.GEMINI_DJ_MODEL,
                temperature=0.3,
                system_instruction=system_instruction,
                role=LLM_LIVE
            )

            if result:
                return self.analysis_model(**result)

            return None

        except Exception as e:
            log_service.error(f"Gemini query analysis error: {str(e)}")
            return None

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

        database_key, database_value = self._stats_database_entry()

        return {
            "total_cached_queries": len(self.query_cache),
            "exact_hits": self.exact_hits,
            "semantic_hits": self.semantic_hits,
            "gemini_calls": self.gemini_calls,
            "exact_hit_rate": exact_rate,
            "semantic_hit_rate": semantic_rate,
            "combined_hit_rate": combined_rate,
            "popular_queries": popular_queries,
            database_key: database_value,
            "json_dir": self.json_dir
        }
