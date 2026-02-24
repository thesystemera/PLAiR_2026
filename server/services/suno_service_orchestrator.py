"""
Suno Service Orchestrator - Highway Architecture for Audio Processing

This orchestrator manages a 6-lane concurrent pipeline from mp3+metadata → catalog entry:

┌─────────────┬──────────────┬──────────────┬──────────────┬──────────────┬──────────────────────────┐
│  Lane 1     │  Lane 2      │  Lane 3      │  Lane 4      │  Lane 5      │       Lane 6             │
│  APOLLO     │  DEMUCS      │  CLEARVOICE  │  SONICMASTER │  WHISPER     │   CPU POOL               │
│  (GPU/VRAM) │  (GPU/VRAM)  │  (GPU/VRAM)  │  (GPU/VRAM)  │  (GPU/VRAM)  │   (CPU: 16-32 workers)   │
│  1 worker   │  1 worker    │  1 worker    │  1 worker    │  1 worker    │   Massive Parallelism    │
└─────────────┴──────────────┴──────────────┴──────────────┴──────────────┴──────────────────────────┘

All GPU lanes (1-5) run in parallel with 1 concurrent job each (services have internal locks).
Lane 6 runs up to N concurrent CPU jobs (configurable via settings.MAX_PARALLEL_CPU_WORKERS).

Lane 6 executes ALL of these in parallel PER TRACK:
  - Mastering & Normalization
  - Audio Features Extraction
  - WebM/Opus Transcoding (128k, 192k, 256k)
  - Artwork Download

Queues are bounded (maxsize=10) to provide backpressure during batch submissions.
Catalog registration only happens when ALL output files exist for a track.
"""

import asyncio
from pathlib import Path
from typing import Dict, Any, Optional, Callable, List, Tuple
from dataclasses import dataclass
from enum import Enum, auto
from collections import defaultdict
from services import log_service
from services.base_service import SingletonService
from services.audio_apollo_service import AudioApolloService
from services.audio_demucs_service import AudioDemucsService
from services.audio_clearvoice_service import AudioClearVoiceService
from services.audio_sonic_master_service import SonicMasterService
from services.audio_master_service import AudioMasterService
from services.audio_features_service import AudioFeaturesService
from services.audio_lyrical_timestamp_service import LyricalTimestampService
from services.audio_transcoding_service import AudioTranscodingService
from services.suno_service import SunoService
from services.catalog_database_service import CatalogDatabaseService
from services.suno_artwork_enrichment_service import ArtworkEnrichmentService
from config import settings

import torch
import torchaudio

LANE_BUFFER = 10

def _clear_cuda_cache():
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

class PipelineState(Enum):
    COMPLETED = auto()
    READY_FOR_FINALIZATION = auto()
    READY_FOR_MASTERING = auto()
    READY_FOR_SONIC = auto()
    READY_FOR_CLEARVOICE = auto()
    READY_FOR_DEMUCS = auto()
    NEW = auto()

@dataclass
class TrackJob:
    track_id: str
    mp3_path: Path
    metadata: Dict[str, Any]
    progress_callback: Optional[Callable] = None

    apollo_complete: bool = False
    demucs_complete: bool = False
    clearvoice_complete: bool = False
    sonic_complete: bool = False
    mastering_complete: bool = False
    features_complete: bool = False
    lyrics_complete: bool = False
    transcoding_complete: bool = False
    artwork_complete: bool = False

    apollo_wav_path: Optional[Path] = None
    demucs_vocals_path: Optional[Path] = None
    demucs_instrumentals_path: Optional[Path] = None
    clearvoice_enhanced_path: Optional[Path] = None
    sonic_wav_path: Optional[Path] = None
    master_wav_path: Optional[Path] = None

    def is_complete(self) -> bool:
        return all([
            self.mastering_complete,
            self.features_complete,
            self.lyrics_complete,
            self.transcoding_complete,
            self.artwork_complete
        ])

class SunoServiceOrchestrator(SingletonService):

    def __init__(self):
        if self._initialized:
            return

        self.apollo: Optional[AudioApolloService] = None
        self.demucs: Optional[AudioDemucsService] = None
        self.clearvoice: Optional[AudioClearVoiceService] = None
        self.sonic_master: Optional[SonicMasterService] = None
        self.master: Optional[AudioMasterService] = None
        self.features: Optional[AudioFeaturesService] = None
        self.lyrics: Optional[LyricalTimestampService] = None
        self.transcoding: Optional[AudioTranscodingService] = None
        self.suno: Optional[SunoService] = None
        self.catalog: Optional[CatalogDatabaseService] = None
        self.artwork_enrichment: Optional[ArtworkEnrichmentService] = None

        self.lane1_queue: Optional[asyncio.Queue] = None
        self.lane2_queue: Optional[asyncio.Queue] = None
        self.lane3_queue: Optional[asyncio.Queue] = None
        self.lane4_queue: Optional[asyncio.Queue] = None
        self.lane5_queue: Optional[asyncio.Queue] = None
        self.lane6_queue: Optional[asyncio.Queue] = None

        self.consumer_tasks = []
        self.running = False
        self.cpu_semaphore: Optional[asyncio.Semaphore] = None
        self._initialized = True

    async def initialize(self):
        log_service.suno("=" * 80)
        log_service.suno("SUNO SERVICE ORCHESTRATOR - HIGHWAY ARCHITECTURE")
        log_service.suno("=" * 80)

        self.apollo = AudioApolloService()
        await self.apollo.initialize()

        if settings.ENABLE_VOCAL_ENHANCEMENT:
            self.demucs = AudioDemucsService()
            await self.demucs.initialize()

            self.clearvoice = AudioClearVoiceService()
            await self.clearvoice.initialize()

        self.sonic_master = SonicMasterService()
        self.sonic_master.configure(
            wet_mix=0.5,
            num_inference_steps=50,
            prompt="give the mix more shine and sparkle, clean and dynamic with rich full harmonics",
            chunk_duration=30,
            fs=44100
        )
        await self.sonic_master.initialize()

        self.master = AudioMasterService()
        await self.master.initialize()

        self.features = AudioFeaturesService()
        await self.features.initialize()

        self.lyrics = LyricalTimestampService()
        await self.lyrics.initialize()

        self.transcoding = AudioTranscodingService()
        await self.transcoding.initialize()

        self.suno = SunoService()
        await self.suno.initialize()

        self.catalog = CatalogDatabaseService()
        await self.catalog.initialize()

        self.artwork_enrichment = ArtworkEnrichmentService()
        await self.artwork_enrichment.initialize()

        self.lane1_queue = asyncio.Queue(maxsize=LANE_BUFFER)
        self.lane2_queue = asyncio.Queue(maxsize=LANE_BUFFER)
        self.lane3_queue = asyncio.Queue(maxsize=LANE_BUFFER)
        self.lane4_queue = asyncio.Queue(maxsize=LANE_BUFFER)
        self.lane5_queue = asyncio.Queue(maxsize=LANE_BUFFER)
        self.lane6_queue = asyncio.Queue(maxsize=LANE_BUFFER)

        cpu_workers = getattr(settings, 'MAX_PARALLEL_CPU_WORKERS', 16)
        self.cpu_semaphore = asyncio.Semaphore(cpu_workers)

        log_service.suno("\n📊 Highway Configuration:")
        log_service.suno("  Lane 1 (Apollo):      1 worker  (GPU/VRAM)")
        log_service.suno("  Lane 2 (Demucs):      1 worker  (GPU/VRAM)")
        log_service.suno("  Lane 3 (ClearVoice):  1 worker  (GPU/VRAM)")
        log_service.suno("  Lane 4 (SonicMaster): 1 worker  (GPU/VRAM)")
        log_service.suno("  Lane 5 (Whisper):     1 worker  (GPU/VRAM)")
        log_service.suno(f"  Lane 6 (CPU Pool):    {cpu_workers} workers (CPU)")
        log_service.suno(f"  Queue Buffer:         {LANE_BUFFER} per lane")
        log_service.suno("=" * 80)

        self.running = True
        self.consumer_tasks = [
            asyncio.create_task(self._lane1_consumer(), name="Lane1-Apollo"),
            asyncio.create_task(self._lane2_consumer(), name="Lane2-Demucs"),
            asyncio.create_task(self._lane3_consumer(), name="Lane3-ClearVoice"),
            asyncio.create_task(self._lane4_consumer(), name="Lane4-SonicMaster"),
            asyncio.create_task(self._lane5_consumer(), name="Lane5-Whisper"),
            asyncio.create_task(self._lane6_consumer(), name="Lane6-CPUPool"),
        ]

        log_service.suno("✓ Orchestrator initialized - All highway lanes operational")

    def assess_track_state(self, track_id: str, metadata: Dict[str, Any]) -> PipelineState:
        if self.transcoding is None:
            raise RuntimeError("transcoding service not initialized")
        master_wav = settings.ENHANCED_WAV_DIR / f"{track_id}.wav"

        if master_wav.exists():
            features = settings.AUDIOFEATURES_DIR / f"{track_id}.json"
            lyrics = settings.LYRIC_TIMESTAMPS_DIR / f"{track_id}.json"
            artwork = settings.ARTWORK_DIR / f"{track_id}.jpeg"
            webm_exists = self.transcoding.get_webm_path(track_id, "128k").exists()

            if features.exists() and lyrics.exists() and artwork.exists() and webm_exists:
                return PipelineState.COMPLETED
            else:
                return PipelineState.READY_FOR_FINALIZATION

        sonic_wav = settings.SONIC_WAV_DIR / f"{track_id}.wav"
        if sonic_wav.exists():
            return PipelineState.READY_FOR_MASTERING

        enhanced_vocal_wav = settings.VOCAL_ENHANCED_WAV_DIR / f"{track_id}.wav"
        if enhanced_vocal_wav.exists():
            return PipelineState.READY_FOR_SONIC

        stems_dir = settings.DEMUCS_STEMS_DIR / track_id
        if stems_dir.exists() and (stems_dir / "vocals.wav").exists():
            return PipelineState.READY_FOR_CLEARVOICE

        apollo_wav = settings.WAV_DIR / f"{track_id}.wav"
        if apollo_wav.exists():
            is_instrumental = metadata.get("generation_params", {}).get("instrumental", False)
            if is_instrumental or not settings.ENABLE_VOCAL_ENHANCEMENT:
                return PipelineState.READY_FOR_SONIC
            return PipelineState.READY_FOR_DEMUCS

        return PipelineState.NEW

    async def smart_submit(
            self,
            track_id: str,
            mp3_path: Path,
            metadata: Dict[str, Any],
            progress_callback: Optional[Callable] = None,
            verbose: bool = True
    ) -> TrackJob:
        assert self.lane1_queue is not None
        assert self.lane2_queue is not None
        assert self.lane3_queue is not None
        assert self.lane4_queue is not None
        assert self.lane5_queue is not None
        assert self.lane6_queue is not None

        state = self.assess_track_state(track_id, metadata)

        job = TrackJob(
            track_id=track_id,
            mp3_path=mp3_path,
            metadata=metadata,
            progress_callback=progress_callback
        )

        def log(msg):
            if verbose:
                log_service.suno(msg)

        if state == PipelineState.COMPLETED:
            log(f"🚀 [{track_id[:8]}] Track complete, re-registering via Lane 6")
            job.master_wav_path = settings.ENHANCED_WAV_DIR / f"{track_id}.wav"
            job.sonic_wav_path = settings.SONIC_WAV_DIR / f"{track_id}.wav"
            job.mastering_complete = True
            job.sonic_complete = True
            await self.lane6_queue.put(job)

        elif state == PipelineState.READY_FOR_FINALIZATION:
            log(f"🚀 [{track_id[:8]}] Resuming at Lane 6 (Finalization/Metadata)")
            job.master_wav_path = settings.ENHANCED_WAV_DIR / f"{track_id}.wav"
            job.sonic_wav_path = settings.SONIC_WAV_DIR / f"{track_id}.wav"
            job.mastering_complete = True
            job.sonic_complete = True
            await self.lane6_queue.put(job)

        elif state == PipelineState.READY_FOR_MASTERING:
            log(f"🚀 [{track_id[:8]}] Resuming at Lane 6 (Mastering) & Lane 5 (Lyrics)")
            job.sonic_wav_path = settings.SONIC_WAV_DIR / f"{track_id}.wav"
            job.sonic_complete = True
            await self.lane6_queue.put(job)
            await self.lane5_queue.put(job)

        elif state == PipelineState.READY_FOR_SONIC:
            log(f"🚀 [{track_id[:8]}] Resuming at Lane 4 (SonicMaster)")
            enhanced_vocal = settings.VOCAL_ENHANCED_WAV_DIR / f"{track_id}.wav"
            apollo_wav = settings.WAV_DIR / f"{track_id}.wav"

            if enhanced_vocal.exists():
                job.clearvoice_enhanced_path = enhanced_vocal
                job.clearvoice_complete = True
            elif apollo_wav.exists():
                job.apollo_wav_path = apollo_wav
                job.apollo_complete = True

            await self.lane4_queue.put(job)

        elif state == PipelineState.READY_FOR_CLEARVOICE:
            log(f"🚀 [{track_id[:8]}] Resuming at Lane 3 (ClearVoice)")
            job.apollo_wav_path = settings.WAV_DIR / f"{track_id}.wav"
            job.apollo_complete = True
            job.demucs_vocals_path = settings.DEMUCS_STEMS_DIR / track_id / "vocals.wav"
            job.demucs_instrumentals_path = settings.DEMUCS_STEMS_DIR / track_id / "no_vocals.wav"
            job.demucs_complete = True
            await self.lane3_queue.put(job)

        elif state == PipelineState.READY_FOR_DEMUCS:
            log(f"🚀 [{track_id[:8]}] Resuming at Lane 2 (Demucs)")
            job.apollo_wav_path = settings.WAV_DIR / f"{track_id}.wav"
            job.apollo_complete = True
            await self.lane2_queue.put(job)

        else:
            log(f"🚀 [{track_id[:8]}] Submitting to Highway - Entry: Lane 1 (Apollo)")
            await self.lane1_queue.put(job)

        return job

    async def submit_track(
            self,
            track_id: str,
            mp3_path: Path,
            metadata: Dict[str, Any],
            progress_callback: Optional[Callable] = None
    ) -> TrackJob:
        return await self.smart_submit(track_id, mp3_path, metadata, progress_callback, verbose=True)

    async def submit_batch(
            self,
            tracks: List[Tuple[str, Path, Dict[str, Any]]],
            progress_callback: Optional[Callable] = None
    ) -> List[TrackJob]:

        jobs = []
        stats = defaultdict(int)

        if self.lane6_queue is None:
            raise RuntimeError("lane6_queue not initialized")

        log_service.suno(f"🚀 Analyzing batch of {len(tracks)} tracks for Highway injection...")

        track_states = []
        for track_id, mp3_path, metadata in tracks:
            state = self.assess_track_state(track_id, metadata)
            track_states.append((track_id, mp3_path, metadata, state))

            if state == PipelineState.NEW:
                stats["Lane 1 (New - Apollo)"] += 1
            elif state == PipelineState.READY_FOR_DEMUCS:
                stats["Lane 2 (Resuming - Demucs)"] += 1
            elif state == PipelineState.READY_FOR_CLEARVOICE:
                stats["Lane 3 (Resuming - ClearVoice)"] += 1
            elif state == PipelineState.READY_FOR_SONIC:
                stats["Lane 4 (Resuming - SonicMaster)"] += 1
            elif state == PipelineState.READY_FOR_MASTERING:
                stats["Lane 6 (Resuming - Mastering)"] += 1
            elif state == PipelineState.READY_FOR_FINALIZATION:
                stats["Lane 6 (Resuming - Finalization)"] += 1
            elif state == PipelineState.COMPLETED:
                stats["Lane 6 (Re-Registering)"] += 1

        log_service.suno("\n" + "=" * 60)
        log_service.suno("BATCH SUBMISSION MANIFEST")
        log_service.suno("=" * 60)
        for category, count in sorted(stats.items()):
            log_service.suno(f"  • {category:<35}: {count}")
        log_service.suno("=" * 60 + "\n")

        log_service.suno("Queuing tracks... (concurrent per-lane distribution)")

        async def queue_one(track_id, mp3_path, metadata):
            job = await self.smart_submit(track_id, mp3_path, metadata, progress_callback, verbose=False)
            jobs.append(job)

        await asyncio.gather(*[
            queue_one(tid, mp3, meta)
            for tid, mp3, meta, _ in track_states
        ])

        log_service.suno(f"✓ All {len(jobs)} tracks successfully queued on Highway.")
        return jobs

    async def _lane1_consumer(self):
        assert self.lane1_queue is not None
        lane1_queue = self.lane1_queue
        lane2_queue = self.lane2_queue
        lane4_queue = self.lane4_queue
        log_service.suno("Lane 1 (Apollo) consumer started")
        while self.running:
            job: Optional[TrackJob] = None
            try:
                job = await lane1_queue.get()
                assert job is not None
                log_service.suno(f"[Lane 1] [{job.track_id[:8]}] Apollo processing...")
                if job.progress_callback:
                    await job.progress_callback("Apollo")

                if self.apollo is None:
                    raise RuntimeError("apollo service not initialized")
                apollo_wav_path = settings.WAV_DIR / f"{job.track_id}.wav"
                result = await self.apollo.process_audio(job.mp3_path, apollo_wav_path)

                if result:
                    job.apollo_complete = True
                    job.apollo_wav_path = result
                    _clear_cuda_cache()

                    is_instrumental = job.metadata.get("generation_params", {}).get("instrumental", False)
                    if is_instrumental:
                        log_service.suno(f"[Lane 1] [{job.track_id[:8]}] Instrumental -> Lane 4")
                        assert lane4_queue is not None
                        await lane4_queue.put(job)
                    elif settings.ENABLE_VOCAL_ENHANCEMENT and self.demucs:
                        log_service.suno(f"[Lane 1] [{job.track_id[:8]}] Complete -> Lane 2")
                        assert lane2_queue is not None
                        await lane2_queue.put(job)
                    else:
                        log_service.suno(f"[Lane 1] [{job.track_id[:8]}] Enhancement Disabled -> Lane 4")
                        assert lane4_queue is not None
                        await lane4_queue.put(job)
                else:
                    log_service.error(f"[Lane 1] [{job.track_id[:8]}] Apollo FAILED")

                lane1_queue.task_done()
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log_service.error(f"[Lane 1] Error: {str(e)}")
                lane1_queue.task_done()
                await asyncio.sleep(0.05)

    async def _lane2_consumer(self):
        assert self.lane2_queue is not None
        lane2_queue = self.lane2_queue
        lane3_queue = self.lane3_queue
        lane4_queue = self.lane4_queue
        log_service.suno("Lane 2 (Demucs) consumer started")
        while self.running:
            try:
                job = await lane2_queue.get()
                assert job is not None
                log_service.suno(f"[Lane 2] [{job.track_id[:8]}] Demucs processing...")
                if job.progress_callback:
                    await job.progress_callback("Demucs")

                if self.demucs is None:
                    raise RuntimeError("demucs service not initialized")
                if job.apollo_wav_path is None:
                    raise ValueError("apollo_wav_path is required for Demucs processing")
                stems_dir = settings.DEMUCS_STEMS_DIR / job.track_id
                stems = await self.demucs.separate_stems(job.apollo_wav_path, stems_dir)

                if stems:
                    job.demucs_complete = True
                    job.demucs_vocals_path = stems.get("vocals")
                    job.demucs_instrumentals_path = stems.get("no_vocals")
                    _clear_cuda_cache()
                    log_service.suno(f"[Lane 2] [{job.track_id[:8]}] Complete -> Lane 3")
                    assert lane3_queue is not None
                    await lane3_queue.put(job)
                else:
                    log_service.error(f"[Lane 2] [{job.track_id[:8]}] Failed -> Skip to Lane 4")
                    assert lane4_queue is not None
                    await lane4_queue.put(job)

                lane2_queue.task_done()
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log_service.error(f"[Lane 2] Error: {str(e)}")
                lane2_queue.task_done()
                await asyncio.sleep(0.05)

    async def _lane3_consumer(self):
        assert self.lane3_queue is not None
        lane3_queue = self.lane3_queue
        lane4_queue = self.lane4_queue
        log_service.suno("Lane 3 (ClearVoice) consumer started")
        while self.running:
            job: Optional[TrackJob] = None
            try:
                job = await lane3_queue.get()
                assert job is not None
                log_service.suno(f"[Lane 3] [{job.track_id[:8]}] ClearVoice processing...")
                if job.progress_callback:
                    await job.progress_callback("ClearVoice")

                if self.clearvoice is None:
                    raise RuntimeError("clearvoice service not initialized")
                if job.demucs_vocals_path is None:
                    raise ValueError("demucs_vocals_path is required for ClearVoice processing")
                enhanced_vocals_path = settings.DEMUCS_STEMS_DIR / job.track_id / "vocals_enhanced.wav"
                enhanced_vocals = await self.clearvoice.enhance_vocals(
                    job.demucs_vocals_path,
                    enhanced_vocals_path
                )

                if enhanced_vocals:
                    job.clearvoice_complete = True

                    vocals_audio, vocals_sr = torchaudio.load(str(enhanced_vocals))
                    instrumentals_audio, inst_sr = torchaudio.load(str(job.demucs_instrumentals_path))
                    min_length = min(vocals_audio.shape[1], instrumentals_audio.shape[1])
                    mixed_audio = vocals_audio[:, :min_length] + instrumentals_audio[:, :min_length]

                    enhanced_path = settings.VOCAL_ENHANCED_WAV_DIR / f"{job.track_id}.wav"
                    torchaudio.save(str(enhanced_path), mixed_audio, vocals_sr)

                    job.clearvoice_enhanced_path = enhanced_path
                    _clear_cuda_cache()
                    log_service.suno(f"[Lane 3] [{job.track_id[:8]}] Complete -> Lane 4")
                else:
                    log_service.error(f"[Lane 3] [{job.track_id[:8]}] Failed -> Skip to Lane 4")
                    job.clearvoice_enhanced_path = job.apollo_wav_path

                assert lane4_queue is not None
                await lane4_queue.put(job)
                lane3_queue.task_done()
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log_service.error(f"[Lane 3] Error: {str(e)}")
                if job is not None:
                    job.clearvoice_enhanced_path = job.apollo_wav_path
                    assert lane4_queue is not None
                    await lane4_queue.put(job)
                lane3_queue.task_done()
                await asyncio.sleep(0.05)

    async def _lane4_consumer(self):
        assert self.lane4_queue is not None
        lane4_queue = self.lane4_queue
        lane5_queue = self.lane5_queue
        lane6_queue = self.lane6_queue
        log_service.suno("Lane 4 (SonicMaster) consumer started")
        while self.running:
            try:
                job = await lane4_queue.get()
                assert job is not None
                log_service.suno(f"[Lane 4] [{job.track_id[:8]}] SonicMaster processing...")
                if job.progress_callback:
                    await job.progress_callback("SonicMaster")

                if self.sonic_master is None:
                    raise RuntimeError("sonic_master service not initialized")
                input_path = job.clearvoice_enhanced_path or job.apollo_wav_path
                if input_path is None:
                    raise ValueError("input_path is required for SonicMaster processing")
                sonic_wav_path = settings.SONIC_WAV_DIR / f"{job.track_id}.wav"

                success = await self.sonic_master.enhance_audio(input_path, sonic_wav_path)

                if success:
                    job.sonic_complete = True
                    job.sonic_wav_path = sonic_wav_path
                    _clear_cuda_cache()
                    log_service.suno(f"[Lane 4] [{job.track_id[:8]}] Complete -> Lane 5 & 6")
                    assert lane5_queue is not None
                    assert lane6_queue is not None
                    await lane5_queue.put(job)
                    await lane6_queue.put(job)
                else:
                    log_service.error(f"[Lane 4] [{job.track_id[:8]}] SonicMaster FAILED")

                lane4_queue.task_done()
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log_service.error(f"[Lane 4] Error: {str(e)}")
                lane4_queue.task_done()
                await asyncio.sleep(0.05)

    async def _lane5_consumer(self):
        assert self.lane5_queue is not None
        lane5_queue = self.lane5_queue
        log_service.suno("Lane 5 (Whisper) consumer started")
        while self.running:
            try:
                job = await lane5_queue.get()
                assert job is not None
                log_service.suno(f"[Lane 5] [{job.track_id[:8]}] Whisper processing...")
                if job.progress_callback:
                    await job.progress_callback("Lyrics")

                if self.lyrics is None:
                    raise RuntimeError("lyrics service not initialized")
                try:
                    result = await self.lyrics.generate_timestamps(
                        track_id=job.track_id,
                        audio_path=job.mp3_path,
                        metadata=job.metadata,
                        save=True
                    )
                    if result:
                        job.lyrics_complete = True
                    else:
                        log_service.suno(f"[Lane 5] [{job.track_id[:8]}] No lyrics detected")
                        job.lyrics_complete = True
                except Exception as e:
                    log_service.error(f"[Lane 5] [{job.track_id[:8]}] Whisper error: {str(e)}")
                    job.lyrics_complete = True

                _clear_cuda_cache()
                lane5_queue.task_done()
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log_service.error(f"[Lane 5] Error: {str(e)}")
                lane5_queue.task_done()
                await asyncio.sleep(0.05)

    async def _lane6_consumer(self):
        assert self.lane6_queue is not None
        assert self.cpu_semaphore is not None
        lane6_queue = self.lane6_queue
        log_service.suno(f"Lane 6 (CPU Pool) consumer started with {self.cpu_semaphore._value} workers")
        while self.running:
            try:
                job = await lane6_queue.get()
                assert job is not None
                asyncio.create_task(self._process_lane6_job(job, lane6_queue))
            except asyncio.CancelledError:
                break

    async def _process_lane6_job(self, job: TrackJob, lane6_queue: asyncio.Queue):
        if self.cpu_semaphore is None:
            raise RuntimeError("cpu_semaphore not initialized")
        try:
            async with self.cpu_semaphore:
                log_service.suno(f"[Lane 6] [{job.track_id[:8]}] Processing...")

                tasks = []
                if not job.mastering_complete:
                    tasks.append(self._lane6_mastering(job))

                tasks.extend([
                    self._lane6_audio_features(job),
                    self._lane6_transcoding(job),
                    self._lane6_artwork(job)
                ])

                results = await asyncio.gather(*tasks, return_exceptions=True)
                for res in results:
                    if isinstance(res, Exception):
                        log_service.error(f"[Lane 6] [{job.track_id[:8]}] Task failed: {str(res)}")

                if job.is_complete():
                    log_service.suno(f"[Lane 6] [{job.track_id[:8]}] Complete -> Catalog")
                    await self._register_to_catalog(job)
                else:
                    log_service.warning(f"[Lane 6] [{job.track_id[:8]}] Incomplete")
        except Exception as e:
            log_service.error(f"[Lane 6] [{job.track_id[:8]}] Error: {str(e)}")
        finally:
            lane6_queue.task_done()

    async def _lane6_mastering(self, job: TrackJob):
        try:
            if self.master is None:
                raise RuntimeError("master service not initialized")
            if job.sonic_wav_path is None:
                raise ValueError("sonic_wav_path is required for mastering")
            if job.progress_callback:
                await job.progress_callback("Mastering")
            final_path = settings.ENHANCED_WAV_DIR / f"{job.track_id}.wav"
            result = await self.master.master_audio(job.sonic_wav_path, final_path, target_lufs=-14.0)
            if result:
                job.mastering_complete = True
                job.master_wav_path = result
        except Exception as e:
            log_service.error(f"[Lane 6 → Mastering] [{job.track_id[:8]}] Error: {str(e)}")
            raise

    async def _lane6_audio_features(self, job: TrackJob):
        try:
            if self.features is None:
                raise RuntimeError("features service not initialized")
            while not job.mastering_complete:
                await asyncio.sleep(0.1)
            if job.features_complete:
                return
            if job.master_wav_path is None:
                raise ValueError("master_wav_path is required for audio features analysis")
            if job.progress_callback:
                await job.progress_callback("AudioFeatures")
            result = await self.features.analyze_audio(job.master_wav_path, job.track_id, save=True)
            if result:
                job.features_complete = True
        except Exception as e:
            log_service.error(f"[Lane 6 → Features] [{job.track_id[:8]}] Error: {str(e)}")
            raise

    async def _lane6_transcoding(self, job: TrackJob):
        try:
            if self.transcoding is None:
                raise RuntimeError("transcoding service not initialized")
            while not job.mastering_complete:
                await asyncio.sleep(0.1)
            if job.transcoding_complete:
                return
            if job.progress_callback:
                await job.progress_callback("Transcoding")
            results = await self.transcoding.batch_create_webm_variants(
                track_ids=[job.track_id], bitrates=['128k', '192k', '256k']
            )
            if results.get(job.track_id) and sum(results[job.track_id].values()) == 3:
                job.transcoding_complete = True
        except Exception as e:
            log_service.error(f"[Lane 6 → Transcoding] [{job.track_id[:8]}] Error: {str(e)}")
            raise

    async def _lane6_artwork(self, job: TrackJob):
        try:
            if self.suno is None:
                raise RuntimeError("suno service not initialized")
            if self.artwork_enrichment is None:
                raise RuntimeError("artwork_enrichment service not initialized")
            if job.artwork_complete:
                return
            if job.progress_callback:
                await job.progress_callback("Artwork")
            artwork_path = settings.ARTWORK_DIR / f"{job.track_id}.jpeg"

            if not artwork_path.exists():
                image_url = job.metadata.get("track_info", {}).get("image_url")
                if not image_url:
                    job.artwork_complete = True
                    return
                if not await self.suno.download_image(image_url, artwork_path):
                    log_service.warning(f"[Lane 6 → Artwork] [{job.track_id[:8]}] Artwork download failed (URL may be expired), skipping")
                    job.artwork_complete = True
                    return

            try:
                await self.artwork_enrichment.enrich_artwork(job.track_id)
                log_service.success(f"[Lane 6 → Artwork] [{job.track_id[:8]}] Enriched with depth map")
            except Exception as enrich_error:
                log_service.warning(f"[Lane 6 → Artwork] [{job.track_id[:8]}] Enrichment failed: {str(enrich_error)}")

            job.artwork_complete = True

        except Exception as e:
            log_service.error(f"[Lane 6 → Artwork] [{job.track_id[:8]}] Error: {str(e)}")
            raise

    async def _register_to_catalog(self, job: TrackJob):
        try:
            if self.transcoding is None:
                raise RuntimeError("transcoding service not initialized")
            if self.catalog is None:
                raise RuntimeError("catalog service not initialized")
            required_files = [
                job.master_wav_path,
                settings.AUDIOFEATURES_DIR / f"{job.track_id}.json",
                settings.LYRIC_TIMESTAMPS_DIR / f"{job.track_id}.json",
                settings.ARTWORK_DIR / f"{job.track_id}.jpeg",
                self.transcoding.get_webm_path(job.track_id, "128k"),
            ]
            if not all(f.exists() for f in required_files):
                log_service.error(f"[Catalog] [{job.track_id[:8]}] Missing files")
                return
            await self.catalog.reload_catalog()
            log_service.suno(f"[Catalog] [{job.track_id[:8]}] ✓ Registered")
        except Exception as e:
            log_service.error(f"[Catalog] [{job.track_id[:8]}] Registration error: {str(e)}")