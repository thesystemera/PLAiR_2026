"""Suno generation jobs: statuses and limits, the job model, its progress and status, and friendly error text."""
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime, UTC
from config import settings


SUNO_CREDIT_BREAKER_COOLDOWN_S = 900
CANCEL_WAIT_TIMEOUT_S = 10
TERMINAL_BATCH_STATUSES = ("completed", "failed")
SUNO_INFLIGHT_TASKS_FILE = settings.FAILED_PROMPTS_DIR.parent / "suno_inflight_tasks.json"
SUBMIT_CONFIRM_SETTLE_S = 20
UNCONFIRMED_RETENTION_S = 30 * 86400
CANCEL_CLEANUP_TIMEOUT_S = 3600
CANCEL_CLEANUP_POLL_S = 2.0
STATUS_SUBMITTING = "submitting"
STATUS_SUBMITTED = "submitted"
STATUS_UNCONFIRMED = "submit_unconfirmed"
UNCONFIRMED_MARKER = "SUBMIT_UNCONFIRMED"
FRIENDLY_ERRORS = (
    (("cancelled by user",), "Cancelled."),
    ((UNCONFIRMED_MARKER.lower(),), "The music service didn't confirm your request in time, so we stopped it to avoid charging you twice."),
    (("insufficient", "credits exhausted", "circuit breaker"), "The music generator is out of capacity right now. Please try again later."),
    (("sensitive_word",), "The music service rejected the lyrics or title for this idea. Try rephrasing your request."),
    (("failed to generate music parameters", "failed to regenerate music parameters"), "We couldn't turn your request into a song idea. Try rephrasing it."),
    (("suno polling failed", "no tracks returned"), "The music service didn't finish this song in time."),
    (("download",), "We couldn't download the finished song from the music service."),
    (("orchestrator", "master wav", "pipeline"), "Audio processing failed for this song."),
    (("suno api error", "not initialized"), "The music service is unavailable right now. Please try again later."),
)
DEFAULT_FRIENDLY_ERROR = "Something went wrong while generating this song."


class SunoSubmitNeedsAttention(Exception):
    pass


def friendly_generation_error(raw: Optional[str]) -> str:
    lowered = (raw or "").lower()
    for needles, message in FRIENDLY_ERRORS:
        if any(needle in lowered for needle in needles):
            return message
    return DEFAULT_FRIENDLY_ERROR

class GenerationJob:
    def __init__(
            self,
            job_id: str,
            session_id: str,
            original_params: Dict[str, Any],
            batch_count: int,
            user_id: Optional[int] = None,
            source_track_id: Optional[str] = None,
            user_request: Optional[str] = None
    ):
        self.job_id = job_id
        self.session_id = session_id
        self.original_params = original_params
        self.batch_count = batch_count
        self.user_id = user_id
        self.source_track_id = source_track_id
        self.user_request = user_request
        self.created_at = datetime.now(UTC)

        self.batches = {}
        for i in range(batch_count):
            self.batches[i] = {
                "status": "pending",
                "tracks": [],
                "attempts": 0,
                "error": None,
                "current_stage": None,
                "title": None,
                "gemini_stage": None,
                "suno_stage": None,
                "upscaling_stage": None,
                "current_track_index": 0,
                "progress_percent": 0,
                "suno_task_id": None,
                "suno_paid": False,
                "refunded": False,
                "track_ids": []
            }

        self.completed_track_ids: List[str] = []
        self.tasks: List[asyncio.Task] = []
        self.current_stage = "Initializing..."
        self.completed_tracks = 0
        self.title = original_params.get("title")
        self._last_broadcast_state: Optional[str] = None
        self._pregenerated_params: Optional[Dict[str, Any]] = None
        self.cancelled = False
        self.resumed = False
        self._finalized = False
        self.reservation: Optional[Dict[str, str]] = None
        self.refunded = 0
        self.track_jobs: List[Any] = []

    def calculate_progress(self, _batch_index: int, stage: str, track_index: int = 0) -> int:

        stage_weights = {
            "gemini": 5, "suno": 10, "downloading": 5, "metadata_enrichment": 3,
            "apollo": 8, "demucs": 7, "vocals": 5, "sonicmaster": 10,
            "master": 5, "audio_features": 3, "lyric_timestamps": 3,
            "artwork": 2, "finalizing": 2
        }

        ordered_per_track = [
            "downloading", "metadata_enrichment", "demucs", "vocals", "apollo",
            "sonicmaster", "master", "audio_features", "lyric_timestamps", "artwork", "finalizing"
        ]

        per_track_total = sum(stage_weights[s] for s in ordered_per_track)
        tracks_per_batch = 2
        max_total = (stage_weights["gemini"] if self.user_request else 0) + stage_weights["suno"] + tracks_per_batch * per_track_total

        batch_progress = 0

        if stage == "gemini":
            batch_progress += stage_weights["gemini"] * 0.5
            return min(int(batch_progress / max_total * 100), 99)

        if self.user_request:
            batch_progress += stage_weights["gemini"]

        if stage == "suno":
            batch_progress += stage_weights["suno"] * 0.5
            return min(int(batch_progress / max_total * 100), 99)

        batch_progress += stage_weights["suno"]

        batch_progress += track_index * per_track_total

        for s in ordered_per_track:
            if s == stage:
                batch_progress += stage_weights[s] * 0.5
                break
            batch_progress += stage_weights[s]

        return min(int(batch_progress / max_total * 100), 99)

    @staticmethod
    def _serialize_state(state: Dict[str, Any]) -> str:
        comparison_state = {
            "status": state.get("status"),
            "completed_tracks": state.get("completed_tracks"),
            "current_stage": state.get("current_stage"),
            "progress_percent": state.get("progress_percent"),
            "track_count": len(state.get("tracks", []))
        }
        return str(comparison_state)

    def _state_changed(self, current_state: Dict[str, Any]) -> bool:
        if self._last_broadcast_state is None:
            return True
        current_serialized = self._serialize_state(current_state)
        return current_serialized != self._last_broadcast_state

    def get_status(self) -> Dict[str, Any]:
        pending = sum(1 for b in self.batches.values() if b["status"] == "pending")
        processing = sum(1 for b in self.batches.values() if b["status"] == "processing")
        completed = sum(1 for b in self.batches.values() if b["status"] == "completed")
        failed = sum(1 for b in self.batches.values() if b["status"] == "failed")
        retrying = sum(1 for b in self.batches.values() if b["status"] == "retrying")

        overall_status = "completed" if completed == self.batch_count else \
            "failed" if failed == self.batch_count else \
                "processing"

        current_batch = None
        for batch in self.batches.values():
            if batch["status"] == "processing":
                current_batch = batch
                break

        current_stage = self.current_stage
        title = self.title
        progress_percent = 0

        if current_batch:
            current_stage = current_batch.get("current_stage") or current_stage
            title = current_batch.get("title") or title
            progress_percent = current_batch.get("progress_percent", 0)

        return {
            "job_id": self.job_id,
            "status": overall_status,
            "batch_count": self.batch_count,
            "pending": pending,
            "processing": processing,
            "completed": completed,
            "failed": failed,
            "retrying": retrying,
            "completed_tracks": len(self.completed_track_ids),
            "total_tracks": self.batch_count * 2,
            "expected_tracks": self.batch_count * 2,
            "source_track_id": self.source_track_id,
            "current_stage": current_stage,
            "title": title,
            "progress_percent": progress_percent,
            "tracks": self.completed_track_ids,
            "error": next((b.get("error_message") for b in reversed(list(self.batches.values()))
                           if b["status"] == "failed" and b.get("error_message")), None),
            "refunded": self.refunded
        }

