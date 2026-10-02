"""
Context Router Service (The "Producer")

This service analyzes user input and determines which context nodes are needed.
Uses Gemini Flash Lite (fast, cheap) with PostgreSQL caching.

This is the brain that makes the node system efficient - it only requests what's needed.
"""

import asyncio
import re
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
from services import usage_tracking
from services.llm_router import LLM_LIVE
from services_radio.context_node_registry import node_registry
from config.settings import settings
from database.pg_pool import get_pooled_connection
from models_global import run_on_gpu_executor
from services.task_utils import spawn

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
    needs_tools: bool = Field(
        default=False,
        description="True when the hosts must find something out or make something happen (play, skip, rate, save, "
                    "a segment); false only for banter, greetings, opinions and questions the context nodes answer"
    )
    tool_plan: List[str] = Field(
        default_factory=list,
        description="Numbered function-call steps with <placeholders>, e.g. 'pulse_search(query=<topic>, kinds=[event])'"
    )
    pulse_topic: str = Field(
        default="",
        description="What the listener's message is about, as a short search phrase for the station's knowledge "
                    "(e.g. 'Radiohead', 'late night food', 'rugby'), or empty"
    )
    pulse_kinds: List[str] = Field(
        default_factory=list,
        description="Which kinds of station knowledge could help with this message (event, place, news, weather, "
                    "area, artist, track, community, review, chart, trend); empty when none"
    )
    pulse_near_me: bool = Field(default=False, description="True when the listener wants things near them or local")
    pulse_when: str = Field(default="", description="Time window if the message has one: now, today, tonight, "
                                                    "tomorrow, weekend, week, month; else empty")


def tool_names() -> set:
    from services_radio.dj_tools import TOOL_NAMES
    return TOOL_NAMES


PULSE_KINDS = ["event", "place", "news", "weather", "area", "artist", "track", "community", "review", "chart", "trend"]
PULSE_WHEN = ["now", "today", "tonight", "tomorrow", "weekend", "week", "month"]


def clean_pulse(topic: str = "", kinds: Optional[List[str]] = None, near_me: bool = False, when: str = "") -> Dict:
    kinds = [k for k in dict.fromkeys(str(k).strip().lower() for k in (kinds or [])) if k in PULSE_KINDS]
    return {"topic": (topic or "").strip()[:120], "kinds": kinds, "near_me": bool(near_me),
            "when": when if when in PULSE_WHEN else ""}


FREE_TEXT_ARGS = ("query", "topic", "artist", "song", "item_id", "parent_id")
_FREE_TEXT_ARG = re.compile(r"\b(" + "|".join(FREE_TEXT_ARGS) + r")\s*=\s*(\"[^\"]*\"|'[^']*'|<[^>]*>|[^,)]*)")
_LISTENER_ARG = re.compile(r",?\s*\b(when|near_me|max_age_days)\s*=\s*(\[[^\]]*\]|\"[^\"]*\"|'[^']*'|<[^>]*>|[^,)]*)")
_WHEN_WORDS = [("tonight", r"\btonight\b"), ("tomorrow", r"\btomorrow\b"), ("weekend", r"\bweekend\b"),
               ("today", r"\btoday\b|\bthis (morning|afternoon|evening)\b"), ("week", r"\bthis week\b|\bweek\b"),
               ("month", r"\bthis month\b|\bmonth\b"), ("now", r"\bright now\b")]
_NEAR_ME = re.compile(r"\b(near me|nearby|near here|around here|round here|close by|close to (me|us|here)|local|"
                      r"locally|in my area|in the area|my neighbou?rhood)\b", re.IGNORECASE)


def clean_plan(plan: List[str]) -> List[str]:
    names = tool_names()
    steps = []
    for step in plan or []:
        text = str(step).strip().lstrip("0123456789.) ").strip()
        name = text.split("(", 1)[0].strip()
        if name in names:
            text = _FREE_TEXT_ARG.sub(lambda m: f"{m.group(1)}=<{m.group(1)}>", text)
            text = _LISTENER_ARG.sub("", text).replace("(, ", "(").replace("(,", "(")
            steps.append(text[:200])
    return steps[:6]


def stated_pulse(pulse: Dict, user_input: str) -> Dict:
    text = (user_input or "").lower()
    when = next((word for word, pattern in _WHEN_WORDS if re.search(pattern, text)), "")
    return {**pulse, "when": when, "near_me": bool(_NEAR_ME.search(text))}


def tool_menu() -> str:
    from services_radio.dj_tools import tool_catalog
    return tool_catalog()

def _selection_pulse(selection) -> Dict:
    return clean_pulse(selection.pulse_topic, selection.pulse_kinds, selection.pulse_near_me, selection.pulse_when)


DEFAULT_NODES = ["core_dj_identity", "station_capabilities", "format_channels", "format_tone", "format_performance_tags_guide",
                 "format_performance_tag_examples"]


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
        return get_pooled_connection(settings.EMBEDDINGS_DATABASE_URL)

    async def initialize(self, ai_service, vector_db_service=None):
        self.ai_service = ai_service
        self.vector_db_service = vector_db_service

        os.makedirs(self.json_dir, exist_ok=True)

        self._initialize_database()
        self._load_cache_from_disk()
        await self._reembed_stale()

        log_service.node_producer("✓ Context Router Service (Producer AI) initialized")
        log_service.node_producer("  📂 Database:   PostgreSQL (ai_radio_embeddings)")
        log_service.node_producer(f"  📂 JSON Cache: {self.json_dir}")

    async def _reembed_stale(self) -> None:
        if not self.vector_db_service:
            return
        dim = self.vector_db_service.embedding_dim
        stale = [(input_hash, cached) for input_hash, cached in self.route_cache.items()
                 if cached["embedding"] is None or cached["embedding"].shape[0] != dim]
        if not stale:
            return
        for _, cached in stale:
            cached["embedding"] = await run_on_gpu_executor(self.vector_db_service._generate_embedding,
                                                            cached["user_input"])

        def _update():
            conn = self._get_connection()
            try:
                c = conn.cursor()
                for input_hash, cached in stale:
                    c.execute("UPDATE context_routing_cache SET embedding = %s WHERE input_hash = %s",
                              (cached["embedding"].tobytes(), input_hash))
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_update)
        log_service.node_producer(f"  Re-embedded {len(stale)} cached routes for the current encoder")

    def _initialize_database(self):
        conn = self._get_connection()
        try:
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
            c.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'context_routing_cache' "
                      "AND column_name = 'pulse_json'")
            if c.fetchone() is None:
                c.execute("DELETE FROM context_routing_cache")
                c.execute("ALTER TABLE context_routing_cache ADD COLUMN IF NOT EXISTS needs_tools BOOLEAN DEFAULT FALSE, "
                          "ADD COLUMN IF NOT EXISTS tool_plan TEXT DEFAULT '[]', "
                          "ADD COLUMN IF NOT EXISTS pulse_json TEXT DEFAULT '{}'")
                log_service.node_producer("  Routing cache reset for tool planning")
            c.execute("ALTER TABLE context_routing_cache ADD COLUMN IF NOT EXISTS prompt_hash TEXT DEFAULT ''")
            c.execute("DELETE FROM context_routing_cache WHERE prompt_hash IS DISTINCT FROM %s", (self._prompt_hash(),))
            if c.rowcount:
                log_service.node_producer(f"  Producer prompt changed: dropped {c.rowcount} cached routes")
            conn.commit()
        finally:
            conn.close()

    def _load_cache_from_disk(self):
        start_time = time.perf_counter()

        try:
            conn = self._get_connection()
            try:
                c = conn.cursor()
                c.execute("SELECT input_hash, user_input, embedding, selected_nodes, reasoning, "
                          "confidence, created_at, times_reused, last_used, needs_tools, tool_plan, pulse_json "
                          "FROM context_routing_cache")

                rows = c.fetchall()
            finally:
                conn.close()

            for row in rows:
                input_hash, user_input, embedding_blob, selected_nodes_json, reasoning, \
                    confidence, created_at, times_reused, last_used, needs_tools, tool_plan_json, pulse_json = row

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
                    "last_used": last_used,
                    "needs_tools": bool(needs_tools),
                    "tool_plan": json.loads(tool_plan_json or "[]"),
                    "pulse": clean_pulse(**json.loads(pulse_json or "{}")),
                }

            elapsed = time.perf_counter() - start_time
            log_service.node_producer(
                f"✓ Loaded {len(self.route_cache)} cached routing decisions ({elapsed:.2f}s)"
            )
        except Exception as e:
            log_service.error(f"[PRODUCER] Failed to load cache: {e}")

    def _prompt_hash(self) -> str:
        if not getattr(self, "_prompt_hash_value", None):
            self._prompt_hash_value = hashlib.md5(self._build_producer_prompt().encode()).hexdigest()
        return self._prompt_hash_value

    @staticmethod
    def _hash_input(user_input: str) -> str:
        return hashlib.md5(user_input.lower().strip().encode()).hexdigest()

    async def determine_nodes(self, user_input: str, use_cache: bool = True,
                              similarity_threshold: Optional[float] = None) -> List[str]:
        return (await self.determine_route(user_input, use_cache, similarity_threshold))["nodes"]

    @staticmethod
    def _route(nodes: List[str], needs_tools: bool = False, tool_plan: Optional[List[str]] = None,
               pulse: Optional[Dict] = None, source: str = "default") -> Dict:
        plan = clean_plan(tool_plan or [])
        return {"nodes": nodes, "needs_tools": bool(needs_tools and plan), "tool_plan": plan,
                "pulse": pulse or clean_pulse(), "source": source}

    async def determine_route(
        self,
        user_input: str,
        use_cache: bool = True,
        similarity_threshold: Optional[float] = None
    ) -> Dict:
        if similarity_threshold is None:
            similarity_threshold = settings.GEMINI_NODE_PRODUCER_SIMILARITY_THRESHOLD
        use_cache = use_cache and settings.PRODUCER_CACHE_ENABLED

        if not user_input or not user_input.strip():
            return self._route(DEFAULT_NODES)

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

            return self._route(cached['selected_nodes'], cached.get('needs_tools'), cached.get('tool_plan'),
                               cached.get("pulse"), "cached plan")

        if use_cache and self.vector_db_service:
            input_embedding = await run_on_gpu_executor(self.vector_db_service._generate_embedding, user_input)

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

                return self._route(cached['selected_nodes'], cached.get('needs_tools'), cached.get('tool_plan'),
                                   stated_pulse(cached.get('pulse') or clean_pulse(), user_input),
                                   f"cached plan, {best_similarity:.0%} match")

        self.llm_calls += 1
        log_service.node_producer("  🤖 CACHE MISS - Calling Producer AI...")

        selection, system_prompt, user_prompt = await self._call_producer_ai(user_input)

        if selection:
            if use_cache:
                await self._save_to_cache(user_input, selection, system_prompt, user_prompt)
                self._log_cache_performance()
            route = self._route(selection.selected_nodes, selection.needs_tools, selection.tool_plan,
                                _selection_pulse(selection), "fresh plan")
            log_service.node_producer(f"  📌 Selected {len(selection.selected_nodes)} nodes → {selection.selected_nodes}"
                                      f" | tools: {route['tool_plan'] if route['needs_tools'] else 'none'}")
            return route

        return self._route(DEFAULT_NODES)

    async def _call_producer_ai(self, user_input: str) -> tuple[NodeSelection | None, str, str]:
        if not self.ai_service:
            log_service.error("[PRODUCER] AI service not configured")
            return None, "", ""

        system_prompt = self._build_producer_prompt()
        user_prompt = f"""Analyze this user input, select the context nodes needed to respond, and plan the studio tools:

User Input: "{user_input}"

Return the selected nodes in order of importance, needs_tools, and the tool_plan."""

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
                system_instruction=system_prompt,
                role=LLM_LIVE
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

The hosts' standing instructions (identity, format, guidelines, tools) are always included. Your first job is to
choose, from the context nodes below, the ones the reply to this message could use.

Available Nodes:
{available_nodes}

CONTEXT NODES:
- Nearly always: "conversation_last_turn" (or "conversation_recent" when the message builds on more than the last
  exchange) and "user_basic".
- Any mention of a track: "track_title_artist", "track_style_description", "track_release_date" (and more track
  nodes for questions about the song). Music requests and discussions: "user_favorite_artists" and "user_persona".
- When in doubt, include the node: context is cheap. Order them most important first.

Examples:
"Hey guys, who are we listening to right now?" -> ["track_title_artist", "track_style_description", "track_release_date", "track_audio_features_full", "conversation_last_turn", "user_basic", "user_persona"]
"Skip this track" -> ["track_title_artist", "queue_next_track", "queue_next_details", "conversation_last_turn", "user_basic", "user_favorite_artists"]
"Tell me about this song" -> ["track_title_artist", "track_release_date", "track_style_description", "track_vocal_info", "track_audio_features_full", "track_lyrics_preview", "conversation_recent", "user_basic", "user_persona", "user_favorite_artists"]
"What's the weather like?" -> ["weather_current", "user_basic", "conversation_last_turn", "station_current_show"]
"Play something upbeat" -> ["user_favorite_artists", "user_basic", "user_persona", "track_title_artist", "conversation_recent", "queue_next_track"]

TOOL PLANNING (needs_tools + tool_plan):
The studio tools the hosts can use, with what each does, its parameters, cost and requirements:
{tool_menu()}

Set needs_tools=true when the hosts must find something out or make something happen:
- anything local, current or factual beyond the context nodes: gigs and events, places nearby, news, weather detail,
  air quality or pollen, the neighbourhood, artist facts, listener shoutouts, what listeners said about a song, the
  listener's own posts, what the city is playing or asking about (pulse_search, pulse_detail, city_trends,
  listener_context);
- the listener pointing back at something that already played for them and isn't simply the current, previous or
  next track: an earlier song, a shoutout, reply or review they heard, a segment that aired or something the hosts
  said earlier (what_aired, then the action tool if they want something done);
- a music request or a question about what the catalog has (search_and_play, pulse_search); the listener trying to
  pin down one particular song or band from clues (find_tracks, then search_and_play with the chosen track_id);
- saving the listener's own voice message (save_shoutout, save_shoutout_reply, save_review);
- any command: skip, go back, pause, resume, restart or jump within the song, drop a queued track
  (playback_control), like or ban a track or a shoutout (rate_track), more like this (seed_radio), a playlist
  (play_playlist), the music on another device (move_playback), Radio Mode, its talk breaks or human / AI music
  (radio_settings). Nothing happens unless a tool is called, so every action needs its step.
- the segment tools (get_news, get_weather, get_events, find_places, get_artist_biography, explain_lyrics,
  play_shoutouts) air a full produced segment. Put the matching one in the plan whenever the subject comes up, as an
  option: the hosts decide whether a quick answer is enough or the listener wants the full rundown.
Set needs_tools=false only for banter, greetings, opinions and questions the context nodes already answer.

tool_plan is a bare numbered list of function-call steps with <placeholders> for values, never invented values.
GOOD: ["1. pulse_search(query=<kind of music>, kinds=[event], when=weekend)", "2. pulse_detail(item_id=<best match>)"]
GOOD: ["1. pulse_search(query=<allergy topic>, kinds=[area, weather])"]
GOOD: ["1. playback_control(action=next)"], ["1. rate_track(rating=like, target=current)", "2. seed_radio(category=mood)"]
GOOD: ["1. search_and_play(category=primary_artist, query=<artist>, mode=play)"]
BAD: ["Look up jazz gigs"] (not a function call), ["pulse_search(query='Blue Note Friday 9pm')"] (invented value)
Leave tool_plan empty when needs_tools is false.

STATION KNOWLEDGE (pulse_topic, pulse_kinds, pulse_near_me, pulse_when):
Decide what the station's own knowledge could add to the reply, whether or not tools are needed.
- pulse_kinds: the kinds that could genuinely help, from ["event", "place", "news", "weather", "area", "artist", "track", "community", "review", "chart", "trend"]
  (event = gigs and shows, place = venues, cafes, bars, shops, news = news stories, weather, area = air quality, pollen,
  neighbourhood, artist = artist biographies, track = songs in the station's catalog, community = listener shoutouts,
  review = what listeners said about songs,
  chart = what the city is playing, trend = what locals have been asking about). Empty for greetings, banter and
  plain commands.
- pulse_topic: the subject as a short search phrase, without filler ("any good cafes near me?" -> "good cafes").
- pulse_near_me: true ONLY when the listener says it (near me, nearby, around here, local, in my area, close to us).
- pulse_when: ONLY a time window the listener actually states ("tonight", "this weekend"). Never guess one:
  "any comedy on?" has no time window. The same goes for tool_plan arguments: never add a when or near_me the
  listener didn't ask for.
Examples:
"Radiohead near me" -> topic "Radiohead", kinds [event, community, news, artist, track], near_me true
"any gigs this weekend?" -> topic "gigs", kinds [event], when "weekend"
"any comedy on?" -> topic "comedy", kinds [event], near_me false, when ""
"what's the latest with the All Blacks?" -> topic "All Blacks", kinds [news, event]
"is it going to rain tonight" -> topic "rain", kinds [weather], when "tonight"
"hey how's it going" -> topic "", kinds []"""

    def _update_cache_stats(self, input_hash: str):
        usage_tracking.record_cache_hit("context_router")
        if input_hash in self.route_cache:
            cached = self.route_cache[input_hash]
            cached["times_reused"] += 1
            cached["last_used"] = time.time()

            spawn(
                asyncio.to_thread(self._persist_cache_stats, input_hash, cached["times_reused"], cached["last_used"]),
                name="context_router_cache_stats"
            )

    def _persist_cache_stats(self, input_hash: str, times_reused: int, last_used: float):
        try:
            conn = self._get_connection()
            try:
                c = conn.cursor()
                c.execute(
                    "UPDATE context_routing_cache SET times_reused = %s, last_used = %s WHERE input_hash = %s",
                    (times_reused, last_used, input_hash)
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            log_service.error(f"[PRODUCER] Failed to update cache stats: {e}")

    async def _save_to_cache(self, user_input: str, selection: NodeSelection, system_prompt: Optional[str] = None, user_prompt: Optional[str] = None):
        input_hash = self._hash_input(user_input)

        embedding = None
        if self.vector_db_service:
            embedding = await run_on_gpu_executor(self.vector_db_service._generate_embedding, user_input)

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
            "last_used": current_time,
            "needs_tools": selection.needs_tools,
            "tool_plan": clean_plan(selection.tool_plan),
            "pulse": _selection_pulse(selection),
        }

        await asyncio.to_thread(
            self._insert_cache_row, input_hash, user_input, embedding, selected_nodes_json, selection, current_time
        )

        if system_prompt and user_prompt:
            await self._save_prompt_debug(user_input, selection, system_prompt, user_prompt)

        log_service.system(
            f"[PRODUCER] Cached new routing decision for '{user_input[:40]}...'"
        )

    def _insert_cache_row(self, input_hash: str, user_input: str, embedding, selected_nodes_json: str,
                          selection: NodeSelection, current_time: float):
        try:
            conn = self._get_connection()
        except Exception as e:
            log_service.error(f"[PRODUCER] DB Write error: {e}")
            return
        c = conn.cursor()
        try:
            c.execute(
                """
                INSERT INTO context_routing_cache (input_hash, user_input, embedding, selected_nodes, reasoning, confidence, created_at, times_reused, last_used, needs_tools, tool_plan, pulse_json, prompt_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    current_time,
                    bool(selection.needs_tools),
                    json.dumps(clean_plan(selection.tool_plan)),
                    json.dumps(_selection_pulse(selection)),
                    self._prompt_hash()
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