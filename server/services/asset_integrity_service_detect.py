"""The asset doctor finding problems: probing each track's and shoutout's files, metadata, DB row and vector store
entry."""
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import soundfile as sf
from PIL import Image
from services import track_asset_stages as stages
from services.catalog_credit import credit_settled, is_ai_track
from services.catalog_vocals import VOCALS
from database.pg_pool import get_pooled_connection
from config import settings
from services.asset_integrity_service_checks import (
    AssetCheck, BACKFILL_METADATA_FIELDS, BLOCKED, DEFERRED, EBML_MAGIC, FEATURE_KEYS, Finding,
    INSPIRED_ARTIST_FIELD, INVALID, MIN_AUDIO_BYTES, MIN_IMAGE_BYTES, MIN_IMAGE_EDGE, MIN_SHOUTOUT_AUDIO_BYTES,
    MIN_SHOUTOUT_DURATION_S, MIN_WEBM_BYTES, MISSING, OK, REPAIRABLE_METADATA_FIELDS, REQUIRED_DERIVED_LISTS,
    SHOUTOUT_DURATION_TOLERANCE_S, SHOUTOUT_STEM, Subject, VOCALS_FIELD, _duration_mismatch, _ffprobe_duration,
    _file_stat, _looks_like_mp3, _ogg_opus_duration, _parse_timestamp,
)


class AssetDetection:
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
        if is_ai_track(metadata) and not credit_settled(metadata):
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
        if subject.db is None or self._indexed_ids is None:
            return Finding(OK, "index state unknown")
        if subject.id not in self._indexed_ids:
            return Finding(MISSING, "not in the catalog vector store (search and stations can't find it)")
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

    def _current_indexed_ids(self) -> Optional[set]:
        vector_db = self._svc("vector_db")
        keys = vector_db.keys() if vector_db is not None else set()
        return keys or None

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
