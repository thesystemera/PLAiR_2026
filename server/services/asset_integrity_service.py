import asyncio
import copy
import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Set, Tuple


from services import log_service
from services import usage_tracking
from services.task_utils import spawn
from config import settings
from services.asset_integrity_service_detect import AssetDetection
from services.asset_integrity_service_repair import AssetRepairs
from services.asset_integrity_service_checks import (
    Finding, MAX_REPORT_ISSUES, OK, PROBLEM_STATUSES, Subject, _file_stat, _now_iso,
)

class AssetIntegrityService(AssetDetection, AssetRepairs):
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
        self._indexed_ids: Optional[set] = None
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
            "index_items": len(self._indexed_ids) if self._indexed_ids is not None else None,
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
                self._indexed_ids = await asyncio.to_thread(self._current_indexed_ids)

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
