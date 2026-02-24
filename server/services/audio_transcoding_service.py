import asyncio
import platform
import traceback
import aiofiles.os
from pathlib import Path
from typing import Optional
from services import log_service
from services.base_service import SingletonService
from config import settings

FFMPEG_EXE_PATH = r"C:\ffmpeg\bin\ffmpeg.exe"
FFMPEG_CWD = r"C:\ffmpeg\bin"

class AudioTranscodingService(SingletonService):

    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.ffmpeg_available = False
        self.libopus_available = False
        self.libmp3lame_available = False
        self.is_windows = platform.system() == "Windows"
        self._initialized = True

    async def _windows_safe_rename(self, src: Path, dst: Path, max_retries: int = 5) -> bool:
        for attempt in range(max_retries):
            try:
                await aiofiles.os.rename(src, dst)
                return True
            except FileExistsError as e:
                log_service.error(f"Destination file still exists during rename: {dst.name}")
                if attempt < max_retries - 1:
                    await self._windows_safe_unlink(dst)
                    wait_time = 0.1 * (2 ** attempt)
                    await asyncio.sleep(wait_time)
                else:
                    log_service.error(f"Failed to rename after {max_retries} attempts: {e}")
                    return False
            except PermissionError as e:
                if attempt < max_retries - 1:
                    wait_time = 0.1 * (2 ** attempt)
                    log_service.upscaling(
                        f"File locked, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                else:
                    log_service.error(f"File locked after {max_retries} attempts: {e}")
                    return False
            except Exception as e:
                log_service.error(f"Unexpected error during rename: {e}")
                return False
        return False

    async def _windows_safe_unlink(self, path: Path, max_retries: int = 3) -> bool:
        if not path.exists():
            return True

        for attempt in range(max_retries):
            try:
                await aiofiles.os.remove(path)
                return True
            except PermissionError:
                if attempt < max_retries - 1:
                    wait_time = 0.1 * (2 ** attempt)
                    await asyncio.sleep(wait_time)
                else:
                    log_service.error(f"Failed to delete file after {max_retries} attempts: {path.name}")
                    return False
            except FileNotFoundError:
                return True
            except Exception as e:
                log_service.error(f"Unexpected error during file deletion: {e}")
                return False
        return False

    async def initialize(self):
        if self.ffmpeg_available:
            log_service.upscaling("FFmpeg already initialized")
            return

        ffmpeg_path_obj = Path(FFMPEG_EXE_PATH)
        if not ffmpeg_path_obj.exists():
            log_service.error("=" * 60)
            log_service.error("FFmpeg check failed: The hardcoded file was not found at:")
            log_service.error(f"{FFMPEG_EXE_PATH}")
            log_service.error("=" * 60)
            self.ffmpeg_available = False
            return

        try:
            process = await asyncio.create_subprocess_exec(
                FFMPEG_EXE_PATH, "-version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=FFMPEG_CWD
            )
            _stdout_version, stderr_version = await process.communicate()

            if process.returncode != 0:
                log_service.error(
                    f"FFmpeg found, but '-version' command failed. Return code: {process.returncode}")
                log_service.error(f"FFmpeg stderr: {stderr_version.decode('utf-8', errors='ignore')}")
                return

            self.ffmpeg_available = True
            log_service.upscaling("✓ FFmpeg is available")

            process_opus = await asyncio.create_subprocess_exec(
                FFMPEG_EXE_PATH, "-encoders",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=FFMPEG_CWD
            )
            stdout_encoders, _stderr_encoders = await process_opus.communicate()

            encoders_output = stdout_encoders.decode('utf-8', errors='ignore')

            if "libopus" in encoders_output:
                self.libopus_available = True
                log_service.upscaling("✓ FFmpeg libopus encoder is available")
            else:
                log_service.error("FFmpeg build does not support libopus - Opus transcoding will fail")
                self.ffmpeg_available = False

            if "libmp3lame" in encoders_output:
                self.libmp3lame_available = True
                log_service.upscaling("✓ FFmpeg libmp3lame encoder is available")
            else:
                log_service.warning("FFmpeg build does not support libmp3lame - MP3 conversion will use fallback")
                self.libmp3lame_available = False

        except FileNotFoundError:
            log_service.error("=" * 60)
            log_service.error("FFmpeg check failed: 'FileNotFoundError'.")
            log_service.error("This is strange, as the file was confirmed to exist at:")
            log_service.error(f"{FFMPEG_EXE_PATH}")
            log_service.error("This strongly suggests an Antivirus or permissions issue is blocking Python.")
            log_service.error("=" * 60)
            self.ffmpeg_available = False
        except PermissionError:
            log_service.error("=" * 60)
            log_service.error("FFmpeg check failed: 'PermissionError'.")
            log_service.error("Python was denied permission to execute:")
            log_service.error(f"{FFMPEG_EXE_PATH}")
            log_service.error("This is almost certainly an Antivirus or file permission issue.")
            log_service.error("=" * 60)
            self.ffmpeg_available = False
        except OSError as e:
            log_service.error(f"FFmpeg check failed with OSError: {str(e)}")
            log_service.error(f"FFmpeg check failed. OS ERROR TYPE: {type(e)}")
            log_service.error(f"FFmpeg check failed. FULL OS ERROR: {repr(e)}")
            self.ffmpeg_available = False
        except Exception as e:
            log_service.error(f"FFmpeg check failed. EXCEPTION TYPE: {type(e)}")
            log_service.error(f"FFmpeg check failed. TRACEBACK: {traceback.format_exc()}")
            log_service.error(f"FFmpeg check failed. FULL ERROR: {repr(e)}")
            self.ffmpeg_available = False

    def get_opus_path(self, track_id: str, bitrate: str = "192k") -> Path:
        if bitrate == "256k":
            opus_dir = settings.OPUS_256K_DIR
        elif bitrate == "128k":
            opus_dir = settings.OPUS_128K_DIR
        else:
            opus_dir = settings.OPUS_192K_DIR

        return opus_dir / f"{track_id}.opus"

    def get_webm_path(self, track_id: str, bitrate: str = "192k") -> Path:
        if bitrate == "256k":
            webm_dir = settings.OPUS_256K_DIR / "webm"
        elif bitrate == "128k":
            webm_dir = settings.OPUS_128K_DIR / "webm"
        else:
            webm_dir = settings.OPUS_192K_DIR / "webm"

        webm_dir.mkdir(parents=True, exist_ok=True)
        return webm_dir / f"{track_id}.webm"

    async def transcode_to_opus(
            self,
            input_path: Path,
            output_path: Path,
            bitrate: str = "192k"
    ) -> bool:
        if not self.ffmpeg_available:
            log_service.error("FFmpeg not available for transcoding")
            return False
        if not self.libopus_available:
            log_service.error("libopus encoder not available in this FFmpeg build")
            return False

        if not input_path.exists():
            log_service.error(f"Input file not found: {input_path}")
            return False

        if output_path.exists():
            file_size_mb = output_path.stat().st_size / (1024 * 1024)
            log_service.upscaling(f"Output file already exists: {output_path.name} ({file_size_mb:.2f} MB)")
            return True

        temp_output = output_path.with_suffix(".opus.tmp")

        if temp_output.exists():
            log_service.upscaling(f"Cleaning up orphaned temp file: {temp_output.name}")
            await self._windows_safe_unlink(temp_output)

        try:
            log_service.upscaling(f"Transcoding {input_path.name} to Opus @ {bitrate}")

            process = await asyncio.create_subprocess_exec(
                FFMPEG_EXE_PATH,
                "-threads", "1",
                "-i", str(input_path),
                "-af", "aresample=resampler=soxr:precision=28:out_sample_rate=48000",
                "-ar", "48000",
                "-c:a", "libopus",
                "-b:a", bitrate,
                "-vbr", "on",
                "-compression_level", "10",
                "-application", "audio",
                "-frame_duration", "20",
                "-packet_loss", "0",
                "-f", "opus",
                "-y",
                str(temp_output),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=FFMPEG_CWD
            )

            _stdout, stderr = await process.communicate()
            error_msg = stderr.decode('utf-8', errors='ignore')

            if process.returncode == 0:
                log_service.upscaling("FFmpeg process completed successfully. Attempting to rename file...")

                if output_path.exists():
                    log_service.upscaling("Destination file exists, removing it first...")
                    await self._windows_safe_unlink(output_path)

                rename_success = await self._windows_safe_rename(temp_output, output_path)

                if not rename_success:
                    log_service.error("Failed to rename temp file after retries")
                    await self._windows_safe_unlink(temp_output)
                    return False

                file_size_mb = output_path.stat().st_size / (1024 * 1024)
                log_service.upscaling(f"Transcoding complete: {output_path.name} ({file_size_mb:.2f} MB)")
                return True
            else:
                log_service.error(f"FFmpeg transcoding FAILED. Return code: {process.returncode}")
                log_service.error(f"FFmpeg stderr: {error_msg}")
                await self._windows_safe_unlink(temp_output)
                return False

        except Exception as e:
            log_service.error(f"An unexpected error occurred during transcoding: {str(e)}")
            log_service.error(f"Transcoding FULL ERROR: {repr(e)}")
            await self._windows_safe_unlink(temp_output)
            return False

    async def convert_to_mp3(
            self,
            input_path: Path,
            output_path: Path,
            bitrate: str = "128k"
    ) -> bool:

        if not self.ffmpeg_available:
            log_service.error("FFmpeg not available for MP3 conversion")
            return False

        if not input_path.exists():
            log_service.error(f"Input file not found: {input_path}")
            return False

        if output_path.exists():
            file_size_mb = output_path.stat().st_size / (1024 * 1024)
            log_service.info(f"MP3 already exists: {output_path.name} ({file_size_mb:.2f} MB)")
            return True

        temp_output = output_path.with_suffix(".mp3.tmp")

        if temp_output.exists():
            await self._windows_safe_unlink(temp_output)

        try:
            input_size_mb = input_path.stat().st_size / (1024 * 1024)

            if self.libmp3lame_available:
                encoder = "libmp3lame"
            else:
                encoder = "mp3"
                log_service.info("Using native MP3 encoder (libmp3lame not available)")

            log_service.info(f"Converting {input_path.name} ({input_size_mb:.1f}MB) to MP3 @ {bitrate} using {encoder}")

            cmd_args = [
                FFMPEG_EXE_PATH,
                "-hide_banner",
                "-threads", "1",
                "-i", str(input_path),
                "-vn",
                "-c:a", encoder,
                "-b:a", bitrate,
                "-ar", "44100",
                "-ac", "2",
                "-f", "mp3",
                "-y",
                str(temp_output),
            ]

            log_service.debug(f"FFmpeg command: {' '.join(cmd_args)}")

            process = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=FFMPEG_CWD
            )

            _stdout, stderr = await process.communicate()

            if process.returncode == 0:
                rename_success = await self._windows_safe_rename(temp_output, output_path)

                if not rename_success:
                    log_service.error("Failed to rename temp MP3 file")
                    await self._windows_safe_unlink(temp_output)
                    return False

                file_size_mb = output_path.stat().st_size / (1024 * 1024)
                log_service.info(f"✓ MP3 conversion complete: {output_path.name} ({file_size_mb:.2f} MB)")
                return True
            else:
                error_output = stderr.decode('utf-8', errors='ignore')
                error_lines = []
                for error_line in error_output.split('\n'):
                    line_lower = error_line.lower()
                    if 'error' in line_lower or 'invalid' in line_lower or 'no such' in line_lower or 'unknown' in line_lower:
                        error_lines.append(error_line.strip())
                if error_lines:
                    error_msg = '; '.join(error_lines[:3])
                else:
                    last_lines = [line.strip() for line in error_output.strip().split('\n')[-5:] if line.strip()]
                    error_msg = '; '.join(last_lines)
                log_service.error(f"MP3 conversion failed: {error_msg}")
                await self._windows_safe_unlink(temp_output)
                return False

        except Exception as e:
            log_service.error(f"MP3 conversion error: {e}")
            await self._windows_safe_unlink(temp_output)
            return False

    async def get_or_create_opus(
            self,
            track_id: str,
            wav_path: Optional[Path] = None,
            mp3_path: Optional[Path] = None,
            bitrate: str = "192k"
    ) -> Optional[Path]:
        opus_path = self.get_opus_path(track_id, bitrate=bitrate)

        if opus_path.exists():
            log_service.upscaling(f"✓ Found existing {bitrate} Opus file for {track_id}")
            return opus_path

        log_service.upscaling(f"⚡ Starting on-demand {bitrate} Opus generation for {track_id}")

        enhanced_wav_path = settings.ENHANCED_WAV_DIR / f"{track_id}.wav"
        if enhanced_wav_path.exists():
            log_service.upscaling(f"  → Attempting transcode from enhanced WAV to {bitrate}")
            success = await self.transcode_to_opus(enhanced_wav_path, opus_path, bitrate=bitrate)
            if success:
                log_service.upscaling(f"✓ Successfully created {bitrate} Opus from enhanced WAV")
                return opus_path
            else:
                log_service.error(f"✗ Enhanced WAV transcode to {bitrate} FAILED, trying next source")

        if wav_path and wav_path.exists():
            log_service.warning(f"  → Enhanced WAV not found for {track_id}, trying regular WAV to {bitrate}")
            success = await self.transcode_to_opus(wav_path, opus_path, bitrate=bitrate)
            if success:
                log_service.upscaling(f"✓ Successfully created {bitrate} Opus from regular WAV")
                return opus_path
            else:
                log_service.error(f"✗ Regular WAV transcode to {bitrate} FAILED, trying next source")

        if mp3_path and mp3_path.exists():
            log_service.warning(f"  → WAV not found for {track_id}, trying MP3 to {bitrate}")
            success = await self.transcode_to_opus(mp3_path, opus_path, bitrate=bitrate)
            if success:
                log_service.upscaling(f"✓ Successfully created {bitrate} Opus from MP3")
                return opus_path
            else:
                log_service.error(f"✗ MP3 transcode to {bitrate} FAILED")

        log_service.error(f"✗✗✗ COMPLETE FAILURE: No source file available for {track_id} at {bitrate}")
        return None

    async def transcode_opus_to_webm(
            self,
            opus_path: Path,
            webm_path: Path
    ) -> bool:
        if not self.ffmpeg_available:
            log_service.error("FFmpeg not available for WebM conversion")
            return False

        if not opus_path.exists():
            log_service.error(f"Opus file not found: {opus_path}")
            return False

        if webm_path.exists():
            file_size_mb = webm_path.stat().st_size / (1024 * 1024)
            log_service.upscaling(f"WebM file already exists: {webm_path.name} ({file_size_mb:.2f} MB)")
            return True

        temp_output = webm_path.with_suffix(".webm.tmp")

        if temp_output.exists():
            log_service.upscaling(f"Cleaning up orphaned temp WebM file: {temp_output.name}")
            await self._windows_safe_unlink(temp_output)

        try:
            log_service.upscaling(f"Converting {opus_path.name} to WebM container (fast codec copy)")

            process = await asyncio.create_subprocess_exec(
                FFMPEG_EXE_PATH,
                "-i", str(opus_path),
                "-c:a", "copy",
                "-f", "webm",
                "-y",
                str(temp_output),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=FFMPEG_CWD
            )

            _stdout, stderr = await process.communicate()
            error_msg = stderr.decode('utf-8', errors='ignore')

            if process.returncode == 0:
                log_service.upscaling("FFmpeg WebM conversion completed. Renaming...")

                if webm_path.exists():
                    await self._windows_safe_unlink(webm_path)

                rename_success = await self._windows_safe_rename(temp_output, webm_path)

                if not rename_success:
                    log_service.error("Failed to rename temp WebM file")
                    await self._windows_safe_unlink(temp_output)
                    return False

                file_size_mb = webm_path.stat().st_size / (1024 * 1024)
                log_service.upscaling(f"WebM conversion complete: {webm_path.name} ({file_size_mb:.2f} MB)")
                return True
            else:
                log_service.error(f"FFmpeg WebM conversion FAILED. Return code: {process.returncode}")
                log_service.error(f"FFmpeg stderr: {error_msg}")
                await self._windows_safe_unlink(temp_output)
                return False

        except Exception as e:
            log_service.error(f"WebM conversion error: {e}")
            await self._windows_safe_unlink(temp_output)
            return False

    async def get_or_create_webm(
            self,
            track_id: str,
            wav_path: Optional[Path] = None,
            mp3_path: Optional[Path] = None,
            bitrate: str = "192k"
    ) -> Optional[Path]:
        webm_path = self.get_webm_path(track_id, bitrate=bitrate)
        webm_exists = webm_path.exists()

        opus_path = await self.get_or_create_opus(track_id, wav_path, mp3_path, bitrate)
        if not opus_path:
            log_service.error(f"✗ Failed to get Opus file for {track_id} @ {bitrate}")
            return None

        if webm_exists:
            log_service.upscaling(f"✓ Found existing {bitrate} WebM (Opus ensured) for {track_id}")
            return webm_path

        log_service.upscaling(f"⚡ Creating {bitrate} WebM container from Opus for {track_id}")

        success = await self.transcode_opus_to_webm(opus_path, webm_path)
        if success:
            log_service.upscaling(f"✓ Successfully created {bitrate} WebM from Opus")
            return webm_path
        else:
            log_service.error(f"✗ WebM conversion FAILED for {track_id}")
            return None

    async def batch_create_webm_variants(
            self,
            track_ids: list[str],
            bitrates: list[str] = None,
            max_workers: int = None
    ) -> dict[str, dict[str, bool]]:

        if bitrates is None:
            bitrates = ['128k', '192k', '256k']

        if max_workers is None:
            max_workers = settings.MAX_PARALLEL_CPU_WORKERS

        log_service.upscaling(f"Batch WebM Generation: {len(track_ids)} tracks × {len(bitrates)} bitrates = {len(track_ids) * len(bitrates)} files")
        log_service.upscaling(f"Parallel workers: {max_workers} (utilizing all CPU cores)")

        semaphore = asyncio.Semaphore(max_workers)
        results = {}

        async def process_one(track_id: str, bitrate: str):
            async with semaphore:
                enhanced_wav = settings.ENHANCED_WAV_DIR / f"{track_id}.wav"

                if not enhanced_wav.exists():
                    log_service.warning(f"[{track_id}] Enhanced WAV not found, skipping")
                    return track_id, bitrate, False

                webm_path = await self.get_or_create_webm(
                    track_id=track_id,
                    wav_path=enhanced_wav,
                    mp3_path=None,
                    bitrate=bitrate
                )

                success = webm_path is not None and webm_path.exists()
                return track_id, bitrate, success

        tasks = [
            process_one(track_id, bitrate)
            for track_id in track_ids
            for bitrate in bitrates
        ]

        task_results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in task_results:
            if isinstance(result, Exception):
                log_service.error(f"Batch encoding error: {result}")
                continue

            track_id, bitrate, success = result
            if track_id not in results:
                results[track_id] = {}
            results[track_id][bitrate] = success

        total = len(track_ids) * len(bitrates)
        successes = sum(1 for track_results in results.values() for success in track_results.values() if success)
        failures = total - successes

        log_service.upscaling(f"Batch WebM Generation complete: {successes}/{total} succeeded, {failures} failed")

        return results

audio_transcoding_service = AudioTranscodingService()