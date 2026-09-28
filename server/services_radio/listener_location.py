import math
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

from config import settings
from services import log_service
from services_radio.dj_content_bank import content_bank, valid_timezone
from services_radio.external_news_service import country_name, resolve_country

COORD_DECIMALS = 4
SAME_PLACE_KM = 0.05
UNKNOWN_ADDRESSES = {"", "unknown", "unknown location", "n/a", "none"}

ACCEPTED = "accepted"
UNCHANGED = "unchanged"
RATE_LIMITED = "rate_limited"
INVALID = "invalid"
JUMP_PENDING = "jump_pending"
DISABLED = "disabled"


def haversine_km(a: tuple, b: tuple) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


def _number(value) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def valid_coordinates(latitude, longitude) -> Optional[tuple]:
    lat, lon = _number(latitude), _number(longitude)
    if lat is None or lon is None:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0) or (lat == 0.0 and lon == 0.0):
        return None
    return round(lat, COORD_DECIMALS), round(lon, COORD_DECIMALS)


def clean_address(address: Optional[str]) -> str:
    text = " ".join(str(address or "").split())
    return "" if text.lower() in UNKNOWN_ADDRESSES else text


def address_city(address: Optional[str]) -> str:
    parts = [p.strip() for p in (address or "").split(",") if p.strip() and not any(ch.isdigit() for ch in p)]
    if not parts:
        return ""
    return parts[-3] if len(parts) >= 3 else parts[0]


def _zone_cities() -> dict:
    from services_radio.regional_knowledge import timezone_cities
    return timezone_cities()


def timezone_city(tz_name: Optional[str]) -> tuple:
    entry = _zone_cities().get(tz_name or "")
    if not entry:
        return "", ""
    return tz_name.rsplit("/", 1)[-1].replace("_", " "), entry[1]


@lru_cache(maxsize=4096)
def nearest_timezone(latitude: float, longitude: float, country_code: Optional[str] = None) -> Optional[str]:
    best, best_km = None, float("inf")
    wanted = (country_code or "").upper()
    for zone, (coords, country) in _zone_cities().items():
        if wanted and country != wanted:
            continue
        km = haversine_km((latitude, longitude), coords)
        if km < best_km:
            best, best_km = zone, km
    if best is None and wanted:
        return nearest_timezone(latitude, longitude)
    return best


@dataclass
class GuestPosition:
    latitude: float
    longitude: float
    accuracy_m: Optional[float]
    timezone: Optional[str]
    updated_at: float
    seen_at: float
    changes: deque = field(default_factory=lambda: deque(maxlen=256))
    pending: Optional[tuple] = None

    @property
    def coords(self) -> tuple:
        return self.latitude, self.longitude


class GuestLocationStore:
    def __init__(self):
        self._entries: "OrderedDict[str, GuestPosition]" = OrderedDict()

    @staticmethod
    def enabled() -> bool:
        return bool(settings.GUEST_LOCATION_ENABLED)

    def _expired(self, entry: GuestPosition, now: float) -> bool:
        return now - entry.seen_at > settings.GUEST_LOCATION_TTL_S

    def _touch(self, session_id: str, entry: GuestPosition) -> None:
        self._entries.pop(session_id, None)
        self._entries[session_id] = entry
        while len(self._entries) > max(1, settings.GUEST_LOCATION_MAX_SESSIONS):
            self._entries.popitem(last=False)

    def update(self, session_id: Optional[str], latitude, longitude, accuracy_m=None, timezone=None,
               now: Optional[float] = None) -> str:
        if not self.enabled():
            return DISABLED
        if not session_id:
            return INVALID
        coords = valid_coordinates(latitude, longitude)
        if coords is None:
            return INVALID
        accuracy = _number(accuracy_m)
        if accuracy is not None and (accuracy < 0 or accuracy > settings.GUEST_LOCATION_MAX_ACCURACY_M):
            return INVALID
        accuracy = round(accuracy) if accuracy is not None else None
        zone = valid_timezone(timezone) if timezone else None
        now = time.time() if now is None else now
        entry = self._entries.get(session_id)
        if entry is not None and self._expired(entry, now):
            self._entries.pop(session_id, None)
            entry = None
        if entry is None:
            entry = GuestPosition(coords[0], coords[1], accuracy, zone, now, now)
            entry.changes.append(now)
            self._touch(session_id, entry)
            return ACCEPTED

        if haversine_km(entry.coords, coords) <= max(SAME_PLACE_KM, (accuracy or 0) / 1000.0 * 0.5):
            entry.seen_at = now
            entry.pending = None
            if zone:
                entry.timezone = zone
            if accuracy is not None and (entry.accuracy_m is None or accuracy < entry.accuracy_m):
                entry.accuracy_m = accuracy
            self._touch(session_id, entry)
            return UNCHANGED

        while entry.changes and now - entry.changes[0] > 3600:
            entry.changes.popleft()
        if now - entry.updated_at < settings.GUEST_LOCATION_MIN_INTERVAL_S \
                or len(entry.changes) >= settings.GUEST_LOCATION_MAX_UPDATES_PER_HOUR:
            return RATE_LIMITED

        distance_km = haversine_km(entry.coords, coords)
        elapsed_h = max(now - entry.updated_at, 1.0) / 3600.0
        coarse_km = max(entry.accuracy_m or 0, accuracy or 0) / 1000.0
        if distance_km - coarse_km > 0 and (distance_km - coarse_km) / elapsed_h > settings.GUEST_LOCATION_MAX_SPEED_KMH:
            pending = entry.pending
            confirmed = pending is not None and haversine_km((pending[0], pending[1]), coords) \
                <= settings.GUEST_LOCATION_JUMP_CONFIRM_KM
            if not confirmed:
                entry.pending = (coords[0], coords[1], now)
                entry.changes.append(now)
                return JUMP_PENDING

        entry.latitude, entry.longitude = coords
        entry.accuracy_m = accuracy
        entry.timezone = zone or entry.timezone
        entry.updated_at = entry.seen_at = now
        entry.pending = None
        entry.changes.append(now)
        self._touch(session_id, entry)
        return ACCEPTED

    def get(self, session_id: Optional[str], now: Optional[float] = None) -> Optional[GuestPosition]:
        if not self.enabled() or not session_id:
            return None
        entry = self._entries.get(session_id)
        if entry is None:
            return None
        if self._expired(entry, time.time() if now is None else now):
            self._entries.pop(session_id, None)
            return None
        return entry

    def forget(self, session_id: Optional[str]) -> None:
        self._entries.pop(session_id or "", None)

    def active(self, max_age_s: float, now: Optional[float] = None) -> dict:
        if not self.enabled():
            return {}
        now = time.time() if now is None else now
        limit = min(max_age_s, settings.GUEST_LOCATION_TTL_S)
        return {session: entry for session, entry in list(self._entries.items()) if now - entry.seen_at <= limit}

    def prune(self, now: Optional[float] = None) -> int:
        now = time.time() if now is None else now
        stale = [session for session, entry in self._entries.items() if self._expired(entry, now)]
        for session in stale:
            self._entries.pop(session, None)
        return len(stale)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


guest_locations = GuestLocationStore()


@dataclass(frozen=True)
class ListenerLocation:
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    accuracy_m: Optional[float] = None
    description: str = ""
    city: str = ""
    region: str = ""
    country_code: str = ""
    timezone: Optional[str] = None
    address: str = ""
    source: str = "none"
    is_guest: bool = True

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    @property
    def coords(self) -> Optional[tuple]:
        return (self.latitude, self.longitude) if self.has_coordinates else None

    @property
    def precise(self) -> bool:
        return self.source in ("profile", "guest") and self.has_coordinates

    @property
    def country(self) -> str:
        return country_name(self.country_code) if self.country_code else ""

    @property
    def place(self) -> str:
        parts: list[str] = []
        for part in (self.city, self.region, self.country):
            if part and part.lower() not in {p.lower() for p in parts}:
                parts.append(part)
        return ", ".join(parts)

    @property
    def news_location(self) -> str:
        parts = [part for part in (self.city, self.country) if part]
        return ", ".join(parts)

    @property
    def label(self) -> str:
        return self.address or self.place

    def query_point(self) -> Optional[str]:
        return f"{self.latitude:.4f},{self.longitude:.4f}" if self.has_coordinates else None


async def _geocode(latitude: float, longitude: float, wait_s: Optional[float]) -> Optional[dict]:
    try:
        from services_radio import area_geocode
        return await area_geocode.describe(latitude, longitude, wait_s)
    except Exception as e:
        log_service.warning(f"[LOCATION] reverse geocode failed: {type(e).__name__}: {e}")
        return None


async def resolve(user=None, session_id: Optional[str] = None, geocode: bool = True,
                  geocode_wait_s: Optional[float] = None) -> ListenerLocation:
    coords = None
    accuracy = None
    address = ""
    tz_name = None
    source = "none"
    is_guest = user is None
    if user is not None:
        coords = valid_coordinates(getattr(user, "latitude", None), getattr(user, "longitude", None))
        address = clean_address(getattr(user, "location", None))
        tz_name = valid_timezone(getattr(user, "timezone", None))
        if coords:
            source = "profile"
    else:
        entry = guest_locations.get(session_id)
        if entry is not None:
            coords = entry.coords
            accuracy = entry.accuracy_m
            tz_name = entry.timezone
            source = "guest"
    tz_name = tz_name or content_bank.session_timezone(session_id)

    payload = await _geocode(coords[0], coords[1], geocode_wait_s) if coords and geocode else None
    payload = payload or {}
    city = payload.get("locality") or address_city(address)
    region = payload.get("region") or ""
    country_code = (payload.get("country_code") or resolve_country(address) or "").upper()
    description = payload.get("description") or ""

    if coords and not tz_name:
        tz_name = nearest_timezone(coords[0], coords[1], country_code or None)

    tz_city, tz_country = timezone_city(tz_name)
    if not coords and not address:
        if tz_city:
            city, country_code = tz_city, tz_country
            source = "timezone"
    else:
        country_code = country_code or tz_country or ""
        if not city and tz_city and (country_code == tz_country) and (
                not coords or haversine_km(coords, _zone_cities()[tz_name][0]) <= settings.REGIONAL_CITY_MATCH_KM):
            city = tz_city

    if source == "none" and address:
        source = "profile_address"

    return ListenerLocation(
        latitude=coords[0] if coords else None,
        longitude=coords[1] if coords else None,
        accuracy_m=accuracy,
        description=description,
        city=city or "",
        region=region,
        country_code=country_code or "",
        timezone=tz_name,
        address=address,
        source=source,
        is_guest=is_guest,
    )


def session_timezone(session_id: Optional[str]) -> Optional[str]:
    guest = guest_locations.get(session_id)
    return (guest.timezone if guest is not None else None) or content_bank.session_timezone(session_id)


def forget_session(session_id: Optional[str]) -> None:
    guest_locations.forget(session_id)
