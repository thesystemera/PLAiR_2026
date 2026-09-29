import re
import time
import json
import random
from datetime import datetime
from pathlib import Path
from typing import List, Dict
from functools import lru_cache

from services_radio.dj_prompt_helper_service import (
    filter_meta_tags_for_gpt_prompt_cleaning,
    clean_gpt_output,
    is_valid_dj_script,
    assemble_prompt,
    UnavailableSegment
)
from services_radio.context_node_registry import node_registry
from services_radio.context_service import gather_raw_dependencies
from services_radio import listener_location as location_resolver
from services_radio.context_router_service import context_router_service
from services import log_service
from services.llm_router import LLM_LIVE, LLM_DJ, LLM_ANNOUNCE, LLM_INTERPRET
from services.llm_result_cache import cache_key, interpretation_caches
from config.settings import settings

PARTIAL_SIGN_OFF = re.compile(r"\b(partial|partly|incomplete|not done)\b", re.IGNORECASE)

BROADCAST_CUE = "Write the script for this segment now, following the instructions above."

SCRIPT_PROVIDER_NOTES = {
    "deepseek": (
        "\n\nMARKUP DISCIPLINE: Asterisks are reserved for *paralanguage* sound cues - never use them for emphasis "
        "inside spoken words (use CAPITALS for emphasis). Every *, %, @, & and $ must belong to a complete tag."
        "\n\nLENGTH: This is live radio - keep it tight. Use at most 90 spoken words in total across all hosts "
        "(tags and cues don't count). Cover only the most interesting points; never pad."
    )
}

PERSONAL_NODE_NEUTRAL_PREFIXES = {
    'user_persona': ("LISTENER PERSONA: Guest",),
    'user_profile': ("LISTENER PROFILE: Guest",),
    'conversation_recent': ("CONVERSATION HISTORY: None", "CONVERSATION HISTORY: No session", "CONVERSATION HISTORY: Error"),
}

INTERPRETATION_CACHE_NODES = {
    'news': ('instruction_news', 'data_news_report', 'user_basic'),
    'weather': ('instruction_weather', 'data_weather_report'),
    'biography': ('instruction_biography', 'data_biography'),
    'lyrics': ('instruction_lyrics', 'data_lyrics'),
}
HOURLY_INTERPRETATIONS = {'weather'}

NA_MARKER = "[N/A]"

RADIO_SEGMENT_BASE_NODES = [
    'core_dj_identity',
    'format_channels',
    'format_tone',
    'format_meta_tags_guide',
    'format_roles_detailed',
    'format_station_characteristics',
    'format_dialogue_examples',
    'instruction_radio_segment',
    'data_radio_segment',
    'user_local_time'
]
RADIO_SEGMENT_MARKUP_NOTE = (
    "\n\nMARKUP DISCIPLINE: Asterisks are reserved for *paralanguage* sound cues - never use them for emphasis "
    "inside spoken words (use CAPITALS for emphasis). Every *, %, @, & and $ must belong to a complete tag."
)

SEGMENT_DATA_NODES = {
    'news': 'data_news_report',
    'weather': 'data_weather_report',
    'location_search': 'data_location_report',
    'events': 'data_events_report',
    'biography': 'data_biography',
    'lyrics': 'data_lyrics',
}
SEGMENT_HOSTS = {'weather': ('TARA', 'LEO')}
SEGMENT_SUBJECTS = {
    'news': ('the news wire', 'the news'),
    'weather': ('the weather feed', 'the weather'),
    'location_search': ('the places lookup', 'places nearby'),
    'events': ('the events listings', 'events'),
    'biography': ("that artist's backstory", 'that biography'),
    'lyrics': ('those lyrics', 'those lyrics'),
}
LOCATION_SEGMENTS = {
    'weather': ('your forecast', True),
    'location_search': ('spots near you', True),
    'events': ('events near you', False),
}
UNAVAILABLE_SEGMENT_LINES = (
    "[BROADCAST] [{host}] &0.2& *sighs* Damn, {subject} just came back empty on us. &0.2& Nothing to report right "
    "now, so give it a minute and ask again.\n[{cohost}] &0.3& *chuckles* Pirate radio, baby. Held together with duct tape.",
    "[BROADCAST] [{host}] &0.2& *groans* Ugh, nothing's coming through on {subject} right now. &0.2& Not gonna make "
    "stuff up, so ask us again in a bit.\n[{cohost}] &0.3& *laughs* Honest radio. What a concept.",
)
NO_LOCATION_SEGMENT_LINES = (
    "[TXT] [{host}] &0.2& *clears throat* I can't pull up {subject} without knowing where you're tuned in from. "
    "&0.2& {fix}\n[{cohost}] &0.3& *chuckles* We're pirates, not psychics.",
)
NO_LOCATION_FIX_GUEST = "Let the app use your location, then ask me again."
NO_LOCATION_FIX_USER = "Set your location in your profile and ask me again."

def gpt_error_handler(func):
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            log_service.error(f"GPT error in {func.__name__}: {e}")
            if hasattr(func, '__annotations__') and 'return' in func.__annotations__:
                return_type = func.__annotations__['return']
                if hasattr(return_type, '__origin__') and return_type.__origin__ is tuple:
                    num_values = len(return_type.__args__)
                    return tuple([None] * num_values)
            return None

    return wrapper

class DJPromptService:
    def __init__(self, gemini_service, vector_db_service, async_session_maker, user_content_speech_enhancement_service=None,
                 user_content_vector_search_service=None, catalog_service=None, playback_service=None,
                 orchestrator=None, web_service=None, news_service=None, location_service=None, events_service=None):
        self.gemini_service = gemini_service
        self.config = {
            'dj_model': settings.GEMINI_DJ_MODEL,
            'dj_temperature': settings.GEMINI_DJ_TEMPERATURE,
            'dj_tokens': settings.GEMINI_DJ_MAX_TOKENS,
            'audio_model': settings.GEMINI_AUDIO_MODEL,
            'audio_temperature': settings.GEMINI_AUDIO_TEMPERATURE,
            'audio_tokens': settings.GEMINI_AUDIO_MAX_TOKENS
        }
        self.vector_db_service = vector_db_service
        self.async_session_maker = async_session_maker
        self.user_content_speech_enhancement_service = user_content_speech_enhancement_service
        self.user_content_vector_search_service = user_content_vector_search_service
        self.catalog_service = catalog_service
        self.playback_service = playback_service
        self.orchestrator = orchestrator
        self.web_service = web_service
        self.news_service = news_service
        self.location_service = location_service
        self.events_service = events_service

        self.node_configs = {
            'interactive_tools': {
                'required_nodes': [
                    'core_dj_identity',
                    'format_channels',
                    'format_tone',
                    'format_meta_tags_guide',
                    'format_roles_detailed',
                    'format_station_characteristics',
                    'format_dialogue_examples',
                    'instruction_dj_tools',
                    'tool_guidance',
                    'station_recent_airings',
                    'city_pulse'
                ],
                'use_ai_picker': True
            },
            'biography': {
                'required_nodes': [
                    'core_dj_identity',
                    'format_channels',
                    'format_tone',
                    'format_meta_tags_guide',
                    'format_roles_detailed',
                    'format_station_characteristics',
                    'format_dialogue_examples',
                    'instruction_biography',
                    'data_biography',
                    'user_local_time',
                    'user_persona',
                    'user_profile',
                    'conversation_recent'
                ],
                'use_ai_picker': False
            },
            'lyrics': {
                'required_nodes': [
                    'core_dj_identity',
                    'format_channels',
                    'format_tone',
                    'format_meta_tags_guide',
                    'format_roles_detailed',
                    'format_station_characteristics',
                    'format_dialogue_examples',
                    'instruction_lyrics',
                    'data_lyrics',
                    'user_local_time',
                    'user_persona',
                    'user_profile',
                    'conversation_recent'
                ],
                'use_ai_picker': False
            },
            'news': {
                'required_nodes': [
                    'core_dj_identity',
                    'format_channels',
                    'format_tone',
                    'format_meta_tags_guide',
                    'format_roles_detailed',
                    'format_station_characteristics',
                    'format_dialogue_examples',
                    'instruction_news',
                    'data_news_report',
                    'user_local_time',
                    'user_basic',
                    'weather_current',
                    'user_persona',
                    'user_profile',
                    'conversation_recent'
                ],
                'use_ai_picker': False
            },
            'weather': {
                'required_nodes': [
                    'core_dj_identity',
                    'format_channels',
                    'format_tone',
                    'format_meta_tags_guide',
                    'format_roles_detailed',
                    'format_station_characteristics',
                    'format_dialogue_examples',
                    'instruction_weather',
                    'data_weather_report',
                    'user_local_time',
                    'conversation_recent'
                ],
                'use_ai_picker': False
            },
            'location_search': {
                'required_nodes': [
                    'core_dj_identity',
                    'format_channels',
                    'format_tone',
                    'format_meta_tags_guide',
                    'format_roles_detailed',
                    'format_station_characteristics',
                    'format_dialogue_examples',
                    'instruction_location_search',
                    'data_location_report',
                    'user_local_time',
                    'user_basic',
                    'weather_current',
                    'user_persona',
                    'user_profile',
                    'conversation_recent'
                ],
                'use_ai_picker': False
            },
            'events': {
                'required_nodes': [
                    'core_dj_identity',
                    'format_channels',
                    'format_tone',
                    'format_meta_tags_guide',
                    'format_roles_detailed',
                    'format_station_characteristics',
                    'format_dialogue_examples',
                    'instruction_events',
                    'data_events_report',
                    'user_local_time',
                    'user_basic',
                    'weather_current',
                    'user_persona',
                    'user_profile',
                    'conversation_recent'
                ],
                'use_ai_picker': False
            },
            'shoutouts': {
                'required_nodes': [
                    'core_dj_identity',
                    'format_channels',
                    'format_tone',
                    'format_meta_tags_guide',
                    'format_roles_detailed',
                    'format_station_characteristics',
                    'format_dialogue_examples',
                    'instruction_shoutouts',
                    'data_shoutouts_data',
                    'user_local_time',
                    'user_basic',
                    'weather_current',
                    'conversation_recent'
                ],
                'use_ai_picker': False
            },
            'announcements': {
                'required_nodes': [
                    'core_dj_identity',
                    'format_channels',
                    'format_tone',
                    'format_meta_tags_guide',
                    'format_roles_detailed',
                    'format_station_characteristics',
                    'format_dialogue_examples',
                    'instruction_announcements',
                    'station_recent_airings',
                    'listener_notes',
                    'bank_talking_points'
                ],
                'use_ai_picker': False,
                'time_presets': {
                    'minimal': {
                        'max_time': 5,
                        'nodes': [
                            'track_title_artist', 'queue_next_track',
                            'user_local_time', 'conversation_recent'
                        ]
                    },
                    'quick': {
                        'max_time': 10,
                        'nodes': [
                            'track_title_artist', 'track_style_description',
                            'queue_next_track', 'queue_next_details',
                            'user_local_time', 'user_basic', 'weather_current',
                            'station_current_show', 'conversation_recent'
                        ]
                    },
                    'standard': {
                        'max_time': 15,
                        'nodes': [
                            'track_title_artist', 'track_duration', 'track_style_description', 'track_audio_features_full',
                            'queue_next_track', 'queue_next_details',
                            'history_last_track',
                            'station_previous_show', 'station_current_show', 'station_next_show',
                            'user_local_time', 'user_basic', 'weather_current',
                            'user_favorite_artists', 'conversation_recent'
                        ]
                    },
                    'full': {
                        'max_time': 20,
                        'nodes': [
                            'track_title_artist', 'track_release_date', 'track_duration',
                            'track_vocal_info', 'track_style_description', 'track_audio_features_full',
                            'queue_next_track', 'queue_next_details', 'queue_next_audio_features',
                            'history_last_track', 'history_last_audio_features',
                            'station_previous_show', 'station_current_show', 'station_next_show',
                            'user_local_time', 'user_basic', 'weather_current',
                            'user_favorite_artists', 'user_banned_tracks',
                            'instruction_shoutouts', 'data_shoutouts_data',
                            'conversation_recent'
                        ]
                    },
                    'extended': {
                        'max_time': 25,
                        'nodes': [
                            'track_title_artist', 'track_release_date', 'track_duration',
                            'track_vocal_info', 'track_style_description', 'track_audio_features_full',
                            'track_progress', 'track_lyrics_preview',
                            'queue_next_track', 'queue_next_details', 'queue_next_audio_features',
                            'queue_upcoming_track', 'queue_upcoming_audio_features',
                            'history_last_track', 'history_last_audio_features',
                            'station_previous_show', 'station_current_show', 'station_next_show',
                            'user_local_time', 'user_basic', 'weather_current',
                            'user_favorite_artists', 'user_banned_tracks',
                            'instruction_shoutouts', 'data_shoutouts_data',
                            'conversation_recent'
                        ]
                    },
                    'everything': {
                        'max_time': 999,
                        'nodes': [
                            'track_title_artist', 'track_release_date', 'track_duration',
                            'track_vocal_info', 'track_style_description', 'track_audio_features_full',
                            'track_progress', 'track_lyrics_preview',
                            'queue_next_track', 'queue_next_details', 'queue_next_audio_features',
                            'queue_upcoming_track', 'queue_upcoming_audio_features',
                            'history_last_track', 'history_last_audio_features',
                            'station_previous_show', 'station_current_show', 'station_next_show',
                            'user_local_time', 'user_basic', 'weather_current',
                            'user_favorite_artists', 'user_banned_tracks',
                            'instruction_shoutouts', 'data_shoutouts_data',
                            'conversation_recent'
                        ]
                    }
                }
            },
            'radio_segment': {
                'required_nodes': RADIO_SEGMENT_BASE_NODES + [
                    'station_recent_airings',
                    'listener_notes'
                ],
                'use_ai_picker': False
            },
            'radio_segment_shared': {
                'required_nodes': list(RADIO_SEGMENT_BASE_NODES),
                'use_ai_picker': False
            },
        }

    def _select_time_preset(self, time_presets: dict, time_remaining: float) -> dict:
        sorted_presets = sorted(
            time_presets.items(),
            key=lambda x: x[1]['max_time']
        )

        for preset_name, preset_config in sorted_presets:
            if time_remaining <= preset_config['max_time']:
                return {
                    'name': preset_name,
                    'nodes': preset_config['nodes']
                }

        last_preset_name, last_preset_config = sorted_presets[-1]
        return {
            'name': last_preset_name,
            'nodes': last_preset_config['nodes']
        }

    async def _get_nodes_unified(
        self,
        gpt_type: str,
        user_id: int,
        session_id: str,
        user_input: str | None = None,
        time_remaining: float | None = None,
        dependencies: Dict | None = None,
        route_out: Dict | None = None,
        **extra_kwargs
    ) -> tuple[Dict[str, str], List[str], str | None]:

        config = self.node_configs.get(gpt_type)
        if not config:
            raise ValueError(f"Unknown GPT type: {gpt_type}")

        raw_data = dict(dependencies) if dependencies else await self._gather_dependencies(user_id, session_id)
        raw_data.update(extra_kwargs)
        raw_data.setdefault('user_input', user_input)

        required_nodes = config['required_nodes'].copy()
        final_nodes = required_nodes.copy()
        dynamic_nodes = []

        if time_remaining is not None and 'time_presets' in config:
            preset = self._select_time_preset(config['time_presets'], time_remaining)
            if preset:
                log_service.node_producer(f"[{gpt_type.upper()}] Time-based preset selected: {preset['name']} ({time_remaining}s)")
                for node in preset['nodes']:
                    if node not in final_nodes:
                        final_nodes.append(node)

        if config['use_ai_picker']:
            if not user_input:
                raise ValueError(f"GPT type '{gpt_type}' requires user_input for AI picker")

            route = await context_router_service.determine_route(user_input=user_input, use_cache=True)
            dynamic_nodes = route["nodes"]
            if route_out is not None:
                route_out.update(route)
                from services_radio.context_nodes import resolve_tool_route
                await resolve_tool_route(route_out, **raw_data)
                raw_data['route'] = route_out

            for node in dynamic_nodes:
                if node not in final_nodes:
                    final_nodes.append(node)

        context_data = await node_registry.fetch_nodes(node_keys=final_nodes, **raw_data)

        system_prompt = assemble_prompt(context_data, final_nodes)
        debug_timestamp = self._save_prompt_debug(
            gpt_type=gpt_type,
            user_input=user_input or f"{gpt_type} request",
            required_nodes=required_nodes,
            dynamic_nodes=dynamic_nodes,
            all_nodes=final_nodes,
            system_prompt=system_prompt,
            context_data=context_data
        )

        return context_data, final_nodes, debug_timestamp

    async def _gather_dependencies(self, user_id, session_id) -> Dict:
        return await gather_raw_dependencies(
            user_id=user_id,
            session_id=session_id,
            async_session_maker=self.async_session_maker,
            playback_service=self.playback_service,
            audio_features_service=self.orchestrator.features if self.orchestrator else None,
            catalog_service=self.catalog_service,
            dj_service=self
        )

    async def _listener_has_location(self, user_id, needs_coordinates: bool, session_id=None) -> bool:
        user = None
        if user_id:
            from services.user_data_cache_service import user_data_cache
            user = await user_data_cache.get_user(user_id)
            if user is None:
                return False
        location = await location_resolver.resolve(user, session_id, geocode=False)
        if location.has_coordinates:
            return True
        return bool(location.address or location.city) and not needs_coordinates

    async def _unavailable_segment(self, gpt_type: str, user_id, session_id=None) -> UnavailableSegment:
        host, cohost = SEGMENT_HOSTS.get(gpt_type, ('LEO', 'TARA'))
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

    @lru_cache(maxsize=1)
    def get_all_paralanguage_meta_tags(self):
        return self.vector_db_service.titles('meta_embeddings')

    @lru_cache(maxsize=1)
    def get_all_audio_meta_tags(self):
        return self.vector_db_service.titles('audio_embeddings')

    @lru_cache(maxsize=1)
    def get_all_correlated_tags(self):
        return [
            ("*leans back and stretches arms*", "%chair squeaking%"),
            ("*laughs heartily*", "%chair rolling slightly%"),
            ("*excited*", "%taps microphone%"),
            ("*sighs deeply*", "%coffee mug clinking%"),
            ("*clears throat*", "%papers shuffling%"),
            ("*yawns*", "%keyboard typing%"),
            ("*gasps in surprise*", "%pen dropping%"),
            ("*chuckles softly*", "%fingers drumming on desk%"),
            ("*takes a deep breath*", "%chair creaking%"),
            ("*sneezes*", "%tissue being pulled from box%"),
            ("*hums thoughtfully*", "%pencil tapping%"),
            ("*whispers excitedly*", "%soft popping on mic%"),
            ("*groans in frustration*", "%crumpling paper%"),
            ("*laughs nervously*", "%fidgeting with pen%"),
            ("*inhales sharply*", "%mic drop%")
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

    @staticmethod
    def _review_step(text: str, calls: list) -> str | None:
        if "[TASK]" not in text:
            return ("[STUDIO] No [TASK] sign-off yet: did you do what you told the listener you'd do? If not, do it "
                    "now. Then sign off with [TASK]. Your line already aired, so don't repeat it.")
        if not PARTIAL_SIGN_OFF.search(text.rsplit("[TASK]", 1)[1]):
            return None
        return ("[STUDIO] You signed off partial: do what you told the listener you'd do now, then sign off again. "
                "Your line already aired, so don't repeat it.")

    @staticmethod
    def _split_interactive_response(response_text: str) -> tuple[str, str]:
        cut = min((index for index in (response_text.find("[INTERNAL DIALOGUE]"), response_text.find("[TASK]"))
                   if index >= 0), default=len(response_text))
        return response_text[:cut].strip(), response_text[cut:].strip()

    @gpt_error_handler
    async def gpt_dj_interactive_tools(self, transcription, session_dict, tool_runtime, on_preamble=None,
                                       on_route=None) -> dict:
        from services_radio.dj_tools import (
            declarations_for,
            READ_TOOLS,
            TOOL_MODE_REPLACED_NODES,
            UNTRUSTED_NODE_KEYS,
        )

        user_id = session_dict.get('user_id')
        session_id = session_dict.get('session_id')

        log_service.node_performance(f"🎙️ DJ Interactive (Tool Mode) - User {user_id or 'Guest'}")

        start_time = time.perf_counter()

        route = {}
        context_data, selected_nodes, debug_timestamp = await self._get_nodes_unified(
            gpt_type='interactive_tools',
            user_id=user_id,
            session_id=session_id,
            user_input=transcription,
            route_out=route
        )

        fetch_time = (time.perf_counter() - start_time) * 1000
        route["context_nodes"] = [node for node in selected_nodes if context_data.get(node)]
        if on_route is not None:
            await on_route(route)

        ordered_nodes = [node for node in selected_nodes if node not in TOOL_MODE_REPLACED_NODES]

        system_prompt = assemble_prompt(context_data, ordered_nodes, untrusted_keys=UNTRUSTED_NODE_KEYS, note=None)

        log_service.gpt(f"Interactive Tools: Prompt System: {system_prompt}")

        user_message = f"[LISTENER TXT] {transcription}"
        log_service.gpt(f"Interactive Tools: Prompt User: {user_message}")

        spoken_preambles = []
        preamble_notes = []

        async def handle_preamble(raw_text, calls=()):
            preamble_main = ""
            if raw_text and raw_text.strip() and NA_MARKER not in raw_text:
                cleaned = clean_gpt_output(raw_text, role='dj_interactive')
                preamble_main, notes = self._split_interactive_response(cleaned)
                if notes:
                    preamble_notes.append(notes)
            if preamble_main:
                spoken_preambles.append(preamble_main)
            if on_preamble is not None:
                await on_preamble(preamble_main, calls)

        result = await self.gemini_service.run_gemini_tool_turn(
            system_instruction=system_prompt,
            user_message=user_message,
            function_declarations=declarations_for(tool_runtime.ctx.planned),
            refresh_tools=lambda: declarations_for(tool_runtime.ctx.planned, tool_runtime.ctx.granted),
            review=self._review_step,
            dispatch=tool_runtime.dispatch,
            temperature=self.config['dj_temperature'],
            max_tokens=self.config['dj_tokens'],
            max_rounds=settings.DJ_TOOL_MAX_ROUNDS,
            spec=LLM_DJ,
            thinking_budget=settings.DJ_TOOL_THINKING_BUDGET,
            call_timeout_s=settings.DJ_TOOL_CALL_TIMEOUT_S,
            on_preamble=handle_preamble,
            followup_tools=READ_TOOLS
        )

        raw_response = result.get("text") or ""
        response_text = clean_gpt_output(raw_response, role='dj_interactive')

        if settings.PROMPT_DEBUG_ENABLED and debug_timestamp:
            import asyncio
            asyncio.create_task(self._async_save_response_debug('interactive_tools', debug_timestamp, response_text))

        log_service.api(f"Interactive Tools: Raw Response ({result.get('rounds')} rounds, "
                        f"{len(result.get('tool_calls') or [])} tool calls): {response_text}")

        if NA_MARKER in raw_response and not spoken_preambles:
            log_service.api("Interactive Tools: Response is not applicable ([N/A])")
            return {"status": "na", "main": "", "notes": "", "preambles": [], "tool_calls": result.get("tool_calls")}

        main_response, notes_section = self._split_interactive_response(response_text)

        log_service.node_performance(
            f"✅ Node System (Tool Mode): {fetch_time:.1f}ms fetch | "
            f"Selected {len(ordered_nodes)} nodes | {(time.perf_counter() - start_time) * 1000:.1f}ms total"
        )

        return {
            "status": "ok",
            "main": main_response,
            "notes": " ".join([note.split("[TASK]", 1)[0] if "[TASK]" in notes_section else note
                               for note in preamble_notes] + [notes_section]).strip(),
            "preambles": spoken_preambles,
            "tool_calls": result.get("tool_calls") or [],
            "rounds": result.get("rounds")
        }

    def _save_prompt_debug(
            self,
            gpt_type: str,
            user_input: str,
            required_nodes: List[str],
            dynamic_nodes: List[str],
            all_nodes: List[str],
            system_prompt: str,
            context_data: Dict[str, str]
    ):
        if not settings.PROMPT_DEBUG_ENABLED:
            return None

        try:
            debug_dir = Path(settings.PROMPT_DEBUG_DIR)
            debug_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")[:-3]
            filename = f"{gpt_type}_{timestamp}.json"
            filepath = debug_dir / filename

            estimated_tokens = len(system_prompt) // 4

            debug_data = {
                "gpt_type": gpt_type,
                "timestamp": timestamp,
                "user_input": user_input,
                "required_nodes": required_nodes,
                "dynamic_nodes": dynamic_nodes,
                "all_nodes": all_nodes,
                "node_count_total": len(all_nodes),
                "node_count_required": len(required_nodes),
                "node_count_dynamic": len(dynamic_nodes),
                "system_prompt": system_prompt,
                "prompt_length_chars": len(system_prompt),
                "estimated_tokens": estimated_tokens,
                "node_outputs": {
                    node: {
                        "content": context_data.get(node, ""),
                        "length_chars": len(context_data.get(node, ""))
                    }
                    for node in all_nodes
                }
            }

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(debug_data, f, indent=2, ensure_ascii=False)

            txt_filename = f"{gpt_type}_{timestamp}.txt"
            txt_filepath = debug_dir / txt_filename

            with open(txt_filepath, 'w', encoding='utf-8') as f:
                f.write("=" * 80 + "\n")
                f.write(f"PROMPT DEBUG - {gpt_type.upper()} - {timestamp}\n")
                f.write("=" * 80 + "\n\n")
                f.write(f"GPT Type: {gpt_type}\n")
                f.write(f"User Input: {user_input}\n\n")

                f.write(f"REQUIRED Nodes ({len(required_nodes)}): {', '.join(required_nodes)}\n")
                if dynamic_nodes:
                    f.write(f"DYNAMIC Nodes ({len(dynamic_nodes)}): {', '.join(dynamic_nodes)}\n")
                else:
                    f.write("DYNAMIC Nodes (0): None (static GPT function)\n")
                f.write(f"TOTAL Nodes ({len(all_nodes)})\n\n")

                f.write(f"Estimated Tokens: {estimated_tokens}\n")
                f.write(f"Prompt Length: {len(system_prompt)} chars\n\n")
                f.write("=" * 80 + "\n")
                f.write("FULL SYSTEM PROMPT\n")
                f.write("=" * 80 + "\n\n")
                f.write(system_prompt)
                f.write("\n\n")
                f.write("=" * 80 + "\n")
                f.write("NODE BREAKDOWN\n")
                f.write("=" * 80 + "\n\n")
                for i, node in enumerate(all_nodes, 1):
                    content = context_data.get(node, "")
                    node_type = "REQUIRED" if node in required_nodes else "DYNAMIC"
                    f.write(f"{i}. [{node_type}] {node} ({len(content)} chars)\n")
                    f.write("-" * 80 + "\n")
                    f.write(content)
                    f.write("\n\n")

            log_service.node_performance(f"📝 Saved {gpt_type} prompt debug: {filename} + {txt_filename}")
            return timestamp

        except Exception as e:
            log_service.error(f"Failed to save prompt debug: {e}")
            return None

    def _append_response_to_debug(self, gpt_type: str, timestamp: str, response: str):
        if not timestamp:
            return

        try:
            debug_dir = Path(settings.PROMPT_DEBUG_DIR)

            json_filepath = debug_dir / f"{gpt_type}_{timestamp}.json"
            if json_filepath.exists():
                with open(json_filepath, 'r', encoding='utf-8') as f:
                    debug_data = json.load(f)

                debug_data['gpt_response'] = response
                debug_data['response_length_chars'] = len(response) if response else 0

                with open(json_filepath, 'w', encoding='utf-8') as f:
                    json.dump(debug_data, f, indent=2, ensure_ascii=False)

            txt_filepath = debug_dir / f"{gpt_type}_{timestamp}.txt"
            if txt_filepath.exists():
                with open(txt_filepath, 'a', encoding='utf-8') as f:
                    f.write("=" * 80 + "\n")
                    f.write("GPT RESPONSE\n")
                    f.write("=" * 80 + "\n\n")
                    if response:
                        f.write(response)
                        f.write(f"\n\nResponse Length: {len(response)} chars\n")
                    else:
                        f.write("[N/A] - GPT returned None\n")
                    f.write("\n")

            log_service.node_performance(f"📝 Appended response to {gpt_type} debug: {timestamp}")

        except Exception as e:
            log_service.error(f"Failed to append response to debug: {e}")

    @gpt_error_handler
    async def gpt_dj_announcements(self, transition_duration_ms, session_dict):
        time_remaining = transition_duration_ms / 1000.0 if transition_duration_ms else 0.0

        context_data, final_nodes, debug_timestamp = await self._get_nodes_unified(
            gpt_type='announcements',
            user_id=session_dict.get('user_id'),
            session_id=session_dict.get('session_id'),
            time_remaining=time_remaining,
            transition_duration_ms=transition_duration_ms
        )

        system_prompt = assemble_prompt(context_data, final_nodes)
        log_service.gpt(f"Announcer: Announcements Prompt: {system_prompt}")

        response_text = await self._execute_gpt_and_save(
            gpt_type='announcements',
            debug_timestamp=debug_timestamp or "",
            messages=[{"role": "system", "content": system_prompt}],
            clean_role='dj_announcements',
            role=LLM_ANNOUNCE,
            validate_script=True
        )

        log_service.api(f"Announcer: Announcements Raw Response: {response_text}")

        if "[N/A]" in response_text:
            log_service.api("Announcer: Announcements response is not applicable ([N/A])")
            return None
        response_text = response_text.strip().strip('"')
        return response_text

    @gpt_error_handler
    async def gpt_radio_segment(self, segment_spec: Dict, facts_text: str, session_dict, shared: bool = False):
        gpt_type = 'radio_segment_shared' if shared else 'radio_segment'
        context_data, final_nodes, debug_timestamp = await self._get_nodes_unified(
            gpt_type=gpt_type,
            user_id=session_dict.get('user_id'),
            session_id=session_dict.get('session_id'),
            radio_segment=segment_spec,
            radio_facts=facts_text
        )

        system_prompt = assemble_prompt(context_data, final_nodes)
        log_service.announcer(f"[RADIO] Segment prompt ({segment_spec.get('label')}): {len(system_prompt)} chars")
        length_note = (
            f"\n\nLENGTH: This is a scheduled radio segment, not a quick link. Use between "
            f"{segment_spec.get('min_words')} and {segment_spec.get('max_words')} spoken words in total across all "
            f"hosts, aiming for about {segment_spec.get('target_words') or segment_spec.get('max_words')} "
            "(tags and cues don't count)."
        )

        response_text = await self._execute_gpt_and_save(
            gpt_type=gpt_type,
            debug_timestamp=debug_timestamp or "",
            messages=[{"role": "system", "content": system_prompt}],
            clean_role='dj_content',
            max_tokens=settings.RADIO_SEGMENT_MAX_TOKENS,
            role=LLM_INTERPRET,
            validate_script=True,
            provider_notes={"deepseek": RADIO_SEGMENT_MARKUP_NOTE + length_note}
        )

        if not response_text or NA_MARKER in response_text:
            return None
        return response_text.strip().strip('"')
