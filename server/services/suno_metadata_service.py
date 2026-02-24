import json
import secrets
import asyncio
import aiofiles
import aiofiles.os
from typing import Dict, Any, Optional
from pathlib import Path
from datetime import datetime, timezone
from services import log_service
from services.base_service import SingletonService
from config import settings

class MusicMetadata(SingletonService):
    def __init__(self):
        if self._initialized:
            return

        self.catalog_dir = settings.CATALOG_DIR
        self.metadata_dir = settings.METADATA_DIR
        self.audio_dir = settings.AUDIO_DIR
        self.artwork_dir = settings.ARTWORK_DIR
        self._metadata_initialized = False
        self._initialized = True

    async def initialize(self):
        if self._metadata_initialized:
            log_service.system("MusicMetadata already initialized")
            return

        self._metadata_initialized = True
        log_service.system("MusicMetadata initialized")

    @staticmethod
    def generate_unique_id() -> str:
        return secrets.token_hex(16)

    async def create_metadata(
            self,
            user_request: str,
            music_params: Dict[str, Any],
            track_data: Optional[Dict[str, Any]] = None,
            unique_id: Optional[str] = None,
            generation_status: str = "params_generated"
    ) -> Dict[str, Any]:
        if not unique_id:
            unique_id = self.generate_unique_id()

        metadata = {
            "id": unique_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "generation_status": generation_status,
            "status_updated_at": datetime.now(timezone.utc).isoformat(),
            "user_request": {
                "original_text": user_request,
                "artist_name": music_params.get("artist_name"),
                "processed_at": datetime.now(timezone.utc).isoformat()
            },
            "generation_params": {
                "prompt": music_params.get("prompt"),
                "custom_mode": music_params.get("custom_mode", False),
                "style": music_params.get("style"),
                "title": music_params.get("title"),
                "artist_name": music_params.get("artist_name"),
                "instrumental": music_params.get("instrumental", False),
                "model": music_params.get("model", "V5"),
                "negative_tags": music_params.get("negative_tags"),
                "vocal_gender": music_params.get("vocal_gender"),
                "style_weight": music_params.get("style_weight"),
                "weirdness": music_params.get("weirdness"),
                "audio_weight": music_params.get("audio_weight"),
            },
            "track_info": {}
        }

        if track_data:
            duration_seconds = track_data.get("duration", 0)
            duration_ms = int(duration_seconds * 1000) if duration_seconds else 0

            metadata["track_info"] = {
                "title": track_data.get("title"),
                "duration": duration_ms,
                "audio_url": track_data.get("audioUrl"),
                "image_url": track_data.get("imageUrl"),
                "video_url": track_data.get("videoUrl"),
                "suno_id": track_data.get("id"),
                "status": track_data.get("status"),
                "created_at": track_data.get("createdAt"),
                "tags": track_data.get("tags"),
                "prompt": track_data.get("prompt")
            }

        return metadata

    async def save_metadata(
            self,
            metadata: Dict[str, Any],
            filename: Optional[str] = None
    ) -> Path:
        if not filename:
            filename = metadata.get("id", self.generate_unique_id())

        filepath = self.metadata_dir / f"{filename}.json"
        temp_filepath = self.metadata_dir / f"{filename}.json.tmp"

        try:
            json_content = json.dumps(metadata, indent=2, ensure_ascii=False)

            async with aiofiles.open(temp_filepath, 'w', encoding='utf-8') as f:
                await f.write(json_content)
                await f.flush()

            try:
                await aiofiles.os.replace(temp_filepath, filepath)
            except Exception as e:
                log_service.warning(f"Async replace failed, using sync: {str(e)}")
                import os
                os.replace(str(temp_filepath), str(filepath))

            log_service.success(f"Metadata saved: {filepath.name}")
            return filepath

        except Exception as e:
            log_service.error(f"Error saving metadata: {str(e)}")
            if temp_filepath.exists():
                try:
                    await aiofiles.os.remove(temp_filepath)
                except Exception as cleanup_error:
                    log_service.warning(f"Failed to cleanup temp file: {str(cleanup_error)}")
            raise

    async def load_metadata(self, filename: str) -> Optional[Dict[str, Any]]:
        if not filename.endswith('.json'):
            filepath = self.metadata_dir / f"{filename}.json"
        else:
            filepath = self.metadata_dir / filename

        max_retries = 3
        retry_delay = 0.1

        for attempt in range(max_retries):
            try:
                async with aiofiles.open(filepath, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    if not content or content.strip() == "":
                        log_service.warning(f"Corrupt metadata: {filepath.name} is empty - deleting")
                        try:
                            filepath.unlink()
                        except Exception as e:
                            log_service.error(f"Failed to delete {filepath.name}: {e}")
                        return None
                    return json.loads(content)

            except FileNotFoundError:
                return None
            except json.JSONDecodeError:
                log_service.warning(f"Corrupt metadata: {filepath.name} has invalid JSON - deleting")
                try:
                    filepath.unlink()
                except Exception as e:
                    log_service.error(f"Failed to delete {filepath.name}: {e}")
                return None
            except PermissionError:
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    continue
                else:
                    log_service.error(f"File locked after {max_retries} attempts: {filepath.name}")
                    return None
            except Exception as e:
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    continue
                else:
                    log_service.error(f"Unexpected error loading {filepath.name}: {str(e)}")
                    return None

        return None

    def get_audio_path(self, unique_id: str) -> Path:
        return self.audio_dir / f"{unique_id}.mp3"

    def get_image_path(self, unique_id: str) -> Path:
        return self.artwork_dir / f"{unique_id}.jpeg"

    async def update_generation_status(
            self,
            unique_id: str,
            new_status: str,
            track_data: Optional[Dict[str, Any]] = None
    ) -> bool:
        try:
            metadata = await self.load_metadata(unique_id)
            if not metadata:
                log_service.error(f"Cannot update status: metadata not found for {unique_id}")
                return False

            metadata["generation_status"] = new_status
            metadata["status_updated_at"] = datetime.now(timezone.utc).isoformat()

            if track_data:
                duration_seconds = track_data.get("duration", 0)
                duration_ms = int(duration_seconds * 1000) if duration_seconds else 0

                metadata["track_info"] = {
                    "title": track_data.get("title"),
                    "duration": duration_ms,
                    "audio_url": track_data.get("audioUrl"),
                    "image_url": track_data.get("imageUrl"),
                    "video_url": track_data.get("videoUrl"),
                    "suno_id": track_data.get("id"),
                    "status": track_data.get("status"),
                    "created_at": track_data.get("createdAt"),
                    "tags": track_data.get("tags"),
                    "prompt": track_data.get("prompt")
                }

            await self.save_metadata(metadata, unique_id)
            return True

        except Exception as e:
            log_service.error(f"Error updating generation status: {str(e)}")
            return False