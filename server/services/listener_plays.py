import math
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from sqlalchemy import func, select

from database import AsyncSessionLocal, PlayEvent
from services import log_service
from services.analytics_service import analytics_service
from services.user_data_cache_service import user_data_cache

LISTEN_MIN_PCT = 50.0
RATING_WEIGHTS = {"super_like": 3.0, "like": 1.0}
SCOPES = ("favorites", "super_likes")


@dataclass
class TrackPlays:
    plays: int = 0
    listens: int = 0
    skips: int = 0
    last_played: Optional[datetime] = None

    def add(self, event_type: str, completion_pct: Optional[float], at: Optional[datetime]) -> None:
        if event_type == "play":
            self.plays += 1
            if at is not None and (self.last_played is None or at > self.last_played):
                self.last_played = at
        elif (completion_pct or 0.0) >= LISTEN_MIN_PCT:
            self.listens += 1
        elif event_type == "skip":
            self.skips += 1


@dataclass
class Loved:
    track_id: str
    rating: str
    weight: float
    plays: TrackPlays


def _owner(user_id: Optional[int], session_id: Optional[str]):
    return PlayEvent.user_id == int(user_id) if user_id else PlayEvent.session_id == session_id


async def track_plays(user_id: Optional[int], session_id: Optional[str],
                      track_ids: Optional[Iterable[str]] = None) -> Dict[str, TrackPlays]:
    if not (user_id or session_id):
        return {}
    wanted = set(track_ids) if track_ids is not None else None
    if wanted is not None and not wanted:
        return {}
    listened = (PlayEvent.event_type != "play") & (PlayEvent.completion_pct >= LISTEN_MIN_PCT)
    skipped = (PlayEvent.event_type == "skip") & (func.coalesce(PlayEvent.completion_pct, 0.0) < LISTEN_MIN_PCT)
    query = (select(PlayEvent.track_id,
                    func.count().filter(PlayEvent.event_type == "play"),
                    func.count().filter(listened),
                    func.count().filter(skipped),
                    func.max(PlayEvent.started_at).filter(PlayEvent.event_type == "play"))
             .where(_owner(user_id, session_id)).group_by(PlayEvent.track_id))
    if wanted is not None:
        query = query.where(PlayEvent.track_id.in_(wanted))
    stats: Dict[str, TrackPlays] = {}
    try:
        async with AsyncSessionLocal() as db:
            for track_id, plays, listens, skips, last in (await db.execute(query)).all():
                stats[track_id] = TrackPlays(int(plays), int(listens), int(skips), last)
    except Exception as e:
        log_service.warning(f"[PLAYS] Play counts failed for {log_service.who(session_id)}: {type(e).__name__}: {e}")
    for event in list(analytics_service.event_buffer):
        if (event.get("user_id") != int(user_id)) if user_id else (event.get("session_id") != session_id):
            continue
        if wanted is not None and event["track_id"] not in wanted:
            continue
        stats.setdefault(event["track_id"], TrackPlays()).add(
            event["event_type"], event.get("completion_pct"), datetime.fromisoformat(event["started_at"]))
    return stats


def love(rating: str, plays: Optional[TrackPlays]) -> float:
    plays = plays or TrackPlays()
    kept = (1 + plays.listens) / (1 + plays.listens + plays.skips)
    return RATING_WEIGHTS.get(rating, 0.0) * math.sqrt(1 + plays.listens) * kept


async def ratings(user_id: Optional[int], within: str = "favorites") -> Dict[str, str]:
    if not user_id:
        return {}
    prefs = await user_data_cache.get_preferences(user_id)
    rated = {track_id: "super_like" for track_id in prefs.get("super_likes") or ()}
    if within != "super_likes":
        for track_id in prefs.get("likes") or ():
            rated.setdefault(track_id, "like")
    return rated


async def loved_tracks(user_id: Optional[int], session_id: Optional[str],
                       within: str = "favorites") -> List[Loved]:
    rated = await ratings(user_id, within)
    if not rated:
        return []
    plays = await track_plays(user_id, session_id, rated)
    loved = [Loved(track_id, rating, love(rating, plays.get(track_id)), plays.get(track_id) or TrackPlays())
             for track_id, rating in rated.items()]
    return sorted(loved, key=lambda item: item.weight, reverse=True)


def weighted_pick(weights: Dict[str, float], count: int) -> List[str]:
    keyed = sorted(((random.random() ** (1.0 / weight), track_id) for track_id, weight in weights.items()
                    if weight > 0), reverse=True)
    return [track_id for _, track_id in keyed[:count]]


def _ago(at: Optional[datetime], now: datetime) -> Optional[str]:
    if at is None:
        return None
    days = (now - at).days
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    if days < 60:
        return f"{days} days ago"
    return f"{days // 30} months ago"


def brief(rating: Optional[str], plays: Optional[TrackPlays], now: Optional[datetime] = None) -> Dict[str, object]:
    now = now or datetime.now(timezone.utc)
    plays = plays or TrackPlays()
    entry: Dict[str, object] = {"rating": rating} if rating else {}
    entry.update({"listens": plays.listens, "skips": plays.skips})
    last = _ago(plays.last_played, now)
    if last:
        entry["last_played"] = last
    return entry
