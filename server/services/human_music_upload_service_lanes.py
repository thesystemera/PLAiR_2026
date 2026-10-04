"""Human uploads: the processing lanes - catalog metadata from Gemini, loudness, the audio lane (split, enhance, master)
and the visual lane (artwork)."""
import asyncio
import math
from pathlib import Path
from typing import Dict, Any, Optional
from services import log_service
from services import track_asset_stages as stages
from services.track_asset_stages import coerce_bool as _coerce_bool
from services.audio_headroom import mix_stems_to_file
from services.audio_master_service import MASTER_TARGET_LUFS
from models_global import GPUOutOfMemoryError
from services.human_music_upload_service_common import (
    DEFAULT_MASTERING_BLEND, LOUD_SOURCE_LUFS, LOUD_SOURCE_MASTERING_BLEND_MAX, LOUD_SOURCE_SONIC_BLEND_MAX,
    SONIC_BLEND_MAX, SONIC_BLEND_MIN_EFFECTIVE, UploadFailed, _UploadProgress, _coerce_percent, _coerce_prompt,
    _is_float_wav, read_embedded_tags,
)


class UploadLanes:
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
                    enhanced_vocals = await self.vocal_enhancer.enhance_vocals(
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
                        log_service.warning("[Upload/Audio] Vocal restoration failed, continuing without vocal enhancement")
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
