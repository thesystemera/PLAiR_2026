import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pytz
from sqlalchemy import delete, func, select

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
CANDIDATES = 100
TRACK_LEAD_MIN = 20
AROUND_MARGIN_MIN_MIN = 10
AROUND_MARGIN_MAX_MIN = 60
POST_TEXT_CHARS = 120
TALK_TEXT_CHARS = 160
DETAIL_MAX_CHARS = 3000
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


def local_clock(at: datetime, tz_name: Optional[str]) -> Optional[str]:
    if not tz_name:
        return None
    try:
        return at.astimezone(pytz.timezone(tz_name)).strftime("%I:%M %p").lstrip("0").lower()
    except pytz.UnknownTimeZoneError:
        return None


@dataclass
class TimelineEntry:
    at: datetime
    kind: str
    id: str
    label: str
    ended: datetime
    outcome: Optional[str] = None

    def brief(self, now: datetime, tz_name: Optional[str] = None) -> Dict[str, Any]:
        entry = {"id": self.id, "kind": self.kind, "what": self.label,
                 "min_ago": max(0, round((now - self.at).total_seconds() / 60))}
        clock = local_clock(self.at, tz_name)
        if clock:
            entry["at"] = clock
        if self.outcome:
            entry["outcome"] = self.outcome
        return entry


@dataclass
class Window:
    since: datetime
    until: datetime
    around: Optional[datetime] = None

    def describe(self, now: datetime) -> str:
        older, newer = (round((now - edge).total_seconds() / 60) for edge in (self.since, self.until))
        return f"the last {older} min" if newer <= 0 else f"{older} to {newer} min ago"


def window(now: datetime, from_minutes: float = 120, to_minutes: float = 0,
           around_minutes: Optional[float] = None) -> Window:
    if around_minutes is not None:
        margin = min(AROUND_MARGIN_MAX_MIN, max(AROUND_MARGIN_MIN_MIN, around_minutes / 2))
        return Window(now - timedelta(minutes=around_minutes + margin),
                      now - timedelta(minutes=max(0.0, around_minutes - margin)),
                      now - timedelta(minutes=around_minutes))
    older, newer = max(from_minutes, to_minutes), min(from_minutes, to_minutes)
    return Window(now - timedelta(minutes=older), now - timedelta(minutes=newer))


def _mine(model, user_id: Optional[int], session_id: Optional[str]):
    return model.user_id == int(user_id) if user_id else model.session_id == session_id


def _buffered(user_id: Optional[int], session_id: Optional[str], since: datetime,
              until: datetime) -> List[Dict[str, Any]]:
    events = []
    for event in list(analytics_service.event_buffer):
        if (event.get("user_id") != int(user_id)) if user_id else (event.get("session_id") != session_id):
            continue
        at = datetime.fromisoformat(event["started_at"])
        if since <= at <= until:
            events.append({"at": at, "id": event["track_id"], "event": event["event_type"]})
    return events


async def _stored(user_id: Optional[int], session_id: Optional[str], since: datetime, until: datetime,
                  rows: int) -> List[Dict[str, Any]]:
    async with AsyncSessionLocal() as db:
        found = (await db.execute(
            select(PlayEvent.started_at, PlayEvent.track_id, PlayEvent.event_type)
            .where(_mine(PlayEvent, user_id, session_id), PlayEvent.started_at >= since,
                   PlayEvent.started_at <= until)
            .order_by(PlayEvent.started_at.desc()).limit(rows))).all()
    return [{"at": at, "id": item_id, "event": event} for at, item_id, event in found]


def _post_label(post: Dict[str, Any]) -> str:
    from services_radio import community_on_air
    song = (post.get("track") or {}).get("title")
    text = community_on_air.text_of(post)
    return (f"{kind_of(post)} from {community_on_air.speaker(post)}" + (f" on '{song}'" if song else "")
            + f": \"{text[:POST_TEXT_CHARS]}{'...' if len(text) > POST_TEXT_CHARS else ''}\"")


def _entries(events: Iterable[Dict[str, Any]], current: Optional[str], now: datetime) -> List[TimelineEntry]:
    from service_registry import services
    catalog, posts = services.catalog_service, services.user_content_service
    entries: List[TimelineEntry] = []
    open_tracks: Dict[str, TimelineEntry] = {}
    for event in sorted(events, key=lambda e: e["at"]):
        item_id = str(event["id"])
        post = posts.get_shoutout(item_id) if posts is not None else None
        if post is not None:
            if event["event"] == START_EVENT:
                entries.append(TimelineEntry(event["at"], kind_of(post), item_id, _post_label(post), event["at"]))
            continue
        if event["event"] != START_EVENT:
            entry = open_tracks.pop(item_id, None)
            if entry is not None:
                entry.outcome, entry.ended = OUTCOMES.get(event["event"], event["event"]), event["at"]
            continue
        track = catalog.get_track(item_id) if catalog is not None else None
        if track is None:
            continue
        length_ms = (track.get("track_info") or {}).get("duration") or 0
        entry = TimelineEntry(event["at"], KIND_TRACK, item_id, log_service.track_label(track),
                              event["at"] + timedelta(milliseconds=length_ms))
        open_tracks[item_id] = entry
        entries.append(entry)
    on_air = open_tracks.get(current or "")
    if on_air is not None:
        on_air.outcome, on_air.ended = "on air now", now
    return entries


async def _played(user_id: Optional[int], session_id: Optional[str], span: Window, wanted: set, now: datetime,
                  current: Optional[str]) -> List[TimelineEntry]:
    since, until = span.since - timedelta(minutes=TRACK_LEAD_MIN), span.until + timedelta(minutes=TRACK_LEAD_MIN)
    recent = _buffered(user_id, session_id, since, until)
    rows = CANDIDATES * ROWS_PER_ENTRY
    while True:
        stored = await _stored(user_id, session_id, since, until, rows)
        entries = [entry for entry in _entries(stored + recent, current, now)
                   if entry.kind in wanted and entry.at <= span.until and entry.ended >= span.since]
        if len(entries) >= CANDIDATES or len(stored) < rows or rows >= MAX_ROWS:
            return entries
        rows *= 4


def _talk_kind(stream: str) -> str:
    return KIND_TALK if stream in TALK_STREAMS else KIND_SEGMENT


def _talk_name(kind: str, label: str) -> str:
    return label or SEGMENT_STREAMS.get(kind) or TALK_STREAMS.get(kind) or kind


async def _talked(user_id: Optional[int], session_id: Optional[str], span: Window,
                  wanted: set) -> List[TimelineEntry]:
    streams = [stream for stream in list(SEGMENT_STREAMS) + list(TALK_STREAMS) if _talk_kind(stream) in wanted]
    if not streams:
        return []
    opening = func.substr(AiredTalk.text, 1, TALK_TEXT_CHARS + 1)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(AiredTalk.id, AiredTalk.aired_at, AiredTalk.kind, AiredTalk.label, opening)
            .where(_mine(AiredTalk, user_id, session_id), AiredTalk.aired_at >= span.since,
                   AiredTalk.aired_at <= span.until, AiredTalk.kind.in_(streams))
            .order_by(AiredTalk.aired_at.desc()).limit(CANDIDATES))).all()
    return [TimelineEntry(
        at, _talk_kind(kind), f"{TALK_ID_PREFIX}{row_id}",
        f"{_talk_name(kind, label)}: \"{text[:TALK_TEXT_CHARS]}{'...' if len(text) > TALK_TEXT_CHARS else ''}\"", at)
        for row_id, at, kind, label, text in rows]


async def timeline(user_id: Optional[int], session_id: Optional[str], kinds: Optional[Iterable[str]] = None,
                   span: Optional[Window] = None, limit: int = 10) -> Tuple[List[TimelineEntry], int]:
    if not user_id and not session_id:
        return [], 0
    now = datetime.now(timezone.utc)
    span = span or window(now)
    wanted = set(kinds or DEFAULT_KINDS)
    entries = await _talked(user_id, session_id, span, wanted)
    if wanted & set(PLAYED_KINDS):
        from service_registry import services
        playback = services.playback_service
        state = playback.get_state(session_id) if playback is not None and session_id else None
        current = ((state or {}).get("current_track") or {}).get("id")
        entries += await _played(user_id, session_id, span, wanted, now, current)
    moment = span.around
    if moment is not None:
        def distance(entry: TimelineEntry) -> float:
            if entry.at <= moment <= entry.ended:
                return 0.0
            return min(abs((entry.at - moment).total_seconds()), abs((entry.ended - moment).total_seconds()))
        entries = sorted(entries, key=distance)
    else:
        entries = sorted(entries, key=lambda entry: entry.at, reverse=True)
    picked = sorted(entries[:limit], key=lambda entry: entry.at, reverse=True)
    return picked, max(0, len(entries) - limit)


async def talk_detail(user_id: Optional[int], session_id: Optional[str], entry_id: str) -> Optional[Dict[str, Any]]:
    key = entry_id.removeprefix(TALK_ID_PREFIX)
    if not key.isdigit() or (not user_id and not session_id):
        return None
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(AiredTalk).where(AiredTalk.id == int(key),
                                                        _mine(AiredTalk, user_id, session_id)))).scalar_one_or_none()
    if row is None:
        return None
    return {"id": entry_id, "kind": _talk_kind(row.kind), "what": _talk_name(row.kind, row.label),
            "min_ago": max(0, round((datetime.now(timezone.utc) - row.aired_at).total_seconds() / 60)),
            "said": row.text[:DETAIL_MAX_CHARS] + ("..." if len(row.text) > DETAIL_MAX_CHARS else "")}


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
