import asyncio
from pathlib import Path
from typing import Optional, Dict, Any
from PIL import Image
import numpy as np
import torch
from services import log_service
from services.base_service import SingletonService
from config import settings
from config.settings import BASE_DIR
from models_global import gpu_lease, raise_if_cuda_oom, GPUOutOfMemoryError

Image.MAX_IMAGE_PIXELS = 40_000_000

class ArtworkEnrichmentService(SingletonService):
    def __init__(self):
        if self._initialized:
            return

        self.artwork_dir = settings.ARTWORK_DIR
        self.enriched_dir = settings.ARTWORK_ENRICHED_DIR
        self._model = None
        self._transform = None
        self._device = None
        self._model_loaded = False
        self._initialized = True

    async def initialize(self):
        if self._model_loaded:
            log_service.upscaling("Depth Anything V2 already loaded")
            return

        try:
            log_service.upscaling("Loading Depth Anything V2 (vits)...")

            self._device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
            log_service.upscaling(f"  - Device: {self._device}")

            import sys
            depth_anything_path = str(BASE_DIR / 'models' / 'depth_anything_v2')
            if depth_anything_path not in sys.path:
                sys.path.insert(0, depth_anything_path)

            from depth_anything_v2.dpt import DepthAnythingV2

            self._model = DepthAnythingV2(
                encoder='vits',
                features=64,
                out_channels=[48, 96, 192, 384]
            )

            model_path = BASE_DIR / 'models' / 'depth_anything_v2_vits.pth'
            if not model_path.exists():
                raise FileNotFoundError(
                    f"Model not found at {model_path}\n"
                    f"Copy from your deepMirror project:\n"
                    f"  deepMirror/models/depth_anything_v2/  →  AI_RADIO/models/depth_anything_v2/\n"
                    f"  deepMirror/models/depth_anything_v2_vits.pth  →  AI_RADIO/models/depth_anything_v2_vits.pth"
                )

            self._model.load_state_dict(torch.load(str(model_path), map_location='cpu'))
            self._model = self._model.to(self._device).eval()

            self._model_loaded = True
            log_service.upscaling("Depth Anything V2 Loaded - Ready.")

        except Exception as e:
            log_service.error(f"Failed to load Depth Anything V2: {str(e)}")
            self._model = None
            self._model_loaded = False

    def _generate_depth_map(self, image: Image.Image) -> np.ndarray:
        return (self._infer_depth(image) * 255).astype(np.uint8)

    def _infer_depth(self, image: Image.Image) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("Depth model not loaded - call initialize() first")
        with torch.no_grad():
            depth = self._model.infer_image(np.array(image.convert('RGB')))
        return ((depth - depth.min()) / (depth.max() - depth.min() + 1e-8)).astype(np.float32)

    async def depth_float(self, image: Image.Image) -> np.ndarray:
        loop = asyncio.get_event_loop()
        async with gpu_lease("Depth-Anything"):
            return await loop.run_in_executor(None, self._infer_depth, image)

    @staticmethod
    def _create_side_by_side_jpeg(
        color_image: Image.Image,
        depth_map: np.ndarray
    ) -> Image.Image:
        w, h = color_image.size
        sbs_image = Image.new('RGB', (w * 2, h))
        sbs_image.paste(color_image, (0, 0))
        depth_pil = Image.fromarray(depth_map, mode='L').convert('RGB')
        sbs_image.paste(depth_pil, (w, 0))
        return sbs_image

    @property
    def available(self) -> bool:
        return self._model_loaded

    async def depth_image(self, image: Image.Image) -> Image.Image:
        loop = asyncio.get_event_loop()
        async with gpu_lease("Depth-Anything"):
            depth_map = await loop.run_in_executor(None, self._generate_depth_map, image)
        return Image.fromarray(depth_map, mode='L')

    async def write_depth_map(self, source: Path, target: Path, quality: int = 90) -> Optional[Path]:
        if not self._model_loaded or not source.exists():
            return None
        if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
            return target
        try:
            loop = asyncio.get_event_loop()
            image = await loop.run_in_executor(None, lambda: Image.open(str(source)).convert('RGB'))
            depth = await self.depth_image(image)
            temp = target.with_name(f"{target.stem}.tmp.jpg")

            def save_image():
                depth.save(str(temp), 'JPEG', quality=quality, optimize=True)
                temp.replace(target)

            await loop.run_in_executor(None, save_image)
            return target
        except GPUOutOfMemoryError:
            raise
        except Exception as e:
            log_service.error(f"Failed to make depth map for {source.name}: {str(e)}")
            raise_if_cuda_oom(e, "Depth-Anything")
            return None

    async def enrich_artwork(
        self,
        unique_id: str,
        quality: int = 90
    ) -> Optional[Path]:

        if not self._model_loaded:
            log_service.error("Depth model not loaded - call initialize() first")
            return None

        artwork_path = self.artwork_dir / f"{unique_id}.jpeg"
        enriched_path = self.enriched_dir / f"{unique_id}.jpeg"

        if not artwork_path.exists():
            log_service.warning(f"Original artwork not found: {artwork_path}")
            return None

        if enriched_path.exists() and enriched_path.stat().st_mtime >= artwork_path.stat().st_mtime:
            return enriched_path

        try:
            loop = asyncio.get_event_loop()
            image = await loop.run_in_executor(None, lambda: Image.open(str(artwork_path)).convert('RGB'))

            log_service.info(f"Processing artwork: {unique_id} ({image.size[0]}x{image.size[1]})")

            depth = await self.depth_image(image)

            sbs_image = await loop.run_in_executor(
                None,
                self._create_side_by_side_jpeg,
                image,
                np.array(depth)
            )

            def save_image():
                sbs_image.save(str(enriched_path), 'JPEG', quality=quality, optimize=True)

            await loop.run_in_executor(None, save_image)

            log_service.success(
                f"Enriched artwork saved: {unique_id} "
                f"({sbs_image.size[0]}x{sbs_image.size[1]})"
            )

            return enriched_path

        except GPUOutOfMemoryError:
            raise
        except Exception as e:
            log_service.error(f"Failed to enrich artwork {unique_id}: {str(e)}")
            raise_if_cuda_oom(e, "Depth-Anything")
            return None

    async def batch_enrich_catalog(
        self,
        max_concurrent: int = 2,
        quality: int = 90
    ) -> Dict[str, Any]:

        log_service.system("Starting batch artwork enrichment...")

        artwork_files = list(self.artwork_dir.glob("*.jpeg")) + list(self.artwork_dir.glob("*.jpg"))
        total = len(artwork_files)

        if total == 0:
            log_service.warning("No artwork found to enrich")
            return {"total": 0, "success": 0, "skipped": 0, "failed": 0}

        log_service.system(f"Found {total} artwork files to process")

        unique_ids = [f.stem for f in artwork_files]

        semaphore = asyncio.Semaphore(max_concurrent)

        async def process_with_semaphore(uid):
            async with semaphore:
                return await self.enrich_artwork(uid, quality)

        results = await asyncio.gather(
            *[process_with_semaphore(uid) for uid in unique_ids],
            return_exceptions=True
        )

        success = sum(1 for r in results if r is not None and not isinstance(r, Exception))
        failed = sum(1 for r in results if isinstance(r, Exception))
        skipped = total - success - failed

        stats = {
            "total": total,
            "success": success,
            "skipped": skipped,
            "failed": failed
        }

        log_service.system(
            f"Batch enrichment complete: {success} success, "
            f"{skipped} skipped, {failed} failed"
        )

        return stats

artwork_enrichment_service = ArtworkEnrichmentService()