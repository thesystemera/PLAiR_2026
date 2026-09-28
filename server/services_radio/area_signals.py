import asyncio
import json
import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import settings
from services import log_service
from services import usage_tracking
from services.http_client import fetch
from services.task_utils import spawn
from services_radio.dj_content_bank import TalkingPoint

METERS_PER_DEGREE = 111320.0
MEMORY_CELLS = 5000
BLOCKED_STATUSES = {"PERMISSION_DENIED", "API_KEY_SERVICE_BLOCKED", "REQUEST_DENIED", "UNAUTHENTICATED"}
RATE_STATUSES = {"RESOURCE_EXHAUSTED", "OVER_QUERY_LIMIT", "OVER_DAILY_LIMIT"}


class AreaBlocked(Exception):
    pass


class AreaRateLimited(Exception):
    pass


class AreaFetchError(Exception):
    pass


@dataclass(frozen=True)
class Cell:
    key: str
    center: tuple


@dataclass
class AreaContext:
    latitude: float
    longitude: float
    tz_name: Optional[str] = None
    subject: str = ""


def grid_cell(latitude: float, longitude: float, size_m: float) -> Cell:
    lat_step = size_m / METERS_PER_DEGREE
    row = math.floor((latitude + 90.0) / lat_step)
    center_lat = min(max(-90.0 + (row + 0.5) * lat_step, -89.9), 89.9)
    lon_step = size_m / (METERS_PER_DEGREE * max(math.cos(math.radians(center_lat)), 0.01))
    col = math.floor((longitude + 180.0) / lon_step)
    center_lon = -180.0 + (col + 0.5) * lon_step
    if center_lon > 180.0:
        center_lon -= 360.0
    return Cell(key=f"{int(size_m)}:{row}:{col}", center=(round(center_lat, 5), round(center_lon, 5)))


def google_error(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return ""
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        reasons = [d.get("reason") for d in error.get("details") or [] if isinstance(d, dict) and d.get("reason")]
        return reasons[0] if reasons else str(error.get("status") or "")
    return str(data.get("status") or "") if isinstance(data, dict) else ""


def raise_for_google(response: httpx.Response) -> None:
    if response.status_code == 200:
        return
    reason = google_error(response)
    if response.status_code in (401, 403) or reason in BLOCKED_STATUSES:
        raise AreaBlocked(f"{response.status_code} {reason}".strip())
    if response.status_code == 429 or reason in RATE_STATUSES:
        raise AreaRateLimited(f"{response.status_code} {reason}".strip())
    raise AreaFetchError(f"{response.status_code} {reason}".strip())


class AreaCacheStore:
    def __init__(self, session_maker=None):
        self._session_maker = session_maker

    def _sessions(self):
        if self._session_maker is None:
            from database import AsyncSessionLocal
            self._session_maker = AsyncSessionLocal
        return self._session_maker

    async def get(self, namespace: str, cell: str) -> Optional[dict]:
        from database.models import AreaCache
        async with self._sessions()() as db:
            row = await db.get(AreaCache, (namespace, cell))
        if row is None:
            return None
        return {"payload": json.loads(row.payload or "{}"), "status": row.status,
                "fetched_at": row.fetched_at, "expires_at": row.expires_at}

    async def put(self, namespace: str, cell: str, payload: dict, status: str, expires_at: datetime) -> None:
        from database.models import AreaCache
        now = datetime.now(timezone.utc)
        stmt = pg_insert(AreaCache).values(namespace=namespace, cell=cell, payload=json.dumps(payload),
                                           status=status, fetched_at=now, expires_at=expires_at)
        stmt = stmt.on_conflict_do_update(index_elements=["namespace", "cell"], set_={
            "payload": stmt.excluded.payload, "status": stmt.excluded.status,
            "fetched_at": stmt.excluded.fetched_at, "expires_at": stmt.excluded.expires_at})
        async with self._sessions()() as db:
            await db.execute(stmt)
            await db.execute(delete(AreaCache).where(
                AreaCache.expires_at < now - timedelta(days=settings.AREA_CACHE_RETENTION_DAYS)))
            await db.commit()

    async def cells(self, namespace: str) -> list[str]:
        from database.models import AreaCache
        async with self._sessions()() as db:
            return list((await db.execute(select(AreaCache.cell).where(AreaCache.namespace == namespace))).scalars())


class AreaSignal:
    name = "area"
    api = ""
    provider = "google"
    attribution = "Google"
    enabled = True
    cell_m = 1000
    refresh_s = 3600
    empty_ttl_s = 86400
    serve_stale_s = 0
    daily_cap = 0

    def __init__(self, store: Optional[AreaCacheStore] = None):
        self.store = store or AreaCacheStore()
        self._memory: "OrderedDict[str, dict]" = OrderedDict()
        self._inflight: dict[str, asyncio.Task] = {}
        self._failed: dict[str, float] = {}
        self._blocked_until = 0.0
        self._calls_day = ""
        self._calls = 0
        self._aired: "OrderedDict[tuple[str, str], float]" = OrderedDict()

    def api_key(self) -> str:
        return settings.GOOGLE_PLACES_API_KEY

    def available(self) -> bool:
        return bool(self.enabled and self.api_key()) and time.monotonic() >= self._blocked_until

    def cell(self, latitude: float, longitude: float) -> Cell:
        return grid_cell(float(latitude), float(longitude), self.cell_m)

    async def fetch(self, cell: Cell) -> Optional[dict]:
        raise NotImplementedError

    def derive(self, previous: Optional[dict], payload: dict) -> dict:
        return payload

    def talking_points(self, cell: Cell, payload: dict, context: AreaContext) -> list[TalkingPoint]:
        return []

    def already_aired(self, subject: str, key: str) -> bool:
        return (subject, key) in self._aired

    def airing_marker(self, subject: str, key: str):
        def mark():
            self._aired.pop((subject, key), None)
            self._aired[(subject, key)] = time.time()
            while len(self._aired) > MEMORY_CELLS:
                self._aired.popitem(last=False)
        return mark

    def _remember(self, cell_key: str, entry: dict) -> None:
        self._memory.pop(cell_key, None)
        self._memory[cell_key] = entry
        while len(self._memory) > MEMORY_CELLS:
            self._memory.popitem(last=False)

    @staticmethod
    def _fresh(entry: Optional[dict], now: datetime) -> bool:
        return bool(entry) and entry["expires_at"] > now

    def _servable(self, entry: Optional[dict], now: datetime) -> bool:
        return bool(entry) and entry["expires_at"] + timedelta(seconds=self.serve_stale_s) > now

    def _under_cap(self) -> bool:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        if today != self._calls_day:
            self._calls_day, self._calls = today, 0
        return not self.daily_cap or self._calls < self.daily_cap

    async def cached(self, latitude: float, longitude: float) -> Optional[dict]:
        cell = self.cell(latitude, longitude)
        now = datetime.now(timezone.utc)
        entry = self._memory.get(cell.key)
        if not self._fresh(entry, now):
            try:
                stored = await self.store.get(self.name, cell.key)
            except Exception as e:
                log_service.warning(f"[AREA] {self.name} cache read failed: {type(e).__name__}: {e}")
                stored = None
            if stored:
                entry = stored
                self._remember(cell.key, stored)
        if not self._servable(entry, now) or entry.get("status") != "ok":
            return None
        return entry["payload"]

    async def read(self, latitude: float, longitude: float, wait_s: float = 0.0) -> tuple[Cell, Optional[dict]]:
        cell = self.cell(latitude, longitude)
        now = datetime.now(timezone.utc)
        entry = self._memory.get(cell.key)
        if not self._fresh(entry, now):
            try:
                stored = await self.store.get(self.name, cell.key)
            except Exception as e:
                log_service.warning(f"[AREA] {self.name} cache read failed: {type(e).__name__}: {e}")
                stored = None
            if stored and (entry is None or stored["fetched_at"] >= entry["fetched_at"]):
                entry = stored
                self._remember(cell.key, stored)
                if self._fresh(stored, now) and stored.get("status") == "ok":
                    usage_tracking.record_api_call(self.api, f"{self.provider}_{self.name}", cached=True)
        if not self._fresh(entry, now):
            task = self.schedule_refresh(cell, entry)
            if task is not None and wait_s > 0:
                try:
                    await asyncio.wait_for(asyncio.shield(task), wait_s)
                except (asyncio.TimeoutError, Exception):
                    pass
                entry = self._memory.get(cell.key) or entry
        if not self._servable(entry, now) or entry.get("status") != "ok":
            return cell, None
        return cell, entry["payload"]

    def schedule_refresh(self, cell: Cell, previous: Optional[dict]) -> Optional[asyncio.Task]:
        running = self._inflight.get(cell.key)
        if running is not None and not running.done():
            return running
        if not self.available():
            return None
        if time.monotonic() < self._failed.get(cell.key, 0.0):
            return None
        if not self._under_cap():
            return None
        task = spawn(self._refresh(cell, previous), name=f"area_{self.name}_refresh")
        self._inflight[cell.key] = task
        return task

    async def _refresh(self, cell: Cell, previous: Optional[dict]) -> None:
        provider = f"{self.provider}_{self.name}"
        try:
            self._calls += 1
            try:
                payload = await self.fetch(cell)
            except AreaBlocked as e:
                usage_tracking.record_api_call(self.api, provider, error=True)
                self._blocked_until = time.monotonic() + settings.AREA_BLOCKED_BACKOFF_S
                log_service.warning(f"[AREA] {self.name}: unavailable ({e}), pausing for "
                                    f"{settings.AREA_BLOCKED_BACKOFF_S // 3600}h")
                return
            except AreaRateLimited as e:
                usage_tracking.record_api_call(self.api, provider, error=True)
                self._blocked_until = time.monotonic() + settings.AREA_RATE_BACKOFF_S
                log_service.warning(f"[AREA] {self.name}: rate limited ({e}), pausing briefly")
                return
            except (AreaFetchError, httpx.HTTPError, ValueError, KeyError, TypeError) as e:
                usage_tracking.record_api_call(self.api, provider, error=True)
                self._failed[cell.key] = time.monotonic() + settings.AREA_ERROR_RETRY_S
                log_service.warning(f"[AREA] {self.name}: fetch failed ({type(e).__name__}: {e})")
                return
            usage_tracking.record_api_call(self.api, provider)
            now = datetime.now(timezone.utc)
            if payload:
                prior = previous["payload"] if previous and previous.get("status") == "ok" else None
                payload = self.derive(prior, payload)
                status, expires = "ok", now + timedelta(seconds=self.refresh_s)
            else:
                payload, status = {}, "empty"
                expires = now + timedelta(seconds=max(self.empty_ttl_s, self.refresh_s))
            entry = {"payload": payload, "status": status, "fetched_at": now, "expires_at": expires}
            self._remember(cell.key, entry)
            self._failed.pop(cell.key, None)
            try:
                await self.store.put(self.name, cell.key, payload, status, expires)
            except Exception as e:
                log_service.warning(f"[AREA] {self.name} cache save failed: {type(e).__name__}: {e}")
            log_service.external(f"[AREA] {self.name}: refreshed cell {cell.key} ({status})")
        finally:
            self._inflight.pop(cell.key, None)


_signals: "OrderedDict[str, AreaSignal]" = OrderedDict()


def install(signals: list[AreaSignal]) -> None:
    _signals.clear()
    for signal in signals:
        _signals[signal.name] = signal


def get_signal(name: str) -> Optional[AreaSignal]:
    return _signals.get(name)


def signals() -> list[AreaSignal]:
    return list(_signals.values())


def listener_context(user, tz_name: Optional[str], subject: Optional[str]) -> Optional[AreaContext]:
    try:
        latitude, longitude = float(user.latitude), float(user.longitude)
    except (AttributeError, TypeError, ValueError):
        return None
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0) or (latitude == 0.0 and longitude == 0.0):
        return None
    return AreaContext(latitude=latitude, longitude=longitude, tz_name=tz_name or getattr(user, "timezone", None),
                       subject=str(subject or getattr(user, "id", "") or ""))


def location_context(location, tz_name: Optional[str] = None, subject=None) -> Optional[AreaContext]:
    coords = getattr(location, "coords", None) if location is not None else None
    if not coords:
        return None
    return AreaContext(latitude=float(coords[0]), longitude=float(coords[1]),
                       tz_name=tz_name or getattr(location, "timezone", None), subject=str(subject or ""))


async def talking_points(context: Optional[AreaContext]) -> list[TalkingPoint]:
    if context is None:
        return []
    points: list[TalkingPoint] = []
    for signal in signals():
        if not signal.enabled:
            continue
        try:
            cell, payload = await signal.read(context.latitude, context.longitude)
            if payload:
                points.extend(signal.talking_points(cell, payload, context))
        except Exception as e:
            log_service.warning(f"[AREA] {signal.name} talking points failed: {type(e).__name__}: {e}")
    return points


async def google_get(url: str, api_key: str, params: Optional[dict] = None, header_key: bool = True) -> httpx.Response:
    if header_key:
        return await fetch("GET", url, params=params or {}, headers={"X-Goog-Api-Key": api_key})
    return await fetch("GET", url, params={**(params or {}), "key": api_key})


async def google_post(url: str, api_key: str, body: dict) -> httpx.Response:
    return await fetch("POST", url, json=body, headers={"X-Goog-Api-Key": api_key})
