from typing import Dict, Optional, Tuple
from datetime import datetime, UTC
from services import log_service
from services.base_service import SingletonService

class RateLimitService(SingletonService):
    def __init__(self):
        if self._initialized:
            return

        self.last_generation_time: Dict[str, float] = {}
        self.daily_generation_count: Dict[str, Dict[str, int]] = {}
        self.cooldown_seconds = 5 * 60
        self.daily_limits = {
            "basic": 20,
            "premium": 100
        }

        self._initialized = True

    async def initialize(self):
        log_service.system("RateLimitService initialized with daily limits only (no per-request cooldown)")

    def _get_user_key(self, user_id: Optional[int], session_id: str) -> str:
        return f"user_{user_id}" if user_id else f"session_{session_id}"

    def _get_current_day(self) -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    def _get_user_tier(self, user) -> str:
        if not user:
            return "basic"
        return getattr(user, 'tier', 'basic')

    async def check_rate_limit(
        self,
        user_id: Optional[int],
        session_id: str,
        user=None
    ) -> Tuple[bool, Optional[str]]:
        user_key = self._get_user_key(user_id, session_id)
        current_day = self._get_current_day()

        user_tier = self._get_user_tier(user)
        daily_limit = self.daily_limits.get(user_tier, self.daily_limits["basic"])

        if user_key not in self.daily_generation_count:
            self.daily_generation_count[user_key] = {}

        if current_day not in self.daily_generation_count[user_key]:
            self.daily_generation_count[user_key] = {current_day: 0}

        day_count = self.daily_generation_count[user_key].get(current_day, 0)

        if day_count >= daily_limit:
            return False, f"Daily generation limit reached ({daily_limit} tracks per day for {user_tier} tier). Try again tomorrow or upgrade your account."

        return True, None

    async def record_generation(
        self,
        user_id: Optional[int],
        session_id: str
    ):
        user_key = self._get_user_key(user_id, session_id)
        current_day = self._get_current_day()

        if user_key not in self.daily_generation_count:
            self.daily_generation_count[user_key] = {}

        if current_day not in self.daily_generation_count[user_key]:
            self.daily_generation_count[user_key] = {current_day: 0}

        self.daily_generation_count[user_key][current_day] += 1

        log_service.system(
            f"[RateLimit] {user_key}: Generation recorded. "
            f"Daily count: {self.daily_generation_count[user_key][current_day]}"
        )

rate_limit_service = RateLimitService()