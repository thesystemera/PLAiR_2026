import time
from typing import Optional

from services import log_service


class PlaybackDevices:
    """Which device plays: devices coming and going, claims, transfers and claim-on-open."""

    def device_connected(self, device_id: str) -> bool:
        self._online_devices.add(device_id)
        self._device_offline_since.pop(device_id, None)
        if self.active_device_id is None:
            self.active_device_id = device_id
            log_service.detail(f"{self._who(device_id)}: first device online - playback is on this device", "playback")
            return True
        return False

    def device_disconnected(self, device_id: str) -> bool:
        self._online_devices.discard(device_id)
        self._device_offline_since.pop(device_id, None)
        self._device_offline_since[device_id] = time.time()
        while len(self._device_offline_since) > self.MAX_TRACKED_OFFLINE:
            del self._device_offline_since[next(iter(self._device_offline_since))]
        return device_id == self.active_device_id

    def is_device_online(self, device_id: Optional[str]) -> bool:
        return bool(device_id) and device_id in self._online_devices

    @property
    def active_device_online(self) -> bool:
        return self.is_device_online(self.active_device_id)

    def can_claim(self, device_id: str, now: Optional[float] = None) -> bool:
        if not self.active_device_id or self.active_device_id == device_id:
            return True
        if self.active_device_id in self._online_devices:
            return False
        offline_since = self._device_offline_since.get(self.active_device_id)
        if offline_since is None:
            return True
        return (now if now is not None else time.time()) - offline_since >= self.DEVICE_CLAIM_GRACE_S

    def _apply_transfer(self, device_id: str, play: Optional[bool] = None, reason: str = "transfer") -> bool:
        previous = self.active_device_id
        self.progress_ms = self.get_simulated_progress()
        self.last_update_time = time.time()
        if play is not None:
            self.is_playing = play
        self.active_device_id = device_id
        self.seek_version += 1
        if previous != device_id:
            self._last_transfer_at = time.time()
            log_service.playback(
                f"{self._who(device_id)}: playback moved here from device {previous[:8] if previous else 'none'} "
                f"({reason}) at {log_service.clock(self.progress_ms)} of {log_service.track_label(self.current_track)}"
            )
        return previous != device_id

    async def transfer(self, device_id: str, play: Optional[bool] = None, notify_callback=None,
                       requester_device_id: Optional[str] = None, seq=None) -> bool:
        async with self._command(requester_device_id, seq):
            reason = "picked on this device" if requester_device_id in (None, device_id) \
                else f"sent from device {requester_device_id[:8]}"
            changed = self._apply_transfer(device_id, play=play, reason=reason)
            if notify_callback:
                await notify_callback(self.get_state())
        return changed

    async def claim(self, device_id: str, play: Optional[bool] = None, notify_callback=None, seq=None) -> bool:
        async with self._command(device_id, seq):
            if not self.can_claim(device_id):
                if notify_callback:
                    await notify_callback(self.get_state())
                return False
            self._apply_transfer(device_id, play=play, reason="claimed")
            if notify_callback:
                await notify_callback(self.get_state())
        return True

    def open_claim_verdict(self, device_id: str, now: Optional[float] = None) -> str:
        if not device_id:
            return "invalid"
        if self.active_device_id == device_id:
            return "already_active"
        now = now if now is not None else time.time()
        if self.active_device_id and now - self._last_transfer_at < self.OPEN_CLAIM_DEBOUNCE_S:
            return "debounced"
        return "claim"

    async def claim_on_open(self, device_id: str, notify_callback=None, seq=None) -> str:
        async with self._command(device_id, seq):
            verdict = self.open_claim_verdict(device_id)
            if verdict == "claim":
                self._apply_transfer(device_id, reason="app opened on this device")
            if notify_callback:
                await notify_callback(self.get_state())
        return verdict
