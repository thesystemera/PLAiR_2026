from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select

from database import AsyncSessionLocal, PlayEvent
from services import log_service
from services.analytics_service import analytics_service
from services.user_content_database_service import kind_of, KINDS

KIND_TRACK = "track"
TIMELINE_KINDS = (KIND_TRACK,) + tuple(KINDS)
START_EVENT = "play"
OUTCOMES = {"complete": "played through", "skip": "skipped"}
ROWS_PER_ENTRY = 3
MAX_ROWS = 2000
POST_TEXT_CHARS = 120


@dataclass
class TimelineEntry:
    at: datetime
    kind: str
    id: str
    label: str
    outcome: Optional[str] = None

    def brief(self, now: datetime) -> Dict[str, Any]:
        entry = {"id": self.id, "kind": self.kind, "what": self.label,
                 "min_ago": max(0, round((now - self.at).total_seconds() / 60))}
        if self.outcome:
            entry["outcome"] = self.outcome
        return entry


def _mine(user_id: Optional[int], session_id: Optional[str]):
    return PlayEvent.user_id == int(user_id) if user_id else PlayEvent.session_id == session_id


def _buffered(user_id: Optional[int], session_id: Optional[str], since: datetime) -> List[Dict[str, Any]]:
    events = []
    for event in list(analytics_service.event_buffer):
        if (event.get("user_id") != int(user_id)) if user_id else (event.get("session_id") != session_id):
            continue
        at = datetime.fromisoformat(event["started_at"])
        if at >= since:
            events.append({"at": at, "id": event["track_id"], "event": event["event_type"]})
    return events


async def _stored(user_id: Optional[int], session_id: Optional[str], since: datetime, rows: int) -> List[Dict[str, Any]]:
    async with AsyncSessionLocal() as db:
        found = (await db.execute(
            select(PlayEvent.started_at, PlayEvent.track_id, PlayEvent.event_type)
            .where(_mine(user_id, session_id), PlayEvent.started_at >= since)
            .order_by(PlayEvent.started_at.desc()).limit(rows))).all()
    return [{"at": at, "id": item_id, "event": event} for at, item_id, event in found]


def _post_label(post: Dict[str, Any]) -> str:
    from services_radio import community_on_air
    song = (post.get("track") or {}).get("title")
    text = community_on_air.text_of(post)
    return (f"{kind_of(post)} from {community_on_air.speaker(post)}" + (f" on '{song}'" if song else "")
            + f": \"{text[:POST_TEXT_CHARS]}{'...' if len(text) > POST_TEXT_CHARS else ''}\"")


def _entries(events: Iterable[Dict[str, Any]]) -> List[TimelineEntry]:
    from service_registry import services
    catalog, posts = services.catalog_service, services.user_content_service
    entries: List[TimelineEntry] = []
    open_tracks: Dict[str, TimelineEntry] = {}
    for event in sorted(events, key=lambda e: e["at"]):
        item_id = str(event["id"])
        post = posts.get_shoutout(item_id) if posts is not None else None
        if post is not None:
            entries.append(TimelineEntry(event["at"], kind_of(post), item_id, _post_label(post)))
            continue
        if event["event"] != START_EVENT:
            entry = open_tracks.pop(item_id, None)
            if entry is not None:
                entry.outcome = OUTCOMES.get(event["event"], event["event"])
            continue
        track = catalog.get_track(item_id) if catalog is not None else None
        if track is None:
            continue
        entry = TimelineEntry(event["at"], KIND_TRACK, item_id, log_service.track_label(track))
        open_tracks[item_id] = entry
        entries.append(entry)
    return entries


async def timeline(user_id: Optional[int], session_id: Optional[str], kinds: Optional[Iterable[str]] = None,
                   minutes: float = 120, limit: int = 10) -> List[TimelineEntry]:
    if not user_id and not session_id:
        return []
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    wanted = set(kinds or ())
    recent = _buffered(user_id, session_id, since)
    rows = limit * ROWS_PER_ENTRY
    while True:
        stored = await _stored(user_id, session_id, since, rows)
        entries = [entry for entry in _entries(stored + recent) if not wanted or entry.kind in wanted]
        if len(entries) >= limit or len(stored) < rows or rows >= MAX_ROWS:
            break
        rows *= 4
    from service_registry import services
    playback = services.playback_service
    state = playback.get_state(session_id) if playback is not None and session_id else None
    current = ((state or {}).get("current_track") or {}).get("id")
    newest_first = sorted(entries, key=lambda entry: entry.at, reverse=True)[:limit]
    for entry in newest_first:
        if entry.kind == KIND_TRACK and entry.outcome is None and entry.id == current:
            entry.outcome = "on air now"
            break
    return newest_first
