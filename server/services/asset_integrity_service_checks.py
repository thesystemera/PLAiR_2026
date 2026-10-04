"""The asset doctor's checks: what each asset check covers, findings, subjects (a track or a shoutout) and the file
probes they use."""
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from services.audio_transcoding_service import FFPROBE_EXE_PATH, FFMPEG_CWD


OK = "ok"
MISSING = "missing"
INVALID = "invalid"
BLOCKED = "blocked"
DEFERRED = "deferred"
PROBLEM_STATUSES = (MISSING, INVALID)

MIN_AUDIO_BYTES = 16 * 1024
MIN_SHOUTOUT_AUDIO_BYTES = 1024
MIN_SHOUTOUT_DURATION_S = 0.3
MIN_IMAGE_BYTES = 1024
MIN_IMAGE_EDGE = 256
MIN_WEBM_BYTES = 1024
DURATION_TOLERANCE_S = 2.0
DURATION_TOLERANCE_RATIO = 0.02
SHOUTOUT_DURATION_TOLERANCE_S = 1.0
FEATURE_KEYS = ("duration", "tempo", "beats", "loudness_segments", "crossfade_points")
REQUIRED_DERIVED_LISTS = ("mood_keywords", "video_search_terms")
INSPIRED_ARTIST_FIELD = "derived_tags.inspired_artist"
VOCALS_FIELD = "derived_tags.vocals"
REPAIRABLE_METADATA_FIELDS = ("duration", "derived_tags", "derived_tags.primary_genre", "derived_tags.mood_keywords",
                              INSPIRED_ARTIST_FIELD, VOCALS_FIELD)
BACKFILL_METADATA_FIELDS = ("derived_tags.video_search_terms",)
MAX_REPORT_ISSUES = 500
FFPROBE_TIMEOUT_S = 30
EBML_MAGIC = b"\x1a\x45\xdf\xa3"
SHOUTOUT_STEM = re.compile(r"^[A-Za-z0-9-]{1,64}$")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class AssetCheck:
    key: str
    kind: str
    label: str
    sources: Tuple[str, ...]
    gpu: bool
    heavy: bool


TRACK_CHECKS: Tuple[AssetCheck, ...] = (
    AssetCheck("metadata", "track", "Metadata JSON and required fields", ("metadata",), False, False),
    AssetCheck("artwork", "track", "Artwork JPEG", ("original", "image_url", "artwork_prompt"), True, True),
    AssetCheck("artwork_enriched", "track", "Enriched artwork (colour + depth)", ("artwork",), True, True),
    AssetCheck("catalog_mp3", "track", "Catalog MP3", ("master_wav",), False, True),
    AssetCheck("opus_128k", "track", "Opus 128k", ("master_wav",), False, True),
    AssetCheck("opus_192k", "track", "Opus 192k", ("master_wav",), False, True),
    AssetCheck("opus_256k", "track", "Opus 256k", ("master_wav",), False, True),
    AssetCheck("webm", "track", "WebM stream containers (validated when present)", ("opus",), False, False),
    AssetCheck("audio_features", "track", "Audio features JSON", ("master_wav",), False, True),
    AssetCheck("lyric_timestamps", "track", "Lyric timestamps JSON", ("metadata", "master_wav"), True, True),
    AssetCheck("db_flags", "track", "DB has_mp3/has_wav/has_artwork flags", ("db_row",), False, False),
    AssetCheck("vector_index", "track", "Catalog vector store entry", ("db_row",), True, False),
)

SHOUTOUT_CHECKS: Tuple[AssetCheck, ...] = (
    AssetCheck("shoutout_audio", "shoutout", "Shoutout MP3", ("source_upload",), True, True),
    AssetCheck("shoutout_transcript", "shoutout", "Shoutout transcript JSON", ("source_upload",), True, True),
    AssetCheck("shoutout_duration", "shoutout", "Shoutout duration metadata", ("audio",), False, False),
    AssetCheck("shoutout_db", "shoutout", "Shoutout DB row", ("audio", "transcript"), False, False),
    AssetCheck("shoutout_enhancement", "shoutout", "Shoutout audio rendered with the current enhancement chain",
               ("source_upload", "transcript"), True, True),
)

CHECKS: Dict[str, AssetCheck] = {check.key: check for check in TRACK_CHECKS + SHOUTOUT_CHECKS}


@dataclass
class Finding:
    status: str
    detail: str = ""


@dataclass
class Subject:
    kind: str
    id: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    db: Optional[Dict[str, Any]] = None
    info: Dict[str, Any] = field(default_factory=dict)
    findings: Dict[str, Finding] = field(default_factory=dict)

    @property
    def is_upload(self) -> bool:
        return self.kind == "track" and self.metadata.get("uploaded_by_user_id") is not None

    @property
    def title(self) -> str:
        params = self.metadata.get("generation_params") or {}
        return params.get("title") or (self.metadata.get("track_info") or {}).get("title") or ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _file_stat(path: Path) -> Optional[Tuple[int, int]]:
    try:
        stat = path.stat()
    except OSError:
        return None
    if not path.is_file():
        return None
    return stat.st_size, stat.st_mtime_ns


def _duration_mismatch(actual: Optional[float], reference: Optional[float]) -> Optional[str]:
    if actual is None or not reference:
        return None
    if abs(actual - reference) > max(DURATION_TOLERANCE_S, reference * DURATION_TOLERANCE_RATIO):
        return f"duration {actual:.1f}s vs master {reference:.1f}s"
    return None


def _ogg_opus_duration(path: Path) -> float:
    with open(path, "rb") as handle:
        head = handle.read(4096)
        if not head.startswith(b"OggS"):
            raise ValueError("not an Ogg stream")
        marker = head.find(b"OpusHead")
        if marker < 0 or marker + 12 > len(head):
            raise ValueError("missing OpusHead")
        pre_skip = int.from_bytes(head[marker + 10:marker + 12], "little")
        size = handle.seek(0, os.SEEK_END)
        handle.seek(max(0, size - 65536))
        tail = handle.read()
    position = tail.rfind(b"OggS")
    while position >= 0 and (position + 27 > len(tail) or tail[position + 4] != 0):
        position = tail.rfind(b"OggS", 0, position)
    if position < 0:
        raise ValueError("no final Ogg page")
    if not tail[position + 5] & 0x04:
        raise ValueError("truncated stream (no end-of-stream page)")
    granule = int.from_bytes(tail[position + 6:position + 14], "little", signed=True)
    if granule <= 0:
        raise ValueError("invalid granule position")
    return max(0.0, (granule - pre_skip) / 48000.0)


def _looks_like_mp3(path: Path) -> bool:
    with open(path, "rb") as handle:
        head = handle.read(4)
    return head[:3] == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)


def _ffprobe_duration(path: Path) -> Optional[float]:
    if not Path(FFPROBE_EXE_PATH).exists():
        return None
    result = subprocess.run(
        [FFPROBE_EXE_PATH, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=FFPROBE_TIMEOUT_S, cwd=FFMPEG_CWD, creationflags=CREATE_NO_WINDOW
    )
    if result.returncode != 0:
        raise ValueError((result.stderr or "ffprobe failed").strip()[:200])
    try:
        return float((result.stdout or "").strip().splitlines()[0])
    except (ValueError, IndexError):
        raise ValueError("ffprobe returned no duration")


