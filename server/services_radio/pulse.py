import asyncio
import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

import numpy as np
import pytz
from sqlalchemy import String, cast, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import settings
from database.connection import AsyncSessionLocal
from database.models import ArtistBiography, PlayEvent, PulseDemand, PulseDemandAsker, User
from service_registry import services
from services import log_service
from services.task_utils import spawn
from services_radio import area_signals, place_memory
from services_radio import regional_knowledge as regional_kb
from services_radio.dj_bank_sources import listener_taste
from services_radio.listener_location import ListenerLocation
from services_radio.news_store import normalize_query, tokens

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

TEXT_MAX = 180
WEATHER_WORDS = ("weather", "forecast", "rain", "raining", "sun", "sunny", "wind", "windy", "cold", "hot", "warm",
                 "temperature", "umbrella", "storm", "snow", "cloud", "outside")
GENERIC_WORDS = set(tokens(
    "gig gigs concert concerts show shows event events happening happenings live on going good best any some "
    "thing things place places spot spots somewhere near nearby around local here me tonight today tomorrow "
    "weekend week month lately latest new cool fun decent grab find looking look check whats what where who"))
AREA_WORDS = ("air", "quality", "pollen", "hay", "fever", "allergy", "allergies", "smog", "smoke", "breathe",
              "neighbourhood", "neighborhood", "suburb", "area", "street")
SHOUTOUT_BROWSE = "recent community messages and shoutouts"
LISTENER_CACHE_S = 60
CONTEXT_MEMO_S = 15
LISTENER_CACHE_MAX = 2000
OFFERED_MAX = 4000


def _unit(vector) -> Optional[np.ndarray]:
    if vector is None:
        return None
    vector = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else None


def _clip(text: str, limit: int = TEXT_MAX) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "..."


def word_overlap(query_words: set, text: str, tags: Iterable[str] = ()) -> float:
    if not query_words:
        return 0.0
    words = set(tokens(text))
    for tag in tags:
        words |= set(tokens(tag)) | set(regional_kb._tag_tokens(tag))
    return len(query_words & words) / len(query_words)


def relevance(query_words: set, query_vector: Optional[np.ndarray], text: str, tags: Iterable[str] = (),
              vector: Optional[np.ndarray] = None) -> float:
    lexical = word_overlap(query_words, text, tags)
    semantic = float(np.dot(query_vector, vector)) if query_vector is not None and vector is not None else 0.0
    if lexical > 0:
        return max(lexical, min(semantic, 1.0))
    return semantic if semantic >= settings.PULSE_SEMANTIC_ONLY_MIN else 0.0


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

    def brief(self, tz_name: Optional[str] = None) -> dict:
        entry = {"id": self.id, "kind": self.kind, "title": _clip(self.title, 120)}
        if self.text:
            entry["text"] = _clip(self.text)
        if self.when:
            entry["when"] = _local_when(self.when, tz_name)
        if self.distance_m is not None:
            entry["distance"] = f"{self.distance_m} m" if self.distance_m < 1000 else f"{self.distance_m / 1000:.1f} km"
        if self.source:
            entry["source"] = self.source
        if self.aired:
            entry["aired_recently"] = True
        return entry

    def line(self, tz_name: Optional[str] = None) -> str:
        brief = self.brief(tz_name)
        extras = " | ".join(str(brief[k]) for k in ("text", "when", "distance") if k in brief)
        return f"- [{self.kind}] {brief['title']}" + (f" ({extras})" if extras else "") + (
            " [already mentioned to this listener]" if self.aired else "")


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
    query_vector: Optional[np.ndarray] = None

    @property
    def words(self) -> set:
        words = set(normalize_query(self.text).split())
        return (words - GENERIC_WORDS) or words

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
        return any(q.wants(kind) for kind in self.kinds)

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        return []

    def can_fetch(self, q: PulseQuery) -> bool:
        return False

    async def fetch(self, q: PulseQuery) -> list[PulseItem]:
        return []


def _text_score(q: PulseQuery, base: float, text: str, tags: Iterable[str] = (),
                vector: Optional[np.ndarray] = None) -> Optional[float]:
    if not q.text:
        return base
    rel = relevance(q.words, q.query_vector, text, tags, vector)
    if rel <= 0:
        return None
    return 0.6 * rel + 0.4 * base


class EventsNode(KnowledgeNode):
    name = "events"
    kinds = (KIND_EVENT,)

    def _items(self, q: PulseQuery, scored) -> list[PulseItem]:
        items = []
        for base, item in scored:
            value = _text_score(q, base, item.embed_text, item.tags, item.vector)
            if value is None:
                continue
            items.append(PulseItem(
                id=f"event:{item.item_id}", kind=KIND_EVENT, title=item.title,
                text=", ".join(part for part in (item.text, " / ".join(item.tags[1:] or item.tags)) if part),
                when=item.starts_at, source=item.attribution, url=item.url, score=value))
        return items

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        regional = regional_kb.get_regional_knowledge()
        if regional is None or q.listener.region is None:
            return []
        scored = await regional.query(q.listener.region, (regional_kb.KIND_EVENT,), q.listener.taste,
                                      window=q.window(), limit=400)
        return self._items(q, scored)

    def can_fetch(self, q: PulseQuery) -> bool:
        return services.events_service is not None and bool(q.listener.location.query_point() or q.listener.location.city)

    async def fetch(self, q: PulseQuery) -> list[PulseItem]:
        regional = regional_kb.get_regional_knowledge()
        location = q.listener.location
        start, end = q.window()
        keyword = " ".join(sorted(q.words)) or None
        events = await services.events_service.get_ticketmaster_events(
            location.query_point() or location.city, location.country_code or None, start, end, keyword)
        if not events:
            return []
        region = q.listener.region
        now = datetime.now(timezone.utc)
        items = [i for i in (regional_kb.TicketmasterEventsCollector.to_item(region, e, now) for e in events) if i] \
            if region is not None else []
        if regional is not None and region is not None and items:
            await regional.ingest(region, items)
        scored = [(regional.score(item, q.listener.taste, now, 30) if regional else 0.5, item) for item in items]
        found = self._items(q, scored) or self._items(PulseQuery(listener=q.listener), scored)
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
                         source="Google Maps", score=score, payload={"website": result.get("website")})

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
                                       title=hydrated.title, text=hydrated.text, source="Google Maps", score=base))
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
            published = None
            try:
                published = datetime.fromisoformat(str(article.get("publishedAt")).replace("Z", "+00:00"))
            except ValueError:
                pass
            items.append(PulseItem(
                id=f"news:{article.get('id')}", kind=KIND_NEWS, title=article.get("title") or "",
                text=(article.get("source") or {}).get("name", ""), when=published, source="Google News",
                url=article.get("url") or "", score=max(0.1, score - rank * 0.04), aired=bool(article.get("aired")),
                payload={"article_id": article.get("id")}))
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
        value = 0.8 if q.kinds and KIND_WEATHER in q.kinds else _text_score(q, 0.8, report, WEATHER_WORDS)
        if value is None:
            return []
        return [PulseItem(id=f"weather:{forecast}", kind=KIND_WEATHER, title=f"Weather ({forecast})",
                          text=_clip(report, 400), source="OpenWeatherMap", score=value)]


class AreaNode(KnowledgeNode):
    name = "area"
    kinds = (KIND_AREA, KIND_WEATHER)
    browsable = False

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        context = area_signals.location_context(q.listener.location, q.listener.tz_name,
                                                subject=q.listener.session_id)
        points = await area_signals.talking_points(context)
        items = []
        explicit = bool(q.kinds and q.kinds & {KIND_AREA, KIND_WEATHER})
        for point in points:
            value = 0.6 if explicit else _text_score(q, 0.6, point.text, AREA_WORDS)
            if value is not None:
                items.append(PulseItem(id=f"area:{point.key}", kind=KIND_AREA, title=point.category or "area",
                                       text=point.text, source=point.source, score=value))
        return items


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


class CommunityNode(KnowledgeNode):
    name = "community"
    kinds = (KIND_COMMUNITY,)

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        search = services.user_content_vector_search_service
        if search is None:
            return []
        from services.user_content_database_service import coarse_location
        results = await search.search(query=q.text or SHOUTOUT_BROWSE,
                                      n_results=max(q.limit * 2, 8), user_location=q.listener.location.coords,
                                      use_ai_analysis=False)
        city = (q.listener.region.name if q.listener.region else "").lower()
        items = []
        for shoutout in results or []:
            user_data = shoutout.get("user_data") or {}
            place = coarse_location(user_data.get("location") or shoutout.get("location")) or ""
            message = (shoutout.get("transcription") or "").strip()
            if not message:
                continue
            local = bool(city) and city in place.lower()
            score = min(1.0, float(shoutout.get("final_score") or 0.5)) * (1.0 if local else 0.8)
            when = None
            try:
                when = datetime.fromisoformat(str(shoutout.get("timestamp")).replace("Z", "+00:00"))
                when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
            value = _text_score(q, score, message) if q.text != SHOUTOUT_BROWSE else score
            if value is None:
                continue
            score = value
            name = user_data.get("username") or shoutout.get("username") or "a listener"
            items.append(PulseItem(
                id=f"shoutout:{shoutout.get('id') or shoutout.get('content_id') or shoutout.get('audio_url')}",
                kind=KIND_COMMUNITY, title=f"Shoutout from {name}" + (f" ({place})" if place else ""),
                text=f'"{_clip(message, 160)}"', when=when, source="PLAiR listeners", score=score,
                payload={"audio_url": shoutout.get("audio_url") or ""}))
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
        rows = await demand.hot(region.key, days=7, min_askers=settings.PULSE_TREND_MIN_ASKERS, limit=q.limit)
        items = []
        for node_name, query_text, askers, asks in rows:
            value = _text_score(q, min(1.0, 0.3 + askers / 10), query_text)
            if value is None:
                continue
            items.append(PulseItem(id=f"trend:{node_name}:{normalize_query(query_text)}", kind=KIND_TREND,
                                   title=f"Listeners in {region.name} have been asking about: {query_text}",
                                   text=f"{askers} listeners, {asks} asks this week ({node_name})",
                                   source="PLAiR listeners", score=value))
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
                label = f"{track.get('title') or 'Unknown'} by {track.get('artists') or 'Unknown'}"
                value = _text_score(q, 0.7 - rank * 0.08, f"{label} {genre}", [genre])
                if value is not None:
                    items.append(PulseItem(id=f"chart:{region.key}:{track_id}", kind=KIND_CHART,
                                           title=f"#{rank + 1} in {region.name} this week: {label}",
                                           text=f"{count} plays by {listeners} listeners", source="PLAiR plays",
                                           score=value, payload={"track_id": track_id}))
        total = sum(genres.values())
        if total and len(rows) >= settings.PULSE_CHART_MIN_LISTENERS:
            top = sorted(genres.items(), key=lambda kv: kv[1], reverse=True)[:3]
            summary = ", ".join(f"{g} ({v / total:.0%})" for g, v in top)
            value = _text_score(q, 0.65, summary, [g for g, _ in top])
            if value is not None:
                items.append(PulseItem(id=f"chart:{region.key}:genres", kind=KIND_CHART,
                                       title=f"What {region.name} is playing this week", text=summary,
                                       source="PLAiR plays", score=value))
        return items


class DemandLedger:
    def __init__(self):
        self._salt_day: Optional[date] = None
        self._salt = ""

    def _asker_hash(self, asker: str, day: date) -> str:
        if self._salt_day != day:
            self._salt_day, self._salt = day, hashlib.sha256(f"{settings.JWT_SECRET_KEY}:{day}".encode()).hexdigest()
        return hashlib.sha256(f"{self._salt}:{asker}".encode()).hexdigest()[:24]

    def record(self, listener: PulseListener, node: str, query_text: str, live: bool) -> None:
        norm = normalize_query(query_text)[:120]
        if not norm or listener.region is None or not settings.PULSE_DEMAND_ENABLED:
            return
        spawn(self._record(listener.region.key, node, norm, query_text.strip()[:160], listener.asker, live),
              name="pulse_demand")

    async def _record(self, region_key: str, node: str, norm: str, query_text: str, asker: str, live: bool) -> None:
        day = datetime.now(timezone.utc).date()
        now = datetime.now(timezone.utc)
        try:
            async with AsyncSessionLocal() as db:
                stmt = pg_insert(PulseDemand).values(
                    region_key=region_key, node=node, query_norm=norm, query=query_text, day=day, asks=1, askers=0,
                    store_hits=0 if live else 1, live_hits=1 if live else 0, last_asked_at=now)
                stmt = stmt.on_conflict_do_update(constraint="uq_pulse_demand", set_={
                    "asks": PulseDemand.asks + 1,
                    "store_hits": PulseDemand.store_hits + (0 if live else 1),
                    "live_hits": PulseDemand.live_hits + (1 if live else 0),
                    "query": stmt.excluded.query, "last_asked_at": now,
                }).returning(PulseDemand.id)
                demand_id = (await db.execute(stmt)).scalar_one()
                inserted = (await db.execute(
                    pg_insert(PulseDemandAsker).values(demand_id=demand_id, asker_hash=self._asker_hash(asker, day),
                                                       day=day)
                    .on_conflict_do_nothing().returning(PulseDemandAsker.demand_id))).first()
                if inserted:
                    await db.execute(PulseDemand.__table__.update().where(PulseDemand.id == demand_id)
                                     .values(askers=PulseDemand.askers + 1))
                await db.commit()
        except Exception as e:
            log_service.warning(f"[PULSE] demand record failed: {type(e).__name__}: {e}")

    async def hot(self, region_key: str, days: int = 7, min_askers: int = 3, limit: int = 10,
                  node: Optional[str] = None) -> list[tuple]:
        since = datetime.now(timezone.utc).date() - timedelta(days=days)
        query = (select(PulseDemand.node, func.max(PulseDemand.query), func.sum(PulseDemand.askers),
                        func.sum(PulseDemand.asks))
                 .where(PulseDemand.region_key == region_key, PulseDemand.day >= since)
                 .group_by(PulseDemand.node, PulseDemand.query_norm)
                 .having(func.sum(PulseDemand.askers) >= min_askers)
                 .order_by(func.sum(PulseDemand.askers).desc(), func.sum(PulseDemand.asks).desc())
                 .limit(limit))
        if node:
            query = query.where(PulseDemand.node == node)
        try:
            async with AsyncSessionLocal() as db:
                return [(n, q, int(a or 0), int(s or 0)) for n, q, a, s in (await db.execute(query)).all()]
        except Exception as e:
            log_service.warning(f"[PULSE] demand read failed: {type(e).__name__}: {e}")
            return []

    async def prune(self) -> None:
        cutoff = datetime.now(timezone.utc).date() - timedelta(days=2)
        async with AsyncSessionLocal() as db:
            await db.execute(PulseDemandAsker.__table__.delete().where(PulseDemandAsker.day < cutoff))
            await db.execute(PulseDemand.__table__.delete().where(
                PulseDemand.day < datetime.now(timezone.utc).date() - timedelta(days=90)))
            await db.commit()


demand = DemandLedger()
charts = RegionCharts()


class Pulse:
    def __init__(self, nodes: list[KnowledgeNode]):
        self.nodes = nodes
        self._listeners: OrderedDict[str, tuple[float, PulseListener]] = OrderedDict()
        self._offered: OrderedDict[tuple, float] = OrderedDict()
        self._region_keys: OrderedDict[str, tuple[float, Optional[str]]] = OrderedDict()
        self._context_memo: OrderedDict[tuple, tuple[float, asyncio.Future]] = OrderedDict()

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
        if q.text and q.query_vector is None:
            regional = regional_kb.get_regional_knowledge()
            q.query_vector = await regional.embed_text(normalize_query(q.text) or q.text) if regional else None
        nodes = [n for n in self.nodes if n.matches(q) and (q.kinds is not None or n.browsable or q.text)]
        results = await asyncio.gather(*(self._search_node(node, q) for node in nodes))
        found = {node.name: items for node, items in zip(nodes, results)}
        items = [item for group in results for item in group]
        live_node = None
        if q.allow_fetch and q.text and len(items) < settings.PULSE_FETCH_BELOW:
            fetchable = [n for n in nodes if n.can_fetch(q)]
            fetchable.sort(key=lambda n: (len(found.get(n.name, [])), 0 if q.kinds and n.kinds[0] in q.kinds else 1))
            if fetchable:
                live_node = fetchable[0]
                try:
                    fetched = await asyncio.wait_for(live_node.fetch(q), timeout=settings.PULSE_FETCH_TIMEOUT_S)
                except Exception as e:
                    log_service.warning(f"[PULSE] {live_node.name} live fetch failed: {type(e).__name__}: {e}")
                    fetched = []
                known = {item.id for item in items}
                items.extend(item for item in fetched if item.id not in known)
        ranked = self._rank(q, items)
        if q.record_demand and q.text:
            primary = next((n.name for n in nodes if found.get(n.name)), live_node.name if live_node else
                           (nodes[0].name if len(nodes) == 1 else "any"))
            demand.record(q.listener, primary if not live_node else live_node.name, q.text, live_node is not None)
        log_service.detail(
            f"[PULSE] {log_service.who(q.listener.session_id)} '{q.text or '*'}' kinds={sorted(q.kinds or [])} -> "
            f"{len(ranked)} items ({', '.join(f'{k}:{len(v)}' for k, v in found.items() if v) or 'none'}"
            f"{', live ' + live_node.name if live_node else ''}) {(time.perf_counter() - started) * 1000:.0f} ms",
            "pulse")
        return ranked

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
        unique.sort(key=lambda item: item.score, reverse=True)
        if q.kinds is not None and len(q.kinds) == 1:
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
        if kind == KIND_EVENT or kind == KIND_PLACE:
            regional = regional_kb.get_regional_knowledge()
            if kind == KIND_PLACE:
                place = await place_memory.get_place(key)
                if place:
                    return {"id": item_id, "kind": kind, **{k: v for k, v in place.items() if v not in (None, "")}}
            if regional is None or listener.region is None:
                return None
            pool = await regional.store.items(listener.region.key, (kind,))
            match = next((i for i in pool if i.item_id == key or i.item_id.startswith(key)), None)
            if match is None:
                return None
            related = [other.title for other in pool if other is not match and match.text and other.text and
                       other.text.split(",")[0] == match.text.split(",")[0]][:4]
            entry = {"id": item_id, "kind": kind, "title": match.title, "details": match.text, "tags": match.tags,
                     "url": match.url, "source": match.attribution}
            if match.starts_at:
                entry["when"] = _local_when(match.starts_at, listener.tz_name)
            if related:
                entry["also_at_this_venue"] = related
            return entry
        if kind == KIND_NEWS and services.news_service is not None and key.isdigit():
            items = await services.news_service.store.items([int(key)])
            item = items.get(int(key))
            if item is None:
                return None
            article = item.as_article()
            return {"id": item_id, "kind": kind, "title": article.get("title"),
                    "details": article.get("description") or "", "source": (article.get("source") or {}).get("name"),
                    "published": article.get("publishedAt"), "tags": getattr(item, "tags", None)}
        if kind == KIND_ARTIST:
            found = await self.node("artists").search(PulseQuery(listener=listener, text=key, kinds={KIND_ARTIST}))
            if found:
                return {"id": item_id, "kind": kind, "title": found[0].title,
                        "details": services.web_service.cached_artist_biography(key) or found[0].text}
        return None

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
    return [EventsNode(), PlacesNode(), NewsNode(), WeatherNode(), AreaNode(), ArtistNode(), CommunityNode(),
            ChartsNode(), TrendsNode()]
