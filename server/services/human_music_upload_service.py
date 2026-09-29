import asyncio
import hashlib
import math
import re
import shutil
import traceback
import uuid
import aiofiles
import aiofiles.os
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Callable, List
from datetime import datetime, timezone
import json
import librosa
import numpy as np
import soundfile as sf

from services import log_service
from services import usage_tracking
from services import track_asset_stages as stages
from services.asset_integrity_service import asset_integrity_service
from services.track_asset_stages import coerce_bool as _coerce_bool
from services.base_service import SingletonService
from services.audio_headroom import mix_stems_to_file
from services.audio_master_service import MASTER_TARGET_LUFS
from config import settings
from models_global import GPUOutOfMemoryError

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

class HumanMusicUploadService(SingletonService):

    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.metadata_extraction_service = None
        self.transcoding_service = None
        self.apollo_service = None
        self.catalog_db_service = None
        self.vector_db_service = None
        self.quality_analysis_service = None
        self.embedded_artwork_service = None
        self.master_service = None
        self.features_service = None
        self.artwork_enrichment_service = None
        self.artwork_generation_service = None
        self.lyric_timestamp_service = None
        self.sonic_master_service = None
        self.demucs_service = None
        self.clearvoice_service = None

        self.audio_formats = {'.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.opus', '.webm'}
        self.video_formats = {'.mp4', '.mov', '.m4v', '.mkv', '.avi'}
        self.supported_formats = self.audio_formats | self.video_formats
        self.max_audio_file_size = settings.UPLOAD_MAX_AUDIO_BYTES
        self.max_video_file_size = settings.UPLOAD_MAX_VIDEO_BYTES
        self.min_track_duration_ms = 30 * 1000
        self.max_track_duration_ms = settings.UPLOAD_MAX_DURATION_S * 1000
        self.metadata_preview_duration_seconds = 15 * 60

        self._inflight_uploads = set()
        self._service_initialized = False
        self._initialized = True

    async def initialize(
        self,
        metadata_extraction_service=None,
        transcoding_service=None,
        apollo_service=None,
        catalog_db_service=None,
        vector_db_service=None,
        quality_analysis_service=None,
        embedded_artwork_service=None,
        master_service=None,
        features_service=None,
        artwork_enrichment_service=None,
        artwork_generation_service=None,
        lyric_timestamp_service=None,
        sonic_master_service=None,
        demucs_service=None,
        clearvoice_service=None
    ):
        if self._service_initialized:
            log_service.info("HumanMusicUploadService already initialized")
            return

        self.metadata_extraction_service = metadata_extraction_service
        self.transcoding_service = transcoding_service
        self.apollo_service = apollo_service
        self.catalog_db_service = catalog_db_service
        self.vector_db_service = vector_db_service
        self.quality_analysis_service = quality_analysis_service
        self.embedded_artwork_service = embedded_artwork_service
        self.master_service = master_service
        self.features_service = features_service
        self.artwork_enrichment_service = artwork_enrichment_service
        self.artwork_generation_service = artwork_generation_service
        self.lyric_timestamp_service = lyric_timestamp_service
        self.sonic_master_service = sonic_master_service
        self.demucs_service = demucs_service
        self.clearvoice_service = clearvoice_service

        self._service_initialized = True
        log_service.info("✓ HumanMusicUploadService initialized (full pipeline enabled)")

    def attach_services(self, apollo_service=None, demucs_service=None, clearvoice_service=None, vector_db_service=None):
        if apollo_service is not None:
            self.apollo_service = apollo_service
        if demucs_service is not None:
            self.demucs_service = demucs_service
        if clearvoice_service is not None:
            self.clearvoice_service = clearvoice_service
        if vector_db_service is not None:
            self.vector_db_service = vector_db_service
        log_service.info(
            f"HumanMusicUploadService lanes: apollo={bool(self.apollo_service)}, "
            f"demucs={bool(self.demucs_service)}, clearvoice={bool(self.clearvoice_service)}, "
            f"vector_index={bool(self.vector_db_service)}"
        )

    def _get_user_tracks_dir(self, user_id: int) -> Path:
        path = settings.USERS_DIR / str(user_id) / "tracks"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _generate_track_id(self, user_id: int, content_sha256: str) -> str:
        timestamp = int(datetime.now(timezone.utc).timestamp())
        return f"user_{user_id}_{timestamp}_{content_sha256[:8]}_{uuid.uuid4().hex[:8]}"

    def validate_upload(self, filename: str, file_size: int, content_type: Optional[str] = None) -> Tuple[bool, str]:
        suffix = Path(filename).suffix.lower()
        if suffix not in self.supported_formats:
            return False, f"Unsupported format: {suffix}. Supported: {', '.join(sorted(self.supported_formats))}"

        max_file_size = self.max_upload_size_for(filename)
        if file_size > max_file_size:
            max_mb = max_file_size / (1024 * 1024)
            return False, f"File too large. Maximum size: {max_mb:.0f}MB"

        if file_size < 1024:
            return False, "File too small to be valid media"

        return True, ""

    def _is_video_upload(self, filename: str, content_type: Optional[str] = None) -> bool:
        return Path(filename).suffix.lower() in self.video_formats

    def max_upload_size_for(self, filename: str) -> int:
        return self.max_video_file_size if self._is_video_upload(filename) else self.max_audio_file_size

    async def get_audio_duration(self, audio_path: Path) -> int:
        try:
            info = await asyncio.to_thread(sf.info, str(audio_path))
            return int(info.duration * 1000)
        except Exception:
            pass
        try:
            duration = await asyncio.to_thread(librosa.get_duration, path=str(audio_path))
            return int(duration * 1000)
        except Exception as e:
            log_service.error(f"Failed to get audio duration: {e}")
            return 0

    def _format_max_duration(self) -> str:
        minutes = self.max_track_duration_ms / 60000
        return f"{minutes:.0f} minutes" if minutes >= 1 else f"{self.max_track_duration_ms // 1000} seconds"

    def _apollo_ready(self) -> bool:
        return bool(self.apollo_service and getattr(self.apollo_service, 'apollo_loaded', False))

    def _vocal_lane_ready(self) -> bool:
        return bool(
            self.demucs_service and getattr(self.demucs_service, 'demucs_loaded', False)
            and self.clearvoice_service and getattr(self.clearvoice_service, 'models_loaded', False)
        )

    def _sonic_ready(self) -> bool:
        if not self.sonic_master_service:
            return False
        return bool(
            getattr(self.sonic_master_service, 'sonic_loaded', False)
            or getattr(self.sonic_master_service, 'sonic_available', False)
        )

    def _plan_stages(self) -> List[Tuple[str, str, float]]:
        plan: List[Tuple[str, str, float]] = [
            ("validate", "Validating", 1),
            ("save_original", "Saved original", 1),
            ("decode", "Decoding audio", 3),
            ("quality", "Analyzing source quality", 2),
            ("metadata", "Extracting metadata with Gemini", 15),
        ]
        if self._apollo_ready():
            plan.append(("apollo", "Restoring audio bandwidth", 12))
        if self._vocal_lane_ready():
            plan.append(("stems", "Separating stems", 10))
            plan.append(("vocals", "Cleaning vocals", 8))
        if self._sonic_ready():
            plan.append(("sonic", "Enhancing mix with SonicMaster", 12))
        plan.append(("master", "Mastering", 5))
        if self.features_service:
            plan.append(("features", "Extracting audio features", 3))
        if self.lyric_timestamp_service:
            plan.append(("lyrics", "Extracting lyric timestamps", 6))
        for bitrate in stages.OPUS_BITRATES:
            plan.append((f"transcode_{bitrate}", f"Transcoding ({bitrate})", 2))
        plan.append(("catalog_mp3", "Creating catalog MP3", 2))
        plan.append(("artwork_embedded", "Extracting embedded artwork", 1))
        if self.artwork_generation_service:
            plan.append(("artwork_generate", "Generating artwork", 8))
        if self.artwork_enrichment_service:
            plan.append(("depth", "Generating depth map", 3))
        plan.append(("save_metadata", "Saving metadata", 1))
        plan.append(("catalog", "Updating database", 1))
        plan.append(("index", "Updating search index", 1))
        return plan

    def _find_existing_upload(self, user_id: int, content_sha256: str) -> Optional[Dict[str, Any]]:
        if not self.catalog_db_service:
            return None
        for metadata in list(self.catalog_db_service.tracks.values()):
            if metadata.get("uploaded_by_user_id") == user_id and metadata.get("source_sha256") == content_sha256:
                return metadata
        return None

    @staticmethod
    def _intermediate_paths(user_tracks_dir: Path, track_id: str) -> List[Path]:
        return [
            user_tracks_dir / f"{track_id}_source.wav",
            user_tracks_dir / f"{track_id}_source.wav.tmp",
            user_tracks_dir / f"{track_id}_enhanced.wav",
            user_tracks_dir / f"{track_id}_stems",
            user_tracks_dir / f"{track_id}_vocal_enhanced.wav",
            user_tracks_dir / f"{track_id}_sonic.wav",
            user_tracks_dir / f"{track_id}_gemini_temp.mp3",
        ]

    @staticmethod
    def _catalog_output_paths(track_id: str) -> List[Path]:
        return stages.catalog_output_paths(track_id)

    @staticmethod
    async def _remove_paths(paths: List[Path]) -> int:
        removed = 0
        for path in paths:
            try:
                if path.is_dir():
                    await asyncio.to_thread(shutil.rmtree, path, True)
                    removed += 1
                elif path.exists():
                    await aiofiles.os.remove(path)
                    removed += 1
            except Exception as e:
                log_service.warning(f"Failed to remove {path}: {e}")
        return removed

    async def _rollback_track(self, user_id: int, track_id: str, catalog_added: bool):
        if catalog_added and self.catalog_db_service:
            try:
                await asyncio.to_thread(self.catalog_db_service._delete_track_from_db, track_id)
            except Exception as e:
                log_service.warning(f"[Upload] Failed to remove {track_id} from database during rollback: {e}")
            self.catalog_db_service.remove_track_from_memory(track_id)

        user_tracks_dir = self._get_user_tracks_dir(user_id)
        paths = list(await asyncio.to_thread(lambda: list(user_tracks_dir.glob(f"{track_id}*"))))
        paths.extend(self._catalog_output_paths(track_id))
        removed = await self._remove_paths(paths)
        log_service.upload(f"[Upload] Rolled back {track_id} ({removed} files removed)")

    async def process_upload(
        self,
        user_id: int,
        filename: str,
        audio_bytes: Optional[bytes] = None,
        user_title: Optional[str] = None,
        artist: Optional[Dict[str, Any]] = None,
        content_type: Optional[str] = None,
        upload_path: Optional[Path] = None,
        file_size: Optional[int] = None,
        enable_upscaling: bool = True,
        progress_callback: Optional[Callable[[Dict[str, Any]], Any]] = None,
        content_sha256: Optional[str] = None,
        upload_id: Optional[str] = None
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:

        usage_tracking.bind_session(user_id=user_id)
        upload_id = upload_id if upload_id and UPLOAD_ID_PATTERN.match(upload_id) else uuid.uuid4().hex
        progress = _UploadProgress(upload_id, progress_callback, self._plan_stages())
        job: Dict[str, Any] = {"track_id": None, "catalog_added": False, "succeeded": False}
        dedupe_key = None
        result: Tuple[bool, str, Optional[Dict[str, Any]]] = (False, "Upload failed", None)

        try:
            if upload_path is None and audio_bytes is None:
                raise UploadFailed("No upload data provided")

            if content_sha256 is None:
                content_sha256 = await self._hash_upload(upload_path, audio_bytes)

            dedupe_key = (user_id, content_sha256)
            if dedupe_key in self._inflight_uploads:
                dedupe_key = None
                raise UploadFailed("This file is already being processed")
            self._inflight_uploads.add(dedupe_key)

            existing = self._find_existing_upload(user_id, content_sha256)
            if existing:
                title = existing.get("generation_params", {}).get("title", "Untitled")
                log_service.upload(f"[Upload] Duplicate upload for user {user_id}: returning {existing.get('id')}")
                job["succeeded"] = True
                job["track_id"] = existing.get("id")
                result = (True, f"Already uploaded: {title}", {**existing, "duplicate_upload": True})
            else:
                result = await self._run_pipeline(
                    job=job,
                    progress=progress,
                    user_id=user_id,
                    filename=filename,
                    audio_bytes=audio_bytes,
                    upload_path=upload_path,
                    file_size=file_size,
                    content_sha256=content_sha256,
                    user_title=user_title,
                    artist=artist,
                    enable_upscaling=enable_upscaling,
                    upload_id=upload_id
                )
                job["succeeded"] = result[0]
                if job["succeeded"] and job["track_id"]:
                    asset_integrity_service.notify_tracks_changed([job["track_id"]], "upload")
        except UploadFailed as e:
            result = (False, str(e), None)
        except GPUOutOfMemoryError as e:
            log_service.error(f"[Upload] GPU out of memory: {e}")
            result = (False, GPU_BUSY_MESSAGE, None)
        except Exception as e:
            log_service.error(f"[Upload] Unexpected failure: {e}\n{traceback.format_exc()}")
            result = (False, "Upload processing failed unexpectedly - please try again", None)
        finally:
            if not job["succeeded"] and job["track_id"]:
                await self._rollback_track(user_id, job["track_id"], job["catalog_added"])
            if dedupe_key is not None:
                self._inflight_uploads.discard(dedupe_key)
            await progress.finish(job["succeeded"], result[1], job["track_id"] if job["succeeded"] else None)

        return result

    @staticmethod
    async def _hash_upload(upload_path: Optional[Path], audio_bytes: Optional[bytes]) -> str:
        if upload_path is None:
            return hashlib.sha256(audio_bytes or b"").hexdigest()

        def hash_file() -> str:
            digest = hashlib.sha256()
            with open(upload_path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()

        return await asyncio.to_thread(hash_file)

    async def _run_pipeline(
        self,
        job: Dict[str, Any],
        progress: _UploadProgress,
        user_id: int,
        filename: str,
        audio_bytes: Optional[bytes],
        upload_path: Optional[Path],
        file_size: Optional[int],
        content_sha256: str,
        user_title: Optional[str],
        artist: Optional[Dict[str, Any]],
        enable_upscaling: bool,
        upload_id: str
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:

        await progress.stage("validate")

        upload_size = file_size if file_size is not None else len(audio_bytes or b"")
        is_valid, error = self.validate_upload(filename, upload_size)
        if not is_valid:
            raise UploadFailed(error)

        if not self.transcoding_service or not self.transcoding_service.ffmpeg_available:
            raise UploadFailed("Uploads are temporarily unavailable (audio decoder offline)")

        if not self.master_service:
            raise UploadFailed("Uploads are temporarily unavailable (mastering offline)")

        if not self.metadata_extraction_service:
            raise UploadFailed("Metadata extraction service not available")

        track_id = self._generate_track_id(user_id, content_sha256)
        job["track_id"] = track_id
        user_tracks_dir = self._get_user_tracks_dir(user_id)
        original_suffix = Path(filename).suffix.lower()
        original_path = user_tracks_dir / f"{track_id}_original{original_suffix}"

        try:
            if upload_path:
                await asyncio.to_thread(shutil.move, str(upload_path), original_path)
            else:
                async with aiofiles.open(original_path, 'wb') as f:
                    await f.write(audio_bytes or b"")
            log_service.upload(f"[Upload] Saved original: {original_path.name}")
        except Exception as e:
            raise UploadFailed(f"Failed to save file: {e}")

        await progress.stage("save_original")

        probe = await self.transcoding_service.probe_media(original_path)
        if probe is not None and not probe.get("has_audio"):
            raise UploadFailed("No audio track found - make sure the file contains audio")

        is_video_upload = original_suffix in self.video_formats or bool(probe and probe.get("has_video"))

        probe_duration_s = probe.get("duration_s") if probe else None
        if probe_duration_s and probe_duration_s * 1000 > self.max_track_duration_ms:
            raise UploadFailed(f"Track too long. Maximum length: {self._format_max_duration()}")

        await progress.stage("decode", "Extracting audio from video" if is_video_upload else "Decoding audio")

        source_audio_path = user_tracks_dir / f"{track_id}_source.wav"
        native_rate = (probe or {}).get("sample_rate") or 44100
        decode_rate = "48000" if native_rate > 44100 else "44100"
        decode_codec = "pcm_f32le"
        decoded = await self.transcoding_service.extract_audio_to_wav(
            input_path=original_path,
            output_path=source_audio_path,
            sample_rate=decode_rate,
            codec=decode_codec
        )
        if not decoded:
            if is_video_upload:
                raise UploadFailed("Could not extract audio from video - make sure it contains an audio track")
            raise UploadFailed("Could not decode audio file - it may be corrupted or use an unsupported codec")

        duration_ms = await self.get_audio_duration(source_audio_path)
        if duration_ms == 0:
            raise UploadFailed("Could not read audio file - may be corrupted")

        if duration_ms < self.min_track_duration_ms:
            raise UploadFailed("Track too short. Minimum length: 30 seconds")

        if duration_ms > self.max_track_duration_ms:
            raise UploadFailed(f"Track too long. Maximum length: {self._format_max_duration()}")

        await progress.stage("quality")

        quality_report = None
        if self.quality_analysis_service:
            quality_source = source_audio_path if is_video_upload else original_path
            quality_report = await self.quality_analysis_service.analyze(quality_source)
            if quality_report:
                log_service.upload(
                    f"[Upload] Quality: {quality_report.quality_tier.upper()}, "
                    f"Bandwidth: {quality_report.bandwidth_utilization:.0%}, "
                    f"Lossless: {quality_report.is_lossless}"
                )

        await progress.stage("metadata")

        catalog_metadata = await self._extract_catalog_metadata(
            track_id=track_id,
            user_id=user_id,
            filename=filename,
            original_path=original_path,
            source_audio_path=source_audio_path,
            duration_ms=duration_ms,
            user_title=user_title,
            artist=artist
        )

        catalog_metadata["source_sha256"] = content_sha256
        catalog_metadata["upload_id"] = upload_id
        if quality_report:
            catalog_metadata["source_quality"] = quality_report.to_dict()

        if is_video_upload:
            catalog_metadata["source_media_type"] = "video"
            catalog_metadata["source_video_filename"] = filename
            catalog_metadata["audio_extracted_from_video"] = True
            catalog_metadata["artwork_generation_deferred"] = True
        else:
            catalog_metadata["source_media_type"] = "audio"

        should_apply_apollo = enable_upscaling
        if enable_upscaling and quality_report and self.quality_analysis_service:
            should_apply_apollo = self.quality_analysis_service.should_apply_processing(quality_report, "apollo")
        catalog_metadata["enhance_requested"] = enable_upscaling

        master_wav_path = settings.ENHANCED_WAV_DIR / f"{track_id}.wav"

        transcode_source = await self._run_audio_lane(
            progress=progress,
            catalog_metadata=catalog_metadata,
            track_id=track_id,
            user_tracks_dir=user_tracks_dir,
            source_audio_path=source_audio_path,
            master_wav_path=master_wav_path,
            should_apply_apollo=should_apply_apollo
        )

        has_artwork = await self._run_visual_lane(
            progress=progress,
            catalog_metadata=catalog_metadata,
            track_id=track_id,
            original_path=original_path,
            generate_visual_artwork=not is_video_upload
        )

        await progress.stage("save_metadata")

        clean_metadata = sanitize_for_json(catalog_metadata)
        metadata_path = settings.METADATA_DIR / f"{track_id}.json"
        try:
            async with aiofiles.open(metadata_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(clean_metadata, indent=2, ensure_ascii=False))
            log_service.upload(f"[Upload] Saved metadata: {metadata_path.name}")
        except Exception as e:
            raise UploadFailed(f"Failed to save metadata: {e}")

        await progress.stage("catalog")

        if self.catalog_db_service:
            try:
                await asyncio.to_thread(
                    self.catalog_db_service._upsert_track_to_db,
                    track_id,
                    clean_metadata,
                    True,
                    master_wav_path.exists(),
                    has_artwork
                )
                job["catalog_added"] = True
                self.catalog_db_service.add_track_to_memory(track_id, clean_metadata, has_artwork=has_artwork)
                log_service.upload(f"[Upload] Added to catalog database: {track_id}")
            except Exception as e:
                log_service.error(f"[Upload] Failed to add to catalog database: {e}")
                raise UploadFailed("Failed to add track to the catalog - please try again")

        await progress.stage("index")

        if self.vector_db_service:
            try:
                await asyncio.to_thread(self.vector_db_service.add_single_track, clean_metadata)
                log_service.upload(f"[Upload] Added to vector index: {track_id}")
            except Exception as e:
                log_service.warning(f"[Upload] Failed to add to vector index (will be picked up by next rebuild): {e}")

        removed = await self._remove_paths(
            [p for p in self._intermediate_paths(user_tracks_dir, track_id) if p != transcode_source]
        )
        if removed:
            log_service.upload(f"[Upload] Cleaned up {removed} intermediate files")

        title = clean_metadata.get("generation_params", {}).get("title", "Untitled")
        genre = clean_metadata.get("derived_tags", {}).get("primary_genre", "Unknown")
        quality_tier = quality_report.quality_tier if quality_report else "unknown"

        return True, f"Successfully uploaded: {title} ({genre}) [Quality: {quality_tier}]", clean_metadata

    async def _extract_catalog_metadata(
        self,
        track_id: str,
        user_id: int,
        filename: str,
        original_path: Path,
        source_audio_path: Path,
        duration_ms: int,
        user_title: Optional[str],
        artist: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        tags = await asyncio.to_thread(read_embedded_tags, original_path)
        analysis_path = source_audio_path
        temp_mp3_path = None
        file_size_mb = source_audio_path.stat().st_size / (1024 * 1024)
        source_is_float = await asyncio.to_thread(_is_float_wav, source_audio_path)

        if file_size_mb > 15 or source_is_float:
            temp_mp3_path = original_path.parent / f"{track_id}_gemini_temp.mp3"
            is_long_form = duration_ms > 15 * 60 * 1000
            preview_note = (
                f"first {self.metadata_preview_duration_seconds // 60} minutes"
                if is_long_form
                else "full track"
            )
            log_service.upload(
                f"[Upload] File is {file_size_mb:.1f}MB, converting {preview_note} "
                "to MP3 for AI analysis..."
            )

            convert_success = await self.transcoding_service.convert_to_mp3(
                input_path=source_audio_path,
                output_path=temp_mp3_path,
                bitrate="64k" if is_long_form else "128k",
                max_duration_seconds=self.metadata_preview_duration_seconds if is_long_form else None
            )

            if convert_success:
                analysis_path = temp_mp3_path
            else:
                log_service.warning("[Upload] MP3 conversion failed, trying with decoded WAV")

        try:
            extracted_metadata, extraction_error = await self.metadata_extraction_service.extract_metadata(
                audio_path=analysis_path,
                user_provided_title=user_title,
                user_provided_artist=(artist or {}).get("name"),
                filename=filename,
                tags=tags
            )
        finally:
            if temp_mp3_path is not None:
                await self._remove_paths([temp_mp3_path])

        if not extracted_metadata:
            raise UploadFailed(extraction_error or "Failed to analyze audio - please try again")

        catalog_metadata = self.metadata_extraction_service.format_as_catalog_metadata(
            extracted=extracted_metadata,
            track_id=track_id,
            user_id=user_id,
            duration_ms=duration_ms,
            original_filename=filename,
            artist=artist,
            tags=tags
        )

        sonic_prompt = _coerce_prompt(catalog_metadata.get("sonic_master_prompt"))
        requested_sonic_blend = _coerce_percent(catalog_metadata.get("sonic_master_blend"), 0)
        sonic_blend = min(requested_sonic_blend, SONIC_BLEND_MAX)
        if not sonic_prompt or sonic_blend < SONIC_BLEND_MIN_EFFECTIVE:
            sonic_blend = 0

        catalog_metadata["mastering_blend"] = _coerce_percent(catalog_metadata.get("mastering_blend"), DEFAULT_MASTERING_BLEND)
        catalog_metadata["sonic_master_blend_requested"] = requested_sonic_blend
        catalog_metadata["sonic_master_blend"] = sonic_blend
        catalog_metadata["sonic_master_prompt"] = sonic_prompt
        catalog_metadata["enhance_vocals"] = _coerce_bool(catalog_metadata.get("enhance_vocals"))
        return catalog_metadata

    async def _measure_loudness(self, audio_path: Path) -> Optional[float]:
        try:
            loudness = await asyncio.to_thread(self.master_service._analyze_loudness_sync, audio_path)
        except Exception as e:
            log_service.warning(f"[Upload/Audio] Could not analyze loudness of {audio_path.name}: {e}")
            return None
        if loudness is None or not math.isfinite(loudness):
            return None
        return float(loudness)

    async def _run_audio_lane(
        self,
        progress: _UploadProgress,
        catalog_metadata: Dict[str, Any],
        track_id: str,
        user_tracks_dir: Path,
        source_audio_path: Path,
        master_wav_path: Path,
        should_apply_apollo: bool
    ) -> Path:
        transcode_source = source_audio_path
        is_instrumental = _coerce_bool(catalog_metadata.get("generation_params", {}).get("instrumental", False))

        source_loudness = await self._measure_loudness(source_audio_path)
        if source_loudness is not None:
            catalog_metadata["source_loudness_lufs"] = source_loudness
            sonic_requested = catalog_metadata.get("sonic_master_blend", 0)
            if source_loudness >= LOUD_SOURCE_LUFS and sonic_requested > LOUD_SOURCE_SONIC_BLEND_MAX:
                catalog_metadata["sonic_master_blend"] = LOUD_SOURCE_SONIC_BLEND_MAX
                catalog_metadata["sonic_master_blend_limited_reason"] = (
                    f"Source already loud at {source_loudness:.1f} LUFS"
                )

        if should_apply_apollo and self._apollo_ready():
            await progress.stage("apollo")
            enhanced_path = user_tracks_dir / f"{track_id}_enhanced.wav"
            try:
                result_path = await self.apollo_service.process_audio(
                    input_path=transcode_source,
                    output_path=enhanced_path
                )
                if result_path and Path(result_path).exists():
                    transcode_source = Path(result_path)
                    catalog_metadata["enhancement_applied"] = True
                    log_service.success("[Upload/Audio] Apollo enhancement applied")
                else:
                    log_service.warning("[Upload/Audio] Apollo enhancement produced no output, using original")
            except GPUOutOfMemoryError:
                raise
            except Exception as e:
                log_service.warning(f"[Upload/Audio] Apollo enhancement failed, using original: {e}")

        if catalog_metadata.get("enhance_vocals") and not is_instrumental and self._vocal_lane_ready():
            await progress.stage("stems")
            log_service.upload("[Upload/Audio] Vocal enhancement requested by Gemini")
            stems_dir = user_tracks_dir / f"{track_id}_stems"
            try:
                stems_dir.mkdir(parents=True, exist_ok=True)
                stems = await self.demucs_service.separate_stems(transcode_source, stems_dir)

                if stems and stems.get("vocals") and stems.get("no_vocals"):
                    log_service.success("[Upload/Audio] Demucs separation complete")

                    await progress.stage("vocals")
                    enhanced_vocals = await self.clearvoice_service.enhance_vocals(
                        stems["vocals"],
                        stems_dir / "vocals_enhanced.wav"
                    )

                    if enhanced_vocals and enhanced_vocals.exists():
                        vocal_enhanced_path = user_tracks_dir / f"{track_id}_vocal_enhanced.wav"
                        await asyncio.to_thread(
                            mix_stems_to_file, enhanced_vocals, stems["no_vocals"], vocal_enhanced_path
                        )
                        transcode_source = vocal_enhanced_path
                        catalog_metadata["vocal_enhancement_applied"] = True
                        log_service.success("[Upload/Audio] Vocals enhanced and remixed")
                    else:
                        log_service.warning("[Upload/Audio] ClearVoice failed, continuing without vocal enhancement")
                else:
                    log_service.warning("[Upload/Audio] Demucs separation failed, continuing without vocal enhancement")
            except GPUOutOfMemoryError:
                raise
            except Exception as e:
                log_service.warning(f"[Upload/Audio] Vocal enhancement failed: {e}")
            finally:
                await self._remove_paths([stems_dir])

        sonic_prompt = catalog_metadata.get("sonic_master_prompt")
        sonic_blend = catalog_metadata.get("sonic_master_blend", 0)
        if sonic_prompt and sonic_blend > 0 and self._sonic_ready():
            await progress.stage("sonic")
            log_service.upload(f"[Upload/Audio] SonicMaster prompt: '{sonic_prompt}' @ {sonic_blend}% blend")
            sonic_wav_path = user_tracks_dir / f"{track_id}_sonic.wav"
            try:
                success = await self.sonic_master_service.enhance_audio(
                    transcode_source,
                    sonic_wav_path,
                    prompt=sonic_prompt,
                    wet_mix=sonic_blend / 100.0
                )
                if success and sonic_wav_path.exists():
                    transcode_source = sonic_wav_path
                    catalog_metadata["sonic_master_applied"] = True
                    catalog_metadata["sonic_master_blend_used"] = sonic_blend
                    log_service.success(f"[Upload/Audio] SonicMaster enhancement applied @ {sonic_blend}% blend")
            except GPUOutOfMemoryError:
                raise
            except Exception as e:
                log_service.warning(f"[Upload/Audio] SonicMaster failed, continuing without: {e}")

        await progress.stage("master")
        await self._master(catalog_metadata, transcode_source, master_wav_path)
        transcode_source = master_wav_path

        if self.features_service:
            await progress.stage("features")
            try:
                features = await stages.extract_audio_features(self.features_service, track_id, master_wav_path)
                if features:
                    catalog_metadata["audio_features_extracted"] = True
                    tempo = features.get('tempo')
                    tempo_text = f"{tempo:.1f}bpm" if isinstance(tempo, (int, float)) else "N/A"
                    log_service.success(f"[Upload/Audio] Features extracted: tempo={tempo_text}")
            except Exception as e:
                log_service.warning(f"[Upload/Audio] Audio features extraction failed: {e}")

        if self.lyric_timestamp_service:
            if not is_instrumental:
                await progress.stage("lyrics")
            try:
                timestamps = await stages.generate_lyric_timestamps(
                    self.lyric_timestamp_service, track_id, catalog_metadata, master_wav_path
                )
                if timestamps and not is_instrumental:
                    catalog_metadata["lyrics_timestamps_extracted"] = True
                    log_service.success("[Upload/Audio] Lyric timestamps extracted")
            except GPUOutOfMemoryError:
                raise
            except Exception as e:
                log_service.warning(f"[Upload/Audio] Lyric timestamp extraction failed: {e}")

        for bitrate in stages.OPUS_BITRATES:
            await progress.stage(f"transcode_{bitrate}")
            if await stages.create_opus_variant(self.transcoding_service, track_id, bitrate, master_wav_path):
                log_service.upload(f"[Upload/Audio] Transcoded to Opus {bitrate}")
            else:
                log_service.warning(f"[Upload/Audio] Opus {bitrate} transcode failed (will be created on demand)")

        await progress.stage("catalog_mp3")
        if not await stages.create_catalog_mp3(self.transcoding_service, track_id, master_wav_path):
            raise UploadFailed("Could not create the streaming copy of your track - please try again")

        return transcode_source

    async def _master(self, catalog_metadata: Dict[str, Any], master_source: Path, master_wav_path: Path):
        requested_mastering_blend = catalog_metadata.get("mastering_blend", DEFAULT_MASTERING_BLEND)
        mastering_blend = requested_mastering_blend

        source_loudness = await self._measure_loudness(master_source)

        if source_loudness is not None and source_loudness >= LOUD_SOURCE_LUFS:
            mastering_blend = min(mastering_blend, LOUD_SOURCE_MASTERING_BLEND_MAX)
            catalog_metadata["mastering_blend_limited_reason"] = (
                f"Pre-master source already loud at {source_loudness:.1f} LUFS"
            )

        target_lufs = MASTER_TARGET_LUFS

        log_service.upload(
            f"[Upload/Audio] Mastering @ {mastering_blend}% EQ blend "
            f"(requested {requested_mastering_blend}%) + target {target_lufs:.1f} LUFS"
        )

        info: Dict[str, Any] = {}
        try:
            result, info = await self.master_service.master_audio_with_report(
                input_path=master_source,
                output_path=master_wav_path,
                target_lufs=target_lufs,
                wet_mix=mastering_blend / 100.0
            )
        except Exception as e:
            log_service.error(f"[Upload/Audio] Mastering raised: {e}")
            result = None

        if not result or not master_wav_path.exists():
            raise UploadFailed("Mastering failed - your track could not be processed. Please try again or upload a different file.")

        limiter = info.get("limiter") or {}
        output_lufs = info.get("output_loudness")
        catalog_metadata["mastering_applied"] = True
        catalog_metadata["mastering_blend_requested"] = requested_mastering_blend
        catalog_metadata["mastering_blend_used"] = mastering_blend
        catalog_metadata["mastering_target_lufs"] = target_lufs
        catalog_metadata["mastering_output_lufs"] = output_lufs
        catalog_metadata["mastering_true_peak_dbtp"] = info.get("output_true_peak_db")
        catalog_metadata["mastering_limiter_max_db"] = limiter.get("max_reduction_db", 0.0)
        catalog_metadata["mastering_loudness_trim_db"] = info.get("loudness_trim_db", 0.0)
        if source_loudness is not None:
            catalog_metadata["pre_master_loudness_lufs"] = float(source_loudness)
        output_text = f"{output_lufs:.1f}" if isinstance(output_lufs, float) and math.isfinite(output_lufs) else "?"
        log_service.success(
            f"[Upload/Audio] Mastered to {output_text} LUFS (target {target_lufs:.1f}) @ {mastering_blend}% EQ blend, "
            f"true peak {info.get('output_true_peak_db', 0.0):.2f} dBTP"
        )

    async def _run_visual_lane(
        self,
        progress: _UploadProgress,
        catalog_metadata: Dict[str, Any],
        track_id: str,
        original_path: Path,
        generate_visual_artwork: bool
    ) -> bool:
        await progress.stage("artwork_embedded")
        has_artwork, _source = await stages.ensure_track_artwork(
            track_id=track_id,
            metadata=catalog_metadata,
            embedded_artwork_service=self.embedded_artwork_service,
            original_path=original_path,
            allow_generation=False
        )
        if has_artwork:
            log_service.success(f"[Upload/Visual] Embedded artwork extracted: {track_id}")
        else:
            log_service.upload("[Upload/Visual] No embedded artwork found")

        if not has_artwork and not generate_visual_artwork:
            log_service.upload("[Upload/Visual] Video upload: artwork generation deferred to the asset doctor")

        if not has_artwork and generate_visual_artwork and self.artwork_generation_service:
            await progress.stage("artwork_generate")
            has_artwork, _source = await stages.ensure_track_artwork(
                track_id=track_id,
                metadata=catalog_metadata,
                artwork_generation_service=self.artwork_generation_service
            )
            if has_artwork:
                catalog_metadata["artwork_generated"] = True
                log_service.success(f"[Upload/Visual] AI artwork generated: {track_id}")

        if has_artwork and self.artwork_enrichment_service:
            await progress.stage("depth")
            try:
                if await stages.enrich_track_artwork(self.artwork_enrichment_service, track_id):
                    catalog_metadata["artwork_enriched"] = True
                    log_service.success("[Upload/Visual] Artwork enriched with depth map")
            except GPUOutOfMemoryError:
                raise
            except Exception as e:
                log_service.warning(f"[Upload/Visual] Artwork enrichment failed: {e}")

        return has_artwork

    async def _cleanup_failed_upload(self, *paths: Path):
        await self._remove_paths(list(paths))

    async def get_user_tracks(self, user_id: int, skip: int = 0, limit: int = 50) -> list:
        if not self.catalog_db_service:
            return []

        user_tracks = []
        for _track_id, metadata in list(self.catalog_db_service.tracks.items()):
            if metadata.get("uploaded_by_user_id") == user_id:
                user_tracks.append(metadata)

        user_tracks.sort(key=lambda t: t.get("created_at", ""), reverse=True)

        return user_tracks[skip:skip + limit]

    async def delete_user_track(self, user_id: int, track_id: str) -> Tuple[bool, str]:

        if not self.catalog_db_service:
            return False, "Catalog service not available"

        track = self.catalog_db_service.tracks.get(track_id)
        if not track:
            return False, "Track not found"

        if track.get("uploaded_by_user_id") != user_id:
            return False, "You can only delete your own uploads"

        try:
            await asyncio.to_thread(
                self.catalog_db_service._delete_track_from_db,
                track_id
            )
        except Exception as e:
            log_service.error(f"Failed to delete from database: {e}")

        self.catalog_db_service.remove_track_from_memory(track_id)

        files_to_remove = self._catalog_output_paths(track_id)
        user_tracks_dir = self._get_user_tracks_dir(user_id)
        files_to_remove.extend(await asyncio.to_thread(lambda: list(user_tracks_dir.glob(f"{track_id}*"))))

        deleted_count = await self._remove_paths(files_to_remove)

        log_service.info(f"[Upload] Deleted {deleted_count} files for track: {track_id}")
        return True, "Track deleted successfully"

    async def update_track_metadata(
        self,
        user_id: int,
        track_id: str,
        updates: Dict[str, Any],
        artist: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str]:

        if not self.catalog_db_service:
            return False, "Catalog service not available"

        current = self.catalog_db_service.tracks.get(track_id)
        if not current:
            return False, "Track not found"

        if current.get("uploaded_by_user_id") != user_id:
            return False, "You can only edit your own uploads"

        track = json.loads(json.dumps(current))
        params = track.setdefault("generation_params", {})
        info = track.setdefault("track_info", {})
        tags = track.setdefault("derived_tags", {})

        if "title" in updates:
            title = " ".join(str(updates["title"] or "").split())[:TITLE_MAX]
            if not title:
                return False, "Title can't be empty"
            params["title"] = info["title"] = title
        if artist is not None:
            params["artist_name"] = info["artist"] = artist["name"]
            track["artist_profile_id"] = artist["id"]
            track["artist_slug"] = artist["slug"]
        if "primary_genre" in updates:
            genre = " ".join(str(updates["primary_genre"] or "").split())[:TAG_MAX]
            if not genre:
                return False, "Genre can't be empty"
            tags["primary_genre"] = genre
        for key in ("secondary_genres", "mood_keywords"):
            if key in updates:
                tags[key] = _clean_tag_list(updates[key])
        if "lyrics" in updates:
            lyrics = str(updates["lyrics"] or "").strip()[:LYRICS_MAX]
            track["transcribed_lyrics"] = lyrics or None
            params["prompt"] = lyrics
            params["instrumental"] = not lyrics
        if "description" in updates:
            track["description"] = str(updates["description"] or "").strip()[:DESCRIPTION_MAX] or None

        track["updated_at"] = datetime.now(timezone.utc).isoformat()

        try:
            await asyncio.to_thread(self.catalog_db_service.write_track_metadata, track_id, track)
        except OSError as e:
            return False, f"Failed to save updates: {e}"
        except Exception as e:
            log_service.warning(f"Failed to update database: {e}")

        self.catalog_db_service.add_track_to_memory(track_id, track)
        if self.vector_db_service:
            try:
                await asyncio.to_thread(self.vector_db_service.add_single_track, track)
            except Exception as e:
                log_service.warning(f"[Upload] Re-indexing {track_id} after edit failed: {e}")
        if "lyrics" in updates:
            await self._remove_paths([settings.LYRIC_TIMESTAMPS_DIR / f"{track_id}.json"])
            asset_integrity_service.notify_tracks_changed([track_id], "lyrics edited")

        log_service.upload(f"[Upload] {log_service.who(user_id=user_id)} edited {track_id}: "
                           f"{', '.join(sorted(set(updates) | ({'artist'} if artist else set())))}")
        return True, "Track updated successfully"

    async def retag_artist(self, profile: Dict[str, Any]) -> int:
        if not self.catalog_db_service:
            return 0
        changed = 0
        for track_id, metadata in list(self.catalog_db_service.tracks.items()):
            if metadata.get("artist_profile_id") != profile["id"]:
                continue
            ok, _message = await self.update_track_metadata(metadata.get("uploaded_by_user_id"), track_id, {},
                                                            artist=profile)
            changed += int(ok)
        return changed
