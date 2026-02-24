import asyncio
import os
import tempfile
from typing import Optional, Dict, Any
from services import log_service
from services.base_service import SingletonService
from config.settings import settings

class WhisperDualService(SingletonService):

    def __init__(self):
        if self._initialized:
            return

        self.fast_model = None
        self.quality_model = None
        self.models_loaded = False
        self._initialized = True

    async def initialize(self):
        if self.models_loaded:
            log_service.system("WhisperDualService already initialized")
            return

        try:
            from faster_whisper import WhisperModel

            device = settings.WHISPER_DEVICE
            compute_type = settings.WHISPER_COMPUTE_TYPE
            fast_model_name = settings.WHISPER_FAST_MODEL
            quality_model_name = settings.WHISPER_QUALITY_MODEL

            log_service.system(f"Loading Faster Whisper FAST model ({fast_model_name})...")
            log_service.system(f"  Device: {device}, Compute type: {compute_type}")

            self.fast_model = await asyncio.to_thread(
                WhisperModel,
                fast_model_name,
                device=device,
                compute_type=compute_type,
                download_root=None
            )

            log_service.success(f"✓ Whisper fast model loaded ({fast_model_name} with {device.upper()})")

            log_service.system(f"Loading Faster Whisper QUALITY model ({quality_model_name})...")
            log_service.system(f"  Device: {device}, Compute type: {compute_type}")

            self.quality_model = await asyncio.to_thread(
                WhisperModel,
                quality_model_name,
                device=device,
                compute_type=compute_type,
                download_root=None
            )

            self.models_loaded = True
            log_service.success(f"✓ Whisper quality model loaded ({quality_model_name} with {device.upper()})")
            log_service.success("✓ DUAL WHISPER SYSTEM READY (matches old architecture)")

        except Exception as e:
            log_service.error(f"Failed to load Whisper models with GPU: {str(e)}")
            log_service.system("Falling back to CPU...")

            try:
                from faster_whisper import WhisperModel

                device = "cpu"
                compute_type = "int8"

                log_service.system("Loading Whisper models on CPU...")

                self.fast_model = await asyncio.to_thread(
                    WhisperModel,
                    settings.WHISPER_FAST_MODEL,
                    device=device,
                    compute_type=compute_type
                )

                self.quality_model = await asyncio.to_thread(
                    WhisperModel,
                    settings.WHISPER_QUALITY_MODEL,
                    device=device,
                    compute_type=compute_type
                )

                self.models_loaded = True
                log_service.success("✓ Dual Whisper models loaded (CPU fallback)")

            except Exception as cpu_error:
                log_service.error(f"Failed to load Whisper models on CPU: {str(cpu_error)}")
                self.models_loaded = False

    async def transcribe_fast(
        self,
        audio_data: bytes,
        language: str = "en",
        timeout_seconds: int = None
    ) -> Optional[str]:

        if not self.models_loaded or not self.fast_model:
            log_service.error("Whisper fast model not loaded")
            return None

        if timeout_seconds is None:
            timeout_seconds = settings.WHISPER_TIMEOUT

        temp_file_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as temp_file:
                temp_file.write(audio_data)
                temp_file_path = temp_file.name

            log_service.system(f"Fast transcribing ({len(audio_data)} bytes)...")

            segments, _ = await asyncio.wait_for(
                asyncio.to_thread(
                    self.fast_model.transcribe,
                    temp_file_path,
                    language=language
                ),
                timeout=timeout_seconds
            )

            transcription = "".join(segment.text for segment in segments).strip()

            if not transcription:
                log_service.error("Fast transcription produced empty result")
                return None

            log_service.success(f"✓ Fast transcribed: \"{transcription}\"")
            return transcription

        except asyncio.TimeoutError:
            log_service.error(f"Fast transcription timed out after {timeout_seconds}s")
            return None
        except Exception as e:
            log_service.error(f"Fast transcription failed: {str(e)}")
            return None

        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                except Exception as e:
                    log_service.error(f"Failed to delete temp file: {str(e)}")

    async def transcribe_quality(
        self,
        audio_data: bytes,
        language: str = "en",
        timeout_seconds: int = None
    ) -> Optional[Dict[str, Any]]:

        if not self.models_loaded or not self.quality_model:
            log_service.error("Whisper quality model not loaded")
            return None

        if timeout_seconds is None:
            timeout_seconds = settings.WHISPER_TIMEOUT

        max_file_size = settings.WHISPER_MAX_FILE_SIZE_MB * 1024 * 1024
        if len(audio_data) > max_file_size:
            log_service.error(f"Audio file too large: {len(audio_data)} bytes (max {max_file_size})")
            return None

        temp_file_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as temp_file:
                temp_file.write(audio_data)
                temp_file_path = temp_file.name

            log_service.system(f"Quality transcribing ({len(audio_data)} bytes)...")

            segments, info = await asyncio.wait_for(
                asyncio.to_thread(
                    self.quality_model.transcribe,
                    temp_file_path,
                    language=language,
                    word_timestamps=True
                ),
                timeout=timeout_seconds
            )

            full_text = ""
            segment_list = []
            words_list = []

            for segment in segments:
                full_text += segment.text + " "
                segment_list.append({
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text.strip()
                })

                if hasattr(segment, 'words') and segment.words:
                    for word in segment.words:
                        words_list.append({
                            "word": word.word.strip(),
                            "start": word.start,
                            "end": word.end,
                            "probability": word.probability
                        })

            full_text = full_text.strip()

            if not full_text:
                log_service.error("Quality transcription produced empty result")
                return None

            result = {
                "text": full_text,
                "language": info.language,
                "language_probability": info.language_probability,
                "duration": info.duration,
                "segments": segment_list,
                "words": words_list
            }

            log_service.success(f"✓ Quality transcribed: \"{full_text}\"")
            log_service.system(f"  Duration: {info.duration:.2f}s, Language: {info.language} ({info.language_probability:.2%})")

            return result

        except asyncio.TimeoutError:
            log_service.error(f"Quality transcription timed out after {timeout_seconds}s")
            return None
        except Exception as e:
            log_service.error(f"Quality transcription failed: {str(e)}")
            return None

        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                except Exception as e:
                    log_service.error(f"Failed to delete temp file: {str(e)}")

whisper_dual_service = WhisperDualService()