import asyncio
import hashlib
import shutil
import aiofiles
import aiofiles.os
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Callable, Coroutine
from datetime import datetime, timezone
import json
import librosa
import numpy as np
import torchaudio

from services import log_service
from services.base_service import SingletonService
from config import settings

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
    elif hasattr(obj, 'item'):  # Generic numpy scalar
        return obj.item()
    return obj

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

        self.supported_formats = {'.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.opus', '.webm'}
        self.max_file_size = 100 * 1024 * 1024

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

    def _get_user_tracks_dir(self, user_id: int) -> Path:
        path = settings.USERS_DIR / str(user_id) / "tracks"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _generate_track_id(self, user_id: int, _filename: str, audio_bytes: bytes) -> str:
        timestamp = int(datetime.now(timezone.utc).timestamp())
        content_hash = hashlib.md5(audio_bytes[:4096]).hexdigest()[:8]
        return f"user_{user_id}_{timestamp}_{content_hash}"

    def validate_upload(self, filename: str, file_size: int) -> Tuple[bool, str]:
        suffix = Path(filename).suffix.lower()
        if suffix not in self.supported_formats:
            return False, f"Unsupported format: {suffix}. Supported: {', '.join(self.supported_formats)}"

        if file_size > self.max_file_size:
            max_mb = self.max_file_size / (1024 * 1024)
            return False, f"File too large. Maximum size: {max_mb:.0f}MB"

        if file_size < 1024:
            return False, "File too small to be valid audio"

        return True, ""

    async def get_audio_duration(self, audio_path: Path) -> int:
        try:
            duration = await asyncio.to_thread(
                librosa.get_duration,
                path=str(audio_path)
            )
            return int(duration * 1000)
        except Exception as e:
            log_service.error(f"Failed to get audio duration: {e}")
            return 0

    async def process_upload(
        self,
        user_id: int,
        filename: str,
        audio_bytes: bytes,
        user_title: Optional[str] = None,
        user_artist: Optional[str] = None,
        enable_upscaling: bool = True,
        progress_callback: Optional[Callable[[str, float], Coroutine]] = None
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:

        completed_stages = 0
        total_stages = 1

        async def advance(stage: str):
            nonlocal completed_stages
            completed_stages += 1
            pct = min(int(completed_stages / total_stages * 100), 99)
            if progress_callback:
                result = progress_callback(stage, pct)
                if asyncio.iscoroutine(result):
                    await result
            log_service.info(f"[Upload] {stage}: {pct}%")

        is_valid, error = self.validate_upload(filename, len(audio_bytes))
        if not is_valid:
            return False, error, None

        total_stages = 5
        await advance("Validating")

        track_id = self._generate_track_id(user_id, filename, audio_bytes)
        user_tracks_dir = self._get_user_tracks_dir(user_id)
        original_suffix = Path(filename).suffix.lower()

        original_path = user_tracks_dir / f"{track_id}_original{original_suffix}"
        try:
            async with aiofiles.open(original_path, 'wb') as f:
                await f.write(audio_bytes)
            log_service.info(f"[Upload] Saved original: {original_path.name}")
        except Exception as e:
            return False, f"Failed to save file: {e}", None

        await advance("Saved original")

        duration_ms = await self.get_audio_duration(original_path)
        if duration_ms == 0:
            return False, "Could not read audio file - may be corrupted", None

        if duration_ms < 30000:
            await self._cleanup_failed_upload(original_path)
            return False, "Track too short. Minimum length: 30 seconds", None

        if duration_ms > 900000:
            await self._cleanup_failed_upload(original_path)
            return False, "Track too long. Maximum length: 15 minutes", None

        await advance("Validated audio")

        quality_report = None
        if self.quality_analysis_service:
            quality_report = await self.quality_analysis_service.analyze(original_path)

            if quality_report:
                log_service.info(
                    f"[Upload] Quality: {quality_report.quality_tier.upper()}, "
                    f"Bandwidth: {quality_report.bandwidth_utilization:.0%}, "
                    f"Lossless: {quality_report.is_lossless}"
                )

        await advance("Analyzing source quality")

        if not self.metadata_extraction_service:
            return False, "Metadata extraction service not available", None

        await advance("Extracting metadata with Gemini")

        analysis_path = original_path
        temp_mp3_path = None
        file_size_mb = original_path.stat().st_size / (1024 * 1024)

        if file_size_mb > 15 and self.transcoding_service and self.transcoding_service.ffmpeg_available:
            temp_mp3_path = original_path.parent / f"{track_id}_gemini_temp.mp3"
            log_service.info(f"[Upload] File is {file_size_mb:.1f}MB, converting to MP3 for AI analysis...")

            convert_success = await self.transcoding_service.convert_to_mp3(
                input_path=original_path,
                output_path=temp_mp3_path,
                bitrate="128k"
            )

            if convert_success:
                analysis_path = temp_mp3_path
            else:
                log_service.warning("[Upload] MP3 conversion failed, trying with original file")

        extracted_metadata, extraction_error = await self.metadata_extraction_service.extract_metadata(
            audio_path=analysis_path,
            user_provided_title=user_title,
            user_provided_artist=user_artist
        )

        if temp_mp3_path and temp_mp3_path.exists():
            try:
                await aiofiles.os.remove(temp_mp3_path)
            except Exception:
                pass

        if not extracted_metadata:
            await self._cleanup_failed_upload(original_path)
            error_msg = extraction_error or "Failed to analyze audio - please try again"
            return False, error_msg, None

        catalog_metadata = self.metadata_extraction_service.format_as_catalog_metadata(
            extracted=extracted_metadata,
            track_id=track_id,
            user_id=user_id,
            duration_ms=duration_ms,
            original_filename=filename
        )

        if quality_report:
            catalog_metadata["source_quality"] = quality_report.to_dict()

        should_apply_apollo = enable_upscaling
        if quality_report and self.quality_analysis_service:
            should_apply_apollo = self.quality_analysis_service.should_apply_processing(
                quality_report, "apollo"
            )

        enhance_vocals = catalog_metadata.get("enhance_vocals", False)
        is_instrumental = catalog_metadata.get("generation_params", {}).get("instrumental", False)
        will_enhance_vocals = enhance_vocals and not is_instrumental and self.demucs_service and self.clearvoice_service

        sonic_prompt = catalog_metadata.get("sonic_master_prompt")
        sonic_blend = catalog_metadata.get("sonic_master_blend", 0)
        will_sonic = sonic_prompt and sonic_blend > 0 and self.sonic_master_service and getattr(self.sonic_master_service, 'sonic_loaded', False)

        will_lyrics = self.lyric_timestamp_service and not is_instrumental

        visual_stages = 1
        if self.artwork_generation_service:
            visual_stages += 1
        if self.artwork_enrichment_service:
            visual_stages += 1

        audio_stages = 0
        if should_apply_apollo and self.apollo_service and getattr(self.apollo_service, 'apollo_loaded', False):
            audio_stages += 1
        if will_enhance_vocals:
            audio_stages += 2
        if will_sonic:
            audio_stages += 1
        if self.master_service:
            audio_stages += 1
        if self.features_service:
            audio_stages += 1
        if will_lyrics:
            audio_stages += 1
        if self.transcoding_service and getattr(self.transcoding_service, 'ffmpeg_available', False):
            audio_stages += 3

        post_stages = 3
        total_stages = completed_stages + visual_stages + audio_stages + post_stages

        pipeline_results = {
            "has_artwork": False,
            "artwork_path": settings.ARTWORK_DIR / f"{track_id}.jpeg",
            "transcode_source": original_path,
            "master_wav_path": settings.ENHANCED_WAV_DIR / f"{track_id}.wav"
        }

        async def run_visual_pipeline():
            nonlocal catalog_metadata
            has_artwork = False
            artwork_path = pipeline_results["artwork_path"]

            await advance("Extracting embedded artwork")
            if self.embedded_artwork_service:
                has_artwork, artwork_path = await self.embedded_artwork_service.extract_and_save_for_track(
                    audio_path=original_path,
                    track_id=track_id
                )
                if has_artwork:
                    log_service.success(f"[Upload/Visual] Embedded artwork extracted: {track_id}")
                else:
                    log_service.info("[Upload/Visual] No embedded artwork found")

            if not has_artwork and self.artwork_generation_service:
                await advance("Generating artwork")
                try:
                    generated_path = await self.artwork_generation_service.generate_artwork_for_track(
                        track_id=track_id,
                        metadata={
                            "title": catalog_metadata.get("generation_params", {}).get("title", "Untitled"),
                            "primary_artist": catalog_metadata.get("track_info", {}).get("artist", "Unknown"),
                            "primary_genre": catalog_metadata.get("derived_tags", {}).get("primary_genre"),
                            "mood": (catalog_metadata.get("derived_tags", {}).get("mood_keywords") or [""])[0] if catalog_metadata.get("derived_tags", {}).get("mood_keywords") else None,
                            "style": (catalog_metadata.get("derived_tags", {}).get("style_keywords") or [""])[0] if catalog_metadata.get("derived_tags", {}).get("style_keywords") else None,
                            "artwork_prompt": catalog_metadata.get("artwork_prompt"),
                        }
                    )
                    if generated_path and generated_path.exists():
                        has_artwork = True
                        artwork_path = generated_path
                        catalog_metadata["artwork_generated"] = True
                        log_service.success(f"[Upload/Visual] AI artwork generated: {track_id}")
                except Exception as e:
                    log_service.warning(f"[Upload/Visual] Artwork generation failed (optional): {e}")

            if has_artwork and self.artwork_enrichment_service:
                await advance("Generating depth map")
                try:
                    enriched_path = await self.artwork_enrichment_service.enrich_artwork(track_id)
                    if enriched_path:
                        catalog_metadata["artwork_enriched"] = True
                        log_service.success("[Upload/Visual] Artwork enriched with depth map")
                except Exception as e:
                    log_service.warning(f"[Upload/Visual] Artwork enrichment failed: {e}")

            pipeline_results["has_artwork"] = has_artwork
            pipeline_results["artwork_path"] = artwork_path
            return has_artwork

        async def run_audio_pipeline():
            nonlocal catalog_metadata
            transcode_source = original_path
            enhanced_wav_path = None
            sonic_wav_path = None
            master_wav_path = pipeline_results["master_wav_path"]

            if should_apply_apollo and self.apollo_service and self.apollo_service.apollo_loaded:
                await advance("Restoring audio bandwidth")
                enhanced_path = user_tracks_dir / f"{track_id}_enhanced.wav"
                try:
                    result_path, stats = await self.apollo_service.process_audio(
                        input_path=original_path,
                        output_path=enhanced_path
                    )
                    if result_path and result_path.exists():
                        transcode_source = result_path
                        enhanced_wav_path = result_path
                        catalog_metadata["enhancement_applied"] = True
                        catalog_metadata["enhancement_stats"] = stats
                        log_service.success("[Upload/Audio] Apollo enhancement applied")
                except Exception as e:
                    log_service.warning(f"[Upload/Audio] Apollo enhancement failed, using original: {e}")

            if will_enhance_vocals:
                await advance("Separating stems")
                log_service.info("[Upload/Audio] Vocal enhancement requested by Gemini")

                try:
                    demucs_input = enhanced_wav_path or transcode_source
                    stems_dir = user_tracks_dir / f"{track_id}_stems"
                    stems_dir.mkdir(parents=True, exist_ok=True)

                    stems = await self.demucs_service.separate_stems(demucs_input, stems_dir)

                    if stems and stems.get("vocals") and stems.get("no_vocals"):
                        vocals_path = stems["vocals"]
                        instrumentals_path = stems["no_vocals"]
                        log_service.success("[Upload/Audio] Demucs separation complete")

                        await advance("Cleaning vocals")
                        enhanced_vocals_path = stems_dir / "vocals_enhanced.wav"
                        enhanced_vocals = await self.clearvoice_service.enhance_vocals(
                            vocals_path,
                            enhanced_vocals_path
                        )

                        if enhanced_vocals and enhanced_vocals.exists():
                            log_service.success("[Upload/Audio] ClearVoice enhancement complete")

                            vocals_audio, vocals_sr = torchaudio.load(str(enhanced_vocals))
                            instrumentals_audio, _inst_sr = torchaudio.load(str(instrumentals_path))

                            min_length = min(vocals_audio.shape[1], instrumentals_audio.shape[1])
                            mixed_audio = vocals_audio[:, :min_length] + instrumentals_audio[:, :min_length]

                            vocal_enhanced_path = user_tracks_dir / f"{track_id}_vocal_enhanced.wav"
                            torchaudio.save(str(vocal_enhanced_path), mixed_audio, vocals_sr)

                            transcode_source = vocal_enhanced_path
                            catalog_metadata["vocal_enhancement_applied"] = True
                            log_service.success("[Upload/Audio] Vocals enhanced and remixed")
                        else:
                            log_service.warning("[Upload/Audio] ClearVoice failed, continuing without vocal enhancement")
                    else:
                        log_service.warning("[Upload/Audio] Demucs separation failed, continuing without vocal enhancement")
                except Exception as e:
                    log_service.warning(f"[Upload/Audio] Vocal enhancement failed: {e}")

            if will_sonic:
                await advance("Enhancing mix with SonicMaster")
                wet_mix = sonic_blend / 100.0
                log_service.info(f"[Upload/Audio] SonicMaster prompt: '{sonic_prompt}' @ {sonic_blend}% blend")

                self.sonic_master_service.configure(wet_mix=wet_mix, prompt=sonic_prompt)
                sonic_wav_path = user_tracks_dir / f"{track_id}_sonic.wav"
                sonic_input = enhanced_wav_path or transcode_source

                try:
                    success = await self.sonic_master_service.enhance_audio(
                        input_path=sonic_input,
                        output_path=sonic_wav_path
                    )
                    if success and sonic_wav_path.exists():
                        transcode_source = sonic_wav_path
                        catalog_metadata["sonic_master_applied"] = True
                        catalog_metadata["sonic_master_blend_used"] = sonic_blend
                        log_service.success(f"[Upload/Audio] SonicMaster enhancement applied @ {sonic_blend}% blend")
                except Exception as e:
                    log_service.warning(f"[Upload/Audio] SonicMaster failed, continuing without: {e}")

            mastering_blend = catalog_metadata.get("mastering_blend", 70)
            if self.master_service:
                await advance("Mastering")
                master_source = sonic_wav_path if sonic_wav_path and sonic_wav_path.exists() else (enhanced_wav_path or transcode_source)

                master_wet_mix = mastering_blend / 100.0
                self.master_service.configure(master_wet_mix=master_wet_mix)
                log_service.info(f"[Upload/Audio] Mastering @ {mastering_blend}% EQ blend + LUFS normalization")

                try:
                    result = await self.master_service.master_audio(
                        input_path=master_source,
                        output_path=master_wav_path,
                        target_lufs=-14.0
                    )
                    if result:
                        transcode_source = master_wav_path
                        catalog_metadata["mastering_applied"] = True
                        catalog_metadata["mastering_blend_used"] = mastering_blend
                        log_service.success(f"[Upload/Audio] Mastered to -14.0 LUFS @ {mastering_blend}% EQ blend")
                except Exception as e:
                    log_service.warning(f"[Upload/Audio] Mastering failed, using previous source: {e}")
                finally:
                    self.master_service.configure(master_wet_mix=0.75)

            if self.features_service:
                await advance("Extracting audio features")
                try:
                    features_source = master_wav_path if master_wav_path.exists() else transcode_source
                    features = await self.features_service.analyze_audio(
                        audio_path=features_source,
                        track_id=track_id,
                        save=True
                    )
                    if features:
                        catalog_metadata["audio_features_extracted"] = True
                        log_service.success(
                            f"[Upload/Audio] Features extracted: "
                            f"tempo={features.get('tempo', 'N/A'):.1f}bpm"
                        )
                except Exception as e:
                    log_service.warning(f"[Upload/Audio] Audio features extraction failed: {e}")

            if will_lyrics:
                await advance("Extracting lyric timestamps")
                try:
                    lyrics_source = master_wav_path if master_wav_path and master_wav_path.exists() else original_path
                    timestamps = await self.lyric_timestamp_service.generate_timestamps(
                        track_id=track_id,
                        metadata=catalog_metadata,
                        audio_path=lyrics_source,
                        save=True
                    )
                    if timestamps:
                        catalog_metadata["lyrics_timestamps_extracted"] = True
                        log_service.success("[Upload/Audio] Lyric timestamps extracted")
                except Exception as e:
                    log_service.warning(f"[Upload/Audio] Lyric timestamp extraction failed: {e}")

            if self.transcoding_service and self.transcoding_service.ffmpeg_available:
                for bitrate in ["128k", "192k", "256k"]:
                    await advance(f"Transcoding ({bitrate})")
                    opus_path = self.transcoding_service.get_opus_path(track_id, bitrate)
                    success = await self.transcoding_service.transcode_to_opus(
                        input_path=transcode_source,
                        output_path=opus_path,
                        bitrate=bitrate
                    )
                    if success:
                        log_service.info(f"[Upload/Audio] Transcoded to Opus {bitrate}")
            else:
                log_service.warning("[Upload/Audio] Transcoding service not available")

            pipeline_results["transcode_source"] = transcode_source
            pipeline_results["master_wav_path"] = master_wav_path
            return True

        log_service.info("[Upload] Starting parallel processing (visual + audio pipelines)")
        await asyncio.gather(
            run_visual_pipeline(),
            run_audio_pipeline()
        )
        log_service.success("[Upload] Parallel processing complete")

        has_artwork = pipeline_results["has_artwork"]
        master_wav_path = pipeline_results["master_wav_path"]

        clean_metadata = sanitize_for_json(catalog_metadata)

        metadata_path = settings.METADATA_DIR / f"{track_id}.json"
        try:
            async with aiofiles.open(metadata_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(clean_metadata, indent=2, ensure_ascii=False))
            log_service.info(f"[Upload] Saved metadata: {metadata_path.name}")
        except Exception as e:
            log_service.error(f"[Upload] Failed to save metadata: {e}")
            return False, f"Failed to save metadata: {e}", None

        await advance("Saving metadata")

        catalog_audio_path = settings.AUDIO_DIR / f"{track_id}{original_suffix}"
        try:
            shutil.copy2(original_path, catalog_audio_path)
        except Exception as e:
            log_service.warning(f"[Upload] Could not copy to catalog audio dir: {e}")

        if self.catalog_db_service:
            try:
                has_wav = master_wav_path.exists() if master_wav_path else False
                await asyncio.to_thread(
                    self.catalog_db_service._upsert_track_to_db,
                    track_id,
                    clean_metadata,
                    True,
                    has_wav,
                    has_artwork
                )
                self.catalog_db_service.tracks[track_id] = clean_metadata
                if track_id not in self.catalog_db_service.track_ids:
                    self.catalog_db_service.track_ids.insert(0, track_id)
                log_service.info(f"[Upload] Added to catalog database: {track_id}")
            except Exception as e:
                log_service.error(f"[Upload] Failed to add to catalog database: {e}")

        await advance("Updating database")

        if self.vector_db_service:
            try:
                await asyncio.to_thread(
                    self.vector_db_service.add_single_track,
                    clean_metadata
                )
                log_service.info(f"[Upload] Added to vector index: {track_id}")
            except Exception as e:
                log_service.warning(f"[Upload] Failed to add to vector index: {e}")

        await advance("Updating search index")

        title = clean_metadata.get("generation_params", {}).get("title", "Untitled")
        genre = clean_metadata.get("derived_tags", {}).get("primary_genre", "Unknown")
        quality_tier = quality_report.quality_tier if quality_report else "unknown"

        return True, f"Successfully uploaded: {title} ({genre}) [Quality: {quality_tier}]", clean_metadata

    async def _cleanup_failed_upload(self, *paths: Path):
        for path in paths:
            try:
                if path.exists():
                    await aiofiles.os.remove(path)
            except Exception as e:
                log_service.warning(f"Failed to cleanup {path}: {e}")

    async def get_user_tracks(self, user_id: int, skip: int = 0, limit: int = 50) -> list:
        if not self.catalog_db_service:
            return []

        user_tracks = []
        for _track_id, metadata in self.catalog_db_service.tracks.items():
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

        if track_id in self.catalog_db_service.tracks:
            del self.catalog_db_service.tracks[track_id]
        if track_id in self.catalog_db_service.track_ids:
            self.catalog_db_service.track_ids.remove(track_id)

        files_to_remove = [
            settings.METADATA_DIR / f"{track_id}.json",

            settings.AUDIO_DIR / f"{track_id}.mp3",
            settings.AUDIO_DIR / f"{track_id}.wav",
            settings.AUDIO_DIR / f"{track_id}.flac",
            settings.AUDIO_DIR / f"{track_id}.ogg",
            settings.AUDIO_DIR / f"{track_id}.m4a",
            settings.AUDIO_DIR / f"{track_id}.aac",
            settings.AUDIO_DIR / f"{track_id}.opus",

            settings.ENHANCED_WAV_DIR / f"{track_id}.wav",

            settings.AUDIOFEATURES_DIR / f"{track_id}.json",

            settings.ARTWORK_DIR / f"{track_id}.jpeg",
            settings.ARTWORK_DIR / f"{track_id}.jpg",
            settings.ARTWORK_ENRICHED_DIR / f"{track_id}.jpeg",
        ]

        for bitrate in ["128k", "192k", "256k"]:
            if self.transcoding_service:
                files_to_remove.append(
                    self.transcoding_service.get_opus_path(track_id, bitrate)
                )
                webm_path = self.transcoding_service.get_webm_path(track_id, bitrate)
                files_to_remove.append(webm_path)

        user_tracks_dir = self._get_user_tracks_dir(user_id)
        for pattern in [f"{track_id}*"]:
            files_to_remove.extend(user_tracks_dir.glob(pattern))

        deleted_count = 0
        for file_path in files_to_remove:
            try:
                if file_path.exists():
                    await aiofiles.os.remove(file_path)
                    deleted_count += 1
            except Exception as e:
                log_service.warning(f"Failed to delete {file_path}: {e}")

        log_service.info(f"[Upload] Deleted {deleted_count} files for track: {track_id}")
        return True, "Track deleted successfully"

    async def update_track_metadata(
        self,
        user_id: int,
        track_id: str,
        updates: Dict[str, Any]
    ) -> Tuple[bool, str]:

        if not self.catalog_db_service:
            return False, "Catalog service not available"

        track = self.catalog_db_service.tracks.get(track_id)
        if not track:
            return False, "Track not found"

        if track.get("uploaded_by_user_id") != user_id:
            return False, "You can only edit your own uploads"

        allowed_updates = {
            "title": ["generation_params", "title"],
            "artist": ["track_info", "artist"],
            "primary_genre": ["derived_tags", "primary_genre"],
            "secondary_genres": ["derived_tags", "secondary_genres"],
            "mood_keywords": ["derived_tags", "mood_keywords"],
            "similar_artists": ["derived_tags", "similar_artists"],
        }

        for key, value in updates.items():
            if key in allowed_updates:
                path = allowed_updates[key]
                if len(path) == 2:
                    if path[0] not in track:
                        track[path[0]] = {}
                    track[path[0]][path[1]] = value

        if "title" in updates:
            if "track_info" not in track:
                track["track_info"] = {}
            track["track_info"]["title"] = updates["title"]

        track["updated_at"] = datetime.now(timezone.utc).isoformat()

        metadata_path = settings.METADATA_DIR / f"{track_id}.json"
        try:
            async with aiofiles.open(metadata_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(track, indent=2, ensure_ascii=False))
        except Exception as e:
            return False, f"Failed to save updates: {e}"

        self.catalog_db_service.tracks[track_id] = track

        try:
            await asyncio.to_thread(
                self.catalog_db_service._upsert_track_to_db,
                track_id,
                track,
                True, False, False
            )
        except Exception as e:
            log_service.warning(f"Failed to update database: {e}")

        return True, "Track updated successfully"