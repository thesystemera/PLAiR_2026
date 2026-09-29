import asyncio
import time
from typing import Dict, List, Optional, Any, Callable
from config import settings
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
        self._broadcast_callbacks: Dict[str, Callable] = {}
        self._last_access: Dict[str, float] = {}
        self._eviction_task: Optional[asyncio.Task] = None

        self._initialized = True

    async def initialize(self):
        if self._eviction_task is None or self._eviction_task.done():
            self._eviction_task = asyncio.create_task(self._evict_idle_sessions_loop())
        log_service.system("PlaybackService initialized - multi-session support enabled")

    async def _evict_idle_sessions_loop(self):
        while True:
            await asyncio.sleep(600)
            try:
                from service_registry import services
                websocket_service = services.websocket_service
                is_connected = websocket_service.has_session if websocket_service else (lambda _sid: False)
                self.evict_idle_sessions(is_connected, settings.PLAYBACK_SESSION_IDLE_TIMEOUT_S)
            except Exception as e:
                log_service.error(f"[PlaybackService] Idle session eviction failed: {e}")

    def evict_idle_sessions(self, is_connected: Callable[[str], bool], max_idle_s: float) -> int:
        now = time.time()
        evicted = 0
        for session_id in list(self.sessions.keys()):
            if is_connected(session_id):
                self._last_access[session_id] = now
                continue
            if now - self._last_access.get(session_id, now) < max_idle_s:
                continue
            state = self.sessions.pop(session_id, None)
            self._last_access.pop(session_id, None)
            self.session_callbacks.pop(session_id, None)
            self._broadcast_callbacks.pop(session_id, None)
            fill_task = getattr(state, "_fill_task", None)
            if fill_task is not None and not fill_task.done():
                fill_task.cancel()
            evicted += 1
        for session_id in list(self._last_access.keys()):
            if session_id not in self.sessions:
                del self._last_access[session_id]
        if evicted:
            log_service.system(f"[PlaybackService] Evicted {evicted} idle playback sessions ({len(self.sessions)} remaining)")
        return evicted

    def has_session(self, session_id: str) -> bool:
        return session_id in self.sessions

    def get_session_state(self, session_id: str) -> PlaybackState:
        self._last_access[session_id] = time.time()
        if session_id not in self.sessions:
            self.sessions[session_id] = PlaybackState(
                session_id=session_id,
                catalog_service=self.catalog,
                vector_search_service=self.vector_search,
                population_service=self.population,
            )
            log_service.detail(f"Created new playback state for {log_service.who(session_id)}", "playback")

        return self.sessions[session_id]

    def register_session_callback(self, session_id: str, callback):
        if session_id not in self.session_callbacks:
            self.session_callbacks[session_id] = []
        self.session_callbacks[session_id].append(callback)
        return callback

    def ensure_broadcast_callback(self, session_id: str, callback_factory: Callable[[], Callable]) -> Callable:
        callback = self._broadcast_callbacks.get(session_id)
        if callback is None:
            callback = callback_factory()
            self._broadcast_callbacks[session_id] = callback
        if callback not in self.session_callbacks.get(session_id, []):
            self.register_session_callback(session_id, callback)
        return callback

    def release_broadcast_callback(self, session_id: str):
        callback = self._broadcast_callbacks.pop(session_id, None)
        if callback is not None:
            self.unregister_session_callback(session_id, callback)

    def unregister_session_callback(self, session_id: str, callback):
        if session_id in self.session_callbacks:
            try:
                self.session_callbacks[session_id].remove(callback)
                if not self.session_callbacks[session_id]:
                    del self.session_callbacks[session_id]
            except ValueError:
                pass

    async def _notify_session_change(self, session_id: str, state: Optional[Dict[str, Any]] = None):
        if session_id in self.session_callbacks:
            if state is None:
                state = self.get_session_state(session_id).get_state()
            callbacks = list(self.session_callbacks[session_id])
            for callback in callbacks:
                try:
                    await callback(state)
                except Exception as e:
                    log_service.error(f"Session callback error for {session_id}: {str(e)}")
                    import traceback
                    log_service.error(f"Traceback: {traceback.format_exc()}")

    async def broadcast_session_state(self, session_id: str):
        if session_id in self.sessions:
            await self._notify_session_change(session_id)

    async def play(self, session_id: str, track_id: Optional[str] = None, user_id: Optional[int] = None,
                   device_id: Optional[str] = None, claim: bool = False):
        state = self.get_session_state(session_id)
        async def notify(snapshot):
            await self._notify_session_change(session_id, snapshot)
        return await state.play(track_id=track_id, user_id=user_id, notify_callback=notify,
                                device_id=device_id, claim=claim)

    async def pause(self, session_id: str):
        state = self.get_session_state(session_id)
        async def notify(snapshot):
            await self._notify_session_change(session_id, snapshot)
        return await state.pause(notify_callback=notify)

    async def stop(self, session_id: str):
        state = self.get_session_state(session_id)
        async def notify(snapshot):
            await self._notify_session_change(session_id, snapshot)
        return await state.stop(notify_callback=notify)

    async def next(self, session_id: str, user_id: Optional[int] = None, skip_reason: Optional[str] = None):
        state = self.get_session_state(session_id)
        async def notify(snapshot):
            await self._notify_session_change(session_id, snapshot)
        return await state.next(user_id=user_id, notify_callback=notify, skip_reason=skip_reason)

    async def previous(self, session_id: str, user_id: Optional[int] = None):
        state = self.get_session_state(session_id)
        async def notify(snapshot):
            await self._notify_session_change(session_id, snapshot)
        return await state.previous(user_id=user_id, notify_callback=notify)

    async def seek(self, session_id: str, position_ms: int):
        state = self.get_session_state(session_id)
        async def notify(snapshot):
            await self._notify_session_change(session_id, snapshot)
        return await state.seek(position_ms=position_ms, notify_callback=notify)

    async def add_to_queue(self, session_id: str, track_ids: List[str],
                           position: Optional[int] = None, user_id: Optional[int] = None):
        state = self.get_session_state(session_id)
        async def notify(snapshot):
            await self._notify_session_change(session_id, snapshot)
        return await state.add_to_queue(
            track_ids=track_ids,
            position=position,
            user_id=user_id,
            notify_callback=notify
        )

    async def remove_from_queue(self, session_id: str, track_id: str, user_id: Optional[int] = None):
        state = self.get_session_state(session_id)
        async def notify(snapshot):
            await self._notify_session_change(session_id, snapshot)
        return await state.remove_from_queue(
            track_id=track_id,
            user_id=user_id,
            notify_callback=notify
        )

    async def seed_radio(self, session_id: str, category: str = "all",
                         track_id: Optional[str] = None, user_id: Optional[int] = None):
        state = self.get_session_state(session_id)
        async def notify(snapshot):
            await self._notify_session_change(session_id, snapshot)
        return await state.seed_radio(
            category=category,
            track_id=track_id,
            user_id=user_id,
            notify_callback=notify
        )

    async def handle_preference_change(self, session_id: str, user_id: int,
                                       track_id: str, preference_type: str):
        state = self.get_session_state(session_id)
        async def notify(snapshot):
            await self._notify_session_change(session_id, snapshot)
        return await state.handle_preference_change(
            user_id=user_id,
            track_id=track_id,
            preference_type=preference_type,
            notify_callback=notify
        )

    def get_state(self, session_id: str, simplified: bool = True) -> Dict[str, Any]:
        state = self.get_session_state(session_id)
        return state.get_state(simplified=simplified)

    async def transfer_playback(self, session_id: str, device_id: str, play: Optional[bool] = None,
                                requester_device_id: Optional[str] = None, seq=None) -> bool:
        state = self.get_session_state(session_id)
        async def notify(snapshot):
            await self._notify_session_change(session_id, snapshot)
        return await state.transfer(device_id, play=play, notify_callback=notify,
                                    requester_device_id=requester_device_id, seq=seq)

    async def claim_playback(self, session_id: str, device_id: str, play: Optional[bool] = None) -> bool:
        state = self.get_session_state(session_id)
        async def notify(snapshot):
            await self._notify_session_change(session_id, snapshot)
        return await state.claim(device_id, play=play, notify_callback=notify)

    async def initialize_new_session(self, session_id: str, user_id: Optional[int] = None):
        state = self.get_session_state(session_id)
        async with state.init_lock:
            await self._initialize_new_session_unlocked(state, session_id, user_id)

    async def _initialize_new_session_unlocked(self, state: PlaybackState, session_id: str, user_id: Optional[int]):
        async def notify(snapshot):
            await self._notify_session_change(session_id, snapshot)

        if len(state.queue) > 0:
            log_service.detail(f"[PlaybackService] {log_service.who(session_id)} already initialized", "playback")
            return

        if self.catalog and self.catalog.tracks:
            all_track_ids = set(self.catalog.tracks.keys())
            if all_track_ids:
                import random
                from services.analytics_service import analytics_service

                history_tracks = []

                top_hits = await analytics_service.get_top_hits(period="all", limit=30)

                if not top_hits:
                    raise Exception("Analytics returned no all-time top hits - analytics may not be initialized!")

                hidden = getattr(self.catalog, "hidden_ids", set())
                valid_hits = [hit["track_id"] for hit in top_hits
                              if hit["track_id"] in all_track_ids and hit["track_id"] not in hidden]

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

                else:
                    seed_track_id = valid_hits[0]
                    seed_track = self.catalog.get_track(seed_track_id)

                if seed_track:
                    if history_tracks:
                        state.history = history_tracks

                    state.queue.append(seed_track)
                    state.current_track_id = seed_track["id"]

                    await state._auto_fill_queue(user_id=user_id, notify_callback=notify)

                    async with state._queue_lock:
                        state._shift_queue_to_target()

                    await state.play(user_id=user_id, notify_callback=notify)

                    log_service.playback(
                        f"{log_service.who(session_id)}: new listening session, starting with "
                        f"{log_service.track_label(seed_track)} from the top hits "
                        f"({len(state.queue)} queued, {len(state.history)} in history)")
                else:
                    log_service.error(f"{log_service.who(session_id)}: new session could not get a seed track")
            else:
                log_service.warning(f"{log_service.who(session_id)}: no tracks available to seed the new session")
        else:
            log_service.warning(f"{log_service.who(session_id)}: catalog not available for the new session")