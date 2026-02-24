import asyncio
import uuid
import aiofiles
from typing import Dict, Any, Optional, Callable, List, Tuple
from datetime import datetime, UTC
import json
from services import log_service
from services.base_service import SingletonService
from config import settings

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
                "progress_percent": 0
            }

        self.completed_track_ids: List[str] = []
        self.tasks: List[asyncio.Task] = []
        self.current_stage = "Initializing..."
        self.completed_tracks = 0
        self.title = original_params.get("title")
        self._last_broadcast_state: Optional[str] = None
        self._pregenerated_params: Optional[Dict[str, Any]] = None

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
            "tracks": self.completed_track_ids
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
        self.suno_credits_exhausted = False
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

            timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            filename = f"{error_type}_{timestamp}.json"
            filepath = failed_prompts_dir / filename

            failure_data = {
                "timestamp": datetime.now(UTC).isoformat(),
                "error_type": error_type,
                "user_request": user_request,
                "music_params": music_params,
                "error_details": error_details
            }

            async with asyncio.Lock():
                async with aiofiles.open(filepath, 'w', encoding='utf-8') as f:
                    await f.write(json.dumps(failure_data, indent=2, ensure_ascii=False))

            log_service.system(f"Failed prompt logged to: {filepath.name}")

        except Exception as e:
            log_service.warning(f"Could not log failed prompt: {str(e)}")

    async def start_generation_job(
            self,
            session_id: str,
            original_params: Dict[str, Any],
            batch_count: int = 3,
            user_id: Optional[int] = None,
            source_track_id: Optional[str] = None,
            user_request: Optional[str] = None,
            pregenerated_params: Optional[Dict[str, Any]] = None
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

    async def _generate_batch(self, job_id: str, batch_index: int):
        job = self.jobs.get(job_id)
        if not job:
            return

        batch = job.batches[batch_index]
        music_params: Optional[Dict[str, Any]] = None
        unique_id: Optional[str] = None
        sensitive_word_attempts = 0

        for attempt in range(self.max_retries + 1):
            batch["attempts"] = attempt + 1

            if attempt > 0:
                batch["status"] = "retrying"
                self.generation_stats["total_retries"] += 1
                log_service.system(
                    f"Job {job_id} batch {batch_index}: Retry attempt {attempt + 1}/{self.max_retries + 1}"
                )
                await self._notify(job.session_id, {
                    "type": "generation_retrying",
                    "data": {
                        "job_id": job_id,
                        "batch_index": batch_index,
                        "attempt": attempt + 1,
                        "max_attempts": self.max_retries + 1
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

                        if self.metadata_service is None:
                            raise Exception("Metadata service not initialized")
                        await self.metadata_service.save_metadata(initial_metadata, unique_id)
                        log_service.success(
                            f"Saved music params to {unique_id}.json (can resume later if needed)")

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

                if self.suno_credits_exhausted:
                    log_service.error("CIRCUIT BREAKER: Suno credits exhausted. Stopping all generation.")
                    log_service.system(
                        "Music params have been saved and can be resumed later after topping up credits.")
                    self.suno_credits_exhausted = True
                    raise Exception("Suno credits exhausted - circuit breaker activated")

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

                task_id = None
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

                    if self.suno_service is None:
                        raise Exception("Suno service not initialized")
                    submit_result = await self.suno_service.submit_task(music_params)

                    if not submit_result:
                        raise Exception("Suno submission returned no result")

                    task_id = submit_result["data"]["taskId"]
                    log_service.system(f"Task ID: {task_id}")

                    await asyncio.sleep(10)

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

                if self.suno_service is None:
                    raise Exception("Suno service not initialized")
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

                    if sensitive_word_attempts < self.max_sensitive_word_retries and job.user_request:
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
                        batch["gemini_stage"] = "COMPLETE"

                        raise Exception(
                            f"SENSITIVE_WORD_ERROR - retrying with fresh Gemini params (attempt {sensitive_word_attempts})")
                    else:
                        reason = "max retries reached" if sensitive_word_attempts >= self.max_sensitive_word_retries else "no user_request to regenerate"
                        raise Exception(f"SENSITIVE_WORD_ERROR - cannot retry ({reason})")

                tracks = poll_data.get("tracks")
                if tracks is None:
                    raise Exception("No tracks returned from Suno")

                track_jobs = []
                track_data_list = []

                for track_index, track in enumerate(tracks):
                    batch["current_track_index"] = track_index
                    track_metadata: Dict[str, Any]
                    current_track_unique_id: str

                    if self.metadata_service is None:
                        raise Exception("Metadata service not initialized")
                    if track_index == 0 and unique_id is not None and job.user_request:
                        await self.metadata_service.update_generation_status(
                            unique_id,
                            "suno_completed",
                            track_data=track
                        )
                        track_metadata = await self.metadata_service.load_metadata(unique_id)
                        current_track_unique_id = unique_id
                    else:
                        track_unique_id = self.metadata_service.generate_unique_id()
                        track_metadata = await self.metadata_service.create_metadata(
                            user_request=job.user_request or music_params.get("prompt", "Generated similar track"),
                            music_params=music_params,
                            track_data=track,
                            unique_id=track_unique_id,
                            generation_status="suno_completed"
                        )
                        current_track_unique_id = track_unique_id

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

                        if self.metadata_service is None:
                            raise Exception("Metadata service not initialized")
                        if self.suno_service is None:
                            raise Exception("Suno service not initialized")
                        audio_path = self.metadata_service.get_audio_path(current_track_unique_id)
                        success = await self.suno_service.download_track(audio_url, audio_path)

                        if success:
                            if self.metadata_service is None:
                                raise Exception("Metadata service not initialized")
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
                                if self.metadata_service is None:
                                    raise Exception("Metadata service not initialized")
                                if self.suno_service is None:
                                    raise Exception("Suno service not initialized")
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
                                progress_callback=make_progress_callback(track_index)
                            )

                            track_jobs.append(track_job)
                            track_data_list.append({
                                "track_id": current_track_unique_id,
                                "track_index": track_index
                            })

                        else:
                            log_service.error(f"Download failed for {current_track_unique_id}. Skipping track.")
                            raise Exception(f"Failed to download track {current_track_unique_id}")

                if track_jobs:
                    log_service.system(f"Waiting for {len(track_jobs)} tracks to complete via Highway Architecture...")
                    while any(not tj.is_complete() for tj in track_jobs):
                        await asyncio.sleep(1.0)
                    log_service.success(f"All {len(track_jobs)} tracks completed via parallel pipeline!")

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
                        if self.metadata_service is None:
                            raise Exception("Metadata service not initialized")
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
                    await self.playback_service.add_to_queue(
                        job.session_id,
                        batch["tracks"],
                        position=None,
                        user_id=job.user_id
                    )

                    log_service.api(
                        f"Auto-added {len(batch['tracks'])} tracks to queue for session {job.session_id}"
                    )

                batch["status"] = "completed"
                batch["error"] = None
                batch["progress_percent"] = 100

                self.generation_stats["successful_jobs"] += 1
                self.generation_stats["total_tracks_generated"] += len(batch["tracks"])

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

                if "insufficient" in error_msg.lower() and "credit" in error_msg.lower():
                    log_service.error("SUNO CREDITS EXHAUSTED - Activating circuit breaker!")
                    log_service.system(
                        "All music params have been saved. You can resume generation after topping up credits.")
                    self.suno_credits_exhausted = True
                    self.generation_stats["credit_exhausted_failures"] += 1
                    self.generation_stats["failed_jobs"] += 1
                    batch["status"] = "failed"
                    await self._notify(job.session_id, {
                        "type": "generation_batch_failed",
                        "data": {
                            "job_id": job_id,
                            "batch_index": batch_index,
                            "error": "Suno credits exhausted",
                            "status": job.get_status()
                        }
                    })
                    return

                log_service.error(
                    f"Job {job_id} batch {batch_index} attempt {attempt + 1} failed: {error_msg}"
                )

                if attempt >= self.max_retries and "SENSITIVE_WORD_ERROR - retrying" not in error_msg:
                    batch["status"] = "failed"
                    self.generation_stats["failed_jobs"] += 1

                    if "SENSITIVE_WORD_ERROR" in error_msg:
                        pass
                    else:
                        self.generation_stats["other_failures"] += 1

                    await self._notify(job.session_id, {
                        "type": "generation_batch_failed",
                        "data": {
                            "job_id": job_id,
                            "batch_index": batch_index,
                            "error": error_msg,
                            "status": job.get_status()
                        }
                    })
                    return

                wait_time = 2 ** attempt
                await asyncio.sleep(wait_time)

        if all(b["status"] in ["completed", "failed"] for b in job.batches.values()):
            await self._notify(job.session_id, {
                "type": "generation_job_completed",
                "data": job.get_status()
            })

    def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        job = self.jobs.get(job_id)
        return job.get_status() if job else None

    def get_all_jobs(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        jobs = self.jobs.values()
        if session_id:
            jobs = [j for j in jobs if j.session_id == session_id]
        return [j.get_status() for j in jobs]

    async def cancel_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False

        for task in job.tasks:
            if not task.done():
                task.cancel()

        log_service.system(f"Cancelled generation job {job_id}")
        return True

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