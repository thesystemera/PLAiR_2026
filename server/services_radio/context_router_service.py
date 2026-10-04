"""
Context Router Service (The "Producer")

This service analyzes user input and determines which context nodes are needed.
Uses Gemini Flash Lite (fast, cheap) with PostgreSQL caching.

This is the brain that makes the node system efficient - it only requests what's needed.
"""

import re
import time
import json
import hashlib
import aiofiles
from datetime import datetime
from typing import List, Optional, Dict
from pydantic import BaseModel, Field

from services.base_service import SingletonService
from services import log_service
from services.llm_router import LLM_LIVE
from services_radio.context_node_registry import node_registry
from config.settings import settings
from services.task_utils import spawn
from services.semantic_cache import SemanticCache

ROUTE_COLUMNS = {
    "selected_nodes": "TEXT",
    "reasoning": "TEXT",
    "confidence": "REAL",
    "needs_tools": "BOOLEAN DEFAULT FALSE",
    "tool_plan": "TEXT DEFAULT '[]'",
    "pulse_json": "TEXT DEFAULT '{}'",
    "prompt_hash": "TEXT DEFAULT ''",
}

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
    from services_radio.dj_tool_registry import TOOL_NAMES
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
    from services_radio.dj_tool_registry import tool_catalog
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
        self.cache = SemanticCache("context_routing_cache", "input_hash", "user_input", ROUTE_COLUMNS,
                                   "Producer route cache", log_service.node_producer)

        self._initialized = True

    async def initialize(self, ai_service):
        self.ai_service = ai_service
        await self.cache.prepare(self._drop_routes_from_old_prompts)
        log_service.node_producer("✓ Context Router Service (Producer AI) initialized")

    def _drop_routes_from_old_prompts(self, cursor) -> None:
        cursor.execute("DELETE FROM context_routing_cache WHERE prompt_hash IS DISTINCT FROM %s",
                       (self._prompt_hash(),))
        if cursor.rowcount:
            log_service.node_producer(f"  Producer prompt changed: dropped {cursor.rowcount} cached routes")

    def _prompt_hash(self) -> str:
        if not getattr(self, "_prompt_hash_value", None):
            self._prompt_hash_value = hashlib.md5(self._build_producer_prompt().encode()).hexdigest()
        return self._prompt_hash_value

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
        log_service.node_producer(f"🧠 Analyzing: '{user_input[:60]}...'")

        if use_cache:
            found = await self.cache.find(user_input, similarity_threshold)
            if found is not None:
                kind, cached, similarity = found
                row = cached["row"]
                nodes = json.loads(row["selected_nodes"] or "[]")
                pulse = clean_pulse(**json.loads(row["pulse_json"] or "{}"))
                log_service.node_producer(f"  ✅ CACHE HIT ({kind}, {similarity:.3f}) → {nodes}")
                if kind == "exact":
                    return self._route(nodes, row["needs_tools"], json.loads(row["tool_plan"] or "[]"), pulse,
                                       "cached plan")
                return self._route(nodes, row["needs_tools"], json.loads(row["tool_plan"] or "[]"),
                                   stated_pulse(pulse, user_input), f"cached plan, {similarity:.0%} match")

        log_service.node_producer("  🤖 CACHE MISS - Calling Producer AI...")

        selection, system_prompt, user_prompt = await self._call_producer_ai(user_input)

        if selection:
            if use_cache:
                spawn(self.cache.save(user_input, {
                    "selected_nodes": json.dumps(selection.selected_nodes),
                    "reasoning": selection.reasoning,
                    "confidence": selection.confidence,
                    "needs_tools": bool(selection.needs_tools),
                    "tool_plan": json.dumps(clean_plan(selection.tool_plan)),
                    "pulse_json": json.dumps(_selection_pulse(selection)),
                    "prompt_hash": self._prompt_hash(),
                }), name="producer_cache_save")
                if system_prompt and user_prompt:
                    spawn(self._save_prompt_debug(user_input, selection, system_prompt, user_prompt),
                          name="producer_prompt_debug")
                line = self.cache.hit_rate_line()
                if line:
                    log_service.node_performance(line)
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
  (playback_control), like or ban a track or a shoutout (rate_track), more like this or a station from several
  things at once, e.g. this style with that mood (seed_radio, one step with a weighted blend), a playlist
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

context_router_service = ContextRouterService()