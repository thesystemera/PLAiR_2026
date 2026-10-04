import asyncio
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.orm import load_only
from database.models import PlayEvent, User, WeatherData
from config.settings import settings
from services import log_service
from services import usage_tracking
from services_radio.dj_bank_sources import weather_change_cue
from services_radio.dj_content_bank import content_bank
from services_radio import regional_knowledge as regional_kb
from services_radio import listener_location as location_resolver
from services_radio.listener_location import guest_locations

class BackgroundTasksService:
    def __init__(self, web_service, tts_vector_db_service, async_session_maker, catalog_vector_db_service=None, catalog_service=None, user_content_vector_db_service=None, user_content_service=None, broadcast_content_func=None, youtube_clip_service=None):
        self.web_service = web_service
        self.tts_vector_db_service = tts_vector_db_service
        self.async_session_maker = async_session_maker
        self.catalog_vector_db_service = catalog_vector_db_service
        self.catalog_service = catalog_service
        self.user_content_vector_db_service = user_content_vector_db_service
        self.user_content_service = user_content_service
        self.broadcast_content_func = broadcast_content_func
        self.youtube_clip_service = youtube_clip_service
        self._guest_weather: "OrderedDict[str, tuple[float, str]]" = OrderedDict()

    async def weather_updater(self):
        while True:
            try:
                async with self.async_session_maker() as db:
                    active_since = datetime.now(timezone.utc) - timedelta(days=14)
                    active_user_ids = select(PlayEvent.user_id).where(PlayEvent.started_at >= active_since).distinct()
                    users = (await db.execute(
                        select(User.id, User.latitude, User.longitude).where(User.id.in_(active_user_ids))
                    )).all()

                fetched = []
                for user_id, latitude, longitude in users:
                    if latitude and longitude:
                        with usage_tracking.subject_scope(user_id=user_id):
                            weather_desc = await self.web_service.retrieve_weather_data(latitude, longitude, 'current')
                        if weather_desc:
                            fetched.append((user_id, weather_desc))

                if fetched:
                    await self._save_user_weather(fetched)

                guests = await self.update_guest_weather()
                if guests:
                    log_service.external(f"Weather Updater: Updated weather for {guests} located guest(s)")

                await asyncio.sleep(3600)  # 1 hour
            except asyncio.CancelledError:
                log_service.info("Weather Updater: Task cancelled")
                break
            except Exception as e:
                log_service.error(f"Weather updater error: {e}")
                await asyncio.sleep(60)

    async def _save_user_weather(self, fetched: list) -> None:
        async with self.async_session_maker() as db:
            existing = {row.user_id: row for row in (await db.execute(
                select(WeatherData).where(WeatherData.user_id.in_([user_id for user_id, _ in fetched]))
            )).scalars().all()}
            for user_id, weather_desc in fetched:
                weather_data = existing.get(user_id)
                if weather_data and settings.DJ_WEATHER_CUES_ENABLED and weather_data.timestamp and \
                        datetime.now(timezone.utc) - weather_data.timestamp < timedelta(hours=3):
                    cue = weather_change_cue(weather_data.description, weather_desc)
                    if cue:
                        content_bank.set_weather_cue(f"user:{user_id}", *cue)
                if weather_data:
                    weather_data.description = weather_desc
                    weather_data.timestamp = datetime.now(timezone.utc)
                else:
                    db.add(WeatherData(user_id=user_id, description=weather_desc, timestamp=datetime.now(timezone.utc)))
            await db.commit()
        for user_id, _ in fetched:
            log_service.external(f"Weather Updater: Updated weather for user {user_id}")

    async def active_regions(self) -> list:
        regions = {}
        async with self.async_session_maker() as db:
            active_since = datetime.now(timezone.utc) - timedelta(days=14)
            active_user_ids = select(PlayEvent.user_id).where(PlayEvent.started_at >= active_since).distinct()
            users = (await db.execute(select(User).options(
                load_only(User.id, User.timezone, User.latitude, User.longitude, User.location, raiseload=True))
                .where(User.id.in_(active_user_ids)))).scalars().all()
        for user in users:
            region = regional_kb.resolve_region(user)
            if region:
                regions.setdefault(region.key, region)
        located = guest_locations.active(regional_kb.ACTIVE_GUEST_MAX_AGE_S)
        for session_id in located:
            location = await location_resolver.resolve(None, session_id, geocode_wait_s=0)
            region = regional_kb.resolve_region(None, None, location=location)
            if region:
                regions.setdefault(region.key, region)
        for session_id, zone in content_bank.active_session_timezones(regional_kb.ACTIVE_GUEST_MAX_AGE_S).items():
            if session_id in located:
                continue
            region = regional_kb.resolve_region(None, zone)
            if region:
                regions.setdefault(region.key, region)
        return list(regions.values())

    async def update_guest_weather(self) -> int:
        if not settings.DJ_WEATHER_CUES_ENABLED or self.web_service is None:
            return 0
        guest_locations.prune()
        updated = 0
        for session_id, entry in guest_locations.active(3 * 3600).items():
            with usage_tracking.subject_scope(session_id=session_id):
                weather_desc = await self.web_service.retrieve_weather_data(entry.latitude, entry.longitude, 'current')
            if not weather_desc:
                continue
            previous = self._guest_weather.get(session_id)
            if previous and time.time() - previous[0] < 3 * 3600:
                cue = weather_change_cue(previous[1], weather_desc)
                if cue:
                    content_bank.set_weather_cue(f"session:{session_id}", *cue)
            self._guest_weather.pop(session_id, None)
            self._guest_weather[session_id] = (time.time(), weather_desc)
            while len(self._guest_weather) > settings.GUEST_LOCATION_MAX_SESSIONS:
                self._guest_weather.popitem(last=False)
            updated += 1
        for session_id in [s for s in self._guest_weather if guest_locations.get(s) is None]:
            self._guest_weather.pop(session_id, None)
        return updated

    async def regional_knowledge_refresher(self):
        await asyncio.sleep(180)
        while True:
            try:
                service = regional_kb.get_regional_knowledge()
                if service is not None:
                    for region in await self.active_regions():
                        with usage_tracking.system_scope("regional_knowledge"):
                            await service.refresh(region)
                        await asyncio.sleep(2)
                await asyncio.sleep(1800)
            except asyncio.CancelledError:
                log_service.info("Regional Knowledge Refresher: Task cancelled")
                break
            except Exception as e:
                log_service.error(f"Regional knowledge refresher error: {e}")
                await asyncio.sleep(300)

    async def listener_request_maintainer(self):
        from service_registry import services
        from services_radio.pulse_demand import demand
        from services_radio.pulse_sources import place_shoutouts
        last_prune = 0.0
        while True:
            try:
                await asyncio.sleep(settings.PULSE_REQUEST_REBUILD_S)
                if services.news_service is not None:
                    await services.news_service.upkeep()
                await place_shoutouts()
                if time.monotonic() - last_prune > 86400:
                    await demand.prune()
                    last_prune = time.monotonic()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log_service.error(f"Listener request maintainer error: {e}")

    async def vector_store_maintainer(self):
        from services.vector_store import STORES
        while True:
            try:
                await asyncio.sleep(settings.VECTOR_REBUILD_INTERVAL_S)
                for store in list(STORES):
                    if store.dirty:
                        await asyncio.to_thread(store.rebuild)
            except asyncio.CancelledError:
                log_service.info("Vector store maintainer: Task cancelled")
                break
            except Exception as e:
                log_service.error(f"Vector store maintainer error: {e}")
                await asyncio.sleep(60)

    async def video_clip_pre_downloader(self):
        await asyncio.sleep(120)

        while True:
            try:
                if not self.youtube_clip_service or not self.catalog_service:
                    await asyncio.sleep(600)
                    continue

                budget = settings.YOUTUBE_CLIPS_PREFETCH_PER_CYCLE
                attempts = 0
                downloaded = 0

                for keyword in self.youtube_clip_service.take_priority_keywords(budget):
                    attempts += 1
                    try:
                        if await self.youtube_clip_service.get_clip_for_keyword(keyword):
                            downloaded += 1
                    except Exception as e:
                        log_service.error(f"[YOUTUBE] Pre-download failed for '{keyword}': {e}")
                    await asyncio.sleep(2)

                for track in list(self.catalog_service.tracks.values()):
                    if attempts >= budget:
                        break
                    metadata = track if isinstance(track, dict) else {}
                    for keyword in self.youtube_clip_service.missing_keywords_for_track(metadata):
                        if attempts >= budget:
                            break
                        attempts += 1
                        try:
                            if await self.youtube_clip_service.get_clip_for_keyword(keyword):
                                downloaded += 1
                        except Exception as e:
                            log_service.error(f"[YOUTUBE] Pre-download failed for '{keyword}': {e}")
                        await asyncio.sleep(2)

                if downloaded:
                    stats = self.youtube_clip_service.get_cache_stats()
                    log_service.success(
                        f"✓ Video clip prefetch: +{downloaded} clips "
                        f"({stats['clip_count']} cached, {stats['total_size_mb']}MB)"
                    )

                await self.youtube_clip_service.enforce_cache_limit()

                await self.youtube_clip_service.wait_for_work(1800)

            except asyncio.CancelledError:
                log_service.info("Video Clip Pre-Downloader: Task cancelled")
                break
            except Exception as e:
                log_service.error(f"Video clip pre-downloader error: {e}")
                await asyncio.sleep(300)