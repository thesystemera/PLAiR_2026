import asyncio
import gc
import time
from pathlib import Path
from typing import Dict, Optional

import numpy as np
from scipy.signal import resample_poly

from services import log_service
from services.base_service import SingletonService
from config import settings
from models_global import gpu_lease, raise_if_cuda_oom
from services.audio_headroom import write_float_wav


def _clear_cuda_cache():
    import torch
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    gc.collect()


def _resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return audio.astype(np.float32, copy=False)
    divisor = np.gcd(int(source_rate), int(target_rate))
    return resample_poly(audio, target_rate // divisor, source_rate // divisor, axis=-1).astype(np.float32)


def _fit_length(audio: np.ndarray, length: int) -> np.ndarray:
    if audio.shape[-1] >= length:
        return audio[..., :length]
    return np.pad(audio, ((0, 0), (0, length - audio.shape[-1])))


def _windowing_array(size: int, fade: int) -> np.ndarray:
    window = np.ones(size, dtype=np.float32)
    if fade > 0:
        window[:fade] = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        window[-fade:] = np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return window


class AudioRoformerService(SingletonService):

    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.model = None
        self.config = None
        self.device = None
        self.roformer_loaded = False
        self.roformer_available = False
        self.lock = asyncio.Lock()
        self._last_used = time.monotonic()
        self._idle_task: Optional[asyncio.Task] = None
        self._initialized = True

    @property
    def checkpoint_path(self) -> Path:
        return settings.ROFORMER_MODEL_DIR / settings.ROFORMER_MODEL_FILENAME

    @property
    def config_path(self) -> Path:
        return settings.ROFORMER_MODEL_DIR / settings.ROFORMER_CONFIG_FILENAME

    async def initialize(self):
        missing = [p.name for p in (self.checkpoint_path, self.config_path) if not p.exists()]
        if missing:
            log_service.error(f"RoFormer not available: missing {missing} in {settings.ROFORMER_MODEL_DIR}")
            self.roformer_available = False
            return
        self.roformer_available = True
        log_service.upscaling(f"RoFormer available: {settings.ROFORMER_MODEL_FILENAME} (loads on first use)")

    async def preload(self):
        async with self.lock:
            if self.roformer_loaded or not self.roformer_available:
                return
            async with gpu_lease("RoFormer load"):
                await asyncio.to_thread(self._load_sync)
            self._mark_used()

    def _load_sync(self):
        import torch
        import yaml
        from ml_collections import ConfigDict

        log_service.upscaling(f"Loading RoFormer separation model {settings.ROFORMER_MODEL_FILENAME}...")
        with open(self.config_path, "r", encoding="utf-8") as f:
            config = ConfigDict(yaml.load(f, Loader=yaml.FullLoader))

        if "num_bands" in config.model:
            from audio_separator.separator.uvr_lib_v5.roformer.mel_band_roformer import MelBandRoformer
            model = MelBandRoformer(**config.model)
        elif "freqs_per_bands" in config.model:
            from audio_separator.separator.uvr_lib_v5.roformer.bs_roformer import BSRoformer
            model = BSRoformer(**config.model)
        else:
            raise ValueError("Unknown RoFormer config: expected num_bands or freqs_per_bands")

        state = torch.load(str(self.checkpoint_path), map_location="cpu", weights_only=True)
        if isinstance(state, dict) and "state_dict" in state:
            state = state["state_dict"]
        model.load_state_dict(state)

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        if self.device == "cuda":
            log_service.upscaling(f"GPU: {torch.cuda.get_device_name(0)}")
        model.to(self.device).eval()
        for param in model.parameters():
            param.requires_grad = False

        self.model = model
        self.config = config
        self.roformer_loaded = True
        log_service.upscaling("RoFormer model loaded and ready")

    def _mark_used(self):
        self._last_used = time.monotonic()
        self._ensure_idle_watcher()

    def _ensure_idle_watcher(self):
        if settings.GPU_IDLE_UNLOAD_MINUTES <= 0:
            return
        if self._idle_task is not None and not self._idle_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._idle_task = loop.create_task(self._idle_unload_loop())

    async def _idle_unload_loop(self):
        idle_seconds = settings.GPU_IDLE_UNLOAD_MINUTES * 60
        while self.roformer_loaded:
            await asyncio.sleep(min(60.0, idle_seconds))
            if not self.roformer_loaded or self.lock.locked():
                continue
            if time.monotonic() - self._last_used < idle_seconds:
                continue
            async with self.lock:
                if self.roformer_loaded and time.monotonic() - self._last_used >= idle_seconds:
                    log_service.upscaling(f"RoFormer idle for {idle_seconds / 60:.0f} min, unloading")
                    await self.unload()

    @property
    def model_rate(self) -> int:
        return int(self.config.audio.get("sample_rate", 44100))

    def _target_index(self) -> int:
        training = self.config.training
        instruments = list(training.get("instruments", ["vocals"]))
        target = training.get("target_instrument")
        if target:
            return 0
        lowered = [name.lower() for name in instruments]
        return lowered.index("vocals") if "vocals" in lowered else 0

    def _demix(self, mix: np.ndarray) -> np.ndarray:
        import torch
        import torch.nn.functional as F

        chunk = int(self.config.audio.chunk_size)
        overlap = max(1, int(settings.ROFORMER_NUM_OVERLAP))
        step = max(1, chunk // overlap)
        fade = chunk // 10
        border = chunk - step
        length = mix.shape[-1]

        audio = torch.from_numpy(np.ascontiguousarray(mix, dtype=np.float32))
        padded = length > 2 * border and border > 0
        if padded:
            audio = F.pad(audio.unsqueeze(0), (border, border), mode="reflect").squeeze(0)
        total = audio.shape[-1]

        base_window = torch.from_numpy(_windowing_array(chunk, fade))
        result = None
        counter = torch.zeros(total, dtype=torch.float32)
        target_index = self._target_index()

        with torch.inference_mode():
            start = 0
            while start < total:
                part = audio[:, start:start + chunk]
                part_len = part.shape[-1]
                if part_len < chunk:
                    mode = "reflect" if part_len > chunk // 2 + 1 else "constant"
                    part = F.pad(part.unsqueeze(0), (0, chunk - part_len), mode=mode).squeeze(0)
                estimate = self.model(part.unsqueeze(0).to(self.device))[0].float().cpu()
                if estimate.dim() == 3:
                    estimate = estimate[target_index]
                window = base_window.clone()
                if start == 0:
                    window[:fade] = 1.0
                if start + step >= total:
                    window[-fade:] = 1.0
                if result is None:
                    result = torch.zeros((estimate.shape[0], total), dtype=torch.float32)
                result[:, start:start + part_len] += estimate[:, :part_len] * window[:part_len]
                counter[start:start + part_len] += window[:part_len]
                start += step

        vocals = (result / counter.clamp(min=1e-10)).numpy()
        if padded:
            vocals = vocals[:, border:border + length]
        return vocals

    def _separate_sync(self, input_path: Path, output_dir: Path) -> Optional[Dict[str, Path]]:
        import librosa

        try:
            audio, source_rate = librosa.load(str(input_path), sr=None, mono=False)
            audio = np.atleast_2d(audio).astype(np.float32)
            channels, length = audio.shape
            model_rate = self.model_rate

            mix = _resample(audio, int(source_rate), model_rate)
            if mix.shape[0] == 1:
                mix = np.repeat(mix, 2, axis=0)
            elif mix.shape[0] > 2:
                mix = mix[:2]

            vocals = self._demix(mix)
            del mix

            if channels == 1:
                vocals = vocals.mean(axis=0, keepdims=True)
            vocals = _fit_length(_resample(vocals, model_rate, int(source_rate)), length)
            if channels > 2:
                vocals = np.concatenate([vocals, np.zeros((channels - 2, length), dtype=np.float32)], axis=0)
            instrumental = audio - vocals

            output_dir.mkdir(parents=True, exist_ok=True)
            vocals_path = output_dir / "vocals.wav"
            instrumental_path = output_dir / "no_vocals.wav"
            write_float_wav(vocals_path, vocals, int(source_rate))
            write_float_wav(instrumental_path, instrumental, int(source_rate))
            log_service.upscaling(f"  Saved vocals: {vocals_path.name}")
            log_service.upscaling(f"  Saved instrumentals: {instrumental_path.name}")

            del audio, vocals, instrumental
            _clear_cuda_cache()
            return {"vocals": vocals_path, "instrumental": instrumental_path, "no_vocals": instrumental_path}

        except Exception as e:
            log_service.error(f"RoFormer separation failed: {e}")
            _clear_cuda_cache()
            raise_if_cuda_oom(e, "RoFormer")
            return None

    async def separate(self, input_path: Path, output_dir: Path) -> Optional[Dict[str, Path]]:
        async with self.lock:
            if not self.roformer_available:
                log_service.error("RoFormer not available")
                return None

            log_service.upscaling(f"RoFormer: Processing {input_path.name}")
            started = time.monotonic()

            async with gpu_lease("RoFormer"):
                if not self.roformer_loaded:
                    try:
                        await asyncio.to_thread(self._load_sync)
                    except Exception as e:
                        log_service.error(f"RoFormer load failed: {e}")
                        _clear_cuda_cache()
                        raise_if_cuda_oom(e, "RoFormer load")
                        return None
                result = await asyncio.to_thread(self._separate_sync, input_path, output_dir)
            self._mark_used()

            if result:
                log_service.upscaling(f"RoFormer separation complete in {time.monotonic() - started:.1f}s")
            else:
                log_service.error("RoFormer separation failed")
            return result

    async def separate_stems(self, input_path: Path, output_dir: Path) -> Optional[Dict[str, Path]]:
        return await self.separate(input_path, output_dir)

    async def unload(self):
        if self.model is not None:
            del self.model
            self.model = None
        _clear_cuda_cache()
        self.roformer_loaded = False
        log_service.upscaling("RoFormer model unloaded")
