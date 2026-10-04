"""Produced segments: the prompts and broadcasts for biographies, lyrics, news, weather, places, events and shoutouts,
the interpretation cache, and the tag lists the hosts may use."""
import random
from datetime import datetime, timezone
from typing import Dict
from functools import lru_cache
from services_radio.dj_prompt_helper_service import (
    PARALANGUAGE_EXAMPLES_FROM_LIBRARY, STARTER_PARALANGUAGE_TAGS, is_clean_paralanguage,
    clean_gpt_output,
    is_valid_dj_script,
    assemble_prompt,
    UnavailableSegment
)
from services_radio import talk_clock
from services import log_service
from services.llm_router import LLM_LIVE, LLM_INTERPRET
from services.llm_result_cache import cache_key, interpretation_caches
from config.settings import settings
from services_radio.dj_prompt_service_configs import (
    BROADCAST_CUE, HOURLY_INTERPRETATIONS, INTERPRETATION_CACHE_NODES, LOCATION_SEGMENTS, NA_MARKER,
    NO_LOCATION_FIX_GUEST, NO_LOCATION_FIX_USER, NO_LOCATION_SEGMENT_LINES, PERSONAL_NODE_NEUTRAL_PREFIXES,
    SCRIPT_PROVIDER_NOTES, SEGMENT_DATA_NODES, SEGMENT_HOSTS, SEGMENT_SUBJECTS, UNAVAILABLE_SEGMENT_LINES,
    gpt_error_handler,
)


class SegmentPrompts:
    async def _unavailable_segment(self, gpt_type: str, user_id, session_id=None) -> UnavailableSegment:
        host, cohost = SEGMENT_HOSTS.get(gpt_type, ('LEO', 'JESS'))
        subject, label = SEGMENT_SUBJECTS.get(gpt_type, ('that', 'that'))
        location_need = LOCATION_SEGMENTS.get(gpt_type)
        if location_need and not await self._listener_has_location(user_id, location_need[1], session_id):
            fix = NO_LOCATION_FIX_USER if user_id else NO_LOCATION_FIX_GUEST
            text = random.choice(NO_LOCATION_SEGMENT_LINES).format(host=host, cohost=cohost,
                                                                   subject=location_need[0], fix=fix)
            feedback = f"Set your location to get {label}"
        else:
            text = random.choice(UNAVAILABLE_SEGMENT_LINES).format(host=host, cohost=cohost, subject=subject)
            feedback = f"Couldn't get {label} right now"
        log_service.external(f"Segment {gpt_type}: no data - airing honest fallback ({feedback})")
        return UnavailableSegment(text, feedback)

    def get_all_paralanguage_tags(self):
        titles = [t for t in self.vector_db_service.titles('paralanguage_embeddings') if is_clean_paralanguage(t)]
        if len(titles) >= PARALANGUAGE_EXAMPLES_FROM_LIBRARY:
            return titles
        return sorted(set(titles) | set(STARTER_PARALANGUAGE_TAGS))

    @lru_cache(maxsize=1)
    def get_all_audio_tags(self):
        return self.vector_db_service.titles('audio_embeddings')

    @lru_cache(maxsize=1)
    def get_all_correlated_tags(self):
        return [
            ("~leans back and stretches arms~", "%chair squeaking%"),
            ("~laughs heartily~", "%chair rolling slightly%"),
            ("~excited~", "%taps microphone%"),
            ("~sighs deeply~", "%coffee mug clinking%"),
            ("~clears throat~", "%papers shuffling%"),
            ("~yawns~", "%keyboard typing%"),
            ("~gasps in surprise~", "%pen dropping%"),
            ("~chuckles softly~", "%fingers drumming on desk%"),
            ("~takes a deep breath~", "%chair creaking%"),
            ("~sneezes~", "%tissue being pulled from box%"),
            ("~hums thoughtfully~", "%pencil tapping%"),
            ("~whispers excitedly~", "%soft popping on mic%"),
            ("~groans in frustration~", "%crumpling paper%"),
            ("~laughs nervously~", "%fidgeting with pen%"),
            ("~inhales sharply~", "%mic drop%")
        ]

    async def _execute_gpt_stream(self, model: str, max_tokens: int, temperature: float, messages: list,
                                  role: str = LLM_LIVE, validate=None, provider_notes=None) -> str:
        system_content = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        if len(messages) > 1 and messages[1]["role"] == "user":
            user_content = messages[1]["content"]
        elif system_content:
            user_content = BROADCAST_CUE
        else:
            user_content = messages[0]["content"]

        response = await self.gemini_service.call_gemini(
            prompt=user_content,
            system_instruction=system_content or None,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            role=role,
            validate=validate,
            provider_notes=provider_notes
        )
        return response or ""

    async def _execute_gpt_and_save(
        self,
        gpt_type: str,
        debug_timestamp: str | None,
        messages: list,
        clean_role: str = 'dj_content',
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        role: str = LLM_LIVE,
        validate_script: bool = False,
        provider_notes: Dict[str, str] | None = None
    ) -> str:

        model = model or self.config['dj_model']
        max_tokens = max_tokens or self.config['dj_tokens']
        temperature = temperature or self.config['dj_temperature']

        response = await self._execute_gpt_stream(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=messages,
            role=role,
            validate=(lambda text: is_valid_dj_script(text, clean_role)) if validate_script else None,
            provider_notes=(provider_notes or SCRIPT_PROVIDER_NOTES) if validate_script else None
        )

        response = NA_MARKER if NA_MARKER in response else clean_gpt_output(response, role=clean_role)

        if validate_script:
            from services_radio.conversation_service import write_turn_trace
            write_turn_trace({
                "at": datetime.now(timezone.utc).isoformat(), "kind": "script", "script": gpt_type,
                "prompt": "\n\n".join(str(m.get("content") or "") for m in messages),
                "response": response, "words": talk_clock.spoken_words(response),
            })

        if settings.PROMPT_DEBUG_ENABLED and debug_timestamp:
            import asyncio
            asyncio.create_task(self._async_save_response_debug(gpt_type, debug_timestamp, response))

        return response

    async def _async_save_response_debug(self, gpt_type: str, timestamp: str, response: str):
        try:
            self._append_response_to_debug(gpt_type, timestamp, response)
        except Exception as e:
            log_service.error(f"Failed to save response debug (non-blocking): {e}")

    @staticmethod
    def _is_personalised(context_data: Dict[str, str]) -> bool:
        for node, neutral_prefixes in PERSONAL_NODE_NEUTRAL_PREFIXES.items():
            content = (context_data.get(node) or "").strip()
            if content and not content.startswith(neutral_prefixes):
                return True
        return False

    def _interpretation_cache_key(self, gpt_type: str | None, context_data: Dict[str, str] | None) -> str | None:
        nodes = INTERPRETATION_CACHE_NODES.get(gpt_type or "")
        if not nodes or not context_data or self._is_personalised(context_data):
            return None
        instruction_node, data_node = nodes[0], nodes[1]
        if not context_data.get(instruction_node) or not context_data.get(data_node):
            return None
        facts = [context_data.get(node) or "" for node in nodes]
        bucket = datetime.now().strftime("%Y-%m-%d %H") if gpt_type in HOURLY_INTERPRETATIONS else ""
        return cache_key(gpt_type, bucket, *facts)

    async def _execute_broadcast_gpt(self, prompt_name: str, system_prompt: str, logger_type: str = 'external',
                                     clean_role: str = 'dj_content', gpt_type: str | None = None, debug_timestamp: str | None = None,
                                     context_data: Dict[str, str] | None = None, user_id=None, session_id=None):

        logger = getattr(log_service, logger_type)
        data_node = SEGMENT_DATA_NODES.get(gpt_type or "")
        if data_node and not (context_data or {}).get(data_node, "").strip():
            return await self._unavailable_segment(gpt_type or "", user_id, session_id)

        logger(f"Prompt: {prompt_name} Prompt: {system_prompt}")

        result_key = self._interpretation_cache_key(gpt_type, context_data)
        result_cache = interpretation_caches.get(gpt_type or "")
        if result_key and result_cache:
            cached = result_cache.get(result_key)
            if cached:
                logger(f"Cached Response: {prompt_name} served from result cache")
                return cached
            async with result_cache.lock(result_key):
                cached = result_cache.get(result_key)
                if cached:
                    logger(f"Cached Response: {prompt_name} served from result cache")
                    return cached
                return await self._generate_broadcast(prompt_name, system_prompt, logger, clean_role, gpt_type,
                                                      debug_timestamp, result_key, result_cache)
        return await self._generate_broadcast(prompt_name, system_prompt, logger, clean_role, gpt_type,
                                              debug_timestamp, result_key, result_cache)

    async def _generate_broadcast(self, prompt_name: str, system_prompt: str, logger, clean_role: str,
                                  gpt_type: str | None, debug_timestamp: str | None, result_key: str | None,
                                  result_cache):
        response_text = await self._execute_gpt_and_save(
            gpt_type=gpt_type or 'broadcast',
            debug_timestamp=debug_timestamp,
            messages=[{"role": "system", "content": system_prompt}],
            clean_role=clean_role,
            role=LLM_INTERPRET,
            validate_script=True
        )

        logger(f"Raw Response: {prompt_name} Raw Response: {response_text}")
        if NA_MARKER in response_text:
            return ""
        final_text = response_text.strip().strip('"')
        seconds = settings.SEGMENT_DEPTHS[talk_clock.depth()]
        log_service.commands(
            f"segment {gpt_type} | {talk_clock.depth()}: {seconds}s = {talk_clock.words_for(seconds)} words at "
            f"{talk_clock.pace():.2f} words/s | wrote {talk_clock.spoken_words(final_text)} words")
        if result_key and result_cache and is_valid_dj_script(final_text, clean_role):
            result_cache.set(result_key, final_text)
        return final_text

    @gpt_error_handler
    async def gpt_biography_interpretation(self, artist_name, session_dict):
        context_data, final_nodes, debug_timestamp = await self._get_nodes_unified(
            gpt_type='biography',
            user_id=session_dict.get('user_id'),
            session_id=session_dict.get('session_id'),
            artist_name=artist_name
        )

        system_prompt = assemble_prompt(context_data, final_nodes)
        return await self._execute_broadcast_gpt("Biography Interpretation", system_prompt,
                                                 gpt_type='biography', debug_timestamp=debug_timestamp or "",
                                                 context_data=context_data, user_id=session_dict.get('user_id'),
                                                 session_id=session_dict.get('session_id'))

    @gpt_error_handler
    async def gpt_lyrics_interpretation(self, lyrics, artist_name, session_dict):
        context_data, final_nodes, debug_timestamp = await self._get_nodes_unified(
            gpt_type='lyrics',
            user_id=session_dict.get('user_id'),
            session_id=session_dict.get('session_id'),
            lyrics=lyrics,
            artist_name=artist_name
        )

        system_prompt = assemble_prompt(context_data, final_nodes)
        return await self._execute_broadcast_gpt("Lyrics Interpretation", system_prompt,
                                                 gpt_type='lyrics', debug_timestamp=debug_timestamp or "",
                                                 context_data=context_data, user_id=session_dict.get('user_id'),
                                                 session_id=session_dict.get('session_id'))

    @gpt_error_handler
    async def gpt_news_interpretation(self, query, is_topic, categories, location, session_dict):
        context_data, final_nodes, debug_timestamp = await self._get_nodes_unified(
            gpt_type='news',
            user_id=session_dict.get('user_id'),
            session_id=session_dict.get('session_id'),
            query=query,
            is_topic=is_topic,
            categories=categories,
            location=location
        )

        system_prompt = assemble_prompt(context_data, final_nodes)
        return await self._execute_broadcast_gpt("News", system_prompt,
                                                 gpt_type='news', debug_timestamp=debug_timestamp or "",
                                                 context_data=context_data, user_id=session_dict.get('user_id'),
                                                 session_id=session_dict.get('session_id'))

    @gpt_error_handler
    async def gpt_weather_interpretation(self, session_dict, forecast_type: str = "current"):
        context_data, final_nodes, debug_timestamp = await self._get_nodes_unified(
            gpt_type='weather',
            user_id=session_dict.get('user_id'),
            session_id=session_dict.get('session_id'),
            forecast_type=forecast_type
        )

        system_prompt = assemble_prompt(context_data, final_nodes)
        return await self._execute_broadcast_gpt("Weather", system_prompt,
                                                 gpt_type='weather', debug_timestamp=debug_timestamp or "",
                                                 context_data=context_data, user_id=session_dict.get('user_id'),
                                                 session_id=session_dict.get('session_id'))

    @gpt_error_handler
    async def gpt_location_search_interpretation(self, query, session_dict):
        context_data, final_nodes, debug_timestamp = await self._get_nodes_unified(
            gpt_type='location_search',
            user_id=session_dict.get('user_id'),
            session_id=session_dict.get('session_id'),
            query=query
        )

        system_prompt = assemble_prompt(context_data, final_nodes)
        return await self._execute_broadcast_gpt("Location Search Interpretation", system_prompt,
                                                 gpt_type='location_search', debug_timestamp=debug_timestamp or "",
                                                 context_data=context_data, user_id=session_dict.get('user_id'),
                                                 session_id=session_dict.get('session_id'))

    @gpt_error_handler
    async def gpt_events_interpretation(self, location, country_code, start_date, end_date, session_dict, keyword=None):
        context_data, final_nodes, debug_timestamp = await self._get_nodes_unified(
            gpt_type='events',
            user_id=session_dict.get('user_id'),
            session_id=session_dict.get('session_id'),
            location=location,
            country_code=country_code,
            start_date=start_date,
            end_date=end_date,
            event_keyword=keyword
        )

        system_prompt = assemble_prompt(context_data, final_nodes)
        return await self._execute_broadcast_gpt("Events Search", system_prompt,
                                                 gpt_type='events', debug_timestamp=debug_timestamp or "",
                                                 context_data=context_data, user_id=session_dict.get('user_id'),
                                                 session_id=session_dict.get('session_id'))

    @gpt_error_handler
    async def gpt_shoutouts_interpretation(self, session_dict, query: str | None = None, n_results: int = 10):

        context_data, final_nodes, debug_timestamp = await self._get_nodes_unified(
            gpt_type='shoutouts',
            user_id=session_dict.get('user_id'),
            session_id=session_dict.get('session_id'),
            query=query,
            n_results=n_results
        )

        if not (context_data.get('data_shoutouts_data') or "").strip():
            log_service.detail("Shoutouts: No shoutouts available - skipping interpretation", "user_content")
            return None

        system_prompt = assemble_prompt(context_data, final_nodes)
        log_service.detail(f"Shoutouts: Shoutouts Prompt: {system_prompt}", "user_content")

        response_text = await self._execute_gpt_and_save(
            gpt_type='shoutouts',
            debug_timestamp=debug_timestamp or "",
            messages=[{"role": "system", "content": system_prompt}],
            clean_role='dj_content',
            role=LLM_INTERPRET,
            validate_script=True
        )

        log_service.detail(f"Shoutouts: Shoutouts Raw Response: {response_text}", "user_content")

        if "[N/A]" in response_text:
            log_service.detail("Shoutouts: Shoutouts response is not applicable ([N/A])", "user_content")
            return None
        return response_text.strip().strip('"')
