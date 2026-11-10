"""Suno generation jobs: statuses and limits, the job model, its progress and status, and friendly error text."""
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime, UTC
from config import settings
from services import task_progress
from services.suno_service import blocked_artist


SONGS_PER_BATCH = 2
FINISHED_STAGES = {"completed": "Complete", "cancelled": "Cancelled"}
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
    (("sensitive_word",), "The music service refused a word in this song idea. Try rephrasing your request."),
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
    refused_name = blocked_artist(raw) if "sensitive_word" in lowered else None
    if refused_name:
        return (f"The music service won't make songs that name {refused_name.title()}, "
                "and rewording it didn't get past that.")
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
        self._last_published: Optional[Dict[str, Any]] = None
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
        max_total = (stage_weights["gemini"] if self.user_request else 0) + stage_weights["suno"] + SONGS_PER_BATCH * per_track_total

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

    def get_status(self) -> Dict[str, Any]:
        batches = list(self.batches.values())
        current = next((b for b in batches if b["status"] in ("processing", "retrying")), None)
        if self.cancelled:
            state = "cancelled"
        elif not all(b["status"] in TERMINAL_BATCH_STATUSES for b in batches):
            state = "processing"
        else:
            state = "completed" if any(b["status"] == "completed" for b in batches) else "failed"

        error = next((b.get("error_message") for b in reversed(batches)
                      if b["status"] == "failed" and b.get("error_message")), None)
        if current is not None:
            stage = current.get("current_stage") or self.current_stage
        elif state == "failed":
            stage = error or self.current_stage
        else:
            stage = FINISHED_STAGES.get(state, self.current_stage)
        titles = [b.get("title") for b in ([current] if current else []) + batches]

        return task_progress.snapshot(
            job_id=self.job_id,
            job_type="generation",
            state=state,
            current_stage=stage,
            progress_percent=100 if state == "completed" else (current or {}).get("progress_percent", 0),
            title=next((t for t in titles if t), None) or self.title or self.user_request,
            tracks=self.completed_track_ids,
            total_tracks=self.batch_count * SONGS_PER_BATCH,
            error=error,
            refunded=self.refunded,
        )
