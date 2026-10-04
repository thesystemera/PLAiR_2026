import asyncio
import hashlib
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
import soundfile as sf

from services import log_service
from services import usage_tracking
from services import track_asset_stages as stages
from services.asset_integrity_service import asset_integrity_service
from services import audio_fingerprint
from services.base_service import SingletonService
from config import settings
from models_global import GPUOutOfMemoryError
from services.human_music_upload_service_lanes import UploadLanes
from services.human_music_upload_service_tracks import UploadedTracks
from services.human_music_upload_service_common import (
    DuplicateSong, GPU_BUSY_MESSAGE, UPLOAD_ID_PATTERN, UploadFailed, _UploadProgress, sanitize_for_json,
)

class HumanMusicUploadService(UploadLanes, UploadedTracks, SingletonService):

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
        self.vocal_enhancer = None

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
        vocal_enhancer=None
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
        self.vocal_enhancer = vocal_enhancer

        self._service_initialized = True
        log_service.info("✓ HumanMusicUploadService initialized (full pipeline enabled)")

    def attach_services(self, apollo_service=None, demucs_service=None, vocal_enhancer=None, vector_db_service=None):
        if apollo_service is not None:
            self.apollo_service = apollo_service
        if demucs_service is not None:
            self.demucs_service = demucs_service
        if vocal_enhancer is not None:
            self.vocal_enhancer = vocal_enhancer
        if vector_db_service is not None:
            self.vector_db_service = vector_db_service
        log_service.info(
            f"HumanMusicUploadService lanes: apollo={bool(self.apollo_service)}, "
            f"demucs={bool(self.demucs_service)}, vocal_enhancer={bool(self.vocal_enhancer)}, "
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
            and self.vocal_enhancer and getattr(self.vocal_enhancer, 'models_loaded', False)
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
        except DuplicateSong as e:
            if job["track_id"]:
                await self._rollback_track(user_id, job["track_id"], job["catalog_added"])
            job["track_id"], job["succeeded"] = e.track.get("id"), True
            title = e.track.get("generation_params", {}).get("title", "Untitled")
            log_service.upload(f"[Upload] Same song as {e.track.get('id')} for user {user_id}: returning it")
            result = (True, f"You've already uploaded this song: {title}", {**e.track, "duplicate_upload": True})
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

    def _same_song(self, song_fingerprint) -> Optional[Dict[str, Any]]:
        if song_fingerprint is None or not self.catalog_db_service:
            return None
        for metadata in list(self.catalog_db_service.tracks.values()):
            other = audio_fingerprint.decode(metadata.get("fingerprint"))
            if other is not None and audio_fingerprint.similarity(song_fingerprint, other) >= audio_fingerprint.SAME_SONG_SIMILARITY:
                return metadata
        return None

    async def backfill_fingerprints(self) -> int:
        if not self.catalog_db_service:
            return 0
        added = 0
        for track_id, metadata in list(self.catalog_db_service.tracks.items()):
            if metadata.get("is_ai_generated") is not False or metadata.get("fingerprint"):
                continue
            audio_path = settings.AUDIO_DIR / f"{track_id}.mp3"
            if not audio_path.exists():
                continue
            song_fingerprint = await asyncio.to_thread(audio_fingerprint.fingerprint, audio_path)
            if song_fingerprint is None:
                continue
            updated = {**metadata, "fingerprint": audio_fingerprint.encode(song_fingerprint)}
            await asyncio.to_thread(self.catalog_db_service.write_track_metadata, track_id, updated)
            self.catalog_db_service.add_track_to_memory(track_id, updated)
            added += 1
        if added:
            log_service.upload(f"[Upload] Fingerprinted {added} existing upload(s) for duplicate detection")
        return added

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

        song_fingerprint = await asyncio.to_thread(audio_fingerprint.fingerprint, source_audio_path)
        match = await asyncio.to_thread(self._same_song, song_fingerprint)
        if match is not None:
            if match.get("uploaded_by_user_id") == user_id:
                raise DuplicateSong(match)
            raise UploadFailed("This song is already on PLAiR, uploaded by another listener. "
                               "If it's yours, contact us and we'll sort it out.")

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
        if song_fingerprint is not None:
            catalog_metadata["fingerprint"] = audio_fingerprint.encode(song_fingerprint)
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

    async def _cleanup_failed_upload(self, *paths: Path):
        await self._remove_paths(list(paths))

