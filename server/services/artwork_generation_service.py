import asyncio
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any
import torch
from PIL import Image
from services import log_service
from services.base_service import SingletonService
from config import settings
from models_global import gpu_lease, raise_if_cuda_oom, GPUOutOfMemoryError

Image.MAX_IMAGE_PIXELS = 40_000_000

class ArtworkGenerationService(SingletonService):

    def __init__(self):
        super().__init__()
        if getattr(self, '_initialized', False):
            return

        self.artwork_dir = settings.ARTWORK_DIR
        self._pipe = None
        self._img2img_pipe = None
        self._device = None
        self._model_initialized = False
        self._load_lock = threading.RLock()
        self._use_lock = asyncio.Lock()
        self._last_used = time.monotonic()
        self._idle_task: Optional[asyncio.Task] = None
        self._initialized = True

    async def initialize(self):
        if self._model_initialized:
            log_service.system("ArtworkGenerationService already initialized")
            return

        log_service.system("ArtworkGenerationService initialized (model loads on-demand)")
        self._model_initialized = True

    def _mark_used(self):
        self._last_used = time.monotonic()
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
        while self._pipe is not None:
            await asyncio.sleep(min(60.0, idle_seconds))
            if self._pipe is None or self._use_lock.locked():
                continue
            if time.monotonic() - self._last_used < idle_seconds:
                continue
            async with self._use_lock:
                if self._pipe is not None and time.monotonic() - self._last_used >= idle_seconds:
                    log_service.system(f"SDXL idle for {idle_seconds / 60:.0f} min, unloading")
                    await asyncio.to_thread(self._unload_sync)

    def _unload_sync(self):
        with self._load_lock:
            self._img2img_pipe = None
            self._pipe = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        import gc
        gc.collect()

    async def unload(self):
        async with self._use_lock:
            await asyncio.to_thread(self._unload_sync)

    def _load_model(self):
        with self._load_lock:
            if self._pipe is not None:
                return
            self._load_model_locked()

    def _load_model_locked(self):
        try:
            log_service.system("Loading SDXL Lightning 4-step...")

            self._device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
            log_service.system(f"Using device: {self._device}")

            if self._device.type != 'cuda':
                log_service.warning("CUDA not available - image generation will be slow")

            from diffusers.pipelines.stable_diffusion_xl.pipeline_stable_diffusion_xl import StableDiffusionXLPipeline  # type: ignore
            from diffusers.models.unets.unet_2d_condition import UNet2DConditionModel  # type: ignore
            from diffusers.schedulers.scheduling_euler_discrete import EulerDiscreteScheduler  # type: ignore
            from huggingface_hub import hf_hub_download
            from safetensors.torch import load_file

            base_model = "stabilityai/stable-diffusion-xl-base-1.0"
            lightning_repo = "ByteDance/SDXL-Lightning"
            checkpoint = "sdxl_lightning_4step_unet.safetensors"

            log_service.info("Loading UNet from SDXL Lightning checkpoint...")

            unet_config = UNet2DConditionModel.load_config(base_model, subfolder="unet")
            unet: Any = UNet2DConditionModel.from_config(unet_config)  # type: ignore

            ckpt_path = hf_hub_download(lightning_repo, checkpoint)
            unet.load_state_dict(load_file(ckpt_path, device="cpu"))
            unet = unet.to(self._device, torch.float16)

            log_service.info("Loading SDXL base pipeline...")

            self._pipe = StableDiffusionXLPipeline.from_pretrained(
                base_model,
                unet=unet,
                torch_dtype=torch.float16,
                variant="fp16"
            ).to(self._device)

            self._pipe.scheduler = EulerDiscreteScheduler.from_config(
                self._pipe.scheduler.config,
                timestep_spacing="trailing"
            )

            if hasattr(self._pipe, 'enable_attention_slicing'):
                self._pipe.enable_attention_slicing()

            log_service.success(f"SDXL Lightning text-to-image loaded on {self._device}")

        except ImportError as e:
            log_service.error(
                f"Missing dependencies for SDXL Lightning: {str(e)}\n"
                f"Install with: pip install diffusers transformers accelerate safetensors"
            )
            self._pipe = None
            raise

        except Exception as e:
            log_service.error(f"Failed to load SDXL Lightning: {str(e)}")
            self._pipe = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            raise

    def _load_img2img_model(self):
        with self._load_lock:
            if self._img2img_pipe is not None:
                return

            self._load_model()

            try:
                from diffusers.pipelines.stable_diffusion_xl.pipeline_stable_diffusion_xl_img2img import StableDiffusionXLImg2ImgPipeline

                log_service.system("Building SDXL Lightning img2img pipeline from shared components...")
                assert self._pipe is not None
                self._img2img_pipe = StableDiffusionXLImg2ImgPipeline(**self._pipe.components)

                log_service.success(f"SDXL Lightning img2img ready on {self._device}")

            except Exception as e:
                log_service.error(f"Failed to build SDXL Lightning img2img: {str(e)}")
                self._img2img_pipe = None
                raise

    def _get_negative_prompt(self) -> str:
        return (
            "text, words, letters, watermark, signature, "
            "blurry, low quality, pixelated, distorted, "
            "ugly, deformed, disfigured, "
            "photo frame, border, margin, "
            "nsfw, nude, explicit"
        )

    def _truncate_prompt(self, prompt: str, max_words: int = 45) -> str:
        words = prompt.split()
        if len(words) <= max_words:
            return prompt

        truncated = ' '.join(words[:max_words])
        log_service.warning(f"Artwork prompt truncated: {len(words)} words -> {max_words} words")
        return truncated

    async def generate_artwork(
        self,
        track_id: str,
        artwork_prompt: str,
        title: str = "Untitled",
        artist: str = "Unknown Artist",
        seed: Optional[int] = None,
        quality: int = 90
    ) -> Optional[Path]:

        artwork_path = self.artwork_dir / f"{track_id}.jpeg"

        if artwork_path.exists():
            log_service.info(f"Artwork already exists: {track_id}")
            return artwork_path

        if not artwork_prompt:
            log_service.error(f"No artwork_prompt provided for {track_id} - cannot generate artwork")
            return None

        prompt = self._truncate_prompt(artwork_prompt)
        negative_prompt = self._get_negative_prompt()

        try:
            async with self._use_lock:
                async with gpu_lease("SDXL"):
                    await asyncio.to_thread(self._load_model)

                    if self._pipe is None:
                        log_service.error("Model not loaded, cannot generate artwork")
                        return None

                    log_service.info(f"Generating artwork for: {title} by {artist}")

                    generator = None
                    if seed is not None:
                        generator = torch.Generator(device=self._device).manual_seed(seed)

                    def generate():
                        with torch.no_grad():
                            if self._pipe is None:
                                raise RuntimeError("Pipeline not initialized")
                            result = self._pipe(
                                prompt=prompt,
                                negative_prompt=negative_prompt,
                                num_inference_steps=4,
                                guidance_scale=0,
                                generator=generator,
                                width=1024,
                                height=1024
                            )
                            return result.images[0]  # type: ignore

                    image = await asyncio.to_thread(generate)
                self._mark_used()

            def save():
                image.save(str(artwork_path), 'JPEG', quality=quality, optimize=True)

            await asyncio.to_thread(save)

            log_service.success(f"Generated artwork: {track_id} (1024x1024)")

            return artwork_path

        except GPUOutOfMemoryError:
            raise
        except Exception as e:
            log_service.error(f"Failed to generate artwork for {track_id}: {str(e)}")
            raise_if_cuda_oom(e, "SDXL")
            return None

    async def generate_artwork_for_track(
        self,
        track_id: str,
        metadata: Dict[str, Any],
        quality: int = 90
    ) -> Optional[Path]:

        artwork_prompt = metadata.get('artwork_prompt')
        if not artwork_prompt:
            log_service.error(f"No artwork_prompt in metadata for {track_id}")
            return None

        return await self.generate_artwork(
            track_id=track_id,
            artwork_prompt=artwork_prompt,
            title=metadata.get('title', 'Untitled'),
            artist=metadata.get('primary_artist', 'Unknown Artist'),
            quality=quality
        )

    async def upscale_artwork(
        self,
        track_id: str,
        source_image_path: Path,
        prompt: Optional[str] = None,
        strength: float = 0.35,
        seed: Optional[int] = None,
        quality: int = 90,
        output_size: int = 1024
    ) -> Optional[Path]:
        """
        Upscale/reinterpret artwork using SDXL Lightning img2img.

        Args:
            track_id: Track ID for output filename
            source_image_path: Path to the source image (e.g., Suno 320x320)
            prompt: Optional prompt to guide the upscale (uses generic if not provided)
            strength: How much to change the image (0.0 = no change, 1.0 = complete reimagine)
                     0.25-0.40 recommended for upscaling while preserving composition
            seed: Optional seed for reproducibility
            quality: JPEG quality (1-100)
            output_size: Output resolution (default 1024x1024)

        Returns:
            Path to the upscaled artwork, or None on failure
        """
        artwork_path = self.artwork_dir / f"{track_id}.jpeg"

        if not source_image_path.exists():
            log_service.error(f"Source image not found: {source_image_path}")
            return None

        try:
            def load_and_prepare_image():
                img = Image.open(source_image_path).convert("RGB")
                return img.resize((output_size, output_size), Image.Resampling.LANCZOS)

            source_image = await asyncio.to_thread(load_and_prepare_image)

            if not prompt:
                prompt = "high quality album cover art, detailed, professional, vibrant colors"

            prompt = self._truncate_prompt(prompt)
            negative_prompt = self._get_negative_prompt()

            async with self._use_lock:
                async with gpu_lease("SDXL img2img"):
                    await asyncio.to_thread(self._load_img2img_model)

                    if self._img2img_pipe is None:
                        log_service.error("img2img model not loaded, cannot upscale artwork")
                        return None

                    log_service.info(f"Upscaling artwork for {track_id} (strength={strength})")

                    generator = None
                    if seed is not None:
                        generator = torch.Generator(device=self._device).manual_seed(seed)

                    def upscale():
                        with torch.no_grad():
                            if self._img2img_pipe is None:
                                raise RuntimeError("img2img pipeline not initialized")
                            result = self._img2img_pipe(
                                prompt=prompt,
                                negative_prompt=negative_prompt,
                                image=source_image,
                                strength=strength,
                                num_inference_steps=4,
                                guidance_scale=0,
                                generator=generator,
                            )
                            return result.images[0]

                    upscaled_image = await asyncio.to_thread(upscale)
                self._mark_used()

            def save():
                upscaled_image.save(str(artwork_path), 'JPEG', quality=quality, optimize=True)

            await asyncio.to_thread(save)

            log_service.success(f"Upscaled artwork: {track_id} ({output_size}x{output_size})")

            return artwork_path

        except GPUOutOfMemoryError:
            raise
        except Exception as e:
            log_service.error(f"Failed to upscale artwork for {track_id}: {str(e)}")
            raise_if_cuda_oom(e, "SDXL img2img")
            return None

    async def batch_upscale_suno_artwork(
        self,
        track_ids: list,
        strength: float = 0.35,
        quality: int = 90,
        max_concurrent: int = 1
    ) -> Dict[str, Any]:
        """
        Batch upscale Suno artwork for multiple tracks.

        Args:
            track_ids: List of track IDs to process
            strength: img2img strength (0.25-0.40 recommended)
            quality: JPEG quality
            max_concurrent: Max concurrent operations (1 recommended for GPU memory)

        Returns:
            Stats dict with success/failed/skipped counts
        """
        import asyncio

        stats = {
            "total": len(track_ids),
            "success": 0,
            "failed": 0,
            "skipped": 0
        }

        sem = asyncio.Semaphore(max_concurrent)

        async def process_track(track_id: str) -> None:
            async with sem:
                source_path = self.artwork_dir / f"{track_id}.jpeg"

                if not source_path.exists():
                    jpg_path = self.artwork_dir / f"{track_id}.jpg"
                    if jpg_path.exists():
                        source_path = jpg_path
                    else:
                        log_service.warning(f"No artwork found for {track_id}")
                        stats["skipped"] += 1
                        return

                try:
                    img = Image.open(source_path)
                    width, height = img.size
                    img.close()

                    if width >= 1024 and height >= 1024:
                        log_service.info(f"Skipping {track_id} - already {width}x{height}")
                        stats["skipped"] += 1
                        return

                except Exception as e:
                    log_service.error(f"Failed to read {track_id}: {e}")
                    stats["failed"] += 1
                    return

                backup_path = self.artwork_dir / f"{track_id}_original.jpeg"
                if not backup_path.exists():
                    try:
                        import shutil
                        shutil.copy2(source_path, backup_path)
                    except Exception as e:
                        log_service.warning(f"Could not backup {track_id}: {e}")

                result = await self.upscale_artwork(
                    track_id=track_id,
                    source_image_path=source_path,
                    strength=strength,
                    quality=quality
                )

                if result:
                    stats["success"] += 1
                else:
                    stats["failed"] += 1

        for track_id in track_ids:
            await process_track(track_id)

        return stats

artwork_generation_service = ArtworkGenerationService()