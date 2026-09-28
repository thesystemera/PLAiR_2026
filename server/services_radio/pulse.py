import asyncio
import hashlib
import math
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional

import numpy as np
import pytz
from sqlalchemy import String, cast, func, select

from config import settings
from database.connection import AsyncSessionLocal
from database.models import ArtistBiography, PlayEvent, User
from models_global import run_on_gpu_executor
from service_registry import services
from services import log_service
from services.listener_request_service import daypart
from services.task_utils import spawn
from services_radio import area_signals, local_knowledge, place_memory
from services_radio import regional_knowledge as regional_kb
from services_radio.dj_bank_sources import listener_taste
from services_radio.listener_location import ListenerLocation
from services_radio.news_store import normalize_query

KIND_EVENT = "event"
KIND_PLACE = "place"
KIND_NEWS = "news"
KIND_WEATHER = "weather"
KIND_AREA = "area"
KIND_ARTIST = "artist"
KIND_COMMUNITY = "community"
KIND_CHART = "chart"
KIND_TREND = "trend"
ALL_KINDS = (KIND_EVENT, KIND_PLACE, KIND_NEWS, KIND_WEATHER, KIND_AREA, KIND_ARTIST, KIND_COMMUNITY, KIND_CHART,
             KIND_TREND)
WHEN_VALUES = ("now", "today", "tonight", "tomorrow", "weekend", "week", "month")

KIND_EXAMPLES = {
    KIND_EVENT: ("gigs and concerts this weekend", "what's on tonight", "live music", "comedy shows", "festivals",
                 "is this band playing"),
    KIND_PLACE: ("good cafes near me", "late night food", "dive bars around here", "where can I get dumplings",
                 "record stores nearby"),
    KIND_NEWS: ("latest news", "what's happening with the election", "sports results", "headlines today",
                "what's going on in the world"),
    KIND_WEATHER: ("what's the weather doing", "is it going to rain", "how cold is it tonight", "forecast for the weekend"),
    KIND_AREA: ("is the air okay today", "pollen and hay fever", "air quality", "what neighbourhood am I in"),
    KIND_COMMUNITY: ("what have people been shouting out about", "listener shoutouts", "messages from the community"),
    KIND_CHART: ("what is the city listening to", "most played songs this week", "what's popular here"),
    KIND_TREND: ("what are people asking about", "what's everyone into lately", "what are locals curious about"),
    KIND_ARTIST: ("tell me about this band", "who is this artist", "the story behind the singer"),
}
SEARCHED_KINDS = {KIND_EVENT, KIND_PLACE, KIND_NEWS}
INTENTS = {KIND_EVENT: "events", KIND_PLACE: "places", KIND_NEWS: "news", KIND_COMMUNITY: "community",
           KIND_WEATHER: "weather", KIND_AREA: "area", KIND_ARTIST: "artists"}
TEXT_MAX = 180
SHOUTOUT_BROWSE = "recent community messages and shoutouts"
LISTENER_CACHE_S = 60
LISTENER_CACHE_MAX = 2000
OFFERED_MAX = 4000
CONTEXT_MEMO_S = 15
NEAR_RADIUS_M = 3000
LINK_NAME_MIN_CHARS = 4
LINK_STOP_NAMES = {"the", "live", "music", "tour", "night", "show", "festival", "auckland", "wellington", "sydney",
                   "melbourne", "london", "new york", "los angeles", "tba", "n/a", "various artists", "concert"}
PEOPLE_WEIGHTS = {"nugget_people": 0.4, "nugget_title": 0.35, "nugget_tags": 0.15, "nugget_place": 0.1}


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
    distance_m: Optional[int] = None
    source: str = ""
    url: str = ""
    score: float = 0.0
    aired: bool = False
    live: bool = False
    payload: dict = field(default_factory=dict)
    published: Optional[datetime] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    area: str = ""
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
        if self.area:
            entry["area"] = _clip(self.area, 60)
        if self.distance_m is not None:
            entry["distance"] = f"{self.distance_m} m" if self.distance_m < 1000 else f"{self.distance_m / 1000:.1f} km"
        if self.source:
            entry["source"] = self.source
        if self.links:
            entry["linked"] = [f"{link['reason']}: {_clip(link['title'], 70)}" for link in self.links[:3]]
        if self.aired:
            entry["aired_recently"] = True
        return entry

    def line(self, tz_name: Optional[str] = None) -> str:
        brief = self.brief(tz_name)
        extras = " | ".join(str(brief[k]) for k in ("text", "when", "age", "area", "distance") if k in brief)
        if brief.get("linked"):
            extras += " | linked: " + "; ".join(brief["linked"])
        return f"- [{self.kind}] {brief['title']}" + (f" ({extras})" if extras else "") + (
            " [already mentioned to this listener]" if self.aired else "")


def _age(moment: datetime) -> str:
    seconds = max(0.0, (datetime.now(timezone.utc) - moment).total_seconds())
    if seconds < 3600:
        return "just now"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    days = int(seconds // 86400)
    return "yesterday" if days == 1 else f"{days} days ago" if days < 60 else f"{days // 30} months ago"


def _distance_m(a: tuple, b: tuple) -> float:
    return place_memory.distance_m(a[0], a[1], b[0], b[1])


def _local_when(moment: datetime, tz_name: Optional[str]) -> str:
    try:
        local = moment.astimezone(pytz.timezone(tz_name)) if tz_name else moment
    except pytz.UnknownTimeZoneError:
        local = moment
    return local.strftime("%a %d %b %H:%M").replace(" 0", " ")


@dataclass
class PulseListener:
    user: Optional[User]
    user_id: Optional[int]
    session_id: Optional[str]
    location: ListenerLocation
    region: Optional[regional_kb.Region]
    taste: regional_kb.Taste
    tz_name: Optional[str]

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
    kind_scores: Dict[str, float] = field(default_factory=dict)

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


class KindRouter:
    def __init__(self):
        self._examples: Optional[Dict[str, np.ndarray]] = None

    def _embedder(self):
        return local_knowledge.local_vector_db

    async def scores(self, text: str) -> Dict[str, float]:
        db = self._embedder()
        if db is None or not text:
            return {}

        def run():
            if self._examples is None:
                self._examples = {kind: np.stack([db._generate_embedding(example) for example in examples])
                                  for kind, examples in KIND_EXAMPLES.items()}
            vector = db._generate_embedding(text)
            return {kind: float(np.max(matrix @ vector)) for kind, matrix in self._examples.items()}

        return await run_on_gpu_executor(run)

    @staticmethod
    def relevant(scores: Dict[str, float], kind: str) -> bool:
        if not scores or kind in SEARCHED_KINDS:
            return True
        best = max(scores.values())
        score = scores.get(kind, 0.0)
        return score >= max(settings.PULSE_KIND_MIN, best - settings.PULSE_KIND_MARGIN)


router = KindRouter()


class KnowledgeNode:
    name = "node"
    kinds: tuple = ()
    browsable = True

    def matches(self, q: PulseQuery) -> bool:
        if q.kinds is not None:
            return any(kind in q.kinds for kind in self.kinds)
        if q.text:
            return any(router.relevant(q.kind_scores, kind) for kind in self.kinds)
        return self.browsable

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        return []

    def can_fetch(self, q: PulseQuery) -> bool:
        return False

    async def fetch(self, q: PulseQuery) -> list[PulseItem]:
        return []


def from_regional(item: regional_kb.KnowledgeItem, score: float) -> PulseItem:
    return from_meta({
        "id": f"{item.kind}:{item.item_id}", "kind": item.kind, "title": item.title, "text": item.text,
        "tags": item.tags, "entities": item.entities, "area": item.area, "latitude": item.latitude,
        "longitude": item.longitude, "starts_at": item.starts_at, "published_at": item.published_at,
        "url": item.url, "attribution": item.attribution}, score)


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
        url=meta.get("url") or "", score=score, published=published, latitude=meta.get("latitude"),
        longitude=meta.get("longitude"), area=meta.get("area") or "", entities=list(meta.get("entities") or []))


def taste_boost(meta: Dict[str, Any], taste: regional_kb.Taste) -> float:
    regional = regional_kb.get_regional_knowledge()
    if regional is None or taste is None or taste.empty:
        return 0.0
    item = regional_kb.KnowledgeItem(source="", kind=meta.get("kind") or "", region_key="", external_id="",
                                     title=meta.get("title") or "", expires_at=datetime.now(timezone.utc),
                                     tags=list(meta.get("tags") or []), starts_at=_parse_time(meta.get("starts_at")))
    return 0.15 * regional.score(item, taste, datetime.now(timezone.utc), 30)


class LocalNuggetsNode(KnowledgeNode):
    name = "local"
    kinds = (KIND_EVENT, KIND_NEWS)

    def _keep(self, q: PulseQuery):
        region_key = q.listener.region.key
        start, end = q.window()
        wanted = {kind for kind in self.kinds if (q.wants(kind) if q.kinds is not None else
                                                  router.relevant(q.kind_scores, kind))}

        def keep(meta: Dict[str, Any]) -> bool:
            if meta.get("region_key") != region_key or meta.get("kind") not in wanted:
                return False
            if meta.get("kind") == KIND_EVENT:
                starts = _parse_time(meta.get("starts_at"))
                return starts is None or start <= starts <= end
            return True
        return keep

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        if q.listener.region is None:
            return []
        if not q.text:
            regional = regional_kb.get_regional_knowledge()
            if regional is None or not q.wants(KIND_EVENT):
                return []
            scored = await regional.query(q.listener.region, (regional_kb.KIND_EVENT,), q.listener.taste,
                                          window=q.window(), limit=q.limit * 3)
            return [from_regional(item, value) for value, item in scored]
        search = local_knowledge.local_search
        if search is None:
            return []
        results = await search.search(q.text, n=q.limit * 6, keep=self._keep(q),
                                      boost=lambda meta: taste_boost(meta, q.listener.taste), use_ai=q.use_ai)
        items: Dict[str, PulseItem] = {}
        for score, _, meta in results:
            if score < settings.PULSE_SEARCH_MIN_SCORE:
                continue
            item = from_meta(meta, score)
            key = f"{item.kind}:{item.title.lower()}"
            other = items.get(key)
            if other is None or (item.when and other.when and item.when < other.when and item.score >= other.score - 0.02):
                items[key] = item
        return list(items.values())[:q.limit * 3]

    def can_fetch(self, q: PulseQuery) -> bool:
        return services.events_service is not None and (q.wants(KIND_EVENT) if q.kinds is not None else
                                                         router.relevant(q.kind_scores, KIND_EVENT)) \
            and bool(q.listener.location.query_point() or q.listener.location.city)

    async def fetch(self, q: PulseQuery) -> list[PulseItem]:
        regional = regional_kb.get_regional_knowledge()
        location = q.listener.location
        start, end = q.window()
        events = await services.events_service.get_ticketmaster_events(
            location.query_point() or location.city, location.country_code or None, start, end, q.text or None)
        region = q.listener.region
        if not events or region is None:
            return []
        now = datetime.now(timezone.utc)
        items = [i for i in (regional_kb.TicketmasterEventsCollector.to_item(region, e, now) for e in events) if i]
        if regional is not None and items:
            await regional.ingest(region, items)
        found = [from_regional(item, 0.5) for item in items]
        for item in found:
            item.live = True
        return found


class PlacesNode(KnowledgeNode):
    name = "places"
    kinds = (KIND_PLACE,)

    @staticmethod
    def _from_result(result: dict, score: float) -> PulseItem:
        details = [result.get("type") or ""]
        if result.get("rating"):
            details.append(f"rated {result['rating']}")
        if result.get("address"):
            details.append(result["address"])
        return PulseItem(id=f"place:{result['place_id']}", kind=KIND_PLACE, title=result.get("name") or "",
                         text=", ".join(d for d in details if d), distance_m=result.get("distance_m"),
                         source="Google Maps", score=score, payload={"website": result.get("website")},
                         entities=[result.get("name") or ""])

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        coords = q.listener.location.coords
        if q.text and coords:
            results = await place_memory.lookup(q.text, coords[0], coords[1], settings.PULSE_PLACE_RADIUS_M,
                                                q.limit)
            return [self._from_result(r, 0.7) for r in results or [] if r.get("place_id")]
        regional = regional_kb.get_regional_knowledge()
        if q.text or regional is None or q.listener.region is None:
            return []
        scored = await regional.query(q.listener.region, (regional_kb.KIND_PLACE,), q.listener.taste, limit=4)
        items = []
        for base, item in scored:
            hydrated = await regional.hydrate(item)
            if hydrated and hydrated.title:
                items.append(PulseItem(id=f"place:{item.external_id.split('|', 1)[0]}", kind=KIND_PLACE,
                                       title=hydrated.title, text=hydrated.text, source="Google Maps", score=base,
                                       latitude=hydrated.latitude, longitude=hydrated.longitude,
                                       entities=[hydrated.title]))
        return items

    def can_fetch(self, q: PulseQuery) -> bool:
        return bool(q.text) and q.listener.location.coords is not None and services.location_service is not None \
            and services.location_service.available()

    async def fetch(self, q: PulseQuery) -> list[PulseItem]:
        results = await services.location_service.get_nearby_places(q.text, q.listener.location.coords,
                                                                     radius=settings.PULSE_PLACE_RADIUS_M,
                                                                     max_results=q.limit)
        items = [self._from_result(r, 0.7) for r in results or [] if r.get("place_id")]
        for item in items:
            item.live = True
        return items


class NewsNode(KnowledgeNode):
    name = "news"
    kinds = (KIND_NEWS,)

    @staticmethod
    def _items(articles: list[dict], score: float) -> list[PulseItem]:
        items = []
        for rank, article in enumerate(articles):
            published = _parse_time(article.get("publishedAt"))
            items.append(PulseItem(
                id=f"news:{article.get('id')}", kind=KIND_NEWS, title=article.get("title") or "",
                text=(article.get("source") or {}).get("name", ""), when=published, source="Google News",
                url=article.get("url") or "", score=max(0.1, score - rank * 0.04), aired=bool(article.get("aired")),
                payload={"article_id": article.get("id")}, published=published))
        return items

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        news = services.news_service
        if news is None or not news.store_enabled:
            return []
        country = q.listener.location.country_code or None
        articles = await news.stored_articles(q.text, country, subject=q.listener.session_id, limit=q.limit)
        return self._items(articles, 0.75 if q.text else 0.5)

    def can_fetch(self, q: PulseQuery) -> bool:
        return bool(q.text) and services.news_service is not None

    async def fetch(self, q: PulseQuery) -> list[PulseItem]:
        country = q.listener.location.country_code or None
        articles, _ = await services.news_service.get_top_news(query=q.text, country=country,
                                                               subject=q.listener.session_id, top_n=q.limit)
        items = self._items(articles, 0.75)
        for item in items:
            item.live = True
        return items


class WeatherNode(KnowledgeNode):
    name = "weather"
    kinds = (KIND_WEATHER,)
    browsable = False

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        coords = q.listener.location.coords
        if coords is None or services.web_service is None:
            return []
        forecast = {"today": "today", "tonight": "today", "tomorrow": "tomorrow", "weekend": "week",
                    "week": "week", "month": "week"}.get(q.when or "", "current")
        report = await services.web_service.retrieve_weather_data(coords[0], coords[1], forecast)
        if not report:
            return []
        return [PulseItem(id=f"weather:{forecast}", kind=KIND_WEATHER, title=f"Weather ({forecast})",
                          text=_clip(report, 400), source="OpenWeatherMap",
                          score=0.6 + 0.3 * q.kind_scores.get(KIND_WEATHER, 1.0))]


class AreaNode(KnowledgeNode):
    name = "area"
    kinds = (KIND_AREA,)
    browsable = False

    def matches(self, q: PulseQuery) -> bool:
        if q.kinds is not None and KIND_WEATHER in q.kinds:
            return True
        return super().matches(q)

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        context = area_signals.location_context(q.listener.location, q.listener.tz_name,
                                                subject=q.listener.session_id)
        points = await area_signals.talking_points(context)
        return [PulseItem(id=f"area:{point.key}", kind=KIND_AREA, title=point.category or "area", text=point.text,
                          source=point.source, score=0.6) for point in points]


class ArtistNode(KnowledgeNode):
    name = "artists"
    kinds = (KIND_ARTIST,)
    browsable = False

    @staticmethod
    def _item(name: str, biography: str, live: bool = False) -> PulseItem:
        return PulseItem(id=f"artist:{name.lower()}", kind=KIND_ARTIST, title=name, text=_clip(biography, 420),
                         source="MusicBrainz / Wikipedia", score=0.9, live=live)

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        if not q.text or services.web_service is None:
            return []
        cached = services.web_service.cached_artist_biography(q.text)
        if cached:
            return [self._item(q.text, cached)]
        from services_radio.external_web_service import _normalize_name
        async with AsyncSessionLocal() as db:
            row = await db.get(ArtistBiography, _normalize_name(q.text))
        if row is None or row.status != "found" or not row.biography or row.expires_at < datetime.now(timezone.utc):
            return []
        return [self._item(row.artist_name or q.text, row.biography)]

    def can_fetch(self, q: PulseQuery) -> bool:
        return bool(q.text) and q.kinds == {KIND_ARTIST} and services.web_service is not None

    async def fetch(self, q: PulseQuery) -> list[PulseItem]:
        biography = await services.web_service.retrieve_artist_biography(q.text)
        return [self._item(q.text, biography, live=True)] if biography else []


def shoutout_meta(shoutout: Dict[str, Any]) -> Dict[str, Any]:
    from services.user_content_database_service import coarse_location
    user_data = shoutout.get("user_data") or {}
    meta = shoutout.get("transcription_metadata") or shoutout.get("metadata") or {}
    address = user_data.get("location") or ""
    parts = [p.strip() for p in address.split(",") if p.strip() and not any(ch.isdigit() for ch in p)]
    shoutout_id = str(shoutout.get("id") or "")
    return {
        "id": f"community:shoutouts:{shoutout_id}", "shoutout_id": shoutout_id,
        "title": f"{'Reply' if shoutout.get('parent_id') else 'Shoutout'} from {user_data.get('username') or 'a listener'}",
        "text": " ".join((shoutout.get("transcription") or shoutout.get("full_transcription") or "").split()),
        "tags": [t for t in [meta.get("category"), *(meta.get("tags") or [])] if t],
        "area": ", ".join(dict.fromkeys(parts[1:3] if len(parts) > 2 else parts[:2])) or coarse_location(address) or "",
        "published_at": shoutout.get("timestamp"),
        "audio": shoutout.get("audio_url") or (
            f"/api/user_content/shoutouts/audio/{shoutout_id.replace('_', '/', 1)}.mp3" if "_" in shoutout_id else ""),
        "latitude": user_data.get("latitude"), "longitude": user_data.get("longitude"),
    }


def shoutout_item(shoutout: Dict[str, Any], score: float, listener: PulseListener) -> PulseItem:
    meta = shoutout_meta(shoutout)
    distance = shoutout.get("distance_km")
    return PulseItem(
        id=meta["id"], kind=KIND_COMMUNITY, title=meta["title"], text=f'"{_clip(meta["text"], 160)}"',
        source="PLAiR listeners", score=score, published=_parse_time(meta["published_at"]), area=meta["area"],
        distance_m=int(distance * 1000) if isinstance(distance, (int, float)) else None,
        payload={"audio_path": meta["audio"], "shoutout_id": meta["shoutout_id"]})


def shoutout_in_region(shoutout: Dict[str, Any], listener: PulseListener) -> bool:
    distance = shoutout.get("distance_km")
    if isinstance(distance, (int, float)):
        return distance <= settings.PULSE_COMMUNITY_RADIUS_KM
    region = listener.region
    location = ((shoutout.get("user_data") or {}).get("location") or "").lower()
    return bool(region and region.name.lower() in location)


class CommunityNode(KnowledgeNode):
    name = "community"
    kinds = (KIND_COMMUNITY,)

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        search = services.user_content_vector_search_service
        if search is None:
            return []
        results = await search.search(query=q.text or SHOUTOUT_BROWSE, n_results=q.limit * 3,
                                      user_location=q.listener.location.coords, use_ai_analysis=q.use_ai)
        items = []
        for shoutout in results or []:
            if not shoutout_in_region(shoutout, q.listener):
                continue
            score = float(shoutout.get("final_score") or 0.0)
            items.append(shoutout_item(shoutout, score, q.listener))
        return items


class ChartsNode(KnowledgeNode):
    name = "charts"
    kinds = (KIND_CHART,)

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        region = q.listener.region
        catalog = services.catalog_service
        if region is None or catalog is None:
            return []
        return await charts.region_chart(region, catalog, q)


class TrendsNode(KnowledgeNode):
    name = "trends"
    kinds = (KIND_TREND,)

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        region = q.listener.region
        if region is None:
            return []
        topics = await demand.hot(region.key, days=7, min_askers=settings.PULSE_TREND_MIN_ASKERS, limit=q.limit * 2)
        if q.text and topics:
            matched = {meta.get("topic") for _, _, meta in await demand.search(q.listener, q.text, days=7, limit=20)}
            topics = [topic for topic in topics if topic["topics"] & matched] or []
        items = []
        for topic in topics[:q.limit]:
            text = f"{topic['askers']} listeners asked this week"
            if topic["top_answers"]:
                text += f"; what kept coming up: {', '.join(topic['top_answers'])}"
            items.append(PulseItem(id=f"trend:{topic['node']}:{normalize_query(topic['label'])}", kind=KIND_TREND,
                                   title=f"People in {region.name} have been asking about {topic['label']} "
                                         f"({topic['node']})",
                                   text=text, source="PLAiR listeners", score=min(1.0, 0.3 + topic["askers"] / 10),
                                   area=topic.get("top_area") or ""))
        return items


class RegionCharts:
    def __init__(self):
        self._cache: dict[str, tuple[float, list]] = {}

    async def region_chart(self, region, catalog, q: PulseQuery) -> list[PulseItem]:
        cached = self._cache.get(region.key)
        if cached and time.monotonic() - cached[0] < settings.PULSE_CHART_CACHE_S:
            rows = cached[1]
        else:
            since = datetime.now(timezone.utc) - timedelta(days=7)
            async with AsyncSessionLocal() as db:
                plays = (await db.execute(
                    select(PlayEvent.track_id, func.count(), func.count(func.distinct(
                        func.coalesce(cast(PlayEvent.user_id, String), PlayEvent.session_id))))
                    .where(PlayEvent.region_key == region.key, PlayEvent.event_type == "play",
                           PlayEvent.started_at >= since)
                    .group_by(PlayEvent.track_id).order_by(func.count().desc()).limit(40))).all()
            rows = [(track_id, count, listeners) for track_id, count, listeners in plays]
            self._cache[region.key] = (time.monotonic(), rows)
        if not rows:
            return []
        genres: dict[str, float] = {}
        items = []
        for rank, (track_id, count, listeners) in enumerate(rows):
            track = catalog.get_track(track_id)
            if not track:
                continue
            tags = track.get("derived_tags") or {}
            genre = str(tags.get("primary_genre") or "").strip()
            if genre:
                genres[genre] = genres.get(genre, 0.0) + count
            if rank < 5 and listeners >= settings.PULSE_CHART_MIN_LISTENERS:
                params = track.get("generation_params") or {}
                title, artist = (params.get("title") or "").strip(), (params.get("artist_name") or "").strip()
                if not title:
                    continue
                label = f"{title} by {artist}" if artist else title
                value = 0.7 - rank * 0.08
                if value is not None:
                    items.append(PulseItem(id=f"chart:{region.key}:{track_id}", kind=KIND_CHART,
                                           title=f"#{rank + 1} in {region.name} this week: {label}",
                                           text=f"{count} plays by {listeners} listeners", source="PLAiR plays",
                                           score=value, payload={"track_id": track_id}))
        total = sum(genres.values())
        if total and len(rows) >= settings.PULSE_CHART_MIN_LISTENERS:
            top = sorted(genres.items(), key=lambda kv: kv[1], reverse=True)[:3]
            summary = ", ".join(f"{g} ({v / total:.0%})" for g, v in top)
            value = 0.65
            if value is not None:
                items.append(PulseItem(id=f"chart:{region.key}:genres", kind=KIND_CHART,
                                       title=f"What {region.name} is playing this week", text=summary,
                                       source="PLAiR plays", score=value))
        return items


class DemandLedger:
    def __init__(self):
        self._salt_day: Optional[date] = None
        self._salt = ""
        self._topics: dict[tuple, tuple[float, list]] = {}
        self._recent: OrderedDict[tuple, float] = OrderedDict()

    def _asker_hash(self, asker: str, day: date) -> str:
        if self._salt_day != day:
            self._salt_day, self._salt = day, hashlib.sha256(f"{settings.JWT_SECRET_KEY}:{day}".encode()).hexdigest()
        return hashlib.sha256(f"{self._salt}:{asker}".encode()).hexdigest()[:24]

    def record(self, listener: PulseListener, node: str, query_text: str, live: bool,
               answers: Iterable[tuple] = ()) -> None:
        topic = normalize_query(query_text)[:120]
        if not topic or listener.region is None or not settings.PULSE_DEMAND_ENABLED:
            return
        if services.request_store is None or services.request_vector_db_service is None:
            return
        recent_key = (listener.asker, topic)
        last = self._recent.get(recent_key)
        if last is not None and time.monotonic() - last < settings.PULSE_REQUEST_DEDUPE_S:
            return
        self._recent[recent_key] = time.monotonic()
        while len(self._recent) > 2000:
            self._recent.popitem(last=False)
        now = datetime.now(timezone.utc)
        local = now
        if listener.tz_name:
            try:
                local = now.astimezone(pytz.timezone(listener.tz_name))
            except pytz.UnknownTimeZoneError:
                pass
        served = [(str(key)[:200], str(title or "")[:200]) for key, title in answers if key][:settings.PULSE_DEMAND_ANSWERS]
        meta = {
            "request_id": uuid.uuid4().hex,
            "text": query_text.strip()[:200],
            "intent": node,
            "topic": topic,
            "answers": [title for _, title in served if title],
            "answer_keys": [key for key, _ in served],
            "region_key": listener.region.key,
            "area": _clip(listener.location.description or listener.location.city or "", 80),
            "daypart": daypart(local),
            "weekday": local.strftime("%A"),
            "asked_at": now.isoformat(),
            "asker": self._asker_hash(listener.asker, now.date()),
            "live": bool(live),
        }
        spawn(self._record(meta), name="pulse_request")

    async def _record(self, meta: dict) -> None:
        try:
            rowid = await asyncio.to_thread(services.request_store.add, meta)
            await run_on_gpu_executor(services.request_vector_db_service.add_request, meta, rowid)
            self._topics = {k: v for k, v in self._topics.items() if k[0] != meta["region_key"]}
        except Exception as e:
            log_service.warning(f"[PULSE] request record failed: {type(e).__name__}: {e}")

    async def topics(self, region_key: str, days: int = 7, node: Optional[str] = None) -> list[dict]:
        vectors = services.request_vector_db_service
        if vectors is None:
            return []
        cache_key = (region_key, days, node)
        cached = self._topics.get(cache_key)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        since = datetime.now(timezone.utc) - timedelta(days=days)
        rows = [(rowid, meta) for rowid, meta in vectors.rows(region_key, since) if not node or meta.get("intent") == node]
        embedded = await run_on_gpu_executor(lambda: [(meta, vectors.vector(rowid, meta)) for rowid, meta in rows])
        topics = _cluster_requests(embedded)
        self._topics[cache_key] = (time.monotonic(), topics)
        return topics

    async def hot(self, region_key: str, days: int = 7, min_askers: int = 3, limit: int = 10,
                  node: Optional[str] = None) -> list[dict]:
        return [topic for topic in await self.topics(region_key, days, node) if topic["askers"] >= min_askers][:limit]

    async def search(self, listener: PulseListener, text: str, days: int = 30, limit: int = 8,
                     use_ai: bool = False) -> list[tuple]:
        search = services.request_search
        if search is None or listener.region is None or not text:
            return []
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        region_key = listener.region.key
        return await search.search(text, n=limit, use_ai=use_ai,
                                   keep=lambda meta: meta.get("region_key") == region_key and
                                   (meta.get("asked_at") or "") >= since)

    async def prune(self) -> None:
        if services.request_store is not None:
            removed = await asyncio.to_thread(services.request_store.prune, settings.PULSE_DEMAND_KEEP_DAYS)
            if removed and services.request_vector_db_service is not None:
                services.request_vector_db_service.dirty = True


def _cluster_requests(rows: list[tuple]) -> list[dict]:
    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (a, va) in enumerate(rows):
        keys_a = set(a.get("answer_keys") or [])
        for j in range(i + 1, len(rows)):
            b, vb = rows[j]
            if a.get("intent") != b.get("intent"):
                continue
            keys_b = set(b.get("answer_keys") or [])
            same = a.get("topic") == b.get("topic")
            if not same and keys_a and keys_b:
                same = len(keys_a & keys_b) / len(keys_a | keys_b) >= settings.PULSE_DEMAND_ANSWER_OVERLAP
            if not same:
                same = float(np.dot(va, vb)) >= settings.PULSE_REQUEST_SIMILARITY
            if same:
                parent[find(j)] = find(i)
    groups: dict[int, dict] = {}
    for i, (meta, _) in enumerate(rows):
        group = groups.setdefault(find(i), {"node": meta.get("intent"), "queries": {}, "askers": set(), "asks": 0,
                                            "live": 0, "answers": {}, "areas": {}, "topics": set(),
                                            "last": meta.get("asked_at")})
        group["topics"].add(meta.get("topic"))
        group["queries"][meta.get("text")] = group["queries"].get(meta.get("text"), 0) + 1
        group["askers"].add(meta.get("asker"))
        group["asks"] += 1
        group["live"] += 1 if meta.get("live") else 0
        group["last"] = max(group["last"], meta.get("asked_at") or "")
        for title in meta.get("answers") or []:
            group["answers"][title] = group["answers"].get(title, 0) + 1
        if meta.get("area"):
            group["areas"][meta["area"]] = group["areas"].get(meta["area"], 0) + 1
    topics = []
    for group in groups.values():
        topics.append({
            "node": group["node"],
            "label": max(group["queries"].items(), key=lambda kv: kv[1])[0],
            "askers": len(group["askers"]),
            "asks": group["asks"],
            "live": group["live"],
            "top_answers": [t for t, _ in sorted(group["answers"].items(), key=lambda kv: kv[1], reverse=True)][:3],
            "top_area": max(group["areas"].items(), key=lambda kv: kv[1])[0] if group["areas"] else "",
            "topics": group["topics"],
            "last": group["last"],
        })
    topics.sort(key=lambda t: (t["askers"], t["asks"]), reverse=True)
    return topics


async def note_request(user, user_id: Optional[int], session_id: Optional[str], node: str, query: Optional[str],
                       answers: Iterable[tuple] = (), live: bool = False) -> None:
    pulse = get_pulse()
    if pulse is None or not query or not (user_id or session_id):
        return
    try:
        listener = await pulse.listener(user_id, session_id, user)
        demand.record(listener, node, query, live, answers)
    except Exception as e:
        log_service.warning(f"[PULSE] demand note failed: {type(e).__name__}: {e}")


demand = DemandLedger()
charts = RegionCharts()


class Pulse:
    def __init__(self, nodes: list[KnowledgeNode]):
        self.nodes = nodes
        self._listeners: OrderedDict[str, tuple[float, PulseListener]] = OrderedDict()
        self._offered: OrderedDict[tuple, float] = OrderedDict()
        self._region_keys: OrderedDict[str, tuple[float, Optional[str]]] = OrderedDict()
        self._context_memo: OrderedDict[tuple, tuple[float, asyncio.Future]] = OrderedDict()
        self._link_cache: dict[str, tuple] = {}

    def node(self, name: str) -> Optional[KnowledgeNode]:
        return next((n for n in self.nodes if n.name == name), None)

    async def listener(self, user_id: Optional[int], session_id: Optional[str],
                       user: Optional[User] = None) -> PulseListener:
        key = f"{user_id or ''}:{session_id or ''}"
        cached = self._listeners.get(key)
        if cached and time.monotonic() - cached[0] < LISTENER_CACHE_S:
            return cached[1]
        if user is None and user_id:
            async with AsyncSessionLocal() as db:
                user = await db.get(User, user_id)
        from services_radio import context_service
        location = await context_service.listener_location(user, session_id)
        tz_name = location.timezone or context_service.listener_timezone(user, session_id)
        region = regional_kb.resolve_region(user, tz_name, location=location)
        taste = await listener_taste(user, user_id, session_id, AsyncSessionLocal, services.catalog_service)
        resolved = PulseListener(user=user, user_id=user_id, session_id=session_id, location=location, region=region,
                                 taste=taste, tz_name=tz_name)
        self._listeners[key] = (time.monotonic(), resolved)
        self._listeners.move_to_end(key)
        while len(self._listeners) > LISTENER_CACHE_MAX:
            self._listeners.popitem(last=False)
        self._remember_region(session_id, region.key if region else None)
        return resolved

    async def listener_for_session(self, session_dict: dict) -> PulseListener:
        return await self.listener(session_dict.get("user_id"), session_dict.get("session_id"))

    def _remember_region(self, session_id: Optional[str], region_key: Optional[str]) -> None:
        if not session_id:
            return
        self._region_keys[session_id] = (time.monotonic(), region_key)
        self._region_keys.move_to_end(session_id)
        while len(self._region_keys) > LISTENER_CACHE_MAX:
            self._region_keys.popitem(last=False)

    async def region_key_for(self, user_id: Optional[int], session_id: Optional[str]) -> Optional[str]:
        cached = self._region_keys.get(session_id or "")
        if cached and time.monotonic() - cached[0] < 3600:
            return cached[1]
        try:
            listener = await self.listener(user_id, session_id)
            return listener.region.key if listener.region else None
        except Exception as e:
            log_service.warning(f"[PULSE] region lookup failed: {type(e).__name__}: {e}")
            self._remember_region(session_id, None)
            return None

    def _was_offered(self, listener: PulseListener, item_id: str) -> bool:
        stamp = self._offered.get((listener.asker, item_id))
        return stamp is not None and time.monotonic() - stamp < settings.PULSE_OFFERED_MEMORY_S

    def mark_offered(self, listener: PulseListener, items: Iterable[PulseItem]) -> None:
        now = time.monotonic()
        for item in items:
            self._offered[(listener.asker, item.id)] = now
            self._offered.move_to_end((listener.asker, item.id))
        while len(self._offered) > OFFERED_MAX:
            self._offered.popitem(last=False)

    async def context_matches(self, listener: PulseListener, text: str) -> list[PulseItem]:
        key = (listener.asker, text or "")
        cached = self._context_memo.get(key)
        if cached and time.monotonic() - cached[0] < CONTEXT_MEMO_S:
            return await asyncio.shield(cached[1])
        future = asyncio.ensure_future(self.query(PulseQuery(listener=listener, text=text or "",
                                                             limit=settings.PULSE_CONTEXT_ITEMS,
                                                             record_demand=False)))
        self._context_memo[key] = (time.monotonic(), future)
        while len(self._context_memo) > 200:
            self._context_memo.popitem(last=False)
        return await asyncio.shield(future)

    async def _search_node(self, node: KnowledgeNode, q: PulseQuery) -> list[PulseItem]:
        try:
            return await asyncio.wait_for(node.search(q), timeout=settings.PULSE_NODE_TIMEOUT_S)
        except asyncio.TimeoutError:
            log_service.warning(f"[PULSE] {node.name} search timed out")
        except Exception as e:
            log_service.warning(f"[PULSE] {node.name} search failed: {type(e).__name__}: {e}")
        return []

    async def query(self, q: PulseQuery) -> list[PulseItem]:
        started = time.perf_counter()
        if q.kinds is not None:
            q.kinds = {k for k in q.kinds if k in ALL_KINDS} or None
        if q.text and q.kinds is None and not q.kind_scores:
            q.kind_scores = await router.scores(q.text)
        nodes = [n for n in self.nodes if n.matches(q)]
        results = await asyncio.gather(*(self._search_node(node, q) for node in nodes))
        found = {node.name: items for node, items in zip(nodes, results)}
        items = [item for group in results for item in group]
        live_node = None
        if q.allow_fetch and q.text and len(items) < settings.PULSE_FETCH_BELOW:
            fetchable = [n for n in nodes if n.can_fetch(q)]
            fetchable.sort(key=lambda n: -max((q.kind_scores.get(k, 0.0) for k in n.kinds), default=0.0))
            if fetchable:
                live_node = fetchable[0]
                try:
                    fetched = await asyncio.wait_for(live_node.fetch(q), timeout=settings.PULSE_FETCH_TIMEOUT_S)
                except Exception as e:
                    log_service.warning(f"[PULSE] {live_node.name} live fetch failed: {type(e).__name__}: {e}")
                    fetched = []
                known = {item.id for item in items}
                items.extend(item for item in fetched if item.id not in known)
        items = self._apply_facets(q, items)
        ranked = self._rank(q, items)
        if ranked and q.listener.region is not None:
            await self.annotate_links(q.listener, ranked)
        if q.record_demand and q.text:
            answers = [item for item in ranked if item.kind not in (KIND_TREND, KIND_CHART)]
            kinds_served = [item.kind for item in answers] or sorted(q.kinds or [])
            intent = INTENTS.get(max(set(kinds_served), key=kinds_served.count), "any") if kinds_served else "any"
            demand.record(q.listener, intent, q.text, live_node is not None,
                          [(item.id, item.title) for item in answers])
        log_service.detail(
            f"[PULSE] {log_service.who(q.listener.session_id)} '{q.text or '*'}' kinds={sorted(q.kinds or [])} -> "
            f"{len(ranked)} items ({', '.join(f'{k}:{len(v)}' for k, v in found.items() if v) or 'none'}"
            f"{', live ' + live_node.name if live_node else ''}) {(time.perf_counter() - started) * 1000:.0f} ms",
            "pulse")
        return ranked


    def _apply_facets(self, q: PulseQuery, items: list[PulseItem]) -> list[PulseItem]:
        point = q.listener.location.coords
        radius = q.radius_m or (NEAR_RADIUS_M if q.near_me else None)
        kept = []
        for item in items:
            if point and item.latitude is not None and item.longitude is not None and item.distance_m is None:
                item.distance_m = int(_distance_m(point, (item.latitude, item.longitude)))
            if radius and (item.distance_m is None or item.distance_m > radius):
                continue
            if q.max_age_days is not None and item.published is not None and \
                    (q.listener.now - item.published).total_seconds() > q.max_age_days * 86400:
                continue
            if item.kind in (KIND_NEWS, KIND_COMMUNITY):
                item.score *= 0.6 + 0.4 * _recency(item.published)
            if (q.near_me or q.sort == "nearest") and item.distance_m is not None:
                item.score *= 0.5 + 0.5 * math.exp(-item.distance_m / 2000.0)
            kept.append(item)
        return kept

    def _sort(self, q: PulseQuery, items: list[PulseItem]) -> list[PulseItem]:
        epoch = datetime.min.replace(tzinfo=timezone.utc)
        far = datetime.max.replace(tzinfo=timezone.utc)
        if q.sort == "newest":
            return sorted(items, key=lambda i: i.published or i.when or epoch, reverse=True)
        if q.sort == "soonest":
            return sorted(items, key=lambda i: i.when if i.when and i.when >= q.listener.now else far)
        if q.sort == "nearest":
            return sorted(items, key=lambda i: i.distance_m if i.distance_m is not None else 10 ** 9)
        return sorted(items, key=lambda item: item.score, reverse=True)

    def _region_index(self, region_key: str) -> dict:
        db = local_knowledge.local_vector_db
        stamp = (len(db._metadata_cache), db.current_index) if db is not None else (0, 0)
        cached = self._link_cache.get(region_key)
        if cached and cached[0] == stamp and time.monotonic() - cached[1] < 300:
            return cached[2]
        by_id: Dict[str, Dict[str, Any]] = {}
        names: Dict[str, list] = {}
        for meta in list(db._metadata_cache.values()) if db is not None else []:
            if meta.get("region_key") != region_key:
                continue
            by_id[meta["id"]] = meta
            candidates = list(meta.get("entities") or [])
            if meta.get("kind") == KIND_EVENT:
                candidates.append(meta.get("title") or "")
            for name in candidates:
                key = " ".join((name or "").lower().split())
                if len(key) >= LINK_NAME_MIN_CHARS and key not in LINK_STOP_NAMES:
                    names.setdefault(key, []).append(meta["id"])
        pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")\b",
                             re.IGNORECASE) if names else None
        index = {"by_id": by_id, "names": names, "pattern": pattern}
        self._link_cache[region_key] = (stamp, time.monotonic(), index)
        return index

    @staticmethod
    def _mentions(index: dict, text: str) -> list[tuple]:
        if index["pattern"] is None or not text:
            return []
        found = []
        for match in index["pattern"].finditer(text):
            for target in index["names"].get(" ".join(match.group(1).lower().split()), []):
                if (target, match.group(1)) not in found:
                    found.append((target, match.group(1)))
        return found

    def _region_shoutouts(self, listener: PulseListener) -> list[Dict[str, Any]]:
        store = services.user_content_service
        point = listener.location.coords
        found = []
        for shoutout in list((getattr(store, "shoutouts", None) or {}).values()):
            if shoutout.get("content_type", "shoutout") != "shoutout":
                continue
            user_data = shoutout.get("user_data") or {}
            try:
                there = (float(user_data.get("latitude")), float(user_data.get("longitude")))
            except (TypeError, ValueError):
                there = None
            if point and there:
                if _distance_m(point, there) > settings.PULSE_COMMUNITY_RADIUS_KM * 1000:
                    continue
            elif not (listener.region and listener.region.name.lower() in (user_data.get("location") or "").lower()):
                continue
            found.append(shoutout)
        return found

    async def related(self, listener: PulseListener, pulse_id: str, limit: int = 6) -> list[dict]:
        if listener.region is None:
            return []
        index = self._region_index(listener.region.key)
        found: Dict[str, dict] = {}
        threshold = settings.PULSE_LINK_SIMILARITY
        region_key = listener.region.key

        own_base = _base_title((index["by_id"].get(pulse_id) or {}).get("title") or "")

        def add(link_id: str, kind: str, title: str, reason: str) -> None:
            base = _base_title(title)
            if link_id == pulse_id or link_id in found or (own_base and base == own_base) or \
                    base in {_base_title(f["title"]) for f in found.values()}:
                return
            found[link_id] = {"id": link_id, "kind": kind, "title": title, "reason": reason}

        if pulse_id.startswith("community:shoutouts:"):
            shoutout = (getattr(services.user_content_service, "shoutouts", None) or {}).get(pulse_id.split(":", 2)[2])
            if shoutout is None:
                return []
            text = shoutout_meta(shoutout)["text"]
            for target, name in self._mentions(index, text):
                meta = index["by_id"].get(target)
                if meta:
                    add(target, meta["kind"], meta["title"], f"mentions {name}")
            if local_knowledge.local_search is not None and text:
                for score, _, meta in await local_knowledge.local_search.search(
                        text, n=4, weights=PEOPLE_WEIGHTS,
                        keep=lambda m: m.get("region_key") == region_key):
                    if score >= threshold:
                        add(meta["id"], meta["kind"], meta["title"], "same subject")
            return list(found.values())[:limit]

        meta = index["by_id"].get(pulse_id)
        if meta is None:
            return []
        own_names = {" ".join(n.lower().split()) for n in [*(meta.get("entities") or []), meta.get("title") or ""]
                     if len(n or "") >= LINK_NAME_MIN_CHARS}
        for shoutout in self._region_shoutouts(listener):
            text = shoutout_meta(shoutout)["text"].lower()
            hit = next((name for name in own_names if name and name not in LINK_STOP_NAMES and
                        re.search(r"\b" + re.escape(name) + r"\b", text)), None)
            if hit:
                sm = shoutout_meta(shoutout)
                add(sm["id"], KIND_COMMUNITY, sm["title"], f"shoutout mentioning {hit}")
        for other in index["by_id"].values():
            if other.get("kind") != KIND_NEWS:
                continue
            if any(re.search(r"\b" + re.escape(name) + r"\b", (other.get("title") or "").lower())
                   for name in own_names if name not in LINK_STOP_NAMES):
                add(other["id"], KIND_NEWS, other["title"], "in the news")
        here = (meta.get("latitude"), meta.get("longitude"))
        partner = {KIND_EVENT: KIND_PLACE, KIND_PLACE: KIND_EVENT}.get(meta.get("kind"))
        if here[0] is not None and partner:
            nearby = sorted(
                ((_distance_m(here, (other["latitude"], other["longitude"])), other) for other in index["by_id"].values()
                 if other.get("kind") == partner and other.get("latitude") is not None),
                key=lambda pair: pair[0])
            for distance, other in nearby[:3]:
                if distance <= settings.PULSE_LINK_DISTANCE_M:
                    add(other["id"], other["kind"], other["title"], f"{int(distance)} m away")
        if local_knowledge.local_search is not None:
            for score, _, other in await local_knowledge.local_search.similar_to(
                    meta, PEOPLE_WEIGHTS, n=4, keep=lambda m: m.get("region_key") == region_key and
                    m.get("kind") != meta.get("kind")):
                if score >= threshold:
                    add(other["id"], other["kind"], other["title"], "same subject")
        return list(found.values())[:limit]

    async def annotate_links(self, listener: PulseListener, items: list[PulseItem]) -> None:
        try:
            index = self._region_index(listener.region.key)
        except Exception as e:
            log_service.warning(f"[PULSE] link index failed: {type(e).__name__}: {e}")
            return
        shoutouts = None
        for item in items:
            links = []
            if item.kind in (KIND_COMMUNITY, KIND_NEWS):
                text = f"{item.title} {item.text}"
                for target, name in self._mentions(index, text):
                    meta = index["by_id"].get(target)
                    if meta and meta["title"] not in {link["title"] for link in links}:
                        links.append({"id": target, "title": meta["title"], "reason": f"mentions {name}"})
            elif item.kind in (KIND_EVENT, KIND_PLACE):
                if shoutouts is None:
                    shoutouts = [shoutout_meta(s) for s in self._region_shoutouts(listener)]
                names = {" ".join(n.lower().split()) for n in [*item.entities, item.title]
                         if len(n or "") >= LINK_NAME_MIN_CHARS and " ".join(n.lower().split()) not in LINK_STOP_NAMES}
                for sm in shoutouts:
                    hit = next((n for n in names if re.search(r"\b" + re.escape(n) + r"\b", sm["text"].lower())), None)
                    if hit:
                        links.append({"id": sm["id"], "title": f"{sm['title']}: {_clip(sm['text'], 60)}",
                                      "reason": f"shoutout mentioning {hit}"})
            item.links = links[:3]

    def _rank(self, q: PulseQuery, items: list[PulseItem]) -> list[PulseItem]:
        seen = set()
        unique = []
        for item in items:
            if item.id in seen or not item.title:
                continue
            seen.add(item.id)
            item.aired = item.aired or self._was_offered(q.listener, item.id)
            unique.append(item)
        for item in unique:
            if item.aired:
                item.score -= settings.PULSE_AIRED_PENALTY
        unique = self._sort(q, unique)
        if (q.kinds is not None and len(q.kinds) == 1) or q.sort != "relevance":
            return unique[:q.limit]
        by_kind: dict[str, list[PulseItem]] = {}
        for item in unique:
            by_kind.setdefault(item.kind, []).append(item)
        picked = []
        while len(picked) < q.limit and any(by_kind.values()):
            for kind in sorted(by_kind, key=lambda k: by_kind[k][0].score if by_kind[k] else -1, reverse=True):
                if by_kind[kind] and len(picked) < q.limit:
                    picked.append(by_kind[kind].pop(0))
        return picked

    async def detail(self, listener: PulseListener, item_id: str) -> Optional[dict]:
        kind, _, key = item_id.partition(":")
        entry = None
        if kind == KIND_COMMUNITY:
            shoutout = (getattr(services.user_content_service, "shoutouts", None) or {}).get(key.split(":", 1)[-1])
            if shoutout is not None:
                sm = shoutout_meta(shoutout)
                entry = {"id": item_id, "kind": kind, "title": sm["title"], "said": sm["text"], "area": sm["area"],
                         "tags": sm["tags"]}
                published = _parse_time(sm["published_at"])
                if published:
                    entry["age"] = _age(published)
                if sm["audio"]:
                    entry["audio"] = f"${sm['audio']}$"
                    entry["how_to_play"] = "Put the audio value in your reply to play the clip on air."
        elif kind == KIND_PLACE and ":" not in key:
            place = await place_memory.get_place(key)
            if place:
                entry = {"id": item_id, "kind": kind, **{k: v for k, v in place.items()
                                                         if v not in (None, "") and k not in ("latitude", "longitude")}}
        elif listener.region is not None:
            meta = self._region_index(listener.region.key)["by_id"].get(item_id)
            if meta is not None:
                entry = {"id": item_id, "kind": kind, "title": meta.get("title"), "details": meta.get("text"),
                         "tags": meta.get("tags"), "source": meta.get("attribution")}
                starts = _parse_time(meta.get("starts_at"))
                if starts:
                    entry["when"] = _local_when(starts, listener.tz_name)
                published = _parse_time(meta.get("published_at"))
                if published:
                    entry["age"] = _age(published)
                if meta.get("area"):
                    entry["area"] = meta["area"]
                if meta.get("entities"):
                    entry["names"] = meta["entities"]
        if entry is None and kind == KIND_NEWS and services.news_service is not None and key.isdigit():
            items = await services.news_service.store.items([int(key)])
            item = items.get(int(key))
            if item is not None:
                article = item.as_article()
                entry = {"id": item_id, "kind": kind, "title": article.get("title"),
                         "details": article.get("description") or "",
                         "source": (article.get("source") or {}).get("name"),
                         "published": article.get("publishedAt"), "tags": article.get("tags")}
        if entry is None and kind == KIND_ARTIST:
            found = await self.node("artists").search(PulseQuery(listener=listener, text=key, kinds={KIND_ARTIST}))
            if found:
                entry = {"id": item_id, "kind": kind, "title": found[0].title,
                         "details": services.web_service.cached_artist_biography(key) or found[0].text}
        if entry is None:
            return None
        related = await self.related(listener, item_id)
        if related:
            entry["related"] = [{k: v for k, v in link.items() if k != "kind"} for link in related]
        return entry

    async def listener_context(self, listener: PulseListener) -> dict:
        from services_radio.dj_bank_sources import compact_listener_notes
        location = listener.location
        context = {
            "local_time": _local_when(listener.now, listener.tz_name) if listener.tz_name else None,
            "city": listener.region.name if listener.region else (location.city or None),
            "neighbourhood": location.description or location.place or None,
            "top_genres": list(listener.taste.genres)[:6],
            "favourite_artists": sorted(listener.taste.artists)[:8],
            "interests": sorted(listener.taste.interests)[:6],
            "signed_in": bool(listener.user_id),
        }
        if listener.user is not None:
            notes = compact_listener_notes(listener.user)
            if notes:
                context["notes"] = notes
        return {k: v for k, v in context.items() if v not in (None, "", [])}


pulse: Optional[Pulse] = None


def install(instance: Optional[Pulse]) -> None:
    global pulse
    pulse = instance


def get_pulse() -> Optional[Pulse]:
    return pulse


def default_nodes() -> list[KnowledgeNode]:
    return [LocalNuggetsNode(), PlacesNode(), NewsNode(), WeatherNode(), AreaNode(), ArtistNode(), CommunityNode(),
            ChartsNode(), TrendsNode()]
