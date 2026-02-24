import asyncio
from datetime import datetime, timezone
from sqlalchemy import select
from database.models import User, WeatherData
from services import log_service

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

    async def weather_updater(self):
        while True:
            try:
                async with self.async_session_maker() as db:
                    result = await db.execute(select(User))
                    users = result.scalars().all()

                    for user in users:
                        if user.latitude and user.longitude:
                            weather_desc = await self.web_service.retrieve_weather_data(
                                user.latitude, user.longitude, 'current'
                            )

                            if weather_desc:
                                weather_result = await db.execute(
                                    select(WeatherData).where(WeatherData.user_id == user.id)
                                )
                                weather_data = weather_result.scalar_one_or_none()

                                if weather_data:
                                    weather_data.description = weather_desc
                                    weather_data.timestamp = datetime.now(timezone.utc)
                                else:
                                    weather_data = WeatherData(
                                        user_id=user.id,
                                        description=weather_desc,
                                        timestamp=datetime.now(timezone.utc)
                                    )
                                    db.add(weather_data)

                                await db.commit()
                                log_service.external(f"Weather Updater: Updated weather for user {user.id}")

                await asyncio.sleep(3600)  # 1 hour
            except asyncio.CancelledError:
                log_service.info("Weather Updater: Task cancelled")
                break
            except Exception as e:
                log_service.error(f"Weather updater error: {e}")
                await asyncio.sleep(60)

    async def vector_database_rebuilder(self):
        while True:
            try:
                await asyncio.sleep(300)  # 5 minutes

                if len(self.tts_vector_db_service.new_embeddings_log) > 0:
                    log_service.info(f"Vector DB: Rebuilding vector database with {len(self.tts_vector_db_service.new_embeddings_log)} new embeddings")
                    await asyncio.to_thread(self.tts_vector_db_service.rebuild_indexes)
            except asyncio.CancelledError:
                log_service.info("Vector DB Rebuilder: Task cancelled")
                break
            except Exception as e:
                log_service.error(f"Vector database rebuilder error: {e}")
                await asyncio.sleep(60)

    async def catalog_index_updater(self):
        await asyncio.sleep(300)

        last_catalog_size = 0

        while True:
            try:
                if not self.catalog_vector_db_service or not self.catalog_service:
                    await asyncio.sleep(300)
                    continue

                current_size = len(self.catalog_service.tracks)

                if current_size != last_catalog_size:
                    log_service.vector_music(
                        f"🔄 Catalog size changed ({last_catalog_size} → {current_size}), "
                        f"rebuilding indexes in background..."
                    )
                    await asyncio.to_thread(
                        self.catalog_vector_db_service.rebuild_indexes,
                        self.catalog_service
                    )
                    last_catalog_size = current_size
                    log_service.success("✓ Catalog vector indexes rebuilt and swapped")

                await asyncio.sleep(300)

            except asyncio.CancelledError:
                log_service.info("Catalog Index Updater: Task cancelled")
                break
            except Exception as e:
                log_service.error(f"Catalog index updater error: {e}")
                await asyncio.sleep(60)

    async def user_content_index_updater(self):
        await asyncio.sleep(300)

        last_user_content_size = 0

        while True:
            try:
                if not self.user_content_vector_db_service or not self.user_content_service:
                    await asyncio.sleep(300)
                    continue

                current_size = len(self.user_content_service.shoutouts)

                if current_size != last_user_content_size:
                    log_service.user_content(
                        f"🔄 User content size changed ({last_user_content_size} → {current_size}), "
                        f"rebuilding indexes in background..."
                    )
                    all_shoutouts = await self.user_content_service.load_all_shoutouts_for_indexing()
                    if all_shoutouts:
                        await asyncio.to_thread(
                            self.user_content_vector_db_service.rebuild_indexes,
                            all_shoutouts
                        )
                        last_user_content_size = current_size
                        log_service.success("✓ User content vector indexes rebuilt and swapped (backup sweep)")
                    else:
                        log_service.warning("⚠️  No shoutouts found to index")

                await asyncio.sleep(300)

            except asyncio.CancelledError:
                log_service.info("User Content Index Updater: Task cancelled")
                break
            except Exception as e:
                log_service.error(f"User content index updater error: {e}")
                await asyncio.sleep(60)

    async def video_clip_pre_downloader(self):
        await asyncio.sleep(120)

        while True:
            try:
                if not self.youtube_clip_service or not self.catalog_service:
                    await asyncio.sleep(600)
                    continue

                tracks = list(self.catalog_service.tracks.values())
                total_downloaded = 0
                tracks_with_terms = 0

                for track in tracks:
                    metadata = track if isinstance(track, dict) else {}
                    gen_params = metadata.get("generation_params", {})
                    derived_tags = metadata.get("derived_tags", {})
                    keywords = gen_params.get("video_search_terms") or derived_tags.get("video_search_terms")

                    if not keywords:
                        continue

                    tracks_with_terms += 1

                    try:
                        count = await self.youtube_clip_service.pre_download_for_track(metadata)
                        total_downloaded += count
                    except Exception as e:
                        log_service.error(f"[YOUTUBE] Pre-download failed for {metadata.get('id', '?')}: {e}")

                    await asyncio.sleep(2)

                if total_downloaded > 0:
                    stats = self.youtube_clip_service.get_cache_stats()
                    log_service.success(
                        f"✓ Video clip pre-download complete: "
                        f"{stats['clip_count']} clips cached ({stats['total_size_mb']}MB) "
                        f"from {tracks_with_terms} tracks"
                    )

                await self.youtube_clip_service.cleanup_old_clips()

                await asyncio.sleep(1800)

            except asyncio.CancelledError:
                log_service.info("Video Clip Pre-Downloader: Task cancelled")
                break
            except Exception as e:
                log_service.error(f"Video clip pre-downloader error: {e}")
                await asyncio.sleep(300)