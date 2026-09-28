import asyncio
import os
import shutil
import time
import uuid
import aiofiles
from typing import Dict, Any, Optional, Callable, List, Tuple
from datetime import datetime, UTC
from pathlib import Path
import json
from services import log_service
from services import usage_tracking
from services.base_service import SingletonService
from services.suno_service import SunoAPIError, SunoSubmitUnconfirmed
from services.rate_limit_service import rate_limit_service
from services.task_utils import spawn
from config import settings

TRACK_PIPELINE_TIMEOUT_S = 3600
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
            "apollo": 8, "demucs": 7, "clearvoice": 5, "sonicmaster": 10,
            "master": 5, "audio_features": 3, "lyric_timestamps": 3,
            "artwork": 2, "finalizing": 2
        }

        ordered_per_track = [
            "downloading", "metadata_enrichment", "apollo", "demucs", "clearvoice",
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

class SunoGenerationQueueService(SingletonService):
    def __init__(self):
        if self._initialized:
            return

        self.jobs: Dict[str, GenerationJob] = {}
        self.suno_service: Optional[Any] = None
        self.prompt_service: Optional[Any] = None
        self.metadata_service: Optional[Any] = None
        self.catalog_service: Optional[Any] = None
        self.playback_service: Optional[Any] = None
        self.vector_search_service: Optional[Any] = None
        self.orchestrator: Optional[Any] = None
        self.enriched_metadata_service: Optional[Any] = None
        self.notification_callback: Optional[Callable] = None
        self.max_retries = 2
        self.max_sensitive_word_retries = 3
        self._credits_exhausted_at: Optional[float] = None
        self._inflight_lock = asyncio.Lock()
        self._submit_lock = asyncio.Lock()
        self._submit_counter = 0
        self.suno_semaphore: Optional[asyncio.Semaphore] = None
        self.generation_stats = {
            "total_jobs": 0,
            "successful_jobs": 0,
            "failed_jobs": 0,
            "sensitive_word_failures": 0,
            "credit_exhausted_failures": 0,
            "other_failures": 0,
            "total_tracks_generated": 0,
            "total_retries": 0,
            "gemini_calls": 0
        }
        self.phrase_cycle_counter = 0
        self._initialized = True

    @property
    def suno_credits_exhausted(self) -> bool:
        if self._credits_exhausted_at is None:
            return False
        if time.monotonic() - self._credits_exhausted_at >= SUNO_CREDIT_BREAKER_COOLDOWN_S:
            self._credits_exhausted_at = None
            log_service.system("Suno credit circuit breaker cooldown elapsed - generation re-enabled")
            return False
        return True

    @suno_credits_exhausted.setter
    def suno_credits_exhausted(self, value: bool):
        self._credits_exhausted_at = time.monotonic() if value else None

    async def initialize(
            self,
            suno_service,
            prompt_service,
            metadata_service,
            catalog_service,
            playback_service,
            vector_search_service,
            orchestrator,
            enriched_metadata_service
    ):
        self.suno_service = suno_service
        self.prompt_service = prompt_service
        self.metadata_service = metadata_service
        self.catalog_service = catalog_service
        self.playback_service = playback_service
        self.vector_search_service = vector_search_service
        self.orchestrator = orchestrator
        self.enriched_metadata_service = enriched_metadata_service

        if self.enriched_metadata_service is None:
            raise Exception("Enriched metadata service not provided")
        await self.enriched_metadata_service.initialize()

        self.suno_semaphore = asyncio.Semaphore(10)
        log_service.success("SunoGenerationQueueService initialized (Suno: 10/10s, Orchestrator: Highway Architecture)")

        try:
            await self._resume_inflight_tasks()
        except Exception as e:
            log_service.warning(f"Could not resume in-flight Suno tasks: {e}")

    def set_notification_callback(self, callback: Callable):
        self.notification_callback = callback

    async def _notify(self, session_id: str, message: Dict[str, Any]):
        if self.notification_callback:
            try:
                if not isinstance(message, dict):
                    return

                if message.get("type") in ["generation_started", "generation_stage_update", "generation_processing"]:
                    status_data = message.get("data", {})
                    if isinstance(status_data, dict) and "job_id" in status_data:
                        job_id = status_data.get("job_id")
                        if job_id is None:
                            return
                        job = self.jobs.get(job_id)
                        if job and hasattr(job, '_state_changed'):
                            full_status = status_data.get("status") if "status" in status_data else status_data

                            if isinstance(full_status, dict):
                                if not job._state_changed(full_status):
                                    return
                                job._last_broadcast_state = job._serialize_state(full_status)

                await self.notification_callback(session_id, message)
            except Exception as e:
                log_service.warning(f"Failed to send notification: {e}")

    @staticmethod
    async def _log_failed_prompt(music_params: Optional[Dict[str, Any]], error_type: str, error_details: Any,
                                 user_request: Optional[str] = None):
        try:
            if settings is None:
                log_service.warning("Cannot log failed prompt: settings is None")
                return
            failed_prompts_dir = settings.FAILED_PROMPTS_DIR

            timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
            filename = f"{error_type}_{timestamp}.json"
            filepath = failed_prompts_dir / filename

            failure_data = {
                "timestamp": datetime.now(UTC).isoformat(),
                "error_type": error_type,
                "user_request": user_request,
                "music_params": music_params,
                "error_details": error_details
            }

            async with aiofiles.open(filepath, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(failure_data, indent=2, ensure_ascii=False))

            log_service.system(f"Failed prompt logged to: {filepath.name}")

        except Exception as e:
            log_service.warning(f"Could not log failed prompt: {str(e)}")

    @staticmethod
    def _read_inflight_sync() -> Dict[str, Dict[str, Any]]:
        path = SUNO_INFLIGHT_TASKS_FILE
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError) as e:
            log_service.warning(f"Could not read in-flight Suno tasks file {path.name}: {e}")
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _write_inflight_sync(records: Dict[str, Dict[str, Any]]):
        path = SUNO_INFLIGHT_TASKS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(path.name + ".tmp")
        temp_path.write_text(json.dumps(records, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(temp_path, path)

    async def _update_inflight(self, task_id: Optional[str], record: Optional[Dict[str, Any]]):
        if not task_id:
            return
        try:
            async with self._inflight_lock:
                records = await asyncio.to_thread(self._read_inflight_sync)
                if record is None:
                    if task_id not in records:
                        return
                    records.pop(task_id)
                else:
                    records[task_id] = record
                await asyncio.to_thread(self._write_inflight_sync, records)
        except Exception as e:
            log_service.warning(f"Could not persist in-flight Suno task {task_id}: {e}")

    @staticmethod
    def _build_inflight_record(
            job: GenerationJob,
            batch_index: int,
            task_id: Optional[str],
            music_params: Dict[str, Any],
            unique_id: Optional[str]
    ) -> Dict[str, Any]:
        return {
            "task_id": task_id,
            "job_id": job.job_id,
            "batch_index": batch_index,
            "session_id": job.session_id,
            "user_id": job.user_id,
            "source_track_id": job.source_track_id,
            "user_request": job.user_request,
            "unique_id": unique_id,
            "music_params": music_params,
            "track_ids": [],
            "reservation": job.reservation,
            "status": STATUS_SUBMITTED if task_id else STATUS_SUBMITTING,
            "pid": os.getpid(),
            "submitted_at": datetime.now(UTC).isoformat()
        }

    async def _swap_inflight(self, old_key: str, new_key: str, record: Dict[str, Any]):
        try:
            async with self._inflight_lock:
                records = await asyncio.to_thread(self._read_inflight_sync)
                records.pop(old_key, None)
                records[new_key] = record
                await asyncio.to_thread(self._write_inflight_sync, records)
        except Exception as e:
            log_service.warning(f"Could not persist in-flight Suno task {new_key}: {e}")

    @staticmethod
    def _pending_key(job_id: str, batch_index: int) -> str:
        return f"pending:{job_id}:{batch_index}"

    @staticmethod
    def _owned_by_live_process(pid: Any) -> bool:
        if not isinstance(pid, int) or pid == os.getpid():
            return False
        try:
            import psutil
        except ImportError:
            return False
        try:
            if not psutil.pid_exists(pid):
                return False
            return "python" in psutil.Process(pid).name().lower()
        except Exception:
            return False

    async def _resume_inflight_tasks(self):
        async with self._inflight_lock:
            records = await asyncio.to_thread(self._read_inflight_sync)
            now = time.time()
            invalid = [
                tid for tid, rec in records.items()
                if not isinstance(rec, dict) or not rec.get("music_params") or (
                    rec.get("status") == STATUS_UNCONFIRMED
                    and now - (rec.get("unconfirmed_at") or now) > UNCONFIRMED_RETENTION_S)
            ]
            if invalid:
                for tid in invalid:
                    records.pop(tid, None)
                await asyncio.to_thread(self._write_inflight_sync, records)

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for task_id, record in records.items():
            if record.get("status") in (STATUS_SUBMITTING, STATUS_UNCONFIRMED):
                continue
            if self._owned_by_live_process(record.get("pid")):
                log_service.system(f"Suno task {task_id} is owned by live process {record.get('pid')} - not resuming")
                continue
            job_id = record.get("job_id") or str(uuid.uuid4())
            if job_id in self.jobs:
                continue
            grouped.setdefault(job_id, []).append({**record, "task_id": task_id})

        for job_id, job_records in grouped.items():
            first = job_records[0]
            job = GenerationJob(
                job_id=job_id,
                session_id=first.get("session_id") or "resume",
                original_params=first["music_params"],
                batch_count=len(job_records),
                user_id=first.get("user_id"),
                source_track_id=first.get("source_track_id"),
                user_request=first.get("user_request")
            )
            job.resumed = True
            job.reservation = first.get("reservation")
            self.jobs[job_id] = job
            self.generation_stats["total_jobs"] += 1

            for batch_index, record in enumerate(job_records):
                job.batches[batch_index]["resume"] = record
                job.batches[batch_index]["title"] = record["music_params"].get("title")
                job.batches[batch_index]["suno_task_id"] = record["task_id"]
                job.tasks.append(asyncio.create_task(self._generate_batch(job_id, batch_index)))

            log_service.system(
                f"Resuming job {job_id}: polling {len(job_records)} in-flight Suno task(s) persisted before restart"
            )

        interrupted = {
            key: record for key, record in records.items()
            if record.get("status") == STATUS_SUBMITTING and not self._owned_by_live_process(record.get("pid"))
        }
        if interrupted:
            await self._handle_interrupted_submits(interrupted)

    async def _handle_interrupted_submits(self, interrupted: Dict[str, Dict[str, Any]]):
        failed_jobs: Dict[str, GenerationJob] = {}
        for key, record in interrupted.items():
            log_service.error(
                f"Suno submit for job {record.get('job_id')} batch {record.get('batch_index')} was interrupted by a "
                f"restart before Suno answered - NOT resubmitting (a task may exist on Suno). Title: "
                f"{(record.get('music_params') or {}).get('title')!r}. Kept in {SUNO_INFLIGHT_TASKS_FILE.name} as {key}."
            )
            refunded = False
            if record.get("reservation") and not record.get("refunded"):
                refunded = await rate_limit_service.refund_generations(
                    record.get("user_id"), record.get("session_id") or "", 1, record.get("reservation"))
            updated = {**record, "status": STATUS_UNCONFIRMED, "unconfirmed_at": time.time(),
                       "refunded": bool(record.get("refunded")) or refunded,
                       "error": "restart during submit"}
            await self._update_inflight(key, updated)

            job_id = record.get("job_id") or str(uuid.uuid4())
            if job_id in self.jobs and job_id not in failed_jobs:
                continue
            job = failed_jobs.get(job_id)
            if job is None:
                job = GenerationJob(
                    job_id=job_id,
                    session_id=record.get("session_id") or "resume",
                    original_params=record.get("music_params") or {},
                    batch_count=0,
                    user_id=record.get("user_id"),
                    source_track_id=record.get("source_track_id"),
                    user_request=record.get("user_request")
                )
                job.reservation = record.get("reservation")
                failed_jobs[job_id] = job
                self.jobs[job_id] = job
            index = job.batch_count
            job.batch_count += 1
            job.batches[index] = {
                "status": "failed", "tracks": [], "attempts": 1,
                "error": f"{UNCONFIRMED_MARKER}: restart during submit",
                "error_message": friendly_generation_error(UNCONFIRMED_MARKER),
                "current_stage": None, "title": (record.get("music_params") or {}).get("title"),
                "gemini_stage": None, "suno_stage": None, "upscaling_stage": None,
                "current_track_index": 0, "progress_percent": 0, "suno_task_id": None,
                "suno_paid": True, "refunded": updated["refunded"], "track_ids": []
            }
            if updated["refunded"]:
                job.refunded += 1
        for job in failed_jobs.values():
            job._finalized = True
            job.current_stage = friendly_generation_error(UNCONFIRMED_MARKER)

    async def start_generation_job(
            self,
            session_id: str,
            original_params: Dict[str, Any],
            batch_count: int = 3,
            user_id: Optional[int] = None,
            source_track_id: Optional[str] = None,
            user_request: Optional[str] = None,
            pregenerated_params: Optional[Dict[str, Any]] = None,
            reservation: Optional[Dict[str, str]] = None
    ) -> Tuple[str, List[asyncio.Task]]:

        log_service.system(
            f"Creating 1 generation job with {batch_count} batches "
            f"(~{batch_count * 2} tracks total) for session {session_id}"
        )

        job_id = str(uuid.uuid4())

        job = GenerationJob(
            job_id=job_id,
            session_id=session_id,
            original_params=original_params,
            batch_count=batch_count,
            user_id=user_id,
            source_track_id=source_track_id,
            user_request=user_request
        )
        job.reservation = reservation

        if pregenerated_params and batch_count == 1:
            job._pregenerated_params = pregenerated_params
            log_service.system(f"Job {job_id}: Using pregenerated params (resume mode)")
        elif user_request:
            log_service.system(f"Job {job_id}: Will call Gemini fresh for each batch for non-deterministic results")

        self.jobs[job_id] = job
        self.generation_stats["total_jobs"] += 1

        tasks = []
        for i in range(batch_count):
            task = asyncio.create_task(
                self._generate_batch(job_id, i)
            )
            job.tasks.append(task)
            tasks.append(task)

        await self._notify(session_id, {
            "type": "generation_started",
            "data": job.get_status()
        })

        log_service.system(f"Started job {job_id} with {batch_count} batches")

        return job_id, tasks

    async def _save_params_metadata(self, job: GenerationJob, music_params: Dict[str, Any]) -> str:
        if self.metadata_service is None:
            raise Exception("Metadata service not initialized")
        unique_id = self.metadata_service.generate_unique_id()
        initial_metadata = await self.metadata_service.create_metadata(
            user_request=job.user_request,
            music_params=music_params,
            track_data=None,
            unique_id=unique_id,
            generation_status="params_generated"
        )

        if job.source_track_id:
            initial_metadata["generated_from"] = job.source_track_id

        await self.metadata_service.save_metadata(initial_metadata, unique_id)
        log_service.success(f"Saved music params to {unique_id}.json (can resume later if needed)")
        return unique_id

    def _assign_track_ids(
            self,
            track_count: int,
            unique_id: Optional[str],
            job: GenerationJob,
            resume_track_ids: List[str]
    ) -> List[str]:
        if self.metadata_service is None:
            raise Exception("Metadata service not initialized")
        track_ids = []
        for track_index in range(track_count):
            if track_index < len(resume_track_ids) and resume_track_ids[track_index]:
                track_ids.append(resume_track_ids[track_index])
            elif track_index == 0 and unique_id is not None and job.user_request:
                track_ids.append(unique_id)
            else:
                track_ids.append(self.metadata_service.generate_unique_id())
        return track_ids

    async def _discard_attempt_outputs(self, job: GenerationJob, batch: Dict[str, Any], track_ids: List[str]):
        if self.metadata_service is None:
            return
        for track_id in track_ids:
            if not track_id:
                continue
            metadata = None
            if (self.metadata_service.metadata_dir / f"{track_id}.json").exists():
                metadata = await self.metadata_service.load_metadata(track_id)
            if metadata and metadata.get("generation_status") == "completed":
                continue
            if track_id in batch["tracks"]:
                batch["tracks"].remove(track_id)
            if track_id in job.completed_track_ids:
                job.completed_track_ids.remove(track_id)
            if metadata:
                await self.metadata_service.update_generation_status(track_id, "suno_failed")

    async def _refund_batch(self, job: GenerationJob, batch_index: int) -> bool:
        batch = job.batches[batch_index]
        if batch.get("refunded") or not job.reservation:
            return False
        batch["refunded"] = True
        try:
            refunded = await rate_limit_service.refund_generations(job.user_id, job.session_id, 1, job.reservation)
        except Exception as e:
            log_service.error(f"Job {job.job_id} batch {batch_index}: generation refund failed: {e}")
            return False
        if refunded:
            job.refunded += 1
        return refunded

    async def _fail_batch(self, job: GenerationJob, batch_index: int, error_msg: str, refund: bool = True):
        batch = job.batches[batch_index]
        batch["status"] = "failed"
        batch["error"] = error_msg
        batch["error_message"] = friendly_generation_error(error_msg)
        refunded = await self._refund_batch(job, batch_index) if refund else False
        await self._notify(job.session_id, {
            "type": "generation_batch_failed",
            "data": {
                "job_id": job.job_id,
                "batch_index": batch_index,
                "error": batch["error_message"],
                "refunded": refunded,
                "cancelled": job.cancelled,
                "status": job.get_status()
            }
        })

    async def _finalize_job_if_done(self, job: GenerationJob):
        if job._finalized:
            return
        if not all(b["status"] in TERMINAL_BATCH_STATUSES for b in job.batches.values()):
            return
        job._finalized = True
        status = job.get_status()
        if job.cancelled:
            log_service.system(f"Job {job.job_id} cancelled ({job.refunded} generation(s) refunded)")
            await self._notify(job.session_id, {
                "type": "generation_job_cancelled",
                "data": {**status, "refunded": job.refunded}
            })
        elif status["completed"] > 0:
            log_service.system(f"Job {job.job_id} finished: {status['completed']}/{job.batch_count} batches completed")
            await self._notify(job.session_id, {
                "type": "generation_job_completed",
                "data": {**status, "refunded": job.refunded}
            })
        else:
            errors = [b["error"] for b in job.batches.values() if b.get("error")]
            log_service.error(f"Job {job.job_id} failed: all {job.batch_count} batches failed ({errors[-1] if errors else 'unknown'})")
            await self._notify(job.session_id, {
                "type": "generation_job_failed",
                "data": {
                    **status,
                    "error": friendly_generation_error(errors[-1] if errors else None),
                    "refunded": job.refunded,
                    "refunded_all": job.refunded >= job.batch_count > 0
                }
            })

    async def _generate_batch(self, job_id: str, batch_index: int):
        job = self.jobs.get(job_id)
        if not job:
            return
        usage_tracking.bind_session(job.session_id, job.user_id)

        try:
            await self._run_batch(job, batch_index)
        except asyncio.CancelledError:
            if not job.cancelled:
                log_service.system(
                    f"Job {job_id} batch {batch_index} interrupted; in-flight Suno task kept for resume"
                )
            raise
        except Exception as e:
            log_service.error(f"Job {job_id} batch {batch_index} crashed: {e}")
            if job.batches[batch_index]["status"] not in TERMINAL_BATCH_STATUSES:
                self.generation_stats["failed_jobs"] += 1
                self.generation_stats["other_failures"] += 1
                await self._update_inflight(job.batches[batch_index].get("suno_task_id"), None)
                await self._fail_batch(job, batch_index, str(e))
        finally:
            if not job.cancelled:
                await self._finalize_job_if_done(job)

    async def _run_batch(self, job: GenerationJob, batch_index: int):
        job_id = job.job_id
        batch = job.batches[batch_index]
        music_params: Optional[Dict[str, Any]] = None
        unique_id: Optional[str] = None
        sensitive_word_attempts = 0
        resume_task_id: Optional[str] = None
        resume_track_ids: List[str] = []
        inflight_record: Optional[Dict[str, Any]] = None
        max_retries = 0 if job.resumed else self.max_retries

        resume = batch.pop("resume", None)
        if resume:
            music_params = resume.get("music_params")
            unique_id = resume.get("unique_id")
            resume_task_id = resume.get("task_id")
            resume_track_ids = list(resume.get("track_ids") or [])
            inflight_record = {**resume, "job_id": job_id, "batch_index": batch_index, "pid": os.getpid()}
            await self._update_inflight(resume_task_id, inflight_record)
            log_service.system(f"Job {job_id} batch {batch_index}: Resuming Suno task {resume_task_id}")

        for attempt in range(max_retries + 1):
            batch["attempts"] = attempt + 1
            task_id: Optional[str] = None
            attempt_track_ids: List[str] = []

            if attempt > 0:
                batch["status"] = "retrying"
                self.generation_stats["total_retries"] += 1
                log_service.system(
                    f"Job {job_id} batch {batch_index}: Retry attempt {attempt + 1}/{max_retries + 1}"
                )
                await self._notify(job.session_id, {
                    "type": "generation_retrying",
                    "data": {
                        "job_id": job_id,
                        "batch_index": batch_index,
                        "attempt": attempt + 1,
                        "max_attempts": max_retries + 1
                    }
                })
            else:
                batch["status"] = "processing"
                await self._notify(job.session_id, {
                    "type": "generation_processing",
                    "data": {
                        "job_id": job_id,
                        "batch_index": batch_index,
                        "status": job.get_status()
                    }
                })

            try:
                if music_params is None:
                    if attempt == 0 and job._pregenerated_params is not None:
                        log_service.system(f"Batch {batch_index}: Using pregenerated music params (optimized)")
                        music_params = job._pregenerated_params
                        unique_id = None
                        batch["title"] = music_params.get("title", "Untitled")
                        batch["gemini_stage"] = "COMPLETE"
                        batch["progress_percent"] = job.calculate_progress(batch_index, "suno", 0)

                    elif job.user_request:
                        batch["current_stage"] = "Generating song parameters..."
                        batch["gemini_stage"] = "GENERATING"
                        batch["progress_percent"] = job.calculate_progress(batch_index, "gemini", 0)
                        await self._notify(job.session_id, {
                            "type": "generation_stage_update",
                            "data": {
                                "job_id": job_id,
                                "batch_index": batch_index,
                                "current_stage": batch["current_stage"],
                                "status": job.get_status()
                            }
                        })

                        log_service.system(
                            f"Batch {batch_index}: Generating fresh music params from user request")

                        if self.catalog_service is None:
                            raise Exception("Catalog service not initialized")
                        if self.prompt_service is None:
                            raise Exception("Prompt service not initialized")
                        repeated_titles = self.catalog_service.get_repeated_titles(min_occurrences=2)
                        overused_phrases = self.catalog_service.get_cycled_phrases(
                            cycle_index=self.phrase_cycle_counter,
                            phrases_per_cycle=5,
                            top_n=20
                        )
                        self.phrase_cycle_counter = (self.phrase_cycle_counter + 1) % 4
                        music_params = await self.prompt_service.generate_music_params(
                            job.user_request,
                            catalog_service=self.catalog_service,
                            repeated_titles=repeated_titles,
                            overused_phrases=overused_phrases
                        )
                        self.generation_stats["gemini_calls"] += 1

                        if not music_params:
                            raise Exception("Failed to generate music parameters")

                        unique_id = await self._save_params_metadata(job, music_params)

                        batch["title"] = music_params.get("title", "Untitled")
                        batch["gemini_stage"] = "COMPLETE"
                        batch["current_stage"] = "Song parameters generated"
                        batch["progress_percent"] = job.calculate_progress(batch_index, "suno", 0)
                        await self._notify(job.session_id, {
                            "type": "generation_stage_update",
                            "data": {
                                "job_id": job_id,
                                "batch_index": batch_index,
                                "current_stage": batch["current_stage"],
                                "status": job.get_status()
                            }
                        })
                    else:
                        music_params = job.original_params
                        unique_id = None

                if self.suno_service is None:
                    raise Exception("Suno service not initialized")

                if resume_task_id is not None:
                    task_id = resume_task_id
                    resume_task_id = None
                else:
                    if self.suno_credits_exhausted:
                        log_service.error("CIRCUIT BREAKER: Suno credits exhausted. Stopping all generation.")
                        log_service.system(
                            "Music params have been saved and can be resumed later after topping up credits.")
                        raise SunoAPIError(429, "Suno credits exhausted - circuit breaker open (insufficient credits)")

                    batch["current_stage"] = "Waiting for Suno slot..."
                    batch["suno_stage"] = "QUEUED"
                    await self._notify(job.session_id, {
                        "type": "generation_stage_update",
                        "data": {
                            "job_id": job_id,
                            "batch_index": batch_index,
                            "current_stage": batch["current_stage"],
                            "status": job.get_status()
                        }
                    })

                    if self.suno_semaphore is None:
                        raise Exception("Suno semaphore not initialized")
                    async with self.suno_semaphore:
                        batch["current_stage"] = "Submitting to Suno AI..."
                        batch["suno_stage"] = "SUBMITTING"
                        await self._notify(job.session_id, {
                            "type": "generation_stage_update",
                            "data": {
                                "job_id": job_id,
                                "batch_index": batch_index,
                                "current_stage": batch["current_stage"],
                                "status": job.get_status()
                            }
                        })

                        task_id, inflight_record = await self._submit_once(job, batch_index, music_params, unique_id)
                        self.suno_credits_exhausted = False
                        log_service.system(f"Task ID: {task_id}")

                        await asyncio.sleep(10)

                batch["suno_task_id"] = task_id
                batch["current_stage"] = "Generating with Suno AI..."
                batch["suno_stage"] = "PENDING"
                batch["progress_percent"] = job.calculate_progress(batch_index, "suno", 0)
                await self._notify(job.session_id, {
                    "type": "generation_stage_update",
                    "data": {
                        "job_id": job_id,
                        "batch_index": batch_index,
                        "current_stage": batch["current_stage"],
                        "status": job.get_status()
                    }
                })

                async def suno_status_callback(status):
                    batch["suno_stage"] = status
                    batch["current_stage"] = f"Suno AI: {status}"
                    await self._notify(job.session_id, {
                        "type": "generation_stage_update",
                        "data": {
                            "job_id": job_id,
                            "batch_index": batch_index,
                            "current_stage": batch["current_stage"],
                            "status": job.get_status()
                        }
                    })

                poll_data = await self.suno_service.await_task(task_id, status_callback=suno_status_callback)

                if not poll_data:
                    raise Exception(f"Suno polling failed for task {task_id}")

                if isinstance(poll_data, dict) and poll_data.get("error") == "SENSITIVE_WORD_ERROR":
                    sensitive_word_attempts += 1

                    await self._log_failed_prompt(
                        music_params=music_params,
                        error_type="SENSITIVE_WORD_ERROR",
                        error_details=poll_data.get("details"),
                        user_request=job.user_request
                    )

                    self.generation_stats["sensitive_word_failures"] += 1

                    can_regenerate = (
                        sensitive_word_attempts < self.max_sensitive_word_retries
                        and attempt < max_retries
                        and job.user_request
                    )
                    if can_regenerate:
                        log_service.warning(
                            f"SENSITIVE_WORD_ERROR detected (attempt {sensitive_word_attempts}/{self.max_sensitive_word_retries}). "
                            f"Calling Gemini again for fresh params..."
                        )

                        batch["current_stage"] = "Regenerating parameters (avoiding sensitive words)..."
                        batch["gemini_stage"] = "REGENERATING"

                        if self.catalog_service is None:
                            raise Exception("Catalog service not initialized")
                        if self.prompt_service is None:
                            raise Exception("Prompt service not initialized")
                        repeated_titles = self.catalog_service.get_repeated_titles(min_occurrences=2)
                        overused_phrases = self.catalog_service.get_cycled_phrases(
                            cycle_index=self.phrase_cycle_counter,
                            phrases_per_cycle=5,
                            top_n=20
                        )
                        self.phrase_cycle_counter = (self.phrase_cycle_counter + 1) % 4
                        music_params = await self.prompt_service.generate_music_params(
                            job.user_request,
                            catalog_service=self.catalog_service,
                            repeated_titles=repeated_titles,
                            overused_phrases=overused_phrases
                        )
                        self.generation_stats["gemini_calls"] += 1

                        if not music_params:
                            raise Exception("Failed to regenerate music parameters after SENSITIVE_WORD_ERROR")

                        log_service.success("Fresh params generated - retrying with new content")
                        batch["title"] = music_params.get("title", batch.get("title"))
                        batch["gemini_stage"] = "COMPLETE"

                        raise Exception(
                            f"SENSITIVE_WORD_ERROR - retrying with fresh Gemini params (attempt {sensitive_word_attempts})")
                    else:
                        reason = "max retries reached" if job.user_request else "no user_request to regenerate"
                        raise Exception(f"SENSITIVE_WORD_ERROR - cannot retry ({reason})")

                tracks = poll_data.get("tracks")
                if tracks is None:
                    raise Exception("No tracks returned from Suno")

                track_ids = self._assign_track_ids(len(tracks), unique_id, job, resume_track_ids)
                resume_track_ids = []
                attempt_track_ids = list(track_ids)
                batch["track_ids"] = list(dict.fromkeys(batch["track_ids"] + track_ids))
                if inflight_record is not None:
                    inflight_record["track_ids"] = track_ids
                    await self._update_inflight(task_id, inflight_record)

                track_jobs = []
                track_data_list = []

                for track_index, track in enumerate(tracks):
                    batch["current_track_index"] = track_index
                    current_track_unique_id = track_ids[track_index]
                    track_metadata: Optional[Dict[str, Any]] = None

                    if self.metadata_service is None:
                        raise Exception("Metadata service not initialized")
                    if unique_id is not None and current_track_unique_id == unique_id and job.user_request:
                        await self.metadata_service.update_generation_status(
                            unique_id,
                            "suno_completed",
                            track_data=track
                        )
                        track_metadata = await self.metadata_service.load_metadata(unique_id)

                    if track_metadata is None:
                        track_metadata = await self.metadata_service.create_metadata(
                            user_request=job.user_request or music_params.get("prompt", "Generated similar track"),
                            music_params=music_params,
                            track_data=track,
                            unique_id=current_track_unique_id,
                            generation_status="suno_completed"
                        )

                        if job.source_track_id:
                            track_metadata["generated_from"] = job.source_track_id

                    audio_url = track.get("audioUrl")
                    image_url = track.get("imageUrl")

                    if audio_url:
                        batch["current_stage"] = f"Downloading track {track_index + 1}/2..."
                        batch["progress_percent"] = job.calculate_progress(batch_index, "downloading", track_index)
                        await self._notify(job.session_id, {
                            "type": "generation_stage_update",
                            "data": {
                                "job_id": job_id,
                                "batch_index": batch_index,
                                "current_stage": batch["current_stage"],
                                "status": job.get_status()
                            }
                        })

                        audio_path = self.metadata_service.get_audio_path(current_track_unique_id)
                        success = await self.suno_service.download_track(audio_url, audio_path)

                        if success:
                            await self.metadata_service.save_metadata(track_metadata, current_track_unique_id)

                            log_service.system(f"Enriching metadata with advanced tags for {current_track_unique_id}")
                            batch["current_stage"] = f"Track {track_index + 1}/2: Enriching Metadata"
                            batch["upscaling_stage"] = "MetadataEnrichment"
                            batch["progress_percent"] = job.calculate_progress(batch_index, "metadata_enrichment", track_index)
                            await self._notify(job.session_id, {
                                "type": "generation_stage_update",
                                "data": {
                                    "job_id": job_id, "batch_index": batch_index,
                                    "current_stage": batch["current_stage"],
                                    "status": job.get_status()
                                }
                            })
                            try:
                                if self.enriched_metadata_service is None:
                                    raise Exception("Enriched metadata service not initialized")
                                enriched_metadata = await self.enriched_metadata_service.enrich_metadata(track_metadata)
                                if enriched_metadata:
                                    await self.enriched_metadata_service.save_enriched_metadata(
                                        enriched_metadata,
                                        overwrite_original=True
                                    )
                                    log_service.success(
                                        f"Metadata enriched: {enriched_metadata.get('derived_tags', {}).get('primary_genre', 'N/A')}")
                                else:
                                    log_service.warning(
                                        f"Metadata enrichment returned None for {current_track_unique_id}")
                            except Exception as e:
                                log_service.error(f"Metadata enrichment failed for {current_track_unique_id}: {str(e)}")

                            if image_url:
                                image_path = self.metadata_service.get_image_path(current_track_unique_id)
                                await self.suno_service.download_image(image_url, image_path)

                            batch["tracks"].append(current_track_unique_id)
                            job.completed_track_ids.append(current_track_unique_id)

                            def make_progress_callback(idx):
                                async def upscaling_progress_callback(stage):
                                    stage_info = {
                                        "Apollo": {"key": "apollo", "display": "Bandwidth Restoration"},
                                        "Demucs": {"key": "demucs", "display": "Stem Separation"},
                                        "ClearVoice": {"key": "clearvoice", "display": "Vocal Enhancement"},
                                        "SonicMaster": {"key": "sonicmaster", "display": "Audio Enhancement"},
                                        "Mastering": {"key": "master", "display": "Mastering"},
                                        "AudioFeatures": {"key": "audio_features", "display": "Extracting Audio Features"},
                                        "Lyrics": {"key": "lyric_timestamps", "display": "Extracting Lyric Timestamps"},
                                        "Transcoding": {"key": "finalizing", "display": "Transcoding"},
                                        "Artwork": {"key": "artwork", "display": "Processing Artwork"},
                                        "MetadataEnrichment": {"key": "metadata_enrichment", "display": "Enriching Metadata"},
                                    }
                                    info = stage_info.get(stage, {"key": "apollo", "display": stage})
                                    progress_stage = info["key"]
                                    batch["current_stage"] = f"Track {idx + 1}/2: {info['display']}"
                                    batch["upscaling_stage"] = stage
                                    batch["progress_percent"] = job.calculate_progress(batch_index, progress_stage, idx)
                                    await self._notify(job.session_id, {
                                        "type": "generation_stage_update",
                                        "data": {
                                            "job_id": job_id,
                                            "batch_index": batch_index,
                                            "current_stage": batch["current_stage"],
                                            "status": job.get_status()
                                        }
                                    })

                                return upscaling_progress_callback

                            if self.orchestrator is None:
                                raise Exception("Orchestrator not initialized")
                            track_job = await self.orchestrator.submit_track(
                                track_id=current_track_unique_id,
                                mp3_path=audio_path,
                                metadata=track_metadata,
                                progress_callback=make_progress_callback(track_index),
                                defer_catalog_reload=True
                            )

                            track_jobs.append(track_job)
                            job.track_jobs.append(track_job)
                            if job.cancelled:
                                track_job.cancelled = True
                            track_data_list.append({
                                "track_id": current_track_unique_id,
                                "track_index": track_index
                            })

                        else:
                            log_service.error(f"Download failed for {current_track_unique_id}. Skipping track.")
                            raise Exception(f"Failed to download track {current_track_unique_id}")

                if track_jobs:
                    log_service.system(f"Waiting for {len(track_jobs)} tracks to complete via Highway Architecture...")
                    loop = asyncio.get_running_loop()
                    pipeline_deadline = loop.time() + TRACK_PIPELINE_TIMEOUT_S
                    while any(not tj.is_finished() for tj in track_jobs):
                        if loop.time() >= pipeline_deadline:
                            log_service.error(
                                f"Highway pipeline timed out after {TRACK_PIPELINE_TIMEOUT_S}s for job {job_id} batch {batch_index}"
                            )
                            break
                        await asyncio.sleep(1.0)
                    if all(tj.is_complete() for tj in track_jobs):
                        log_service.success(f"All {len(track_jobs)} tracks completed via parallel pipeline!")
                    else:
                        log_service.warning(f"Highway pipeline finished with incomplete tracks for job {job_id} batch {batch_index}")

                for track_data, track_job in zip(track_data_list, track_jobs):
                    current_track_unique_id = track_data["track_id"]
                    track_index = track_data["track_index"]

                    if not track_job.mastering_complete:
                        log_service.error(f"Orchestrator pipeline failed for {current_track_unique_id}")
                        raise Exception("Orchestrator pipeline failed")

                    log_service.success(f"Orchestrator complete: {track_job.master_wav_path.name}")
                    job.completed_tracks += 1

                    batch["current_stage"] = f"Track {track_index + 1}/2: Finalizing..."
                    batch["upscaling_stage"] = "Complete"
                    batch["progress_percent"] = job.calculate_progress(batch_index, "finalizing", track_index)
                    await self._notify(job.session_id, {
                        "type": "generation_stage_update",
                        "data": {
                            "job_id": job_id,
                            "batch_index": batch_index,
                            "current_stage": batch["current_stage"],
                            "status": job.get_status()
                        }
                    })

                    if self.vector_search_service is None:
                        raise Exception("Vector search service not initialized")
                    await self.vector_search_service.add_track(current_track_unique_id)

                    if settings is None:
                        raise Exception("Settings not initialized")
                    master_wav_path = settings.ENHANCED_WAV_DIR / f"{current_track_unique_id}.wav"
                    if master_wav_path.exists():
                        await self.metadata_service.update_generation_status(
                            current_track_unique_id,
                            "completed"
                        )
                        log_service.success(
                            f"Job {job_id} batch {batch_index}: Track {current_track_unique_id} completed"
                        )
                    else:
                        log_service.error(
                            f"CRITICAL: Master WAV missing for {current_track_unique_id}, NOT marking as completed!"
                        )
                        raise Exception(f"Master WAV file missing: {master_wav_path}")

                if self.catalog_service is None:
                    raise Exception("Catalog service not initialized")
                await self.catalog_service.reload_catalog()

                if batch["tracks"]:
                    if self.playback_service is None:
                        raise Exception("Playback service not initialized")
                    try:
                        await self.playback_service.add_to_queue(
                            job.session_id,
                            batch["tracks"],
                            position=None,
                            user_id=job.user_id
                        )
                        log_service.api(
                            f"Auto-added {len(batch['tracks'])} tracks to queue for session {job.session_id}"
                        )
                    except Exception as e:
                        log_service.warning(
                            f"Could not auto-add generated tracks to queue for session {job.session_id}: {e}"
                        )

                batch["status"] = "completed"
                batch["error"] = None
                batch["progress_percent"] = 100

                self.generation_stats["successful_jobs"] += 1
                self.generation_stats["total_tracks_generated"] += len(batch["tracks"])

                await self._update_inflight(task_id, None)

                await self._notify(job.session_id, {
                    "type": "generation_batch_completed",
                    "data": {
                        "job_id": job_id,
                        "batch_index": batch_index,
                        "tracks": batch["tracks"],
                        "status": job.get_status()
                    }
                })

                return

            except Exception as e:
                error_msg = str(e)
                batch["error"] = error_msg

                if task_id:
                    await self._update_inflight(task_id, None)
                inflight_record = None

                if isinstance(e, SunoSubmitNeedsAttention):
                    self.generation_stats["failed_jobs"] += 1
                    self.generation_stats["other_failures"] += 1
                    await self._fail_batch(job, batch_index, error_msg)
                    return

                lowered = error_msg.lower()
                is_credit_error = (isinstance(e, SunoAPIError) and e.is_insufficient_credits) or (
                        "insufficient" in lowered and "credit" in lowered)
                if is_credit_error:
                    if not self.suno_credits_exhausted:
                        log_service.error(
                            f"SUNO CREDITS EXHAUSTED - Activating circuit breaker for {SUNO_CREDIT_BREAKER_COOLDOWN_S}s!")
                        log_service.system(
                            "All music params have been saved. You can resume generation after topping up credits.")
                        self.suno_credits_exhausted = True
                    self.generation_stats["credit_exhausted_failures"] += 1
                    self.generation_stats["failed_jobs"] += 1
                    await self._fail_batch(job, batch_index, "Suno credits exhausted")
                    return

                log_service.error(
                    f"Job {job_id} batch {batch_index} attempt {attempt + 1} failed: {error_msg}"
                )

                if attempt >= max_retries and "SENSITIVE_WORD_ERROR - retrying" not in error_msg:
                    self.generation_stats["failed_jobs"] += 1

                    if "SENSITIVE_WORD_ERROR" not in error_msg:
                        self.generation_stats["other_failures"] += 1

                    await self._fail_batch(job, batch_index, error_msg)
                    return

                discard_ids = list(attempt_track_ids)
                if unique_id is not None and unique_id not in discard_ids:
                    discard_ids.append(unique_id)
                await self._discard_attempt_outputs(job, batch, discard_ids)

                if job.user_request and music_params is not None and unique_id is not None:
                    unique_id = await self._save_params_metadata(job, music_params)

                wait_time = 2 ** attempt
                await asyncio.sleep(wait_time)

        if batch["status"] not in TERMINAL_BATCH_STATUSES:
            self.generation_stats["failed_jobs"] += 1
            await self._fail_batch(job, batch_index, batch.get("error") or "Generation failed after all retries")

    async def _submit_once(self, job: GenerationJob, batch_index: int, music_params: Dict[str, Any],
                           unique_id: Optional[str]) -> Tuple[str, Dict[str, Any]]:
        if self.suno_service is None:
            raise Exception("Suno service not initialized")
        batch = job.batches[batch_index]
        pending_key = self._pending_key(job.job_id, batch_index)
        pending = self._build_inflight_record(job, batch_index, None, music_params, unique_id)
        async with self._submit_lock:
            credits_before = await self.suno_service.get_credits()
            await self._update_inflight(pending_key, pending)
            batch["suno_paid"] = True
            try:
                submit_result = await self.suno_service.submit_task(music_params)
            except asyncio.CancelledError:
                if job.cancelled:
                    await self._update_inflight(pending_key, {
                        **pending, "status": STATUS_UNCONFIRMED, "unconfirmed_at": time.time(),
                        "refunded": True, "error": "cancelled by user during submit"})
                raise
            except SunoSubmitUnconfirmed as e:
                created = await self._task_possibly_created(credits_before)
                if created is False:
                    batch["suno_paid"] = False
                    await self._update_inflight(pending_key, None)
                    raise Exception(f"Suno submit failed ({e}); credits unchanged so no task was created") from e
                await self._update_inflight(pending_key, {
                    **pending, "status": STATUS_UNCONFIRMED, "unconfirmed_at": time.time(),
                    "refunded": bool(job.reservation), "error": str(e),
                    "credits_before": credits_before})
                log_service.error(
                    f"Job {job.job_id} batch {batch_index}: Suno submit outcome unknown ({e}); not resubmitting to "
                    f"avoid a double charge. Kept as {pending_key} in {SUNO_INFLIGHT_TASKS_FILE.name} for review."
                )
                raise SunoSubmitNeedsAttention(f"{UNCONFIRMED_MARKER}: {e}") from e
            except Exception:
                batch["suno_paid"] = False
                await self._update_inflight(pending_key, None)
                raise
            task_id = submit_result["data"]["taskId"]
            usage_tracking.record_suno_generation(model=str(settings.SUNO_MODEL_VERSION))
            self._submit_counter += 1
            spawn(self._observe_credit_delta(credits_before), name=f"suno_credit_delta:{task_id}")
            record = self._build_inflight_record(job, batch_index, task_id, music_params, unique_id)
            await self._swap_inflight(pending_key, task_id, record)
            return task_id, record

    async def _observe_credit_delta(self, credits_before: Optional[float]):
        if credits_before is None or self.suno_service is None:
            return
        submits_before = self._submit_counter
        await asyncio.sleep(20)
        if self._submit_counter != submits_before:
            return
        credits_after = await self.suno_service.get_credits()
        if credits_after is not None and self._submit_counter == submits_before:
            usage_tracking.note_suno_credit_delta(credits_before - credits_after)

    async def _task_possibly_created(self, credits_before: Optional[float]) -> Optional[bool]:
        if credits_before is None or self.suno_service is None:
            return None
        readings = []
        for _ in range(2):
            await asyncio.sleep(SUBMIT_CONFIRM_SETTLE_S)
            readings.append(await self.suno_service.get_credits())
        if any(reading is None for reading in readings):
            return None
        if any(reading < credits_before for reading in readings):
            return True
        if all(reading == credits_before for reading in readings):
            return False
        return None

    def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        job = self.jobs.get(job_id)
        return job.get_status() if job else None

    def is_job_owner(self, job_id: str, session_id: str, user_id: Optional[int] = None) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False
        if user_id is not None and job.user_id is not None:
            return int(job.user_id) == int(user_id)
        return job.session_id == session_id

    def get_all_jobs(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        jobs = self.jobs.values()
        if session_id:
            jobs = [j for j in jobs if j.session_id == session_id]
        return [j.get_status() for j in jobs]

    async def cancel_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False

        if job._finalized:
            return True

        job.cancelled = True
        for track_job in job.track_jobs:
            track_job.cancelled = True
        pending = [task for task in job.tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            _done, still_running = await asyncio.wait(pending, timeout=CANCEL_WAIT_TIMEOUT_S)
            if still_running:
                log_service.warning(
                    f"Job {job_id}: {len(still_running)} batch task(s) did not stop within {CANCEL_WAIT_TIMEOUT_S}s")
        for track_job in job.track_jobs:
            track_job.cancelled = True

        cancelled_track_ids: List[str] = []
        for batch_index, batch in job.batches.items():
            if batch["status"] in TERMINAL_BATCH_STATUSES:
                continue
            cancelled_track_ids.extend(batch.get("track_ids") or [])
            await self._update_inflight(batch.get("suno_task_id"), None)
            await self._fail_batch(job, batch_index, "Cancelled by user", refund=not batch.get("suno_paid"))

        await self._finalize_job_if_done(job)
        if cancelled_track_ids:
            spawn(self._cleanup_cancelled_tracks(job, cancelled_track_ids), name=f"suno_cancel_cleanup:{job_id[:8]}")
        log_service.system(f"Cancelled generation job {job_id}")
        return True

    @staticmethod
    def _cancelled_track_paths(track_id: str) -> List[Path]:
        from services import track_asset_stages as stages
        paths = [p for p in stages.catalog_output_paths(track_id) if p != stages.metadata_path(track_id)]
        paths.extend([
            settings.WAV_DIR / f"{track_id}.wav",
            settings.UPSCALED_WAV_DIR / f"{track_id}.wav",
            settings.SONIC_WAV_DIR / f"{track_id}.wav",
            settings.VOCAL_ENHANCED_WAV_DIR / f"{track_id}.wav",
            settings.DEMUCS_STEMS_DIR / track_id,
        ])
        return paths

    @classmethod
    def _quarantine_cancelled_sync(cls, track_id: str) -> int:
        target_dir = settings.ASSET_DOCTOR_QUARANTINE_DIR / "cancelled_generations" / f"{track_id}.{int(time.time())}"
        moved = 0
        for path in cls._cancelled_track_paths(track_id):
            if not path.exists():
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(path), str(target_dir / f"{path.parent.name}__{path.name}"))
                moved += 1
            except OSError as e:
                log_service.warning(f"Could not move cancelled output {path}: {e}")
        return moved

    async def _cleanup_cancelled_tracks(self, job: GenerationJob, track_ids: List[str]):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + CANCEL_CLEANUP_TIMEOUT_S
        watched = [tj for tj in job.track_jobs if tj.track_id in track_ids]
        while any(not tj.is_settled() for tj in watched):
            if loop.time() >= deadline:
                log_service.warning(f"Job {job.job_id}: cancelled tracks still processing after "
                                    f"{CANCEL_CLEANUP_TIMEOUT_S}s - leaving their files in place")
                return
            await asyncio.sleep(CANCEL_CLEANUP_POLL_S)

        for track_id in track_ids:
            try:
                metadata = None
                if self.metadata_service is not None and (self.metadata_service.metadata_dir / f"{track_id}.json").exists():
                    metadata = await self.metadata_service.load_metadata(track_id)
                if metadata and metadata.get("generation_status") == "completed":
                    continue
                moved = await asyncio.to_thread(self._quarantine_cancelled_sync, track_id)
                if metadata:
                    await self.metadata_service.update_generation_status(track_id, "cancelled")
                log_service.system(f"Job {job.job_id}: moved {moved} output file(s) of cancelled track {track_id} to quarantine")
            except Exception as e:
                log_service.warning(f"Job {job.job_id}: cleanup of cancelled track {track_id} failed: {e}")

    def reset_generation_stats(self):
        self.generation_stats = {
            "total_jobs": 0,
            "successful_jobs": 0,
            "failed_jobs": 0,
            "sensitive_word_failures": 0,
            "credit_exhausted_failures": 0,
            "other_failures": 0,
            "total_tracks_generated": 0,
            "total_retries": 0,
            "gemini_calls": 0
        }

    async def print_generation_summary(self):
        stats = self.generation_stats
        log_service.system("\n" + "=" * 80)
        log_service.system("GENERATION SUMMARY")
        log_service.system("=" * 80)
        log_service.success(f"Total Jobs: {stats['total_jobs']}")
        log_service.success(f"  ✓ Successful: {stats['successful_jobs']}")
        if stats['failed_jobs'] > 0:
            log_service.error(f"  ✗ Failed: {stats['failed_jobs']}")
            if stats['sensitive_word_failures'] > 0:
                log_service.warning(f"    - Sensitive word failures: {stats['sensitive_word_failures']}")
            if stats['credit_exhausted_failures'] > 0:
                log_service.warning(f"    - Credit exhausted: {stats['credit_exhausted_failures']}")
            if stats['other_failures'] > 0:
                log_service.warning(f"    - Other failures: {stats['other_failures']}")

        log_service.success(f"\nTotal Tracks Generated: {stats['total_tracks_generated']}")
        log_service.system(f"Gemini API Calls: {stats['gemini_calls']}")
        log_service.system(f"Total Retries: {stats['total_retries']}")

        success_rate = (stats['successful_jobs'] / stats['total_jobs'] * 100) if stats['total_jobs'] > 0 else 0
        log_service.success(f"Success Rate: {success_rate:.1f}%")
        log_service.system("=" * 80)