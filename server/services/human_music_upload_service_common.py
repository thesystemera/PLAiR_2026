"""Human uploads: limits, value cleaning, embedded tags, the upload exceptions and the progress events the app shows."""
import asyncio
import math
import re
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Callable, List
import numpy as np
import soundfile as sf
from services import log_service


UPLOAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
GPU_BUSY_MESSAGE = "The server's GPU ran out of memory while processing your upload. Please try again in a few minutes."
DEFAULT_MASTERING_BLEND = 70
SONIC_BLEND_MIN_EFFECTIVE = 10
SONIC_BLEND_MAX = 60
LOUD_SOURCE_LUFS = -12.0
LOUD_SOURCE_MASTERING_BLEND_MAX = 35
LOUD_SOURCE_SONIC_BLEND_MAX = 35
SONIC_PROMPT_MAX_CHARS = 200

def sanitize_for_json(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, (np.float32, np.float64, np.floating)):
        return float(obj)
    elif isinstance(obj, (np.int32, np.int64, np.integer)):
        return int(obj)
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif hasattr(obj, 'item'):
        return obj.item()
    return obj

def _coerce_percent(value: Any, default: int) -> int:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, str):
        value = value.strip().rstrip('%').strip()
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return int(round(max(0.0, min(100.0, number))))

def _coerce_prompt(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    if cleaned.lower() in ("null", "none", "n/a"):
        return None
    return cleaned[:SONIC_PROMPT_MAX_CHARS] if cleaned else None

def _is_float_wav(path: Path) -> bool:
    try:
        return sf.info(str(path)).subtype in ("FLOAT", "DOUBLE")
    except Exception:
        return False

TITLE_MAX = 120
VISIBILITIES = ("public", "unlisted", "private")
TAG_MAX = 60
TAG_LIST_MAX = 8
LYRICS_MAX = 20000
DESCRIPTION_MAX = 2000


def _clean_tag_list(values) -> List[str]:
    if isinstance(values, str):
        values = values.split(",")
    cleaned = []
    for value in values or []:
        value = " ".join(str(value or "").split())[:TAG_MAX]
        if value and value.lower() not in (c.lower() for c in cleaned):
            cleaned.append(value)
    return cleaned[:TAG_LIST_MAX]


EMBEDDED_TAG_KEYS = ("title", "artist", "albumartist", "album", "date", "genre")


def read_embedded_tags(path: Path) -> Dict[str, str]:
    try:
        import mutagen
        audio = mutagen.File(str(path), easy=True)
    except Exception:
        return {}
    found = {}
    for key in EMBEDDED_TAG_KEYS:
        values = (audio.tags or {}).get(key) if audio is not None and audio.tags is not None else None
        value = " ".join(str(values[0]).split())[:200] if values else ""
        if value:
            found[key] = value
    return found


class DuplicateSong(Exception):
    def __init__(self, track: Dict[str, Any]):
        super().__init__("duplicate song")
        self.track = track


class UploadFailed(Exception):
    pass

class _UploadProgress:

    def __init__(self, upload_id: str, callback: Optional[Callable[[Dict[str, Any]], Any]], plan: List[Tuple[str, str, float]]):
        self.upload_id = upload_id
        self.callback = callback
        self.plan = plan
        self.positions = {key: index for index, (key, _label, _weight) in enumerate(plan)}
        self.total_weight = sum(weight for _key, _label, weight in plan) or 1.0
        self.position = -1
        self.percent = 0
        self.loop = asyncio.get_running_loop()
        self.started_at = self.loop.time()
        self.stage_started_at = self.started_at
        self.finished = False

    async def _emit(self, payload: Dict[str, Any]):
        if not self.callback:
            return
        try:
            result = self.callback(payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            log_service.warning(f"[Upload] Progress notification failed: {e}")

    async def stage(self, key: str, label: Optional[str] = None):
        index = self.positions.get(key)
        if index is None:
            return
        if index > self.position:
            self.position = index
        completed_weight = sum(weight for _key, _label, weight in self.plan[:self.position])
        self.percent = max(self.percent, min(int(completed_weight / self.total_weight * 100), 99))

        now = self.loop.time()
        elapsed_ms = int((now - self.started_at) * 1000)
        previous_stage_ms = int((now - self.stage_started_at) * 1000)
        self.stage_started_at = now

        stage_label = label or self.plan[index][1]
        log_service.upload(
            f"[Upload] {stage_label}: {self.percent}% | elapsed={elapsed_ms}ms | previous_stage={previous_stage_ms}ms"
        )
        await self._emit({
            "upload_id": self.upload_id,
            "stage": stage_label,
            "percent": self.percent,
            "status": "running",
        })

    async def finish(self, success: bool, message: str, track_id: Optional[str] = None):
        if self.finished:
            return
        self.finished = True
        if success:
            self.percent = 100
        await self._emit({
            "upload_id": self.upload_id,
            "stage": "Complete" if success else "Upload failed",
            "percent": self.percent,
            "status": "done" if success else "failed",
            "message": message,
            "track_id": track_id,
        })

