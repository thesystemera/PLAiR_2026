import asyncio
import json
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone
from collections import deque
from sqlalchemy import select, func
from database import AsyncSessionLocal, PlayEvent, TrackAnalytics, TrackPreference, PreferenceType
from database.models import ShoutoutAnalytics, ShoutoutPreference, ShoutoutPreferenceType
from services import log_service
from services.base_service import SingletonService
from services.analytics_file_service import analytics_file_service

async def safe_background_task(coro, task_name="background_task"):
    try:
        await coro
    except Exception as e:
        log_service.error(f"{task_name} failed with exception: {e}")
        import traceback
        log_service.error(f"Traceback: {traceback.format_exc()}")

class AnalyticsService(SingletonService):
    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.event_buffer: deque = deque(maxlen=1000)
        self.buffer_lock = asyncio.Lock()
        self.cache: Dict = {
            "top_hits_all_time": [],
            "top_hits_week": [],
            "top_hits_day": [],
            "track_stats": {},
            "top_shoutouts_all_time": [],
            "top_shoutouts_week": [],
            "top_shoutouts_day": [],
            "shoutout_stats": {}
        }
        self.cache_lock = asyncio.Lock()
        self.cache_ttl = timedelta(minutes=5)
        self.last_cache_update = datetime.now(timezone.utc)

        self.flush_interval = 30
        self.aggregate_interval = 300
        self.aggregate_semaphore = asyncio.Semaphore(5)
        self._running = False
        self._initialized = True

    async def initialize(self):
        await analytics_file_service.initialize()
        await self._load_cache(silent=True)
        log_service.analytics("AnalyticsService initialized")

    async def log_play_event(self, user_id: Optional[int], track_id: str, session_id: Optional[str] = None,
                             device_id: Optional[str] = None, event_type: str = "play",
                             skip_reason: Optional[str] = None, duration_ms: Optional[int] = None,
                             completion_pct: Optional[float] = None):
        event = {
            "user_id": user_id,
            "track_id": track_id,
            "session_id": session_id,
            "device_id": device_id,
            "event_type": event_type,
            "skip_reason": skip_reason,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": duration_ms,
            "completion_pct": completion_pct
        }

        async with self.buffer_lock:
            self.event_buffer.append(event)

        if len(self.event_buffer) >= 50:
            asyncio.create_task(safe_background_task(self._flush_events(), "analytics_flush"))

    async def _flush_events(self):
        async with self.buffer_lock:
            if not self.event_buffer:
                return
            events_to_flush = list(self.event_buffer)
            self.event_buffer.clear()

        try:
            async with AsyncSessionLocal() as session:
                for event_data in events_to_flush:
                    play_event = PlayEvent(
                        user_id=event_data["user_id"],
                        track_id=event_data["track_id"],
                        session_id=event_data["session_id"],
                        device_id=event_data["device_id"],
                        event_type=event_data["event_type"],
                        skip_reason=event_data["skip_reason"],
                        started_at=datetime.fromisoformat(event_data["started_at"]),
                        duration_ms=event_data["duration_ms"],
                        completion_pct=event_data["completion_pct"]
                    )
                    session.add(play_event)

                await session.commit()

            await asyncio.gather(*[
                analytics_file_service.append_daily_event(event)
                for event in events_to_flush
            ])

            log_service.analytics(f"Flushed {len(events_to_flush)} play events")
        except Exception as e:
            log_service.error(f"Failed to flush events: {e}")

    async def aggregate_track_analytics(self, track_id: str):
        async with self.aggregate_semaphore:
            try:
                async with AsyncSessionLocal() as session:
                    existing_analytics = await session.get(TrackAnalytics, track_id)

                    result = await session.execute(
                        select(PlayEvent).where(PlayEvent.track_id == track_id)
                    )
                    events = result.scalars().all()

                    if not events:
                        return

                    if existing_analytics and existing_analytics.updated_at is not None:
                        new_events = [e for e in events if e.started_at > existing_analytics.updated_at]  # type: ignore
                        if not new_events:
                            return

                    total_plays = len([e for e in events if str(e.event_type) == "play"])
                    unique_listeners = len(set(e.user_id for e in events if e.user_id is not None))
                    last_played = max((e.started_at for e in events), default=None)
                    skip_count = len([e for e in events if str(e.event_type) == "skip"])
                    completions = [e.completion_pct for e in events if e.completion_pct is not None]
                    avg_completion = sum(completions) / len(completions) if completions else 0.0
                    skip_rate = skip_count / total_plays if total_plays > 0 else 0.0

                    pref_result = await session.execute(
                        select(TrackPreference).where(TrackPreference.track_id == track_id)
                    )
                    preferences = pref_result.scalars().all()
                    like_count = len([p for p in preferences if str(p.preference_type) == str(PreferenceType.LIKE)])
                    superlike_count = len([p for p in preferences if str(p.preference_type) == str(PreferenceType.SUPER_LIKE)])
                    ban_count = len([p for p in preferences if str(p.preference_type) == str(PreferenceType.BAN)])

                    daily_plays = {}
                    weekly_plays = {}
                    for event in events:
                        day_key = event.started_at.strftime("%Y-%m-%d")
                        week_key = event.started_at.strftime("%Y-W%U")
                        daily_plays[day_key] = daily_plays.get(day_key, 0) + 1
                        weekly_plays[week_key] = weekly_plays.get(week_key, 0) + 1

                    popularity_score = (
                        (total_plays * 0.4) +
                        (like_count * 0.3) +
                        (superlike_count * 0.5) -
                        (ban_count * 0.8) -
                        (skip_count * 0.2)
                    )

                    if existing_analytics:
                        existing_analytics.total_plays = total_plays  # type: ignore
                        existing_analytics.unique_listeners = unique_listeners  # type: ignore
                        existing_analytics.last_played = last_played  # type: ignore
                        existing_analytics.like_count = like_count  # type: ignore
                        existing_analytics.superlike_count = superlike_count  # type: ignore
                        existing_analytics.ban_count = ban_count  # type: ignore
                        existing_analytics.avg_completion_pct = avg_completion  # type: ignore
                        existing_analytics.skip_count = skip_count  # type: ignore
                        existing_analytics.skip_rate = skip_rate  # type: ignore
                        existing_analytics.daily_plays = json.dumps(daily_plays)  # type: ignore
                        existing_analytics.weekly_plays = json.dumps(weekly_plays)  # type: ignore
                        existing_analytics.popularity_score = popularity_score  # type: ignore
                        existing_analytics.updated_at = datetime.now(timezone.utc)  # type: ignore
                    else:
                        analytics = TrackAnalytics(
                            track_id=track_id,
                            total_plays=total_plays,
                            unique_listeners=unique_listeners,
                            last_played=last_played,
                            like_count=like_count,
                            superlike_count=superlike_count,
                            ban_count=ban_count,
                            avg_completion_pct=avg_completion,
                            skip_count=skip_count,
                            skip_rate=skip_rate,
                            daily_plays=json.dumps(daily_plays),
                            weekly_plays=json.dumps(weekly_plays),
                            popularity_score=popularity_score
                        )
                        session.add(analytics)

                    await session.commit()

                analytics_data = {
                    "track_id": track_id,
                    "total_plays": total_plays,
                    "unique_listeners": unique_listeners,
                    "last_played": last_played.isoformat() if last_played is not None else None,
                    "engagement": {
                        "likes": like_count,
                        "superlikes": superlike_count,
                        "bans": ban_count
                    },
                    "quality": {
                        "avg_completion_pct": round(float(avg_completion), 2) if isinstance(avg_completion, (int, float)) else 0.0,
                        "skip_count": skip_count,
                        "skip_rate": round(float(skip_rate), 4) if isinstance(skip_rate, (int, float)) else 0.0
                    },
                    "time_series": {
                        "daily": daily_plays,
                        "weekly": weekly_plays
                    },
                    "popularity_score": round(float(popularity_score), 2),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }

                await analytics_file_service.write_track_analytics(track_id, analytics_data)

            except Exception as e:
                log_service.error(f"Failed to aggregate analytics for track {track_id}: {e}")

    async def aggregate_all(self, full_rebuild: bool = False):
        """
        Aggregate analytics for all tracks with recent play events.
        
        Args:
            full_rebuild: If True, re-aggregate ALL tracks with any play events.
                         If False, only process tracks with events since last aggregation.
        """
        try:
            async with AsyncSessionLocal() as session:
                if full_rebuild:
                    # Full rebuild: get ALL track IDs that have any play events
                    result = await session.execute(
                        select(PlayEvent.track_id).distinct()
                    )
                    all_ids = [row[0] for row in result.all()]
                    log_service.analytics(f"Starting FULL analytics rebuild for {len(all_ids)} tracks...")
                else:
                    # Incremental: get track IDs with events since last analytics update
                    # Join with TrackAnalytics to find tracks with new events
                    result = await session.execute(
                        select(PlayEvent.track_id)
                        .outerjoin(TrackAnalytics, PlayEvent.track_id == TrackAnalytics.track_id)
                        .where(
                            (TrackAnalytics.track_id.is_(None)) |  # No analytics yet
                            (PlayEvent.started_at > TrackAnalytics.updated_at)  # New events since last update
                        )
                        .distinct()
                    )
                    all_ids = [row[0] for row in result.all()]

            if not all_ids:
                return

            track_ids = [item_id for item_id in all_ids if '_' not in item_id or not item_id.split('_')[0].isdigit()]
            shoutout_ids = [item_id for item_id in all_ids if '_' in item_id and item_id.split('_')[0].isdigit()]

            tasks = []
            if track_ids:
                log_service.analytics(f"Aggregating analytics for {len(track_ids)} tracks...")
                tasks.extend([self.aggregate_track_analytics(track_id) for track_id in track_ids])

            if shoutout_ids:
                log_service.analytics(f"Aggregating analytics for {len(shoutout_ids)} shoutouts...")
                tasks.extend([self.aggregate_shoutout_analytics(shoutout_id) for shoutout_id in shoutout_ids])

            if tasks:
                await asyncio.gather(*tasks)

            await self._load_cache()
            log_service.analytics(f"✓ Aggregated analytics for {len(track_ids)} tracks, {len(shoutout_ids)} shoutouts")
        except Exception as e:
            log_service.error(f"Failed to aggregate all analytics: {e}")

    async def get_top_hits(self, period: str = "all", limit: int = 50) -> List[Dict]:
        """
        Get top hits for a period. Simple time expansion if needed: day -> week -> all
        """
        cache_key = f"top_hits_{period}"

        async with self.cache_lock:
            if cache_key in self.cache and self.cache[cache_key]:
                cache_age = datetime.now(timezone.utc) - self.last_cache_update
                if cache_age < self.cache_ttl:
                    return self.cache[cache_key][:limit]

        try:
            async with AsyncSessionLocal() as session:
                hits = await self._fetch_top_hits_for_period(session, period, limit)
                
                # Simple time expansion: day -> week -> all
                if len(hits) < limit and period == "day":
                    existing_ids = {h["track_id"] for h in hits}
                    week_hits = await self._fetch_top_hits_for_period(session, "week", limit)
                    for hit in week_hits:
                        if hit["track_id"] not in existing_ids:
                            hits.append(hit)
                            existing_ids.add(hit["track_id"])
                            if len(hits) >= limit:
                                break
                
                if len(hits) < limit and period in ["day", "week"]:
                    existing_ids = {h["track_id"] for h in hits}
                    all_hits = await self._fetch_top_hits_for_period(session, "all", limit)
                    for hit in all_hits:
                        if hit["track_id"] not in existing_ids:
                            hits.append(hit)
                            if len(hits) >= limit:
                                break
                
                return hits
        except Exception as e:
            log_service.error(f"Failed to get top hits for {period}: {e}")
            return []
    
    async def _fetch_top_hits_for_period(self, session, period: str, limit: int) -> List[Dict]:
        """Fetch top hits for a specific period without fallback."""
        query = select(TrackAnalytics).order_by(TrackAnalytics.popularity_score.desc())

        if period == "week":
            week_ago = datetime.now(timezone.utc) - timedelta(days=7)
            query = query.where(TrackAnalytics.last_played >= week_ago)
        elif period == "day":
            day_ago = datetime.now(timezone.utc) - timedelta(days=1)
            query = query.where(TrackAnalytics.last_played >= day_ago)
        # "all" has no time filter

        result = await session.execute(query.limit(limit))
        analytics = result.scalars().all()

        return [{
            "track_id": str(a.track_id) if a.track_id is not None else "",
            "total_plays": a.total_plays if a.total_plays is not None else 0,
            "unique_listeners": a.unique_listeners if a.unique_listeners is not None else 0,
            "popularity_score": round(float(a.popularity_score), 2) if isinstance(a.popularity_score, (int, float)) else 0.0,
            "likes": a.like_count if a.like_count is not None else 0,
            "superlikes": a.superlike_count if a.superlike_count is not None else 0
        } for a in analytics]
    async def get_track_stats(self, track_id: str) -> Optional[Dict]:
        async with self.cache_lock:
            if track_id in self.cache["track_stats"]:
                return self.cache["track_stats"][track_id]

        try:
            async with AsyncSessionLocal() as session:
                analytics = await session.get(TrackAnalytics, track_id)
                if not analytics:
                    return None

                result = await session.execute(
                    select(func.count(TrackAnalytics.track_id))  # pylint: disable=E1102
                    .where(TrackAnalytics.popularity_score < analytics.popularity_score)
                )
                tracks_below = result.scalar() or 0

                result = await session.execute(
                    select(func.count(TrackAnalytics.track_id))  # pylint: disable=E1102
                )
                total_tracks = result.scalar() or 1

                percentile = (tracks_below / total_tracks * 100) if total_tracks > 0 else 0

                stats = {
                    "track_id": track_id,
                    "total_plays": analytics.total_plays,
                    "unique_listeners": analytics.unique_listeners,
                    "last_played": analytics.last_played.isoformat() if analytics.last_played is not None else None,
                    "likes": analytics.like_count,
                    "superlikes": analytics.superlike_count,
                    "bans": analytics.ban_count,
                    "avg_completion_pct": round(float(analytics.avg_completion_pct), 2) if isinstance(analytics.avg_completion_pct, (int, float)) else 0.0,
                    "skip_count": analytics.skip_count if analytics.skip_count is not None else 0,
                    "skip_rate": round(float(analytics.skip_rate), 4) if isinstance(analytics.skip_rate, (int, float)) else 0.0,
                    "popularity_score": round(float(analytics.popularity_score), 2) if isinstance(analytics.popularity_score, (int, float)) else 0.0,
                    "percentile": round(percentile, 1)
                }

                async with self.cache_lock:
                    self.cache["track_stats"][track_id] = stats

                return stats
        except Exception as e:
            log_service.error(f"Failed to get stats for track {track_id}: {e}")
            return None

    async def aggregate_shoutout_analytics(self, shoutout_id: str):
        async with self.aggregate_semaphore:
            try:
                async with AsyncSessionLocal() as session:
                    existing_analytics = await session.get(ShoutoutAnalytics, shoutout_id)

                    result = await session.execute(
                        select(PlayEvent).where(PlayEvent.track_id == shoutout_id)
                    )
                    events = result.scalars().all()

                    if not events:
                        return

                    if existing_analytics and existing_analytics.updated_at is not None:
                        new_events = [e for e in events if e.started_at > existing_analytics.updated_at]  # type: ignore
                        if not new_events:
                            return

                    total_plays = len([e for e in events if str(e.event_type) == "play"])
                    unique_listeners = len(set(e.user_id for e in events if e.user_id is not None))
                    last_played = max((e.started_at for e in events), default=None)
                    skip_count = len([e for e in events if str(e.event_type) == "skip"])
                    completions = [e.completion_pct for e in events if e.completion_pct is not None]
                    avg_completion = sum(completions) / len(completions) if completions else 0.0
                    skip_rate = skip_count / total_plays if total_plays > 0 else 0.0

                    pref_result = await session.execute(
                        select(ShoutoutPreference).where(ShoutoutPreference.shoutout_id == shoutout_id)
                    )
                    preferences = pref_result.scalars().all()
                    like_count = len([p for p in preferences if str(p.preference_type) == str(ShoutoutPreferenceType.LIKE)])
                    superlike_count = len([p for p in preferences if str(p.preference_type) == str(ShoutoutPreferenceType.SUPER_LIKE)])
                    ban_count = len([p for p in preferences if str(p.preference_type) == str(ShoutoutPreferenceType.BAN)])

                    daily_plays = {}
                    weekly_plays = {}
                    for event in events:
                        day_key = event.started_at.strftime("%Y-%m-%d")
                        week_key = event.started_at.strftime("%Y-W%U")
                        daily_plays[day_key] = daily_plays.get(day_key, 0) + 1
                        weekly_plays[week_key] = weekly_plays.get(week_key, 0) + 1

                    popularity_score = (
                        (total_plays * 0.4) +
                        (like_count * 0.3) +
                        (superlike_count * 0.5) -
                        (ban_count * 0.8) -
                        (skip_count * 0.2)
                    )

                    if existing_analytics:
                        existing_analytics.total_plays = total_plays  # type: ignore
                        existing_analytics.unique_listeners = unique_listeners  # type: ignore
                        existing_analytics.last_played = last_played  # type: ignore
                        existing_analytics.like_count = like_count  # type: ignore
                        existing_analytics.super_like_count = superlike_count  # type: ignore
                        existing_analytics.ban_count = ban_count  # type: ignore
                        existing_analytics.avg_completion_pct = avg_completion  # type: ignore
                        existing_analytics.skip_count = skip_count  # type: ignore
                        existing_analytics.skip_rate = skip_rate  # type: ignore
                        existing_analytics.daily_plays = json.dumps(daily_plays)  # type: ignore
                        existing_analytics.weekly_plays = json.dumps(weekly_plays)  # type: ignore
                        existing_analytics.popularity_score = popularity_score  # type: ignore
                        existing_analytics.updated_at = datetime.now(timezone.utc)  # type: ignore
                    else:
                        analytics = ShoutoutAnalytics(
                            shoutout_id=shoutout_id,
                            total_plays=total_plays,
                            unique_listeners=unique_listeners,
                            last_played=last_played,
                            like_count=like_count,
                            super_like_count=superlike_count,
                            ban_count=ban_count,
                            avg_completion_pct=avg_completion,
                            skip_count=skip_count,
                            skip_rate=skip_rate,
                            daily_plays=json.dumps(daily_plays),
                            weekly_plays=json.dumps(weekly_plays),
                            popularity_score=popularity_score
                        )
                        session.add(analytics)

                    await session.commit()

            except Exception as e:
                log_service.error(f"Failed to aggregate analytics for shoutout {shoutout_id}: {e}")

    async def get_top_shoutouts(self, period: str = "all", limit: int = 50) -> List[Dict]:
        cache_key = f"top_shoutouts_{period}"

        async with self.cache_lock:
            if cache_key in self.cache and self.cache[cache_key]:
                cache_age = datetime.now(timezone.utc) - self.last_cache_update
                if cache_age < self.cache_ttl:
                    return self.cache[cache_key][:limit]

        try:
            async with AsyncSessionLocal() as session:
                query = select(ShoutoutAnalytics).order_by(ShoutoutAnalytics.popularity_score.desc())

                if period == "week":
                    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
                    query = query.where(ShoutoutAnalytics.last_played >= week_ago)
                elif period == "day":
                    day_ago = datetime.now(timezone.utc) - timedelta(days=1)
                    query = query.where(ShoutoutAnalytics.last_played >= day_ago)

                result = await session.execute(query.limit(limit))
                analytics = result.scalars().all()

                shoutouts = [{
                    "shoutout_id": str(a.shoutout_id) if a.shoutout_id is not None else "",
                    "total_plays": a.total_plays if a.total_plays is not None else 0,
                    "unique_listeners": a.unique_listeners if a.unique_listeners is not None else 0,
                    "popularity_score": round(float(a.popularity_score), 2) if isinstance(a.popularity_score, (int, float)) else 0.0,
                    "likes": a.like_count if a.like_count is not None else 0,
                    "superlikes": a.super_like_count if a.super_like_count is not None else 0
                } for a in analytics]

                return shoutouts
        except Exception as e:
            log_service.error(f"Failed to get top shoutouts for {period}: {e}")
            return []

    async def get_shoutout_stats(self, shoutout_id: str) -> Optional[Dict]:
        async with self.cache_lock:
            if shoutout_id in self.cache["shoutout_stats"]:
                return self.cache["shoutout_stats"][shoutout_id]

        try:
            async with AsyncSessionLocal() as session:
                analytics = await session.get(ShoutoutAnalytics, shoutout_id)
                if not analytics:
                    return None

                result = await session.execute(
                    select(func.count(ShoutoutAnalytics.shoutout_id))  # pylint: disable=E1102
                    .where(ShoutoutAnalytics.popularity_score < analytics.popularity_score)
                )
                shoutouts_below = result.scalar() or 0

                result = await session.execute(
                    select(func.count(ShoutoutAnalytics.shoutout_id))  # pylint: disable=E1102
                )
                total_shoutouts = result.scalar() or 1

                percentile = (shoutouts_below / total_shoutouts * 100) if total_shoutouts > 0 else 0

                stats = {
                    "shoutout_id": shoutout_id,
                    "total_plays": analytics.total_plays,
                    "unique_listeners": analytics.unique_listeners,
                    "last_played": analytics.last_played.isoformat() if analytics.last_played is not None else None,
                    "likes": analytics.like_count,
                    "superlikes": analytics.super_like_count,
                    "bans": analytics.ban_count,
                    "avg_completion_pct": round(float(analytics.avg_completion_pct), 2) if isinstance(analytics.avg_completion_pct, (int, float)) else 0.0,
                    "skip_count": analytics.skip_count if analytics.skip_count is not None else 0,
                    "skip_rate": round(float(analytics.skip_rate), 4) if isinstance(analytics.skip_rate, (int, float)) else 0.0,
                    "popularity_score": round(float(analytics.popularity_score), 2) if isinstance(analytics.popularity_score, (int, float)) else 0.0,
                    "percentile": round(percentile, 1)
                }

                async with self.cache_lock:
                    self.cache["shoutout_stats"][shoutout_id] = stats

                return stats
        except Exception as e:
            log_service.error(f"Failed to get stats for shoutout {shoutout_id}: {e}")
            return None

    async def _load_cache(self, silent=False):
        try:
            top_all = await self.get_top_hits("all", 100)
            top_week = await self.get_top_hits("week", 100)
            top_day = await self.get_top_hits("day", 100)

            top_shoutouts_all = await self.get_top_shoutouts("all", 100)
            top_shoutouts_week = await self.get_top_shoutouts("week", 100)
            top_shoutouts_day = await self.get_top_shoutouts("day", 100)

            async with self.cache_lock:
                self.cache["top_hits_all_time"] = top_all
                self.cache["top_hits_week"] = top_week
                self.cache["top_hits_day"] = top_day
                self.cache["top_shoutouts_all_time"] = top_shoutouts_all
                self.cache["top_shoutouts_week"] = top_shoutouts_week
                self.cache["top_shoutouts_day"] = top_shoutouts_day
                self.last_cache_update = datetime.now(timezone.utc)

            if not silent:
                log_service.analytics("Analytics cache refreshed")
        except Exception as e:
            log_service.error(f"Failed to load cache: {e}")

    async def initialize_from_catalog(self):
        try:
            from pathlib import Path
            from config.settings import settings

            catalog_dir = Path(settings.AUDIO_DIR)
            catalog_dir_exists = await asyncio.to_thread(catalog_dir.exists)

            if not catalog_dir_exists:
                log_service.warning(f"Catalog directory not found: {catalog_dir}")
                return

            mp3_files = await asyncio.to_thread(list, catalog_dir.glob("*.mp3"))
            track_ids = [f.stem for f in mp3_files]

            initialized_count = 0
            skipped_count = 0

            for track_id in track_ids:
                async with AsyncSessionLocal() as session:
                    existing = await session.get(TrackAnalytics, track_id)
                    if existing:
                        skipped_count += 1
                        continue

                    pref_result = await session.execute(
                        select(TrackPreference).where(TrackPreference.track_id == track_id)
                    )
                    preferences = pref_result.scalars().all()

                    like_count = len([p for p in preferences if str(p.preference_type) == str(PreferenceType.LIKE)])
                    superlike_count = len([p for p in preferences if str(p.preference_type) == str(PreferenceType.SUPER_LIKE)])
                    ban_count = len([p for p in preferences if str(p.preference_type) == str(PreferenceType.BAN)])

                    popularity_score = (
                        (like_count * 0.3) +
                        (superlike_count * 0.5) -
                        (ban_count * 0.8)
                    )

                    analytics = TrackAnalytics(
                        track_id=track_id,
                        total_plays=0,
                        unique_listeners=0,
                        last_played=None,
                        like_count=like_count,
                        superlike_count=superlike_count,
                        ban_count=ban_count,
                        avg_completion_pct=0.0,
                        skip_count=0,
                        skip_rate=0.0,
                        daily_plays=json.dumps({}),
                        weekly_plays=json.dumps({}),
                        popularity_score=popularity_score
                    )
                    session.add(analytics)
                    await session.commit()

                    initialized_count += 1

            total_tracks = len(track_ids)
            log_service.analytics(f"✓ Analytics initialized: {total_tracks} tracks ({initialized_count} new, {skipped_count} existing)")
            await self._load_cache(silent=True)
        except Exception as e:
            log_service.error(f"Failed to initialize from catalog: {e}")

    async def start_background_tasks(self):
        if self._running:
            return

        self._running = True
        asyncio.create_task(self._flush_loop())
        asyncio.create_task(self._aggregate_loop())
        log_service.analytics("Analytics background tasks started")

    async def _flush_loop(self):
        while self._running:
            await asyncio.sleep(self.flush_interval)
            await self._flush_events()

    async def _aggregate_loop(self):
        full_rebuild_counter = 0
        while self._running:
            await asyncio.sleep(self.aggregate_interval)
            
            # Every 6th cycle (30 minutes by default), do a full rebuild
            full_rebuild_counter += 1
            do_full_rebuild = full_rebuild_counter >= 6
            
            await self.aggregate_all(full_rebuild=do_full_rebuild)
            
            if do_full_rebuild:
                full_rebuild_counter = 0

    async def stop_background_tasks(self):
        self._running = False
        await self._flush_events()
        log_service.analytics("Analytics background tasks stopped")

analytics_service = AnalyticsService()