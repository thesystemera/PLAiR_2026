import time
from typing import Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy import case, func, select

from database import AsyncSessionLocal, PlayEvent
from database.models import ShoutoutPreference, ShoutoutPreferenceType
from services import log_service
from services.analytics_service import analytics_service
from services.rate_limit_service import rate_limit_service

SCORE_TTL_S = 60.0
BANS_TTL_S = 60.0
AIRED_TTL_S = 30 * 60.0
ON_AIR_PLAY_DEDUPE_S = 6 * 3600
BURIED_MIN_BANS = 3


class CommunityEngagement:
    def __init__(self):
        self._scores: Dict[str, Tuple[float, Dict[str, float]]] = {}
        self._bans: Dict[int, Tuple[float, Set[str]]] = {}
        self._aired: Dict[str, List[Tuple[float, str]]] = {}

    @staticmethod
    def score(plays: int, likes: int, superlikes: int, bans: int, skips: int) -> float:
        return analytics_service._popularity_score(plays, likes, superlikes, bans, skips)

    async def stats(self, ids: Iterable[str]) -> Dict[str, Dict[str, float]]:
        ids = [i for i in dict.fromkeys(ids) if i]
        now = time.monotonic()
        result = {i: self._scores[i][1] for i in ids if i in self._scores and now - self._scores[i][0] < SCORE_TTL_S}
        missing = [i for i in ids if i not in result]
        if not missing:
            return result
        fresh = {i: {"plays": 0, "likes": 0, "superlikes": 0, "bans": 0, "skips": 0} for i in missing}
        try:
            async with AsyncSessionLocal() as session:
                prefs = await session.execute(
                    select(ShoutoutPreference.shoutout_id, ShoutoutPreference.preference_type,
                           func.count(ShoutoutPreference.id))  # pylint: disable=E1102
                    .where(ShoutoutPreference.shoutout_id.in_(missing))
                    .group_by(ShoutoutPreference.shoutout_id, ShoutoutPreference.preference_type)
                )
                keys = {ShoutoutPreferenceType.LIKE: "likes", ShoutoutPreferenceType.SUPER_LIKE: "superlikes",
                        ShoutoutPreferenceType.BAN: "bans"}
                for sid, pref_type, count in prefs.all():
                    if pref_type in keys:
                        fresh[sid][keys[pref_type]] = int(count)
                plays = await session.execute(
                    select(PlayEvent.track_id,
                           func.sum(case((PlayEvent.event_type == "play", 1), else_=0)),
                           func.sum(case((PlayEvent.event_type == "skip", 1), else_=0)))
                    .where(PlayEvent.track_id.in_(missing))
                    .group_by(PlayEvent.track_id)
                )
                for sid, play_count, skip_count in plays.all():
                    fresh[sid]["plays"] = int(play_count or 0)
                    fresh[sid]["skips"] = int(skip_count or 0)
        except Exception as e:
            log_service.throttled("community_stats", f"[Community] Engagement lookup failed: {e}")
            return {**result, **fresh}
        for sid, counts in fresh.items():
            counts["score"] = self.score(int(counts["plays"]), int(counts["likes"]), int(counts["superlikes"]),
                                         int(counts["bans"]), int(counts["skips"]))
            self._scores[sid] = (now, counts)
            result[sid] = counts
        return result

    def forget(self, shoutout_id: str):
        self._scores.pop(shoutout_id, None)

    async def banned_by(self, user_id: Optional[int]) -> Set[str]:
        if not user_id:
            return set()
        now = time.monotonic()
        cached = self._bans.get(user_id)
        if cached and now - cached[0] < BANS_TTL_S:
            return cached[1]
        try:
            async with AsyncSessionLocal() as session:
                rows = await session.execute(
                    select(ShoutoutPreference.shoutout_id)
                    .where(ShoutoutPreference.user_id == user_id,
                           ShoutoutPreference.preference_type == ShoutoutPreferenceType.BAN)
                )
                bans = {row[0] for row in rows.all()}
        except Exception as e:
            log_service.throttled("community_bans", f"[Community] Ban lookup failed: {e}")
            bans = cached[1] if cached else set()
        self._bans[user_id] = (now, bans)
        return bans

    def forget_bans(self, user_id: int):
        self._bans.pop(user_id, None)

    async def airable(self, ids: Iterable[str], user_id: Optional[int]) -> Set[str]:
        ids = list(ids)
        own_bans = await self.banned_by(user_id)
        counts = await self.stats(ids)
        return {
            sid for sid in ids
            if sid not in own_bans
            and not (counts.get(sid, {}).get("bans", 0) >= BURIED_MIN_BANS
                     and counts[sid]["bans"] > counts[sid].get("likes", 0) + counts[sid].get("superlikes", 0))
        }

    async def rank(self, items: List[Dict], key: str = "id") -> List[Dict]:
        counts = await self.stats(item.get(key) for item in items)
        for item in items:
            item["engagement"] = counts.get(item.get(key), {})
        return sorted(items, key=lambda it: (it["engagement"].get("score", 0), it.get("timestamp", "")), reverse=True)

    async def top_reply(self, replies: List[Dict], user_id: Optional[int]) -> Optional[Dict]:
        if not replies:
            return None
        allowed = await self.airable([r.get("id") for r in replies], user_id)
        ranked = await self.rank([r for r in replies if r.get("id") in allowed])
        return ranked[0] if ranked else None

    def note_aired(self, session_id: str, shoutout_id: str):
        now = time.time()
        history = [(t, sid) for t, sid in self._aired.get(session_id, []) if now - t < AIRED_TTL_S and sid != shoutout_id]
        history.append((now, shoutout_id))
        self._aired[session_id] = history[-20:]

    def last_aired(self, session_id: Optional[str]) -> List[str]:
        now = time.time()
        return [sid for t, sid in reversed(self._aired.get(session_id or "", [])) if now - t < AIRED_TTL_S]

    async def record_on_air_play(self, shoutout_id: str, session_id: Optional[str], user_id: Optional[int]):
        if session_id:
            self.note_aired(session_id, shoutout_id)
        if not rate_limit_service.claim_once(f"shoutout_play:{session_id}:{shoutout_id}:play", ON_AIR_PLAY_DEDUPE_S):
            return
        self.forget(shoutout_id)
        await analytics_service.log_play_event(user_id=user_id or None, track_id=shoutout_id, session_id=session_id,
                                               event_type="play")


community_engagement = CommunityEngagement()
