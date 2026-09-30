import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import delete, select

from config.settings import settings
from database import AsyncSessionLocal, PlayEvent
from database.models import AiredTalk
from services import log_service
from services.analytics_service import analytics_service
from services.user_content_database_service import kind_of, KINDS

KIND_TRACK = "track"
KIND_SEGMENT = "segment"
KIND_TALK = "talk"
PLAYED_KINDS = (KIND_TRACK,) + tuple(KINDS)
TIMELINE_KINDS = PLAYED_KINDS + (KIND_SEGMENT, KIND_TALK)
DEFAULT_KINDS = PLAYED_KINDS + (KIND_SEGMENT,)
START_EVENT = "play"
OUTCOMES = {"complete": "played through", "skip": "skipped"}
ROWS_PER_ENTRY = 3
MAX_ROWS = 2000
POST_TEXT_CHARS = 120
TALK_TEXT_CHARS = 160
TALK_ID_PREFIX = "aired:"
PRUNE_EVERY_S = 3600.0
TALK_STREAMS = {"interactive": "chat reply", "announcer": "between-track talk"}
SEGMENT_STREAMS = {
    "news": "news bulletin",
    "weather": "weather forecast",
    "events": "gig guide",
    "location_search": "places rundown",
    "biography": "artist story",
    "lyrics": "lyrics breakdown",
    "shoutouts": "listener shoutouts segment",
    "radio_segment": "talk break",
}

_last_prune = 0.0


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


def _mine(model, user_id: Optional[int], session_id: Optional[str]):
    return model.user_id == int(user_id) if user_id else model.session_id == session_id


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
            .where(_mine(PlayEvent, user_id, session_id), PlayEvent.started_at >= since)
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
            if event["event"] == START_EVENT:
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


async def _played(user_id: Optional[int], session_id: Optional[str], since: datetime, wanted: set,
                  limit: int) -> List[TimelineEntry]:
    recent = _buffered(user_id, session_id, since)
    rows = limit * ROWS_PER_ENTRY
    while True:
        stored = await _stored(user_id, session_id, since, rows)
        entries = [entry for entry in _entries(stored + recent) if entry.kind in wanted]
        if len(entries) >= limit or len(stored) < rows or rows >= MAX_ROWS:
            return entries
        rows *= 4


def _talk_kind(stream: str) -> str:
    return KIND_TALK if stream in TALK_STREAMS else KIND_SEGMENT


def _talk_label(row) -> str:
    name = row.label or SEGMENT_STREAMS.get(row.kind) or TALK_STREAMS.get(row.kind) or row.kind
    return f"{name}: \"{row.text[:TALK_TEXT_CHARS]}{'...' if len(row.text) > TALK_TEXT_CHARS else ''}\""


async def _talked(user_id: Optional[int], session_id: Optional[str], since: datetime, wanted: set,
                  limit: int) -> List[TimelineEntry]:
    streams = [stream for stream in list(SEGMENT_STREAMS) + list(TALK_STREAMS) if _talk_kind(stream) in wanted]
    if not streams:
        return []
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(AiredTalk).where(_mine(AiredTalk, user_id, session_id), AiredTalk.aired_at >= since,
                                    AiredTalk.kind.in_(streams))
            .order_by(AiredTalk.aired_at.desc()).limit(limit))).scalars().all()
    return [TimelineEntry(row.aired_at, _talk_kind(row.kind), f"{TALK_ID_PREFIX}{row.id}", _talk_label(row))
            for row in rows]


async def timeline(user_id: Optional[int], session_id: Optional[str], kinds: Optional[Iterable[str]] = None,
                   minutes: float = 120, limit: int = 10) -> List[TimelineEntry]:
    if not user_id and not session_id:
        return []
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    wanted = set(kinds or DEFAULT_KINDS)
    entries = await _talked(user_id, session_id, since, wanted, limit)
    if wanted & set(PLAYED_KINDS):
        entries += await _played(user_id, session_id, since, wanted, limit)
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


async def talk_detail(user_id: Optional[int], session_id: Optional[str], entry_id: str) -> Optional[Dict[str, Any]]:
    key = entry_id.removeprefix(TALK_ID_PREFIX)
    if not key.isdigit() or (not user_id and not session_id):
        return None
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(AiredTalk).where(AiredTalk.id == int(key),
                                                        _mine(AiredTalk, user_id, session_id)))).scalar_one_or_none()
    if row is None:
        return None
    return {"id": entry_id, "kind": _talk_kind(row.kind),
            "what": row.label or SEGMENT_STREAMS.get(row.kind) or TALK_STREAMS.get(row.kind) or row.kind,
            "min_ago": max(0, round((datetime.now(timezone.utc) - row.aired_at).total_seconds() / 60)),
            "said": row.text}


async def record_talk(user_id: Optional[int], session_id: Optional[str], stream: str, text: str,
                      label: str = "", seconds: float = 0.0) -> None:
    global _last_prune
    text = (text or "").strip()
    if not text or not (user_id or session_id) or (stream not in SEGMENT_STREAMS and stream not in TALK_STREAMS):
        return
    try:
        async with AsyncSessionLocal() as db:
            db.add(AiredTalk(user_id=int(user_id) if user_id else None, session_id=session_id, kind=stream,
                             label=label or "", text=text, seconds=float(seconds or 0.0)))
            if time.monotonic() - _last_prune > PRUNE_EVERY_S:
                _last_prune = time.monotonic()
                await db.execute(delete(AiredTalk).where(
                    AiredTalk.aired_at < datetime.now(timezone.utc) - timedelta(days=settings.DJ_TIMELINE_KEEP_DAYS)))
            await db.commit()
    except Exception as e:
        log_service.warning(f"[Timeline] couldn't record {stream} for {log_service.who(session_id, user_id=user_id)}: "
                            f"{type(e).__name__}: {e}")
