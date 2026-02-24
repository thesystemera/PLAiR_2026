from typing import Dict, List, Optional, Any, Callable
from services import log_service
from services.base_service import SingletonService
from services.playback_state import PlaybackState
from services.playback_population_service import PlaybackPopulationService

class PlaybackService(SingletonService):
    def __init__(self, catalog_service=None, vector_search_service=None):
        if self._initialized:
            return

        self.catalog = catalog_service
        self.vector_search = vector_search_service
        self.population = PlaybackPopulationService(catalog_service, vector_search_service) if catalog_service else None
        self.sessions: Dict[str, PlaybackState] = {}
        self.session_callbacks: Dict[str, List[Callable]] = {}

        self._initialized = True

    async def initialize(self):
        log_service.system("PlaybackService initialized - multi-session support enabled")

    def has_session(self, session_id: str) -> bool:
        return session_id in self.sessions

    def get_session_state(self, session_id: str) -> PlaybackState:
        if session_id not in self.sessions:
            self.sessions[session_id] = PlaybackState(
                session_id=session_id,
                catalog_service=self.catalog,
                vector_search_service=self.vector_search,
                population_service=self.population,
            )
            log_service.station(f"Created new playback state for session: {session_id}")

        return self.sessions[session_id]

    def register_session_callback(self, session_id: str, callback):
        if session_id not in self.session_callbacks:
            self.session_callbacks[session_id] = []
        self.session_callbacks[session_id].append(callback)
        return callback

    def unregister_session_callback(self, session_id: str, callback):
        if session_id in self.session_callbacks:
            try:
                self.session_callbacks[session_id].remove(callback)
                if not self.session_callbacks[session_id]:
                    del self.session_callbacks[session_id]
            except ValueError:
                pass

    async def _notify_session_change(self, session_id: str):
        if session_id in self.session_callbacks:
            state = self.get_session_state(session_id).get_state()
            callbacks = list(self.session_callbacks[session_id])
            for callback in callbacks:
                try:
                    await callback(state)
                except Exception as e:
                    log_service.error(f"Session callback error for {session_id}: {str(e)}")
                    import traceback
                    log_service.error(f"Traceback: {traceback.format_exc()}")

    async def play(self, session_id: str, track_id: Optional[str] = None, user_id: Optional[int] = None):
        state = self.get_session_state(session_id)
        async def notify(_):
            await self._notify_session_change(session_id)
        return await state.play(track_id=track_id, user_id=user_id, notify_callback=notify)

    async def pause(self, session_id: str):
        state = self.get_session_state(session_id)
        async def notify(_):
            await self._notify_session_change(session_id)
        return await state.pause(notify_callback=notify)

    async def stop(self, session_id: str):
        state = self.get_session_state(session_id)
        async def notify(_):
            await self._notify_session_change(session_id)
        return await state.stop(notify_callback=notify)

    async def next(self, session_id: str, user_id: Optional[int] = None, skip_reason: Optional[str] = None):
        state = self.get_session_state(session_id)
        async def notify(_):
            await self._notify_session_change(session_id)
        return await state.next(user_id=user_id, notify_callback=notify, skip_reason=skip_reason)

    async def previous(self, session_id: str):
        state = self.get_session_state(session_id)
        async def notify(_):
            await self._notify_session_change(session_id)
        return await state.previous(notify_callback=notify)

    async def seek(self, session_id: str, position_ms: int):
        state = self.get_session_state(session_id)
        async def notify(_):
            await self._notify_session_change(session_id)
        return await state.seek(position_ms=position_ms, notify_callback=notify)

    async def add_to_queue(self, session_id: str, track_ids: List[str],
                           position: Optional[int] = None, user_id: Optional[int] = None):
        state = self.get_session_state(session_id)
        async def notify(_):
            await self._notify_session_change(session_id)
        return await state.add_to_queue(
            track_ids=track_ids,
            position=position,
            user_id=user_id,
            notify_callback=notify
        )

    async def remove_from_queue(self, session_id: str, track_id: str, user_id: Optional[int] = None):
        state = self.get_session_state(session_id)
        async def notify(_):
            await self._notify_session_change(session_id)
        return await state.remove_from_queue(
            track_id=track_id,
            user_id=user_id,
            notify_callback=notify
        )

    async def seed_radio(self, session_id: str, category: str = "all",
                         track_id: Optional[str] = None, user_id: Optional[int] = None):
        state = self.get_session_state(session_id)
        async def notify(_):
            await self._notify_session_change(session_id)
        return await state.seed_radio(
            category=category,
            track_id=track_id,
            user_id=user_id,
            notify_callback=notify
        )

    async def handle_preference_change(self, session_id: str, user_id: int,
                                       track_id: str, preference_type: str):
        state = self.get_session_state(session_id)
        async def notify(_):
            await self._notify_session_change(session_id)
        return await state.handle_preference_change(
            user_id=user_id,
            track_id=track_id,
            preference_type=preference_type,
            notify_callback=notify
        )

    def get_state(self, session_id: str, simplified: bool = True) -> Dict[str, Any]:
        state = self.get_session_state(session_id)
        return state.get_state(simplified=simplified)

    def set_active_device(self, session_id: str, device_id: str):
        state = self.get_session_state(session_id)
        state.active_device_id = device_id

    async def initialize_new_session(self, session_id: str, user_id: Optional[int] = None):
        state = self.get_session_state(session_id)
        async def notify(_):
            await self._notify_session_change(session_id)

        if len(state.queue) > 0:
            log_service.system(f"[PlaybackService] Session {session_id} already initialized")
            return

        log_service.system(f"[PlaybackService] Initializing new session: {session_id}")

        if self.catalog and self.catalog.tracks:
            all_track_ids = list(self.catalog.tracks.keys())
            if all_track_ids:
                import random
                from services.analytics_service import analytics_service

                history_tracks = []

                top_hits = await analytics_service.get_top_hits(period="all", limit=30)

                if not top_hits:
                    raise Exception("Analytics returned no all-time top hits - analytics may not be initialized!")

                valid_hits = [hit["track_id"] for hit in top_hits if hit["track_id"] in all_track_ids]

                if not valid_hits:
                    raise Exception(f"No valid top hits found in catalog! Top hits: {len(top_hits)}, Catalog: {len(all_track_ids)}")

                if len(valid_hits) >= 6:
                    random.shuffle(valid_hits)
                    history_tracks = [self.catalog.get_track(tid) for tid in valid_hits[:5]]
                    history_tracks = [t for t in history_tracks if t]

                    remaining = [tid for tid in valid_hits[5:] if tid not in [t["id"] for t in history_tracks]]
                    if remaining:
                        seed_track = self.catalog.get_track(remaining[0])
                    else:
                        seed_track = self.catalog.get_track(valid_hits[0])

                    log_service.system(
                        f"[PlaybackService] Seeding from top hits with {len(history_tracks)} history tracks")
                else:
                    seed_track_id = valid_hits[0]
                    seed_track = self.catalog.get_track(seed_track_id)
                    log_service.system(f"[PlaybackService] Seeding from top hits (week) - {len(valid_hits)} available")

                if seed_track:
                    if history_tracks:
                        state.history = history_tracks
                        log_service.system(f"[PlaybackService] Pre-populated {len(history_tracks)} tracks into history")

                    state.queue.append(seed_track)
                    state.current_track_id = seed_track["id"]

                    log_service.success(
                        f"[PlaybackService] Seeded session {session_id} with: {seed_track.get('generation_params', {}).get('title', 'Unknown')}")

                    await state._auto_fill_queue(user_id=user_id, notify_callback=notify)

                    async with state._queue_lock:
                        state._shift_queue_to_target()

                    await state.play(user_id=user_id, notify_callback=notify)

                    log_service.success(
                        f"[PlaybackService] Session {session_id} initialized - queue: {len(state.queue)}, index: {state.current_index}, history: {len(state.history)}")
                else:
                    log_service.error(f"[PlaybackService] Failed to get seed track for session {session_id}")
            else:
                log_service.warning(f"[PlaybackService] No tracks available to seed session {session_id}")
        else:
            log_service.warning(f"[PlaybackService] Catalog not available for session {session_id}")