import asyncio
import json
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone
from collections import deque
from sqlalchemy import select, func, case, cast, String
from database import AsyncSessionLocal, PlayEvent, TrackAnalytics, TrackPreference, PreferenceType
from database.models import ShoutoutAnalytics, ShoutoutPreference, ShoutoutPreferenceType
from services import log_service
from services.base_service import SingletonService
from services.analytics_file_service import analytics_file_service
from services.task_utils import spawn

ANALYTICS_MAX_FLUSH_ATTEMPTS = 5
ANALYTICS_CACHE_SIZE = 100
AGGREGATE_CHUNK = 500
TOP_HITS_PERIOD_DAYS = {"week": 7, "day": 1}

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
            "top_hits_all": [],
            "top_hits_week": [],
            "top_hits_day": [],
            "track_stats": {},
            "top_shoutouts_all": [],
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
        self._aggregated_event_id = 0
        self._running = False
        self._flush_task: Optional[asyncio.Task] = None
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

        if len(self.event_buffer) >= 50 and (self._flush_task is None or self._flush_task.done()):
            self._flush_task = spawn(safe_background_task(self._flush_events(), "analytics_flush"), name="analytics_flush")

    async def _flush_events(self):
        async with self.buffer_lock:
            if not self.event_buffer:
                return
            events_to_flush = list(self.event_buffer)
            self.event_buffer.clear()

        from services_radio.pulse import get_pulse
        pulse = get_pulse()
        if pulse is not None:
            for event_data in events_to_flush:
                if "region_key" not in event_data:
                    event_data["region_key"] = await pulse.region_key_for(event_data["user_id"],
                                                                          event_data["session_id"])

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
                        completion_pct=event_data["completion_pct"],
                        region_key=event_data.get("region_key")
                    )
                    session.add(play_event)

                await session.commit()
        except Exception as e:
            await self._requeue_events(events_to_flush)
            log_service.error(f"Failed to flush events (re-queued for retry): {e}")
            return

        try:
            await analytics_file_service.append_daily_events(
                {k: v for k, v in event.items() if k != "_flush_attempts"}
                for event in events_to_flush
            )
        except Exception as e:
            log_service.error(f"Failed to append flushed events to daily file: {e}")

        log_service.analytics(f"Flushed {len(events_to_flush)} play events")

    async def _requeue_events(self, events: List[Dict]):
        retry_events = []
        dropped = 0
        for event in events:
            attempts = event.get("_flush_attempts", 0) + 1
            if attempts >= ANALYTICS_MAX_FLUSH_ATTEMPTS:
                dropped += 1
                continue
            event["_flush_attempts"] = attempts
            retry_events.append(event)

        if dropped:
            log_service.error(f"Dropping {dropped} play events after {ANALYTICS_MAX_FLUSH_ATTEMPTS} failed flush attempts")

        async with self.buffer_lock:
            self.event_buffer = deque(retry_events + list(self.event_buffer), maxlen=self.event_buffer.maxlen)

    @staticmethod
    def _day_bucket(session):
        bind = session.bind
        if bind is not None and bind.dialect.name == "postgresql":
            return func.to_char(func.timezone("UTC", PlayEvent.started_at), "YYYY-MM-DD")
        return func.date(PlayEvent.started_at)

    async def _aggregate_event_stats(self, session, item_id: str) -> Optional[Dict]:
        result = await session.execute(
            select(
                func.count(PlayEvent.id),  # pylint: disable=E1102
                func.sum(case((PlayEvent.event_type == "play", 1), else_=0)),
                func.sum(case((PlayEvent.event_type == "skip", 1), else_=0)),
                func.count(func.distinct(func.coalesce(cast(PlayEvent.user_id, String), PlayEvent.session_id))),  # pylint: disable=E1102
                func.max(PlayEvent.started_at),
                func.avg(PlayEvent.completion_pct)
            ).where(PlayEvent.track_id == item_id)
        )
        event_count, total_plays, skip_count, unique_listeners, last_played, avg_completion = result.one()
        if not event_count:
            return None

        day_bucket = self._day_bucket(session)
        day_result = await session.execute(
            select(day_bucket, func.count(PlayEvent.id))  # pylint: disable=E1102
            .where(PlayEvent.track_id == item_id)
            .group_by(day_bucket)
        )
        daily_plays = {}
        weekly_plays = {}
        for day, count in day_result.all():
            day_key = str(day)[:10]
            week_key = datetime.strptime(day_key, "%Y-%m-%d").strftime("%Y-W%U")
            daily_plays[day_key] = daily_plays.get(day_key, 0) + int(count)
            weekly_plays[week_key] = weekly_plays.get(week_key, 0) + int(count)

        total_plays = int(total_plays or 0)
        skip_count = int(skip_count or 0)
        return {
            "total_plays": total_plays,
            "skip_count": skip_count,
            "unique_listeners": int(unique_listeners or 0),
            "last_played": last_played,
            "avg_completion": float(avg_completion) if avg_completion is not None else 0.0,
            "skip_rate": skip_count / total_plays if total_plays > 0 else 0.0,
            "daily_plays": daily_plays,
            "weekly_plays": weekly_plays
        }

    @staticmethod
    async def _count_preferences(session, model, id_column, item_id: str) -> Dict:
        result = await session.execute(
            select(model.preference_type, func.count(model.id))  # pylint: disable=E1102
            .where(id_column == item_id)
            .group_by(model.preference_type)
        )
        return {pref_type: int(count) for pref_type, count in result.all()}

    @staticmethod
    def _popularity_score(total_plays: int, like_count: int, superlike_count: int, ban_count: int, skip_count: int) -> float:
        return (
            (total_plays * 0.4) +
            (like_count * 0.3) +
            (superlike_count * 0.5) -
            (ban_count * 0.8) -
            (skip_count * 0.2)
        )

    async def aggregate_track_analytics(self, track_id: str) -> bool:
        async with self.aggregate_semaphore:
            try:
                async with AsyncSessionLocal() as session:
                    stats = await self._aggregate_event_stats(session, track_id)
                    if stats is None:
                        return True

                    pref_counts = await self._count_preferences(session, TrackPreference, TrackPreference.track_id, track_id)
                    like_count = pref_counts.get(PreferenceType.LIKE, 0)
                    superlike_count = pref_counts.get(PreferenceType.SUPER_LIKE, 0)
                    ban_count = pref_counts.get(PreferenceType.BAN, 0)

                    total_plays = stats["total_plays"]
                    unique_listeners = stats["unique_listeners"]
                    last_played = stats["last_played"]
                    skip_count = stats["skip_count"]
                    skip_rate = stats["skip_rate"]
                    avg_completion = stats["avg_completion"]
                    daily_plays = stats["daily_plays"]
                    weekly_plays = stats["weekly_plays"]
                    popularity_score = self._popularity_score(total_plays, like_count, superlike_count, ban_count, skip_count)

                    existing_analytics = await session.get(TrackAnalytics, track_id)
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
                        "avg_completion_pct": round(float(avg_completion), 2),
                        "skip_count": skip_count,
                        "skip_rate": round(float(skip_rate), 4)
                    },
                    "time_series": {
                        "daily": daily_plays,
                        "weekly": weekly_plays
                    },
                    "popularity_score": round(float(popularity_score), 2),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }

                await analytics_file_service.write_track_analytics(track_id, analytics_data)
                return True

            except Exception as e:
                log_service.error(f"Failed to aggregate analytics for track {track_id}: {e}")
                return False

    async def _aggregate_tracks_bulk(self, track_ids: List[str]) -> bool:
        try:
            changed = 0
            for start in range(0, len(track_ids), AGGREGATE_CHUNK):
                changed += await self._aggregate_track_chunk(track_ids[start:start + AGGREGATE_CHUNK])
            if changed:
                log_service.analytics(f"Track analytics changed for {changed} of {len(track_ids)} tracks")
            return True
        except Exception as e:
            log_service.error(f"Failed to aggregate track analytics: {e}")
            return False

    async def _aggregate_track_chunk(self, chunk: List[str]) -> int:
        written = []
        async with AsyncSessionLocal() as session:
            stats_rows = await session.execute(
                select(
                    PlayEvent.track_id,
                    func.count(PlayEvent.id),  # pylint: disable=E1102
                    func.sum(case((PlayEvent.event_type == "play", 1), else_=0)),
                    func.sum(case((PlayEvent.event_type == "skip", 1), else_=0)),
                    func.count(func.distinct(func.coalesce(cast(PlayEvent.user_id, String), PlayEvent.session_id))),  # pylint: disable=E1102
                    func.max(PlayEvent.started_at),
                    func.avg(PlayEvent.completion_pct)
                ).where(PlayEvent.track_id.in_(chunk)).group_by(PlayEvent.track_id)
            )
            day_bucket = self._day_bucket(session)
            day_rows = await session.execute(
                select(PlayEvent.track_id, day_bucket, func.count(PlayEvent.id))  # pylint: disable=E1102
                .where(PlayEvent.track_id.in_(chunk))
                .group_by(PlayEvent.track_id, day_bucket)
            )
            pref_rows = await session.execute(
                select(TrackPreference.track_id, TrackPreference.preference_type, func.count(TrackPreference.id))  # pylint: disable=E1102
                .where(TrackPreference.track_id.in_(chunk))
                .group_by(TrackPreference.track_id, TrackPreference.preference_type)
            )
            existing_rows = await session.execute(select(TrackAnalytics).where(TrackAnalytics.track_id.in_(chunk)))
            existing = {row.track_id: row for row in existing_rows.scalars()}

            days: Dict[str, List] = {}
            for track_id, day, count in day_rows.all():
                days.setdefault(track_id, []).append((day, count))
            prefs: Dict[str, Dict] = {}
            for track_id, pref_type, count in pref_rows.all():
                prefs.setdefault(track_id, {})[pref_type] = int(count)

            now = datetime.now(timezone.utc)
            for track_id, event_count, total_plays, skip_count, unique_listeners, last_played, avg_completion in stats_rows.all():
                if not event_count:
                    continue
                daily_plays: Dict[str, int] = {}
                weekly_plays: Dict[str, int] = {}
                for day, count in days.get(track_id, []):
                    day_key = str(day)[:10]
                    week_key = datetime.strptime(day_key, "%Y-%m-%d").strftime("%Y-W%U")
                    daily_plays[day_key] = daily_plays.get(day_key, 0) + int(count)
                    weekly_plays[week_key] = weekly_plays.get(week_key, 0) + int(count)
                total_plays = int(total_plays or 0)
                skip_count = int(skip_count or 0)
                pref_counts = prefs.get(track_id, {})
                like_count = pref_counts.get(PreferenceType.LIKE, 0)
                superlike_count = pref_counts.get(PreferenceType.SUPER_LIKE, 0)
                ban_count = pref_counts.get(PreferenceType.BAN, 0)
                values = {
                    "total_plays": total_plays,
                    "unique_listeners": int(unique_listeners or 0),
                    "last_played": last_played,
                    "like_count": like_count,
                    "superlike_count": superlike_count,
                    "ban_count": ban_count,
                    "avg_completion_pct": float(avg_completion) if avg_completion is not None else 0.0,
                    "skip_count": skip_count,
                    "skip_rate": skip_count / total_plays if total_plays > 0 else 0.0,
                    "popularity_score": self._popularity_score(total_plays, like_count, superlike_count, ban_count, skip_count),
                }

                row = existing.get(track_id)
                if row is None:
                    session.add(TrackAnalytics(track_id=track_id, daily_plays=json.dumps(daily_plays),
                                               weekly_plays=json.dumps(weekly_plays), **values))
                elif (any(getattr(row, key) != value for key, value in values.items())
                      or json.loads(row.daily_plays or "{}") != daily_plays
                      or json.loads(row.weekly_plays or "{}") != weekly_plays):
                    for key, value in values.items():
                        setattr(row, key, value)
                    row.daily_plays = json.dumps(daily_plays)  # type: ignore
                    row.weekly_plays = json.dumps(weekly_plays)  # type: ignore
                    row.updated_at = now  # type: ignore
                else:
                    continue
                written.append((track_id, values, daily_plays, weekly_plays))

            if written:
                await session.commit()

        for track_id, values, daily_plays, weekly_plays in written:
            await analytics_file_service.write_track_analytics(track_id, {
                "track_id": track_id,
                "total_plays": values["total_plays"],
                "unique_listeners": values["unique_listeners"],
                "last_played": values["last_played"].isoformat() if values["last_played"] is not None else None,
                "engagement": {
                    "likes": values["like_count"],
                    "superlikes": values["superlike_count"],
                    "bans": values["ban_count"]
                },
                "quality": {
                    "avg_completion_pct": round(float(values["avg_completion_pct"]), 2),
                    "skip_count": values["skip_count"],
                    "skip_rate": round(float(values["skip_rate"]), 4)
                },
                "time_series": {
                    "daily": daily_plays,
                    "weekly": weekly_plays
                },
                "popularity_score": round(float(values["popularity_score"]), 2),
                "updated_at": datetime.now(timezone.utc).isoformat()
            })
        return len(written)

    async def aggregate_all(self, full_rebuild: bool = False):
        try:
            async with AsyncSessionLocal() as session:
                max_event_id = (await session.execute(select(func.max(PlayEvent.id)))).scalar()
                if max_event_id is None:
                    return

                query = select(PlayEvent.track_id).where(PlayEvent.id <= max_event_id).distinct()
                if full_rebuild:
                    result = await session.execute(query)
                    all_ids = [row[0] for row in result.all()]
                    log_service.analytics(f"Starting FULL analytics rebuild for {len(all_ids)} items...")
                else:
                    result = await session.execute(query.where(PlayEvent.id > self._aggregated_event_id))
                    all_ids = [row[0] for row in result.all()]

            if not all_ids:
                self._aggregated_event_id = max(self._aggregated_event_id, max_event_id)
                return

            track_ids = [item_id for item_id in all_ids if '_' not in item_id or not item_id.split('_')[0].isdigit()]
            shoutout_ids = [item_id for item_id in all_ids if '_' in item_id and item_id.split('_')[0].isdigit()]

            tasks = []
            if track_ids:
                log_service.analytics(f"Aggregating analytics for {len(track_ids)} tracks...")
                tasks.append(self._aggregate_tracks_bulk(track_ids))

            if shoutout_ids:
                log_service.analytics(f"Aggregating analytics for {len(shoutout_ids)} shoutouts...")
                tasks.extend([self.aggregate_shoutout_analytics(shoutout_id) for shoutout_id in shoutout_ids])

            results = await asyncio.gather(*tasks) if tasks else []
            if all(results):
                self._aggregated_event_id = max(self._aggregated_event_id, max_event_id)

            await self._invalidate_cache()
            await self._load_cache()
            log_service.analytics(f"✓ Aggregated analytics for {len(track_ids)} tracks, {len(shoutout_ids)} shoutouts")
        except Exception as e:
            log_service.error(f"Failed to aggregate all analytics: {e}")

    def _cached_list(self, cache_key: str, limit: int) -> Optional[List[Dict]]:
        cached = self.cache.get(cache_key)
        if not cached:
            return None
        if datetime.now(timezone.utc) - self.last_cache_update >= self.cache_ttl:
            return None
        if limit > len(cached) and len(cached) >= ANALYTICS_CACHE_SIZE:
            return None
        return cached[:limit]

    async def get_top_hits(self, period: str = "all", limit: int = 50) -> List[Dict]:
        async with self.cache_lock:
            cached = self._cached_list(f"top_hits_{period}", limit)
            if cached is not None:
                return cached

        return await self._query_top_hits(period, limit)

    async def _query_top_hits(self, period: str, limit: int) -> List[Dict]:
        try:
            async with AsyncSessionLocal() as session:
                return await self._ranked_with_fallback(session, self._fetch_top_hits_for_period, "track_id", period, limit)
        except Exception as e:
            log_service.error(f"Failed to get top hits for {period}: {e}")
            return []

    @staticmethod
    async def _ranked_with_fallback(session, fetch, id_key: str, period: str, limit: int) -> List[Dict]:
        hits = await fetch(session, period, limit)
        fallbacks = {"day": ["week", "all"], "week": ["all"]}.get(period, [])
        existing_ids = {h[id_key] for h in hits}
        for fallback_period in fallbacks:
            if len(hits) >= limit:
                break
            for hit in await fetch(session, fallback_period, limit):
                if hit[id_key] not in existing_ids:
                    hits.append(hit)
                    existing_ids.add(hit[id_key])
                    if len(hits) >= limit:
                        break
        return hits

    async def _fetch_top_hits_for_period(self, session, period: str, limit: int) -> List[Dict]:
        if period in TOP_HITS_PERIOD_DAYS:
            cutoff = datetime.now(timezone.utc) - timedelta(days=TOP_HITS_PERIOD_DAYS[period])
            period_plays = func.count(PlayEvent.id)  # pylint: disable=E1102
            query = (
                select(TrackAnalytics)
                .join(PlayEvent, PlayEvent.track_id == TrackAnalytics.track_id)
                .where(PlayEvent.event_type == "play", PlayEvent.started_at >= cutoff)
                .group_by(TrackAnalytics.track_id)
                .order_by(period_plays.desc(), TrackAnalytics.popularity_score.desc(), TrackAnalytics.track_id)
            )
        else:
            query = select(TrackAnalytics).order_by(TrackAnalytics.popularity_score.desc())

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

    async def aggregate_shoutout_analytics(self, shoutout_id: str) -> bool:
        async with self.aggregate_semaphore:
            try:
                async with AsyncSessionLocal() as session:
                    stats = await self._aggregate_event_stats(session, shoutout_id)
                    if stats is None:
                        return True

                    pref_counts = await self._count_preferences(session, ShoutoutPreference, ShoutoutPreference.shoutout_id, shoutout_id)
                    like_count = pref_counts.get(ShoutoutPreferenceType.LIKE, 0)
                    superlike_count = pref_counts.get(ShoutoutPreferenceType.SUPER_LIKE, 0)
                    ban_count = pref_counts.get(ShoutoutPreferenceType.BAN, 0)

                    total_plays = stats["total_plays"]
                    unique_listeners = stats["unique_listeners"]
                    last_played = stats["last_played"]
                    skip_count = stats["skip_count"]
                    skip_rate = stats["skip_rate"]
                    avg_completion = stats["avg_completion"]
                    daily_plays = stats["daily_plays"]
                    weekly_plays = stats["weekly_plays"]
                    popularity_score = self._popularity_score(total_plays, like_count, superlike_count, ban_count, skip_count)

                    existing_analytics = await session.get(ShoutoutAnalytics, shoutout_id)
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
                return True

            except Exception as e:
                log_service.error(f"Failed to aggregate analytics for shoutout {shoutout_id}: {e}")
                return False

    async def get_top_shoutouts(self, period: str = "all", limit: int = 50) -> List[Dict]:
        async with self.cache_lock:
            cached = self._cached_list(f"top_shoutouts_{period}", limit)
            if cached is not None:
                return cached

        return await self._query_top_shoutouts(period, limit)

    async def _query_top_shoutouts(self, period: str, limit: int) -> List[Dict]:
        try:
            async with AsyncSessionLocal() as session:
                return await self._ranked_with_fallback(session, self._fetch_top_shoutouts_for_period, "shoutout_id", period, limit)
        except Exception as e:
            log_service.error(f"Failed to get top shoutouts for {period}: {e}")
            return []

    async def _fetch_top_shoutouts_for_period(self, session, period: str, limit: int) -> List[Dict]:
        if period in TOP_HITS_PERIOD_DAYS:
            cutoff = datetime.now(timezone.utc) - timedelta(days=TOP_HITS_PERIOD_DAYS[period])
            period_plays = func.count(PlayEvent.id)  # pylint: disable=E1102
            query = (
                select(ShoutoutAnalytics)
                .join(PlayEvent, PlayEvent.track_id == ShoutoutAnalytics.shoutout_id)
                .where(PlayEvent.event_type == "play", PlayEvent.started_at >= cutoff)
                .group_by(ShoutoutAnalytics.shoutout_id)
                .order_by(period_plays.desc(), ShoutoutAnalytics.popularity_score.desc(), ShoutoutAnalytics.shoutout_id)
            )
        else:
            query = select(ShoutoutAnalytics).order_by(ShoutoutAnalytics.popularity_score.desc())

        result = await session.execute(query.limit(limit))
        analytics = result.scalars().all()

        return [{
            "shoutout_id": str(a.shoutout_id) if a.shoutout_id is not None else "",
            "total_plays": a.total_plays if a.total_plays is not None else 0,
            "unique_listeners": a.unique_listeners if a.unique_listeners is not None else 0,
            "popularity_score": round(float(a.popularity_score), 2) if isinstance(a.popularity_score, (int, float)) else 0.0,
            "likes": a.like_count if a.like_count is not None else 0,
            "superlikes": a.super_like_count if a.super_like_count is not None else 0
        } for a in analytics]

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

    async def _invalidate_cache(self):
        async with self.cache_lock:
            self.cache["track_stats"] = {}
            self.cache["shoutout_stats"] = {}

    async def _load_cache(self, silent=False):
        try:
            top_all = await self._query_top_hits("all", ANALYTICS_CACHE_SIZE)
            top_week = await self._query_top_hits("week", ANALYTICS_CACHE_SIZE)
            top_day = await self._query_top_hits("day", ANALYTICS_CACHE_SIZE)

            top_shoutouts_all = await self._query_top_shoutouts("all", ANALYTICS_CACHE_SIZE)
            top_shoutouts_week = await self._query_top_shoutouts("week", ANALYTICS_CACHE_SIZE)
            top_shoutouts_day = await self._query_top_shoutouts("day", ANALYTICS_CACHE_SIZE)

            async with self.cache_lock:
                self.cache["top_hits_all"] = top_all
                self.cache["top_hits_week"] = top_week
                self.cache["top_hits_day"] = top_day
                self.cache["top_shoutouts_all"] = top_shoutouts_all
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
        spawn(self._flush_loop(), name="analytics_flush_loop")
        spawn(self._aggregate_loop(), name="analytics_aggregate_loop")
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