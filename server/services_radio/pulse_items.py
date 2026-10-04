"""City Pulse building blocks: item kinds, PulseItem, the listener, the query and the KnowledgeNode base every source
implements."""
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
import pytz
from config import settings
from database.models import User
from services_radio import geo
from services_radio import regional_knowledge as regional_kb
from services_radio.listener_location import ListenerLocation
from services_radio.talking_clock import clock_time


KIND_EVENT = "event"
KIND_PLACE = "place"
KIND_NEWS = "news"
KIND_WEATHER = "weather"
KIND_AREA = "area"
KIND_ARTIST = "artist"
KIND_COMMUNITY = "community"
KIND_CHART = "chart"
KIND_TREND = "trend"
KIND_TRACK = "track"
KIND_REVIEW = "review"
PLACED_KINDS = frozenset(("event", "place", "news", "community"))
LISTENING_TOP = 8
LISTENING_NOTE = ("most_loved ranks their liked and super-liked tracks by rating plus how often they listen through; "
                  "most_played is what they have listened to most, rated or not. listens = played at least halfway, "
                  "skips = cut short.")
ALL_KINDS = (KIND_EVENT, KIND_PLACE, KIND_NEWS, KIND_WEATHER, KIND_AREA, KIND_ARTIST, KIND_COMMUNITY, KIND_CHART,
             KIND_TREND, KIND_TRACK, KIND_REVIEW)
WHEN_VALUES = ("now", "today", "tonight", "tomorrow", "weekend", "week", "month")
INTENTS = {KIND_EVENT: "events", KIND_PLACE: "places", KIND_NEWS: "news", KIND_COMMUNITY: "community",
           KIND_WEATHER: "weather", KIND_AREA: "area", KIND_ARTIST: "artists", KIND_TRACK: "music",
           KIND_REVIEW: "reviews"}
TEXT_MAX = 180
SHOUTOUT_BROWSE = "recent community messages and shoutouts"
LISTENER_CACHE_S = 60
LISTENER_CACHE_MAX = 2000
OFFERED_MAX = 4000
CONTEXT_MEMO_S = 15
LINK_NAME_MIN_CHARS = 4
LINK_STOP_NAMES = {"the", "live", "music", "tour", "night", "show", "festival", "auckland", "wellington", "sydney",
                   "melbourne", "london", "new york", "los angeles", "tba", "n/a", "various artists", "concert"}


def _recency(moment: Optional[datetime]) -> float:
    if moment is None:
        return 0.5
    days = max(0.0, (datetime.now(timezone.utc) - moment).total_seconds() / 86400.0)
    return 0.5 ** (days / max(settings.PULSE_RECENCY_HALF_LIFE_DAYS, 0.1))


def _clip(text: str, limit: int = TEXT_MAX) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "..."


def _base_title(title: str) -> str:
    return " ".join(re.split(r"\s+[-|:–]\s+", title or "", maxsplit=1)[0].lower().split())


def _parse_time(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class PulseItem:
    id: str
    kind: str
    title: str
    text: str = ""
    when: Optional[datetime] = None
    source: str = ""
    url: str = ""
    score: float = 0.0
    aired: bool = False
    live: bool = False
    payload: dict = field(default_factory=dict)
    published: Optional[datetime] = None
    where: Optional[geo.Where] = None
    area: str = ""
    near: str = ""
    gap_m: Optional[float] = None
    entities: list = field(default_factory=list)
    links: list = field(default_factory=list)

    def brief(self, tz_name: Optional[str] = None) -> dict:
        entry = {"id": self.id, "kind": self.kind, "title": _clip(self.title, 120)}
        if self.text:
            entry["text"] = _clip(self.text)
        if self.when:
            entry["when"] = _local_when(self.when, tz_name)
        if self.published and self.kind in (KIND_COMMUNITY, KIND_NEWS):
            entry["age"] = _age(self.published)
        where = self.where.label if self.where and self.where.label else self.area
        if where:
            entry["where"] = _clip(where, 70)
        if self.near:
            entry["near"] = self.near
        if self.source:
            entry["source"] = self.source
        if self.links:
            entry["linked"] = [f"{link['reason']}: {_clip(link['title'], 70)}" for link in self.links[:3]]
        if self.aired:
            entry["aired_recently"] = True
        return entry

    def line(self, tz_name: Optional[str] = None, with_id: bool = False) -> str:
        brief = self.brief(tz_name)
        extras = " | ".join(str(brief[k]) for k in ("text", "when", "age", "where", "near") if k in brief)
        if brief.get("linked"):
            extras += " | linked: " + "; ".join(brief["linked"])
        return f"- [{self.kind}] {brief['title']}" + (f" ({extras})" if extras else "") + (
            " [already mentioned to this listener]" if self.aired else "") + (f" {{id: {self.id}}}" if with_id else "")


def _age(moment: datetime) -> str:
    seconds = max(0.0, (datetime.now(timezone.utc) - moment).total_seconds())
    if seconds < 3600:
        return "just now"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    days = int(seconds // 86400)
    return "yesterday" if days == 1 else f"{days} days ago" if days < 60 else f"{days // 30} months ago"


def _local_when(moment: datetime, tz_name: Optional[str]) -> str:
    try:
        local = moment.astimezone(pytz.timezone(tz_name)) if tz_name else moment
    except pytz.UnknownTimeZoneError:
        local = moment
    return f"{local.strftime('%a %d %b').replace(' 0', ' ')} {clock_time(local)}"


@dataclass
class PulseListener:
    user: Optional[User]
    user_id: Optional[int]
    session_id: Optional[str]
    location: ListenerLocation
    region: Optional[regional_kb.Region]
    taste: regional_kb.Taste
    tz_name: Optional[str]
    where: Optional[geo.Where] = None

    @property
    def asker(self) -> str:
        return f"user:{self.user_id}" if self.user_id else f"session:{self.session_id}"

    @property
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass
class PulseQuery:
    listener: PulseListener
    text: str = ""
    kinds: Optional[set] = None
    when: Optional[str] = None
    limit: int = 6
    allow_fetch: bool = False
    record_demand: bool = True
    near_me: bool = False
    radius_m: Optional[float] = None
    max_age_days: Optional[float] = None
    sort: str = "relevance"
    use_ai: bool = False
    per_kind: int = 3
    kind_order: list = field(default_factory=list)
    within: Optional[str] = None
    mine: bool = False
    track_id: Optional[str] = None

    def wants(self, kind: str) -> bool:
        return self.kinds is None or kind in self.kinds

    def window(self) -> tuple:
        now = self.listener.now
        try:
            zone = pytz.timezone(self.listener.tz_name) if self.listener.tz_name else pytz.utc
        except pytz.UnknownTimeZoneError:
            zone = pytz.utc
        local = now.astimezone(zone)
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        if self.when in ("today", "tonight", "now"):
            return now, (midnight + timedelta(days=1, hours=4)).astimezone(timezone.utc)
        if self.when == "tomorrow":
            return ((midnight + timedelta(days=1)).astimezone(timezone.utc),
                    (midnight + timedelta(days=2, hours=4)).astimezone(timezone.utc))
        if self.when == "weekend":
            friday = midnight + timedelta(days=(4 - local.weekday()) % 7)
            start = max(now, (friday + timedelta(hours=17)).astimezone(timezone.utc))
            return start, (friday + timedelta(days=3, hours=4)).astimezone(timezone.utc)
        if self.when == "week":
            return now, now + timedelta(days=7)
        return now, now + timedelta(days=30)


class KnowledgeNode:
    name = "node"
    kinds: tuple = ()
    browsable = True

    def matches(self, q: PulseQuery) -> bool:
        if q.kinds is not None:
            return any(kind in q.kinds for kind in self.kinds)
        return bool(q.text) or self.browsable

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        return []

    def can_fetch(self, q: PulseQuery) -> bool:
        return False

    async def wants_fetch(self, q: PulseQuery, found: list[PulseItem]) -> bool:
        return len(found) < settings.PULSE_FETCH_BELOW

    async def fetch(self, q: PulseQuery) -> list[PulseItem]:
        return []


def from_regional(item: regional_kb.KnowledgeItem, score: float) -> PulseItem:
    return from_meta({
        "id": f"{item.kind}:{item.item_id}", "kind": item.kind, "title": item.title, "text": item.text,
        "tags": item.tags, "entities": item.entities, "area": item.area,
        "where": _where_dict(geo.from_row(item.area, item.latitude, item.longitude)),
        "starts_at": item.starts_at, "published_at": item.published_at,
        "url": item.url, "attribution": item.attribution}, score)


def _where_dict(where: Optional[geo.Where]) -> Optional[dict]:
    return where.as_dict() if where else None


def from_meta(meta: Dict[str, Any], score: float) -> PulseItem:
    kind = meta.get("kind") or ""
    tags = meta.get("tags") or []
    text = meta.get("text") or ""
    if kind == KIND_EVENT:
        text = ", ".join(part for part in (text, " / ".join(tags[1:] or tags)) if part)
    starts = _parse_time(meta.get("starts_at"))
    published = _parse_time(meta.get("published_at"))
    return PulseItem(
        id=meta.get("id") or "", kind=kind, title=meta.get("title") or "", text=text,
        when=starts or (published if kind == KIND_NEWS else None), source=meta.get("attribution") or "",
        url=meta.get("url") or "", score=score, published=published, where=geo.Where.from_dict(meta.get("where")),
        area=meta.get("area") or "", entities=list(meta.get("entities") or []))


def taste_boost(meta: Dict[str, Any], taste: regional_kb.Taste) -> float:
    regional = regional_kb.get_regional_knowledge()
    if regional is None or taste is None or taste.empty:
        return 0.0
    item = regional_kb.KnowledgeItem(source="", kind=meta.get("kind") or "", region_key="", external_id="",
                                     title=meta.get("title") or "", expires_at=datetime.now(timezone.utc),
                                     tags=list(meta.get("tags") or []), starts_at=_parse_time(meta.get("starts_at")))
    return 0.15 * regional.score(item, taste, datetime.now(timezone.utc), 30)
