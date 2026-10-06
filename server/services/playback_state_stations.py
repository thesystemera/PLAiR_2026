import time
from typing import Any, Dict, List, Optional

from services import log_service
from services.playback_lists import is_list_mode


class PlaybackStations:
    """Switching what the station plays: a list (favorites, discovery, top hits) or a station built from aspects
    (one seed aspect or a weighted blend, from a song or from words)."""

    def station_label(self) -> str:
        if not self.seed_blend:
            return self.radio_mode
        return " + ".join(f"{item['category']} {float(item.get('weight') or 1):g}"
                          + (f" ('{item['words']}')" if item.get("words") else "") for item in self.seed_blend)

    async def seed_radio(self, category: str = "all", track_id: Optional[str] = None,
                         user_id: Optional[int] = None, notify_callback=None,
                         blend: Optional[List[Dict[str, Any]]] = None):
        if is_list_mode(category) and not blend:
            self.radio_mode = category
            self.seed_track_id = None
            self.seed_blend = None
            async with self._queue_lock:
                picks = self._upcoming_picks()
                self._reset_fill_epoch()
                self._retire_played()
                self.queue = []
                self.current_track_id = None
                self._auto_filled_track_ids.clear()

            await self._auto_fill_queue(user_id=user_id, notify_callback=notify_callback)

            if self.queue or picks:
                async with self._queue_lock:
                    pick_ids = {t["id"] for t in picks}
                    fill = [t for t in self.queue if t["id"] not in pick_ids]
                    self.queue = fill[:1] + picks + fill[1:]
                    self.current_track_id = self.queue[0]["id"]
                    self.progress_ms = 0
                    self.last_update_time = time.time()
                    self._shift_queue_from_history()

                log_service.playback(
                    f"{self._who()}: switched to {category} radio, starting with "
                    f"{log_service.track_label(self.current_track)} ({len(self.queue)} queued)")

                if notify_callback:
                    await notify_callback(self.get_state())
            else:
                log_service.warning(f"{self._who()}: switched to {category} radio but no tracks were found")
                if notify_callback:
                    await notify_callback(self.get_state())

            return True

        if blend:
            category = max(blend, key=lambda item: float(item.get("weight") or 1.0))["category"]
        needs_song = not blend or any(not item.get("words") for item in blend)

        seed_track = None
        if needs_song and track_id:
            seed_track = self.catalog.get_track(track_id) if self.catalog else None
            if not seed_track:
                log_service.error(f"{self._who()}: {category} radio not seeded - seed track {track_id} not found")
                return False
        elif needs_song:
            seed_track = self.current_track or (self.history[-1] if self.history else None)
        if needs_song and not seed_track:
            log_service.warning(f"{self._who()}: {category} radio not seeded - no seed track available")
            return False

        self.radio_mode = category
        self.seed_track_id = seed_track["id"] if seed_track else None
        self.seed_blend = blend or None

        async with self._queue_lock:
            picks = self._upcoming_picks()
            self._reset_fill_epoch()
            seed_epoch = self._fill_epoch
            if self.current_track:
                self.queue = self.queue[:self.current_index + 1] + picks
            else:
                self.queue = picks
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
                blend=self.seed_blend,
                user_id=user_id,
                session_id=self.session_id,
            )

            async with self._queue_lock:
                if seed_epoch == self._fill_epoch:
                    self._append_unique_tracks(new_tracks)
                self._enforce_queue_size()

            log_service.playback(
                f"{self._who()}: seeded {self.station_label()} radio"
                + (f" from {log_service.track_label(seed_track)}" if seed_track else "")
                + f" ({len(self.queue)} tracks queued)")

        if len(self.queue) < self.QUEUE_SIZE:
            self._spawn_background_fill(user_id, notify_callback)

        if self.queue and not self.current_track_id:
            async with self._queue_lock:
                self.current_track_id = self.queue[0]["id"]
                self.progress_ms = 0
                self.last_update_time = time.time()
                self._shift_queue_to_target()

            log_service.detail(
                f"{self._who()}: {category} radio starts with {log_service.track_label(self.queue[0])}", "playback")

        if notify_callback:
            await notify_callback(self.get_state())

        return True
