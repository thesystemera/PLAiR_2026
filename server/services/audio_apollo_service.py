import sys
import asyncio
from pathlib import Path
from typing import Optional
import torch
import librosa
import numpy as np
import gc
from services import log_service
from services.base_service import SingletonService
from config import settings
from models_global import gpu_lease, raise_if_cuda_oom
from services.audio_headroom import write_float_wav

sys.path.insert(0, str(settings.APOLLO_DIR))
from look2hear.models import BaseModel

class AudioApolloService(SingletonService):
    LABEL = "Apollo"
    MODEL_ARGS = {"sr": 44100, "win": 20, "feature_dim": 256, "layer": 6}
    CHUNK_SECONDS = 20
    OVERLAP_SECONDS = 2
    BATCH = 1

    def checkpoint(self) -> Optional[Path]:
        checkpoints = list(settings.APOLLO_CHECKPOINTS_DIR.glob("*.bin")) + list(
            settings.APOLLO_CHECKPOINTS_DIR.glob("*.ckpt"))
        return checkpoints[0] if checkpoints else None

    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.model = None
        self.device = None
        self.apollo_loaded = False
        self.lock = asyncio.Lock()
        self._initialized = True

    async def initialize(self):
        if self.apollo_loaded:
            log_service.upscaling(f"{self.LABEL} already loaded")
            return

        log_service.upscaling(f"Loading {self.LABEL} restoration model...")

        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        if torch.cuda.is_available():
            log_service.upscaling(f"GPU: {torch.cuda.get_device_name(0)}")
            log_service.upscaling(f"VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.2f}GB")

        checkpoint = self.checkpoint()
        if checkpoint is None or not checkpoint.exists():
            log_service.error(f"No {self.LABEL} checkpoint found")
            return

        log_service.upscaling(f"Loading checkpoint: {checkpoint.name}")

        self.model = BaseModel.from_pretrain(str(checkpoint), **self.MODEL_ARGS)

        if self.device == "cuda":
            self.model = self.model.cuda()

        self.model.eval()

        for param in self.model.parameters():
            param.requires_grad = False

        self.apollo_loaded = True
        log_service.upscaling(f"{self.LABEL} model loaded and ready")

    def _process_audio_sync(
            self,
            input_path: Path,
            output_path: Path
    ) -> tuple[Optional[Path], dict]:
        try:
            audio, _sr = librosa.load(str(input_path), mono=False, sr=44100)

            if audio.ndim == 1:
                audio = audio[np.newaxis, :]

            channels, original_length = audio.shape
            duration = original_length / 44100

            chunk_samples = int(self.CHUNK_SECONDS * 44100)
            overlap_samples = int(self.OVERLAP_SECONDS * 44100)
            hop_samples = chunk_samples - overlap_samples

            padding_samples = chunk_samples // 2
            audio_padded = np.pad(audio, ((0, 0), (padding_samples, padding_samples)), mode='reflect')

            padded_length = audio_padded.shape[1]
            num_chunks = (padded_length - overlap_samples + hop_samples - 1) // hop_samples

            metadata = {
                "duration": duration,
                "channels": channels,
                "num_chunks": num_chunks
            }

            chunks = []
            for chunk_idx in range(num_chunks):
                start_idx = chunk_idx * hop_samples
                chunk = audio_padded[:, start_idx:min(start_idx + chunk_samples, padded_length)]
                pad_amount = chunk_samples - chunk.shape[1]
                if pad_amount > 0:
                    chunk = np.pad(chunk, ((0, 0), (0, pad_amount)), mode='reflect')
                chunks.append((chunk, pad_amount))

            outputs = []
            for first in range(0, len(chunks), self.BATCH):
                group = chunks[first:first + self.BATCH]
                waveform = torch.from_numpy(np.stack([c for c, _ in group])).float()
                if self.device == "cuda":
                    waveform = waveform.cuda()
                with torch.no_grad():
                    enhanced = self.model(waveform).cpu().numpy()
                for row, (_, pad_amount) in enumerate(group):
                    out = enhanced[row].reshape(channels, -1)
                    outputs.append(out[:, :-pad_amount] if pad_amount > 0 else out)
                del waveform, enhanced

            enhanced_chunks = []
            fade_samples = overlap_samples
            curve = 0.5 * (1 - np.cos(np.linspace(0, np.pi, fade_samples)))
            for chunk_idx, enhanced_chunk in enumerate(outputs):
                if chunk_idx > 0:
                    blended = enhanced_chunks[-1][:, -fade_samples:] * (1.0 - curve) + enhanced_chunk[:, :fade_samples] * curve
                    enhanced_chunks[-1] = enhanced_chunks[-1][:, :-fade_samples]
                    enhanced_chunk = enhanced_chunk.copy()
                    enhanced_chunk[:, :fade_samples] = blended
                enhanced_chunks.append(enhanced_chunk)

            enhanced_padded = np.concatenate(enhanced_chunks, axis=1)

            if enhanced_padded.shape[1] > padding_samples * 2:
                enhanced = enhanced_padded[:, padding_samples: padding_samples + original_length]
            else:
                enhanced = enhanced_padded[:, :original_length]

            output_path.parent.mkdir(parents=True, exist_ok=True)
            write_float_wav(output_path, enhanced, 44100)

            del audio, audio_padded, enhanced, enhanced_chunks, enhanced_padded
            if self.device == "cuda":
                torch.cuda.empty_cache()
            gc.collect()

            return output_path, metadata

        except Exception as e:
            if self.device == "cuda":
                torch.cuda.empty_cache()
            gc.collect()
            raise_if_cuda_oom(e, self.LABEL)
            return None, {"error": str(e)}

    async def process_audio(
            self,
            input_path: Path,
            output_path: Path,
    ) -> Optional[Path]:
        async with self.lock:
            if not self.apollo_loaded:
                log_service.error(f"{self.LABEL} model not loaded")
                return None

            if output_path.exists():
                log_service.upscaling(f"{self.LABEL} WAV already exists: {output_path.name}")
                return output_path

            log_service.upscaling(f"{self.LABEL}: Processing {input_path.name}")

            async with gpu_lease(self.LABEL):
                result, metadata = await asyncio.to_thread(
                    self._process_audio_sync,
                    input_path,
                    output_path
                )

            if not result:
                log_service.error(f"{self.LABEL} processing failed: {metadata.get('error', 'Unknown error')}")
                return None

            log_service.upscaling(
                f"  Duration: {metadata['duration']:.1f}s | "
                f"Channels: {metadata['channels']} | "
                f"Chunks: {metadata['num_chunks']}"
            )
            log_service.upscaling(f"{self.LABEL} complete: {output_path.name}")

            return result

    async def unload(self):
        if self.model is not None:
            del self.model
            self.model = None

        if self.device == "cuda":
            torch.cuda.empty_cache()

        gc.collect()
        self.apollo_loaded = False
        log_service.upscaling(f"{self.LABEL} model unloaded")