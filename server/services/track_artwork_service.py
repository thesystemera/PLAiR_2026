import io
import aiofiles
import aiofiles.os
from typing import Dict
from PIL import Image
from pathlib import Path

from services import log_service
from services.base_service import SingletonService
from config import settings

class TrackArtworkService(SingletonService):

    def __init__(self):
        if self._initialized:
            return

        self.allowed_extensions = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
        self.max_size_bytes = 10 * 1024 * 1024  # 10MB
        self.target_size = (1024, 1024)
        self.jpeg_quality = 92

        self.artwork_dir = settings.ARTWORK_DIR
        self.artwork_enriched_dir = settings.ARTWORK_ENRICHED_DIR

        self.artwork_enrichment_service = None
        self.catalog_db_service = None
        self.artwork_generation_service = None

        self._initialized = True

    async def initialize(
        self,
        artwork_enrichment_service=None,
        catalog_db_service=None,
        artwork_generation_service=None
    ):
        self.artwork_enrichment_service = artwork_enrichment_service
        self.catalog_db_service = catalog_db_service
        self.artwork_generation_service = artwork_generation_service

        self.artwork_dir.mkdir(parents=True, exist_ok=True)
        self.artwork_enriched_dir.mkdir(parents=True, exist_ok=True)

        log_service.system("✓ TrackArtworkService initialized")

    async def upload_artwork(
        self,
        track_id: str,
        user_id: int,
        file_contents: bytes,
        original_filename: str,
        enrich: bool = True
    ) -> Dict:

        if self.catalog_db_service:
            track = self.catalog_db_service.tracks.get(track_id)
            if not track:
                raise ValueError("Track not found")
            if track.get("uploaded_by_user_id") != user_id:
                raise ValueError("You can only upload artwork for your own tracks")

        file_ext = Path(original_filename).suffix.lower()
        if file_ext not in self.allowed_extensions:
            raise ValueError(f"Invalid file type. Allowed: {', '.join(self.allowed_extensions)}")

        if len(file_contents) > self.max_size_bytes:
            raise ValueError("File too large. Maximum size is 10MB")

        try:
            image = Image.open(io.BytesIO(file_contents))

            if image.mode == 'RGBA':
                background = Image.new('RGB', image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[3])
                image = background
            elif image.mode == 'P':
                image = image.convert('RGBA')
                background = Image.new('RGB', image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[3])
                image = background
            elif image.mode != 'RGB':
                image = image.convert('RGB')

            width, height = image.size
            min_dim = min(width, height)

            left = (width - min_dim) // 2
            top = (height - min_dim) // 2
            image = image.crop((left, top, left + min_dim, top + min_dim))

            image = image.resize(self.target_size, Image.Resampling.LANCZOS)

            output = io.BytesIO()
            image.save(output, format='JPEG', quality=self.jpeg_quality, optimize=True)
            output.seek(0)

            artwork_path = self.artwork_dir / f"{track_id}.jpeg"
            async with aiofiles.open(artwork_path, 'wb') as f:
                await f.write(output.read())

            log_service.success(f"[TrackArtwork] Uploaded artwork for {track_id}")

            has_artwork = True
            if self.catalog_db_service:
                try:
                    if track_id in self.catalog_db_service.tracks:
                        self.catalog_db_service.tracks[track_id]["has_artwork"] = True

                    import asyncio
                    await asyncio.to_thread(
                        self.catalog_db_service.update_track_artwork_status,
                        track_id,
                        True
                    )
                except Exception as e:
                    log_service.warning(f"[TrackArtwork] Failed to update DB: {e}")

            enriched = False
            if enrich and self.artwork_enrichment_service:
                try:
                    enriched_path = await self.artwork_enrichment_service.enrich_artwork(track_id)
                    if enriched_path:
                        enriched = True
                        log_service.success(f"[TrackArtwork] Generated depth map for {track_id}")
                except Exception as e:
                    log_service.warning(f"[TrackArtwork] Depth map generation failed: {e}")

            return {
                "status": "success",
                "message": "Artwork uploaded successfully",
                "track_id": track_id,
                "has_artwork": has_artwork,
                "artwork_enriched": enriched,
                "artwork_url": f"/api/artwork/{track_id}"
            }

        except Exception as e:
            log_service.error(f"[TrackArtwork] Error processing image: {e}")
            raise ValueError(f"Error processing image: {str(e)}")

    async def delete_artwork(self, track_id: str, user_id: int) -> Dict:
        if self.catalog_db_service:
            track = self.catalog_db_service.tracks.get(track_id)
            if not track:
                raise ValueError("Track not found")
            if track.get("uploaded_by_user_id") != user_id:
                raise ValueError("You can only delete artwork for your own tracks")

        artwork_path = self.artwork_dir / f"{track_id}.jpeg"
        enriched_path = self.artwork_enriched_dir / f"{track_id}.jpeg"

        deleted = False

        if artwork_path.exists():
            await aiofiles.os.remove(artwork_path)
            deleted = True
            log_service.info(f"[TrackArtwork] Deleted artwork for {track_id}")

        if enriched_path.exists():
            await aiofiles.os.remove(enriched_path)
            log_service.info(f"[TrackArtwork] Deleted enriched artwork for {track_id}")

        if self.catalog_db_service:
            try:
                if track_id in self.catalog_db_service.tracks:
                    self.catalog_db_service.tracks[track_id]["has_artwork"] = False

                import asyncio
                await asyncio.to_thread(
                    self.catalog_db_service.update_track_artwork_status,
                    track_id,
                    False
                )
            except Exception as e:
                log_service.warning(f"[TrackArtwork] Failed to update DB: {e}")

        if not deleted:
            raise ValueError("No artwork to delete")

        return {
            "status": "success",
            "message": "Artwork deleted",
            "track_id": track_id
        }

    async def has_artwork(self, track_id: str) -> bool:
        artwork_path = self.artwork_dir / f"{track_id}.jpeg"
        return artwork_path.exists()

    async def generate_artwork(self, track_id: str, user_id: int) -> Dict:
        if not self.artwork_generation_service:
            raise ValueError("Artwork generation service not available")

        if not self.catalog_db_service:
            raise ValueError("Catalog service not available")

        track = self.catalog_db_service.tracks.get(track_id)
        if not track:
            raise ValueError("Track not found")
        if track.get("uploaded_by_user_id") != user_id:
            raise ValueError("You can only generate artwork for your own uploads")

        artwork_path = self.artwork_dir / f"{track_id}.jpeg"
        if artwork_path.exists():
            raise ValueError("Track already has artwork. Delete it first to regenerate.")

        gen_params = track.get("generation_params", {})
        derived_tags = track.get("derived_tags", {})
        track_info = track.get("track_info", {})

        mood_keywords = derived_tags.get("mood_keywords") or []
        style_keywords = derived_tags.get("style_keywords") or []

        metadata = {
            "title": gen_params.get("title", "Untitled"),
            "primary_artist": track_info.get("artist", "Unknown"),
            "primary_genre": derived_tags.get("primary_genre"),
            "mood": mood_keywords[0] if mood_keywords else None,
            "style": style_keywords[0] if style_keywords else None,
            "artwork_prompt": track.get("artwork_prompt"),
        }

        generated_path = await self.artwork_generation_service.generate_artwork_for_track(
            track_id=track_id,
            metadata=metadata
        )

        if not generated_path or not generated_path.exists():
            raise ValueError("Artwork generation failed")

        import asyncio
        await asyncio.to_thread(
            self.catalog_db_service.update_track_artwork_status,
            track_id,
            True
        )

        if track_id in self.catalog_db_service.tracks:
            self.catalog_db_service.tracks[track_id]["has_artwork"] = True

        log_service.success(f"[TrackArtwork] Generated AI artwork for {track_id}")

        enriched = False
        if self.artwork_enrichment_service:
            try:
                await self.artwork_enrichment_service.enrich_artwork(track_id)
                enriched = True
                log_service.success(f"[TrackArtwork] Generated depth map for {track_id}")
            except Exception as e:
                log_service.warning(f"[TrackArtwork] Depth map generation failed (non-critical): {e}")

        return {
            "status": "success",
            "message": "AI artwork generated successfully",
            "has_artwork": True,
            "artwork_enriched": enriched
        }

track_artwork_service = TrackArtworkService()