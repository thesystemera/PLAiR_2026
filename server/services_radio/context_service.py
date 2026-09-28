import time
from typing import Dict, Optional, List, cast
from datetime import datetime, timedelta, timezone
import pytz
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from database.models import User, TrackPreference, PreferenceType, WeatherData
from services import log_service
from services.user_content_database_service import coarse_location
from services_radio.external_news_service import resolve_country, resolve_city
from services_radio.dj_content_bank import content_bank
from services_radio import listener_location as location_resolver
from services_radio.listener_location import ListenerLocation

AUDIO_FEATURES_CACHE_TTL_S = 3600
AUDIO_FEATURES_CACHE_MAX = 512
_audio_features_cache: Dict[str, tuple] = {}

def _format_release_date(iso_date_str: str) -> str:
    if not iso_date_str or iso_date_str == 'N/A':
        return 'N/A'
    try:
        if 'T' in iso_date_str:
            date_obj = datetime.fromisoformat(iso_date_str.replace('Z', '+00:00'))
        else:
            date_obj = datetime.strptime(iso_date_str[:10], "%Y-%m-%d")
        return date_obj.strftime("%B %d, %Y")
    except (ValueError, AttributeError):
        return iso_date_str[:10] if len(iso_date_str) >= 10 else iso_date_str

def _format_duration(duration_ms: int) -> str:
    if not duration_ms or duration_ms == 'N/A':
        return 'N/A'
    try:
        seconds = int(duration_ms / 1000)
        minutes, secs = divmod(seconds, 60)
        return f"{minutes}:{secs:02d}"
    except (ValueError, TypeError):
        return 'N/A'

def listener_timezone(user: Optional[User], session_id: Optional[str] = None) -> Optional[str]:
    tz_name = getattr(user, 'timezone', None) if user is not None else None
    if not tz_name and user is None:
        guest = location_resolver.guest_locations.get(session_id)
        tz_name = guest.timezone if guest is not None else None
    return tz_name or content_bank.session_timezone(session_id)

async def listener_location(user: Optional[User], session_id: Optional[str] = None, geocode: bool = True,
                            geocode_wait_s: Optional[float] = None) -> ListenerLocation:
    return await location_resolver.resolve(user, session_id, geocode=geocode, geocode_wait_s=geocode_wait_s)

async def _listener(listener: Optional[ListenerLocation], user, session_id: Optional[str]) -> ListenerLocation:
    return listener if listener is not None else await listener_location(user, session_id)

def format_user_time_str(user: Optional[User], tz_name: Optional[str] = None) -> str:
    try:
        tz_name = tz_name or (user.timezone if user is not None else None)
        if tz_name is not None:
            server_time = datetime.now(timezone.utc)
            local_time = server_time.astimezone(pytz.timezone(cast(str, tz_name)))
            return f"Local Time: {local_time.strftime('%I:%M %p')}"
        else:
            utc_time = datetime.now(timezone.utc)
            return f"Time: {utc_time.strftime('%I:%M %p')} UTC"
    except Exception as e:
        log_service.error(f"[Context] Error formatting user local time: {e}")
        return "Time: Unknown"

def _listener_now(user: Optional[User] = None, tz_name: Optional[str] = None) -> datetime:
    tz_name = tz_name or (getattr(user, 'timezone', None) if user is not None else None)
    if tz_name:
        try:
            return datetime.now(pytz.timezone(cast(str, tz_name))).replace(tzinfo=None)
        except pytz.UnknownTimeZoneError:
            pass
    return datetime.now()

def get_show_details(user: Optional[User] = None, tz_name: Optional[str] = None) -> tuple[str | None, str, str | None]:
    schedule = {
        "06:00 AM": "The Early Bird Show",
        "09:00 AM": "The Morning Vibes Show",
        "12:00 PM": "Lunch Break Beats",
        "03:00 PM": "Afternoon Chill",
        "06:00 PM": "Evening Drive",
        "09:00 PM": "Night Owl Tunes",
        "12:00 AM": "Midnight Madness",
        "03:00 AM": "Late Night Lounge"
    }

    from datetime import timedelta, date
    schedule_times = [(datetime.strptime(time_str, "%I:%M %p"), show) for time_str, show in schedule.items()]
    schedule_times.sort()

    current_time = _listener_now(user, tz_name)

    previous_show = None
    current_show = None
    next_show = None

    for i in range(len(schedule_times)):
        start_time, show_name = schedule_times[i]
        end_time = schedule_times[(i + 1) % len(schedule_times)][0]

        duration = (end_time - start_time) % timedelta(days=1)
        duration_str = f"{duration.seconds // 3600}h {(duration.seconds % 3600) // 60}m"

        if start_time.time() <= current_time.time() < end_time.time():
            remaining_time = datetime.combine(date.today(), end_time.time()) - \
                             datetime.combine(date.today(), current_time.time())
            remaining_str = f"{remaining_time.seconds // 3600}h {(remaining_time.seconds % 3600) // 60}m"

            current_show = f"{show_name} ({duration_str}, {remaining_str} remaining)"
            previous_show = f"{schedule_times[i - 1][1]} ({duration_str})"
            next_show = f"{schedule_times[(i + 1) % len(schedule_times)][1]} ({duration_str})"
            break

    if current_show is None:
        last_show = schedule_times[-1]
        first_show = schedule_times[0]
        duration = (first_show[0] - last_show[0]) % timedelta(days=1)
        duration_str = f"{duration.seconds // 3600}h {(duration.seconds % 3600) // 60}m"

        remaining_time = datetime.combine(date.today(), first_show[0].time()) - \
                         datetime.combine(date.today(), current_time.time())
        if remaining_time.total_seconds() < 0:
            remaining_time += timedelta(days=1)
        remaining_str = f"{remaining_time.seconds // 3600}h {(remaining_time.seconds % 3600) // 60}m"

        current_show = f"{last_show[1]} ({duration_str}, {remaining_str} remaining)"
        previous_show = f"{schedule_times[-2][1]} ({duration_str})"
        next_show = f"{first_show[1]} ({duration_str})"

    return previous_show, current_show, next_show

async def get_user_favorites(user_id: int, db: AsyncSession, catalog_service) -> str:
    try:
        result = await db.execute(
            select(TrackPreference)
            .where(TrackPreference.user_id == user_id)
            .where(TrackPreference.preference_type.in_([PreferenceType.LIKE, PreferenceType.SUPER_LIKE]))
            .order_by(TrackPreference.created_at.desc())
            .limit(30)
        )
        preferences = result.scalars().all()

        if not preferences:
            return "LISTENER'S FAVORITE ARTISTS: None recorded"

        artists = set()
        for pref in preferences:
            track = catalog_service.get_track(pref.track_id)
            if track:
                artist = track.get('generation_params', {}).get('artist_name')
                if artist and artist != 'AI Generated':
                    artists.add(artist)

        if artists:
            artist_list = list(artists)[:7]
            return f"LISTENER'S FAVORITE ARTISTS: {', '.join(artist_list)}"
        return "LISTENER'S FAVORITE ARTISTS: None recorded"

    except Exception as e:
        log_service.error(f"[Context] Error getting favorites: {e}")
        return "LISTENER'S FAVORITE ARTISTS: Error retrieving"

async def get_user_banned(user_id: int, db: AsyncSession, catalog_service) -> str:
    try:
        result = await db.execute(
            select(TrackPreference)
            .where(TrackPreference.user_id == user_id)
            .where(TrackPreference.preference_type == PreferenceType.BAN)
            .order_by(TrackPreference.created_at.desc())
            .limit(10)
        )
        banned_prefs = result.scalars().all()

        if not banned_prefs:
            return "BANNED SONGS: None"

        banned_tracks = []
        for pref in banned_prefs:
            track = catalog_service.get_track(pref.track_id)
            if track:
                title = track.get('generation_params', {}).get('title')
                if title:
                    banned_tracks.append(title)

        if banned_tracks:
            return f"BANNED SONGS: {', '.join(banned_tracks[:5])}"
        return "BANNED SONGS: None"

    except Exception as e:
        log_service.error(f"[Context] Error getting banned tracks: {e}")
        return "BANNED SONGS: None"

async def get_db_weather(user_id: int, db: AsyncSession) -> str:
    try:
        result = await db.execute(
            select(WeatherData)
            .where(WeatherData.user_id == user_id)
            .order_by(WeatherData.timestamp.desc())
            .limit(1)
        )
        weather_data = result.scalars().first()

        if weather_data and weather_data.timestamp and datetime.now(timezone.utc) - weather_data.timestamp < timedelta(hours=3):
            return f"CURRENT WEATHER: {weather_data.description}"
        return "CURRENT WEATHER: Unknown"
    except Exception as e:
        log_service.error(f"[Context] Error getting weather: {e}")
        return "CURRENT WEATHER: Unknown"

async def gather_raw_dependencies(
    user_id: Optional[int],
    session_id: str,
    async_session_maker,
    playback_service,
    audio_features_service,
    catalog_service,
    dj_service=None
) -> Dict:
    user = None
    if user_id:
        async with async_session_maker() as db:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()

    track_info = await get_track_context(
        session_id,
        playback_service,
        audio_features_service
    )
    location = await listener_location(user, session_id)

    return {
        'user': user,
        'listener_location': location,
        'listener_timezone': location.timezone or listener_timezone(user, session_id),
        'async_session_maker': async_session_maker,
        'user_id': user_id,
        'session_id': session_id,
        'current_track': track_info['current_track'],
        'next_track': track_info['next_track'],
        'upcoming_track': track_info['upcoming_track'],
        'last_track': track_info['last_track'],
        'playback_service': playback_service,
        'catalog_service': catalog_service,
        'audio_features_service': audio_features_service,
        'dj_service': dj_service
    }

async def get_track_context(session_id: str, playback_service, audio_features_service) -> Dict:
    try:
        playback_state = playback_service.get_state(session_id, simplified=False)

        if not playback_state or not playback_state.get('current_track'):
            return {
                'last_track': _empty_track(),
                'current_track': _empty_track(),
                'next_track': _empty_track(),
                'upcoming_track': _empty_track()
            }

        queue = playback_state.get('queue', [])
        history = playback_state.get('history', [])
        current_index = playback_state.get('current_index', 0)

        last_track = _empty_track()
        if history and len(history) > 0:
            last_track = await _format_track(history[-1], audio_features_service)

        current_track_data = _empty_track()
        current_track = playback_state.get('current_track')
        if current_track:
            current_track_data = await _format_track(current_track, audio_features_service)
            current_track_data['progress_seconds'] = playback_state.get('progress_ms', 0) // 1000
            if current_track_data['duration_seconds'] > 0:
                current_track_data['progress_percentage'] = (
                    current_track_data['progress_seconds'] / current_track_data['duration_seconds']
                ) * 100

        next_track = _empty_track()
        if current_index + 1 < len(queue):
            next_track = await _format_track(queue[current_index + 1], audio_features_service)

        upcoming_track = _empty_track()
        if current_index + 2 < len(queue):
            upcoming_track = await _format_track(queue[current_index + 2], audio_features_service)

        return {
            'last_track': last_track,
            'current_track': current_track_data,
            'next_track': next_track,
            'upcoming_track': upcoming_track
        }

    except Exception as e:
        log_service.error(f"[Context] Error getting track context: {e}")
        return {
            'last_track': _empty_track(),
            'current_track': _empty_track(),
            'next_track': _empty_track(),
            'upcoming_track': _empty_track()
        }

def _summarize_audio_features(features: Optional[Dict]) -> Dict:
    if not features:
        return {}
    dynamic_range = features.get('dynamic_range', 0)
    overall_loudness = features.get('overall_loudness', -30)

    energy = min(1.0, max(0.0, (overall_loudness + 30) / 40 + dynamic_range / 80))

    tempo = features.get('tempo', 120)
    if 115 <= tempo <= 135:
        danceability = 1.0
    elif 90 <= tempo <= 160:
        danceability = 0.7
    elif 60 <= tempo <= 180:
        danceability = 0.5
    else:
        danceability = 0.3

    return {
        'tempo': features.get('tempo', 0),
        'energy': round(energy, 2),
        'danceability': round(danceability, 2),
        'loudness': features.get('overall_loudness', 0),
        'dynamic_range': features.get('dynamic_range', 0),
        'time_signature': features.get('time_signature', 4),
        'key': features.get('key', 'N/A'),
        'mode': features.get('mode', 'N/A'),
        'valence': round(features.get('valence', 0), 2),
        'beat_count': features.get('beat_count', 0)
    }

async def _track_audio_features(track_id: str, audio_features_service) -> Dict:
    now = time.monotonic()
    cached = _audio_features_cache.get(track_id)
    if cached and now - cached[0] < AUDIO_FEATURES_CACHE_TTL_S:
        return cached[1]
    audio_features = _summarize_audio_features(await audio_features_service.load_features(track_id))
    _audio_features_cache.pop(track_id, None)
    _audio_features_cache[track_id] = (now, audio_features)
    while len(_audio_features_cache) > AUDIO_FEATURES_CACHE_MAX:
        _audio_features_cache.pop(next(iter(_audio_features_cache)))
    return audio_features

async def _format_track(track: Dict, audio_features_service) -> Dict:
    try:
        track_id = track.get('id', 'N/A')

        audio_features = {}
        if track_id != 'N/A' and audio_features_service is not None:
            audio_features = dict(await _track_audio_features(track_id, audio_features_service))

        title = (track.get('track_info', {}).get('title') or
                 track.get('generation_params', {}).get('title') or
                 track.get('title', 'N/A'))

        artist = (track.get('generation_params', {}).get('artist_name') or
                  track.get('user_request', {}).get('original_text') or
                  'AI Generated')

        style_description = track.get('generation_params', {}).get('style', '')

        full_lyrics = track.get('generation_params', {}).get('prompt', '')
        lyrics_preview = full_lyrics[:500] + '...' if len(full_lyrics) > 500 else full_lyrics

        instrumental = track.get('generation_params', {}).get('instrumental', False)
        vocal_gender_code = track.get('generation_params', {}).get('vocal_gender', '')
        vocal_gender = 'Male' if vocal_gender_code == 'm' else 'Female' if vocal_gender_code == 'f' else ''

        duration_ms = track.get('track_info', {}).get('duration', 0)
        duration_seconds = duration_ms // 1000 if duration_ms else track.get('duration_seconds', 0)
        duration_formatted = _format_duration(duration_ms)

        created_at = track.get('created_at', '')
        release_date_formatted = _format_release_date(created_at)

        return {
            'id': track_id,
            'name': title,
            'artists': artist,
            'credited_artist': (track.get('generation_params', {}).get('artist_name') or '').strip(),
            'duration': duration_formatted,
            'duration_seconds': duration_seconds,
            'release_date': release_date_formatted,
            'instrumental': instrumental,
            'vocal_gender': vocal_gender,
            'style_description': style_description,
            'lyrics_preview': lyrics_preview,
            'audio_features': audio_features,
            'progress_seconds': 0,
            'progress_percentage': 0
        }
    except Exception as e:
        log_service.error(f"[Context] Error formatting track: {e}")
        return _empty_track()

def _empty_track() -> Dict:
    return {
        'id': 'N/A',
        'name': 'N/A',
        'artists': 'N/A',
        'duration': 'N/A',
        'duration_seconds': 0,
        'release_date': 'N/A',
        'audio_features': {},
        'progress_seconds': 0,
        'progress_percentage': 0
    }

async def get_shoutouts_data(
    dj_service,
    user,
    query: Optional[str] = None,
    n_results: int = 5,
    session_id: Optional[str] = None,
    listener: Optional[ListenerLocation] = None
) -> str:

    if not dj_service or not dj_service.user_content_vector_search_service:
        return ""

    try:
        user_location = (await _listener(listener, user, session_id)).coords

        search_query = query if query else "Recent community messages and shoutouts"

        shoutouts = await dj_service.user_content_vector_search_service.search(
            query=search_query,
            n_results=n_results,
            user_location=user_location,
            use_ai_analysis=False
        )

        if not shoutouts:
            return ""

        formatted = "AVAILABLE SHOUTOUTS:\n"
        for i, shoutout in enumerate(shoutouts, 1):
            user_data = shoutout.get('user_data', {})
            username = user_data.get('username') or shoutout.get('username', 'Unknown Listener')
            location = coarse_location(user_data.get('location') or shoutout.get('location')) or 'Unknown Location'
            score = shoutout.get('final_score', 0)
            transcription = shoutout.get('transcription', '').strip()
            audio_url = shoutout.get('audio_url', '')

            formatted += f"{i}. From: {username} ({location}) | Score: {score:.2f}\n"
            formatted += f"   Message: \"{transcription}\"\n"
            formatted += f"   Audio: ${audio_url}$\n\n"

        return formatted

    except Exception as e:
        log_service.warning(f"[Context] Failed to fetch shoutouts: {e}")
        return ""

async def get_biography_data(dj_service, artist_name: Optional[str] = None, current_track: Optional[Dict] = None) -> str:

    if not artist_name and current_track:
        artist_name = current_track.get('artists', 'Unknown Artist')

    if not artist_name or not dj_service or not dj_service.web_service:
        return ""

    try:
        biography = await dj_service.web_service.retrieve_artist_biography(artist_name)
        if not biography:
            return ""
        return f"ARTIST: {artist_name}\n\nBIOGRAPHY:\n{biography}"
    except Exception as e:
        log_service.warning(f"[Context] Failed to fetch biography: {e}")
        return ""

async def get_news_data(
    dj_service,
    user,
    query: Optional[str] = None,
    is_topic: bool = False,
    categories: Optional[List] = None,
    location: Optional[str] = None,
    session_id: Optional[str] = None,
    listener: Optional[ListenerLocation] = None
) -> str:

    if not dj_service or not dj_service.news_service:
        return ""

    news_service = dj_service.news_service
    scope = (location or "WORLD").upper()
    general = not query or query == "general news"

    try:
        home_country = (await _listener(listener, user, session_id)).country_code or None
        local_city = None
        if scope == "WORLD":
            country = home_country
            if general:
                query, is_topic = "WORLD", True
        elif scope in ("NATIONAL", "US"):
            country = home_country if scope == "NATIONAL" else "US"
            if general:
                query, is_topic = "NATION", True
        else:
            country = resolve_country(location) or home_country
            city = resolve_city(location)
            if city:
                if general or is_topic:
                    local_city, query = city, city
                else:
                    query = f"{query} {city}"
                is_topic = False
        if local_city and hasattr(news_service, "get_local_news"):
            articles, news_report = await news_service.get_local_news(local_city, country=country, subject=session_id)
        else:
            articles, news_report = await news_service.get_top_news(query=query, is_topic=is_topic, country=country,
                                                                    subject=session_id)
        if not articles or not news_report:
            return ""
        if session_id and hasattr(news_service, "mark_aired"):
            await news_service.mark_aired(session_id, articles)
        cat_str = ', '.join(categories) if categories else 'N/A'
        return f"NEWS REPORT:\n{news_report}\n\nQUERY: {query}\nIS TOPIC: {is_topic}\nCATEGORIES: {cat_str}\nLOCATION: {location}"
    except Exception as e:
        log_service.warning(f"[Context] Failed to fetch news: {e}")
        return ""

async def get_weather_data(dj_service, user, forecast_type: str = "current", session_id: Optional[str] = None,
                           listener: Optional[ListenerLocation] = None) -> str:

    if not dj_service or not dj_service.web_service:
        return ""

    coords = (await _listener(listener, user, session_id)).coords
    if not coords:
        return ""

    try:
        weather_data = await dj_service.web_service.retrieve_weather_data(coords[0], coords[1], forecast_type)
        if not weather_data:
            return ""
        return f"WEATHER REPORT ({forecast_type.upper()}):\n{weather_data}"
    except Exception as e:
        log_service.warning(f"[Context] Failed to fetch weather: {e}")
        return ""

async def get_location_data(dj_service, user, query: Optional[str] = None, session_id: Optional[str] = None,
                            listener: Optional[ListenerLocation] = None) -> str:
    if not query or not dj_service or not dj_service.location_service:
        return ""

    user_location = (await _listener(listener, user, session_id)).coords
    if not user_location:
        return ""

    try:
        search_report = await dj_service.location_service.get_location_search_report(query, user_location)
        if not search_report:
            return ""
        return f"LOCATION SEARCH REPORT:\n{search_report}"
    except Exception as e:
        log_service.warning(f"[Context] Failed to fetch location: {e}")
        return ""

def format_regional_events(scored_items) -> str:
    lines = []
    for _, item in scored_items:
        tags = " / ".join(item.tags[1:] or item.tags)
        lines.append(f"- {item.title} ({' | '.join(part for part in (item.text, tags) if part)})")
    return "\n".join(lines)

async def get_regional_events_data(
    dj_service,
    user,
    user_id: Optional[int],
    session_id: Optional[str],
    tz_name: Optional[str],
    async_session_maker,
    catalog_service,
    location: Optional[str] = None,
    country_code: Optional[str] = None,
    start_date=None,
    end_date=None,
    keyword: Optional[str] = None,
    listener: Optional[ListenerLocation] = None
) -> str:
    from services_radio.regional_knowledge import KIND_EVENT, TicketmasterEventsCollector, get_regional_knowledge, resolve_region
    from services_radio.external_events_service import GENERIC_EVENT_WORDS
    from services_radio.dj_bank_sources import listener_taste

    listener = await _listener(listener, user, session_id)
    regional = get_regional_knowledge()
    region = resolve_region(user, tz_name, location=listener) if regional is not None else None
    needle = (keyword or "").strip()
    if needle.lower() in GENERIC_EVENT_WORDS:
        needle = ""
    if region is not None and start_date and end_date:
        taste = await listener_taste(user, user_id, session_id, async_session_maker, catalog_service)
        pooled = await regional.query(region, (KIND_EVENT,), taste, window=(start_date, end_date), limit=12,
                                      text_query=needle or None, record_hit=True)
        if len(pooled) >= (1 if needle else 3):
            log_service.external(f"[Context] Events served from the {region.name} pool ({len(pooled)})")
            return f"EVENTS DATA:\n{format_regional_events(pooled)}"

    if location is None and country_code is None:
        location = listener.query_point() or listener.city or None
        country_code = listener.country_code or None
    if location is None and country_code is None:
        return ""

    async def feed_pool(events):
        if regional is None or region is None:
            return
        now = datetime.now(timezone.utc)
        items = [item for item in (TicketmasterEventsCollector.to_item(region, event, now) for event in events) if item]
        await regional.ingest(region, items)

    return await get_events_data(dj_service, location, country_code, start_date, end_date, keyword, feed_pool)

async def get_events_data(
    dj_service,
    location: Optional[str] = None,
    country_code: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    keyword: Optional[str] = None,
    on_events=None
) -> str:

    if not dj_service or not dj_service.events_service:
        return ""

    try:
        events_data = await dj_service.events_service.get_ticketmaster_events(location, country_code, start_date,
                                                                              end_date, keyword)
        if not events_data:
            return ""
        if on_events is not None:
            await on_events(events_data)
        return f"EVENTS DATA:\n{dj_service.events_service.format_events(events_data)}"
    except Exception as e:
        log_service.warning(f"[Context] Failed to fetch events: {e}")
        return ""