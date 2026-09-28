import json
import os
import time
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta, UTC
from config import settings
from services import log_service
from services.base_service import SingletonService

MAX_TRACKED_BUCKETS = 200_000

class RateLimitService(SingletonService):
    def __init__(self):
        if self._initialized:
            return

        self.last_generation_time: Dict[str, float] = {}
        self.generation_usage: Dict[str, Dict] = {}
        self._usage_loaded = False
        self._buckets: Dict[str, Tuple[float, float, float, float]] = {}
        self._claims: Dict[str, float] = {}
        self._last_prune = time.monotonic()
        self.cooldown_seconds = 5 * 60

        self._initialized = True

    async def initialize(self):
        self._load_usage()
        log_service.system(
            f"RateLimitService initialized (premium {settings.PREMIUM_MONTHLY_GENERATIONS}/month, "
            f"free {settings.FREE_GENERATION_LIMIT}/{settings.FREE_GENERATION_PERIOD})"
        )

    def _get_user_key(self, user_id: Optional[int], session_id: str) -> str:
        return f"user_{user_id}" if user_id else f"session_{session_id}"

    def _get_current_day(self) -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    def _get_current_month(self) -> str:
        return datetime.now(UTC).strftime("%Y-%m")

    def _get_user_tier(self, user) -> str:
        if not user:
            return "basic"
        return getattr(user, 'tier', 'basic')

    def consume(self, bucket: str, key: str, limit: int, window_s: float, cost: float = 1.0) -> Tuple[bool, int]:
        now = time.monotonic()
        if now - self._last_prune > 60 or len(self._buckets) > MAX_TRACKED_BUCKETS:
            self._prune(now)

        capacity = float(max(limit, 0))
        rate = capacity / window_s if window_s > 0 else 0.0
        bucket_key = f"{bucket}:{key}"
        tokens, last, _, _ = self._buckets.get(bucket_key, (capacity, now, capacity, rate))
        tokens = min(capacity, tokens + (now - last) * rate)

        if tokens >= cost:
            self._buckets[bucket_key] = (tokens - cost, now, capacity, rate)
            return True, 0

        self._buckets[bucket_key] = (tokens, now, capacity, rate)
        retry_after = int((cost - tokens) / rate) + 1 if rate > 0 else int(window_s)
        return False, retry_after

    def claim_once(self, key: str, ttl_s: float) -> bool:
        now = time.monotonic()
        if now - self._last_prune > 60 or len(self._claims) > MAX_TRACKED_BUCKETS:
            self._prune(now)
        expires = self._claims.get(key)
        if expires is not None and expires > now:
            return False
        self._claims[key] = now + max(ttl_s, 0.0)
        return True

    def _prune(self, now: float):
        self._last_prune = now
        self._claims = {k: exp for k, exp in self._claims.items() if exp > now}
        if len(self._claims) > MAX_TRACKED_BUCKETS:
            newest = sorted(self._claims.items(), key=lambda item: item[1])[-MAX_TRACKED_BUCKETS:]
            self._claims = dict(newest)
        stale = [
            k for k, (tokens, last, capacity, rate) in self._buckets.items()
            if tokens + (now - last) * rate >= capacity
        ]
        for k in stale:
            del self._buckets[k]
        if len(self._buckets) > MAX_TRACKED_BUCKETS:
            oldest = sorted(self._buckets.items(), key=lambda item: item[1][1])
            for k, _ in oldest[:len(self._buckets) - MAX_TRACKED_BUCKETS]:
                del self._buckets[k]

    def _limit_for(self, user) -> Tuple[int, str]:
        if self._get_user_tier(user) == "premium":
            return settings.PREMIUM_MONTHLY_GENERATIONS, "month"
        period = settings.FREE_GENERATION_PERIOD if settings.FREE_GENERATION_PERIOD in ("day", "month") else "day"
        return settings.FREE_GENERATION_LIMIT, period

    def _load_usage(self):
        if self._usage_loaded:
            return
        self._usage_loaded = True
        path = settings.GENERATION_USAGE_PATH
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self.generation_usage = {k: v for k, v in data.items() if isinstance(v, dict)}
        except Exception as e:
            log_service.error(f"[RateLimit] Failed to load generation usage: {e}")

    def _save_usage(self):
        day, month = self._get_current_day(), self._get_current_month()
        self.generation_usage = {
            k: v for k, v in self.generation_usage.items()
            if v.get("day") == day or v.get("month") == month
        }
        path = settings.GENERATION_USAGE_PATH
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.generation_usage), encoding="utf-8")
            os.replace(tmp, path)
        except Exception as e:
            log_service.error(f"[RateLimit] Failed to save generation usage: {e}")

    def _usage_entry(self, user_key: str) -> Dict:
        self._load_usage()
        day, month = self._get_current_day(), self._get_current_month()
        entry = self.generation_usage.setdefault(user_key, {})
        if entry.get("day") != day:
            entry["day"], entry["day_count"] = day, 0
        if entry.get("month") != month:
            entry["month"], entry["month_count"] = month, 0
        return entry

    @staticmethod
    def _period_reset(period: str) -> datetime:
        now = datetime.now(UTC)
        if period == "month":
            return datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=UTC)
        return datetime(now.year, now.month, now.day, tzinfo=UTC) + timedelta(days=1)

    def get_generation_usage(self, user_id: Optional[int], session_id: str, user=None) -> Dict:
        limit, period = self._limit_for(user)
        entry = self._usage_entry(self._get_user_key(user_id, session_id))
        used = int(entry.get(f"{period}_count", 0))
        return {
            "tier": self._get_user_tier(user),
            "limit": limit,
            "used": used,
            "remaining": max(limit - used, 0),
            "period": period,
            "resets_at": self._period_reset(period).isoformat(),
        }

    def _limit_message(self, usage: Dict, cost: int) -> str:
        remaining = usage["remaining"]
        reset = self._period_reset(usage["period"])
        if usage["tier"] == "premium":
            if remaining > 0:
                return (f"Only {remaining} of your {usage['limit']} Premium generations are left this month "
                        f"and this request needs {cost}. Lower the batch count or wait until {reset:%B} {reset.day}.")
            return (f"You've used all {usage['limit']} Premium generations for this month. "
                    f"Your allowance resets on {reset:%B} {reset.day}.")
        unit = "today" if usage["period"] == "day" else "this month"
        if remaining > 0:
            return (f"Only {remaining} of your {usage['limit']} free generations are left {unit} "
                    f"and this request needs {cost}. Lower the batch count or upgrade to Premium.")
        return (f"Free generation limit reached ({usage['limit']} per {usage['period']}). "
                f"Try again {'tomorrow' if usage['period'] == 'day' else 'next month'} or upgrade to Premium.")

    async def reserve_generations(
        self,
        user_id: Optional[int],
        session_id: str,
        user=None,
        cost: int = 1
    ) -> Tuple[bool, Optional[str], Dict]:
        usage = self.get_generation_usage(user_id, session_id, user)
        if cost > usage["remaining"]:
            return False, self._limit_message(usage, cost), usage

        user_key = self._get_user_key(user_id, session_id)
        entry = self._usage_entry(user_key)
        entry["day_count"] = int(entry.get("day_count", 0)) + cost
        entry["month_count"] = int(entry.get("month_count", 0)) + cost
        self._save_usage()
        usage = self.get_generation_usage(user_id, session_id, user)
        log_service.system(
            f"[RateLimit] {user_key}: Reserved {cost} generation(s). "
            f"{usage['used']}/{usage['limit']} used this {usage['period']}"
        )
        return True, None, usage

    def reservation_stamp(self) -> Dict[str, str]:
        return {"day": self._get_current_day(), "month": self._get_current_month()}

    async def refund_generations(self, user_id: Optional[int], session_id: str, cost: int = 1,
                                 reserved: Optional[Dict[str, str]] = None) -> bool:
        if cost <= 0:
            return False
        entry = self._usage_entry(self._get_user_key(user_id, session_id))
        refunded = False
        for period in ("day", "month"):
            if reserved and reserved.get(period) != entry.get(period):
                continue
            entry[f"{period}_count"] = max(int(entry.get(f"{period}_count", 0)) - cost, 0)
            refunded = True
        self._save_usage()
        log_service.system(f"[RateLimit] {self._get_user_key(user_id, session_id)}: Refunded {cost} generation(s)")
        return refunded

rate_limit_service = RateLimitService()