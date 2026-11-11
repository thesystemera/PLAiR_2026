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
from services.audio_headroom import frequency_blend, write_float_wav

BASE_DIR = Path(__file__).parent.parent.parent
SONIC_MASTER_DIR = BASE_DIR / "SonicMaster"
SUNO_SONIC_SETTINGS = {
    "wet_mix": 0.5,
    "num_inference_steps": settings.SONIC_MASTER_STEPS,
    "prompt": settings.SONIC_MASTER_SUNO_PROMPT,
    "chunk_duration": 30,
    "fs": 44100,
}
VAE_HOP_SAMPLES = 2048
FAST_FP16_MIN_CAPABILITY = 7
WINDOW_WEIGHT_FLOOR = 1e-4
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


def _vae_tiles(frames: int, rate: int):
    tile = max(1, int(settings.SONIC_MASTER_VAE_TILE_S * rate / VAE_HOP_SAMPLES)) if settings.SONIC_MASTER_VAE_TILE_S > 0 else frames
    margin = int(settings.SONIC_MASTER_VAE_MARGIN_S * rate / VAE_HOP_SAMPLES)
    for start in range(0, frames, tile):
        yield start, tile, max(0, start - margin), start + tile + margin


def vae_encode_tiled(vae, audio: torch.Tensor, rate: int) -> torch.Tensor:
    frames = -(-audio.shape[-1] // VAE_HOP_SAMPLES)
    parts = []
    for start, tile, lo, hi in _vae_tiles(frames, rate):
        z = vae.encode(audio[..., lo * VAE_HOP_SAMPLES:hi * VAE_HOP_SAMPLES]).latent_dist.mode()
        parts.append(z[..., start - lo:start - lo + tile])
    return torch.cat(parts, dim=-1)


def vae_decode_tiled(vae, latent: torch.Tensor, rate: int) -> torch.Tensor:
    parts = []
    for start, tile, lo, hi in _vae_tiles(latent.shape[-1], rate):
        wav = vae.decode(latent[..., lo:hi]).sample
        parts.append(wav[..., (start - lo) * VAE_HOP_SAMPLES:(start - lo + tile) * VAE_HOP_SAMPLES])
    return torch.cat(parts, dim=-1)


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
                await asyncio.to_thread(self._park)
            self._mark_used()

    def _place(self, device: str):
        if self.model is not None:
            self.model.to(device)
        if self.vae is not None:
            self.vae.to(device)

    def _park(self):
        if settings.SONIC_MASTER_PARK_ON_CPU and self.device == "cuda":
            self._place("cpu")
            _clear_cuda_cache()

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
        if self.device != "cuda":
            return False
        if self.precision == "auto":
            return torch.cuda.get_device_capability()[0] >= FAST_FP16_MIN_CAPABILITY
        return self.precision == "fp16"

    def _match_rms(self, source: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        source_rms = torch.sqrt(torch.mean(source ** 2))
        target_rms = torch.sqrt(torch.mean(target ** 2))

        if source_rms > 1e-6:
            scale = target_rms / source_rms
            return source * scale
        return source

    @torch.no_grad()
    def _flow_whole_song(self, latents: torch.Tensor, prompt: str) -> tuple[torch.Tensor, int]:
        model = self.model
        if model is None:
            raise RuntimeError("Model not initialized")
        device = model.transformer.device
        length, total = model.audio_seq_len, latents.shape[1]
        last = max(total - length, 0)
        starts = list(range(0, last + 1, length // 2))
        if starts[-1] != last:
            starts.append(last)

        states, mask = model.encode_text([prompt])
        masked = torch.where(mask.unsqueeze(-1).expand_as(states), states,
                             torch.full((), float("nan"), device=states.device, dtype=states.dtype))
        pooled_text = model.fc_text(torch.nanmean(masked, dim=1))
        pooled = model.fc_text_audio(torch.cat([pooled_text, torch.zeros_like(pooled_text)], dim=1))
        states = torch.cat([states, model.encode_duration(torch.tensor([self.chunk_duration], device=device))], dim=1)
        text_ids = torch.zeros(states.shape[1], 3, device=device)
        audio_ids = torch.arange(length).unsqueeze(-1).repeat(1, 3).to(device)
        weight = (torch.hann_window(length, device=device) + WINDOW_WEIGHT_FLOOR).view(1, length, 1).to(latents.dtype)

        timesteps = torch.linspace(1.0, 0.0, self.num_inference_steps + 1)
        step = float(timesteps[0] - timesteps[1])
        for t in timesteps[:-1]:
            velocity = torch.zeros_like(latents)
            cover = torch.zeros(1, total, 1, device=device, dtype=latents.dtype)
            for start in starts:
                predicted = model.transformer(
                    hidden_states=latents[:, start:start + length],
                    timestep=t.reshape(1).to(device),
                    guidance=None,
                    pooled_projections=pooled,
                    encoder_hidden_states=states,
                    txt_ids=text_ids,
                    img_ids=audio_ids,
                    return_dict=False,
                )[0]
                velocity[:, start:start + length] += predicted * weight
                cover[:, start:start + length] += weight
            latents = latents + step * velocity / cover
        return latents, len(starts)

    def _match_level_windows(self, wet: torch.Tensor, dry: torch.Tensor) -> torch.Tensor:
        size = self.chunk_duration * self.fs
        weight = torch.hann_window(size) + WINDOW_WEIGHT_FLOOR
        gain, cover = torch.zeros(wet.shape[1]), torch.zeros(wet.shape[1])
        for start in range(0, wet.shape[1], size // 2):
            w, d = wet[:, start:start + size], dry[:, start:start + size]
            power = float((w ** 2).mean())
            level = float(torch.sqrt((d ** 2).mean() / power)) if power > 1e-12 else 1.0
            gain[start:start + w.shape[1]] += level * weight[:w.shape[1]]
            cover[start:start + w.shape[1]] += weight[:w.shape[1]]
        return wet * (gain / cover)

    def _enhance_audio_sync(self, input_path: Path, output_path: Path, prompt: str, wet_mix: float) -> bool:
        try:
            audio, sr = torchaudio.load(str(input_path))

            if audio.shape[0] == 1:
                audio = audio.repeat(2, 1)
            elif audio.shape[0] > 2:
                audio = audio[:2, :]

            if sr != self.fs:
                audio = torchaudio.functional.resample(audio, sr, self.fs)

            if self.model is None or self.vae is None:
                raise RuntimeError("SonicMaster not initialized")
            total_samples = audio.shape[1]
            window_samples = self.model.audio_seq_len * VAE_HOP_SAMPLES
            padded_samples = max(-(-total_samples // VAE_HOP_SAMPLES) * VAE_HOP_SAMPLES, window_samples)
            padded = torch.nn.functional.pad(audio, (0, padded_samples - total_samples))
            model_dtype = torch.float16 if self._half else torch.float32

            with torch.no_grad():
                z = vae_encode_tiled(self.vae, padded.unsqueeze(0).to(self.device, dtype=model_dtype), self.fs)
                latents, windows = self._flow_whole_song(z.transpose(1, 2), prompt)
                wav = vae_decode_tiled(self.vae, latents.transpose(2, 1), self.fs).float().cpu().squeeze(0)[:, :total_samples]
                del z, latents

            log_service.upscaling(f"SonicMaster: {windows} windows of {self.chunk_duration} s blended in the sampler, decoded once")
            wav = torch.nan_to_num(wav, nan=0.0, posinf=0.0, neginf=0.0)
            output = self._match_rms(self._match_level_windows(wav, audio), audio)

            final = frequency_blend(output.numpy(), audio.numpy(), wet_mix, self.fs)
            log_service.upscaling(f"SonicMaster: {wet_mix:.0%} wet, blended frequency by frequency with its input")

            write_float_wav(output_path, final, self.fs)

            del audio, padded, wav, output, final
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
                _clear_cuda_cache()
                if not self.sonic_loaded:
                    try:
                        await asyncio.to_thread(self._load_sync)
                    except Exception as e:
                        log_service.error(f"SonicMaster reload failed: {e}")
                        _clear_cuda_cache()
                        raise_if_cuda_oom(e, "SonicMaster load")
                        return False
                if self.device == "cuda":
                    await asyncio.to_thread(self._place, "cuda")
                try:
                    result = await asyncio.to_thread(
                        self._enhance_audio_sync, input_path, output_path, call_prompt, call_wet_mix
                    )
                finally:
                    await asyncio.to_thread(self._park)
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