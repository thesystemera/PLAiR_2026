import re
import time
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict
from functools import lru_cache

from services_radio.dj_prompt_helper_service import (
    filter_meta_tags_for_gpt_prompt_cleaning,
    clean_gpt_output
)
from services_radio.context_node_registry import node_registry
from services_radio.context_service import gather_raw_dependencies
from services_radio.context_router_service import context_router_service
from services import log_service
from config.settings import settings

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
            'command_model': settings.GEMINI_COMMAND_MODEL,
            'command_temperature': settings.GEMINI_COMMAND_TEMPERATURE,
            'command_tokens': settings.GEMINI_COMMAND_MAX_TOKENS,
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
            'interactive': {
                'required_nodes': [
                    'core_dj_identity',
                    'format_channels',
                    'format_tone',
                    'format_meta_tags_guide',
                    'format_roles_detailed',
                    'format_station_characteristics',
                    'format_dialogue_examples'
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
                    'instruction_announcements'
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
            'command_extraction': {
                'required_nodes': [
                    'instruction_hal11000_identity',
                    'instruction_hal11000_format_rules',
                    'instruction_hal11000_commands',
                    'instruction_hal11000_rules',
                    'instruction_hal11000_examples',
                    'instruction_hal11000_verification',
                    'history_last_track', 'track_title_artist', 'queue_next_track',
                    'user_favorite_artists', 'user_profile', 'conversation_recent'
                ],
                'use_ai_picker': False
            }
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
        **extra_kwargs
    ) -> tuple[Dict[str, str], List[str], str | None]:

        config = self.node_configs.get(gpt_type)
        if not config:
            raise ValueError(f"Unknown GPT type: {gpt_type}")

        raw_data = await gather_raw_dependencies(
            user_id=user_id,
            session_id=session_id,
            async_session_maker=self.async_session_maker,
            playback_service=self.playback_service,
            audio_features_service=self.orchestrator.features if self.orchestrator else None,
            catalog_service=self.catalog_service,
            dj_service=self
        )
        raw_data.update(extra_kwargs)

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

            dynamic_nodes = await context_router_service.determine_nodes(
                user_input=user_input,
                use_cache=True
            )

            for node in dynamic_nodes:
                if node not in final_nodes:
                    final_nodes.append(node)

        context_data = await node_registry.fetch_nodes(node_keys=final_nodes, **raw_data)

        system_prompt = "\n\n".join(context_data[node] for node in final_nodes if node in context_data and context_data[node])
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

    @lru_cache(maxsize=1)
    def get_all_paralanguage_meta_tags(self):
        return list(set(item['title'] for item in self.vector_db_service.meta_db_data.values()))

    @lru_cache(maxsize=1)
    def get_all_audio_meta_tags(self):
        return list(set(item['title'] for item in self.vector_db_service.audio_db_data.values()))

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

    async def _execute_gpt_stream(self, model: str, max_tokens: int, temperature: float, messages: list) -> str:
        system_content = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        user_content = messages[1]["content"] if len(messages) > 1 and messages[1]["role"] == "user" else messages[0][
            "content"]

        response = await self.gemini_service.call_gemini(
            prompt=user_content,
            system_instruction=system_content,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens
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
        temperature: float | None = None
    ) -> str:

        model = model or self.config['dj_model']
        max_tokens = max_tokens or self.config['dj_tokens']
        temperature = temperature or self.config['dj_temperature']

        response = await self._execute_gpt_stream(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=messages
        )

        response = clean_gpt_output(response, role=clean_role)

        if settings.PROMPT_DEBUG_ENABLED and debug_timestamp:
            import asyncio
            asyncio.create_task(self._async_save_response_debug(gpt_type, debug_timestamp, response))

        return response

    async def _async_save_response_debug(self, gpt_type: str, timestamp: str, response: str):
        try:
            self._append_response_to_debug(gpt_type, timestamp, response)
        except Exception as e:
            log_service.error(f"Failed to save response debug (non-blocking): {e}")

    async def _execute_broadcast_gpt(self, prompt_name: str, system_prompt: str, logger_type: str = 'external',
                                     clean_role: str = 'dj_content', gpt_type: str | None = None, debug_timestamp: str | None = None):

        logger = getattr(log_service, logger_type)
        logger(f"Prompt: {prompt_name} Prompt: {system_prompt}")

        response_text = await self._execute_gpt_and_save(
            gpt_type=gpt_type or 'broadcast',
            debug_timestamp=debug_timestamp,
            messages=[{"role": "system", "content": system_prompt}],
            clean_role=clean_role
        )

        logger(f"Raw Response: {prompt_name} Raw Response: {response_text}")
        return response_text.strip().strip('"')

    @gpt_error_handler
    async def gpt_biography_interpretation(self, artist_name, session_dict):
        context_data, final_nodes, debug_timestamp = await self._get_nodes_unified(
            gpt_type='biography',
            user_id=session_dict.get('user_id'),
            session_id=session_dict.get('session_id'),
            artist_name=artist_name
        )

        system_prompt = "\n\n".join(context_data[node] for node in final_nodes if node in context_data and context_data[node])
        return await self._execute_broadcast_gpt("Biography Interpretation", system_prompt,
                                                 gpt_type='biography', debug_timestamp=debug_timestamp or "")

    @gpt_error_handler
    async def gpt_lyrics_interpretation(self, lyrics, artist_name, session_dict):
        context_data, final_nodes, debug_timestamp = await self._get_nodes_unified(
            gpt_type='lyrics',
            user_id=session_dict.get('user_id'),
            session_id=session_dict.get('session_id'),
            lyrics=lyrics,
            artist_name=artist_name
        )

        system_prompt = "\n\n".join(context_data[node] for node in final_nodes if node in context_data and context_data[node])
        return await self._execute_broadcast_gpt("Lyrics Interpretation", system_prompt,
                                                 gpt_type='lyrics', debug_timestamp=debug_timestamp or "")

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

        system_prompt = "\n\n".join(context_data[node] for node in final_nodes if node in context_data and context_data[node])
        return await self._execute_broadcast_gpt("News", system_prompt,
                                                 gpt_type='news', debug_timestamp=debug_timestamp or "")

    @gpt_error_handler
    async def gpt_weather_interpretation(self, session_dict, forecast_type: str = "current"):
        context_data, final_nodes, debug_timestamp = await self._get_nodes_unified(
            gpt_type='weather',
            user_id=session_dict.get('user_id'),
            session_id=session_dict.get('session_id'),
            forecast_type=forecast_type
        )

        system_prompt = "\n\n".join(context_data[node] for node in final_nodes if node in context_data and context_data[node])
        return await self._execute_broadcast_gpt("Weather", system_prompt,
                                                 gpt_type='weather', debug_timestamp=debug_timestamp or "")

    @gpt_error_handler
    async def gpt_location_search_interpretation(self, query, session_dict):
        context_data, final_nodes, debug_timestamp = await self._get_nodes_unified(
            gpt_type='location_search',
            user_id=session_dict.get('user_id'),
            session_id=session_dict.get('session_id'),
            query=query
        )

        system_prompt = "\n\n".join(context_data[node] for node in final_nodes if node in context_data and context_data[node])
        return await self._execute_broadcast_gpt("Location Search Interpretation", system_prompt,
                                                 gpt_type='location_search', debug_timestamp=debug_timestamp or "")

    @gpt_error_handler
    async def gpt_events_interpretation(self, location, country_code, start_date, end_date, session_dict):
        context_data, final_nodes, debug_timestamp = await self._get_nodes_unified(
            gpt_type='events',
            user_id=session_dict.get('user_id'),
            session_id=session_dict.get('session_id'),
            location=location,
            country_code=country_code,
            start_date=start_date,
            end_date=end_date
        )

        system_prompt = "\n\n".join(context_data[node] for node in final_nodes if node in context_data and context_data[node])
        return await self._execute_broadcast_gpt("Events Search", system_prompt,
                                                 gpt_type='events', debug_timestamp=debug_timestamp or "")

    @gpt_error_handler
    async def gpt_shoutouts_interpretation(self, session_dict, query: str | None = None, n_results: int = 10):

        context_data, final_nodes, debug_timestamp = await self._get_nodes_unified(
            gpt_type='shoutouts',
            user_id=session_dict.get('user_id'),
            session_id=session_dict.get('session_id'),
            query=query,
            n_results=n_results
        )

        system_prompt = "\n\n".join(context_data[node] for node in final_nodes if node in context_data and context_data[node])
        log_service.user_content(f"Shoutouts: Shoutouts Prompt: {system_prompt}")

        response_text = await self._execute_gpt_and_save(
            gpt_type='shoutouts',
            debug_timestamp=debug_timestamp or "",
            messages=[{"role": "system", "content": system_prompt}],
            clean_role='dj_content'
        )

        log_service.user_content(f"Shoutouts: Shoutouts Raw Response: {response_text}")

        if "[N/A]" in response_text:
            log_service.user_content("Shoutouts: Shoutouts response is not applicable ([N/A])")
            return None
        return response_text.strip().strip('"')

    @gpt_error_handler
    async def gpt_dj_interactive(self, transcription, session_dict):
        user_id = session_dict.get('user_id')
        session_id = session_dict.get('session_id')

        log_service.node_performance(f"🎙️ DJ Interactive (Node System) - User {user_id or 'Guest'}")

        start_time = time.perf_counter()

        context_data, selected_nodes, debug_timestamp = await self._get_nodes_unified(
            gpt_type='interactive',
            user_id=user_id,
            session_id=session_id,
            user_input=transcription
        )

        fetch_time = (time.perf_counter() - start_time) * 1000

        system_prompt = "\n\n".join(context_data[node] for node in selected_nodes if node in context_data and context_data[node])

        log_service.gpt(f"Interactive: Prompt System: {system_prompt}")

        user_message = f"[LISTENER TXT] {transcription}"
        log_service.gpt(f"Interactive: Prompt User: {user_message}")

        response_text = await self._execute_gpt_and_save(
            gpt_type='interactive',
            debug_timestamp=debug_timestamp or "",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            clean_role='dj_interactive'
        )

        log_service.api(f"Interactive: Raw Response: {response_text}")

        if "[N/A]" in response_text:
            log_service.api("Interactive: Response is not applicable ([N/A])")
            return None

        parts = response_text.split("[INTERNAL DIALOGUE]", 1)
        main_response = parts[0].strip()
        notes_section = f"[INTERNAL DIALOGUE]{parts[1]}" if len(parts) > 1 else ""

        log_service.node_performance(
            f"✅ Node System: {fetch_time:.1f}ms total | "
            f"Selected {len(selected_nodes)} nodes"
        )

        return main_response, notes_section

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

        system_prompt = '\n\n'.join(context_data[node] for node in final_nodes if node in context_data and context_data[node])
        log_service.gpt(f"Announcer: Announcements Prompt: {system_prompt}")

        response_text = await self._execute_gpt_and_save(
            gpt_type='announcements',
            debug_timestamp=debug_timestamp or "",
            messages=[{"role": "system", "content": system_prompt}],
            clean_role='dj_announcements'
        )

        log_service.api(f"Announcer: Announcements Raw Response: {response_text}")

        if "[N/A]" in response_text:
            log_service.api("Announcer: Announcements response is not applicable ([N/A])")
            return None
        response_text = response_text.strip().strip('"')
        return response_text

    @gpt_error_handler
    async def gpt_command_extraction(self, gpt_response, transcription, session_dict):
        context_data, final_nodes, debug_timestamp = await self._get_nodes_unified(
            gpt_type='command_extraction',
            user_id=session_dict.get('user_id'),
            session_id=session_dict.get('session_id')
        )

        filtered_gpt_response = filter_meta_tags_for_gpt_prompt_cleaning(gpt_response)

        raw_history = context_data.get('conversation_recent', '')
        filtered_conversation_history = filter_meta_tags_for_gpt_prompt_cleaning(raw_history)

        context_joined = '\n\n'.join(context_data[node] for node in final_nodes if node in context_data and context_data[node])
        system_prompt = f"{context_joined}\n\nCONVERSATION HISTORY:\n{filtered_conversation_history}"
        user_message = (
            f"[LISTENER TXT] {transcription}\n"
            f"[DJ RESPONSE] {filtered_gpt_response}"
        )
        log_service.commands(f"[HAL11000 PIPELINE] System Prompt:\n{system_prompt}")
        log_service.commands(f"[HAL11000 PIPELINE] User Message:\n{user_message}")
        commands_text = await self._execute_gpt_stream(
            model=self.config['command_model'],
            max_tokens=self.config['command_tokens'],
            temperature=self.config['command_temperature'],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ]
        )
        log_service.commands(f"[HAL11000 PIPELINE] Raw GPT Response (BEFORE cleaning):\n{commands_text}")

        command_pattern = r'\(\{[a-z_]+\}(?:\{[a-z_]+\})*\)(?:"[^"]*")?|\{N/A\}'

        extracted_commands = re.findall(command_pattern, commands_text)
        log_service.commands("[HAL11000 PIPELINE] Command extraction output for forbidden blocks")

        if not extracted_commands:
            log_service.commands("[HAL11000 PIPELINE] No commands extracted (result: )")
            return ""

        formatted_commands = '\n'.join(extracted_commands)
        log_service.commands(f"[HAL11000 PIPELINE] Cleaned GPT Response (AFTER filtering):\n{formatted_commands}")

        return formatted_commands