import asyncio
import time
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from services import log_service
from services.api_utils import simplify_track_info
from services.user_data_cache_service import user_data_cache
from services.analytics_service import analytics_service

async def safe_background_task(coro, task_name="background_task"):
    try:
        await coro
    except Exception as e:
        log_service.error(f"{task_name} failed with exception: {e}")
        import traceback
        log_service.error(f"Traceback: {traceback.format_exc()}")

class PlaybackState:
    QUEUE_SIZE = 11
    TARGET_INDEX = 5
    HISTORY_BUFFER = 5

    def __init__(self, session_id: str, catalog_service=None, vector_search_service=None, population_service=None):
        self.session_id = session_id
        self.catalog = catalog_service
        self.vector_search = vector_search_service
        self.population = population_service

        self.queue = []
        self.current_track_id = None
        self.history = []
        self.is_playing = False
        self.progress_ms = 0
        self.last_update_time = None
        self.radio_mode = 'top_hits_week'

        self._auto_filled_track_ids = set()
        self.active_device_id = None
        self._last_broadcast_time = 0

        self._current_play_start_time = None
        self._current_play_track_id = None

        self.latency_samples = []
        self.average_latency_ms = 0
        self.drift_ms = 0

        self.override_active = False
        self.override_mode = None

        self.crossfade_timing_cache = {}
        self.announcer_timing_cache = {}
        self.last_skip_reason = None

        self._queue_lock = asyncio.Lock()
        self._simulation_task = None

    @property
    def current_index(self):
        if not self.current_track_id:
            return 0
        for i, track in enumerate(self.queue):
            if track.get("id") == self.current_track_id:
                return i
        return 0

    @property
    def current_track(self):
        if not self.current_track_id:
            return None
        for track in self.queue:
            if track.get("id") == self.current_track_id:
                return track
        return None

    def get_simulated_progress(self) -> int:
        if not self.is_playing or self.last_update_time is None:
            return self.progress_ms

        elapsed = (time.time() - self.last_update_time) * 1000
        simulated = self.progress_ms + int(elapsed)

        if self.current_track:
            duration = self.current_track.get("track_info", {}).get("duration", 0)
            if duration > 0:
                simulated = min(simulated, duration)

        return simulated

    def _shift_queue_to_target(self):
        while self.current_index > self.TARGET_INDEX and self.queue:
            removed = self.queue.pop(0)
            self.history.append(removed)
            if len(self.history) > 50:
                self.history.pop(0)
            self._auto_filled_track_ids.discard(removed["id"])

    def _shift_queue_from_history(self):
        while self.current_index < self.TARGET_INDEX and self.history:
            prev_track = self.history.pop()
            self.queue.insert(0, prev_track)
            if len(self.queue) > self.QUEUE_SIZE:
                removed = self.queue.pop()
                self._auto_filled_track_ids.discard(removed["id"])

    def _enforce_queue_size(self):
        while len(self.queue) > self.QUEUE_SIZE:
            if self.current_index < len(self.queue) - 1:
                removed = self.queue.pop()
                self._auto_filled_track_ids.discard(removed["id"])
            else:
                removed = self.queue.pop(0)
                self._auto_filled_track_ids.discard(removed["id"])

    @staticmethod
    async def _get_user_preferences(user_id: Optional[int] = None):
        if not user_id:
            return {"likes": set(), "super_likes": set(), "bans": set()}
        try:
            return await user_data_cache.get_preferences(user_id)
        except Exception as e:
            log_service.error(f"Error getting user preferences: {e}")
            return {"likes": set(), "super_likes": set(), "bans": set()}

    async def _log_play_start(self, track_id: str, user_id: Optional[int] = None):
        self._current_play_track_id = track_id
        self._current_play_start_time = datetime.now(timezone.utc)
        await analytics_service.log_play_event(
            user_id=user_id,
            track_id=track_id,
            session_id=self.session_id,
            device_id=self.active_device_id,
            event_type="play"
        )

    async def _log_play_end(self, user_id: Optional[int] = None, event_type: str = "complete",
                            skip_reason: Optional[str] = None):
        if not self._current_play_track_id or not self._current_play_start_time:
            return

        track = self.current_track
        if not track:
            return

        duration_ms = int((datetime.now(timezone.utc) - self._current_play_start_time).total_seconds() * 1000)
        track_duration = track.get("track_info", {}).get("duration", 0)
        completion_pct = min(100.0, (self.progress_ms / track_duration * 100)) if track_duration else 0.0

        await analytics_service.log_play_event(
            user_id=user_id,
            track_id=self._current_play_track_id,
            session_id=self.session_id,
            device_id=self.active_device_id,
            event_type=event_type,
            skip_reason=skip_reason,
            duration_ms=duration_ms,
            completion_pct=completion_pct
        )

        self._current_play_track_id = None
        self._current_play_start_time = None

    async def _auto_fill_queue(self, user_id: Optional[int] = None, notify_callback=None):
        if self.population:
            new_tracks = await self.population.fill_queue(
                radio_mode=self.radio_mode,
                queue=list(self.queue),
                history=list(self.history),
                queue_size=self.QUEUE_SIZE,
                user_id=user_id,
                session_id=self.session_id,
            )
        else:
            new_tracks = []

        if new_tracks:
            async with self._queue_lock:
                for track in new_tracks:
                    if len(self.queue) < self.QUEUE_SIZE:
                        self.queue.append(track)
                        self._auto_filled_track_ids.add(track["id"])
                self._enforce_queue_size()

            if notify_callback:
                await notify_callback(self.get_state())

    async def _auto_fill_queue_background(self, user_id: Optional[int] = None, notify_callback=None):
        try:
            await self._auto_fill_queue(user_id=user_id, notify_callback=notify_callback)
        except Exception as e:
            log_service.error(f"[{self.session_id}] Background queue auto-fill error: {e}")

    async def play(self, track_id: Optional[str] = None, user_id: Optional[int] = None, notify_callback=None):
        async with self._queue_lock:
            if track_id:
                track = self.catalog.get_track(track_id) if self.catalog else None
                if not track:
                    log_service.error(f"Track not found: {track_id}")
                    return False

                existing_idx = next((i for i, t in enumerate(self.queue) if t["id"] == track_id), None)

                if existing_idx is not None:
                    self.current_track_id = track_id
                    self._shift_queue_to_target()
                else:
                    insert_pos = self.current_index + 1 if self.queue else 0
                    self.queue.insert(insert_pos, track)
                    self.current_track_id = track_id
                    self._shift_queue_to_target()
                    self._enforce_queue_size()

                self.progress_ms = 0
                log_service.playback(
                    f"[{self.session_id}] Playing: {track.get('generation_params', {}).get('title', 'Unknown')}")

            if not self.current_track:
                pass

        if not self.current_track:
            await self._auto_fill_queue(user_id=user_id)
            if not self.current_track:
                return False
            if self.queue:
                self.current_track_id = self.queue[0]["id"]

        self.is_playing = True
        self.last_update_time = time.time()
        await self._log_play_start(track_id=self.current_track["id"], user_id=user_id)

        if notify_callback:
            await notify_callback(self.get_state())

        asyncio.create_task(safe_background_task(
            self._auto_fill_queue_background(user_id=user_id, notify_callback=notify_callback),
            f"auto_fill_queue_{self.session_id}"
        ))

        return True

    async def pause(self, notify_callback=None):
        self.progress_ms = self.get_simulated_progress()
        self.is_playing = False
        self.last_update_time = time.time()
        log_service.playback(f"[{self.session_id}] Playback paused")
        if notify_callback:
            await notify_callback(self.get_state())
        return True

    async def stop(self, notify_callback=None):
        self.is_playing = False
        self.queue = []
        self.current_track_id = None
        self.progress_ms = 0
        self.last_update_time = None
        self._auto_filled_track_ids.clear()
        self.radio_mode = 'top_hits_week'
        log_service.playback(f"[{self.session_id}] Playback stopped")
        if notify_callback:
            await notify_callback(self.get_state())
        return True

    async def next(self, user_id: Optional[int] = None, notify_callback=None, skip_reason: Optional[str] = None):
        log_service.playback(f"[{self.session_id}] next() called via Legacy/REST API")

        async with self._queue_lock:
            if not self.queue:
                return False

            reason = skip_reason or "user_skip"
            await self._log_play_end(user_id=user_id, event_type="skip", skip_reason=reason)

            current_idx = self.current_index
            if current_idx + 1 < len(self.queue):
                self.current_track_id = self.queue[current_idx + 1]["id"]
            self._shift_queue_to_target()

        if self.current_index >= len(self.queue):
            await self._auto_fill_queue(user_id=user_id)
            async with self._queue_lock:
                if self.current_index >= len(self.queue) and self.queue:
                    self.current_track_id = self.queue[-1]["id"]

        self.progress_ms = 0
        self.last_update_time = time.time()

        if self.current_track:
            await self._log_play_start(track_id=self.current_track["id"], user_id=user_id)

        if notify_callback:
            await notify_callback(self.get_state())

        asyncio.create_task(safe_background_task(
            self._auto_fill_queue_background(user_id=user_id, notify_callback=notify_callback),
            f"auto_fill_queue_{self.session_id}"
        ))

        return True

    async def previous(self, user_id: Optional[int] = None, notify_callback=None):
        log_service.playback(f"[{self.session_id}] previous() called via Legacy/REST API")

        async with self._queue_lock:
            if not self.history:
                return False

            await self._log_play_end(user_id=user_id, event_type="skip", skip_reason="user_previous")

            prev_track = self.history.pop()
            self.queue.insert(0, prev_track)
            self.current_track_id = prev_track["id"]

            self._shift_queue_from_history()
            self._enforce_queue_size()

            self.progress_ms = 0
            self.last_update_time = time.time()

        if self.current_track:
            await self._log_play_start(track_id=self.current_track["id"], user_id=user_id)

        if notify_callback:
            await notify_callback(self.get_state())

        return True

    async def seek(self, position_ms: int, notify_callback=None):
        if not self.current_track:
            return False

        duration_ms = self.current_track.get("track_info", {}).get("duration", 0)
        self.progress_ms = max(0, min(position_ms, duration_ms) if duration_ms else position_ms)
        self.last_update_time = time.time()
        self._last_broadcast_time = 0

        log_service.playback(f"[{self.session_id}] Seeked to: {self.progress_ms}ms")
        if notify_callback:
            await notify_callback(self.get_state())
        return True

    async def add_to_queue(self, track_ids: List[str], position: Optional[int] = None,
                           user_id: Optional[int] = None, notify_callback=None):
        added = []
        async with self._queue_lock:
            existing_ids = {t["id"] for t in self.queue}

            for track_id in track_ids:
                if track_id in existing_ids:
                    continue

                track = self.catalog.get_track(track_id) if self.catalog else None
                if not track:
                    continue

                insert_pos = position if position is not None else self.current_index + 1
                self.queue.insert(insert_pos, track)

                existing_ids.add(track_id)
                added.append(track_id)

            self._enforce_queue_size()

        log_service.playback(f"[{self.session_id}] Added {len(added)} track(s) to queue")

        if notify_callback:
            await notify_callback(self.get_state())

        asyncio.create_task(safe_background_task(
            self._auto_fill_queue_background(user_id=user_id, notify_callback=notify_callback),
            f"auto_fill_queue_{self.session_id}"
        ))

        return added

    async def remove_from_queue(self, track_id: str, user_id: Optional[int] = None, notify_callback=None):
        async with self._queue_lock:
            removed_indices = [i for i, t in enumerate(self.queue) if t.get("id") == track_id]

            if not removed_indices:
                return False

            for i in sorted(removed_indices, reverse=True):
                removed_track = self.queue.pop(i)
                self._auto_filled_track_ids.discard(track_id)

                if removed_track["id"] == self.current_track_id:
                    if i < len(self.queue):
                        self.current_track_id = self.queue[i]["id"]
                    elif self.queue:
                        self.current_track_id = self.queue[-1]["id"]
                    else:
                        self.current_track_id = None
                    self.progress_ms = 0
                    self.last_update_time = time.time()

            self._shift_queue_to_target()
            self._shift_queue_from_history()

        log_service.playback(f"[{self.session_id}] Removed track from queue: {track_id}")

        if notify_callback:
            await notify_callback(self.get_state())

        asyncio.create_task(safe_background_task(
            self._auto_fill_queue_background(user_id=user_id, notify_callback=notify_callback),
            f"auto_fill_queue_{self.session_id}"
        ))

        return True

    async def seed_radio(self, category: str = "all", track_id: Optional[str] = None,
                         user_id: Optional[int] = None, notify_callback=None):
        from services.playback_population_service import is_playlist_mode

        if is_playlist_mode(category):
            self.radio_mode = category
            async with self._queue_lock:
                self.queue = []
                self.current_track_id = None
                self._auto_filled_track_ids.clear()

            log_service.playback(f"[{self.session_id}] Mode switched to: {category.title()}")

            await self._auto_fill_queue(user_id=user_id, notify_callback=notify_callback)

            if self.queue:
                async with self._queue_lock:
                    self.current_track_id = self.queue[0]["id"]
                    self.progress_ms = 0
                    self._shift_queue_to_target()

                log_service.playback(f"[{self.session_id}] Queue seeded: {self.queue[0].get('generation_params', {}).get('title', 'Unknown')}")

                if notify_callback:
                    await notify_callback(self.get_state())
            elif notify_callback:
                await notify_callback(self.get_state())

            return True

        self.radio_mode = category

        if track_id:
            seed_track = self.catalog.get_track(track_id) if self.catalog else None
            if not seed_track:
                log_service.error(f"[{self.session_id}] Seed track not found: {track_id}")
                return False
        else:
            seed_track = self.current_track or (self.history[-1] if self.history else None)

        if not seed_track:
            log_service.warning(f"[{self.session_id}] No seed track available")
            return False

        log_service.playback(f"[{self.session_id}] Seeding radio — category: {category}")

        async with self._queue_lock:
            if self.current_track:
                self.queue = [self.current_track]
            else:
                self.queue = []
                self.current_track_id = None
            self._auto_filled_track_ids.clear()

        if self.population:
            needed = self.QUEUE_SIZE - len(self.queue)
            new_tracks = await self.population.seed_fill(
                seed_track=seed_track,
                category=category,
                needed=needed,
                queue=list(self.queue),
                history=list(self.history),
                user_id=user_id,
                session_id=self.session_id,
            )

            async with self._queue_lock:
                for track in new_tracks:
                    if len(self.queue) < self.QUEUE_SIZE:
                        self.queue.append(track)
                        self._auto_filled_track_ids.add(track["id"])

            log_service.success(f"[{self.session_id}] Radio seeded with {len(self.queue)} tracks")

        if len(self.queue) < self.QUEUE_SIZE:
            asyncio.create_task(safe_background_task(
                self._auto_fill_queue_background(user_id=user_id, notify_callback=notify_callback),
                f"auto_fill_queue_{self.session_id}"
            ))

        if self.queue and not self.current_track_id:
            async with self._queue_lock:
                self.current_track_id = self.queue[0]["id"]
                self.progress_ms = 0
                self._shift_queue_to_target()

            log_service.playback(f"[{self.session_id}] Queue seeded: {self.queue[0].get('generation_params', {}).get('title', 'Unknown')}")

        if notify_callback:
            await notify_callback(self.get_state())

        return True

    async def handle_preference_change(self, user_id: int, track_id: str, preference_type: str, notify_callback=None):
        if preference_type != "ban":
            return

        track_in_queue = any(t.get("id") == track_id for t in self.queue)
        is_current = self.current_track and self.current_track.get("id") == track_id

        if track_in_queue:
            log_service.playback(f"[{self.session_id}] Ban: Removing {track_id} from queue")
            await self.remove_from_queue(track_id, user_id=user_id, notify_callback=notify_callback)

        if is_current and self.is_playing:
            log_service.playback(f"[{self.session_id}] Ban: Skipping current track {track_id}")
            await self.next(user_id=user_id, notify_callback=notify_callback, skip_reason="ban")

        if (track_in_queue or is_current) and notify_callback:
            await notify_callback(self.get_state())

    async def handle_track_transition(self, from_track_id: str, to_track_id: str, transition_type: str,
                                      user_id: Optional[int] = None, notify_callback=None,
                                      crossfade_info: Optional[Dict] = None):
        log_service.playback(
            f"[{self.session_id}] Track transition: {from_track_id[:8] if from_track_id else 'None'} → {to_track_id[:8] if to_track_id else 'None'} ({transition_type})")

        if crossfade_info:
            log_service.playback(
                f"[{self.session_id}] Crossfade analytics: "
                f"duration={crossfade_info.get('actual_duration_ms', 0)}ms, "
                f"used_hint={crossfade_info.get('used_backend_hint', False)}, "
                f"confidence={crossfade_info.get('backend_confidence', 'none')}"
            )

        if transition_type == 'crossfade':
            self.last_skip_reason = 'auto_crossfade'
            await self._log_play_end(user_id=user_id, event_type="complete")
        else:
            self.last_skip_reason = transition_type
            await self._log_play_end(user_id=user_id, event_type="skip", skip_reason=transition_type)

        async with self._queue_lock:
            found_in_queue = any(t['id'] == to_track_id for t in self.queue)
            found_in_history = any(t['id'] == to_track_id for t in self.history)

            if found_in_queue:
                self.current_track_id = to_track_id
                self.progress_ms = 0
                self.last_update_time = time.time()
                await self._log_play_start(track_id=to_track_id, user_id=user_id)

                self._shift_queue_to_target()
                self._shift_queue_from_history()

            elif found_in_history:
                log_service.playback(f"[{self.session_id}] Track found in history, moving to queue")
                history_index = next((i for i, t in enumerate(self.history) if t['id'] == to_track_id), -1)
                restored_track = self.history.pop(history_index)
                self.queue.insert(0, restored_track)
                self.current_track_id = to_track_id
                self._shift_queue_from_history()
                self._enforce_queue_size()
                self.progress_ms = 0
                self.last_update_time = time.time()
                await self._log_play_start(track_id=to_track_id, user_id=user_id)

            else:
                queue_preview = [t['id'][:8] for t in self.queue[:5]] if self.queue else []
                log_service.error(
                    f"[{self.session_id}] DESYNC: Frontend moved to {to_track_id[:8]} "
                    f"but track not in backend queue or history. Queue head: {queue_preview}. "
                    f"Attempting force sync by reloading queue from catalog."
                )

                if self.catalog:
                    track = self.catalog.get_track(to_track_id)
                    if track:
                        self.queue.insert(0, track)
                        self.current_track_id = to_track_id
                        self._shift_queue_from_history()
                        self._enforce_queue_size()
                        self.progress_ms = 0
                        self.last_update_time = time.time()
                        await self._log_play_start(track_id=to_track_id, user_id=user_id)
                    else:
                        log_service.error(f"[{self.session_id}] CRITICAL: Track {to_track_id} totally unknown.")

        asyncio.create_task(safe_background_task(
            self._auto_fill_queue_background(user_id=user_id, notify_callback=notify_callback),
            f"auto_fill_queue_{self.session_id}"
        ))

        if notify_callback:
            await notify_callback(self.get_state())

        return True

    async def handle_playback_heartbeat(self, track_id: str, actual_position_ms: int, is_playing: bool,
                                        buffered_ahead_ms: int = 0, timestamp: Optional[int] = None):
        if not self.current_track or self.current_track.get('id') != track_id:
            return False

        simulated_progress = self.get_simulated_progress()
        self.drift_ms = actual_position_ms - simulated_progress

        self.latency_samples.append({
            'drift_ms': self.drift_ms,
            'timestamp': timestamp or int(time.time() * 1000),
            'buffered_ahead_ms': buffered_ahead_ms
        })

        if len(self.latency_samples) > 10:
            self.latency_samples.pop(0)

        if self.latency_samples:
            self.average_latency_ms = sum(s['drift_ms'] for s in self.latency_samples) / len(self.latency_samples)

        self.progress_ms = actual_position_ms
        self.is_playing = is_playing
        self.last_update_time = time.time()

        if abs(self.drift_ms) > 3000:
            pass

        return True

    def set_crossfade_timing(self, current_track_id: str, next_track_id: str, timing: Dict):
        self.crossfade_timing_cache[(current_track_id, next_track_id)] = timing

    def set_announcer_timing(self, current_track_id: str, next_track_id: str, timing: Dict):
        self.announcer_timing_cache[(current_track_id, next_track_id)] = timing

    def get_state(self, simplified: bool = True) -> Dict[str, Any]:
        current_track_info = None
        if self.current_track:
            current_track_info = {
                **self.current_track,
                "duration_ms": self.current_track.get("track_info", {}).get("duration", 0),
                "has_artwork": self.catalog.has_artwork(self.current_track.get("id")) if self.catalog else False
            }

        if simplified:
            queue_info = [simplify_track_info(t, self.catalog) for t in self.queue]
            history_info = [simplify_track_info(t, self.catalog) for t in self.history[-10:]]
        else:
            queue_info = self.queue
            history_info = self.history[-10:]

        crossfade_hint = None
        announcer_hint = None
        if self.current_index < len(self.queue) - 1:
            cache_key = (self.queue[self.current_index]['id'], self.queue[self.current_index + 1]['id'])
            crossfade_hint = self.crossfade_timing_cache.get(cache_key)
            announcer_hint = self.announcer_timing_cache.get(cache_key)

        simulated_progress = self.get_simulated_progress()

        return {
            "current_track": current_track_info,
            "progress_ms": simulated_progress,
            "is_playing": self.is_playing,
            "queue": queue_info,
            "history": history_info,
            "current_index": self.current_index,
            "active_device_id": self.active_device_id,
            "activeSeedMode": self.radio_mode if self.radio_mode != "standard" else None,
            "crossfade_hint": crossfade_hint,
            "announcer_hint": announcer_hint,
            "override_active": self.override_active,
            "override_mode": self.override_mode,
            "average_latency_ms": self.average_latency_ms,
            "drift_ms": self.drift_ms,
            "last_skip_reason": self.last_skip_reason
        }