import time
from typing import List, Dict

from services_radio.dj_prompt_helper_service import (
    clean_gpt_output,
    assemble_prompt
)
from services_radio.context_node_registry import node_registry
from services_radio.context_service import gather_raw_dependencies
from services_radio import listener_location as location_resolver
from services_radio import talk_clock
from services_radio.filler_scripts import plain_talk
from services_radio.context_router_service import context_router_service
from services import log_service
from services.llm_router import LLM_DJ, LLM_INTERPRET
from config.settings import settings
from services_radio.dj_prompt_service_segments import SegmentPrompts
from services_radio.dj_prompt_service_debug import PromptDebug
from services_radio.dj_prompt_service_configs import (
    NA_MARKER, PARTIAL_SIGN_OFF, RADIO_SEGMENT_MARKUP_NOTE, build_node_configs, gpt_error_handler,
)

class DJPromptService(SegmentPrompts, PromptDebug):
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

        self.node_configs = build_node_configs()

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
                from services_radio.context_nodes_station import resolve_tool_route
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
        notes = response_text[cut:]
        repeat = min((index for index in (notes.find("[BROADCAST]"), notes.find("[TXT]")) if index >= 0),
                     default=len(notes))
        if repeat < len(notes):
            log_service.warning(f"DJ reply repeated its script after the notes ({len(notes) - repeat} chars) - "
                                f"kept out of the notes and history")
        return response_text[:cut].strip(), notes[:repeat].strip()

    @gpt_error_handler
    async def gpt_dj_interactive_tools(self, transcription, session_dict, tool_runtime, on_preamble=None,
                                       on_route=None) -> dict:
        from services_radio.dj_tools_registry import (
            DJ_FUNCTION_DECLARATIONS,
            READ_TOOLS,
            SEGMENT_TOOLS,
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

        system_nodes = [node for node in self.node_configs['interactive_tools']['required_nodes']
                        if node_registry.is_system(node)]
        live_nodes = [node for node in selected_nodes
                      if not node_registry.is_system(node) and node not in TOOL_MODE_REPLACED_NODES]
        system_prompt = assemble_prompt(context_data, system_nodes, untrusted_keys=set(), note=None)
        live_context = assemble_prompt(context_data, live_nodes, untrusted_keys=UNTRUSTED_NODE_KEYS, note=None)

        log_service.gpt(f"Interactive Tools: Prompt System: {system_prompt}")

        aired = " / ".join(filter(None, (plain_talk(text) for text in session_dict.get('on_air') or [])))
        on_air_note = (f"[ON AIR JUST NOW] While the message was coming in, you two were thinking out loud to each "
                       f"other on a hot mic: \"{aired}\". That was just you, not the listener and not an answer: no "
                       "need to mention, correct or apologise for it. Don't repeat it; answer what the listener "
                       "actually said.\n\n") if aired else ""
        user_message = f"{live_context}\n\n{on_air_note}[LISTENER TXT] {transcription}" if live_context else \
            f"{on_air_note}[LISTENER TXT] {transcription}"
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
            function_declarations=DJ_FUNCTION_DECLARATIONS,
            review=self._review_step,
            dispatch=tool_runtime.dispatch,
            temperature=self.config['dj_temperature'],
            max_tokens=self.config['dj_tokens'],
            max_rounds=settings.DJ_TOOL_MAX_ROUNDS,
            spec=LLM_DJ,
            thinking_budget=settings.DJ_TOOL_THINKING_BUDGET,
            followup_thinking_budget=settings.DJ_TOOL_FOLLOWUP_THINKING_BUDGET,
            cache_label="dj_interactive",
            call_timeout_s=settings.DJ_TOOL_CALL_TIMEOUT_S,
            on_preamble=handle_preamble,
            followup_tools=READ_TOOLS,
            handoff_tools=SEGMENT_TOOLS,
            repeatable_tools={"playback_control"}
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
            f"{len(system_nodes)} system + {len(live_nodes)} live nodes | "
            f"{(time.perf_counter() - start_time) * 1000:.1f}ms total"
        )

        return {
            "status": "ok",
            "main": main_response,
            "notes": " ".join([note.split("[TASK]", 1)[0] if "[TASK]" in notes_section else note
                               for note in preamble_notes] + [notes_section]).strip(),
            "preambles": spoken_preambles,
            "tool_calls": result.get("tool_calls") or [],
            "rounds": result.get("rounds"),
            "trace": result.get("trace") or [],
            "user_message": user_message
        }

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
        log_service.commands(
            f"talk break {segment_spec.get('label')} | {int(segment_spec.get('seconds') or 0)}s = "
            f"{segment_spec.get('target_words')} words at {talk_clock.pace():.2f} words/s | wrote "
            f"{talk_clock.spoken_words(response_text)} words")
        return response_text.strip().strip('"')
