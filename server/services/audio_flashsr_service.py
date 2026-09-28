import asyncio
import gc
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
from scipy import fft as sp_fft
from scipy.ndimage import uniform_filter1d
from scipy.signal import resample_poly, welch

from services import log_service
from services.base_service import SingletonService
from config import settings
from models_global import gpu_lease, raise_if_cuda_oom
from services.audio_headroom import write_float_wav

MODEL_SR = 48000
WINDOW_SAMPLES = 245760
OUTPUT_SR = 44100
CUTOFF_THRESHOLD_DB = -60.0
CUTOFF_SMOOTH_HZ = 200.0
CUTOFF_MARGIN_HZ = 150.0
MIN_CROSSOVER_HZ = 4000.0
GUARD_BAND_HZ = 1500.0
GUARD_SMOOTH_SECONDS = 1.0
NUM_STEPS = 1
SEED = 0


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


def detect_cutoff_hz(audio: np.ndarray, rate: int) -> float:
    x = np.atleast_2d(audio)
    nperseg = 4096 if rate <= 48000 else 8192
    freqs, psd = welch(x, fs=rate, nperseg=nperseg, axis=-1)
    level = 10.0 * np.log10(np.mean(np.atleast_2d(psd), axis=0) + 1e-20)
    bins = max(1, int(round(CUTOFF_SMOOTH_HZ / (freqs[1] - freqs[0]))))
    level = uniform_filter1d(level, size=bins, mode='nearest')
    reference = float(np.median(level[(freqs >= 1000) & (freqs < 4000)]))
    above = np.nonzero(level > reference + CUTOFF_THRESHOLD_DB)[0]
    if above.size == 0:
        return 0.0
    return float(freqs[above[-1]])


def _crossover_mask(freqs: np.ndarray, center_hz: float, width_hz: float) -> np.ndarray:
    lo = center_hz - width_hz / 2.0
    hi = center_hz + width_hz / 2.0
    mask = np.clip((freqs - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
    return (0.5 - 0.5 * np.cos(np.pi * mask)).astype(np.float32)


def _band_signal(spectrum: np.ndarray, freqs: np.ndarray, lo: float, hi: float, length: int) -> np.ndarray:
    band = ((freqs >= lo) & (freqs < hi)).astype(np.float32)
    return sp_fft.irfft(spectrum * band, n=length, axis=-1, workers=4).astype(np.float32)


def _seam_head(overlap: int, guard: int) -> np.ndarray:
    head = np.ones(overlap, dtype=np.float32)
    if overlap == 0:
        return head
    ramp_len = max(1, overlap - 2 * guard)
    head[:guard] = 0.0
    ramp = np.linspace(0.0, np.pi / 2.0, ramp_len, dtype=np.float32)
    head[guard:guard + ramp_len] = np.sin(ramp) ** 2
    return head


def _seam_weights(length: int, head: np.ndarray, has_prev: bool, has_next: bool) -> np.ndarray:
    weights = np.ones(length, dtype=np.float32)
    overlap = head.shape[0]
    if overlap == 0:
        return weights
    if has_prev:
        span = min(overlap, length)
        weights[:span] *= head[:span]
    if has_next and length >= overlap:
        weights[-overlap:] *= 1.0 - head
    return weights


class AudioFlashSRService(SingletonService):

    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.model = None
        self.device = None
        self.flashsr_loaded = False
        self.flashsr_available = False
        self.lock = asyncio.Lock()
        self._scheduler_cls = None
        self._wrapper_cls = None
        self._lowpass_cls = None
        self._last_used = time.monotonic()
        self._idle_task: Optional[asyncio.Task] = None
        self._initialized = True

    @property
    def weights_dir(self) -> Path:
        return settings.FLASHSR_DIR / "weights"

    async def initialize(self):
        weights = [self.weights_dir / name for name in ("student_ldm.pth", "sr_vocoder.pth", "vae.pth")]
        missing = [w.name for w in weights if not w.exists()]
        if missing or not (settings.FLASHSR_DIR / "FlashSR").exists():
            log_service.error(f"FlashSR not available: missing {missing or 'FlashSR code'} in {settings.FLASHSR_DIR}")
            self.flashsr_available = False
            return
        self.flashsr_available = True
        log_service.upscaling("FlashSR available (loads on first use)")

    async def preload(self):
        async with self.lock:
            if self.flashsr_loaded or not self.flashsr_available:
                return
            async with gpu_lease("FlashSR load"):
                await asyncio.to_thread(self._load_sync)
            self._mark_used()

    def _load_sync(self):
        import torch

        log_service.upscaling("Loading FlashSR super-resolution model...")
        repo = str(settings.FLASHSR_DIR)
        if repo not in sys.path:
            sys.path.insert(0, repo)

        from FlashSR.FlashSR import FlashSR  # type: ignore
        from FlashSR.AudioSR.AudioSRUnet import AudioSRUnet  # type: ignore
        from FlashSR.VAEWrapper import VAEWrapper  # type: ignore
        from FlashSR.SRVocoder import SRVocoder  # type: ignore
        from FlashSR.Util.UtilAudioLowPassFilter import UtilAudioLowPassFilter  # type: ignore
        from TorchJaekwon.Model.Diffusion.DDPM.DDPM import DDPM  # type: ignore
        from TorchJaekwon.Model.Diffusion.External.diffusers.DiffusersWrapper import DiffusersWrapper  # type: ignore
        from TorchJaekwon.Model.Diffusion.External.diffusers.schedulers.scheduling_dpmsolver_multistep import (  # type: ignore
            DPMSolverMultistepScheduler,
        )

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        if self.device == "cuda":
            log_service.upscaling(f"GPU: {torch.cuda.get_device_name(0)}")

        model = FlashSR.__new__(FlashSR)
        DDPM.__init__(model, model=AudioSRUnet(), model_output_type='v_prediction', beta_schedule_type='cosine')
        model.load_state_dict(torch.load(str(self.weights_dir / "student_ldm.pth"), map_location="cpu", weights_only=True))
        model.vae = VAEWrapper(str(self.weights_dir / "vae.pth"))
        model.sr_vocoder = SRVocoder()
        model.sr_vocoder.load_state_dict(
            torch.load(str(self.weights_dir / "sr_vocoder.pth"), map_location="cpu", weights_only=True)
        )
        model = model.to(self.device).eval()
        model.vae.to(self.device)
        for param in model.parameters():
            param.requires_grad = False

        self.model = model
        self._scheduler_cls = DPMSolverMultistepScheduler
        self._wrapper_cls = DiffusersWrapper
        self._lowpass_cls = UtilAudioLowPassFilter
        self.flashsr_loaded = True
        log_service.upscaling("FlashSR model loaded and ready")

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
        while self.flashsr_loaded:
            await asyncio.sleep(min(60.0, idle_seconds))
            if not self.flashsr_loaded or self.lock.locked():
                continue
            if time.monotonic() - self._last_used < idle_seconds:
                continue
            async with self.lock:
                if self.flashsr_loaded and time.monotonic() - self._last_used >= idle_seconds:
                    log_service.upscaling(f"FlashSR idle for {idle_seconds / 60:.0f} min, unloading")
                    await self.unload()

    def _infer_window(self, window: np.ndarray, seed: int, lowpass_hz: Optional[float]) -> np.ndarray:
        import torch

        if lowpass_hz is not None:
            window = self._lowpass_cls.lowpass(
                window, MODEL_SR, filter_name='cheby', filter_order=8, cutoff_freq=int(lowpass_hz)
            ).astype(np.float32)

        model = self.model
        devices = [torch.cuda.current_device()] if self.device == "cuda" else []
        with torch.random.fork_rng(devices=devices), torch.inference_mode():
            torch.manual_seed(seed)
            cond_audio = torch.from_numpy(np.ascontiguousarray(window, dtype=np.float32)).to(self.device)
            scheduler = self._scheduler_cls(
                **self._wrapper_cls.get_diffusers_scheduler_config(model, {'timestep_spacing': 'trailing'})
            )
            _, cond, extra = model.preprocess(x_start=None, cond=cond_audio)
            shape = tuple(model.get_x_shape(cond=cond))
            scheduler.set_timesteps(NUM_STEPS)
            noise = torch.randn((1,) + shape[1:], device=self.device).expand(shape).contiguous()
            latent = noise * scheduler.init_noise_sigma
            for step in scheduler.timesteps:
                model_input = scheduler.scale_model_input(latent, step)
                timestep = torch.full((shape[0],), step, device=self.device, dtype=torch.long)
                output = model.apply_model(model_input, timestep, cond, False, cfg_scale=model.cfg_scale)
                latent = scheduler.step(output, step, latent, return_dict=False)[0]
            audio = model.postprocess(latent, extra)[..., :window.shape[-1]]
            result = audio.float().cpu().numpy()
        del cond_audio, cond, extra, noise, latent, audio
        return result

    def _super_resolve(self, model_input: np.ndarray, lowpass_hz: Optional[float]) -> tuple[np.ndarray, int]:
        channels, length = model_input.shape
        overlap = int(round(settings.FLASHSR_OVERLAP_SECONDS * MODEL_SR))
        overlap = int(np.clip(overlap, 0, WINDOW_SAMPLES // 2))
        guard = min(int(round(0.1 * MODEL_SR)), max(0, (overlap - 1) // 4))
        head = _seam_head(overlap, guard)
        hop = WINDOW_SAMPLES - overlap

        accumulator = np.zeros((channels, length), dtype=np.float32)
        norm = np.zeros(length, dtype=np.float32)

        windows = 0
        offset = 0
        while offset < length:
            end = min(offset + WINDOW_SAMPLES, length)
            segment = model_input[:, offset:end]
            valid = segment.shape[1]
            if valid < WINDOW_SAMPLES:
                segment = np.pad(segment, ((0, 0), (0, WINDOW_SAMPLES - valid)))
            enhanced = self._infer_window(segment, SEED + windows, lowpass_hz)[:, :valid]
            weights = _seam_weights(valid, head, has_prev=offset > 0, has_next=end < length)
            accumulator[:, offset:end] += enhanced * weights
            norm[offset:end] += weights
            windows += 1
            if end >= length:
                break
            offset += hop

        return accumulator / np.maximum(norm, 1e-8), windows

    def _merge_bands(self, original: np.ndarray, generated: np.ndarray, crossover_hz: float) -> tuple[np.ndarray, float]:
        length = original.shape[-1]
        freqs = sp_fft.rfftfreq(length, 1.0 / OUTPUT_SR)
        original_spec = sp_fft.rfft(original, axis=-1, workers=4)
        generated_spec = sp_fft.rfft(generated, axis=-1, workers=4)

        below = _band_signal(original_spec, freqs, crossover_hz - GUARD_BAND_HZ, crossover_hz, length)
        above = _band_signal(generated_spec, freqs, crossover_hz, crossover_hz + GUARD_BAND_HZ, length)
        smooth = max(1, int(GUARD_SMOOTH_SECONDS * OUTPUT_SR))
        below_energy = uniform_filter1d(np.mean(below ** 2, axis=0), size=smooth, mode='nearest')
        above_energy = uniform_filter1d(np.mean(above ** 2, axis=0), size=smooth, mode='nearest')
        del below, above
        ceiling = 10.0 ** (settings.FLASHSR_HF_MAX_REL_DB / 10.0)
        gain = np.sqrt(np.minimum(1.0, ceiling * (below_energy + 1e-14) / (above_energy + 1e-14))).astype(np.float32)
        gain = uniform_filter1d(gain, size=max(1, smooth // 4), mode='nearest')

        mask = _crossover_mask(freqs, crossover_hz, settings.FLASHSR_CROSSOVER_WIDTH_HZ)
        low = sp_fft.irfft(original_spec * (1.0 - mask), n=length, axis=-1, workers=4).astype(np.float32)
        high = sp_fft.irfft(generated_spec * mask, n=length, axis=-1, workers=4).astype(np.float32)
        mean_gain_db = float(20.0 * np.log10(max(float(np.mean(gain)), 1e-6)))
        return low + high * gain, mean_gain_db

    def _process_audio_sync(self, input_path: Path, output_path: Path) -> tuple[Optional[Path], dict]:
        import librosa

        try:
            audio, source_rate = librosa.load(str(input_path), sr=None, mono=False)
            audio = np.atleast_2d(audio).astype(np.float32)
            original = _resample(audio, int(source_rate), OUTPUT_SR)
            length = original.shape[1]
            cutoff = detect_cutoff_hz(audio, int(source_rate))
            metadata = {
                "duration": length / OUTPUT_SR,
                "channels": original.shape[0],
                "source_rate": int(source_rate),
                "cutoff_hz": cutoff,
            }

            if cutoff >= settings.FLASHSR_SKIP_ABOVE_HZ:
                metadata["skipped"] = True
                write_float_wav(output_path, original, OUTPUT_SR)
                return output_path, metadata

            input_rate = int(settings.FLASHSR_INPUT_SR)
            crossover = float(np.clip(cutoff - CUTOFF_MARGIN_HZ, MIN_CROSSOVER_HZ, OUTPUT_SR / 2 - 1000.0))
            lowpass_hz = crossover if crossover < input_rate / 2 - 500.0 else None
            model_input = _resample(_resample(audio, int(source_rate), input_rate), input_rate, MODEL_SR)
            del audio

            generated48, windows = self._super_resolve(model_input, lowpass_hz)
            del model_input
            generated = _fit_length(_resample(generated48, MODEL_SR, OUTPUT_SR), length)
            del generated48

            if settings.FLASHSR_BAND_REPLACE:
                result, gain_db = self._merge_bands(original, generated, crossover)
            else:
                result, gain_db = generated, 0.0

            metadata.update({"crossover_hz": crossover, "windows": windows, "hf_gain_db": gain_db, "skipped": False})
            write_float_wav(output_path, result, OUTPUT_SR)

            del original, generated, result
            _clear_cuda_cache()
            return output_path, metadata

        except Exception as e:
            _clear_cuda_cache()
            raise_if_cuda_oom(e, "FlashSR")
            return None, {"error": str(e)}

    async def process_audio(self, input_path: Path, output_path: Path) -> Optional[Path]:
        async with self.lock:
            if not self.flashsr_available:
                log_service.error("FlashSR not available")
                return None

            if output_path.exists():
                log_service.upscaling(f"FlashSR WAV already exists: {output_path.name}")
                return output_path

            log_service.upscaling(f"FlashSR: Processing {input_path.name}")
            started = time.monotonic()

            async with gpu_lease("FlashSR"):
                if not self.flashsr_loaded:
                    try:
                        await asyncio.to_thread(self._load_sync)
                    except Exception as e:
                        log_service.error(f"FlashSR load failed: {e}")
                        _clear_cuda_cache()
                        raise_if_cuda_oom(e, "FlashSR load")
                        return None
                result, metadata = await asyncio.to_thread(self._process_audio_sync, input_path, output_path)
            self._mark_used()

            if not result:
                log_service.error(f"FlashSR processing failed: {metadata.get('error', 'Unknown error')}")
                return None

            if metadata.get("skipped"):
                log_service.upscaling(
                    f"FlashSR: source already full-band (cutoff {metadata['cutoff_hz'] / 1000:.1f} kHz), "
                    f"resampled only: {output_path.name}"
                )
                return result

            log_service.upscaling(
                f"  Duration: {metadata['duration']:.1f}s | Channels: {metadata['channels']} | "
                f"Cutoff: {metadata['cutoff_hz'] / 1000:.1f} kHz | Crossover: {metadata['crossover_hz'] / 1000:.1f} kHz | "
                f"Windows: {metadata['windows']} | HF gain: {metadata['hf_gain_db']:+.1f} dB | "
                f"{time.monotonic() - started:.1f}s"
            )
            log_service.upscaling(f"FlashSR complete: {output_path.name}")
            return result

    async def unload(self):
        if self.model is not None:
            vae = getattr(self.model, "vae", None)
            if vae is not None:
                vae.autoencoder = None
            del self.model
            self.model = None
        _clear_cuda_cache()
        self.flashsr_loaded = False
        log_service.upscaling("FlashSR model unloaded")
