import time
from collections import OrderedDict
from typing import Optional

from config import settings
from services_radio import area_signals
from services_radio.area_signals import AreaContext, AreaFetchError, AreaSignal, Cell, raise_for_google
from services_radio.dj_content_bank import TalkingPoint, clip

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
NAME = "geocode"
MAX_SUBJECTS = 5000
NEIGHBOURHOOD_TYPES = ("neighborhood", "sublocality_level_1", "sublocality", "colloquial_area")
LOCALITY_TYPES = ("locality", "postal_town", "administrative_area_level_3", "administrative_area_level_2")
SKIPPED_ROUTES = {"unnamed road"}


def _component(results: list, types: tuple, short: bool = False) -> str:
    for wanted in types:
        for result in results:
            for component in result.get("address_components") or []:
                if wanted in (component.get("types") or []):
                    name = component.get("short_name" if short else "long_name") or ""
                    if name:
                        return name.strip()
    return ""


def parse_geocode(data: dict) -> Optional[dict]:
    status = data.get("status")
    if status == "ZERO_RESULTS":
        return None
    if status in area_signals.BLOCKED_STATUSES:
        raise area_signals.AreaBlocked(status)
    if status in area_signals.RATE_STATUSES:
        raise area_signals.AreaRateLimited(status)
    if status != "OK":
        raise AreaFetchError(str(status))
    results = data.get("results") or []
    street = _component(results[:3], ("route",))
    if street.lower() in SKIPPED_ROUTES:
        street = ""
    neighbourhood = _component(results, NEIGHBOURHOOD_TYPES)
    locality = _component(results, LOCALITY_TYPES)
    region = _component(results, ("administrative_area_level_1",))
    country = _component(results, ("country",))
    country_code = _component(results, ("country",), short=True)
    parts: list[str] = []
    for part in (street, neighbourhood, locality):
        if part and part.lower() not in {p.lower() for p in parts}:
            parts.append(part)
    if len(parts) < 2 and region and region.lower() not in {p.lower() for p in parts}:
        parts.append(region)
    if not parts:
        return None
    return {"street": street, "neighbourhood": neighbourhood, "locality": locality, "region": region,
            "country": country, "country_code": country_code, "description": clip(", ".join(parts), 120)}


def area_name(payload: Optional[dict]) -> str:
    if not payload:
        return ""
    return payload.get("neighbourhood") or payload.get("locality") or ""


class ReverseGeocodeSignal(AreaSignal):
    name = NAME
    api = "geocoding"
    attribution = "Google Maps"
    enabled = settings.AREA_GEOCODE_ENABLED
    cell_m = settings.AREA_GEOCODE_CELL_M
    refresh_s = settings.AREA_GEOCODE_TTL_DAYS * 86400
    empty_ttl_s = settings.AREA_GEOCODE_TTL_DAYS * 86400
    serve_stale_s = 365 * 86400
    daily_cap = settings.AREA_GEOCODE_DAILY_CAP

    def __init__(self, store=None):
        super().__init__(store)
        self._announced: "OrderedDict[str, tuple[str, str, float]]" = OrderedDict()

    async def fetch(self, cell: Cell) -> Optional[dict]:
        response = await area_signals.google_get(
            GEOCODE_URL, self.api_key(),
            {"latlng": f"{cell.center[0]:.5f},{cell.center[1]:.5f}", "language": "en"}, header_key=False)
        raise_for_google(response)
        return parse_geocode(response.json())

    def talking_points(self, cell: Cell, payload: dict, context: AreaContext) -> list[TalkingPoint]:
        if not settings.AREA_GEOCODE_CUES_ENABLED or not context.subject:
            return []
        area = area_name(payload)
        if not area:
            return []
        previous = self._announced.get(context.subject)
        if previous and previous[0] == area:
            return []
        description = payload.get("description") or area
        subject = context.subject

        def mark_announced():
            self._announced.pop(subject, None)
            self._announced[subject] = (area, description, time.time())
            while len(self._announced) > MAX_SUBJECTS:
                self._announced.popitem(last=False)

        if previous:
            text = (f"The listener's device location has moved: they're now around {description} "
                    f"(earlier around {previous[1]}).")
            priority = 0.6
        else:
            text = f"The listener is tuned in from around {description}."
            priority = 0.3
        return [TalkingPoint(f"place:{subject}:{area.lower()}", "whereabouts", text, priority, untrusted=True,
                             on_pick=mark_announced, source="google_maps")]


def _signal() -> Optional[ReverseGeocodeSignal]:
    signal = area_signals.get_signal(NAME)
    return signal if isinstance(signal, ReverseGeocodeSignal) and signal.enabled else None


async def describe(latitude, longitude, wait_s: Optional[float] = None) -> Optional[dict]:
    signal = _signal()
    if signal is None:
        return None
    try:
        lat, lon = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None
    _, payload = await signal.read(lat, lon, settings.AREA_GEOCODE_CONTEXT_WAIT_S if wait_s is None else wait_s)
    return payload


async def cached_area_name(latitude: float, longitude: float) -> str:
    signal = _signal()
    if signal is None:
        return ""
    return area_name(await signal.cached(latitude, longitude))
