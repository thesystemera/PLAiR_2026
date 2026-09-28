import asyncio
import math
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytz
from sqlalchemy import String, cast, func, select

from config.settings import settings
from database.models import PlayEvent, PreferenceType, TrackPreference
from services import log_service
from services_radio.dj_content_bank import TalkingPoint, clip, fact_sentences
from services_radio.regional_knowledge import Taste

J2000 = 2451545.0
J2000_EPOCH = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
KNOWN_NEW_MOON = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
SYNODIC_MONTH_DAYS = 29.530588853
JUST_HAPPENED_MIN = 20
MIN_PLAYS_FOR_GENRE = 8
MIN_PLAYS_FOR_ARTIST = 3
MIN_STREAK_DAYS = 3
MIN_STATION_LISTENERS = 10
NO_UPDATE_TEXT = "no updates needed"

_TEMPERATURE = re.compile(r"Temperature: (-?\d+)")
_CONDITION = re.compile(r"Weather: ([A-Za-z ]+)")
_CONDITION_CATEGORIES = (
    ("storm", ("thunder",)),
    ("snow", ("snow", "sleet")),
    ("rain", ("rain", "drizzle", "shower")),
    ("fog", ("mist", "fog", "haze", "smoke", "dust", "sand")),
    ("clear", ("clear",)),
    ("clouds", ("cloud",)),
)
_CATEGORY_CUES = {
    "rain": "Rain just started ({desc}), {temp}°C out there.",
    "storm": "A thunderstorm just rolled in, {temp}°C.",
    "snow": "Snow just started falling, {temp}°C.",
    "fog": "Fog just rolled in ({desc}), {temp}°C.",
    "clear": "The skies just cleared, {temp}°C.",
    "clouds": "Clouds just moved in ({desc}), {temp}°C.",
}

_stats_cache: dict[str, tuple[float, object]] = {}
_stats_locks: dict[str, asyncio.Lock] = {}
STATS_CACHE_MAX = 2000


def compact_listener_notes(user, limit: Optional[int] = None) -> str:
    limit = limit or settings.DJ_LISTENER_NOTES_MAX_CHARS
    picked: list[str] = []
    for source in (getattr(user, "profile", None), getattr(user, "persona", None)):
        sentences = [s for s in fact_sentences(source or "") if NO_UPDATE_TEXT not in s.lower()]
        if sentences and len(" ".join(picked + sentences[:1])) <= limit:
            picked.append(sentences[0])
    interests = [i.strip() for i in (getattr(user, "shoutout_interests", None) or "").split(",")
                 if i.strip() and NO_UPDATE_TEXT not in i.lower()][:3]
    if interests:
        tail = f"Into: {', '.join(interests)}."
        if len(" ".join(picked + [tail])) <= limit:
            picked.append(tail)
    return clip(" ".join(picked), limit) if picked else ""


def parse_weather(description: Optional[str]) -> Optional[dict]:
    temperature = _TEMPERATURE.search(description or "")
    condition = _CONDITION.search(description or "")
    if not temperature or not condition:
        return None
    desc = condition.group(1).strip().lower()
    category = next((name for name, words in _CONDITION_CATEGORIES if any(w in desc for w in words)), "other")
    return {"temp": int(temperature.group(1)), "desc": desc, "category": category}


def weather_change_cue(old_description: Optional[str], new_description: Optional[str]) -> Optional[tuple[str, str]]:
    old, new = parse_weather(old_description), parse_weather(new_description)
    if not old or not new:
        return None
    hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    if new["category"] != old["category"] and new["category"] in _CATEGORY_CUES:
        text = _CATEGORY_CUES[new["category"]].format(desc=new["desc"], temp=new["temp"])
        return f"weather:{new['category']}:{hour}", text
    delta = new["temp"] - old["temp"]
    if abs(delta) >= settings.DJ_WEATHER_CUE_TEMP_DELTA_C:
        direction = "dropped" if delta < 0 else "jumped"
        return f"weather:temp:{hour}", f"The temperature just {direction} {abs(delta)}°C in the last hour, now {new['temp']}°C."
    return None


def _julian(moment: datetime) -> float:
    return J2000 + (moment - J2000_EPOCH).total_seconds() / 86400.0


def _from_julian(julian: float) -> datetime:
    return J2000_EPOCH + timedelta(days=julian - J2000)


def sun_events(latitude: float, longitude: float, around: datetime) -> list[tuple[str, datetime]]:
    events = []
    base = _julian(around)
    for offset in (-1, 0, 1):
        day_number = math.ceil(base + offset - J2000 + 0.0008)
        mean_solar = day_number - longitude / 360.0
        anomaly = (357.5291 + 0.98560028 * mean_solar) % 360.0
        m = math.radians(anomaly)
        center = 1.9148 * math.sin(m) + 0.02 * math.sin(2 * m) + 0.0003 * math.sin(3 * m)
        ecliptic = math.radians((anomaly + center + 180.0 + 102.9372) % 360.0)
        transit = J2000 + mean_solar + 0.0053 * math.sin(m) - 0.0069 * math.sin(2 * ecliptic)
        sin_decl = math.sin(ecliptic) * math.sin(math.radians(23.4397))
        cos_decl = math.cos(math.asin(sin_decl))
        lat = math.radians(latitude)
        cos_hour = (math.sin(math.radians(-0.833)) - math.sin(lat) * sin_decl) / (math.cos(lat) * cos_decl)
        if -1.0 <= cos_hour <= 1.0:
            hour_angle = math.degrees(math.acos(cos_hour))
            events.append(("sunrise", _from_julian(transit - hour_angle / 360.0)))
            events.append(("sunset", _from_julian(transit + hour_angle / 360.0)))
    return sorted(events, key=lambda event: event[1])


def moon_age_days(moment: datetime) -> float:
    return ((moment - KNOWN_NEW_MOON).total_seconds() / 86400.0) % SYNODIC_MONTH_DAYS


def _zone(tz_name: Optional[str]):
    try:
        return pytz.timezone(tz_name) if tz_name else pytz.utc
    except pytz.UnknownTimeZoneError:
        return pytz.utc


def sky_points(latitude, longitude, tz_name: Optional[str], now: Optional[datetime] = None) -> list[TalkingPoint]:
    try:
        lat, lon = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return []
    now = now or datetime.now(timezone.utc)
    zone = _zone(tz_name)
    local_now = now.astimezone(zone)
    points = []
    lead = timedelta(minutes=settings.DJ_SKY_CUE_LEAD_MIN)
    for kind, at in sun_events(lat, lon, now):
        local_at = at.astimezone(zone)
        clock = local_at.strftime("%I:%M %p").lstrip("0")
        minutes = int(round((at - now).total_seconds() / 60))
        key = f"sky:{kind}:{local_at.strftime('%Y%m%d')}"
        if timedelta(0) < at - now <= lead:
            verb = "Sunset" if kind == "sunset" else "Sunrise"
            points.append(TalkingPoint(f"{key}:soon", "sky", f"{verb} in about {minutes} minutes ({clock} local).", 0.8))
        elif timedelta(0) <= now - at <= timedelta(minutes=JUST_HAPPENED_MIN):
            text = "The sun just went down." if kind == "sunset" else "The sun just came up."
            points.append(TalkingPoint(f"{key}:done", "sky", text, 0.75))
    age = moon_age_days(now)
    night = local_now.hour >= 18 or local_now.hour < 6
    today = local_now.strftime("%Y%m%d")
    if night and abs(age - SYNODIC_MONTH_DAYS / 2) <= 1.0:
        points.append(TalkingPoint(f"sky:fullmoon:{today}", "sky", "Full moon tonight.", 0.35))
    elif night and (age <= 1.0 or age >= SYNODIC_MONTH_DAYS - 1.0):
        points.append(TalkingPoint(f"sky:newmoon:{today}", "sky", "New moon tonight - darkest skies of the month.", 0.3))
    return points


def _cached(key: str):
    entry = _stats_cache.get(key)
    if entry and time.monotonic() - entry[0] < settings.DJ_STATS_CACHE_S:
        return entry[1]
    return None


def _lock(key: str) -> asyncio.Lock:
    global _stats_locks
    if len(_stats_locks) > STATS_CACHE_MAX:
        _stats_locks = {k: lock for k, lock in _stats_locks.items() if lock.locked()}
    return _stats_locks.setdefault(key, asyncio.Lock())


def _store(key: str, points):
    now = time.monotonic()
    _stats_cache.pop(key, None)
    _stats_cache[key] = (now, points)
    while len(_stats_cache) > STATS_CACHE_MAX:
        _stats_cache.pop(next(iter(_stats_cache)))
    return points


def _track_facts(catalog_service, track_id: str) -> tuple[str, str, str]:
    track = catalog_service.get_track(track_id) if catalog_service else None
    track = track or {}
    params = track.get("generation_params") or {}
    genre = (track.get("derived_tags") or {}).get("primary_genre") or ""
    return (params.get("title") or "").strip(), (params.get("artist_name") or "").strip(), str(genre).strip()


def _streak_days(hours: list[datetime], zone, today) -> int:
    days = {hour.astimezone(zone).date() for hour in hours}
    day = today if today in days else today - timedelta(days=1)
    streak = 0
    while day in days:
        streak += 1
        day -= timedelta(days=1)
    return streak


async def listener_stat_points(user_id: Optional[int], session_id: Optional[str], async_session_maker,
                               catalog_service, tz_name: Optional[str]) -> list[TalkingPoint]:
    if not async_session_maker or not (user_id or session_id):
        return []
    cache_key = f"listener:{user_id or session_id}"
    cached = _cached(cache_key)
    if cached is not None:
        return cached
    async with _lock(cache_key):
        cached = _cached(cache_key)
        if cached is not None:
            return cached
        return await _listener_stat_points(cache_key, user_id, session_id, async_session_maker, catalog_service,
                                           tz_name)


async def _listener_stat_points(cache_key: str, user_id: Optional[int], session_id: Optional[str],
                                async_session_maker, catalog_service, tz_name: Optional[str]) -> list[TalkingPoint]:
    owner = PlayEvent.user_id == user_id if user_id else PlayEvent.session_id == session_id
    now = datetime.now(timezone.utc)
    try:
        async with async_session_maker() as db:
            plays = (await db.execute(
                select(PlayEvent.track_id, func.count())
                .where(owner, PlayEvent.event_type == "play", PlayEvent.started_at >= now - timedelta(days=7))
                .group_by(PlayEvent.track_id)
            )).all()
            hours = (await db.execute(
                select(func.date_trunc("hour", PlayEvent.started_at))
                .where(owner, PlayEvent.started_at >= now - timedelta(days=30))
                .distinct()
            )).scalars().all()
    except Exception as e:
        log_service.warning(f"[BANK] Listener stats failed: {type(e).__name__}: {e}")
        return []

    total = sum(count for _, count in plays)
    genres: dict[str, int] = {}
    artists: dict[str, int] = {}
    for track_id, count in plays:
        _, artist, genre = _track_facts(catalog_service, track_id)
        if genre:
            genres[genre] = genres.get(genre, 0) + count
        if artist:
            artists[artist] = artists.get(artist, 0) + count

    points = []
    week = now.strftime("%G%V")
    if total >= MIN_PLAYS_FOR_GENRE and genres:
        genre, count = max(genres.items(), key=lambda item: item[1])
        points.append(TalkingPoint(f"taste:genre:{genre}:{week}", "taste",
                                   f"Listener's top genre this week: {genre} ({count} of {total} plays).", 0.5))
    if artists:
        artist, count = max(artists.items(), key=lambda item: item[1])
        if count >= MIN_PLAYS_FOR_ARTIST:
            points.append(TalkingPoint(f"taste:artist:{artist}:{week}", "taste",
                                       f"Listener's most-played credited artist this week: {artist} ({count} plays).", 0.45))
    zone = _zone(tz_name)
    streak = _streak_days([h for h in hours if h is not None], zone, now.astimezone(zone).date())
    if streak >= MIN_STREAK_DAYS:
        points.append(TalkingPoint(f"streak:{streak}", "streak", f"Listener has tuned in {streak} days in a row.", 0.55))
    return _store(cache_key, points)


async def station_stat_points(async_session_maker, catalog_service) -> list[TalkingPoint]:
    if not async_session_maker:
        return []
    cached = _cached("station")
    if cached is not None:
        return cached
    async with _lock("station"):
        cached = _cached("station")
        if cached is not None:
            return cached
        return await _station_stat_points(async_session_maker, catalog_service)


async def _station_stat_points(async_session_maker, catalog_service) -> list[TalkingPoint]:
    since =datetime.now(timezone.utc) - timedelta(days=7)
    listener = func.coalesce(cast(PlayEvent.user_id, String), PlayEvent.session_id)
    try:
        async with async_session_maker() as db:
            top = (await db.execute(
                select(PlayEvent.track_id, func.count().label("plays"))
                .where(PlayEvent.event_type == "play", PlayEvent.started_at >= since)
                .group_by(PlayEvent.track_id)
                .order_by(func.count().desc())
                .limit(1)
            )).first()
            listeners = (await db.execute(
                select(func.count(func.distinct(listener)))
                .where(PlayEvent.event_type == "play", PlayEvent.started_at >= since)
            )).scalar() or 0
    except Exception as e:
        log_service.warning(f"[BANK] Station stats failed: {type(e).__name__}: {e}")
        return []

    points = []
    week = datetime.now(timezone.utc).strftime("%G%V")
    if top:
        title, artist, _ = _track_facts(catalog_service, top[0])
        if title:
            credit = f" (credited to {artist})" if artist else ""
            points.append(TalkingPoint(f"station:top:{top[0]}:{week}", "station",
                                       f"Most-played track on the station this week: '{title}'{credit}, {top[1]} plays.", 0.35))
    if listeners >= MIN_STATION_LISTENERS:
        points.append(TalkingPoint(f"station:listeners:{week}", "station",
                                   f"{listeners} different listeners tuned in to the station this week.", 0.25))
    return _store("station", points)


PREFERENCE_WEIGHTS = {PreferenceType.SUPER_LIKE: 4.0, PreferenceType.LIKE: 3.0, PreferenceType.BAN: -3.0}


def _genre_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [part.strip(" '\"") for part in value.strip("[]").split(",") if part.strip(" '\"")]
    return []


async def listener_taste(user, user_id: Optional[int], session_id: Optional[str], async_session_maker,
                         catalog_service) -> Taste:
    interests = {i.strip().lower() for i in (getattr(user, "shoutout_interests", None) or "").split(",")
                 if i.strip() and NO_UPDATE_TEXT not in i.lower()}
    if not async_session_maker or not (user_id or session_id):
        return Taste(interests=interests)
    cache_key = f"taste:{user_id or session_id}"
    cached = _cached(cache_key)
    if cached is not None:
        return cached
    async with _lock(cache_key):
        cached = _cached(cache_key)
        if cached is not None:
            return cached
        return await _listener_taste(cache_key, interests, user_id, session_id, async_session_maker, catalog_service)


async def _listener_taste(cache_key: str, interests: set, user_id: Optional[int], session_id: Optional[str],
                          async_session_maker, catalog_service) -> Taste:
    owner = PlayEvent.user_id == user_id if user_id else PlayEvent.session_id == session_id
    weights: dict[str, float] = {}
    try:
        async with async_session_maker() as db:
            plays = (await db.execute(
                select(PlayEvent.track_id, func.count())
                .where(owner, PlayEvent.event_type == "play",
                       PlayEvent.started_at >= datetime.now(timezone.utc) - timedelta(days=30))
                .group_by(PlayEvent.track_id)
            )).all()
            preferences = []
            if user_id:
                preferences = (await db.execute(
                    select(TrackPreference.track_id, TrackPreference.preference_type)
                    .where(TrackPreference.user_id == user_id)
                    .order_by(TrackPreference.created_at.desc())
                    .limit(200)
                )).all()
    except Exception as e:
        log_service.warning(f"[BANK] Taste profile failed: {type(e).__name__}: {e}")
        return Taste(interests=interests)

    for track_id, count in plays:
        weights[track_id] = weights.get(track_id, 0.0) + float(count)
    for track_id, preference in preferences:
        weights[track_id] = weights.get(track_id, 0.0) + PREFERENCE_WEIGHTS.get(preference, 0.0)

    genres: dict[str, float] = {}
    artist_weights: dict[str, float] = {}
    for track_id, weight in weights.items():
        track = catalog_service.get_track(track_id) if catalog_service else None
        if not track:
            continue
        tags = track.get("derived_tags") or {}
        primary = str(tags.get("primary_genre") or "").strip()
        if primary:
            genres[primary] = genres.get(primary, 0.0) + weight
        for secondary in _genre_list(tags.get("secondary_genres")):
            genres[secondary] = genres.get(secondary, 0.0) + weight * 0.5
        artist = ((track.get("generation_params") or {}).get("artist_name") or "").strip()
        if artist:
            artist_weights[artist] = artist_weights.get(artist, 0.0) + weight

    taste = Taste(
        genres={g: w for g, w in sorted(genres.items(), key=lambda kv: kv[1], reverse=True)[:12] if w > 0},
        artists={a for a, w in artist_weights.items() if w >= 2.0},
        interests=interests,
    )
    return _store(cache_key, taste)
