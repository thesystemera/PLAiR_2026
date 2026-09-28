import re
import time

from config.settings import settings
from services import log_service
from services import usage_tracking
from services.http_client import fetch

TICKETMASTER_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
CACHE_TTL_SECONDS = 3600
CACHE_MAX_ENTRIES = 200
MAX_EVENTS = 25
SEARCH_RADIUS_KM = 50
POOL_PAGE_SIZE = 200
TICKETMASTER_DEEP_PAGING_LIMIT = 1000
GENERIC_EVENT_WORDS = {"event", "events", "concert", "concerts", "gig", "gigs", "show", "shows", "music",
                       "live music", "anything", "something", "stuff", "things", "local", "nearby"}
_LATLONG = re.compile(r"^\s*-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?\s*$")


class EventsService:
    def __init__(self):
        self._cache: dict[tuple, tuple[float, list]] = {}

    async def get_ticketmaster_events(self, location, country_code, start_date, end_date, keyword=None) -> list[dict]:
        keyword = (keyword or "").strip()
        if keyword.lower() in GENERIC_EVENT_WORDS:
            keyword = ""
        if keyword:
            events = await self._fetch_events(location, country_code, start_date, end_date, keyword)
            if events:
                return events
            log_service.external(f"Events: nothing matched '{keyword}', using all events")
        return await self._fetch_events(location, country_code, start_date, end_date)

    async def _fetch_events(self, location, country_code, start_date, end_date, keyword="") -> list[dict]:
        params = {
            "apikey": settings.TICKETMASTER_API_KEY,
            "startDateTime": start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDateTime": end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "size": MAX_EVENTS * 2,
            "sort": "date,asc",
        }
        if location and _LATLONG.match(location):
            params.update({"latlong": location.replace(" ", ""), "radius": SEARCH_RADIUS_KM, "unit": "km"})
        elif location:
            params["city"] = location
        if country_code:
            params["countryCode"] = country_code
        if keyword:
            params["keyword"] = keyword

        key = (location, country_code, params["startDateTime"][:13], params["endDateTime"][:13], keyword.lower())
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]

        try:
            response = await fetch("GET", TICKETMASTER_URL, params=params)
            response.raise_for_status()
            events = response.json().get("_embedded", {}).get("events", [])
        except Exception as e:
            usage_tracking.record_api_call("events", "ticketmaster", error=True)
            log_service.error(f"Events: Ticketmaster request failed: {type(e).__name__}")
            return []
        usage_tracking.record_api_call("events", "ticketmaster")

        unique_events = {}
        for event in events:
            venue = (event.get("_embedded", {}).get("venues") or [{}])[0]
            key_event = (event.get("name"), venue.get("name"))
            unique_events.setdefault(key_event, (event, venue))

        formatted = [self.format_event(event, venue) for event, venue in list(unique_events.values())[:MAX_EVENTS]]

        log_service.external(f"Events: {len(formatted)} events for {location or country_code}")
        now = time.monotonic()
        self._cache = {k: v for k, v in self._cache.items() if now - v[0] < CACHE_TTL_SECONDS}
        self._cache[key] = (now, formatted)
        while len(self._cache) > CACHE_MAX_ENTRIES:
            self._cache.pop(next(iter(self._cache)))
        return formatted

    @staticmethod
    def format_event(event: dict, venue: dict) -> dict:
        classification = (event.get("classifications") or [{}])[0]
        price = (event.get("priceRanges") or [{}])[0]
        start = event.get("dates", {}).get("start", {})
        return {
            "id": event.get("id", ""),
            "name": event.get("name", "N/A"),
            "date": start.get("localDate", "N/A"),
            "time": start.get("localTime", ""),
            "starts_at": start.get("dateTime", ""),
            "venue": venue.get("name", "N/A"),
            "city": (venue.get("city") or {}).get("name", ""),
            "segment": (classification.get("segment") or {}).get("name", ""),
            "genre": (classification.get("genre") or {}).get("name", ""),
            "subgenre": (classification.get("subGenre") or {}).get("name", ""),
            "url": event.get("url", ""),
            "price_range": f"{price.get('min')}-{price.get('max')} {price.get('currency', '')}" if price.get("min") else "",
            "status": event.get("dates", {}).get("status", {}).get("code", ""),
            "info": (event.get("info") or event.get("description") or "")[:160],
        }

    async def fetch_event_pages(self, latlong: str, country_code, start_date, end_date, pages: int) -> list[dict]:
        events: dict[str, dict] = {}
        for page in range(max(1, pages)):
            if (page + 1) * POOL_PAGE_SIZE > TICKETMASTER_DEEP_PAGING_LIMIT:
                break
            params = {
                "apikey": settings.TICKETMASTER_API_KEY,
                "startDateTime": start_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endDateTime": end_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "latlong": latlong, "radius": SEARCH_RADIUS_KM, "unit": "km",
                "size": POOL_PAGE_SIZE, "page": page, "sort": "date,asc",
            }
            if country_code:
                params["countryCode"] = country_code
            try:
                response = await fetch("GET", TICKETMASTER_URL, params=params)
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                usage_tracking.record_api_call("events", "ticketmaster", error=True)
                log_service.error(f"Events pool: Ticketmaster page {page} failed: {type(e).__name__}")
                break
            usage_tracking.record_api_call("events", "ticketmaster")
            for event in data.get("_embedded", {}).get("events", []):
                venue = (event.get("_embedded", {}).get("venues") or [{}])[0]
                formatted = self.format_event(event, venue)
                if formatted["id"]:
                    events.setdefault(formatted["id"], formatted)
            total_pages = (data.get("page") or {}).get("totalPages", 0)
            if page + 1 >= total_pages:
                break
        return list(events.values())

    @staticmethod
    def format_events(events: list[dict]) -> str:
        lines = []
        for e in events:
            details = " | ".join(part for part in (
                f"{e['date']} {e['time']}".strip(), f"{e['venue']}, {e['city']}".strip(", "),
                e["genre"], e["price_range"], e["status"],
            ) if part and part != "Undefined")
            lines.append(f"- {e['name']} ({details})" + (f": {e['info']}" if e["info"] else ""))
        return "\n".join(lines)
