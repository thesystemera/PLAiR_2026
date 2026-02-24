import asyncio
from pathlib import Path
from typing import Optional, Dict
import torch
import torchaudio
import gc
from demucs.pretrained import get_model
from demucs.apply import apply_model
from services import log_service
from services.base_service import SingletonService
from config import settings

class AudioDemucsService(SingletonService):

    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.model = None
        self.device = None
        self.demucs_loaded = False
        self.lock = asyncio.Lock()
        self.apply_model = None
        self._initialized = True

    async def initialize(self):
        if self.demucs_loaded:
            log_service.upscaling("Demucs already loaded")
            return

        log_service.upscaling("Loading Demucs stem separation model...")

        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        if torch.cuda.is_available():
            log_service.upscaling(f"GPU: {torch.cuda.get_device_name(0)}")

        try:
            self.model = get_model(settings.DEMUCS_MODEL)
            self.model.to(self.device)
            self.model.eval()

            self.apply_model = apply_model

            self.demucs_loaded = True
            log_service.upscaling(f"Demucs model '{settings.DEMUCS_MODEL}' loaded and ready")

        except Exception as e:
            log_service.error(f"Failed to load Demucs model: {e}")
            return

    def _separate_stems_sync(
        self,
        input_path: Path,
        output_dir: Path
    ) -> Optional[Dict[str, Path]]:
        try:
            audio, sr = torchaudio.load(str(input_path))

            if audio.shape[0] == 1:
                audio = audio.repeat(2, 1)
            elif audio.shape[0] > 2:
                audio = audio[:2, :]

            if sr != 44100:
                audio = torchaudio.functional.resample(audio, sr, 44100)

            audio = audio.unsqueeze(0).to(self.device)

            log_service.upscaling("Demucs: Separating stems...")
            with torch.no_grad():
                sources = self.apply_model(
                    self.model,
                    audio,
                    device=self.device,
                    shifts=1,
                    split=True,
                    overlap=0.25,
                    progress=False
                )

            sources = sources.squeeze(0)

            stem_names = self.model.sources

            vocals_idx = stem_names.index("vocals")
            vocals = sources[vocals_idx].cpu()

            no_vocals = torch.zeros_like(vocals)
            for idx, stem_name in enumerate(stem_names):
                if stem_name != "vocals":
                    no_vocals += sources[idx].cpu()

            output_dir.mkdir(parents=True, exist_ok=True)

            vocals_path = output_dir / "vocals.wav"
            torchaudio.save(str(vocals_path), vocals, 44100)
            log_service.upscaling(f"  Saved vocals: {vocals_path.name}")

            no_vocals_path = output_dir / "no_vocals.wav"
            torchaudio.save(str(no_vocals_path), no_vocals, 44100)
            log_service.upscaling(f"  Saved instrumentals: {no_vocals_path.name}")

            del audio, sources, vocals, no_vocals
            if self.device == "cuda":
                torch.cuda.empty_cache()
            gc.collect()

            return {
                "vocals": vocals_path,
                "no_vocals": no_vocals_path
            }

        except Exception as e:
            log_service.error(f"Demucs separation failed: {str(e)}")
            if self.device == "cuda":
                torch.cuda.empty_cache()
            gc.collect()
            return None

    async def separate_stems(
        self,
        input_path: Path,
        output_dir: Path
    ) -> Optional[Dict[str, Path]]:
        async with self.lock:
            if not self.demucs_loaded:
                log_service.error("Demucs model not loaded")
                return None

            log_service.upscaling(f"Demucs: Processing {input_path.name}")

            result = await asyncio.to_thread(
                self._separate_stems_sync,
                input_path,
                output_dir
            )

            if result:
                log_service.upscaling("Demucs separation complete")
            else:
                log_service.error("Demucs separation failed")

            return result

    async def unload(self):
        if self.model is not None:
            del self.model
            self.model = None

        if self.device == "cuda":
            torch.cuda.empty_cache()

        gc.collect()
        self.demucs_loaded = False
        log_service.upscaling("Demucs model unloaded")