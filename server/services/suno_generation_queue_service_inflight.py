"""Suno generations in flight: recorded on disk while Suno works, resumed or refunded after a restart, failed prompts
logged."""
import asyncio
import os
import time
import uuid
from typing import Dict, Any, Optional, List
from datetime import datetime, UTC
import json
from services import log_service
from services.rate_limit_service import rate_limit_service
from services.suno_generation_queue_service_jobs import (
    GenerationJob, STATUS_SUBMITTED, STATUS_SUBMITTING, STATUS_UNCONFIRMED, SUNO_INFLIGHT_TASKS_FILE,
    UNCONFIRMED_MARKER, UNCONFIRMED_RETENTION_S, friendly_generation_error,
)


class GenerationInflight:
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
