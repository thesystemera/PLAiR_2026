import asyncio
import time
from typing import Dict, List, Optional

from config import settings
from services import log_service, usage_tracking
from services.task_utils import safe_background_task, spawn


class PlaybackQueue:
    """The queue: songs kept behind the current one, then the songs ahead (picks first, the station fill behind
    them), never more than QUEUE_AHEAD_SONGS of them."""

    def _shift_queue_to_target(self):
        while self.current_index > self.TARGET_INDEX and self.queue:
            removed = self.queue.pop(0)
            self._remember(removed)
            self._auto_filled_track_ids.discard(removed["id"])

    def _remember(self, track: Dict) -> None:
        self.history.append(track)
        del self.history[:-settings.QUEUE_HISTORY_SONGS or None]

    def _retire_played(self) -> None:
        if self.current_track:
            for track in self.queue[:self.current_index + 1]:
                self._remember(track)

    def _shift_queue_from_history(self):
        while self.current_index < self.TARGET_INDEX and self.history:
            self.queue.insert(0, self.history.pop())
        self._enforce_queue_size()

    def _upcoming_picks(self) -> List[Dict]:
        start = self.current_index + 1 if self.current_track else 0
        return [t for t in self.queue[start:] if t.get("id") not in self._auto_filled_track_ids]

    def _after_picks_index(self) -> int:
        start = self.current_index + 1 if self.current_track else 0
        index = start
        for i in range(start, len(self.queue)):
            if self.queue[i].get("id") not in self._auto_filled_track_ids:
                index = i + 1
        return index

    def _enforce_queue_size(self):
        start = self.current_index + 1 if self.current_track else 0
        for i in range(len(self.queue) - 1, start - 1, -1):
            if len(self.queue) <= self.QUEUE_SIZE:
                return
            if self.queue[i].get("id") in self._auto_filled_track_ids:
                self._auto_filled_track_ids.discard(self.queue.pop(i)["id"])
        while len(self.queue) > self.QUEUE_SIZE and len(self.queue) > start:
            self._auto_filled_track_ids.discard(self.queue.pop()["id"])

    def _reset_fill_epoch(self):
        self._fill_epoch += 1

    def _append_unique_tracks(self, new_tracks: List[Dict]) -> bool:
        existing_ids = {t.get("id") for t in self.queue}
        added = False
        for track in new_tracks:
            if len(self.queue) >= self.QUEUE_SIZE:
                break
            track_id = track.get("id")
            if track_id in existing_ids:
                continue
            self.queue.append(track)
            existing_ids.add(track_id)
            self._auto_filled_track_ids.add(track_id)
            added = True
        return added

    async def _run_auto_fill(self, user_id: Optional[int], epoch: int) -> bool:
        usage_tracking.bind_session(self.session_id, user_id)
        if not self.population:
            return False

        seed_track = self.catalog.get_track(self.seed_track_id) if self.catalog and self.seed_track_id else None
        new_tracks = await self.population.fill_queue(
            radio_mode=self.radio_mode,
            queue=list(self.queue),
            history=list(self.history),
            queue_size=self.QUEUE_SIZE,
            seed_track=seed_track,
            blend=self.seed_blend,
            user_id=user_id,
            session_id=self.session_id,
        )

        if not new_tracks:
            return False

        async with self._queue_lock:
            if epoch != self._fill_epoch:
                return False
            added = self._append_unique_tracks(new_tracks)
            self._enforce_queue_size()
        return added

    async def _auto_fill_queue(self, user_id: Optional[int] = None, notify_callback=None):
        task = self._fill_task
        if task is None or task.done() or self._fill_task_epoch != self._fill_epoch:
            task = spawn(self._run_auto_fill(user_id, self._fill_epoch), name=f"auto_fill_run_{self.session_id}")
            self._fill_task = task
            self._fill_task_epoch = self._fill_epoch

        added = await asyncio.shield(task)

        if added and notify_callback:
            await notify_callback(self.get_state())

    async def _auto_fill_queue_background(self, user_id: Optional[int] = None, notify_callback=None):
        try:
            await self._auto_fill_queue(user_id=user_id, notify_callback=notify_callback)
        except Exception as e:
            log_service.error(f"{self._who()}: Background queue auto-fill error: {e}")

    def _spawn_background_fill(self, user_id: Optional[int], notify_callback):
        spawn(safe_background_task(
            self._auto_fill_queue_background(user_id=user_id, notify_callback=notify_callback),
            f"auto_fill_queue_{self.session_id}"
        ), name=f"auto_fill_queue_{self.session_id}")

    def _has_next_track(self) -> bool:
        return bool(self.current_track_id) and self.current_track is not None and self.current_index + 1 < len(self.queue)

    async def _ensure_next_track(self, user_id: Optional[int]) -> bool:
        for _ in range(self.NEXT_FILL_ATTEMPTS):
            if self._has_next_track():
                return True
            try:
                await self._auto_fill_queue(user_id=user_id)
            except Exception as e:
                log_service.error(f"{self._who()}: Queue fill before skip failed: {e}")
                return self._has_next_track()
        return self._has_next_track()

    async def add_to_queue(self, track_ids: List[str], position: Optional[int] = None,
                           user_id: Optional[int] = None, notify_callback=None, play_next: bool = False):
        added = []
        async with self._command():
            async with self._queue_lock:
                upcoming_start = self.current_index + 1 if self.current_track else 0
                if position is None:
                    position = upcoming_start if play_next else self._after_picks_index()
                insert_pos = max(upcoming_start, min(position, len(self.queue)))

                for track_id in dict.fromkeys(track_ids):
                    index = next((i for i, t in enumerate(self.queue) if t["id"] == track_id), None)
                    if index is not None and index < upcoming_start:
                        continue
                    if index is not None:
                        track = self.queue.pop(index)
                        if index < insert_pos:
                            insert_pos -= 1
                    else:
                        track = self.catalog.get_track(track_id) if self.catalog else None
                        if not track:
                            continue

                    self.queue.insert(insert_pos, track)
                    self._auto_filled_track_ids.discard(track_id)
                    insert_pos += 1
                    added.append(track_id)

                self._enforce_queue_size()

            log_service.playback(f"{self._who()}: queued {len(added)} track(s)")

            if notify_callback:
                await notify_callback(self.get_state())

        self._spawn_background_fill(user_id, notify_callback)
        return added

    async def _remove_from_queue_unlocked(self, track_id: str, user_id: Optional[int] = None) -> bool:
        replacement_index = None
        async with self._queue_lock:
            removed_indices = [i for i, t in enumerate(self.queue) if t.get("id") == track_id]

            if not removed_indices:
                return False

            for i in sorted(removed_indices, reverse=True):
                removed_track = self.queue.pop(i)
                self._auto_filled_track_ids.discard(track_id)

                if removed_track["id"] == self.current_track_id:
                    replacement_index = i
                    self.current_track_id = None

            if replacement_index is None:
                self._shift_queue_to_target()
                self._shift_queue_from_history()
                return True

        if replacement_index >= len(self.queue):
            try:
                await self._auto_fill_queue(user_id=user_id)
            except Exception as e:
                log_service.error(f"{self._who()}: Queue fill after removal failed: {e}")

        async with self._queue_lock:
            if self.current_track_id is None:
                if replacement_index < len(self.queue):
                    self.current_track_id = self.queue[replacement_index]["id"]
                elif self.queue:
                    self.current_track_id = self.queue[-1]["id"]
            self.progress_ms = 0
            self.last_update_time = time.time()
            self._shift_queue_to_target()
            self._shift_queue_from_history()
        return True

    async def remove_from_queue(self, track_id: str, user_id: Optional[int] = None, notify_callback=None):
        async with self._command():
            was_current = track_id == self.current_track_id
            removed_track = next((t for t in self.queue if t.get("id") == track_id), None)
            if was_current:
                self._log_play_end(user_id=user_id, event_type="skip", skip_reason="removed")
            removed = await self._remove_from_queue_unlocked(track_id, user_id=user_id)
            if not removed:
                return False
            if was_current and self.current_track_id:
                self._log_play_start(track_id=self.current_track_id, user_id=user_id)

            log_service.playback(
                f"{self._who()}: removed {log_service.track_label(removed_track, track_id)} from the queue")

            await self._prefill_before_broadcast(user_id)
            if notify_callback:
                await notify_callback(self.get_state())

        self._spawn_background_fill(user_id, notify_callback)
        return True
