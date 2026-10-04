import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import settings
from database import AsyncSessionLocal
from database.models import PlaceCache, PlaceSearch
from services import log_service
from services import usage_tracking
from models_global import run_on_gpu_executor

_WORD = re.compile(r"[a-z0-9]+")
SEARCH_ORIGIN_DECIMALS = 3
_STOP = {
    "good", "best", "nice", "great", "cool", "decent", "top", "some", "any", "nearby", "near", "me", "around",
    "close", "closest", "the", "a", "an", "in", "of", "to", "for", "place", "places", "spot", "spots", "where",
    "can", "i", "we", "get", "find", "local", "area", "here", "what", "are", "there", "is", "recommend",
    "recommendation", "recommendations", "somewhere", "go", "grab", "open", "now", "this", "street", "road",
}
_ALIASES = {
    "coffee": "cafe", "cafes": "cafe", "caf": "cafe", "coffeeshop": "cafe", "espresso": "cafe", "flat": "cafe",
    "bars": "bar", "pub": "bar", "pubs": "bar", "drinks": "bar", "drink": "bar", "cocktail": "bar",
    "cocktails": "bar", "restaurants": "restaurant", "food": "restaurant", "eat": "restaurant",
    "eats": "restaurant", "dinner": "restaurant", "lunch": "restaurant", "gig": "venue", "gigs": "venue",
    "venues": "venue", "vinyl": "records", "record": "records", "stores": "store", "shops": "store",
    "shop": "store", "bookshop": "bookstore", "books": "bookstore", "book": "bookstore", "parks": "park",
}


def normalize_query(text: Optional[str]) -> str:
    tokens = []
    for word in _WORD.findall((text or "").lower().replace("é", "e")):
        if word in _STOP:
            continue
        word = _ALIASES.get(word, word)
        if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
            word = _ALIASES.get(word[:-1], word[:-1])
        tokens.append(word)
    return " ".join(sorted(set(tokens)))


def query_similarity(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    lexical = len(ta & tb) / min(len(ta), len(tb))
    try:
        from services_radio.regional_knowledge import get_regional_knowledge
        regional = get_regional_knowledge()
        if regional is not None:
            regional.warm([a, b])
            return max(lexical, regional.similarity(a, b))
    except Exception:
        pass
    return lexical


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _box(lat: float, lon: float, radius_m: float):
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * max(math.cos(math.radians(lat)), 0.01))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon


def _as_result(row: PlaceCache, lat: float, lon: float) -> dict:
    return {
        "place_id": row.place_id,
        "name": row.name,
        "address": row.address,
        "type": row.type,
        "phone": row.phone,
        "website": row.website,
        "rating": row.rating,
        "user_ratings_total": row.rating_count,
        "open_now": None,
        "opening_hours": json.loads(row.opening_hours) if row.opening_hours else None,
        "price_level": row.price_level,
        "distance_m": round(distance_m(lat, lon, row.latitude, row.longitude)),
        "latitude": row.latitude,
        "longitude": row.longitude,
        "details": json.loads(row.details) if row.details else {},
    }


def _row_text(row: PlaceCache) -> str:
    return normalize_query(" ".join([row.name or "", row.type or "", " ".join(json.loads(row.tags or "[]"))]))


async def lookup(query: str, lat: float, lon: float, radius_m: float, limit: int) -> Optional[list[dict]]:
    if not settings.PLACE_MEMORY_ENABLED:
        return None
    norm = normalize_query(query)
    if not norm:
        return None
    now = datetime.now(timezone.utc)
    threshold = settings.PLACE_MEMORY_QUERY_MATCH
    reuse_m = settings.PLACE_MEMORY_REUSE_DISTANCE_M
    try:
        async with AsyncSessionLocal() as db:
            south, north, west, east = _box(lat, lon, reuse_m)
            searches = (await db.execute(select(PlaceSearch).where(and_(
                PlaceSearch.expires_at > now, PlaceSearch.latitude.between(south, north),
                PlaceSearch.longitude.between(west, east))))).scalars().all()
            best = None
            for search in searches:
                if distance_m(lat, lon, search.latitude, search.longitude) > reuse_m:
                    continue
                score = query_similarity(norm, search.query_norm)
                if score >= threshold and (best is None or score > best[0]):
                    best = (score, search)
            if best is not None:
                ids = json.loads(best[1].place_ids or "[]")
                rows = (await db.execute(select(PlaceCache).where(
                    PlaceCache.place_id.in_(ids), PlaceCache.expires_at > now))).scalars().all()
                if rows:
                    results = sorted((_as_result(row, lat, lon) for row in rows), key=lambda r: r["distance_m"])
                    usage_tracking.record_api_call("places", "place_memory", cached=True)
                    log_service.external(f"[PLACES] memory hit for '{norm}' ({len(results)} places, same spot)")
                    return results[:limit]

            south, north, west, east = _box(lat, lon, radius_m)
            rows = (await db.execute(select(PlaceCache).where(and_(
                PlaceCache.expires_at > now, PlaceCache.latitude.between(south, north),
                PlaceCache.longitude.between(west, east))))).scalars().all()
        matches = [row for row in rows
                   if distance_m(lat, lon, row.latitude, row.longitude) <= radius_m
                   and query_similarity(norm, _row_text(row)) >= threshold]
        if len(matches) >= settings.PLACE_MEMORY_MIN_HITS:
            results = sorted((_as_result(row, lat, lon) for row in matches), key=lambda r: r["distance_m"])
            usage_tracking.record_api_call("places", "place_memory", cached=True)
            log_service.external(f"[PLACES] memory hit for '{norm}' ({len(results)} places within {int(radius_m)} m)")
            return results[:limit]
    except Exception as e:
        log_service.warning(f"[PLACES] memory lookup failed: {type(e).__name__}: {e}")
    return None


async def _area_tag(lat: float, lon: float) -> str:
    try:
        from services_radio.area_geocode import cached_area_name
        return await cached_area_name(lat, lon)
    except Exception:
        return ""


async def remember(query: str, lat: float, lon: float, radius_m: float, results: list[dict]) -> None:
    if not settings.PLACE_MEMORY_ENABLED or not results:
        return
    norm = normalize_query(query)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=settings.PLACE_MEMORY_TTL_DAYS)
    placed = [r for r in results if r.get("place_id") and r.get("latitude") is not None and r.get("longitude") is not None]
    if not placed:
        return
    try:
        async with AsyncSessionLocal() as db:
            ids = [r["place_id"] for r in placed]
            existing = {row.place_id: json.loads(row.tags or "[]") for row in (await db.execute(
                select(PlaceCache).where(PlaceCache.place_id.in_(ids)))).scalars().all()}
            rows = []
            for r in placed:
                area = normalize_query(await _area_tag(float(r["latitude"]), float(r["longitude"])))
                tags = sorted(set(existing.get(r["place_id"], [])) | ({norm} if norm else set())
                              | ({area} if area else set()))
                rows.append({
                    "place_id": r["place_id"], "name": r.get("name") or "", "type": r.get("type"),
                    "address": r.get("address"), "phone": r.get("phone"), "website": r.get("website"),
                    "rating": r.get("rating"), "rating_count": r.get("user_ratings_total"),
                    "price_level": r.get("price_level"),
                    "opening_hours": json.dumps(r["opening_hours"]) if r.get("opening_hours") else None,
                    "latitude": float(r["latitude"]), "longitude": float(r["longitude"]),
                    "tags": json.dumps(tags), "details": json.dumps(r["details"]) if r.get("details") else None,
                    "fetched_at": now, "expires_at": expires,
                })
            stmt = pg_insert(PlaceCache).values(rows)
            stmt = stmt.on_conflict_do_update(index_elements=["place_id"], set_={
                column: stmt.excluded[column] for column in (
                    "name", "type", "address", "phone", "website", "rating", "rating_count", "price_level",
                    "opening_hours", "latitude", "longitude", "tags", "fetched_at", "expires_at")}
                | {"details": func.coalesce(stmt.excluded.details, PlaceCache.details)})
            await db.execute(stmt)
            if norm:
                await db.execute(pg_insert(PlaceSearch).values(
                    query_norm=norm, latitude=round(lat, SEARCH_ORIGIN_DECIMALS),
                    longitude=round(lon, SEARCH_ORIGIN_DECIMALS), radius_m=int(radius_m),
                    place_ids=json.dumps(ids), created_at=now, expires_at=expires))
            await db.execute(delete(PlaceSearch).where(PlaceSearch.expires_at < now))
            await db.commit()
    except Exception as e:
        log_service.warning(f"[PLACES] memory save failed: {type(e).__name__}: {e}")
        return
    from services_radio import local_knowledge
    if local_knowledge.place_vector_db is not None:
        try:
            await run_on_gpu_executor(local_knowledge.place_vector_db.add_rows, [f"place:{pid}" for pid in ids])
        except Exception as e:
            log_service.warning(f"[PLACES] adding new places to the search failed: {type(e).__name__}: {e}")


async def searched_near(query: str, lat: float, lon: float) -> bool:
    norm = normalize_query(query)
    if not norm:
        return True
    reuse_m = settings.PLACE_MEMORY_REUSE_DISTANCE_M
    south, north, west, east = _box(lat, lon, reuse_m)
    async with AsyncSessionLocal() as db:
        searches = (await db.execute(select(PlaceSearch).where(and_(
            PlaceSearch.expires_at > datetime.now(timezone.utc), PlaceSearch.latitude.between(south, north),
            PlaceSearch.longitude.between(west, east))))).scalars().all()
    return any(distance_m(lat, lon, search.latitude, search.longitude) <= reuse_m
               and query_similarity(norm, search.query_norm) >= settings.PLACE_MEMORY_QUERY_MATCH
               for search in searches)


async def get_place(place_id: str) -> Optional[dict]:
    try:
        async with AsyncSessionLocal() as db:
            row = await db.get(PlaceCache, place_id)
        if row is None:
            return None
        return {"name": row.name, "type": row.type or "", "address": row.address or "",
                "latitude": row.latitude, "longitude": row.longitude}
    except Exception:
        return None
