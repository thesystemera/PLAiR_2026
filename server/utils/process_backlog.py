import argparse
import os
import shutil
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render Suno tracks that have no master yet through the full chain, "
                                                 "on a chosen GPU, in a process of its own")
    parser.add_argument("--gpu", type=int, default=0, help="PCI-ordered GPU index (0 = Quadro P6000, PLAiR's card; 1 = the RTX 6000, shared with the owner's other projects)")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N tracks")
    parser.add_argument("--in-flight", type=int, default=4, help="Tracks inside the lanes at once")
    parser.add_argument("--dry-run", action="store_true", help="List what would run")
    parser.add_argument("--keep-intermediates", action="store_true", help="Keep the decoded, Apollo, premaster and SonicMaster WAVs")
    parser.add_argument("--track", action="append", default=[], help="Render only these track ids (repeatable)")
    parser.add_argument("--rerender", action="store_true",
                        help="After the missing tracks, re-render masters made by an older chain version")
    return parser.parse_args()


ARGS = parse_args()
os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"] = str(ARGS.gpu)
os.environ["LOG_FILE_NAME"] = "backlog.log"
os.environ["ASSET_DOCTOR_ENABLED"] = "false"
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio  # noqa: E402
import faulthandler  # noqa: E402
import json  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import psycopg2  # noqa: E402

from config import settings  # noqa: E402
from services import log_service  # noqa: E402
from services.log_service import start_log_worker, stop_worker  # noqa: E402
from services import track_asset_stages as stages  # noqa: E402


def backlog():
    mp3_dir = settings.CATALOG_DIR / "mp3"
    masters = {p.stem for p in settings.ENHANCED_WAV_DIR.glob("*.wav")}
    ids, stale = [], []
    for p in mp3_dir.glob("*.mp3"):
        meta_path = settings.CATALOG_DIR / "metadata" / f"{p.stem}.json"
        if not meta_path.exists():
            continue
        if p.stem not in masters:
            ids.append(p.stem)
        elif ARGS.rerender:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("uploaded_by_user_id") is None and (meta.get("master_chain_version") or 0) < settings.MASTER_CHAIN_VERSION:
                stale.append(p.stem)
    with psycopg2.connect(settings.DATABASE_URL.replace("+asyncpg", "")) as conn, conn.cursor() as cur:
        cur.execute("select track_id, preference_type::text from track_preferences "
                    "where preference_type::text in ('SUPER_LIKE', 'LIKE')")
        rank = {}
        for track_id, kind in cur.fetchall():
            rank[track_id] = min(rank.get(track_id, 2), 0 if kind == "SUPER_LIKE" else 1)
    ids.sort(key=lambda t: (rank.get(t, 2), t))
    stale.sort(key=lambda t: (rank.get(t, 2), t))
    return ids + stale, rank, set(ids)


def clear_previous_render(track_id: str):
    for folder in (settings.DECODED_WAV_DIR, settings.WAV_DIR, settings.PREMASTER_WAV_DIR, settings.SONIC_WAV_DIR):
        (folder / f"{track_id}.wav").unlink(missing_ok=True)
    shutil.rmtree(settings.DEMUCS_STEMS_DIR / track_id, ignore_errors=True)
    for bitrate in stages.OPUS_BITRATES:
        stages.opus_path(track_id, bitrate).unlink(missing_ok=True)
        stages.webm_path(track_id, bitrate).unlink(missing_ok=True)


def remove_intermediates(track_id: str):
    for folder in (settings.DECODED_WAV_DIR, settings.WAV_DIR, settings.PREMASTER_WAV_DIR, settings.SONIC_WAV_DIR):
        (folder / f"{track_id}.wav").unlink(missing_ok=True)
    shutil.rmtree(settings.DEMUCS_STEMS_DIR / track_id, ignore_errors=True)


def mark_added(track_id: str):
    path = settings.CATALOG_DIR / "metadata" / f"{track_id}.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["catalog_added_at"] = datetime.now(timezone.utc).isoformat()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


CRASH_LOG = open(Path(__file__).parent.parent.parent / "data" / "logs" / "backlog_crash.log", "a", encoding="utf-8")
faulthandler.enable(file=CRASH_LOG, all_threads=True)


async def main():
    ids, rank, missing = backlog()
    if ARGS.track:
        ids = list(ARGS.track)
    if ARGS.limit:
        ids = ids[:ARGS.limit]
    print(f"Backlog: {len(ids)} tracks (super-liked {sum(rank.get(t) == 0 for t in ids)}, "
          f"liked {sum(rank.get(t) == 1 for t in ids)}) on GPU {ARGS.gpu}", flush=True)
    if ARGS.dry_run or not ids:
        return

    await start_log_worker()
    from services.suno_service_orchestrator import SunoServiceOrchestrator, TrackJob
    orchestrator = SunoServiceOrchestrator()
    await orchestrator.initialize()

    pending, done, failed = list(ids), 0, 0
    running = []
    started = time.time()
    while pending or running:
        while pending and len(running) < ARGS.in_flight:
            track_id = pending.pop(0)
            clear_previous_render(track_id)
            if track_id in missing:
                mark_added(track_id)
            metadata = json.loads((settings.CATALOG_DIR / "metadata" / f"{track_id}.json").read_text(encoding="utf-8"))
            job = TrackJob(track_id=track_id, mp3_path=settings.CATALOG_DIR / "mp3" / f"{track_id}.mp3",
                           metadata=metadata, defer_catalog_reload=True)
            await orchestrator.lane1_queue.put(job)
            running.append((job, time.time()))
        await asyncio.sleep(2)
        for job, t0 in list(running):
            if job.is_settled():
                running.remove((job, t0))
                ok = job.catalog_ready
                if ok and not ARGS.keep_intermediates:
                    remove_intermediates(job.track_id)
                done += ok
                failed += not ok
                elapsed = time.time() - started
                left = len(pending) + len(running)
                rate = elapsed / max(1, done + failed)
                log_service.suno(f"[Backlog] {job.track_id[:8]} {'ready' if ok else 'FAILED'} in {time.time() - t0:.0f} s | "
                                 f"{done} ready, {failed} failed, {left} left, ~{rate * left / 3600:.1f} h to go")

    await orchestrator.catalog.reload_catalog()
    log_service.suno(f"[Backlog] Finished: {done} ready, {failed} failed")
    await stop_worker()


if __name__ == "__main__":
    asyncio.run(main())
