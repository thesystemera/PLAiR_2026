"""
Context Router Service (The "Producer")

This service analyzes user input and determines which context nodes are needed.
Uses Gemini Flash Lite (fast, cheap) with PostgreSQL caching.

This is the brain that makes the node system efficient - it only requests what's needed.
"""

import time
import os
import json
import psycopg2
import hashlib
import numpy as np
import aiofiles
from datetime import datetime
from typing import List, Optional, Dict
from pydantic import BaseModel, Field

from services.base_service import SingletonService
from services import log_service
from services_radio.context_node_registry import node_registry
from config.settings import settings

class NodeSelection(BaseModel):
    selected_nodes: List[str] = Field(
        description="List of node keys to fetch, in order of importance"
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Brief explanation of why these nodes were selected"
    )
    confidence: float = Field(
        default=1.0,
        description="Confidence score 0.0-1.0",
        ge=0.0,
        le=1.0
    )

class ContextRouterService(SingletonService):
    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.ai_service = None
        self.vector_db_service = None
        self.json_dir = str(settings.CONTEXT_ROUTING_CACHE_DIR)
        self.route_cache: Dict[str, Dict] = {}

        self.exact_hits = 0
        self.semantic_hits = 0
        self.llm_calls = 0

        self._initialized = True

    def _get_connection(self):
        return psycopg2.connect(settings.EMBEDDINGS_DATABASE_URL)

    async def initialize(self, ai_service, vector_db_service=None):
        self.ai_service = ai_service
        self.vector_db_service = vector_db_service

        os.makedirs(self.json_dir, exist_ok=True)

        self._initialize_database()
        self._load_cache_from_disk()

        log_service.node_producer("✓ Context Router Service (Producer AI) initialized")
        log_service.node_producer("  📂 Database:   PostgreSQL (ai_radio_embeddings)")
        log_service.node_producer(f"  📂 JSON Cache: {self.json_dir}")

    def _initialize_database(self):
        conn = self._get_connection()
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS context_routing_cache (
                input_hash TEXT PRIMARY KEY,
                user_input TEXT,
                embedding BYTEA,
                selected_nodes TEXT,
                reasoning TEXT,
                confidence REAL,
                created_at REAL,
                times_reused INTEGER DEFAULT 0,
                last_used REAL
            )
        ''')
        conn.commit()
        conn.close()

    def _load_cache_from_disk(self):
        start_time = time.perf_counter()

        try:
            conn = self._get_connection()
            c = conn.cursor()
            c.execute("SELECT input_hash, user_input, embedding, selected_nodes, reasoning, "
                      "confidence, created_at, times_reused, last_used "
                      "FROM context_routing_cache")

            rows = c.fetchall()
            conn.close()

            for row in rows:
                input_hash, user_input, embedding_blob, selected_nodes_json, reasoning, \
                    confidence, created_at, times_reused, last_used = row

                if embedding_blob:
                    embedding = np.frombuffer(bytes(embedding_blob), dtype=np.float32)
                else:
                    embedding = None

                selected_nodes = json.loads(selected_nodes_json)

                self.route_cache[input_hash] = {
                    "user_input": user_input,
                    "embedding": embedding,
                    "selected_nodes": selected_nodes,
                    "reasoning": reasoning,
                    "confidence": confidence,
                    "created_at": created_at,
                    "times_reused": times_reused,
                    "last_used": last_used
                }

            elapsed = time.perf_counter() - start_time
            log_service.node_producer(
                f"✓ Loaded {len(self.route_cache)} cached routing decisions ({elapsed:.2f}s)"
            )
        except Exception as e:
            log_service.error(f"[PRODUCER] Failed to load cache: {e}")

    @staticmethod
    def _hash_input(user_input: str) -> str:
        return hashlib.md5(user_input.lower().strip().encode()).hexdigest()

    async def determine_nodes(
        self,
        user_input: str,
        use_cache: bool = True,
        similarity_threshold: Optional[float] = None
    ) -> List[str]:
        if similarity_threshold is None:
            similarity_threshold = settings.GEMINI_NODE_PRODUCER_SIMILARITY_THRESHOLD

        if not user_input or not user_input.strip():
            return ["core_dj_identity", "station_capabilities", "format_channels", "format_tone", "format_meta_tags_guide"]

        user_input = user_input.strip()
        input_hash = self._hash_input(user_input)

        log_service.node_producer(f"🧠 Analyzing: '{user_input[:60]}...'")

        if use_cache and input_hash in self.route_cache:
            cached = self.route_cache[input_hash]
            self._update_cache_stats(input_hash)
            self.exact_hits += 1

            log_service.node_producer(
                f"  ✅ CACHE HIT (Exact) - Reused {cached['times_reused']}x → {cached['selected_nodes']}"
            )

            return cached['selected_nodes']

        if use_cache and self.vector_db_service:
            input_embedding = self.vector_db_service._generate_embedding(user_input)

            best_match = None
            best_similarity = 0.0

            for cache_hash, cached in self.route_cache.items():
                if cached["embedding"] is not None:
                    similarity = float(np.dot(input_embedding, cached["embedding"]))
                    if similarity > best_similarity:
                        best_similarity = similarity
                        best_match = (cache_hash, cached)

            if best_match and best_similarity >= similarity_threshold:
                cache_hash, cached = best_match
                self._update_cache_stats(cache_hash)
                self.semantic_hits += 1

                log_service.node_producer(
                    f"  ✅ CACHE HIT (Semantic {best_similarity:.3f}) → {cached['selected_nodes']}"
                )

                return cached['selected_nodes']

        self.llm_calls += 1
        log_service.node_producer("  🤖 CACHE MISS - Calling Producer AI...")

        selection, system_prompt, user_prompt = await self._call_producer_ai(user_input)

        if selection:
            await self._save_to_cache(user_input, selection, system_prompt, user_prompt)
            self._log_cache_performance()
            log_service.node_producer(f"  📌 Selected {len(selection.selected_nodes)} nodes → {selection.selected_nodes}")
            return selection.selected_nodes

        return ["core_dj_identity", "station_capabilities", "format_channels", "format_tone", "format_meta_tags_guide"]

    async def _call_producer_ai(self, user_input: str) -> tuple[NodeSelection | None, str, str]:
        if not self.ai_service:
            log_service.error("[PRODUCER] AI service not configured")
            return None, "", ""

        system_prompt = self._build_producer_prompt()
        user_prompt = f"""Analyze this user input and select the MINIMAL set of nodes needed to respond:

User Input: "{user_input}"

Return the selected nodes in order of importance."""

        prompt_chars = len(system_prompt) + len(user_prompt)
        prompt_tokens = prompt_chars // 4

        log_service.node_producer(
            f"  📊 Producer AI Call: {prompt_chars} chars (~{prompt_tokens} tokens) | "
            f"Model: {settings.GEMINI_NODE_PRODUCER_MODEL} | Temp: {settings.GEMINI_NODE_PRODUCER_TEMPERATURE}"
        )

        start_llm = time.perf_counter()
        try:
            result = await self.ai_service.call_gemini_structured(
                prompt=user_prompt,
                response_schema=NodeSelection,
                model=settings.GEMINI_NODE_PRODUCER_MODEL,
                temperature=settings.GEMINI_NODE_PRODUCER_TEMPERATURE,
                system_instruction=system_prompt
            )
            llm_time = (time.perf_counter() - start_llm) * 1000

            log_service.node_producer(
                f"  ⚡ Producer AI Response: {llm_time:.1f}ms"
            )

            if result:
                if isinstance(result, dict):
                    selection = NodeSelection(**result)
                else:
                    selection = result
                return selection, system_prompt, user_prompt

            return None, system_prompt, user_prompt

        except Exception as e:
            log_service.error(f"[PRODUCER] Error calling Producer AI: {e}")
            return None, system_prompt, user_prompt

    @staticmethod
    def _build_producer_prompt() -> str:
        available_nodes = node_registry.get_menu_for_ai()

        return f"""You are the "Producer" for PLAiR.fm's dynamic context system.

Your job is to analyze user input and select ONLY the context nodes needed to respond effectively.

Available Nodes:
{available_nodes}

CRITICAL RULES:
1. FORMATTING NODES (always select these core structure nodes):
   - "core_dj_identity" - ALWAYS include first (required for tone/personality)
   - "station_capabilities" - ALWAYS include
   - "format_channels" - ALWAYS include (critical [BROADCAST]/[TXT] rules)
   - "format_tone" - ALWAYS include (language style)
   - "format_meta_tags_guide" - ALWAYS include (required for structuring responses)
   - "guidelines_critical" - Include when listener requests info/services
   - "guidelines_general" - ALWAYS include
   - "guidelines_internal_dialogue" - ALWAYS include
   - "format_roles_detailed" - OPTIONAL but use often (detailed DJ personalities)
   - "format_station_characteristics" - OPTIONAL but use often (station vibe)
   - "format_dialogue_examples" - OPTIONAL (example dialogue when needed)

2. CONTENT NODES (select GENEROUSLY based on relevance):
   - Be GENEROUS - err on the side of including MORE context
   - Token cost is LOW, so prefer more context over less
   - Consider the user's intent:
     * Simple greetings/commands → 10-14 nodes total (formatting + basic content + conversation + track basics)
     * Questions about current track → 14-20 nodes total (formatting + full track info + conversation + user context)
     * Complex queries → 20-28 nodes total (formatting + comprehensive context)

3. DEFAULT INCLUSIONS (nearly always include):
   - "conversation_recent" or "conversation_last_turn" - CRITICAL for context continuity
   - "user_basic" - Almost always relevant
   - For ANY track mention: include "track_title_artist", "track_style_description", "track_release_date"
   - For music discussions: include "user_favorite_artists" and "user_persona"
   - When in doubt, ADD the node rather than exclude it

4. COST OPTIMIZATION:
   - Prioritize LOW and MEDIUM cost nodes
   - MEDIUM cost nodes are fine to include liberally
   - Only avoid HIGH cost nodes if truly unnecessary
   - Order nodes by importance (most critical first)

Examples:

User: "Hey guys, who are we listening to right now?"
Selected: ["core_dj_identity", "station_capabilities", "format_channels", "format_tone", "format_meta_tags_guide", "guidelines_general", "guidelines_internal_dialogue", "format_roles_detailed", "track_title_artist", "track_style_description", "track_release_date", "track_audio_features_full", "conversation_last_turn", "user_basic", "user_persona"]
Reasoning: "Track inquiry - include core formatting + roles for DJ banter + full track context + conversation history + user context"

User: "Skip this track"
Selected: ["core_dj_identity", "station_capabilities", "format_channels", "format_tone", "format_meta_tags_guide", "guidelines_general", "guidelines_internal_dialogue", "format_station_characteristics", "queue_next_track", "queue_next_details", "track_title_artist", "conversation_last_turn", "user_basic", "user_favorite_artists"]
Reasoning: "Playback command - core formatting + station vibe + what's next + current track + conversation + user taste"

User: "Tell me about this song"
Selected: ["core_dj_identity", "station_capabilities", "format_channels", "format_tone", "format_meta_tags_guide", "guidelines_general", "guidelines_internal_dialogue", "format_roles_detailed", "track_title_artist", "track_release_date", "track_style_description", "track_vocal_info", "track_audio_features_full", "track_lyrics_preview", "conversation_recent", "user_basic", "user_persona", "user_favorite_artists"]
Reasoning: "Broad question about song - core formatting + roles for personality + comprehensive track info + conversation + user profile"

User: "What's the weather like?"
Selected: ["core_dj_identity", "station_capabilities", "format_channels", "format_tone", "format_meta_tags_guide", "guidelines_critical", "guidelines_general", "guidelines_internal_dialogue", "weather_current", "user_basic", "conversation_last_turn", "station_current_show"]
Reasoning: "Non-music service request - core formatting + critical guidelines + weather + conversation continuity + show context"

User: "Play something upbeat"
Selected: ["core_dj_identity", "station_capabilities", "format_channels", "format_tone", "format_meta_tags_guide", "guidelines_general", "guidelines_internal_dialogue", "format_roles_detailed", "user_favorite_artists", "user_basic", "user_persona", "track_title_artist", "conversation_recent", "queue_next_track"]
Reasoning: "Music request - core formatting + roles for personality + user taste profile + current context + conversation history"

Remember: Your goal is EFFICIENCY. Only select what's needed, nothing more."""

    def _update_cache_stats(self, input_hash: str):
        if input_hash in self.route_cache:
            cached = self.route_cache[input_hash]
            cached["times_reused"] += 1
            cached["last_used"] = time.time()

            try:
                conn = self._get_connection()
                c = conn.cursor()
                c.execute(
                    "UPDATE context_routing_cache SET times_reused = %s, last_used = %s WHERE input_hash = %s",
                    (cached["times_reused"], cached["last_used"], input_hash)
                )
                conn.commit()
                conn.close()
            except Exception as e:
                log_service.error(f"[PRODUCER] Failed to update cache stats: {e}")

    async def _save_to_cache(self, user_input: str, selection: NodeSelection, system_prompt: Optional[str] = None, user_prompt: Optional[str] = None):
        input_hash = self._hash_input(user_input)

        embedding = None
        if self.vector_db_service:
            embedding = self.vector_db_service._generate_embedding(user_input)

        selected_nodes_json = json.dumps(selection.selected_nodes)
        current_time = time.time()

        self.route_cache[input_hash] = {
            "user_input": user_input,
            "embedding": embedding,
            "selected_nodes": selection.selected_nodes,
            "reasoning": selection.reasoning,
            "confidence": selection.confidence,
            "created_at": current_time,
            "times_reused": 0,
            "last_used": current_time
        }

        conn = self._get_connection()
        c = conn.cursor()
        try:
            c.execute(
                """
                INSERT INTO context_routing_cache (input_hash, user_input, embedding, selected_nodes, reasoning, confidence, created_at, times_reused, last_used)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (input_hash) DO NOTHING
                """,
                (
                    input_hash,
                    user_input,
                    embedding.tobytes() if embedding is not None else None,
                    selected_nodes_json,
                    selection.reasoning,
                    selection.confidence,
                    current_time,
                    0,
                    current_time
                )
            )
            conn.commit()
        except psycopg2.IntegrityError:
            conn.rollback()
        except Exception as e:
            log_service.error(f"[PRODUCER] DB Write error: {e}")
            conn.rollback()
        finally:
            conn.close()

        await self._save_json_backup(user_input, selection, input_hash, system_prompt, user_prompt)

        if system_prompt and user_prompt:
            await self._save_prompt_debug(user_input, selection, system_prompt, user_prompt)

        log_service.system(
            f"[PRODUCER] Cached new routing decision for '{user_input[:40]}...'"
        )

    async def _save_json_backup(self, user_input: str, selection: NodeSelection, input_hash: str, _system_prompt: Optional[str] = None, _user_prompt: Optional[str] = None):
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{timestamp}_{input_hash[:8]}.json"
        filepath = os.path.join(self.json_dir, filename)

        data = {
            "timestamp": timestamp,
            "user_input": user_input,
            "input_hash": input_hash,
            "selection": {
                "selected_nodes": selection.selected_nodes,
                "reasoning": selection.reasoning,
                "confidence": selection.confidence
            }
        }

        try:
            async with aiofiles.open(filepath, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            log_service.warning(f"[PRODUCER] Failed to save JSON backup: {e}")

    async def _save_prompt_debug(self, user_input: str, selection: NodeSelection, system_prompt: str, user_prompt: str):
        try:
            from pathlib import Path
            debug_dir = Path(settings.PROMPT_DEBUG_DIR)
            debug_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")[:-3]

            prompt_chars = len(system_prompt) + len(user_prompt)
            prompt_tokens = prompt_chars // 4

            json_filename = f"producer_prompt_{timestamp}.json"
            json_filepath = debug_dir / json_filename

            json_data = {
                "timestamp": timestamp,
                "user_input": user_input,
                "prompts": {
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "system_prompt_chars": len(system_prompt),
                    "user_prompt_chars": len(user_prompt),
                    "total_chars": prompt_chars,
                    "estimated_tokens": prompt_tokens
                },
                "selection": {
                    "selected_nodes": selection.selected_nodes,
                    "node_count": len(selection.selected_nodes),
                    "reasoning": selection.reasoning,
                    "confidence": selection.confidence
                }
            }

            async with aiofiles.open(json_filepath, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(json_data, indent=2, ensure_ascii=False))

            txt_filename = f"producer_prompt_{timestamp}.txt"
            txt_filepath = debug_dir / txt_filename

            async with aiofiles.open(txt_filepath, 'w', encoding='utf-8') as f:
                await f.write("=" * 80 + "\n")
                await f.write(f"PRODUCER AI PROMPT DEBUG - {timestamp}\n")
                await f.write("=" * 80 + "\n\n")
                await f.write(f"User Input: {user_input}\n\n")
                await f.write(f"Prompt Size: {prompt_chars} chars (~{prompt_tokens} tokens)\n")
                await f.write(f"Selected Nodes ({len(selection.selected_nodes)}): {', '.join(selection.selected_nodes)}\n")
                await f.write(f"Reasoning: {selection.reasoning}\n")
                await f.write(f"Confidence: {selection.confidence}\n\n")
                await f.write("=" * 80 + "\n")
                await f.write("SYSTEM PROMPT\n")
                await f.write("=" * 80 + "\n\n")
                await f.write(system_prompt)
                await f.write("\n\n")
                await f.write("=" * 80 + "\n")
                await f.write("USER PROMPT\n")
                await f.write("=" * 80 + "\n\n")
                await f.write(user_prompt)
                await f.write("\n")

            log_service.node_producer(f"  💾 Saved producer prompt debug: {json_filename}")

        except Exception as e:
            log_service.error(f"[PRODUCER] Failed to save prompt debug: {e}")

    def _log_cache_performance(self):
        total = self.exact_hits + self.semantic_hits + self.llm_calls
        if total > 0 and total % 5 == 0:
            hit_rate = ((self.exact_hits + self.semantic_hits) / total) * 100
            log_service.node_performance(
                f"📊 Cache: {hit_rate:.1f}% hit rate | "
                f"Exact: {self.exact_hits} | Semantic: {self.semantic_hits} | LLM: {self.llm_calls}"
            )

    def get_cache_stats(self) -> Dict:
        total_requests = self.exact_hits + self.semantic_hits + self.llm_calls
        exact_rate = (self.exact_hits / total_requests * 100) if total_requests > 0 else 0
        semantic_rate = (self.semantic_hits / total_requests * 100) if total_requests > 0 else 0
        combined_rate = exact_rate + semantic_rate

        popular_decisions = sorted(
            [(v["user_input"], v["times_reused"]) for v in self.route_cache.values()],
            key=lambda x: x[1],
            reverse=True
        )[:10]

        return {
            "total_cached_decisions": len(self.route_cache),
            "exact_hits": self.exact_hits,
            "semantic_hits": self.semantic_hits,
            "llm_calls": self.llm_calls,
            "exact_hit_rate": exact_rate,
            "semantic_hit_rate": semantic_rate,
            "combined_hit_rate": combined_rate,
            "popular_decisions": popular_decisions,
            "database_url": settings.EMBEDDINGS_DATABASE_URL,
            "json_dir": self.json_dir
        }

context_router_service = ContextRouterService()