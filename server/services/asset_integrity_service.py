import asyncio
import copy
import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Set, Tuple

import soundfile as sf
from PIL import Image

import models_global
from services import log_service
from services.user_content_database_service import kind_of, sting_file
from services import usage_tracking
from services import track_asset_stages as stages
from services.catalog_credit import ai_artist, is_ai_track, known_artists, name_like
from services.catalog_vocals import VOCALS, settled_vocals
from services.task_utils import spawn
from services.audio_transcoding_service import FFPROBE_EXE_PATH, FFMPEG_CWD
from database.pg_pool import get_pooled_connection
from config import settings

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
VECTOR_REBUILD_COOLDOWN_S = 600
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
    AssetCheck("vector_index", "track", "Catalog vector index entry", ("db_row",), True, True),
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


class AssetIntegrityService:
    def __init__(self):
        self._services: Dict[str, Any] = {}
        self._attempts: Dict[str, Dict[str, Any]] = {}
        self._last_report: Optional[Dict[str, Any]] = None
        self._probe_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.Lock()
        self._touched_paths: Set[str] = set()
        self._state_loaded = False
        self._scan_lock: Optional[asyncio.Lock] = None
        self._repair_times: Deque[float] = deque()
        self._reenhance_times: Deque[float] = deque()
        self._pending_track_ids: Set[str] = set()
        self._event_task: Optional[asyncio.Task] = None
        self._scheduler_task: Optional[asyncio.Task] = None
        self._followup_delay: Optional[float] = None
        self._index_items: Optional[int] = None
        self._vector_rebuilt_at = 0.0
        self._current_scan: Optional[Dict[str, Any]] = None

    def bind(self, **services_to_bind):
        for name, service in services_to_bind.items():
            if service is not None:
                self._services[name] = service

    def _svc(self, name: str):
        return self._services.get(name)

    def _lock(self) -> asyncio.Lock:
        if self._scan_lock is None:
            self._scan_lock = asyncio.Lock()
        return self._scan_lock

    def _load_state(self):
        if self._state_loaded:
            return
        self._state_loaded = True
        try:
            state = json.loads(settings.ASSET_DOCTOR_STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(state, dict):
                self._attempts = state.get("attempts") or {}
                self._last_report = state.get("last_report")
        except FileNotFoundError:
            pass
        except Exception as e:
            log_service.warning(f"[AssetDoctor] Could not read state file: {e}")
        try:
            cache = json.loads(settings.ASSET_DOCTOR_CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(cache, dict):
                self._probe_cache = cache
        except FileNotFoundError:
            pass
        except Exception as e:
            log_service.warning(f"[AssetDoctor] Could not read probe cache: {e}")

    @staticmethod
    def _write_json_atomic(path: Path, data: Any):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.tmp")
        temp_path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(temp_path, path)

    def _state_snapshot(self) -> Dict[str, Any]:
        return {"version": 1, "saved_at": _now_iso(), "attempts": copy.deepcopy(self._attempts),
                "last_report": self._last_report}

    async def _save_state(self, include_cache: bool = False):
        snapshot = self._state_snapshot()
        with self._cache_lock:
            cache = dict(self._probe_cache) if include_cache else None
        try:
            await asyncio.to_thread(self._write_json_atomic, settings.ASSET_DOCTOR_STATE_PATH, snapshot)
            if cache is not None:
                await asyncio.to_thread(self._write_json_atomic, settings.ASSET_DOCTOR_CACHE_PATH, cache)
        except Exception as e:
            log_service.warning(f"[AssetDoctor] Could not persist state: {e}")

    def _cache_get(self, path: Path, stat: Tuple[int, int]) -> Optional[Dict[str, Any]]:
        with self._cache_lock:
            self._touched_paths.add(str(path))
            entry = self._probe_cache.get(str(path))
        if entry and entry.get("s") == stat[0] and entry.get("m") == stat[1]:
            return entry
        return None

    def _cache_put(self, path: Path, stat: Tuple[int, int], **values):
        with self._cache_lock:
            self._touched_paths.add(str(path))
            self._probe_cache[str(path)] = {"s": stat[0], "m": stat[1], **values}

    def _prune_cache(self):
        with self._cache_lock:
            self._probe_cache = {path: entry for path, entry in self._probe_cache.items() if path in self._touched_paths}
            self._touched_paths = set()

    def _master_duration(self, track_id: str) -> Optional[float]:
        path = stages.master_wav_path(track_id)
        stat = _file_stat(path)
        if stat is None:
            return None
        cached = self._cache_get(path, stat)
        if cached is not None:
            return cached.get("d")
        try:
            duration = float(sf.info(str(path)).duration)
        except Exception:
            duration = None
        self._cache_put(path, stat, d=duration)
        return duration

    def _check_image(self, path: Path, side_by_side: bool = False) -> Finding:
        stat = _file_stat(path)
        if stat is None:
            return Finding(MISSING, "file missing")
        if stat[0] < MIN_IMAGE_BYTES:
            return Finding(INVALID, f"file too small ({stat[0]} bytes)")
        cached = self._cache_get(path, stat)
        if cached is None:
            try:
                with Image.open(path) as image:
                    width, height = image.size
                    image.draft("RGB", (max(1, width // 8), max(1, height // 8)))
                    image.load()
            except Exception as e:
                return Finding(INVALID, f"unreadable image: {str(e)[:120]}")
            cached = {"w": width, "h": height}
            self._cache_put(path, stat, **cached)
        width, height = cached.get("w", 0), cached.get("h", 0)
        if min(width, height) < MIN_IMAGE_EDGE:
            return Finding(INVALID, f"image too small ({width}x{height})")
        if side_by_side and abs(width - 2 * height) > 2:
            return Finding(INVALID, f"not a side-by-side colour+depth image ({width}x{height})")
        return Finding(OK)

    def _audio_duration(self, path: Path, stat: Tuple[int, int], kind: str) -> Tuple[Optional[float], Optional[str]]:
        cached = self._cache_get(path, stat)
        if cached is not None:
            return cached.get("d"), cached.get("e")
        duration, error = None, None
        try:
            if kind == "opus":
                duration = _ogg_opus_duration(path)
            elif settings.ASSET_DOCTOR_PROBE_AUDIO:
                duration = _ffprobe_duration(path)
        except Exception as e:
            error = str(e)[:160]
        self._cache_put(path, stat, d=duration, e=error)
        return duration, error

    def _check_audio_file(self, path: Path, kind: str, reference: Optional[float], min_bytes: int = MIN_AUDIO_BYTES) -> Finding:
        stat = _file_stat(path)
        if stat is None:
            return Finding(MISSING, "file missing")
        if stat[0] < min_bytes:
            return Finding(INVALID, f"file too small ({stat[0]} bytes)")
        if kind == "mp3":
            try:
                if not _looks_like_mp3(path):
                    return Finding(INVALID, "not an MP3 stream")
            except OSError as e:
                return Finding(INVALID, f"unreadable: {e}")
        duration, error = self._audio_duration(path, stat, kind)
        if error:
            return Finding(INVALID, error)
        mismatch = _duration_mismatch(duration, reference)
        if mismatch:
            return Finding(INVALID, mismatch)
        return Finding(OK)

    def _read_json(self, path: Path) -> Tuple[Optional[Any], Optional[Finding]]:
        if _file_stat(path) is None:
            return None, Finding(MISSING, "file missing")
        try:
            return json.loads(path.read_text(encoding="utf-8")), None
        except Exception as e:
            return None, Finding(INVALID, f"unreadable JSON: {str(e)[:120]}")

    @staticmethod
    def _missing_metadata_fields(metadata: Dict[str, Any]) -> List[str]:
        params = metadata.get("generation_params") or {}
        info = metadata.get("track_info") or {}
        derived = metadata.get("derived_tags")
        missing = []
        if not (params.get("title") or info.get("title")):
            missing.append("title")
        duration = info.get("duration")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0:
            missing.append("duration")
        if not isinstance(derived, dict) or not derived:
            missing.append("derived_tags")
            return missing
        genre = (derived.get("primary_genre") or "").strip()
        if not genre or genre.lower() == "unknown":
            missing.append("derived_tags.primary_genre")
        if is_ai_track(metadata) and not name_like(derived.get("inspired_artist")):
            missing.append(INSPIRED_ARTIST_FIELD)
        if derived.get("vocals") not in VOCALS:
            missing.append(VOCALS_FIELD)
        for name in REQUIRED_DERIVED_LISTS:
            value = derived.get(name)
            if name == "video_search_terms" and not value:
                value = metadata.get("video_search_terms")
            if not isinstance(value, list) or not [v for v in value if isinstance(v, str) and v.strip()]:
                missing.append(f"derived_tags.{name}")
        return missing

    @staticmethod
    def _repairable_metadata_fields() -> Tuple[str, ...]:
        if settings.ASSET_DOCTOR_BACKFILL_METADATA:
            return REPAIRABLE_METADATA_FIELDS + BACKFILL_METADATA_FIELDS
        return REPAIRABLE_METADATA_FIELDS

    def _deferred_artwork(self, subject: Subject) -> bool:
        if not subject.metadata.get("artwork_generation_deferred"):
            return False
        created = _parse_timestamp(subject.metadata.get("created_at"))
        if created is None:
            return False
        return time.time() - created < settings.ASSET_DOCTOR_DEFERRED_ARTWORK_GRACE_MINUTES * 60

    def _detect_track(self, subject: Subject):
        track_id = subject.id
        findings = subject.findings
        findings.clear()

        metadata, problem = self._read_json(stages.metadata_path(track_id))
        if problem is not None:
            findings["metadata"] = problem
        elif not isinstance(metadata, dict):
            findings["metadata"] = Finding(INVALID, "metadata is not an object")
        else:
            subject.metadata = metadata
            missing = self._missing_metadata_fields(metadata)
            if missing:
                repairable = [m for m in missing if m in self._repairable_metadata_fields()]
                findings["metadata"] = Finding(INVALID if repairable else BLOCKED, "missing " + ", ".join(missing))
            else:
                findings["metadata"] = Finding(OK)

        reference = self._master_duration(track_id)
        if reference is None:
            duration_ms = (subject.metadata.get("track_info") or {}).get("duration")
            reference = duration_ms / 1000.0 if isinstance(duration_ms, (int, float)) and duration_ms > 0 else None
        original = stages.uploaded_original_path(track_id, subject.metadata) if subject.is_upload else None
        subject.info.update({
            "master_wav": _file_stat(stages.master_wav_path(track_id)) is not None,
            "reference_duration": reference,
            "original": original.name if original else None,
            "image_url": bool((subject.metadata.get("track_info") or {}).get("image_url")),
            "artwork_prompt": bool(subject.metadata.get("artwork_prompt")),
        })

        artwork = self._check_image(stages.artwork_path(track_id))
        if artwork.status == MISSING and self._deferred_artwork(subject):
            artwork = Finding(DEFERRED, "video upload inside the artwork grace period")
        findings["artwork"] = artwork
        subject.info["artwork"] = artwork.status == OK

        enriched = self._check_image(stages.enriched_artwork_path(track_id), side_by_side=True)
        if enriched.status == OK and artwork.status == OK:
            art_stat = _file_stat(stages.artwork_path(track_id))
            enriched_stat = _file_stat(stages.enriched_artwork_path(track_id))
            if art_stat and enriched_stat and enriched_stat[1] < art_stat[1]:
                enriched = Finding(INVALID, "stale (older than artwork)")
        if enriched.status == MISSING and artwork.status == DEFERRED:
            enriched = Finding(DEFERRED, "waiting for deferred artwork")
        findings["artwork_enriched"] = enriched

        catalog_mp3 = self._check_audio_file(stages.catalog_mp3_path(track_id), "mp3", reference)
        if catalog_mp3.status == INVALID and catalog_mp3.detail.startswith("duration"):
            mp3_stat = _file_stat(stages.catalog_mp3_path(track_id))
            mp3_duration = (self._cache_get(stages.catalog_mp3_path(track_id), mp3_stat) or {}).get("d") if mp3_stat else None
            if mp3_duration and reference and mp3_duration > reference:
                catalog_mp3 = Finding(BLOCKED, f"{catalog_mp3.detail}: master WAV is shorter than the catalog MP3 (check the master)")
        findings["catalog_mp3"] = catalog_mp3
        webm_problems = []
        for bitrate in stages.OPUS_BITRATES:
            findings[f"opus_{bitrate}"] = self._check_audio_file(stages.opus_path(track_id, bitrate), "opus", reference)
            webm_stat = _file_stat(stages.webm_path(track_id, bitrate))
            if webm_stat is not None:
                try:
                    with open(stages.webm_path(track_id, bitrate), "rb") as handle:
                        magic = handle.read(4)
                except OSError:
                    magic = b""
                if webm_stat[0] < MIN_WEBM_BYTES or magic != EBML_MAGIC:
                    webm_problems.append(bitrate)
        findings["webm"] = Finding(INVALID, "corrupt " + ", ".join(webm_problems)) if webm_problems else Finding(OK)

        findings["audio_features"] = self._detect_features(track_id, reference)
        findings["lyric_timestamps"] = self._detect_lyrics(subject)
        findings["db_flags"] = self._detect_db_flags(subject)
        findings["vector_index"] = self._detect_vector_index(subject)

    def _detect_features(self, track_id: str, reference: Optional[float]) -> Finding:
        path = stages.audio_features_path(track_id)
        stat = _file_stat(path)
        if stat is None:
            return Finding(MISSING, "file missing")
        cached = self._cache_get(path, stat)
        if cached is None:
            data, problem = self._read_json(path)
            if problem is not None:
                return problem
            if not isinstance(data, dict):
                return Finding(INVALID, "features are not an object")
            absent = [key for key in FEATURE_KEYS if key not in data]
            duration = data.get("duration") if isinstance(data.get("duration"), (int, float)) else None
            cached = {"absent": absent, "d": duration}
            self._cache_put(path, stat, **cached)
        if cached.get("absent"):
            return Finding(INVALID, "missing keys " + ", ".join(cached["absent"]))
        mismatch = _duration_mismatch(cached.get("d"), reference)
        return Finding(INVALID, mismatch) if mismatch else Finding(OK)

    def _detect_lyrics(self, subject: Subject) -> Finding:
        path = stages.lyric_timestamps_path(subject.id)
        stat = _file_stat(path)
        placeholder_expected = not stages.track_has_sung_lyrics(subject.metadata)
        subject.info["lyrics_placeholder"] = placeholder_expected
        if stat is None:
            return Finding(MISSING, "file missing" + (" (instrumental placeholder)" if placeholder_expected else ""))
        cached = self._cache_get(path, stat)
        if cached is None:
            data, problem = self._read_json(path)
            if problem is not None:
                return problem
            if not isinstance(data, dict) or not isinstance(data.get("lyrics"), list):
                return Finding(INVALID, "no lyrics list")
            cached = {"lines": len(data["lyrics"])}
            self._cache_put(path, stat, **cached)
        if not placeholder_expected and cached.get("lines", 0) == 0:
            return Finding(INVALID, "empty placeholder but the track has lyrics")
        return Finding(OK)

    def _disk_flags(self, track_id: str) -> Tuple[bool, bool, bool]:
        has_mp3 = any(_file_stat(settings.AUDIO_DIR / f"{track_id}{ext}") is not None
                      for ext in stages.CATALOG_AUDIO_EXTENSIONS)
        has_wav = _file_stat(stages.master_wav_path(track_id)) is not None
        has_artwork = _file_stat(stages.artwork_path(track_id)) is not None
        return has_mp3, has_wav, has_artwork

    def _detect_db_flags(self, subject: Subject) -> Finding:
        if subject.db is None:
            return Finding(MISSING, "no catalog DB row")
        disk = self._disk_flags(subject.id)
        db = (subject.db.get("has_mp3"), subject.db.get("has_wav"), subject.db.get("has_artwork"))
        mismatches = [
            f"{name} db={int(bool(db_value))} disk={int(disk_value)}"
            for name, db_value, disk_value in zip(("has_mp3", "has_wav", "has_artwork"), db, disk)
            if bool(db_value) != disk_value
        ]
        return Finding(INVALID, "; ".join(mismatches)) if mismatches else Finding(OK)

    def _detect_vector_index(self, subject: Subject) -> Finding:
        if subject.db is None or self._index_items is None:
            return Finding(OK, "index state unknown")
        rowid = subject.db.get("rowid") or 0
        if rowid > self._index_items:
            return Finding(MISSING, f"rowid {rowid} beyond index size {self._index_items}")
        return Finding(OK)

    def _detect_shoutout(self, subject: Subject):
        findings = subject.findings
        findings.clear()
        info = subject.info
        typed, _problem = self._read_json(info["json"])
        if isinstance(typed, dict) and typed.get("text_only"):
            subject.metadata = typed
            ok = Finding(OK, "typed, no audio")
            findings.update({"shoutout_audio": ok, "shoutout_transcript": ok, "shoutout_duration": ok,
                             "shoutout_enhancement": ok,
                             "shoutout_db": ok if subject.db is not None else Finding(MISSING, "no DB row")})
            return
        audio = self._check_audio_file(info["mp3"], "mp3", None, min_bytes=MIN_SHOUTOUT_AUDIO_BYTES)
        if audio.status == OK:
            stat = _file_stat(info["mp3"])
            duration = (self._cache_get(info["mp3"], stat) or {}).get("d") if stat else None
            info["audio_duration"] = duration
            if duration is not None and duration < MIN_SHOUTOUT_DURATION_S:
                audio = Finding(INVALID, f"audio only {duration:.2f}s long")
        findings["shoutout_audio"] = audio

        transcript, problem = self._read_json(info["json"])
        if problem is not None:
            findings["shoutout_transcript"] = problem
        elif not isinstance(transcript, dict) or not (transcript.get("full_transcription") or "").strip():
            findings["shoutout_transcript"] = Finding(INVALID, "empty transcription")
        else:
            subject.metadata = transcript
            findings["shoutout_transcript"] = Finding(OK)

        transcript_ok = findings["shoutout_transcript"].status == OK
        audio_ok = audio.status == OK
        if transcript_ok and audio_ok:
            stored = (subject.metadata.get("transcription_metadata") or {}).get("duration")
            actual = info.get("audio_duration")
            if not isinstance(stored, (int, float)) or stored <= 0:
                findings["shoutout_duration"] = Finding(INVALID, "duration missing")
            elif actual is not None and abs(stored - actual) > SHOUTOUT_DURATION_TOLERANCE_S:
                findings["shoutout_duration"] = Finding(INVALID, f"duration {stored:.1f}s vs audio {actual:.1f}s")
            else:
                findings["shoutout_duration"] = Finding(OK)
        else:
            findings["shoutout_duration"] = Finding(BLOCKED, "needs valid audio and transcript")

        if subject.db is None:
            findings["shoutout_db"] = Finding(MISSING if transcript_ok and audio_ok else BLOCKED, "no DB row")
        elif not audio_ok and subject.db.get("has_mp3"):
            findings["shoutout_db"] = Finding(INVALID, "DB row points at missing/invalid audio")
        elif transcript_ok and audio_ok and not subject.db.get("has_mp3"):
            findings["shoutout_db"] = Finding(INVALID, "has_mp3 flag is 0")
        else:
            findings["shoutout_db"] = Finding(OK)

        findings["shoutout_enhancement"] = self._detect_shoutout_enhancement(subject, transcript_ok and audio_ok)

    @staticmethod
    def _detect_shoutout_enhancement(subject: Subject, rendered: bool) -> Finding:
        if not settings.ASSET_DOCTOR_SHOUTOUT_REENHANCE_ENABLED or not rendered:
            return Finding(OK)
        version = subject.metadata.get("enhancement_version")
        version = version if isinstance(version, int) and not isinstance(version, bool) else 0
        current = settings.SHOUTOUT_ENHANCEMENT_VERSION
        if version >= current:
            return Finding(OK)
        return Finding(INVALID, f"rendered with enhancement v{version}, current is v{current}")

    def _detect_subject(self, subject: Subject):
        if subject.kind == "track":
            self._detect_track(subject)
        else:
            self._detect_shoutout(subject)

    def _detect_one(self, subject: Subject, key: str) -> Finding:
        self._detect_subject(subject)
        return subject.findings.get(key, Finding(OK))

    def _load_track_subjects(self, track_ids: Optional[Set[str]]) -> List[Subject]:
        conn = get_pooled_connection(settings.CATALOG_DATABASE_URL)
        try:
            cursor = conn.cursor()
            columns = "track_id, rowid, has_mp3, has_wav, has_artwork, metadata_json"
            if track_ids is None:
                cursor.execute(f"SELECT {columns} FROM tracks")
            else:
                cursor.execute(f"SELECT {columns} FROM tracks WHERE track_id = ANY(%s)", (list(track_ids),))
            rows = cursor.fetchall()
        finally:
            conn.close()

        subjects = []
        for track_id, rowid, has_mp3, has_wav, has_artwork, metadata_json in rows:
            try:
                metadata = json.loads(metadata_json) if metadata_json else {}
            except Exception:
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            subject = Subject("track", track_id, metadata=metadata, db={
                "rowid": rowid, "has_mp3": bool(has_mp3), "has_wav": bool(has_wav), "has_artwork": bool(has_artwork)
            })
            subject.info["db_metadata"] = metadata
            subjects.append(subject)

        if track_ids is not None:
            known = {subject.id for subject in subjects}
            for track_id in sorted(track_ids - known):
                if _file_stat(stages.metadata_path(track_id)) and _file_stat(stages.master_wav_path(track_id)):
                    subjects.append(Subject("track", track_id))

        subjects.sort(key=lambda s: (not s.is_upload, -(_parse_timestamp(s.metadata.get("created_at")) or 0.0)))
        return subjects

    def _load_shoutout_subjects(self) -> List[Subject]:
        conn = get_pooled_connection(settings.USER_CONTENT_DATABASE_URL)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT content_id, user_id, has_mp3, parent_id, duration FROM shoutouts")
            rows = cursor.fetchall()
        finally:
            conn.close()

        db_rows = {
            content_id: {"user_id": user_id, "has_mp3": bool(has_mp3), "parent_id": parent_id, "duration": duration}
            for content_id, user_id, has_mp3, parent_id, duration in rows
        }
        ids: Set[str] = set(db_rows)
        if settings.USERS_DIR.exists():
            for user_dir in settings.USERS_DIR.iterdir():
                if not user_dir.name.isdigit():
                    continue
                shoutouts_dir = user_dir / "shoutouts"
                if not shoutouts_dir.is_dir():
                    continue
                for path in shoutouts_dir.iterdir():
                    if path.suffix in (".mp3", ".json") and SHOUTOUT_STEM.match(path.stem):
                        ids.add(f"{user_dir.name}_{path.stem}")

        subjects = []
        for shoutout_id in sorted(ids):
            user_part, _sep, stem = shoutout_id.partition("_")
            if not user_part.isdigit() or not stem:
                continue
            shoutouts_dir = settings.USERS_DIR / user_part / "shoutouts"
            uploads_dir = settings.USERS_DIR / user_part / "uploads"
            subject = Subject("shoutout", shoutout_id, db=db_rows.get(shoutout_id), info={
                "user_id": int(user_part),
                "timestamp": stem,
                "mp3": shoutouts_dir / f"{stem}.mp3",
                "json": shoutouts_dir / f"{stem}.json",
                "source_webm": uploads_dir / f"{stem}.webm",
                "source_json": uploads_dir / f"{stem}.json",
            })
            subjects.append(subject)
        return subjects

    def _current_index_items(self) -> Optional[int]:
        vector_db = self._svc("vector_db")
        if vector_db is not None:
            try:
                return int(vector_db.current_annoy_index().get_n_items())
            except Exception:
                return None
        best = None
        try:
            from annoy import AnnoyIndex
            from pathlib import Path
            from services.base_vector_database_service import EMBEDDING_DIM, index_paths
            for name in index_paths(settings.CATALOG_EMBEDDINGS_DIR, "catalog"):
                path = Path(name)
                if not path.exists():
                    continue
                index = AnnoyIndex(EMBEDDING_DIM, "angular")
                index.load(str(path), prefault=False)
                items = index.get_n_items()
                index.unload()
                best = items if best is None else max(best, items)
        except Exception as e:
            log_service.warning(f"[AssetDoctor] Could not read vector index files: {e}")
        return best

    def _is_gpu(self, check: AssetCheck, subject: Subject) -> bool:
        if check.key == "lyric_timestamps" and subject.info.get("lyrics_placeholder"):
            return False
        return check.gpu

    def _is_heavy(self, check: AssetCheck, subject: Subject) -> bool:
        if check.key == "lyric_timestamps" and subject.info.get("lyrics_placeholder"):
            return False
        if check.key == "metadata":
            detail = subject.findings.get("metadata", Finding(OK)).detail
            return "derived_tags" in detail
        return check.heavy

    def _fingerprint(self, check: AssetCheck, subject: Subject) -> str:
        if subject.kind == "shoutout":
            paths = [subject.info["source_webm"], subject.info["source_json"], subject.info["json"]]
        else:
            paths = [stages.master_wav_path(subject.id), stages.metadata_path(subject.id)]
            if check.key == "artwork_enriched":
                paths.append(stages.artwork_path(subject.id))
        return "|".join(str(_file_stat(path)) for path in paths)

    def _repair_blocker(self, check: AssetCheck, subject: Subject, finding: Finding) -> Optional[str]:
        track_id = subject.id
        needs = {
            "metadata": ("catalog",),
            "artwork": ("catalog",),
            "artwork_enriched": ("artwork_enrichment",),
            "catalog_mp3": ("transcoding", "catalog"),
            "opus_128k": ("transcoding",),
            "opus_192k": ("transcoding",),
            "opus_256k": ("transcoding",),
            "webm": ("transcoding",),
            "audio_features": ("features",),
            "lyric_timestamps": ("lyrics",),
            "db_flags": ("catalog",),
            "vector_index": ("vector_db", "catalog"),
            "shoutout_audio": ("speech_enhancement", "user_content"),
            "shoutout_transcript": ("speech_enhancement", "user_content", "ai"),
            "shoutout_duration": ("user_content",),
            "shoutout_db": ("user_content",),
            "shoutout_enhancement": ("speech_enhancement", "user_content"),
        }.get(check.key, ())
        unbound = [name for name in needs if self._svc(name) is None]
        if unbound:
            return "service unavailable: " + ", ".join(unbound)

        if check.key in ("catalog_mp3", "opus_128k", "opus_192k", "opus_256k", "audio_features", "webm"):
            if not subject.info.get("master_wav"):
                return "master WAV missing"
        if check.key == "lyric_timestamps" and not subject.info.get("lyrics_placeholder"):
            if not subject.info.get("master_wav") and stages.demucs_vocal_stem(track_id) is None:
                return "no audio source"
        if check.key == "artwork_enriched" and subject.findings.get("artwork", Finding(OK)).status != OK:
            return "artwork missing"
        if check.key == "artwork":
            has_source = (
                subject.info.get("original")
                or (subject.info.get("image_url") and self._svc("suno") is not None)
                or (self._svc("artwork_generation") is not None and stages.artwork_request(subject.metadata).get("artwork_prompt"))
            )
            if not has_source:
                return "no artwork source (original, image URL or prompt)"
        if check.key == "metadata":
            if finding.status == MISSING or finding.detail.startswith(("unreadable", "metadata is not")):
                if not subject.info.get("db_metadata"):
                    return "no metadata copy in DB"
            else:
                fields = self._missing_metadata_fields(subject.metadata)
                can_fill_duration = "duration" in fields and subject.info.get("master_wav")
                fillable_tags = [f for f in fields if f.startswith("derived_tags") and f in self._repairable_metadata_fields()]
                can_fill_tags = bool(fillable_tags) and self._svc("enriched_metadata") is not None
                can_fill_artist = INSPIRED_ARTIST_FIELD in fields and ai_artist(subject.metadata)[0] is not None
                if not (can_fill_duration or can_fill_tags or can_fill_artist or VOCALS_FIELD in fields):
                    return "fields need manual edit or the enrichment service"
        if check.key == "db_flags" and subject.db is None and not subject.info.get("master_wav"):
            return "master WAV missing"
        if check.key in ("shoutout_audio", "shoutout_transcript", "shoutout_enhancement"):
            if _file_stat(subject.info["source_webm"]) is None or _file_stat(subject.info["source_json"]) is None:
                return "source recording missing"
        return None

    def _busy_reason(self, gpu: bool) -> Optional[str]:
        upload = self._svc("upload")
        if upload is not None and getattr(upload, "_inflight_uploads", None):
            return "upload in progress"
        orchestrator = self._svc("orchestrator")
        if orchestrator is not None:
            for lane in range(1, 7):
                queue = getattr(orchestrator, f"lane{lane}_queue", None)
                if queue is not None and queue.qsize() > 0:
                    return f"Suno pipeline lane {lane} busy"
        if gpu:
            holder = models_global.gpu_lease_holder()
            if holder is not None:
                return f"GPU lease held by {holder}"
        return None

    async def _wait_until_idle(self, gpu: bool) -> bool:
        deadline = time.monotonic() + settings.ASSET_DOCTOR_MAX_BUSY_WAIT_S
        while True:
            reason = self._busy_reason(gpu)
            if reason is None:
                return True
            if time.monotonic() >= deadline:
                log_service.catalog(f"[AssetDoctor] Still busy ({reason}); postponing remaining repairs")
                return False
            await asyncio.sleep(settings.ASSET_DOCTOR_BUSY_POLL_S)

    def _prune_repair_window(self):
        cutoff = time.monotonic() - 3600
        while self._repair_times and self._repair_times[0] < cutoff:
            self._repair_times.popleft()
        while self._reenhance_times and self._reenhance_times[0] < cutoff:
            self._reenhance_times.popleft()

    def _reenhance_capped(self) -> bool:
        self._prune_repair_window()
        return len(self._reenhance_times) >= max(0, settings.ASSET_DOCTOR_SHOUTOUT_REENHANCE_PER_HOUR)

    def _backoff_seconds(self, attempts: int) -> float:
        base = settings.ASSET_DOCTOR_RETRY_BASE_MINUTES * 60 * (2 ** max(0, attempts - 1))
        return min(base, settings.ASSET_DOCTOR_RETRY_MAX_HOURS * 3600)

    async def _attempt_repair(self, subject: Subject, check: AssetCheck) -> str:
        finding = await asyncio.to_thread(self._detect_one, subject, check.key)
        if finding.status not in PROBLEM_STATUSES:
            return "already_ok"

        blocker = self._repair_blocker(check, subject, finding)
        if blocker:
            subject.findings[check.key] = Finding(finding.status, f"{finding.detail} [blocked: {blocker}]")
            return "blocked"

        key = f"{subject.kind}:{subject.id}:{check.key}"
        fingerprint = self._fingerprint(check, subject)
        record = self._attempts.get(key)
        if record and record.get("fingerprint") != fingerprint:
            record = None
        now = time.time()
        if record and record.get("failed"):
            return "failed_permanently"
        if record and record.get("next_attempt", 0) > now:
            return "backoff"

        reenhance = check.key == "shoutout_enhancement"
        if reenhance and self._reenhance_capped():
            return "reenhance_capped"

        heavy = self._is_heavy(check, subject)
        if heavy:
            self._prune_repair_window()
            if len(self._repair_times) >= max(0, settings.ASSET_DOCTOR_MAX_REPAIRS_PER_HOUR):
                return "rate_limited"

        if not await self._wait_until_idle(self._is_gpu(check, subject)):
            return "busy"

        if heavy:
            self._repair_times.append(time.monotonic())
        if reenhance:
            self._reenhance_times.append(time.monotonic())
        attempts = (record or {}).get("attempts", 0) + 1
        record = {"attempts": attempts, "last_attempt": now, "fingerprint": fingerprint,
                  "check": check.key, "subject": subject.id, "kind": subject.kind, "title": subject.title}
        self._attempts[key] = record

        error = ""
        transient = False
        started = time.monotonic()
        try:
            await getattr(self, f"_repair_{check.key}")(subject, finding)
        except models_global.GPUOutOfMemoryError as e:
            error = f"GPU out of memory: {e}"
            transient = True
        except Exception as e:
            error = f"{type(e).__name__}: {str(e)[:200]}"

        verified = await asyncio.to_thread(self._detect_one, subject, check.key)
        elapsed = time.monotonic() - started
        if verified.status not in PROBLEM_STATUSES:
            self._attempts.pop(key, None)
            log_service.catalog(f"[AssetDoctor] Repaired {check.key} for {subject.kind} {subject.id} ({elapsed:.1f}s)")
            await self._save_state()
            return "repaired"

        if transient:
            record["attempts"] = attempts - 1
        record["last_error"] = error or verified.detail or verified.status
        record["next_attempt"] = now + self._backoff_seconds(max(1, record["attempts"]))
        if record["attempts"] >= settings.ASSET_DOCTOR_MAX_ATTEMPTS:
            record["failed"] = True
            log_service.warning(
                f"[AssetDoctor] Giving up on {check.key} for {subject.kind} {subject.id} after "
                f"{record['attempts']} attempts: {record['last_error']}"
            )
        else:
            log_service.warning(
                f"[AssetDoctor] Repair of {check.key} for {subject.kind} {subject.id} failed "
                f"(attempt {record['attempts']}/{settings.ASSET_DOCTOR_MAX_ATTEMPTS}): {record['last_error']}"
            )
        await self._save_state()
        return "failed"

    async def _repair_phase(self, subjects: List[Subject]) -> Dict[str, int]:
        summary: Counter = Counter()
        rate_limited = False
        reenhance_capped = False
        for subject in subjects:
            checks = TRACK_CHECKS if subject.kind == "track" else SHOUTOUT_CHECKS
            for check in checks:
                finding = subject.findings.get(check.key)
                if finding is None or finding.status not in PROBLEM_STATUSES:
                    continue
                if reenhance_capped and check.key == "shoutout_enhancement":
                    summary["reenhance_capped"] += 1
                    continue
                if rate_limited and self._is_heavy(check, subject):
                    summary["rate_limited"] += 1
                    continue
                result = await self._attempt_repair(subject, check)
                summary[result] += 1
                if result == "rate_limited":
                    rate_limited = True
                elif result == "reenhance_capped":
                    reenhance_capped = True
                elif result == "busy":
                    summary["postponed_busy"] += 1
                    self._followup_delay = settings.ASSET_DOCTOR_BUSY_POLL_S * 10
                    return dict(summary)
        followups = []
        if rate_limited and self._repair_times:
            followups.append(self._repair_times[0] + 3600 - time.monotonic() + 5)
        if reenhance_capped and self._reenhance_times:
            followups.append(self._reenhance_times[0] + 3600 - time.monotonic() + 5)
        if followups:
            self._followup_delay = max(60.0, min(followups))
        return dict(summary)

    @staticmethod
    def _quarantine_sync(path: Path) -> Optional[Path]:
        if not path.exists():
            return None
        target_dir = settings.ASSET_DOCTOR_QUARANTINE_DIR / path.parent.name
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{path.stem}.{int(time.time())}{path.suffix}"
        shutil.move(str(path), str(target))
        log_service.catalog(f"[AssetDoctor] Quarantined invalid file {path.name} -> {target}")
        return target

    async def _quarantine(self, path: Path):
        await asyncio.to_thread(self._quarantine_sync, path)

    async def _refresh_flags(self, subject: Subject):
        catalog = self._svc("catalog")
        if catalog is not None and subject.db is not None:
            flags = await asyncio.to_thread(catalog.refresh_track_flags, subject.id)
            subject.db.update({"has_mp3": flags[0], "has_wav": flags[1], "has_artwork": flags[2]})

    async def _save_track_metadata(self, track_id: str, metadata: Dict[str, Any]):
        catalog = self._svc("catalog")
        await asyncio.to_thread(catalog.write_track_metadata, track_id, metadata)
        if track_id in catalog.tracks:
            catalog.tracks[track_id] = metadata
        vector_db = self._svc("vector_db")
        if vector_db is not None:
            try:
                await asyncio.to_thread(vector_db.add_single_track, metadata)
            except Exception as e:
                log_service.warning(f"[AssetDoctor] Embedding refresh failed for {track_id}: {e}")

    async def _repair_metadata(self, subject: Subject, finding: Finding):
        track_id = subject.id
        if finding.status == MISSING or finding.detail.startswith(("unreadable", "metadata is not")):
            metadata = copy.deepcopy(subject.info.get("db_metadata") or {})
            if not metadata:
                raise RuntimeError("no metadata copy available")
            metadata["id"] = track_id
            await self._save_track_metadata(track_id, metadata)
            return

        metadata = copy.deepcopy(subject.metadata)
        missing = self._missing_metadata_fields(metadata)
        changed = False
        if "duration" in missing:
            duration = await asyncio.to_thread(self._master_duration, track_id)
            if duration:
                metadata.setdefault("track_info", {})["duration"] = int(round(duration * 1000))
                changed = True

        if INSPIRED_ARTIST_FIELD in missing:
            catalog = self._svc("catalog")
            tracks = catalog.tracks if catalog is not None else {}
            artist, source = ai_artist(metadata, tracks, known_artists(tracks))
            if artist:
                metadata.setdefault("derived_tags", {})["inspired_artist"] = artist
                missing.remove(INSPIRED_ARTIST_FIELD)
                changed = True
                log_service.info(f"[Asset doctor] {track_id}: inspired artist set to {artist!r} ({source})")

        if VOCALS_FIELD in missing:
            derived = metadata.setdefault("derived_tags", {})
            derived.pop("vocals", None)
            if settled_vocals(metadata):
                derived["vocals"] = settled_vocals(metadata)
                missing.remove(VOCALS_FIELD)
                changed = True

        derived_missing = [m for m in missing if m.startswith("derived_tags") and m in self._repairable_metadata_fields()]
        enrichment = self._svc("enriched_metadata")
        if derived_missing and enrichment is not None:
            enriched = await enrichment.enrich_metadata(copy.deepcopy(metadata))
            new_tags = (enriched or {}).get("derived_tags") or {}
            if new_tags:
                derived = metadata.get("derived_tags") if isinstance(metadata.get("derived_tags"), dict) else {}
                for name, value in new_tags.items():
                    current = derived.get(name)
                    empty = current in (None, "", [], {}) or (name == "primary_genre" and str(current).lower() == "unknown")
                    if empty and value not in (None, "", []):
                        derived[name] = value
                if not derived.get("enriched_at"):
                    derived["enriched_at"] = new_tags.get("enriched_at") or _now_iso()
                metadata["derived_tags"] = derived
                if (enriched or {}).get("generation_params", {}).get("style_canonical") and \
                        not metadata.get("generation_params", {}).get("style_canonical"):
                    metadata.setdefault("generation_params", {})["style_canonical"] = enriched["generation_params"]["style_canonical"]
                changed = True

        if VOCALS_FIELD in missing and metadata.get("derived_tags", {}).get("vocals") not in VOCALS:
            metadata["derived_tags"]["vocals"] = "unknown"
            changed = True

        if not changed:
            raise RuntimeError("nothing could be filled in")
        await self._save_track_metadata(track_id, metadata)
        subject.metadata = metadata

    async def _repair_artwork(self, subject: Subject, finding: Finding):
        track_id = subject.id
        if finding.status == INVALID:
            await self._quarantine(stages.artwork_path(track_id))
            await self._quarantine(stages.enriched_artwork_path(track_id))
        metadata = subject.metadata
        original = None
        if subject.is_upload and metadata.get("source_media_type") != "video":
            original = await asyncio.to_thread(stages.uploaded_original_path, track_id, metadata)
        has_artwork, source = await stages.ensure_track_artwork(
            track_id=track_id,
            metadata=metadata,
            embedded_artwork_service=self._svc("embedded_artwork"),
            original_path=original,
            suno_service=None if subject.is_upload else self._svc("suno"),
            artwork_generation_service=self._svc("artwork_generation")
        )
        if has_artwork:
            check = await asyncio.to_thread(self._check_image, stages.artwork_path(track_id))
            if check.status != OK:
                await self._quarantine(stages.artwork_path(track_id))
                if source == "downloaded" and self._svc("artwork_generation") is not None:
                    has_artwork, source = await stages.ensure_track_artwork(
                        track_id=track_id, metadata=metadata,
                        artwork_generation_service=self._svc("artwork_generation")
                    )
        await self._refresh_flags(subject)
        if has_artwork and source == "generated" and subject.is_upload and metadata.get("artwork_generation_deferred"):
            updated = copy.deepcopy(metadata)
            updated["artwork_generated"] = True
            updated["artwork_generation_deferred"] = False
            await self._save_track_metadata(track_id, updated)
            subject.metadata = updated
        if not has_artwork:
            raise RuntimeError("no artwork could be produced")
        log_service.catalog(f"[AssetDoctor] Artwork for {track_id} restored from {source}")

    async def _repair_artwork_enriched(self, subject: Subject, finding: Finding):
        if finding.status == INVALID and not finding.detail.startswith("stale"):
            await self._quarantine(stages.enriched_artwork_path(subject.id))
        if not await stages.enrich_track_artwork(self._svc("artwork_enrichment"), subject.id):
            raise RuntimeError("depth enrichment produced no output")

    async def _repair_catalog_mp3(self, subject: Subject, finding: Finding):
        if finding.status == INVALID:
            await self._quarantine(stages.catalog_mp3_path(subject.id))
        created = await stages.create_catalog_mp3(self._svc("transcoding"), subject.id, stages.master_wav_path(subject.id))
        await self._refresh_flags(subject)
        if not created:
            raise RuntimeError("MP3 transcode failed")

    async def _repair_opus(self, subject: Subject, finding: Finding, bitrate: str):
        if finding.status == INVALID:
            await self._quarantine(stages.opus_path(subject.id, bitrate))
            await self._quarantine(stages.webm_path(subject.id, bitrate))
        if not await stages.create_opus_variant(self._svc("transcoding"), subject.id, bitrate, stages.master_wav_path(subject.id)):
            raise RuntimeError(f"Opus {bitrate} transcode failed")

    async def _repair_opus_128k(self, subject: Subject, finding: Finding):
        await self._repair_opus(subject, finding, "128k")

    async def _repair_opus_192k(self, subject: Subject, finding: Finding):
        await self._repair_opus(subject, finding, "192k")

    async def _repair_opus_256k(self, subject: Subject, finding: Finding):
        await self._repair_opus(subject, finding, "256k")

    async def _repair_webm(self, subject: Subject, finding: Finding):
        transcoding = self._svc("transcoding")
        for bitrate in stages.OPUS_BITRATES:
            if bitrate in finding.detail:
                await self._quarantine(stages.webm_path(subject.id, bitrate))
                created = await transcoding.get_or_create_webm(
                    track_id=subject.id, wav_path=stages.master_wav_path(subject.id), bitrate=bitrate
                )
                if not created:
                    raise RuntimeError(f"WebM {bitrate} rebuild failed")

    async def _repair_audio_features(self, subject: Subject, finding: Finding):
        if finding.status == INVALID:
            await self._quarantine(stages.audio_features_path(subject.id))
        if not await stages.extract_audio_features(self._svc("features"), subject.id, stages.master_wav_path(subject.id)):
            raise RuntimeError("feature extraction failed")

    async def _repair_lyric_timestamps(self, subject: Subject, finding: Finding):
        if finding.status == INVALID:
            await self._quarantine(stages.lyric_timestamps_path(subject.id))
        master = stages.master_wav_path(subject.id)
        result = await stages.generate_lyric_timestamps(
            self._svc("lyrics"), subject.id, subject.metadata, master if subject.info.get("master_wav") else None
        )
        if not result:
            raise RuntimeError("no lyric timestamps produced")

    async def _repair_db_flags(self, subject: Subject, finding: Finding):
        catalog = self._svc("catalog")
        if subject.db is None:
            flags = await asyncio.to_thread(catalog.compute_track_flags, subject.id)
            await asyncio.to_thread(catalog._upsert_track_to_db, subject.id, subject.metadata, *flags)
            state = await asyncio.to_thread(catalog.get_track_db_state, subject.id)
            subject.db = state
            if subject.id not in catalog.tracks:
                catalog.add_track_to_memory(subject.id, subject.metadata, has_artwork=flags[2])
            return
        flags = await asyncio.to_thread(catalog.refresh_track_flags, subject.id)
        subject.db.update({"has_mp3": flags[0], "has_wav": flags[1], "has_artwork": flags[2]})

    async def _repair_vector_index(self, subject: Subject, finding: Finding):
        if time.monotonic() - self._vector_rebuilt_at < VECTOR_REBUILD_COOLDOWN_S:
            self._index_items = await asyncio.to_thread(self._current_index_items)
            return
        vector_db = self._svc("vector_db")
        catalog = self._svc("catalog")
        async with models_global.gpu_lease("Asset doctor vector index"):
            await asyncio.to_thread(vector_db.rebuild_indexes, catalog)
        self._vector_rebuilt_at = time.monotonic()
        self._index_items = await asyncio.to_thread(self._current_index_items)

    async def _register_shoutout(self, subject: Subject):
        user_content = self._svc("user_content")
        info = subject.info
        data, problem = await asyncio.to_thread(self._read_json, info["json"])
        if problem is not None or not isinstance(data, dict):
            raise RuntimeError("transcript unavailable after repair")
        parent_id = data.get("parent_id") or (subject.db or {}).get("parent_id")
        data["id"] = subject.id
        data["content_type"] = kind_of({**data, "parent_id": parent_id})
        if parent_id:
            data["parent_id"] = parent_id
        await asyncio.to_thread(self._write_json_atomic, info["json"], data)
        has_mp3 = _file_stat(info["mp3"]) is not None
        await asyncio.to_thread(user_content._upsert_shoutout_to_db, subject.id, info["user_id"], data, has_mp3, parent_id)
        if has_mp3 or data.get("text_only"):
            user_content.remember_shoutout(subject.id, data)
        else:
            user_content.forget_shoutout(subject.id)
        subject.metadata = data
        subject.db = {"user_id": info["user_id"], "has_mp3": has_mp3, "parent_id": parent_id,
                      "duration": (data.get("transcription_metadata") or {}).get("duration")}

    async def _repair_shoutout_audio(self, subject: Subject, finding: Finding):
        info = subject.info
        enhancement = self._svc("speech_enhancement")
        if finding.status == INVALID:
            await self._quarantine(info["mp3"])
        transcript_ok = subject.findings.get("shoutout_transcript", Finding(MISSING)).status == OK
        if transcript_ok:
            ok = await enhancement.rerender_audio(str(info["source_webm"]), str(info["mp3"]), kind_of(subject.metadata),
                                                  str(info["source_json"]), str(info["json"]))
        else:
            ok = await enhancement.enhance_audio(str(info["source_webm"]), str(info["mp3"]), kind_of(subject.metadata),
                                                 str(info["source_json"]), str(info["json"]), self._svc("ai"))
        if not ok:
            raise RuntimeError("shoutout re-render failed")
        await self._register_shoutout(subject)

    async def _repair_shoutout_transcript(self, subject: Subject, finding: Finding):
        info = subject.info
        if finding.status == INVALID:
            await self._quarantine(info["json"])
        ok = await self._svc("speech_enhancement").enhance_audio(
            str(info["source_webm"]), str(info["mp3"]), kind_of(subject.metadata),
            str(info["source_json"]), str(info["json"]), self._svc("ai")
        )
        if not ok:
            raise RuntimeError("shoutout reprocessing failed")
        await self._register_shoutout(subject)

    async def _repair_shoutout_duration(self, subject: Subject, finding: Finding):
        info = subject.info
        duration = await asyncio.to_thread(_ffprobe_duration, info["mp3"])
        if not duration:
            raise RuntimeError("could not measure shoutout duration")
        data, problem = await asyncio.to_thread(self._read_json, info["json"])
        if problem is not None or not isinstance(data, dict):
            raise RuntimeError("transcript unreadable")
        meta = data.get("transcription_metadata") if isinstance(data.get("transcription_metadata"), dict) else {}
        meta["duration"] = round(duration, 3)
        data["transcription_metadata"] = meta
        await asyncio.to_thread(self._write_json_atomic, info["json"], data)
        await self._register_shoutout(subject)

    async def _repair_shoutout_db(self, subject: Subject, finding: Finding):
        await self._register_shoutout(subject)

    @staticmethod
    def _reenhance_paths(subject: Subject) -> Dict[str, Path]:
        info = subject.info
        stamp = int(time.time())
        version = subject.metadata.get("enhancement_version") or 0
        work_dir = settings.ASSET_DOCTOR_QUARANTINE_DIR / "_shoutout_reenhance_work"
        backup_dir = settings.ASSET_DOCTOR_QUARANTINE_DIR / "shoutout_reenhance_backup" / str(info["user_id"])
        stem = info["timestamp"]
        return {
            "work_dir": work_dir,
            "work_mp3": work_dir / f"{info['user_id']}_{stem}.mp3",
            "work_json": work_dir / f"{info['user_id']}_{stem}.json",
            "backup_dir": backup_dir,
            "backup_mp3": backup_dir / f"{stem}.v{version}.{stamp}.mp3",
            "backup_json": backup_dir / f"{stem}.v{version}.{stamp}.json",
        }

    @staticmethod
    def _stage_reenhance_sync(info: Dict[str, Any], paths: Dict[str, Path]):
        paths["work_dir"].mkdir(parents=True, exist_ok=True)
        for leftover in (paths["work_mp3"], paths["work_json"]):
            if leftover.exists():
                os.replace(leftover, leftover.with_name(f"{leftover.name}.stale.{time.time_ns()}"))
        shutil.copy2(info["json"], paths["work_json"])

    @staticmethod
    def _install_reenhance_sync(info: Dict[str, Any], paths: Dict[str, Path]):
        paths["backup_dir"].mkdir(parents=True, exist_ok=True)
        shutil.copy2(info["mp3"], paths["backup_mp3"])
        shutil.copy2(info["json"], paths["backup_json"])
        os.replace(paths["work_mp3"], info["mp3"])
        os.replace(paths["work_json"], info["json"])
        work_sting, sting = sting_file(paths["work_mp3"]), sting_file(info["mp3"])
        if os.path.exists(work_sting):
            os.replace(work_sting, sting)
        elif os.path.exists(sting):
            os.remove(sting)
        log_service.detail(f"[AssetDoctor] Backed up previous shoutout render to {paths['backup_mp3']}", "catalog")

    async def _repair_shoutout_enhancement(self, subject: Subject, finding: Finding):
        info = subject.info
        paths = self._reenhance_paths(subject)
        await asyncio.to_thread(self._stage_reenhance_sync, info, paths)
        ok = await self._svc("speech_enhancement").rerender_audio(
            str(info["source_webm"]), str(paths["work_mp3"]), kind_of(subject.metadata),
            str(info["source_json"]), str(paths["work_json"])
        )
        rendered, _problem = await asyncio.to_thread(self._read_json, paths["work_json"])
        if not ok or not isinstance(rendered, dict) or _file_stat(paths["work_mp3"]) is None:
            raise RuntimeError("shoutout re-render failed; previous audio kept")
        if not rendered.get("audio_enhanced") or rendered.get("enhancement_version") != settings.SHOUTOUT_ENHANCEMENT_VERSION:
            raise RuntimeError("enhancement chain fell back to raw audio; previous audio kept")
        check = await asyncio.to_thread(self._check_audio_file, paths["work_mp3"], "mp3", None, MIN_SHOUTOUT_AUDIO_BYTES)
        if check.status != OK:
            raise RuntimeError(f"re-rendered audio invalid ({check.detail}); previous audio kept")
        await asyncio.to_thread(self._install_reenhance_sync, info, paths)
        await self._register_shoutout(subject)

    def _issue_entry(self, subject: Subject, key: str, finding: Finding) -> Dict[str, Any]:
        record = self._attempts.get(f"{subject.kind}:{subject.id}:{key}") or {}
        entry = {
            "kind": subject.kind,
            "id": subject.id,
            "check": key,
            "status": finding.status,
            "detail": finding.detail,
        }
        if subject.kind == "track":
            entry.update({
                "title": subject.title,
                "upload": subject.is_upload,
                "sources": {
                    "master_wav": subject.info.get("master_wav"),
                    "original": subject.info.get("original"),
                    "image_url": subject.info.get("image_url"),
                    "artwork_prompt": subject.info.get("artwork_prompt"),
                },
            })
        else:
            entry["sources"] = {
                "source_webm": _file_stat(subject.info["source_webm"]) is not None,
                "source_json": _file_stat(subject.info["source_json"]) is not None,
            }
        if record:
            entry["repair"] = {k: record.get(k) for k in ("attempts", "failed", "next_attempt", "last_error")}
        return entry

    def _build_report(self, reason: str, targeted: bool, tracks: List[Subject], shoutouts: List[Subject],
                      started: float) -> Dict[str, Any]:
        counts: Dict[str, Dict[str, int]] = {}
        issues = []
        for subject in tracks + shoutouts:
            for key, finding in subject.findings.items():
                if finding.status == OK:
                    continue
                bucket = counts.setdefault(key, {"missing": 0, "invalid": 0, "blocked": 0, "deferred": 0, "uploads": 0})
                bucket[finding.status] = bucket.get(finding.status, 0) + 1
                if subject.is_upload:
                    bucket["uploads"] += 1
                issues.append(self._issue_entry(subject, key, finding))
        issues.sort(key=lambda issue: (not issue.get("upload", False), issue["kind"], issue["id"], issue["check"]))
        return {
            "reason": reason,
            "targeted": targeted,
            "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat(),
            "tracks_scanned": len(tracks),
            "uploads_scanned": sum(1 for subject in tracks if subject.is_upload),
            "shoutouts_scanned": len(shoutouts),
            "index_items": self._index_items,
            "counts": counts,
            "issue_count": len(issues),
            "issues": issues[:MAX_REPORT_ISSUES],
        }

    @staticmethod
    def _summary_line(report: Dict[str, Any]) -> str:
        actionable = []
        waiting = {}
        for key, bucket in sorted(report.get("counts", {}).items()):
            found = [f"{status} {bucket[status]}" for status in ("missing", "invalid") if bucket.get(status)]
            if found:
                actionable.append(f"{key} {', '.join(found)}")
            for status in ("blocked", "deferred"):
                if bucket.get(status):
                    waiting[status] = waiting.get(status, 0) + bucket[status]
        repairs = report.get("repairs") or {}
        repair_text = ", ".join(f"{k} {v}" for k, v in sorted(repairs.items())) if repairs else "none"
        waiting_text = ", ".join(f"{count} {status}" for status, count in sorted(waiting.items()))
        return (
            f"[AssetDoctor] {report['reason']} scan: {report['tracks_scanned']} tracks, "
            f"{report['shoutouts_scanned']} shoutouts in {report.get('duration_s', 0):.0f}s | "
            f"to fix: {'; '.join(actionable) if actionable else 'nothing'}"
            f"{f' | not auto-fixable: {waiting_text}' if waiting_text else ''} | repairs: {repair_text}"
        )

    @staticmethod
    def _detail_line(report: Dict[str, Any]) -> str:
        parts = []
        for key, bucket in sorted(report.get("counts", {}).items()):
            found = [f"{status} {bucket[status]}" for status in ("missing", "invalid", "blocked", "deferred") if bucket.get(status)]
            uploads = f" ({bucket['uploads']} uploads)" if bucket.get("uploads") else ""
            parts.append(f"{key}: {', '.join(found)}{uploads}")
        return f"[AssetDoctor] {report['reason']} scan detail: {'; '.join(parts) if parts else 'no issues'}"

    async def run_scan(self, reason: str = "manual", track_ids: Optional[Iterable[str]] = None,
                       repair: Optional[bool] = None, include_shoutouts: bool = True,
                       persist: bool = True) -> Dict[str, Any]:
        with usage_tracking.system_scope("asset_doctor"):
            return await self._run_scan(reason, track_ids, repair, include_shoutouts, persist)

    async def _run_scan(self, reason: str, track_ids: Optional[Iterable[str]], repair: Optional[bool],
                        include_shoutouts: bool, persist: bool) -> Dict[str, Any]:
        await asyncio.to_thread(self._load_state)
        if repair is None:
            repair = settings.ASSET_DOCTOR_REPAIR_ENABLED
        async with self._lock():
            started = time.time()
            targeted = track_ids is not None
            wanted = set(track_ids) if targeted else None
            self._current_scan = {"reason": reason, "started_at": _now_iso(), "phase": "loading"}
            try:
                tracks = await asyncio.to_thread(self._load_track_subjects, wanted)
                shoutouts = [] if targeted or not include_shoutouts else await asyncio.to_thread(self._load_shoutout_subjects)
                self._index_items = await asyncio.to_thread(self._current_index_items)

                self._current_scan["phase"] = "detecting"
                pace = max(0.0, settings.ASSET_DOCTOR_SCAN_PACE_MS / 1000.0)
                for subject in tracks + shoutouts:
                    await asyncio.to_thread(self._detect_subject, subject)
                    if pace:
                        await asyncio.sleep(pace)

                report = self._build_report(reason, targeted, tracks, shoutouts, started)
                if repair:
                    self._current_scan["phase"] = "repairing"
                    report["repairs"] = await self._repair_phase(tracks + shoutouts)
                    report["remaining_issues"] = sum(
                        1 for subject in tracks + shoutouts for finding in subject.findings.values()
                        if finding.status in PROBLEM_STATUSES
                    )
                if not targeted:
                    self._prune_cache()
                report["duration_s"] = round(time.time() - started, 1)
                report["finished_at"] = _now_iso()
            finally:
                self._current_scan = None

            if persist:
                self._last_report = report
                await self._save_state(include_cache=True)
            log_service.catalog(self._summary_line(report))
            log_service.detail(self._detail_line(report), "catalog")
            return report

    def notify_tracks_changed(self, track_ids: Iterable[str], reason: str = "event"):
        if not settings.ASSET_DOCTOR_ENABLED:
            return
        ids = {track_id for track_id in track_ids if track_id}
        if not ids:
            return
        self._pending_track_ids.update(ids)
        if self._event_task is not None and not self._event_task.done():
            return
        try:
            self._event_task = spawn(self._run_pending(reason), name="asset_doctor_event_scan")
        except RuntimeError:
            pass

    async def _run_pending(self, reason: str):
        await asyncio.sleep(settings.ASSET_DOCTOR_EVENT_DELAY_S)
        while self._pending_track_ids:
            batch = set(self._pending_track_ids)
            self._pending_track_ids.clear()
            try:
                await self.run_scan(reason=reason, track_ids=batch)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_service.error(f"[AssetDoctor] Event scan failed: {e}")

    async def _scheduler(self):
        await asyncio.sleep(settings.ASSET_DOCTOR_STARTUP_DELAY_S)
        while True:
            try:
                await self.run_scan(reason="scheduled")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_service.error(f"[AssetDoctor] Scheduled scan failed: {e}")
            interval = max(60.0, settings.ASSET_DOCTOR_INTERVAL_HOURS * 3600)
            delay = interval if self._followup_delay is None else min(interval, self._followup_delay)
            self._followup_delay = None
            await asyncio.sleep(delay)

    def start(self):
        if not settings.ASSET_DOCTOR_ENABLED:
            log_service.catalog("[AssetDoctor] Disabled (ASSET_DOCTOR_ENABLED=false)")
            return
        if self._scheduler_task is not None and not self._scheduler_task.done():
            return
        self._scheduler_task = spawn(self._scheduler(), name="asset_doctor")
        log_service.catalog(
            f"[AssetDoctor] Scheduled: first scan in {settings.ASSET_DOCTOR_STARTUP_DELAY_S:.0f}s, "
            f"then every {settings.ASSET_DOCTOR_INTERVAL_HOURS:g}h (repairs "
            f"{'on' if settings.ASSET_DOCTOR_REPAIR_ENABLED else 'off'}, max {settings.ASSET_DOCTOR_MAX_REPAIRS_PER_HOUR}/h)"
        )

    async def stop(self):
        for task in (self._scheduler_task, self._event_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        if self._state_loaded:
            await self._save_state(include_cache=True)

    def trigger_scan(self, repair: Optional[bool] = None, track_ids: Optional[List[str]] = None) -> bool:
        if self._lock().locked():
            return False
        spawn(self.run_scan(reason="admin", track_ids=track_ids, repair=repair), name="asset_doctor_admin_scan")
        return True

    def reset_failures(self, subject_id: Optional[str] = None) -> int:
        keys = [key for key in self._attempts if subject_id is None or key.split(":", 2)[1] == subject_id]
        for key in keys:
            self._attempts.pop(key, None)
        return len(keys)

    async def status(self) -> Dict[str, Any]:
        await asyncio.to_thread(self._load_state)
        self._prune_repair_window()
        failed = [dict(record, key=key) for key, record in self._attempts.items() if record.get("failed")]
        backoff = [dict(record, key=key) for key, record in self._attempts.items() if not record.get("failed")]
        return {
            "enabled": settings.ASSET_DOCTOR_ENABLED,
            "repair_enabled": settings.ASSET_DOCTOR_REPAIR_ENABLED,
            "running": self._current_scan,
            "pending_event_tracks": len(self._pending_track_ids),
            "repairs_last_hour": len(self._repair_times),
            "max_repairs_per_hour": settings.ASSET_DOCTOR_MAX_REPAIRS_PER_HOUR,
            "failed": failed,
            "retrying": backoff,
            "last_report": self._last_report,
        }


asset_integrity_service = AssetIntegrityService()
