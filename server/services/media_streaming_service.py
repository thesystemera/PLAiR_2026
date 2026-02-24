from typing import Optional
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import StreamingResponse, FileResponse
import aiofiles
from database import User

class MediaStreamingService:

    def __init__(self):
        self._initialized = False

    async def initialize(self):
        if self._initialized:
            return
        self._initialized = True

    @staticmethod
    def resolve_bitrate(user: Optional[User]) -> str:
        if not user:
            return "192k"

        if user.audio_quality == "auto":
            return "256k"
        elif user.audio_quality in ["128k", "192k", "256k"]:
            return user.audio_quality
        else:
            return "192k"

    async def stream_file(
        self,
        file_path: Path,
        range_header: Optional[str],
        media_type: str,
        extra_headers: dict = None
    ):
        if not file_path or not file_path.exists():
            raise HTTPException(status_code=404, detail="Media file not found")

        file_size = file_path.stat().st_size
        headers = extra_headers or {}

        if range_header:
            range_match = range_header.replace("bytes=", "").split("-")
            start = int(range_match[0]) if range_match[0] else 0
            end = int(range_match[1]) if len(range_match) > 1 and range_match[1] else file_size - 1

            if end >= file_size:
                end = file_size - 1

            content_length = end - start + 1

            async def iterfile():
                async with aiofiles.open(file_path, "rb") as f:
                    await f.seek(start)
                    remaining = content_length
                    while remaining > 0:
                        chunk_size = min(65536, remaining)
                        data = await f.read(chunk_size)
                        if not data:
                            break
                        remaining -= len(data)
                        yield data

            headers.update({
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(content_length),
                "Content-Type": media_type,
            })

            return StreamingResponse(
                iterfile(),
                status_code=206,
                headers=headers,
                media_type=media_type
            )
        else:
            headers.update({
                "Accept-Ranges": "bytes",
            })
            return FileResponse(
                file_path,
                media_type=media_type,
                headers=headers
            )