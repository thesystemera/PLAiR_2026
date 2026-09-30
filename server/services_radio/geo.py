import asyncio
import json
import math
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import settings
from database.connection import AsyncSessionLocal
from database.models import GeoPlace
from services import log_service, usage_tracking
from services.http_client import fetch

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
SCOPES = ("spot", "street", "neighbourhood", "city", "region", "country")
LOCAL_SCOPES = frozenset(SCOPES[:4])
FINE_SCOPES = frozenset(SCOPES[:3])
SCOPE_TYPES = (
    ("country", ("country",)),
    ("region", ("administrative_area_level_1", "administrative_area_level_2", "continent", "archipelago",
                "natural_feature")),
    ("city", ("locality", "postal_town", "administrative_area_level_3")),
    ("neighbourhood", ("neighborhood", "sublocality", "sublocality_level_1", "colloquial_area", "postal_code",
                       "park", "airport")),
    ("street", ("route", "intersection")),
)
EARTH_RADIUS_M = 6371000.0
MEMORY_MAX = 5000


@dataclass(frozen=True)
class Where:
    label: str
    lat: float
    lon: float
    radius_m: float = 0.0
    scope: str = "spot"

    @property
    def local(self) -> bool:
        return self.scope in LOCAL_SCOPES

    @property
    def fine(self) -> bool:
        return self.scope in FINE_SCOPES

    def as_dict(self) -> dict:
        return {"label": self.label, "lat": round(self.lat, 6), "lon": round(self.lon, 6),
                "radius_m": int(round(self.radius_m)), "scope": self.scope}

    def public(self) -> dict:
        return {"label": self.label, "scope": self.scope}

    @staticmethod
    def from_dict(data: Any) -> Optional["Where"]:
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                return None
        if not isinstance(data, dict):
            return None
        return from_row(data.get("label"), data.get("lat"), data.get("lon"), data.get("radius_m"), data.get("scope"))


def from_row(label: Optional[str], lat, lon, radius_m=None, scope: Optional[str] = None) -> Optional[Where]:
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    try:
        radius = max(0.0, float(radius_m or 0.0))
    except (TypeError, ValueError):
        radius = 0.0
    return Where(str(label or "").strip(), lat, lon, radius, scope if scope in SCOPES else "spot")


def distance_m(a: Where, b: Where) -> float:
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def gap_m(a: Where, b: Where) -> float:
    return max(0.0, distance_m(a, b) - a.radius_m - b.radius_m)


def contains(area: Where, point: Where) -> bool:
    return area.radius_m > 0 and distance_m(area, point) <= area.radius_m


def near(listener: Optional[Where], item: Optional[Where], radius_m: float) -> bool:
    if listener is None or item is None or not item.local:
        return False
    return gap_m(listener, item) <= radius_m


def overlap(a: Optional[Where], b: Optional[Where], slack_m: float) -> Optional[float]:
    if a is None or b is None or not a.fine or not b.fine:
        return None
    gap = gap_m(a, b)
    return gap if gap <= slack_m else None


def span(metres: float) -> str:
    if metres < 1000:
        return f"{max(50, int(round(metres / 50.0)) * 50)} m"
    km = metres / 1000.0
    return f"{km:.1f} km" if km < 10 else f"{int(round(km)):,} km"


def relation(listener: Optional[Where], item: Optional[Where]) -> str:
    if listener is None or item is None:
        return ""
    if contains(item, listener):
        return f"the listener is in {item.label}" if item.label else "where the listener is"
    gap = gap_m(listener, item)
    if gap == 0:
        return f"in the listener's area ({listener.label})" if listener.label else "in the listener's area"
    return f"{span(gap)} from the listener"


def scope_of(types: Iterable[str]) -> str:
    types = set(types or ())
    for scope, names in SCOPE_TYPES:
        if types & set(names):
            return scope
    return "spot"


def _corner_radius(lat: float, lon: float, box: dict) -> float:
    centre = Where("", lat, lon)
    radius = 0.0
    for corner in (box.get("northeast"), box.get("southwest")):
        if isinstance(corner, dict) and corner.get("lat") is not None and corner.get("lng") is not None:
            radius = max(radius, distance_m(centre, Where("", float(corner["lat"]), float(corner["lng"]))))
    return radius


def parse_forward(data: dict, label: str) -> Optional[Where]:
    results = data.get("results") or []
    if not results:
        return None
    result = results[0]
    geometry = result.get("geometry") or {}
    location = geometry.get("location") or {}
    if location.get("lat") is None or location.get("lng") is None:
        return None
    lat, lon = float(location["lat"]), float(location["lng"])
    box = geometry.get("bounds") or geometry.get("viewport") or {}
    scope = scope_of(result.get("types"))
    return Where(label, lat, lon, _corner_radius(lat, lon, box), scope)


def normalize_phrase(phrase: Optional[str]) -> str:
    return " ".join(re.sub(r"[^\w\s,'-]", " ", (phrase or "").lower()).split())[:200]


class GeoResolver:
    def __init__(self):
        self._memory: OrderedDict[str, tuple[float, Optional[Where]]] = OrderedDict()
        self._inflight: Dict[str, asyncio.Future] = {}

    @staticmethod
    def key(phrase: str, country_code: Optional[str]) -> str:
        return f"{normalize_phrase(phrase)}|{(country_code or '').lower()}"

    @staticmethod
    def available() -> bool:
        return bool(settings.GOOGLE_PLACES_API_KEY) and settings.GEO_ENABLED

    def _remember(self, key: str, where: Optional[Where]) -> None:
        self._memory[key] = (time.monotonic(), where)
        self._memory.move_to_end(key)
        while len(self._memory) > MEMORY_MAX:
            self._memory.popitem(last=False)

    def cached(self, phrase: Optional[str], country_code: Optional[str] = None) -> Optional[Where]:
        hit = self._memory.get(self.key(phrase or "", country_code))
        return hit[1] if hit else None

    async def resolve(self, phrase: Optional[str], country_code: Optional[str] = None) -> Optional[Where]:
        label = " ".join((phrase or "").split())[:160]
        if not normalize_phrase(label) or not self.available():
            return None
        key = self.key(label, country_code)
        hit = self._memory.get(key)
        if hit is not None:
            return hit[1]
        pending = self._inflight.get(key)
        if pending is not None:
            return await asyncio.shield(pending)
        future = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            where = await self._resolve(key, label, country_code)
            future.set_result(where)
            return where
        except Exception as e:
            log_service.throttled(f"geo:{type(e).__name__}", f"[GEO] geocode failed for '{label}': "
                                                               f"{type(e).__name__}: {e}")
            future.set_result(None)
            return None
        finally:
            self._inflight.pop(key, None)

    async def _resolve(self, key: str, label: str, country_code: Optional[str]) -> Optional[Where]:
        async with AsyncSessionLocal() as db:
            row = await db.get(GeoPlace, key)
        retry_before = datetime.now(timezone.utc) - timedelta(days=settings.GEO_MISS_RETRY_DAYS)
        if row is not None and (row.latitude is not None or row.resolved_at >= retry_before):
            where = from_row(row.label, row.latitude, row.longitude, row.radius_m, row.scope)
            self._remember(key, where)
            return where
        params = {"address": label, "language": "en", "key": settings.GOOGLE_PLACES_API_KEY}
        if country_code:
            params["region"] = country_code.lower()
        response = await fetch("GET", GEOCODE_URL, circuit=True, params=params)
        data = response.json()
        status = data.get("status")
        if status not in ("OK", "ZERO_RESULTS"):
            usage_tracking.record_api_call("geocoding", "google", error=True)
            raise RuntimeError(str(status))
        usage_tracking.record_api_call("geocoding", "google")
        where = parse_forward(data, label) if status == "OK" else None
        values = {"key": key, "phrase": label, "label": where.label if where else None,
                  "latitude": where.lat if where else None, "longitude": where.lon if where else None,
                  "radius_m": where.radius_m if where else None, "scope": where.scope if where else None,
                  "resolved_at": datetime.now(timezone.utc)}
        async with AsyncSessionLocal() as db:
            await db.execute(pg_insert(GeoPlace).values(**values).on_conflict_do_update(
                index_elements=[GeoPlace.key], set_={k: v for k, v in values.items() if k != "key"}))
            await db.commit()
        self._remember(key, where)
        log_service.detail(f"[GEO] '{label}' -> " + (f"{where.scope} r={int(where.radius_m)} m" if where else "no match"),
                           "pulse")
        return where

    async def resolve_many(self, phrases: Iterable[Optional[str]], country_code: Optional[str] = None) -> list:
        return list(await asyncio.gather(*(self.resolve(p, country_code) for p in phrases)))


resolver = GeoResolver()


async def listener_where(location) -> Optional[Where]:
    if location is None:
        return None
    label = location.description or location.city or location.place
    if location.has_coordinates:
        return Where(label or "", float(location.latitude), float(location.longitude),
                     float(location.accuracy_m or 0.0), "spot")
    phrase = location.place or location.city
    return await resolver.resolve(phrase, location.country_code or None) if phrase else None

