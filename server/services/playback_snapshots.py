import json
import time
from datetime import timedelta
from typing import Any, Dict, Optional

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert

from config import settings
from database import AsyncSessionLocal
from database.models import PlaybackSnapshot, utc_now


def snapshot(state) -> Optional[Dict[str, Any]]:
    if not state.current_track_id or not state.queue:
        return None
    queue_ids = [t["id"] for t in state.queue if t.get("id")]
    return {
        "radio_mode": state.radio_mode,
        "seed_track_id": state.seed_track_id,
        "seed_blend": state.seed_blend,
        "queue": queue_ids,
        "current_track_id": state.current_track_id,
        "progress_ms": int(state.get_simulated_progress()),
        "is_playing": bool(state.is_playing),
        "history": [t["id"] for t in state.history[-settings.QUEUE_HISTORY_SONGS:] if t.get("id")],
        "auto_filled": [tid for tid in queue_ids if tid in state._auto_filled_track_ids],
    }


def restore(state, saved: Dict[str, Any], catalog) -> bool:
    def tracks(ids):
        return [t for t in (catalog.get_track(tid) for tid in ids or []) if t]

    queue = tracks(saved.get("queue"))
    current = saved.get("current_track_id")
    current_track = next((t for t in queue if t["id"] == current), None)
    if current_track is None:
        return False
    duration = (current_track.get("track_info") or {}).get("duration") or 0
    progress = max(0, int(saved.get("progress_ms") or 0))
    state.queue = queue
    state.history = tracks(saved.get("history"))
    state.current_track_id = current
    state.radio_mode = saved.get("radio_mode") or state.radio_mode
    state.seed_track_id = saved.get("seed_track_id") if catalog.get_track(saved.get("seed_track_id") or "") else None
    state.seed_blend = saved.get("seed_blend") or None
    state.progress_ms = min(progress, duration) if duration else progress
    state.is_playing = False
    state.last_update_time = time.time()
    state._auto_filled_track_ids = set(saved.get("auto_filled") or []) & {t["id"] for t in queue}
    return True


async def load(session_id: str) -> Optional[Dict[str, Any]]:
    async with AsyncSessionLocal() as db:
        row = await db.get(PlaybackSnapshot, session_id)
        return json.loads(row.state) if row else None


async def save(snapshots: Dict[str, Dict[str, Any]]) -> None:
    if not snapshots:
        return
    now = utc_now()
    rows = [{"session_id": sid, "state": json.dumps(snap), "updated_at": now} for sid, snap in snapshots.items()]
    stmt = insert(PlaybackSnapshot).values(rows)
    stmt = stmt.on_conflict_do_update(index_elements=[PlaybackSnapshot.session_id],
                                      set_={"state": stmt.excluded.state, "updated_at": stmt.excluded.updated_at})
    async with AsyncSessionLocal() as db:
        await db.execute(stmt)
        await db.commit()


async def prune(keep_days: int) -> int:
    async with AsyncSessionLocal() as db:
        result = await db.execute(delete(PlaybackSnapshot)
                                  .where(PlaybackSnapshot.updated_at < utc_now() - timedelta(days=keep_days)))
        await db.commit()
        return result.rowcount or 0

