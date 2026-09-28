import asyncio
import re
import time
from typing import Optional

import httpx

from config.settings import settings
from services import log_service
from services.http_client import fetch
from services import usage_tracking
from services.task_utils import spawn
from services_radio import place_memory

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places/"
PLACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{10,300}$")
UNAVAILABLE_BACKOFF_S = 12 * 3600
FIELD_MASK = ",".join([
    "places.id", "places.displayName", "places.formattedAddress", "places.primaryTypeDisplayName", "places.rating",
    "places.userRatingCount", "places.priceLevel", "places.currentOpeningHours.openNow",
    "places.currentOpeningHours.weekdayDescriptions", "places.websiteUri", "places.nationalPhoneNumber",
    "places.location",
])
PRICE_LEVELS = {"PRICE_LEVEL_INEXPENSIVE": "$", "PRICE_LEVEL_MODERATE": "$$",
                "PRICE_LEVEL_EXPENSIVE": "$$$", "PRICE_LEVEL_VERY_EXPENSIVE": "$$$$"}
CACHE_TTL_SECONDS = 86400
CACHE_MAX_ENTRIES = 200


class LocationSearchUnavailable(Exception):
    pass


class LocationService:
    def __init__(self):
        self._cache: dict[tuple, tuple[float, list]] = {}
        self._locks: dict[tuple, asyncio.Lock] = {}
        self._unavailable_until = 0.0
        log_service.system("Location Service initialized (Places API New)")

    def available(self) -> bool:
        return bool(settings.GOOGLE_PLACES_API_KEY) and time.monotonic() >= self._unavailable_until

    async def _places_request(self, method: str, url: str, field_mask: str, api: str, provider: str, **kwargs):
        if not self.available():
            return None
        headers = {"X-Goog-Api-Key": settings.GOOGLE_PLACES_API_KEY, "X-Goog-FieldMask": field_mask}
        try:
            response = await fetch(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as e:
            usage_tracking.record_api_call(api, provider, error=True)
            log_service.error(f"Places: request failed: {type(e).__name__}")
            return None
        usage_tracking.record_api_call(api, provider, error=response.status_code != 200)
        if response.status_code in (401, 403):
            self._unavailable_until = time.monotonic() + UNAVAILABLE_BACKOFF_S
            log_service.warning(f"Places: unavailable ({response.status_code}), pausing pool lookups for 12h")
            return None
        if response.status_code != 200:
            log_service.error(f"Places: returned {response.status_code}")
            return None
        return response.json()

    async def search_place_ids(self, query: str, center: tuple, radius: float = 15000,
                               max_results: int = 10) -> list[str]:
        body = {
            "textQuery": query,
            "maxResultCount": max_results,
            "locationBias": {"circle": {"center": {"latitude": round(float(center[0]), 2),
                                                   "longitude": round(float(center[1]), 2)},
                                        "radius": float(radius)}},
        }
        data = await self._places_request("POST", PLACES_SEARCH_URL, "places.id", "places_ids",
                                          "google_places_ids", json=body)
        return [place["id"] for place in (data or {}).get("places", []) if place.get("id")]

    async def place_summary(self, place_id: str) -> Optional[dict]:
        if not PLACE_ID_PATTERN.match(place_id or ""):
            return None
        data = await self._places_request("GET", f"{PLACE_DETAILS_URL}{place_id}",
                                          "displayName,primaryTypeDisplayName", "places_details", "google_places_details")
        name = ((data or {}).get("displayName") or {}).get("text")
        if not name:
            return None
        return {"name": name, "type": ((data or {}).get("primaryTypeDisplayName") or {}).get("text") or ""}

    async def get_nearby_places(self, query, location, radius=1500, max_results=5) -> list[dict]:
        if not settings.GOOGLE_PLACES_API_KEY:
            raise LocationSearchUnavailable("GOOGLE_PLACES_API_KEY is not set")

        lat, lon = round(float(location[0]), 3), round(float(location[1]), 3)
        key = (query.lower(), lat, lon, radius, max_results)
        cached = self._cached(key)
        if cached is not None:
            return cached
        async with self._lock(key):
            cached = self._cached(key)
            if cached is not None:
                return cached
            return await self._search_nearby(query, location, lat, lon, radius, max_results, key)

    def _cached(self, key: tuple) -> Optional[list]:
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
            usage_tracking.record_api_call("places", "google_places", cached=True)
            return cached[1]
        return None

    def _lock(self, key: tuple) -> asyncio.Lock:
        if len(self._locks) > 500:
            self._locks = {k: lock for k, lock in self._locks.items() if lock.locked()}
        return self._locks.setdefault(key, asyncio.Lock())

    async def _search_nearby(self, query, location, lat, lon, radius, max_results, key) -> list[dict]:
        remembered = await place_memory.lookup(query, float(location[0]), float(location[1]), radius, max_results)
        if remembered:
            self._remember_in_process(key, remembered)
            return remembered

        body = {
            "textQuery": query,
            "maxResultCount": max_results,
            "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": float(radius)}},
        }
        headers = {"X-Goog-Api-Key": settings.GOOGLE_PLACES_API_KEY, "X-Goog-FieldMask": FIELD_MASK}
        try:
            response = await fetch("POST", PLACES_SEARCH_URL, json=body, headers=headers)
        except httpx.HTTPError as e:
            usage_tracking.record_api_call("places", "google_places", error=True)
            raise LocationSearchUnavailable(f"request failed: {type(e).__name__}") from e
        usage_tracking.record_api_call("places", "google_places", error=response.status_code != 200)

        if response.status_code in (401, 403):
            error = response.json().get("error", {})
            raise LocationSearchUnavailable(f"{error.get('status')}: {error.get('message', '')[:120]}")
        response.raise_for_status()

        results = []
        for place in response.json().get("places", []):
            hours = place.get("currentOpeningHours", {})
            results.append({
                "place_id": place.get("id"),
                "name": place.get("displayName", {}).get("text"),
                "address": place.get("formattedAddress"),
                "type": place.get("primaryTypeDisplayName", {}).get("text"),
                "phone": place.get("nationalPhoneNumber"),
                "website": place.get("websiteUri"),
                "rating": place.get("rating"),
                "user_ratings_total": place.get("userRatingCount"),
                "open_now": hours.get("openNow"),
                "opening_hours": hours.get("weekdayDescriptions"),
                "price_level": PRICE_LEVELS.get(place.get("priceLevel", "")),
                "latitude": place.get("location", {}).get("latitude"),
                "longitude": place.get("location", {}).get("longitude"),
            })

        spawn(place_memory.remember(query, float(location[0]), float(location[1]), radius, results),
              name="place_memory_save")
        self._remember_in_process(key, results)
        return results

    def _remember_in_process(self, key, results):
        now = time.monotonic()
        self._cache = {k: v for k, v in self._cache.items() if now - v[0] < CACHE_TTL_SECONDS}
        self._cache[key] = (now, results)
        while len(self._cache) > CACHE_MAX_ENTRIES:
            self._cache.pop(next(iter(self._cache)))

    async def get_location_search_report(self, query, location):
        search_terms = query.replace("nearby", "").replace("around", "").strip()
        try:
            results = await self.get_nearby_places(search_terms, location)
        except LocationSearchUnavailable as e:
            log_service.error(f"Location search unavailable: {e}")
            return ""
        except httpx.HTTPError as e:
            log_service.error(f"Location search failed: {type(e).__name__}")
            return ""

        if not results:
            log_service.external(f"Location search: no results for '{search_terms}'")
            return ""

        lines = [f"Search Results for '{search_terms}':", ""]
        for i, place in enumerate(results, 1):
            name = f"<a href='{place['website']}' target='_blank'>{place['name']}</a>" if place["website"] else place["name"]
            lines.append(f"{i}. {name}" + (f" ({place['type']})" if place["type"] else ""))
            lines.append(f"   Address: {place['address']}")
            if place.get("distance_m") is not None:
                lines.append(f"   Distance: about {place['distance_m']} m away")
            if place["phone"]:
                lines.append(f"   Phone: {place['phone']}")
            if place["rating"]:
                lines.append(f"   Rating: {place['rating']}/5 ({place['user_ratings_total']} reviews)")
            if place["open_now"] is not None:
                lines.append(f"   Open now: {'Yes' if place['open_now'] else 'No'}")
            if place["price_level"]:
                lines.append(f"   Price: {place['price_level']}")
            if place["opening_hours"]:
                lines.append("   Hours: " + "; ".join(place["opening_hours"]))
            lines.append("")
        return "\n".join(lines)
