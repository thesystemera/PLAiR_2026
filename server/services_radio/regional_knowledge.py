import asyncio
import json
import math
import re
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Awaitable, Callable, Iterable, Optional

import numpy as np
import pytz
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import settings
from database.models import RegionalItem, RegionalRefresh, RegionalRegion
from services import log_service
from services import usage_tracking
from services.task_utils import spawn
from services_radio.external_news_service import resolve_country

KIND_EVENT = "event"
KIND_PLACE = "place"
KIND_NEWS = "news"

EVENTS_COLLECTOR_ENABLED = settings.REGIONAL_EVENTS_ENABLED
EVENTS_REFRESH_S = settings.REGIONAL_EVENTS_REFRESH_S
EVENTS_DAYS_AHEAD = settings.REGIONAL_EVENTS_DAYS_AHEAD
EVENTS_PAGES = settings.REGIONAL_EVENTS_PAGES
PLACES_COLLECTOR_ENABLED = settings.REGIONAL_PLACES_ENABLED
PLACES_REFRESH_S = settings.REGIONAL_PLACES_REFRESH_S
PLACES_CATEGORIES = settings.REGIONAL_PLACES_CATEGORIES
PLACES_PER_CATEGORY = settings.REGIONAL_PLACES_PER_CATEGORY
PLACES_SEARCH_RADIUS_M = settings.REGIONAL_PLACES_RADIUS_M
PLACE_ID_RETENTION_DAYS = settings.REGIONAL_PLACE_ID_RETENTION_DAYS
PLACE_HYDRATIONS_PER_HOUR = settings.REGIONAL_PLACE_LOOKUPS_PER_HOUR
MAX_ITEMS_PER_REGION_KIND = settings.REGIONAL_MAX_ITEMS_PER_KIND
READ_CACHE_S = settings.REGIONAL_READ_CACHE_S
TZ_CITY_MATCH_KM = settings.REGIONAL_CITY_MATCH_KM
ACTIVE_GUEST_MAX_AGE_S = settings.REGIONAL_ACTIVE_GUEST_MAX_AGE_S

_IGNORED_TAGS = {"", "undefined", "other", "miscellaneous", "n/a"}
_TOKEN = re.compile(r"[a-z0-9]+")
_TAG_ALIASES = {
    "hiphop": "hip hop", "rap": "hip hop", "r&b": "rnb", "edm": "electronic", "dance": "electronic",
    "house": "electronic", "techno": "electronic", "trance": "electronic", "drum": "electronic",
    "punk": "rock", "grunge": "rock", "shoegaze": "rock", "alternative": "rock", "indie": "rock",
    "metal": "metal", "folk": "folk", "country": "country", "jazz": "jazz", "blues": "blues",
    "soul": "rnb", "funk": "rnb", "disco": "electronic", "synthwave": "electronic", "ambient": "electronic",
    "classical": "classical", "opera": "classical", "reggae": "reggae", "latin": "latin", "pop": "pop",
}
_SEGMENT_FACTORS = {"music": 1.0, "arts & theatre": 0.6, "film": 0.4, "sports": 0.3}


@dataclass(frozen=True)
class Region:
    key: str
    name: str
    country: Optional[str] = None
    center: Optional[tuple] = field(default=None, compare=False, repr=False)


@dataclass
class KnowledgeItem:
    source: str
    kind: str
    region_key: str
    external_id: str
    title: str
    expires_at: datetime
    text: str = ""
    tags: list = field(default_factory=list)
    starts_at: Optional[datetime] = None
    url: str = ""
    attribution: str = ""
    cost_usd: float = 0.0
    vector: Optional[np.ndarray] = field(default=None, compare=False, repr=False)

    @property
    def item_id(self) -> str:
        return f"{self.source}:{self.external_id}"

    @property
    def embed_text(self) -> str:
        return " ".join(part for part in (self.title, self.text, " ".join(self.tags)) if part)


@dataclass
class Taste:
    genres: dict = field(default_factory=dict)
    artists: set = field(default_factory=set)
    interests: set = field(default_factory=set)

    @property
    def empty(self) -> bool:
        return not (self.genres or self.artists or self.interests)


class Collector:
    name = "collector"
    kind = ""
    refresh_s = 86400
    enabled = True

    def available(self) -> bool:
        return self.enabled

    async def fetch(self, region: Region) -> list[KnowledgeItem]:
        raise NotImplementedError

    async def hydrate(self, item: KnowledgeItem) -> Optional[KnowledgeItem]:
        return item


def _haversine_km(a: tuple, b: tuple) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def _iso6709(value: str) -> Optional[tuple]:
    match = re.match(r"^([+-]\d{4,6})([+-]\d{5,7})$", value)
    if not match:
        return None

    def degrees(part: str, width: int) -> float:
        sign = -1.0 if part[0] == "-" else 1.0
        digits = part[1:]
        whole, minutes, seconds = digits[:width], digits[width:width + 2], digits[width + 2:width + 4] or "0"
        return sign * (int(whole) + int(minutes) / 60.0 + int(seconds) / 3600.0)

    return degrees(match.group(1), 2), degrees(match.group(2), 3)


@lru_cache(maxsize=1)
def timezone_cities() -> dict:
    cities = {}
    try:
        with pytz.open_resource("zone.tab") as handle:
            for line in handle.read().decode("utf-8").splitlines():
                if line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 3:
                    coords = _iso6709(parts[1])
                    if coords:
                        cities[parts[2]] = (coords, parts[0])
    except OSError as e:
        log_service.warning(f"[REGIONAL] zone.tab unavailable: {e}")
    return cities


def _tz_region(tz_name: Optional[str]) -> Optional[Region]:
    entry = timezone_cities().get(tz_name or "")
    if not entry:
        return None
    coords, country = entry
    return Region(key=f"tz:{tz_name}", name=tz_name.rsplit("/", 1)[-1].replace("_", " "), country=country,
                  center=coords)


def _locality(location: Optional[str]) -> Optional[str]:
    parts = [p.strip() for p in (location or "").split(",") if p.strip() and not any(ch.isdigit() for ch in p)]
    if not parts:
        return None
    return parts[-3] if len(parts) >= 3 else parts[0]


def resolve_region(user=None, tz_name: Optional[str] = None, location=None) -> Optional[Region]:
    if location is not None:
        tz_name = location.timezone or tz_name
        coords = location.coords
        locality = location.city or None
        country = location.country_code or None
    else:
        tz_name = (getattr(user, "timezone", None) if user is not None else None) or tz_name
        try:
            coords = (float(user.latitude), float(user.longitude)) if user is not None and user.latitude and user.longitude else None
        except (TypeError, ValueError):
            coords = None
        locality = _locality(getattr(user, "location", None))
        country = resolve_country(getattr(user, "location", None))
    tz_region = _tz_region(tz_name)
    if coords is None:
        return tz_region
    if tz_region and _haversine_km(coords, tz_region.center) <= TZ_CITY_MATCH_KM:
        return tz_region
    if not locality:
        return tz_region
    return Region(key=f"loc:{(country or 'xx').lower()}:{locality.lower()}", name=locality, country=country,
                  center=(round(coords[0], 1), round(coords[1], 1)))


@lru_cache(maxsize=8192)
def _tag_tokens(tag: str) -> frozenset:
    tokens = set()
    for token in _TOKEN.findall(tag.lower().replace("hip-hop", "hiphop")):
        tokens.add(token)
        if token in _TAG_ALIASES:
            tokens.add(_TAG_ALIASES[token])
    return frozenset(tokens)


def lexical_similarity(a: str, b: str) -> float:
    ta, tb = _tag_tokens(a), _tag_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _unit(blob: Optional[bytes]) -> Optional[np.ndarray]:
    if not blob:
        return None
    vector = np.frombuffer(blob, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else None


def _days_ahead(item: KnowledgeItem, now: datetime) -> Optional[float]:
    if not item.starts_at:
        return None
    return (item.starts_at - now).total_seconds() / 86400.0


class TicketmasterEventsCollector(Collector):
    name = "ticketmaster_events"
    kind = KIND_EVENT
    refresh_s = EVENTS_REFRESH_S
    enabled = EVENTS_COLLECTOR_ENABLED

    def __init__(self, events_service):
        self.events_service = events_service

    def available(self) -> bool:
        return self.enabled and self.events_service is not None

    @staticmethod
    def to_item(region: Region, event: dict, now: datetime) -> Optional[KnowledgeItem]:
        event_id = event.get("id")
        if not event_id or not event.get("name"):
            return None
        starts_at = None
        for candidate in (event.get("starts_at"), f"{event.get('date')}T{event.get('time') or '12:00:00'}"):
            try:
                parsed = datetime.fromisoformat(str(candidate).replace("Z", "+00:00"))
                starts_at = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                break
            except (TypeError, ValueError):
                continue
        tags = [t for t in (event.get("segment"), event.get("genre"), event.get("subgenre"))
                if t and t.strip().lower() not in _IGNORED_TAGS]
        details = ", ".join(part for part in (event.get("venue"), event.get("date"), event.get("time", "")[:5]) if part)
        return KnowledgeItem(
            source="ticketmaster", kind=KIND_EVENT, region_key=region.key, external_id=str(event_id),
            title=str(event["name"])[:200], text=details[:200], tags=tags, starts_at=starts_at,
            expires_at=(starts_at or now) + timedelta(hours=6), url=event.get("url") or "",
            attribution="Ticketmaster",
        )

    async def fetch(self, region: Region) -> list[KnowledgeItem]:
        if not region.center:
            return []
        now = datetime.now(timezone.utc)
        raw = await self.events_service.fetch_event_pages(
            f"{region.center[0]:.2f},{region.center[1]:.2f}", region.country, now,
            now + timedelta(days=EVENTS_DAYS_AHEAD), EVENTS_PAGES)
        return [item for item in (self.to_item(region, event, now) for event in raw) if item]


class GoogleNewsCollector(Collector):
    name = "google_news"
    kind = KIND_NEWS
    refresh_s = settings.NEWS_REGION_REFRESH_S
    enabled = settings.NEWS_REGION_PREFETCH_ENABLED

    def __init__(self, news_service):
        self.news_service = news_service

    def available(self) -> bool:
        return self.enabled and self.news_service is not None and self.news_service.store_enabled

    async def fetch(self, region: Region) -> list[KnowledgeItem]:
        articles = await self.news_service.refresh_region(region.country, region.name, region.key)
        now = datetime.now(timezone.utc)
        items = []
        for article in articles:
            try:
                published = datetime.fromisoformat(article["publishedAt"].replace("Z", "+00:00"))
            except (KeyError, AttributeError, ValueError):
                published = None
            expires = max((published or now) + timedelta(seconds=settings.NEWS_RETENTION_S // 2),
                          now + timedelta(seconds=self.refresh_s * 2))
            items.append(KnowledgeItem(
                source="google_news", kind=KIND_NEWS, region_key=region.key, external_id=str(article["id"]),
                title=article["title"][:200], text=(article.get("source") or {}).get("name", "")[:200],
                tags=["local news"], starts_at=None, expires_at=expires, url=article.get("url") or "",
                attribution="Google News"))
        return items


class GooglePlacesCollector(Collector):
    name = "google_places"
    kind = KIND_PLACE
    refresh_s = PLACES_REFRESH_S
    enabled = PLACES_COLLECTOR_ENABLED

    def __init__(self, location_service):
        self.location_service = location_service
        self._hydrations: list[float] = []

    def available(self) -> bool:
        return self.enabled and self.location_service is not None and self.location_service.available()

    async def fetch(self, region: Region) -> list[KnowledgeItem]:
        if not region.center:
            return []
        expires = datetime.now(timezone.utc) + timedelta(days=PLACE_ID_RETENTION_DAYS)
        items = []
        for category in PLACES_CATEGORIES:
            for place_id in await self.location_service.search_place_ids(
                    category, region.center, PLACES_SEARCH_RADIUS_M, PLACES_PER_CATEGORY):
                items.append(KnowledgeItem(source="google_places", kind=KIND_PLACE, region_key=region.key,
                                           external_id=f"{place_id}|{category}", title="", tags=[category],
                                           expires_at=expires, attribution="Google Maps"))
            if not self.location_service.available():
                break
        return items

    async def hydrate(self, item: KnowledgeItem) -> Optional[KnowledgeItem]:
        if item.title:
            return item
        from services_radio import place_memory
        remembered = await place_memory.get_place(item.external_id.split("|", 1)[0])
        if remembered:
            return replace(item, title=remembered["name"], text=remembered.get("type") or "")
        now = time.monotonic()
        self._hydrations = [t for t in self._hydrations if now - t < 3600]
        if len(self._hydrations) >= PLACE_HYDRATIONS_PER_HOUR or not self.available():
            return None
        self._hydrations.append(now)
        summary = await self.location_service.place_summary(item.external_id.split("|", 1)[0])
        if not summary:
            return None
        return replace(item, title=summary["name"], text=summary.get("type") or "")


class RegionalKnowledgeStore:
    def __init__(self, async_session_maker):
        self.async_session_maker = async_session_maker
        self._read_cache: dict = {}
        self._read_locks: dict[tuple, asyncio.Lock] = {}
        self._generations: dict[str, int] = {}

    async def touch_region(self, region: Region) -> None:
        stmt = pg_insert(RegionalRegion).values(key=region.key, name=region.name, country=region.country,
                                                last_active_at=datetime.now(timezone.utc))
        stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={
            "name": stmt.excluded.name, "country": stmt.excluded.country,
            "last_active_at": stmt.excluded.last_active_at})
        async with self.async_session_maker() as db:
            await db.execute(stmt)
            await db.commit()

    async def last_refreshes(self, region_key: str) -> dict:
        async with self.async_session_maker() as db:
            rows = (await db.execute(
                select(RegionalRefresh.collector, RegionalRefresh.refreshed_at)
                .where(RegionalRefresh.region_key == region_key))).all()
        return {collector: refreshed_at for collector, refreshed_at in rows}

    async def save(self, region: Region, collector: str, kind: str, items: list[KnowledgeItem], status: str) -> None:
        now = datetime.now(timezone.utc)
        async with self.async_session_maker() as db:
            if items:
                rows = [{
                    "region_key": item.region_key, "source": item.source, "kind": item.kind,
                    "external_id": item.external_id, "title": item.title, "text": item.text,
                    "tags": json.dumps(item.tags), "starts_at": item.starts_at, "expires_at": item.expires_at,
                    "url": item.url, "attribution": item.attribution, "fetched_at": now,
                } for item in items]
                stmt = pg_insert(RegionalItem).values(rows)
                stmt = stmt.on_conflict_do_update(constraint="uq_regional_items_source_id", set_={
                    column: stmt.excluded[column] for column in
                    ("kind", "title", "text", "tags", "starts_at", "expires_at", "url", "attribution", "fetched_at")})
                await db.execute(stmt)
            await db.execute(delete(RegionalItem).where(RegionalItem.expires_at < now))
            overflow = (
                select(RegionalItem.id)
                .where(RegionalItem.region_key == region.key, RegionalItem.kind == kind)
                .order_by(RegionalItem.starts_at.asc().nulls_last(), RegionalItem.fetched_at.desc())
                .offset(MAX_ITEMS_PER_REGION_KIND)
            )
            await db.execute(delete(RegionalItem).where(RegionalItem.id.in_(overflow.scalar_subquery())))
            if collector:
                refresh = pg_insert(RegionalRefresh).values(region_key=region.key, collector=collector,
                                                            refreshed_at=now, status=status, item_count=len(items))
                refresh = refresh.on_conflict_do_update(index_elements=["region_key", "collector"], set_={
                    "refreshed_at": refresh.excluded.refreshed_at, "status": refresh.excluded.status,
                    "item_count": refresh.excluded.item_count})
                await db.execute(refresh)
            await db.commit()
        self._generations[region.key] = self._generations.get(region.key, 0) + 1
        self._read_cache = {k: v for k, v in self._read_cache.items() if k[0] != region.key}

    def _cached_items(self, cache_key: tuple) -> Optional[list[KnowledgeItem]]:
        cached = self._read_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < READ_CACHE_S:
            return cached[1]
        return None

    async def items(self, region_key: str, kinds: Iterable[str]) -> list[KnowledgeItem]:
        kinds = tuple(sorted(kinds))
        cache_key = (region_key, kinds)
        cached = self._cached_items(cache_key)
        if cached is not None:
            return cached
        async with self._read_locks.setdefault(cache_key, asyncio.Lock()):
            cached = self._cached_items(cache_key)
            if cached is not None:
                return cached
            return await self._load_items(cache_key, region_key, kinds)

    async def _load_items(self, cache_key: tuple, region_key: str, kinds: tuple) -> list[KnowledgeItem]:
        generation = self._generations.get(region_key, 0)
        now = datetime.now(timezone.utc)
        async with self.async_session_maker() as db:
            rows = (await db.execute(
                select(RegionalItem)
                .where(RegionalItem.region_key == region_key, RegionalItem.kind.in_(kinds),
                       RegionalItem.expires_at > now)
                .order_by(RegionalItem.starts_at.asc().nulls_last())
                .limit(MAX_ITEMS_PER_REGION_KIND * max(1, len(kinds)))
            )).scalars().all()
        items = [KnowledgeItem(
            source=row.source, kind=row.kind, region_key=row.region_key, external_id=row.external_id,
            title=row.title or "", text=row.text or "", tags=json.loads(row.tags or "[]"), starts_at=row.starts_at,
            expires_at=row.expires_at, url=row.url or "", attribution=row.attribution or "",
            vector=_unit(row.embedding),
        ) for row in rows]
        if self._generations.get(region_key, 0) == generation:
            now_s = time.monotonic()
            self._read_cache = {k: v for k, v in self._read_cache.items() if now_s - v[0] < READ_CACHE_S}
            self._read_cache[cache_key] = (now_s, items)
        return items

    async def missing_embeddings(self, region_key: Optional[str], limit: int = 200) -> list[tuple]:
        query = select(RegionalItem.id, RegionalItem.region_key, RegionalItem.title, RegionalItem.text,
                       RegionalItem.tags).where(RegionalItem.embedding.is_(None), RegionalItem.title != "")
        if region_key:
            query = query.where(RegionalItem.region_key == region_key)
        async with self.async_session_maker() as db:
            rows = (await db.execute(query.limit(limit))).all()
        return [(row.id, row.region_key, " ".join(part for part in (
            row.title, row.text, " ".join(json.loads(row.tags or "[]"))) if part)) for row in rows]

    async def set_embeddings(self, vectors: dict, region_keys: Iterable[str]) -> None:
        if not vectors:
            return
        async with self.async_session_maker() as db:
            for item_id, vector in vectors.items():
                await db.execute(RegionalItem.__table__.update().where(RegionalItem.id == item_id).values(
                    embedding=np.asarray(vector, dtype=np.float32).tobytes()))
            await db.commit()
        keys = set(region_keys)
        for key in keys:
            self._generations[key] = self._generations.get(key, 0) + 1
        self._read_cache = {k: v for k, v in self._read_cache.items() if k[0] not in keys}

    async def region_count(self) -> int:
        async with self.async_session_maker() as db:
            return (await db.execute(select(func.count()).select_from(RegionalRegion))).scalar() or 0


class RegionalKnowledgeService:
    def __init__(self, store: RegionalKnowledgeStore, collectors: list[Collector],
                 embedder: Optional[Callable[[str], Awaitable[np.ndarray]]] = None):
        self.store = store
        self.collectors = {collector.name: collector for collector in collectors}
        self.embedder = embedder
        self._vectors: dict[str, np.ndarray] = {}
        self._warming: set[str] = set()
        self._embedding_regions: set[str] = set()

    def collector_for(self, item: KnowledgeItem) -> Optional[Collector]:
        return next((c for c in self.collectors.values() if c.kind == item.kind and item.source in c.name), None)

    async def refresh(self, region: Region, force: bool = False) -> dict:
        results = {}
        await self.store.touch_region(region)
        last = await self.store.last_refreshes(region.key)
        now = datetime.now(timezone.utc)
        for collector in self.collectors.values():
            refreshed_at = last.get(collector.name)
            due = force or refreshed_at is None or (now - refreshed_at).total_seconds() >= collector.refresh_s
            if not due or not collector.available():
                continue
            try:
                items = await collector.fetch(region)
                status = "ok"
            except Exception as e:
                log_service.error(f"[REGIONAL] {collector.name} failed for {region.name}: {type(e).__name__}: {e}")
                items, status = [], "error"
            if status == "ok" and not items and not collector.available():
                continue
            await self.store.save(region, collector.name, collector.kind, items, status)
            self.warm(tag for item in items for tag in item.tags)
            self.embed_items(region.key)
            results[collector.name] = len(items)
            log_service.external(f"[REGIONAL] {collector.name}: {len(items)} items for {region.name}")
        return results

    async def ingest(self, region: Optional[Region], items: list[KnowledgeItem]) -> None:
        if region is None or not items:
            return
        try:
            await self.store.touch_region(region)
            await self.store.save(region, "", items[0].kind, items, "ingested")
            self.embed_items(region.key)
        except Exception as e:
            log_service.warning(f"[REGIONAL] ingest failed for {region.name}: {type(e).__name__}: {e}")

    def embed_items(self, region_key: Optional[str] = None) -> None:
        marker = region_key or "*"
        if self.embedder is None or marker in self._embedding_regions:
            return
        self._embedding_regions.add(marker)
        spawn(self._embed_items(region_key, marker), name="regional_embed_items")

    async def _embed_items(self, region_key: Optional[str], marker: str) -> None:
        try:
            while True:
                pending = await self.store.missing_embeddings(region_key)
                if not pending:
                    return
                vectors = {}
                for item_id, _, text in pending:
                    vector = await self.embedder(text)
                    if vector is not None:
                        vectors[item_id] = vector
                if not vectors:
                    return
                await self.store.set_embeddings(vectors, {key for _, key, _ in pending})
                log_service.detail(f"[REGIONAL] embedded {len(vectors)} items", "pulse")
                if len(pending) < 200:
                    return
        except Exception as e:
            log_service.warning(f"[REGIONAL] item embedding failed: {type(e).__name__}: {e}")
        finally:
            self._embedding_regions.discard(marker)

    async def embed_text(self, text: str) -> Optional[np.ndarray]:
        if self.embedder is None or not text:
            return None
        key = text.lower()
        if key in self._vectors:
            return self._vectors[key]
        try:
            vector = np.asarray(await self.embedder(text), dtype=np.float32)
        except Exception as e:
            log_service.warning(f"[REGIONAL] embedding failed: {type(e).__name__}: {e}")
            return None
        norm = float(np.linalg.norm(vector))
        if norm <= 0:
            return None
        self._vectors[key] = vector / norm
        return self._vectors[key]

    def warm(self, labels: Iterable[str]) -> None:
        if self.embedder is None:
            return
        missing = {label.lower() for label in labels if label and label.lower() not in self._vectors} - self._warming
        if missing:
            self._warming |= missing
            spawn(self._warm(sorted(missing)), name="regional_embed_warm")

    async def _warm(self, labels: list[str]) -> None:
        try:
            for label in labels:
                vector = await self.embedder(label)
                if vector is not None and np.linalg.norm(vector) > 0:
                    self._vectors[label] = vector / np.linalg.norm(vector)
        except Exception as e:
            log_service.warning(f"[REGIONAL] embedding warm-up failed: {type(e).__name__}: {e}")
        finally:
            self._warming -= set(labels)

    def similarity(self, a: str, b: str) -> float:
        score = lexical_similarity(a, b)
        va, vb = self._vectors.get(a.lower()), self._vectors.get(b.lower())
        if va is not None and vb is not None:
            score = max(score, float(np.dot(va, vb)))
        return score

    def score(self, item: KnowledgeItem, taste: Optional[Taste], now: datetime, window_days: float) -> float:
        days = _days_ahead(item, now)
        proximity = 0.5 if days is None else max(0.0, 1.0 - max(days, 0.0) / max(window_days, 1.0))
        tags = [t for t in item.tags if t.lower() not in _IGNORED_TAGS]
        segment = next((_SEGMENT_FACTORS[t.lower()] for t in tags if t.lower() in _SEGMENT_FACTORS), 0.5)
        if item.kind == KIND_PLACE:
            if taste is None or not taste.interests:
                return 0.3
            return 0.3 + 0.7 * max((lexical_similarity(tag, interest) for tag in tags for interest in taste.interests),
                                   default=0.0)
        if taste is None or taste.empty:
            return 0.5 * proximity * segment
        top = max(taste.genres.values(), default=0.0) or 1.0
        genre = max((weight / top * self.similarity(tag, name)
                     for name, weight in taste.genres.items() if weight > 0 for tag in tags), default=0.0)
        title = item.title.lower()
        artist = 1.0 if any(len(a) >= 4 and a.lower() in title for a in taste.artists) else 0.0
        return (0.55 * min(genre, 1.0) + 0.25 * artist + 0.2 * proximity) * segment

    async def query(self, region: Optional[Region], kinds: Iterable[str], taste: Optional[Taste] = None,
                    window: Optional[tuple] = None, exclude: Iterable[str] = (), limit: int = 5,
                    text_query: Optional[str] = None, min_score: float = 0.0,
                    record_hit: bool = False) -> list[tuple[float, KnowledgeItem]]:
        if region is None:
            return []
        try:
            items = await self.store.items(region.key, kinds)
        except Exception as e:
            log_service.warning(f"[REGIONAL] query failed for {region.name}: {type(e).__name__}: {e}")
            return []
        now = datetime.now(timezone.utc)
        start, end = window or (now, now + timedelta(days=14))
        window_days = max((end - now).total_seconds() / 86400.0, 1.0)
        excluded = set(exclude)
        needle = (text_query or "").strip().lower()
        if taste is not None:
            self.warm(taste.genres)
        scored = []
        for item in items:
            if item.item_id in excluded:
                continue
            if item.kind == KIND_EVENT and item.starts_at and not (start <= item.starts_at <= end):
                continue
            if needle and needle not in item.embed_text.lower() and not any(
                    lexical_similarity(needle, tag) > 0 for tag in item.tags):
                continue
            value = self.score(item, taste, now, window_days)
            if value >= min_score:
                scored.append((value, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        if scored and record_hit:
            usage_tracking.record_api_call("events", "regional_pool", cached=True)
        return scored[:limit]

    async def hydrate(self, item: KnowledgeItem) -> Optional[KnowledgeItem]:
        collector = self.collector_for(item)
        if collector is None:
            return item
        try:
            return await collector.hydrate(item)
        except Exception as e:
            log_service.warning(f"[REGIONAL] hydrate failed for {item.item_id}: {type(e).__name__}: {e}")
            return None


regional_knowledge: Optional[RegionalKnowledgeService] = None


def set_regional_knowledge(service: Optional[RegionalKnowledgeService]) -> None:
    global regional_knowledge
    regional_knowledge = service


def get_regional_knowledge() -> Optional[RegionalKnowledgeService]:
    return regional_knowledge
