import asyncio

from services_radio.conversation_service import save_conversation_to_database
from services.task_utils import spawn
from services import log_service
from services_radio.dj_executor_community import ExecutorCommunity
from services_radio.dj_executor_playback import ExecutorPlayback
from services_radio.dj_executor_search import ExecutorSearch
from services_radio.dj_executor_segments import INTERPRETATION_GATE_TIMEOUT_S, ExecutorSegments


class CommandExecutorService(ExecutorSearch, ExecutorPlayback, ExecutorCommunity, ExecutorSegments):
    def __init__(self, dj_prompt_service, news_service, location_service, events_service, web_service,
                 user_content_speech_enhancement_service, user_content_service, user_content_vector_search_service,
                 tts_queue_manager, sio, async_session_maker,
                 vector_search_service, playback_service, catalog_service, gemini_ai_service=None,
                 user_content_vector_db_service=None, broadcast_content_func=None, broadcast_playback_state_callback=None):
        self.dj_prompt_service = dj_prompt_service
        self.gemini_ai_service = gemini_ai_service
        self.news_service = news_service
        self.location_service = location_service
        self.events_service = events_service
        self.web_service = web_service
        self.user_content_speech_enhancement_service = user_content_speech_enhancement_service
        self.user_content_service = user_content_service
        self.user_content_vector_search_service = user_content_vector_search_service
        self.tts_queue_manager = tts_queue_manager
        self.sio = sio
        self.async_session_maker = async_session_maker
        self.vector_search_service = vector_search_service
        self.playback_service = playback_service
        self.catalog_service = catalog_service
        self.user_content_vector_db_service = user_content_vector_db_service
        self.broadcast_content_func = broadcast_content_func
        self.broadcast_playback_state_callback = broadcast_playback_state_callback

    @staticmethod
    def _track_id_of(item):
        if isinstance(item, dict):
            return item.get('id')
        return item

    def _resolve_track_id(self, session_id, target):
        state = self.playback_service.get_state(session_id) or {}
        if target == "current":
            return self._track_id_of(state.get('current_track') or {})
        if target == "previous":
            history = state.get('history') or []
            return self._track_id_of(history[-1]) if history else None
        if target == "next":
            queue = state.get('queue') or []
            next_index = (state.get('current_index') or 0) + 1 if state.get('current_track') else 0
            return self._track_id_of(queue[next_index]) if next_index < len(queue) else None
        if target and self.catalog_service is not None and self.catalog_service.get_track(target):
            return target
        return None

    def _track_label(self, track_id):
        track = self.catalog_service.get_track(track_id) if track_id else None
        if not track or not track.get('generation_params', {}).get('title'):
            return None
        params = track.get("generation_params") or {}
        artists = log_service.track_artists(track)
        return f"{params['title']} by {artists[0]}" if artists else params['title']

    def _upcoming_labels(self, session_id, limit=3):
        state = self.playback_service.get_state(session_id) or {}
        queue = state.get('queue') or []
        start = (state.get('current_index') or 0) + 1 if state.get('current_track') else 0
        labels = []
        for item in queue[start:start + limit]:
            label = self._track_label(self._track_id_of(item))
            if label:
                labels.append(label)
        return labels

    @staticmethod
    async def _await_gate(gate):
        if gate is None:
            return
        try:
            await asyncio.wait_for(gate.wait(), timeout=INTERPRETATION_GATE_TIMEOUT_S)
        except asyncio.TimeoutError:
            log_service.warning("[COMMAND EXECUTOR] Interpretation gate timed out - releasing segment")

    @staticmethod
    def spawn_segment(coro, session_dict, name):
        task = spawn(coro, name=name)
        turn_tasks = session_dict.get('_turn_tasks')
        if turn_tasks is not None:
            turn_tasks.add(task)
            task.add_done_callback(turn_tasks.discard)
        return task

    RATINGS = ("like", "super_like", "clear", "ban")

    async def _note(self, session_dict, message, kind="info"):
        user_id = session_dict.get('user_id')
        if user_id:
            async with self.async_session_maker() as db:
                await save_conversation_to_database(user_id=user_id, db=db, message_type='interactive',
                                                    **{kind: message})
        await self.sio.emit('conversation_update', {kind: message, 'message_type': 'interactive'},
                            room=session_dict.get('session_id'))

