import asyncio
import os
import time
import warnings
from collections import deque
from typing import Callable, Optional

import psutil

from config.settings import settings
from services import log_service
from services.task_utils import spawn

with warnings.catch_warnings():
    warnings.simplefilter("ignore", FutureWarning)
    import pynvml

NOTICE_TYPE = "server_load"
TTS_ENGINE_MARK = "tts_chatterbox"
SYSTEM_PIDS = (0, 4)
MASTERING_SCRIPTS = ("process_backlog", "reprocess_catalog_audio", "remaster")
MASTERING_LANES = ("SonicMaster", "Apollo", "RoFormer", "Demucs", "FlashSR", "Whisper lyrics", "Reprocess", "Audiobox")
ARTWORK_LANES = ("SDXL", "Depth-Anything")

MESSAGES = {
    "mastering": "The studio is mastering new songs, so the hosts may be slow or a bit choppy",
    "artwork": "The studio is painting cover art, so the hosts may be slow or a bit choppy",
    "other": "The studio's computer is busy, so the hosts may be slow or a bit choppy",
}


def cause_of_lane(lane: str) -> str:
    if lane.startswith(MASTERING_LANES):
        return "mastering"
    if lane.startswith(ARTWORK_LANES):
        return "artwork"
    return "other"


def cause_of_command(command: str) -> str:
    return "mastering" if any(script in command for script in MASTERING_SCRIPTS) else "other"


def describe_command(command: str) -> str:
    parts = command.replace('"', "").split()
    script = next((i for i, part in enumerate(parts) if part.endswith(".py")), None)
    if script is None:
        return os.path.basename(parts[0]) if parts else "unknown process"
    return " ".join([os.path.basename(parts[script])] + parts[script + 1:])[:120]


class GpuProcesses:
    """Who is using the station's GPU right now, split into the station itself and everything else."""

    def __init__(self, handle, own_pid: int):
        self.handle = handle
        self.own_pid = own_pid
        self._last_ts = 0
        self._known: dict[int, tuple[float, bool, str]] = {}

    def _identify(self, pid: int) -> Optional[tuple[bool, str]]:
        try:
            proc = psutil.Process(pid)
            created = proc.create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
        known = self._known.get(pid)
        if known and known[0] == created:
            return known[1], known[2]
        try:
            command = " ".join(proc.cmdline()) or proc.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            command = f"pid {pid}"
        try:
            ours = pid == self.own_pid or any(parent.pid == self.own_pid for parent in proc.parents())
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            ours = False
        ours = ours or TTS_ENGINE_MARK in command
        self._known[pid] = (created, ours, command)
        return ours, command

    def foreign_use(self) -> dict[str, float]:
        try:
            samples = pynvml.nvmlDeviceGetProcessUtilization(self.handle, self._last_ts)
        except pynvml.NVMLError_NotFound:
            return {}
        per_pid: dict[int, list[int]] = {}
        for sample in samples:
            self._last_ts = max(self._last_ts, sample.timeStamp)
            per_pid.setdefault(sample.pid, []).append(sample.smUtil)
        use: dict[str, float] = {}
        for pid, values in per_pid.items():
            if pid in SYSTEM_PIDS:
                continue
            identity = self._identify(pid)
            if identity is None or identity[0]:
                continue
            use[identity[1]] = use.get(identity[1], 0.0) + sum(values) / len(values)
        for pid in [pid for pid in self._known if pid not in per_pid]:
            del self._known[pid]
        return use


class LoadTracker:
    """Turns per-sample GPU use by work other than the station into a steady busy / normal state."""

    def __init__(self, busy_util: float, busy_after_s: float, clear_util: float, clear_after_s: float):
        self.busy_util = busy_util
        self.busy_after_s = busy_after_s
        self.clear_util = clear_util
        self.clear_after_s = clear_after_s
        self.samples: deque = deque()
        self.busy = False
        self.cause = "other"
        self.source = ""
        self.since = time.time()

    def _mean(self, now: float, window_s: float) -> Optional[float]:
        if not self.samples or now - self.samples[0][0] < window_s:
            return None
        values = [load for t, load, _, _ in self.samples if now - t <= window_s]
        return sum(values) / len(values) if values else None

    def add(self, now: float, foreign: dict[str, float], lease: Optional[str]) -> bool:
        source, load, cause = "", 0.0, "other"
        if lease:
            source, load, cause = f"{lease} in the backend", 100.0, cause_of_lane(lease)
        if foreign:
            command = max(foreign, key=foreign.get)
            if sum(foreign.values()) > load:
                source, load, cause = describe_command(command), min(100.0, sum(foreign.values())), cause_of_command(command)
        self.samples.append((now, load, cause, source))
        while self.samples and now - self.samples[0][0] > max(self.busy_after_s, self.clear_after_s) + 1:
            self.samples.popleft()
        if load > 0:
            self.cause, self.source = cause, source
        if not self.busy:
            mean = self._mean(now, self.busy_after_s)
            if mean is not None and mean >= self.busy_util:
                self.busy, self.since = True, now
                return True
        else:
            mean = self._mean(now, self.clear_after_s)
            if mean is not None and mean < self.clear_util:
                self.busy, self.since = False, now
                return True
        return False

    def mean(self, now: float, window_s: float) -> float:
        return self._mean(now, window_s) or 0.0


class ServerLoadMonitor:
    def __init__(self):
        self.tracker = LoadTracker(settings.SERVER_LOAD_BUSY_UTIL, settings.SERVER_LOAD_BUSY_AFTER_S,
                                   settings.SERVER_LOAD_CLEAR_UTIL, settings.SERVER_LOAD_CLEAR_AFTER_S)
        self.processes: Optional[GpuProcesses] = None
        self.lease_holder: Callable[[], Optional[str]] = lambda: None
        self.broadcast = None
        self._task: Optional[asyncio.Task] = None
        self._cause = None
        self._busy_since = time.time()

    def public(self) -> dict:
        if not self.tracker.busy:
            return {"state": "normal"}
        return {"state": "busy", "cause": self.tracker.cause, "since": int(self.tracker.since * 1000),
                "message": MESSAGES[self.tracker.cause]}

    async def _announce(self):
        if self.broadcast is not None:
            await self.broadcast({"type": NOTICE_TYPE, "data": self.public()})

    async def _run(self):
        while True:
            await asyncio.sleep(settings.SERVER_LOAD_SAMPLE_S)
            try:
                foreign = await asyncio.to_thread(self.processes.foreign_use)
            except (pynvml.NVMLError, psutil.Error) as e:
                log_service.throttled("server_load_sample", f"[SERVER LOAD] GPU sample failed: {e}")
                continue
            now = time.time()
            tracker = self.tracker
            if tracker.add(now, foreign, self.lease_holder()):
                if tracker.busy:
                    log_service.system(
                        f"[SERVER LOAD] Busy: {tracker.source} is using {tracker.mean(now, tracker.busy_after_s):.0f}% "
                        f"of the GPU; listeners see the notice ({tracker.cause})")
                else:
                    log_service.system(f"[SERVER LOAD] Back to normal after {(now - self._busy_since) / 60:.0f} min")
                self._busy_since = tracker.since
                self._cause = tracker.cause
                await self._announce()
            elif tracker.busy and tracker.cause != self._cause:
                self._cause = tracker.cause
                log_service.system(f"[SERVER LOAD] Still busy, now {tracker.source} ({tracker.cause})")
                await self._announce()

    def start(self, device, lease_holder: Callable[[], Optional[str]], broadcast):
        if not settings.SERVER_LOAD_MONITOR or getattr(device, "type", "cpu") != "cuda":
            return
        try:
            import torch
            uuid = str(torch.cuda.get_device_properties(device).uuid)
            pynvml.nvmlInit()
            handle = next((h for h in (pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(pynvml.nvmlDeviceGetCount()))
                           if uuid in str(pynvml.nvmlDeviceGetUUID(h))), None)
        except (pynvml.NVMLError, RuntimeError) as e:
            log_service.warning(f"[SERVER LOAD] GPU monitor unavailable: {e}")
            return
        if handle is None:
            log_service.warning(f"[SERVER LOAD] GPU {uuid} not found by NVML; monitor off")
            return
        self.processes = GpuProcesses(handle, os.getpid())
        self.lease_holder = lease_holder
        self.broadcast = broadcast
        self._task = spawn(self._run(), name="server_load_monitor")
        log_service.success(f"✓ Server load monitor watching {pynvml.nvmlDeviceGetName(handle)} for work other than the station")

    def stop(self):
        if self._task is not None:
            self._task.cancel()


monitor = ServerLoadMonitor()
