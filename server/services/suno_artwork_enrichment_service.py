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
        if self._model is None:
            raise RuntimeError("Depth model not loaded - call initialize() first")

        img_array = np.array(image.convert('RGB'))

        with torch.no_grad():
            depth = self._model.infer_image(img_array)

        depth_normalized = (depth - depth.min()) / (depth.max() - depth.min() + 1e-8)
        depth_uint8 = (depth_normalized * 255).astype(np.uint8)

        return depth_uint8

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

        if enriched_path.exists():
            return enriched_path

        if not artwork_path.exists():
            log_service.warning(f"Original artwork not found: {artwork_path}")
            return None

        try:
            loop = asyncio.get_event_loop()
            image = await loop.run_in_executor(None, Image.open, str(artwork_path))

            log_service.info(f"Processing artwork: {unique_id} ({image.size[0]}x{image.size[1]})")

            depth_map = await loop.run_in_executor(None, self._generate_depth_map, image)

            sbs_image = await loop.run_in_executor(
                None,
                self._create_side_by_side_jpeg,
                image,
                depth_map
            )

            def save_image():
                sbs_image.save(str(enriched_path), 'JPEG', quality=quality, optimize=True)

            await loop.run_in_executor(None, save_image)

            log_service.success(
                f"Enriched artwork saved: {unique_id} "
                f"({sbs_image.size[0]}x{sbs_image.size[1]})"
            )

            return enriched_path

        except Exception as e:
            log_service.error(f"Failed to enrich artwork {unique_id}: {str(e)}")
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