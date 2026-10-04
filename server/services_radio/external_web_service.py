import asyncio
import datetime
import time
import unicodedata
from collections import OrderedDict, defaultdict
from typing import Optional
from urllib.parse import quote

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config.settings import settings
from services import log_service
from services import usage_tracking
from services import web_fetch
from services.http_client import fetch
from services.task_utils import spawn
from services_radio.talking_clock import clock_time

MUSICBRAINZ_URL = "https://musicbrainz.org/ws/2/artist/"


async def musicbrainz_get(url: str, params: dict):
    return await web_fetch.get(url, web_fetch.MUSICBRAINZ, params=params)
WEATHER_URL = "https://api.openweathermap.org/data/2.5/"
COMPASS = ("north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest")
WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
BIOGRAPHY_NEGATIVE_CACHE_SECONDS = 600
BIOGRAPHY_CACHE_MAX = 500
WEATHER_CACHE_MAX = 5000
FORECAST_CACHE_MAX = 500
MIN_MUSICBRAINZ_SCORE = 90


def _normalize_name(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return "".join(ch for ch in ascii_name.lower() if ch.isalnum())


class WebService:
    def __init__(self, area_store=None, session_maker=None):
        self.weather_cache: dict[tuple, tuple[float, str]] = {}
        self.forecast_cache: "OrderedDict[tuple, tuple[float, dict]]" = OrderedDict()
        self.biography_cache: dict[str, tuple[float, str, float]] = {}
        self.area_store = area_store
        self._session_maker = session_maker
        self._locks: dict[tuple, asyncio.Lock] = {}
        self._biography_tasks: dict[str, asyncio.Task] = {}

    def _sessions(self):
        if self._session_maker is None:
            from database import AsyncSessionLocal
            self._session_maker = AsyncSessionLocal
        return self._session_maker

    def _lock(self, key: tuple) -> asyncio.Lock:
        if len(self._locks) > 1000:
            self._locks = {k: lock for k, lock in self._locks.items() if lock.locked()}
        return self._locks.setdefault(key, asyncio.Lock())

    def cached_artist_biography(self, artist_name: str) -> Optional[str]:
        cached = self.biography_cache.get(_normalize_name(artist_name or ""))
        if cached and time.monotonic() - cached[0] < cached[2]:
            return cached[1]
        return None

    def _remember_biography(self, key: str, biography: str, ttl: float) -> None:
        now = time.monotonic()
        self.biography_cache.pop(key, None)
        self.biography_cache[key] = (now, biography, ttl)
        self.biography_cache = {k: v for k, v in self.biography_cache.items() if now - v[0] < v[2]}
        while len(self.biography_cache) > BIOGRAPHY_CACHE_MAX:
            self.biography_cache.pop(next(iter(self.biography_cache)))

    async def _stored_biography(self, key: str) -> Optional[tuple[str, float]]:
        if not settings.BIOGRAPHY_PERSIST_ENABLED:
            return None
        from database.models import ArtistBiography
        try:
            async with self._sessions()() as db:
                row = await db.get(ArtistBiography, key)
        except Exception as e:
            log_service.warning(f"Biography store read failed: {type(e).__name__}: {e}")
            return None
        if row is None:
            return None
        remaining = (row.expires_at - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        if remaining <= 0:
            return None
        return ((row.biography or "") if row.status == "found" else ""), remaining

    async def _save_biography(self, key: str, artist_name: str, biography: str, ttl: float) -> None:
        if not settings.BIOGRAPHY_PERSIST_ENABLED:
            return
        from database.models import ArtistBiography
        now = datetime.datetime.now(datetime.timezone.utc)
        stmt = pg_insert(ArtistBiography).values(
            name_key=key, artist_name=artist_name[:300], biography=biography,
            status="found" if biography else "not_found", fetched_at=now,
            expires_at=now + datetime.timedelta(seconds=ttl))
        stmt = stmt.on_conflict_do_update(index_elements=["name_key"], set_={
            column: stmt.excluded[column] for column in ("artist_name", "biography", "status", "fetched_at",
                                                         "expires_at")})
        try:
            async with self._sessions()() as db:
                await db.execute(stmt)
                await db.commit()
        except Exception as e:
            log_service.warning(f"Biography store write failed: {type(e).__name__}: {e}")

    async def retrieve_artist_biography(self, artist_name: str) -> str:
        key = _normalize_name(artist_name or "")
        if not key:
            return ""
        cached = self.cached_artist_biography(artist_name)
        if cached is not None:
            return cached
        task = self._biography_tasks.get(key)
        if task is None:
            task = spawn(self._resolve_biography(artist_name, key), name="biography_lookup")
            self._biography_tasks[key] = task
            task.add_done_callback(lambda _, k=key: self._biography_tasks.pop(k, None))
        return await asyncio.shield(task)

    async def _resolve_biography(self, artist_name: str, key: str) -> str:
        cached = self.cached_artist_biography(artist_name)
        if cached is not None:
            return cached
        stored = await self._stored_biography(key)
        if stored is not None:
            self._remember_biography(key, stored[0], stored[1])
            usage_tracking.record_api_call("biography", "musicbrainz_wikipedia", cached=True)
            return stored[0]
        return await self._lookup_biography(artist_name, key)

    async def _lookup_biography(self, artist_name: str, key: str) -> str:
        failed = False
        try:
            biography = await asyncio.wait_for(self._fetch_biography(artist_name, key), settings.BIOGRAPHY_DEADLINE_S)
        except asyncio.TimeoutError:
            log_service.error(f"Biography lookup for '{artist_name}' exceeded {settings.BIOGRAPHY_DEADLINE_S:.0f}s")
            biography, failed = "", True
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log_service.error(f"Biography lookup failed for '{artist_name}': {type(e).__name__}: {e}")
            biography, failed = "", True
        usage_tracking.record_api_call("biography", "musicbrainz_wikipedia", error=failed)

        if biography:
            ttl = settings.BIOGRAPHY_TTL_DAYS * 86400
        else:
            ttl = BIOGRAPHY_NEGATIVE_CACHE_SECONDS if failed else settings.BIOGRAPHY_NOT_FOUND_TTL_S
        self._remember_biography(key, biography, ttl)
        if not failed:
            await self._save_biography(key, artist_name, biography, ttl)
        return biography

    async def _fetch_biography(self, artist_name: str, key: str) -> str:
        response = await musicbrainz_get(MUSICBRAINZ_URL, {"query": f'artist:"{artist_name}"', "fmt": "json", "limit": 5})
        response.raise_for_status()
        match = next((
            a for a in response.json().get("artists", [])
            if a.get("score", 0) >= MIN_MUSICBRAINZ_SCORE and key in {
                _normalize_name(a.get("name", "")),
                *(_normalize_name(alias.get("name", "")) for alias in a.get("aliases", []) or []),
            }
        ), None)
        if not match:
            log_service.external(f"Biography: no confident MusicBrainz match for '{artist_name}'")
            return ""

        response = await musicbrainz_get(f"{MUSICBRAINZ_URL}{match['id']}", {"inc": "url-rels", "fmt": "json"})
        response.raise_for_status()
        wikidata_url = next((
            r["url"]["resource"] for r in response.json().get("relations", []) if r.get("type") == "wikidata"
        ), None)
        if not wikidata_url:
            return ""

        wikidata_id = wikidata_url.rstrip("/").split("/")[-1]
        response = await fetch("GET", WIKIDATA_API_URL, circuit=True, params={
            "action": "wbgetentities", "ids": wikidata_id, "props": "sitelinks", "sitefilter": "enwiki", "format": "json"
        })
        response.raise_for_status()
        data = response.json()
        entity = data.get("entities", {}).get(wikidata_id, {})
        if "error" in data or "missing" in entity:
            raise ValueError(f"Wikidata entity {wikidata_id} unavailable")
        title = entity.get("sitelinks", {}).get("enwiki", {}).get("title")
        if not title:
            return ""

        response = await fetch("GET", f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(title.replace(' ', '_'), safe='')}",
                               circuit=True)
        response.raise_for_status()
        return response.json().get("extract") or ""

    async def retrieve_weather_data(self, latitude, longitude, forecast_type: str = "current") -> Optional[str]:
        formatter = {
            "current": self.format_current_weather,
            "today": lambda data: self.format_daily_forecast(data, 0),
            "tomorrow": lambda data: self.format_daily_forecast(data, 1),
            "week": self.format_weekly_forecast,
        }.get(forecast_type)
        if not formatter:
            log_service.error(f"Weather: invalid forecast type '{forecast_type}'")
            return None

        lat, lon = round(float(latitude), 2), round(float(longitude), 2)
        cache_key = (lat, lon, forecast_type)
        cached = self._cached_weather(cache_key)
        if cached is not None:
            usage_tracking.record_api_call("weather", "openweathermap", cached=True)
            return cached
        endpoint = "weather" if forecast_type == "current" else "forecast"
        async with self._lock(("weather", lat, lon, endpoint)):
            cached = self._cached_weather(cache_key)
            if cached is None:
                cached = await self._stored_weather(cache_key)
            if cached is None and endpoint == "forecast":
                cached = self._format_cached_forecast(cache_key, formatter)
            if cached is not None:
                usage_tracking.record_api_call("weather", "openweathermap", cached=True)
                return cached
            return await self._fetch_weather(cache_key, formatter)

    def _format_cached_forecast(self, cache_key: tuple, formatter) -> Optional[str]:
        entry = self.forecast_cache.get(cache_key[:2])
        if not entry:
            return None
        age = time.monotonic() - entry[0]
        if age >= settings.WEATHER_CACHE_S:
            self.forecast_cache.pop(cache_key[:2], None)
            return None
        formatted = formatter(entry[1])
        if formatted:
            self._remember_weather(cache_key, formatted, age)
        return formatted

    def _remember_forecast(self, cell: tuple, data: dict) -> None:
        now = time.monotonic()
        self.forecast_cache.pop(cell, None)
        self.forecast_cache[cell] = (now, data)
        while self.forecast_cache:
            oldest = next(iter(self.forecast_cache.values()))
            if len(self.forecast_cache) <= FORECAST_CACHE_MAX and now - oldest[0] < settings.WEATHER_CACHE_S:
                break
            self.forecast_cache.popitem(last=False)

    def _cached_weather(self, cache_key: tuple) -> Optional[str]:
        cached = self.weather_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < settings.WEATHER_CACHE_S:
            return cached[1]
        return None

    def _remember_weather(self, cache_key: tuple, formatted: str, age_s: float = 0.0) -> None:
        now = time.monotonic()
        self.weather_cache = {k: v for k, v in self.weather_cache.items() if now - v[0] < settings.WEATHER_CACHE_S}
        self.weather_cache[cache_key] = (now - age_s, formatted)
        while len(self.weather_cache) > WEATHER_CACHE_MAX:
            self.weather_cache.pop(next(iter(self.weather_cache)))

    @staticmethod
    def _weather_cell(cache_key: tuple) -> tuple[str, str]:
        lat, lon, forecast_type = cache_key
        return f"weather_{forecast_type}", f"{lat:.2f},{lon:.2f}"

    async def _stored_weather(self, cache_key: tuple) -> Optional[str]:
        if self.area_store is None or not settings.WEATHER_PERSIST_ENABLED:
            return None
        try:
            stored = await self.area_store.get(*self._weather_cell(cache_key))
        except Exception as e:
            log_service.warning(f"Weather: store read failed: {type(e).__name__}: {e}")
            return None
        now = datetime.datetime.now(datetime.timezone.utc)
        if not stored or stored["status"] != "ok" or stored["expires_at"] <= now:
            return None
        formatted = (stored["payload"] or {}).get("text")
        if not formatted:
            return None
        self._remember_weather(cache_key, formatted, (now - stored["fetched_at"]).total_seconds())
        return formatted

    async def _fetch_weather(self, cache_key: tuple, formatter) -> Optional[str]:
        lat, lon, forecast_type = cache_key
        endpoint = "weather" if forecast_type == "current" else "forecast"
        try:
            response = await fetch("GET", f"{WEATHER_URL}{endpoint}", circuit=True, params={
                "lat": lat, "lon": lon, "appid": settings.WEATHER_API_KEY, "units": "metric",
            })
        except httpx.HTTPError as e:
            usage_tracking.record_api_call("weather", "openweathermap", error=True)
            log_service.error(f"Weather: request failed: {type(e).__name__}")
            return None
        usage_tracking.record_api_call("weather", "openweathermap", error=response.status_code != 200)
        if response.status_code != 200:
            log_service.error(f"Weather: OpenWeatherMap returned {response.status_code}")
            return None

        data = response.json()
        if endpoint == "forecast":
            self._remember_forecast((lat, lon), data)
        formatted = formatter(data)
        if formatted:
            self._remember_weather(cache_key, formatted)
            if self.area_store is not None and settings.WEATHER_PERSIST_ENABLED:
                expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
                    seconds=settings.WEATHER_CACHE_S)
                try:
                    await self.area_store.put(*self._weather_cell(cache_key), {"text": formatted}, "ok", expires)
                except Exception as e:
                    log_service.warning(f"Weather: store write failed: {type(e).__name__}: {e}")
        return formatted

    @staticmethod
    def _local_time(timestamp: int, tz_offset_seconds: int) -> datetime.datetime:
        tz = datetime.timezone(datetime.timedelta(seconds=tz_offset_seconds))
        return datetime.datetime.fromtimestamp(timestamp, tz)

    @staticmethod
    def _wind(wind: dict) -> str:
        speed = f"{round(wind['speed'], 1):g} m/s"
        if wind.get("deg") is None:
            return speed
        return f"{speed} from the {COMPASS[round(wind['deg'] / 45) % 8]}"

    @staticmethod
    def format_current_weather(current_data):
        try:
            main = current_data["main"]
            wind = current_data["wind"]
            weather = current_data["weather"][0]
            sys = current_data["sys"]
            tz = current_data.get("timezone", 0)
            return (
                f"Current Weather in {current_data['name']}:\n"
                f"Temperature: {int(main['temp'])}°C, Feels Like: {int(main['feels_like'])}°C, "
                f"Humidity: {main['humidity']}%, Pressure: {main['pressure']} hPa, "
                f"Visibility: {current_data.get('visibility', 'N/A')} meters, "
                f"Wind: {WebService._wind(wind)}, "
                f"Cloudiness: {current_data['clouds']['all']}%, "
                f"Weather: {weather['description']}, "
                f"Sunrise: {clock_time(WebService._local_time(sys['sunrise'], tz))}, "
                f"Sunset: {clock_time(WebService._local_time(sys['sunset'], tz))}"
            )
        except KeyError as e:
            log_service.error(f"Weather: unexpected current-weather payload, missing {e}")
            return None

    @staticmethod
    def format_daily_forecast(forecast_data, day_offset=0):
        tz = forecast_data.get("city", {}).get("timezone", 0)
        now_local = WebService._local_time(int(time.time()), tz)
        target_date = (now_local + datetime.timedelta(days=day_offset)).date()
        day_name = "Today's" if day_offset == 0 else "Tomorrow's"
        lines = []
        for entry in forecast_data["list"]:
            entry_time = WebService._local_time(entry["dt"], tz)
            if entry_time.date() == target_date:
                main = entry["main"]
                wind = entry["wind"]
                lines.append(
                    f"{clock_time(entry_time)} - "
                    f"Temp: {int(main['temp'])}°C, Feels Like: {int(main['feels_like'])}°C, "
                    f"Humidity: {main['humidity']}%, "
                    f"Wind: {WebService._wind(wind)}, "
                    f"Weather: {entry['weather'][0]['description']}"
                )
        if not lines:
            return f"No forecast data available for {'today' if day_offset == 0 else 'tomorrow'}."
        return f"{day_name} Forecast:\n" + "\n".join(lines)

    @staticmethod
    def format_weekly_forecast(forecast_data):
        tz = forecast_data.get("city", {}).get("timezone", 0)
        daily_summaries = defaultdict(lambda: {"temps": [], "descs": []})
        for entry in forecast_data["list"]:
            date = WebService._local_time(entry["dt"], tz).date()
            daily_summaries[date]["temps"].append(entry["main"]["temp"])
            daily_summaries[date]["descs"].append(entry["weather"][0]["description"])
        lines = [
            f"{date.strftime('%A, %B %d')} - High: {int(max(d['temps']))}°C, Low: {int(min(d['temps']))}°C, "
            f"Weather: {max(set(d['descs']), key=d['descs'].count)}"
            for date, d in daily_summaries.items()
        ]
        return "5-Day Forecast:\n" + "\n".join(lines)
