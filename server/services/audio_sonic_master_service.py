import sys
import asyncio
from pathlib import Path
import torch
import torchaudio
import soundfile as sf
import yaml
import numpy as np
import gc
from safetensors.torch import load_file
from diffusers.models.autoencoders.autoencoder_oobleck import AutoencoderOobleck  # type: ignore
from services import log_service
from services.base_service import SingletonService

BASE_DIR = Path(__file__).parent.parent.parent
SONIC_MASTER_DIR = BASE_DIR / "SonicMaster"
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
        self.device = None

        self.model = None
        self.vae = None
        self.lock = asyncio.Lock()
        self.sonic_loaded = False
        self._initialized = True

    def configure(
            self,
            wet_mix: float = None,  # type: ignore
            num_inference_steps: int = None,  # type: ignore
            prompt: str = None,  # type: ignore
            chunk_duration: int = None,  # type: ignore
            fs: int = None  # type: ignore
    ):
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
        self.model.to(self.device).eval().half()

        for p in self.model.text_encoder.parameters():
            p.requires_grad = False
        self.model.text_encoder.eval()

        log_service.upscaling("SonicMaster: Loading VAE...")
        vae_device = self.device if self.device is not None else "cpu"
        self.vae = AutoencoderOobleck.from_pretrained(
            "stabilityai/stable-audio-open-1.0",
            subfolder="vae"
        ).to(vae_device).half()  # type: ignore
        self.vae.eval()

        self.sonic_loaded = True
        log_service.upscaling("SonicMaster model loaded and ready")

    def _match_rms(self, source: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
        source_rms = torch.sqrt(torch.mean(source ** 2))
        target_rms = torch.sqrt(torch.mean(target ** 2))

        if source_rms > 1e-6:
            scale = target_rms / source_rms
            return source * scale
        return source

    def _enhance_audio_sync(self, input_path: Path, output_path: Path) -> bool:
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
            stride_samples = chunk_samples // 2

            output_buffer = torch.zeros_like(audio)
            weight_buffer = torch.zeros_like(audio)

            window = torch.hann_window(chunk_samples).to(audio.device)
            window = window.unsqueeze(0).repeat(2, 1)

            num_chunks = int(np.ceil(total_samples / stride_samples))

            log_service.upscaling(f"SonicMaster: Processing {num_chunks} chunks with 50% overlap...")

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

                chunk_gpu = chunk.unsqueeze(0).to(self.device).half()

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
                        self.prompt,
                        audiocond_latents=None,
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

                    wav = self.vae.decode(result_latent.transpose(2, 1)).sample.float().cpu()  # type: ignore

                wav = wav.squeeze(0)
                wav = torch.clamp(wav, -1.0, 1.0)

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

                if ref_slice.shape[1] == wav_fitted.shape[1]:
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

            if self.wet_mix < 1.0:
                final = self.wet_mix * output_buffer + (1.0 - self.wet_mix) * original_audio
            else:
                final = output_buffer

            output_path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(str(output_path), final.numpy().T, self.fs, format="WAV")

            del audio, original_audio, final, output_buffer, weight_buffer, window
            _clear_cuda_cache()

            return True

        except Exception as e:
            log_service.error(f"SonicMaster processing error: {e}")
            _clear_cuda_cache()
            return False

    async def enhance_audio(self, input_path: Path, output_path: Path) -> bool:
        async with self.lock:
            if not self.sonic_loaded:
                log_service.error("SonicMaster model not loaded")
                return False

            log_service.upscaling(f"SonicMaster: Enhancing {input_path.name}")

            result = await asyncio.to_thread(self._enhance_audio_sync, input_path, output_path)

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