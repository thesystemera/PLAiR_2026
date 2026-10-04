"""The asset doctor fixing problems: per-check repairs, when a repair may run (GPU, busy, back-off) and the repair phase
of a scan."""
import asyncio
import copy
import os
import shutil
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional
import models_global
from services import log_service
from services.user_content_database_service import kind_of, sting_file
from services import track_asset_stages as stages
from services.catalog_credit import ai_artist, known_artists, settle_ai_credit
from services.catalog_vocals import VOCALS, settled_vocals
from config import settings
from services.asset_integrity_service_checks import (
    AssetCheck, Finding, INSPIRED_ARTIST_FIELD, INVALID, MIN_SHOUTOUT_AUDIO_BYTES, MISSING, OK, PROBLEM_STATUSES,
    SHOUTOUT_CHECKS, Subject, TRACK_CHECKS, VOCALS_FIELD, _ffprobe_duration, _file_stat, _now_iso,
)


class AssetRepairs:
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
            artist, source, settled = settle_ai_credit(metadata, tracks, known_artists(tracks))
            if settled:
                missing.remove(INSPIRED_ARTIST_FIELD)
                changed = True
                log_service.info(f"[Asset doctor] {track_id}: artist credit set to {artist!r} ({source})")

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

        if INSPIRED_ARTIST_FIELD in missing and settle_ai_credit(metadata)[2]:
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
        vector_db = self._svc("vector_db")
        async with models_global.gpu_lease("Asset doctor vector index"):
            await asyncio.to_thread(vector_db.add_rows, [subject.id])
        self._indexed_ids = await asyncio.to_thread(self._current_indexed_ids)

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
