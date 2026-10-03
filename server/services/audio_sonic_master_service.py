import sys
import asyncio
from pathlib import Path
import torch
import torchaudio
import yaml
import numpy as np
import gc
import time
from typing import Optional
from safetensors.torch import load_file
from diffusers.models.autoencoders.autoencoder_oobleck import AutoencoderOobleck  # type: ignore
from services import log_service
from services.base_service import SingletonService
from config import settings
from models_global import gpu_lease, raise_if_cuda_oom
from services.audio_headroom import spectrally_balanced_blend, write_float_wav

BASE_DIR = Path(__file__).parent.parent.parent
SONIC_MASTER_DIR = BASE_DIR / "SonicMaster"
SUNO_SONIC_SETTINGS = {
    "wet_mix": 0.25,
    "num_inference_steps": settings.SONIC_MASTER_STEPS,
    "prompt": settings.SONIC_MASTER_SUNO_PROMPT,
    "chunk_duration": 30,
    "fs": 44100,
}
VAE_HOP_SAMPLES = 2048
CONDITIONING_SECONDS = 10
PROMPT_TEMPLATES = (
    (("shine", "sparkle", "bright", "treble", "dull"), "Give the mix more shine and sparkle."),
    (("air", "open", "breath", "airy"), "Add more air and openness to the sound."),
    (("mud", "muddy", "low-mid", "boxy"), "Clean up the muddiness in the low-mids."),
    (("harsh", "sibilan", "piercing"), "Clean up the harshness in the signal."),
    (("vocal", "voice", "singer"), "Bring the vocals forward in the mix."),
    (("reverb", "echo", "roomy"), "Can you remove the excess reverb in this audio, please?"),
    (("compress", "squash", "dynamic", "decompress"), "Increase the dynamic range."),
    (("clip", "distort"), "Fix the digital distortion."),
    (("punch", "transient", "impact"), "Add more impact and dynamic punch to the sound."),
    (("bass", "low end", "low-end", "sub"), "Add weight and depth to the bottom end."),
    (("warm", "analog"), "Enhance the warmth for a fuller sound."),
    (("stereo", "wide", "width", "spacious", "narrow"), "Enhance the stereo field for a more immersive sound."),
    (("noise", "hiss"), "Clean up the noisiness in the audio."),
    (("clear", "clarity", "definition"), "Increase the clarity!"),
)


def template_prompt(prompt: str) -> str:
    known = {template.lower() for _keywords, template in PROMPT_TEMPLATES}
    if (prompt or "").strip().lower() in known:
        return prompt.strip()
    lowered = (prompt or "").lower()
    picked = [template for keywords, template in PROMPT_TEMPLATES if any(k in lowered for k in keywords)]
    if not picked:
        return "Improve the balance in this song."
    return " ".join(picked[:2])
sys.path.insert(0, str(SONIC_MASTER_DIR))

from model import TangoFlux  # noqa: E402  # type: ignore

def _clear_cuda_cache():
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    gc.collect()


class SonicMasterService(SingletonService):

    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.checkpoint_path = str(
            SONIC_MASTER_DIR / "models--amaai-lab--SonicMaster" / "snapshots" /
            "9e2721cd1b71dd56fd8c016e8a6f4cb6d34861b1" / "model.safetensors"
        )
        self.config_path = str(SONIC_MASTER_DIR / "configs" / "tangoflux_config.yaml")
        self.wet_mix = 0.4
        self.num_inference_steps = 50
        self.prompt = "give the mix more breath and depth, a clean and dynamic mix with rich full harmonics"
        self.chunk_duration = 30
        self.fs = 44100
        self.precision = settings.SONIC_MASTER_PRECISION
        self.device = None

        self.model = None
        self.vae = None
        self.lock = asyncio.Lock()
        self.sonic_loaded = False
        self.sonic_available = False
        self._last_used = time.monotonic()
        self._idle_task: Optional[asyncio.Task] = None
        self._initialized = True

    def configure(
            self,
            wet_mix: float = None,  # type: ignore
            num_inference_steps: int = None,  # type: ignore
            prompt: str = None,  # type: ignore
            chunk_duration: int = None,  # type: ignore
            fs: int = None,  # type: ignore
            precision: str = None  # type: ignore
    ):
        if precision is not None and precision != self.precision:
            self.precision = precision
            if self.sonic_loaded:
                self.model = None
                self.vae = None
                self.sonic_loaded = False
                _clear_cuda_cache()
        if wet_mix is not None:
            self.wet_mix = wet_mix
        if num_inference_steps is not None:
            self.num_inference_steps = num_inference_steps
        if prompt is not None:
            self.prompt = prompt
        if chunk_duration is not None:
            self.chunk_duration = chunk_duration
        if fs is not None:
            self.fs = fs

    async def initialize(self):
        if self.sonic_loaded:
            log_service.upscaling("SonicMaster already loaded")
            return

        async with self.lock:
            if self.sonic_loaded:
                return
            async with gpu_lease("SonicMaster load"):
                await asyncio.to_thread(self._load_sync)
            self._mark_used()

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
        while self.sonic_loaded:
            await asyncio.sleep(min(60.0, idle_seconds))
            if not self.sonic_loaded or self.lock.locked():
                continue
            if time.monotonic() - self._last_used < idle_seconds:
                continue
            async with self.lock:
                if self.sonic_loaded and time.monotonic() - self._last_used >= idle_seconds:
                    log_service.upscaling(f"SonicMaster idle for {idle_seconds / 60:.0f} min, unloading")
                    await self.unload()

    def _load_sync(self):
        log_service.upscaling("Loading SonicMaster audio enhancement model...")

        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        if torch.cuda.is_available():
            log_service.upscaling(f"GPU: {torch.cuda.get_device_name(0)}")
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True

        with open(self.config_path, "r", encoding='utf-8') as f:
            cfg = yaml.safe_load(f)

        log_service.upscaling("SonicMaster: Loading TangoFlux model...")
        self.model = TangoFlux(config=cfg["model"])

        ckpt_path = Path(self.checkpoint_path)
        if ckpt_path.is_dir():
            ckpt_path = ckpt_path / "model.safetensors"

        self.model.load_state_dict(load_file(str(ckpt_path)), strict=False)
        self.model.to(self.device).eval()
        if self._half:
            self.model.half()

        for p in self.model.text_encoder.parameters():
            p.requires_grad = False
        self.model.text_encoder.eval()

        log_service.upscaling("SonicMaster: Loading VAE...")
        vae_device = self.device if self.device is not None else "cpu"
        self.vae = AutoencoderOobleck.from_pretrained(
            "stabilityai/stable-audio-open-1.0",
            subfolder="vae"
        ).to(vae_device)  # type: ignore
        if self._half:
            self.vae.half()
        self.vae.eval()
        log_service.upscaling(f"SonicMaster precision: {'fp16' if self._half else 'fp32'}")

        self.sonic_loaded = True
        self.sonic_available = True
        log_service.upscaling("SonicMaster model loaded and ready")

    @property
    def _half(self) -> bool:
        return self.precision == "fp16" and self.device == "cuda"

    def _match_rms(self, source: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        source_rms = torch.sqrt(torch.mean(source ** 2))
        target_rms = torch.sqrt(torch.mean(target ** 2))

        if source_rms > 1e-6:
            scale = target_rms / source_rms
            return source * scale
        return source

    def _enhance_audio_sync(self, input_path: Path, output_path: Path, prompt: str, wet_mix: float) -> bool:
        try:
            audio, sr = torchaudio.load(str(input_path))

            if audio.shape[0] == 1:
                audio = audio.repeat(2, 1)
            elif audio.shape[0] > 2:
                audio = audio[:2, :]

            if sr != self.fs:
                audio = torchaudio.functional.resample(audio, sr, self.fs)

            original_audio = audio.clone()
            total_samples = audio.shape[1]

            chunk_samples = self.chunk_duration * self.fs
            if settings.SONIC_MASTER_ALIGN_CHUNKS:
                chunk_samples = (chunk_samples // VAE_HOP_SAMPLES) * VAE_HOP_SAMPLES
            overlap_samples = min(int(settings.SONIC_MASTER_CHUNK_OVERLAP_S * self.fs), chunk_samples // 2)
            stride_samples = chunk_samples - overlap_samples
            model_dtype = torch.float16 if self._half else torch.float32
            conditioning_samples = min(CONDITIONING_SECONDS * self.fs, chunk_samples)
            prev_cond_latent = None

            output_buffer = torch.zeros_like(audio)
            weight_buffer = torch.zeros_like(audio)

            if overlap_samples * 2 >= chunk_samples:
                window = torch.hann_window(chunk_samples)
            else:
                ramp = 0.5 * (1 - torch.cos(torch.pi * (torch.arange(overlap_samples) + 0.5) / overlap_samples))
                window = torch.ones(chunk_samples)
                window[:overlap_samples] = ramp
                window[-overlap_samples:] = ramp.flip(0)
            window = window.to(audio.device).unsqueeze(0).repeat(2, 1)

            num_chunks = int(np.ceil(total_samples / stride_samples))

            log_service.upscaling(f"SonicMaster: Processing {num_chunks} chunks, {overlap_samples / self.fs:.1f}s crossfade")

            for chunk_idx in range(num_chunks):
                start_idx = chunk_idx * stride_samples
                end_idx = start_idx + chunk_samples

                input_slice = audio[:, start_idx:end_idx]

                current_slice_len = input_slice.shape[1]
                pad_amount = chunk_samples - current_slice_len

                if pad_amount > 0:
                    chunk = torch.nn.functional.pad(input_slice, (0, pad_amount))
                else:
                    chunk = input_slice

                chunk_gpu = chunk.unsqueeze(0).to(self.device, dtype=model_dtype)

                with torch.no_grad():
                    if self.vae is None:
                        raise RuntimeError("VAE not initialized")
                    z = self.vae.encode(chunk_gpu).latent_dist.mode()  # type: ignore

                z_in = z.transpose(1, 2)

                with torch.no_grad():
                    if self.model is None:
                        raise RuntimeError("Model not initialized")
                    result_latent = self.model.inference_flow(
                        z_in,
                        prompt,
                        audiocond_latents=prev_cond_latent,
                        num_inference_steps=self.num_inference_steps,
                        timesteps=None,
                        guidance_scale=1.0,
                        duration=self.chunk_duration,
                        seed=0,
                        disable_progress=True,
                        num_samples_per_prompt=1,
                        callback_on_step_end=None,
                        solver="Euler",
                    )

                    decoded = self.vae.decode(result_latent.transpose(2, 1)).sample  # type: ignore
                    if settings.SONIC_MASTER_CHUNK_CONDITIONING:
                        tail = decoded[:, :, -conditioning_samples:]
                        prev_cond_latent = self.vae.encode(tail).latent_dist.mode().transpose(1, 2)  # type: ignore
                    wav = decoded.float().cpu()
                    del decoded

                wav = torch.nan_to_num(wav.squeeze(0), nan=0.0, posinf=0.0, neginf=0.0)

                valid_end_idx = min(end_idx, total_samples)
                dest_slice = output_buffer[:, start_idx:valid_end_idx]
                actual_dest_len = dest_slice.shape[1]

                wav_len = wav.shape[1]

                if wav_len < actual_dest_len:
                    diff = actual_dest_len - wav_len
                    wav_fitted = torch.nn.functional.pad(wav, (0, diff))
                elif wav_len > actual_dest_len:
                    wav_fitted = wav[:, :actual_dest_len]
                else:
                    wav_fitted = wav

                ref_slice = input_slice[:, :actual_dest_len]

                if settings.SONIC_MASTER_CHUNK_RMS_MATCH and ref_slice.shape[1] == wav_fitted.shape[1]:
                    wav_fitted = self._match_rms(wav_fitted, ref_slice)

                win_cropped = window[:, :actual_dest_len]

                try:
                    dest_slice += wav_fitted * win_cropped
                    weight_buffer[:, start_idx:valid_end_idx] += win_cropped
                except RuntimeError as e:
                    log_service.error(f"SonicMaster: Stitching error at chunk {chunk_idx + 1}/{num_chunks}")
                    raise e

                del chunk_gpu, z, z_in, result_latent, wav, wav_fitted, win_cropped
                _clear_cuda_cache()

            mask = weight_buffer > 1e-6
            output_buffer[mask] /= weight_buffer[mask]
            output_buffer[~mask] = original_audio[~mask]

            output_buffer = self._match_rms(output_buffer, original_audio)

            final, blend_info = spectrally_balanced_blend(
                output_buffer.numpy(), original_audio.numpy(), wet_mix, self.fs,
                max_boost_db=3.5 if settings.SONIC_MASTER_BLEND_COMPENSATION else 0.0
            )
            if blend_info["compensation_max_db"] > 0.05:
                log_service.upscaling(
                    f"SonicMaster: Blend power compensation up to {blend_info['compensation_max_db']:+.1f}dB "
                    f"(HF avg {blend_info['compensation_hf_db']:+.1f}dB)"
                )

            write_float_wav(output_path, final, self.fs)

            del audio, original_audio, final, output_buffer, weight_buffer, window
            _clear_cuda_cache()

            return True

        except Exception as e:
            log_service.error(f"SonicMaster processing error: {e}")
            _clear_cuda_cache()
            raise_if_cuda_oom(e, "SonicMaster")
            return False

    async def enhance_audio(
            self,
            input_path: Path,
            output_path: Path,
            prompt: Optional[str] = None,
            wet_mix: Optional[float] = None
    ) -> bool:
        call_prompt = prompt or self.prompt
        if settings.SONIC_MASTER_TEMPLATE_PROMPTS:
            call_prompt = template_prompt(call_prompt)
        call_wet_mix = float(np.clip(self.wet_mix if wet_mix is None else wet_mix, 0.0, 1.0))

        async with self.lock:
            if not self.sonic_loaded and not self.sonic_available:
                log_service.error("SonicMaster model not loaded")
                return False

            log_service.upscaling(f"SonicMaster: Enhancing {input_path.name}")

            async with gpu_lease("SonicMaster"):
                if not self.sonic_loaded:
                    try:
                        await asyncio.to_thread(self._load_sync)
                    except Exception as e:
                        log_service.error(f"SonicMaster reload failed: {e}")
                        _clear_cuda_cache()
                        raise_if_cuda_oom(e, "SonicMaster load")
                        return False
                result = await asyncio.to_thread(
                    self._enhance_audio_sync, input_path, output_path, call_prompt, call_wet_mix
                )
            self._mark_used()

            if result:
                log_service.upscaling(f"SonicMaster complete: {output_path.name}")
            else:
                log_service.error("SonicMaster processing failed")

            _clear_cuda_cache()
            return result

    async def unload(self):
        if self.model is not None:
            del self.model
            self.model = None

        if self.vae is not None:
            del self.vae
            self.vae = None

        _clear_cuda_cache()
        self.sonic_loaded = False
        log_service.upscaling("SonicMaster model unloaded")