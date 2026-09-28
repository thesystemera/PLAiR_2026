from typing import Optional
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import StreamingResponse, FileResponse, Response
import aiofiles
from database import User

def parse_range(range_header: str, file_size: int):
    spec = range_header.strip().lower()
    if not spec.startswith("bytes="):
        return None
    first, sep, last = spec[len("bytes="):].split(",")[0].strip().partition("-")
    if not sep:
        return None
    try:
        if first == "":
            if not last:
                return None
            length = min(int(last), file_size)
            if length <= 0:
                return None
            return file_size - length, file_size - 1
        start = int(first)
        end = int(last) if last else file_size - 1
    except ValueError:
        return None
    if start < 0 or start >= file_size or start > end:
        return None
    return start, min(end, file_size - 1)


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
            parsed = parse_range(range_header, file_size)
            if parsed is None:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}", "Accept-Ranges": "bytes"})
            start, end = parsed

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