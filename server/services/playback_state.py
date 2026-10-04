import asyncio
import contextlib
import time
import uuid
from typing import Dict, List, Optional, Any, Set
from datetime import datetime, timezone
from config import settings
from services import log_service
from services.api_utils import simplify_track_info
from services.analytics_service import analytics_service
from services.task_utils import safe_background_task, spawn
from services.playback_state_devices import PlaybackDevices
from services.playback_state_queue import PlaybackQueue
from services.playback_state_stations import PlaybackStations

def _valid_seq(seq) -> bool:
    return isinstance(seq, int) and not isinstance(seq, bool) and 0 <= seq < 2 ** 53

class PlaybackState(PlaybackDevices, PlaybackQueue, PlaybackStations):
    TARGET_INDEX = settings.QUEUE_PLAYED_SONGS
    QUEUE_SIZE = settings.QUEUE_PLAYED_SONGS + 1 + settings.QUEUE_AHEAD_SONGS
    TIMING_CACHE_MAX = 64
    MAX_TRACKED_ACKS = 16
    MAX_TRACKED_OFFLINE = 32
    DEVICE_CLAIM_GRACE_S = 15.0
    OPEN_CLAIM_DEBOUNCE_S = 2.0
    HEARTBEAT_COMMAND_QUIET_S = 2.0
    NEXT_FILL_ATTEMPTS = 2
    PREFILL_BUDGET_S = 0.3

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
        self.radio_mode = 'top_hits_all'
        self.seed_track_id: Optional[str] = None
        self.seed_blend: Optional[List[Dict[str, Any]]] = None

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
        self.talk_break: Optional[Dict[str, Any]] = None

        self.crossfade_timing_cache = {}
        self.announcer_timing_cache = {}
        self.last_skip_reason = None

        self._queue_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self.init_lock = asyncio.Lock()
        self._simulation_task = None

        self._fill_epoch = 0
        self._fill_task: Optional[asyncio.Task] = None
        self._fill_task_epoch = -1

        self.state_epoch = uuid.uuid4().hex[:12]
        self.version = 0
        self.seek_version = 0
        self.acks: Dict[str, int] = {}
        self._last_command_time = 0.0
        self._pending_commands = 0
        self._online_devices: Set[str] = set()
        self._device_offline_since: Dict[str, float] = {}
        self._last_transfer_at = 0.0

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

    def _who(self, device_id: Optional[str] = None) -> str:
        return log_service.who(self.session_id, device_id)

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

    def _record_ack(self, device_id: Optional[str], seq) -> None:
        if not device_id or not _valid_seq(seq):
            return
        self.acks.pop(device_id, None)
        self.acks[device_id] = seq
        while len(self.acks) > self.MAX_TRACKED_ACKS:
            del self.acks[next(iter(self.acks))]

    def acknowledge(self, device_id: Optional[str], seq) -> None:
        self._record_ack(device_id, seq)

    async def acknowledge_command(self, device_id: Optional[str], seq, notify_callback=None) -> None:
        async with self._command(device_id, seq):
            if notify_callback:
                await notify_callback(self.get_state())

    @contextlib.asynccontextmanager
    async def _command(self, device_id: Optional[str] = None, seq=None):
        self._pending_commands += 1
        try:
            await self._command_lock.acquire()
        finally:
            self._pending_commands -= 1
        try:
            self._record_ack(device_id, seq)
            self._last_command_time = time.time()
            yield
        finally:
            self._command_lock.release()

    async def _prefill_before_broadcast(self, user_id: Optional[int]):
        if not self.population or len(self.queue) >= self.QUEUE_SIZE or self._pending_commands > 0:
            return
        try:
            await asyncio.wait_for(self._auto_fill_queue(user_id=user_id), self.PREFILL_BUDGET_S)
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            log_service.error(f"{self._who()}: Queue prefill failed: {e}")

    def _spawn_play_event(self, **event):
        spawn(safe_background_task(
            analytics_service.log_play_event(**event),
            f"play_event_{self.session_id}"
        ), name=f"play_event_{self.session_id}")

    def _log_play_start(self, track_id: str, user_id: Optional[int] = None):
        self._current_play_track_id = track_id
        self._current_play_start_time = datetime.now(timezone.utc)
        self._spawn_play_event(
            user_id=user_id,
            track_id=track_id,
            session_id=self.session_id,
            device_id=self.active_device_id,
            event_type="play"
        )

    def _log_play_end(self, user_id: Optional[int] = None, event_type: str = "complete",
                      skip_reason: Optional[str] = None):
        if not self._current_play_track_id or not self._current_play_start_time:
            return

        track = self.current_track
        if not track:
            return

        duration_ms = int((datetime.now(timezone.utc) - self._current_play_start_time).total_seconds() * 1000)
        track_duration = track.get("track_info", {}).get("duration", 0)
        progress = self.get_simulated_progress()
        completion_pct = min(100.0, (progress / track_duration * 100)) if track_duration else 0.0

        self._spawn_play_event(
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

    async def play(self, track_id: Optional[str] = None, user_id: Optional[int] = None, notify_callback=None,
                   device_id: Optional[str] = None, seq=None, claim: bool = False):
        async with self._command(device_id, seq):
            if claim and device_id and self.can_claim(device_id):
                self._apply_transfer(device_id, reason="play pressed here")
            was_playing = self.is_playing

            if track_id:
                track = self.catalog.get_track(track_id) if self.catalog else None
                if not track:
                    log_service.error(f"Track not found: {track_id}")
                    if notify_callback:
                        await notify_callback(self.get_state())
                    return False

                async with self._queue_lock:
                    if self.current_track_id != track_id:
                        self._log_play_end(user_id=user_id, event_type="skip", skip_reason="user_select")

                    existing_idx = next((i for i, t in enumerate(self.queue) if t["id"] == track_id), None)

                    if existing_idx is not None:
                        self.current_track_id = track_id
                        self._shift_queue_to_target()
                    else:
                        insert_pos = self.current_index + 1 if self.queue and self.current_track else 0
                        self.queue.insert(insert_pos, track)
                        self.current_track_id = track_id
                        self._shift_queue_to_target()
                        self._enforce_queue_size()

                    self.progress_ms = 0
                log_service.playback(f"{self._who(device_id)}: picked {log_service.track_label(track)}")

            if not self.current_track:
                await self._auto_fill_queue(user_id=user_id)
                async with self._queue_lock:
                    if not self.current_track and self.queue:
                        self.current_track_id = self.queue[0]["id"]
                        self.progress_ms = 0
                        self._shift_queue_to_target()
                if not self.current_track:
                    if notify_callback:
                        await notify_callback(self.get_state())
                    return False

            self.is_playing = True
            self.last_update_time = time.time()
            if not track_id and not was_playing and device_id:
                log_service.playback(
                    f"{self._who(device_id)}: resumed {log_service.track_label(self.current_track)} "
                    f"at {log_service.clock(self.progress_ms)}")
            current_id = self.current_track["id"]
            if self._current_play_track_id != current_id:
                self._log_play_start(track_id=current_id, user_id=user_id)

            await self._prefill_before_broadcast(user_id)
            if notify_callback:
                await notify_callback(self.get_state())

        self._spawn_background_fill(user_id, notify_callback)
        return True

    async def pause(self, notify_callback=None, device_id: Optional[str] = None, seq=None):
        async with self._command(device_id, seq):
            self.progress_ms = self.get_simulated_progress()
            self.is_playing = False
            self.last_update_time = time.time()
            log_service.playback(
                f"{self._who(device_id)}: paused {log_service.track_label(self.current_track)} "
                f"at {log_service.clock(self.progress_ms)}")
            if notify_callback:
                await notify_callback(self.get_state())
        return True

    async def stop(self, notify_callback=None, device_id: Optional[str] = None, seq=None):
        async with self._command(device_id, seq):
            self.is_playing = False
            self._reset_fill_epoch()
            async with self._queue_lock:
                self.queue = []
                self.current_track_id = None
                self._auto_filled_track_ids.clear()
            self.progress_ms = 0
            self.last_update_time = None
            self.radio_mode = 'top_hits_all'
            self.seed_track_id = None
            self.seed_blend = None
            log_service.playback(f"{self._who(device_id)}: playback stopped, queue cleared")
            if notify_callback:
                await notify_callback(self.get_state())
        return True

    async def _next_unlocked(self, user_id: Optional[int], skip_reason: Optional[str]) -> bool:
        if not self.queue and not self.current_track_id:
            return False

        await self._ensure_next_track(user_id)

        async with self._queue_lock:
            if not self._has_next_track():
                return False
            self._log_play_end(user_id=user_id, event_type="skip", skip_reason=skip_reason or "user_skip")
            self.current_track_id = self.queue[self.current_index + 1]["id"]
            self._shift_queue_to_target()

        self.progress_ms = 0
        self.is_playing = True
        self.last_update_time = time.time()
        self._log_play_start(track_id=self.current_track_id, user_id=user_id)
        return True

    async def next(self, user_id: Optional[int] = None, notify_callback=None, skip_reason: Optional[str] = None,
                   device_id: Optional[str] = None, seq=None):
        async with self._command(device_id, seq):
            advanced = await self._next_unlocked(user_id, skip_reason)
            if advanced:
                log_service.playback(
                    f"{self._who(device_id)}: skipped to {log_service.track_label(self.current_track)} "
                    f"({skip_reason or 'skip'})")
                await self._prefill_before_broadcast(user_id)
            else:
                log_service.warning(f"{self._who(device_id)}: skip ignored - no next track available")
            if notify_callback:
                await notify_callback(self.get_state())

        self._spawn_background_fill(user_id, notify_callback)
        return advanced

    async def previous(self, user_id: Optional[int] = None, notify_callback=None,
                       device_id: Optional[str] = None, seq=None):
        async with self._command(device_id, seq):
            moved = False
            async with self._queue_lock:
                idx = self.current_index
                target = None
                if self.current_track is not None and idx > 0:
                    target = self.queue[idx - 1]
                elif self.history:
                    target = self.history.pop()
                    self.queue.insert(0, target)

                if target is not None:
                    self._log_play_end(user_id=user_id, event_type="skip", skip_reason="user_previous")
                    self.current_track_id = target["id"]
                    self._enforce_queue_size()
                    self.progress_ms = 0
                    self.is_playing = True
                    self.last_update_time = time.time()
                    moved = True

            if moved:
                self._log_play_start(track_id=self.current_track_id, user_id=user_id)
                log_service.playback(f"{self._who(device_id)}: back to {log_service.track_label(self.current_track)}")

            if notify_callback:
                await notify_callback(self.get_state())

        return moved

    async def seek(self, position_ms: int, notify_callback=None, device_id: Optional[str] = None, seq=None):
        async with self._command(device_id, seq):
            if not self.current_track:
                if notify_callback:
                    await notify_callback(self.get_state())
                return False

            try:
                position_ms = int(position_ms)
            except (TypeError, ValueError):
                position_ms = 0

            duration_ms = self.current_track.get("track_info", {}).get("duration", 0)
            self.progress_ms = max(0, min(position_ms, duration_ms) if duration_ms else position_ms)
            self.last_update_time = time.time()
            self._last_broadcast_time = 0
            self.seek_version += 1

            log_service.playback(
                f"{self._who(device_id)}: seeked to {log_service.clock(self.progress_ms)} in "
                f"{log_service.track_label(self.current_track)}")
            if notify_callback:
                await notify_callback(self.get_state())
        return True

    async def handle_preference_change(self, user_id: int, track_id: str, preference_type: str, notify_callback=None):
        if preference_type != "ban":
            return

        async with self._command():
            track_in_queue = any(t.get("id") == track_id for t in self.queue)
            is_current = self.current_track_id == track_id

            if not track_in_queue:
                return

            if is_current:
                self._log_play_end(user_id=user_id, event_type="skip", skip_reason="ban")
            else:
                log_service.detail(f"{self._who()}: banned track {track_id} removed from the queue", "playback")

            await self._remove_from_queue_unlocked(track_id, user_id=user_id)

            if is_current and self.current_track_id:
                log_service.playback(
                    f"{self._who()}: skipped the banned track, now playing {log_service.track_label(self.current_track)}")
                self._log_play_start(track_id=self.current_track_id, user_id=user_id)

            if notify_callback:
                await notify_callback(self.get_state())

        self._spawn_background_fill(user_id, notify_callback)

    async def handle_track_transition(self, from_track_id: str, to_track_id: str, transition_type: str,
                                      user_id: Optional[int] = None, notify_callback=None,
                                      crossfade_info: Optional[Dict] = None,
                                      device_id: Optional[str] = None, seq=None):
        applied = False
        async with self._command(device_id, seq):
            if crossfade_info:
                log_service.detail(
                    f"{self._who(device_id)}: crossfade analytics "
                    f"duration={crossfade_info.get('actual_duration_ms', 0)}ms, "
                    f"used_hint={crossfade_info.get('used_backend_hint', False)}, "
                    f"confidence={crossfade_info.get('backend_confidence', 'none')}", "playback"
                )

            if not to_track_id or to_track_id == self.current_track_id:
                if notify_callback:
                    await notify_callback(self.get_state())
                return to_track_id == self.current_track_id and bool(to_track_id)

            if from_track_id and self.current_track_id and from_track_id != self.current_track_id:
                log_service.warning(
                    f"{self._who(device_id)}: stale transition ignored - device left {from_track_id[:8]} "
                    f"but session is on {self.current_track_id[:8]}"
                )
                if notify_callback:
                    await notify_callback(self.get_state())
                return False

            if transition_type in (None, 'crossfade'):
                self.last_skip_reason = 'auto_crossfade'
                self._log_play_end(user_id=user_id, event_type="complete")
            else:
                self.last_skip_reason = transition_type
                self._log_play_end(user_id=user_id, event_type="skip", skip_reason=transition_type)

            async with self._queue_lock:
                found_in_queue = any(t['id'] == to_track_id for t in self.queue)
                found_in_history = any(t['id'] == to_track_id for t in self.history)

                if found_in_queue:
                    self.current_track_id = to_track_id
                    self._shift_queue_to_target()
                    applied = True

                elif found_in_history:
                    log_service.detail(f"{self._who(device_id)}: track found in history, moving to queue", "playback")
                    history_index = next((i for i, t in enumerate(self.history) if t['id'] == to_track_id), -1)
                    restored_track = self.history.pop(history_index)
                    self.queue.insert(0, restored_track)
                    self.current_track_id = to_track_id
                    self._shift_queue_from_history()
                    self._enforce_queue_size()
                    applied = True

                else:
                    queue_preview = [t['id'][:8] for t in self.queue[:5]] if self.queue else []
                    log_service.error(
                        f"{self._who(device_id)}: DESYNC - frontend moved to {to_track_id[:8]} "
                        f"but track not in backend queue or history. Queue head: {queue_preview}. "
                        f"Attempting force sync by reloading queue from catalog."
                    )

                    track = self.catalog.get_track(to_track_id) if self.catalog else None
                    if track:
                        insert_pos = self.current_index + 1 if self.current_track else 0
                        self.queue.insert(insert_pos, track)
                        self.current_track_id = to_track_id
                        self._shift_queue_to_target()
                        self._enforce_queue_size()
                        applied = True
                    else:
                        log_service.error(f"{self._who()}: CRITICAL: Track {to_track_id} totally unknown.")

                if applied:
                    self.progress_ms = 0
                    self.is_playing = True
                    self.last_update_time = time.time()

            if applied:
                fade = ""
                if crossfade_info and crossfade_info.get('actual_duration_ms'):
                    fade = f", {crossfade_info.get('actual_duration_ms', 0) / 1000:.1f}s crossfade"
                log_service.playback(
                    f"{self._who(device_id)}: now playing {log_service.track_label(self.current_track, to_track_id)} "
                    f"({'next track' if transition_type in (None, 'crossfade') else transition_type}{fade})")
                self._log_play_start(track_id=to_track_id, user_id=user_id)
                await self._prefill_before_broadcast(user_id)

            if notify_callback:
                await notify_callback(self.get_state())

        self._spawn_background_fill(user_id, notify_callback)
        return applied

    async def handle_playback_heartbeat(self, track_id: str, actual_position_ms: int, is_playing: bool,
                                        buffered_ahead_ms: int = 0, timestamp: Optional[int] = None,
                                        device_id: Optional[str] = None):
        if device_id is not None and self.active_device_id and device_id != self.active_device_id:
            return False

        if self._command_lock.locked():
            return False

        if not self.current_track or self.current_track.get('id') != track_id:
            return False

        try:
            actual_position_ms = max(0, int(actual_position_ms))
            buffered_ahead_ms = int(buffered_ahead_ms or 0)
        except (TypeError, ValueError):
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
        self.last_update_time = time.time()

        if time.time() - self._last_command_time >= self.HEARTBEAT_COMMAND_QUIET_S:
            self.is_playing = bool(is_playing)

        return True

    @classmethod
    def _put_bounded(cls, cache: Dict, key, value):
        cache.pop(key, None)
        cache[key] = value
        while len(cache) > cls.TIMING_CACHE_MAX:
            del cache[next(iter(cache))]

    def set_crossfade_timing(self, current_track_id: str, next_track_id: str, timing: Dict):
        self._put_bounded(self.crossfade_timing_cache, (current_track_id, next_track_id), timing)

    def set_announcer_timing(self, current_track_id: str, next_track_id: str, timing: Dict):
        self._put_bounded(self.announcer_timing_cache, (current_track_id, next_track_id), timing)

    def get_state(self, simplified: bool = True) -> Dict[str, Any]:
        self.version += 1

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
            "active_device_online": self.active_device_online,
            "activeSeedMode": self.radio_mode if self.radio_mode != "standard" else None,
            "station_blend": self.station_label() if self.seed_blend else None,
            "crossfade_hint": crossfade_hint,
            "announcer_hint": announcer_hint,
            "override_active": self.override_active,
            "override_mode": self.override_mode,
            "average_latency_ms": self.average_latency_ms,
            "drift_ms": self.drift_ms,
            "last_skip_reason": self.last_skip_reason,
            "talk_break": {**self.talk_break, "after_track_id": self.current_track_id} if self.talk_break else None,
            "state_epoch": self.state_epoch,
            "version": self.version,
            "seek_version": self.seek_version,
            "acks": dict(self.acks),
            "server_time_ms": int(time.time() * 1000),
        }
