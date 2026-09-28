import asyncio
import contextlib
import gc
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from services import log_service
from services.base_service import SingletonService
from config import settings
from models_global import gpu_lease, raise_if_cuda_oom

MODEL_SR = 16000
WINDOW_SECONDS = 10
AXES = ("PQ", "PC", "CE", "CU")


class AudioQualityScoreService(SingletonService):

    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.model = None
        self.transforms = None
        self.device = None
        self.scorer_loaded = False
        self.scorer_available = False
        self.lock = asyncio.Lock()
        self._last_used = time.monotonic()
        self._idle_task: Optional[asyncio.Task] = None
        self._initialized = True

    async def initialize(self):
        model_dir = settings.AUDIOBOX_AESTHETICS_DIR
        missing = [name for name in ("config.json", "model.safetensors") if not (model_dir / name).exists()]
        if missing:
            log_service.error(f"Audiobox Aesthetics not available: missing {missing} in {model_dir}")
            self.scorer_available = False
            return
        self.scorer_available = True
        log_service.upscaling("Audiobox Aesthetics scorer available (loads on first use)")

    def _resolve_device(self) -> str:
        import torch
        wanted = settings.AUDIOBOX_DEVICE
        if wanted == "cuda" or (wanted == "auto" and torch.cuda.is_available()):
            return "cuda" if torch.cuda.is_available() else "cpu"
        return "cpu"

    def _load_sync(self):
        from audiobox_aesthetics.model.aes import AesMultiOutput, Normalize

        self.device = self._resolve_device()
        log_service.upscaling(f"Loading Audiobox Aesthetics scorer on {self.device}...")
        model = AesMultiOutput.from_pretrained(str(settings.AUDIOBOX_AESTHETICS_DIR))
        model.to(self.device).eval()
        self.transforms = {
            axis: Normalize(mean=model.target_transform[axis]["mean"], std=model.target_transform[axis]["std"])
            for axis in AXES
        }
        self.model = model
        self.scorer_loaded = True
        log_service.upscaling("Audiobox Aesthetics scorer loaded and ready")

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
        while self.scorer_loaded:
            await asyncio.sleep(min(60.0, idle_seconds))
            if not self.scorer_loaded or self.lock.locked():
                continue
            if time.monotonic() - self._last_used < idle_seconds:
                continue
            async with self.lock:
                if self.scorer_loaded and time.monotonic() - self._last_used >= idle_seconds:
                    log_service.upscaling(f"Audiobox Aesthetics idle for {idle_seconds / 60:.0f} min, unloading")
                    await self.unload()

    def _load_audio(self, path: Path):
        import librosa
        import torch
        import torchaudio

        audio, rate = librosa.load(str(path), sr=None, mono=False)
        wav = torch.from_numpy(np.atleast_2d(audio).astype(np.float32))
        wav = torchaudio.functional.resample(wav, orig_freq=int(rate), new_freq=MODEL_SR)
        return wav.mean(dim=0, keepdim=True)

    def _score_sync(self, path: Path) -> Dict[str, float]:
        import torch
        import torch.nn.functional as F

        wav = self._load_audio(path)
        window = WINDOW_SECONDS * MODEL_SR
        pieces, masks, weights = [], [], []
        for start in range(0, wav.shape[-1], window):
            piece = wav[..., start:start + window]
            valid = piece.shape[-1]
            if valid < window:
                piece = F.pad(piece, (0, window - valid))
            mask = torch.zeros_like(piece, dtype=torch.bool)
            mask[:, :valid] = True
            pieces.append(piece)
            masks.append(mask)
            weights.append(valid / window)

        batch_size = max(1, int(settings.AUDIOBOX_BATCH_SIZE))
        totals = {axis: 0.0 for axis in AXES}
        weight_sum = float(sum(weights))
        with torch.inference_mode():
            for offset in range(0, len(pieces), batch_size):
                wavs = torch.stack(pieces[offset:offset + batch_size]).to(self.device)
                mask = torch.stack(masks[offset:offset + batch_size]).to(self.device)
                batch_weights = torch.tensor(weights[offset:offset + batch_size], dtype=torch.float32)
                predictions = self.model({"wav": wavs, "mask": mask})
                for axis in AXES:
                    values = self.transforms[axis].inverse(predictions[axis]).float().cpu()
                    totals[axis] += float((values * batch_weights).sum())
                del wavs, mask, predictions

        return {axis: round(totals[axis] / max(weight_sum, 1e-9), 3) for axis in AXES}

    def _score_many_sync(self, paths: List[Path]) -> List[Optional[Dict[str, float]]]:
        if not self.scorer_loaded:
            self._load_sync()
        results = []
        for path in paths:
            try:
                results.append(self._score_sync(Path(path)))
            except Exception as e:
                raise_if_cuda_oom(e, "Audiobox Aesthetics")
                log_service.error(f"Audiobox Aesthetics failed on {Path(path).name}: {e}")
                results.append(None)
        if self.device == "cuda":
            import torch
            torch.cuda.empty_cache()
        return results

    async def _run(self, paths: List[Path]) -> List[Optional[Dict[str, float]]]:
        async with self.lock:
            if not self.scorer_available:
                log_service.error("Audiobox Aesthetics scorer not available")
                return [None for _ in paths]
            use_gpu = self._resolve_device() == "cuda"
            try:
                async with (gpu_lease("Audiobox Aesthetics") if use_gpu else contextlib.nullcontext()):
                    results = await asyncio.to_thread(self._score_many_sync, paths)
            except Exception as e:
                log_service.error(f"Audiobox Aesthetics scoring failed: {e}")
                raise_if_cuda_oom(e, "Audiobox Aesthetics")
                results = [None for _ in paths]
            self._mark_used()
            return results

    async def score(self, path: Path) -> Optional[Dict[str, float]]:
        result = (await self._run([Path(path)]))[0]
        if result:
            log_service.upscaling(
                f"Aesthetics {Path(path).name}: PQ {result['PQ']:.2f} | PC {result['PC']:.2f} | "
                f"CE {result['CE']:.2f} | CU {result['CU']:.2f}"
            )
        return result

    async def compare(self, paths: Dict[str, Path]) -> Dict[str, Optional[Dict[str, float]]]:
        names = list(paths.keys())
        results = await self._run([Path(paths[name]) for name in names])
        scores = dict(zip(names, results))
        for name, result in scores.items():
            if result:
                log_service.upscaling(
                    f"Aesthetics [{name}]: PQ {result['PQ']:.2f} | PC {result['PC']:.2f} | "
                    f"CE {result['CE']:.2f} | CU {result['CU']:.2f}"
                )
        return scores

    async def unload(self):
        if self.model is not None:
            del self.model
            self.model = None
        if self.device == "cuda":
            import torch
            torch.cuda.empty_cache()
        gc.collect()
        self.scorer_loaded = False
        log_service.upscaling("Audiobox Aesthetics scorer unloaded")
