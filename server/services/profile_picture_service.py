from __future__ import annotations
import io
import asyncio
import aiofiles
from typing import Optional, Dict, Any
from PIL import Image
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User
from config import settings
from services import log_service
from services.base_service import SingletonService
from services.suno_artwork_enrichment_service import artwork_enrichment_service
from services.normal_map_service import ensure_pair_normal

Image.MAX_IMAGE_PIXELS = 40_000_000
DEPTH_FILENAME = "profile_depth.jpg"
NORMAL_FILENAME = "profile_normal.jpg"

class ProfilePictureService(SingletonService):
    def __init__(self):
        if self._initialized:
            return

        self.allowed_extensions = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
        self.max_size_bytes = 5 * 1024 * 1024  # 5MB
        self.target_size = (512, 512)
        self.jpeg_quality = 90
        self._depth_jobs: Dict[int, asyncio.Task] = {}
        self._initialized = True

    @staticmethod
    def depth_path(user_id: int) -> Path:
        return settings.get_user_profile_picture_path(user_id, DEPTH_FILENAME)

    def _depth_job(self, user_id: int, picture_path: Path) -> asyncio.Task:
        job = self._depth_jobs.get(user_id)
        if job is None:
            job = asyncio.create_task(
                artwork_enrichment_service.write_depth_map(picture_path, self.depth_path(user_id))
            )
            self._depth_jobs[user_id] = job
            job.add_done_callback(lambda _t: self._depth_jobs.pop(user_id, None))
        return job

    async def get_depth_path(self, user_id: int, db: AsyncSession) -> Optional[Path]:
        picture_path = await self.get_profile_picture_path(user_id, db)
        if not picture_path or not artwork_enrichment_service.available:
            return None
        target = self.depth_path(user_id)
        if target.exists() and target.stat().st_mtime >= picture_path.stat().st_mtime:
            return target
        return await asyncio.shield(self._depth_job(user_id, picture_path))

    async def get_normal_path(self, user_id: int, db: AsyncSession) -> Optional[Path]:
        depth_path = await self.get_depth_path(user_id, db)
        if not depth_path:
            return None
        picture_path = settings.get_user_profile_picture_path(user_id, "profile.jpg")
        target = settings.get_user_profile_picture_path(user_id, NORMAL_FILENAME)
        return await ensure_pair_normal(f"profile:{user_id}", picture_path, depth_path, target)

    def _render_profile_jpeg(self, file_contents: bytes) -> bytes:
        image = Image.open(io.BytesIO(file_contents))

        if image.mode == 'RGBA':
            background = Image.new('RGB', image.size, (255, 255, 255))
            background.paste(image, mask=image.split()[3])
            image = background
        elif image.mode != 'RGB':
            image = image.convert('RGB')

        image = image.resize(self.target_size, Image.Resampling.LANCZOS)

        output = io.BytesIO()
        image.save(output, format='JPEG', quality=self.jpeg_quality, optimize=True)
        return output.getvalue()

    async def upload_profile_picture(
        self,
        user_id: int,
        file_contents: bytes,
        original_filename: str,
        db: AsyncSession
    ) -> Dict[str, Any]:

        file_ext = Path(original_filename).suffix.lower()
        if file_ext not in self.allowed_extensions:
            raise ValueError(f"Invalid file type. Allowed: {', '.join(self.allowed_extensions)}")

        if len(file_contents) > self.max_size_bytes:
            raise ValueError("File too large. Maximum size is 5MB")

        try:
            output_bytes = await asyncio.to_thread(self._render_profile_jpeg, file_contents)

            filename = "profile.jpg"
            file_path = settings.get_user_profile_picture_path(user_id, filename)

            async with aiofiles.open(file_path, 'wb') as f:
                await f.write(output_bytes)

            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()

            if user:
                user.profile_picture = filename  # type: ignore
                await db.commit()
                log_service.api(f"Profile picture uploaded for user {user.id}")

            if artwork_enrichment_service.available:
                self._depth_job(user_id, file_path)

            return {
                "status": "success",
                "message": "Profile picture uploaded",
                "profile_picture": filename
            }

        except Exception as e:
            log_service.error(f"Error processing profile picture: {str(e)}")
            raise ValueError(f"Error processing image: {str(e)}")

    async def get_profile_picture_path(self, user_id: int, db: AsyncSession) -> Optional[Path]:

        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if not user:
            return None

        filename: str = user.profile_picture  # type: ignore
        if not filename:
            return None

        file_path = settings.get_user_profile_picture_path(user_id, filename)

        if not file_path.exists():
            return None

        return file_path

    async def delete_profile_picture(self, user_id: int, db: AsyncSession) -> Dict[str, Any]:

        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if not user:
            raise ValueError("No profile picture to delete")

        filename: str = user.profile_picture  # type: ignore
        if not filename:
            raise ValueError("No profile picture to delete")

        user_id_val: int = user.id  # type: ignore
        file_path = settings.get_user_profile_picture_path(user_id_val, filename)

        if file_path.exists():
            file_path.unlink()
            log_service.api(f"Profile picture deleted for user {user_id_val}")
        self.depth_path(user_id_val).unlink(missing_ok=True)
        settings.get_user_profile_picture_path(user_id_val, NORMAL_FILENAME).unlink(missing_ok=True)

        user.profile_picture = None  # type: ignore
        await db.commit()

        return {"status": "success", "message": "Profile picture deleted"}

profile_picture_service = ProfilePictureService()