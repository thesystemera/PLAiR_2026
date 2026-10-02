import asyncio
import os
from pathlib import Path
from typing import Optional
import torch
import torchaudio
import pyloudnorm as pyln
import soundfile as sf
import gc
import numpy as np
from clearvoice import ClearVoice
from clearvoice.networks import SpeechModel
from services import log_service
from services.base_service import SingletonService
from config.settings import BASE_DIR
from models_global import gpu_lease, raise_if_cuda_oom
from services.audio_headroom import write_float_wav

SR_RATE = 48000
MIN_MEASURABLE_LUFS = -70.0

SpeechModel.get_free_gpu = lambda self: torch.cuda.current_device()

class AudioClearVoiceService(SingletonService):
    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.sr_model = None
        self.models_loaded = False
        self.lock = asyncio.Lock()
        self._initialized = True

    async def initialize(self):
        if self.models_loaded:
            log_service.upscaling("ClearVoice already loaded")
            return

        original_cwd = os.getcwd()
        try:
            os.chdir(BASE_DIR)
            log_service.upscaling("Loading ClearVoice vocal super-resolution (MossFormer2_SR_48K)...")
            self.sr_model = ClearVoice(
                task='speech_super_resolution',
                model_names=['MossFormer2_SR_48K']
            )
            self.models_loaded = True
            log_service.upscaling("ClearVoice ready")
        except Exception as e:
            log_service.error(f"Failed to load ClearVoice: {str(e)}")
            self.models_loaded = False
        finally:
            os.chdir(original_cwd)

    @staticmethod
    def _loudness(audio: np.ndarray, rate: int) -> float:
        value = float(pyln.Meter(rate).integrated_loudness(audio.astype(np.float64)))
        return value if np.isfinite(value) and value > MIN_MEASURABLE_LUFS else float("nan")

    def _process_audio_sync(self, input_path: Path, output_path: Path) -> Optional[Path]:
        temp_input_48k = output_path.with_suffix(".temp_in_48k.wav")
        temp_sr_out = output_path.with_suffix(".temp_sr_out.wav")
        try:
            vocals, rate = sf.read(str(input_path), dtype="float32", always_2d=True)
            vocals_48k = torchaudio.functional.resample(torch.from_numpy(vocals.T.copy()), rate, SR_RATE)
            sf.write(str(temp_input_48k), vocals_48k.numpy().T, SR_RATE, subtype="FLOAT")

            result = self.sr_model(input_path=str(temp_input_48k), online_write=False)
            self.sr_model.write(result, output_path=str(temp_sr_out))
            lifted, _ = sf.read(str(temp_sr_out), dtype="float32", always_2d=True)
            lifted = torchaudio.functional.resample(torch.from_numpy(lifted.T.copy()), SR_RATE, rate).numpy().T
            if lifted.shape[1] < vocals.shape[1]:
                lifted = np.repeat(lifted[:, :1], vocals.shape[1], axis=1)
            length = min(lifted.shape[0], vocals.shape[0])
            lifted = lifted[:length]

            source_lufs = self._loudness(vocals[:length], rate)
            lifted_lufs = self._loudness(lifted, rate)
            gain_db = source_lufs - lifted_lufs if np.isfinite(source_lufs) and np.isfinite(lifted_lufs) else 0.0
            lifted = lifted * 10 ** (gain_db / 20)

            write_float_wav(output_path, lifted.T, rate)
            log_service.upscaling(f"  Vocal super-resolution done, level matched {gain_db:+.2f} dB")
            return output_path

        except Exception as e:
            log_service.error(f"Vocal super-resolution failed: {str(e)}")
            raise_if_cuda_oom(e, "ClearVoice")
            return None
        finally:
            for p in (temp_input_48k, temp_sr_out):
                if p.exists():
                    p.unlink()
            gc.collect()

    async def enhance_vocals(self, input_path: Path, output_path: Path) -> Optional[Path]:
        async with self.lock:
            if not self.models_loaded:
                log_service.error("ClearVoice models not loaded")
                return None

            log_service.upscaling(f"Processing Chain: {input_path.name}")
            async with gpu_lease("ClearVoice"):
                return await asyncio.to_thread(self._process_audio_sync, input_path, output_path)

    async def unload(self):
        self.sr_model = None
        gc.collect()
        self.models_loaded = False
        log_service.upscaling("ClearVoice Chain unloaded")