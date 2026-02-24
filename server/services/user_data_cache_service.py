import asyncio
from typing import Optional, Dict, Set
from datetime import datetime, timedelta
from sqlalchemy import select
from database import AsyncSessionLocal, User, TrackPreference, PreferenceType
from database.models import ShoutoutPreference, ShoutoutPreferenceType
from services import log_service
from services.base_service import SingletonService

class UserDataCacheService(SingletonService):

    def __init__(self):
        if getattr(self, '_initialized', False):
            return
        
        self._user_cache: Dict[int, User] = {}
        self._user_cache_timestamps: Dict[int, datetime] = {}
        self._user_ttl = timedelta(hours=1)
        
        self._username_index: Dict[str, int] = {}
        
        self._prefs_cache: Dict[int, Dict[str, Set[str]]] = {}
        self._prefs_cache_timestamps: Dict[int, datetime] = {}
        self._prefs_ttl = timedelta(minutes=30)
        
        self._shoutout_prefs_cache: Dict[int, Dict[str, Set[str]]] = {}
        self._shoutout_prefs_timestamps: Dict[int, datetime] = {}
        self._shoutout_prefs_ttl = timedelta(minutes=30)
        
        self._lock = asyncio.Lock()
        
        self._user_hits = 0
        self._user_misses = 0
        self._prefs_hits = 0
        self._prefs_misses = 0
        
        self._initialized = True
    
    async def initialize(self):
        log_service.system("UserDataCacheService warming cache...")
        await self._warm_user_cache()
        stats = self.get_stats()
        log_service.system(
            f"UserDataCacheService ready: "
            f"{stats['cached_users']} users, "
            f"{stats['memory_estimate_mb']} MB"
        )

    async def get_user(self, user_id: Optional[int]) -> Optional[User]:
        if user_id is None or user_id == 0:
            return None

        async with self._lock:
            if user_id in self._user_cache:
                timestamp = self._user_cache_timestamps.get(user_id)
                if timestamp is not None:
                    cache_age = datetime.now() - timestamp
                    if cache_age < self._user_ttl:
                        self._user_hits += 1
                        return self._user_cache[user_id]
                self._remove_user_from_cache(user_id)

        user = await self._fetch_user_from_db(user_id)
        if user:
            await self._add_user_to_cache(user)
            self._user_misses += 1
        return user
    
    async def get_user_by_username(self, username: Optional[str]) -> Optional[User]:
        if not username:
            return None

        username_lower = username.lower()

        async with self._lock:
            if username_lower in self._username_index:
                user_id = self._username_index[username_lower]
                if user_id in self._user_cache:
                    timestamp = self._user_cache_timestamps.get(user_id)
                    if timestamp is not None:
                        cache_age = datetime.now() - timestamp
                        if cache_age < self._user_ttl:
                            self._user_hits += 1
                            return self._user_cache[user_id]

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.username == username)
            )
            user = result.scalar_one_or_none()
            if user:
                await self._add_user_to_cache(user)
                self._user_misses += 1
            return user
    
    async def invalidate_user(self, user_id: int) -> None:
        async with self._lock:
            self._remove_user_from_cache(user_id)
            if user_id in self._prefs_cache:
                del self._prefs_cache[user_id]
            if user_id in self._prefs_cache_timestamps:
                del self._prefs_cache_timestamps[user_id]
            if user_id in self._shoutout_prefs_cache:
                del self._shoutout_prefs_cache[user_id]
            if user_id in self._shoutout_prefs_timestamps:
                del self._shoutout_prefs_timestamps[user_id]
        log_service.system(f"UserDataCache invalidated user {user_id}")

    async def get_preferences(self, user_id: Optional[int]) -> Dict[str, Set[str]]:
        if user_id is None or user_id == 0:
            return {"likes": set(), "super_likes": set(), "bans": set()}

        async with self._lock:
            if user_id in self._prefs_cache:
                timestamp = self._prefs_cache_timestamps.get(user_id)
                if timestamp is not None:
                    cache_age = datetime.now() - timestamp
                    if cache_age < self._prefs_ttl:
                        self._prefs_hits += 1
                        return self._prefs_cache[user_id].copy()
                if user_id in self._prefs_cache:
                    del self._prefs_cache[user_id]
                if user_id in self._prefs_cache_timestamps:
                    del self._prefs_cache_timestamps[user_id]
        
        prefs = await self._fetch_preferences_from_db(user_id)
        async with self._lock:
            self._prefs_cache[user_id] = prefs
            self._prefs_cache_timestamps[user_id] = datetime.now()
        self._prefs_misses += 1
        return prefs.copy()
    
    async def get_banned_ids(self, user_id: Optional[int]) -> Set[str]:
        if not user_id:
            return set()
        prefs = await self.get_preferences(user_id)
        return prefs["bans"]
    
    async def invalidate_preferences(self, user_id: int) -> None:
        async with self._lock:
            if user_id in self._prefs_cache:
                del self._prefs_cache[user_id]
            if user_id in self._prefs_cache_timestamps:
                del self._prefs_cache_timestamps[user_id]
        log_service.system(f"UserDataCache invalidated preferences for user {user_id}")

    async def get_shoutout_preferences(self, user_id: Optional[int]) -> Dict[str, Set[str]]:
        if user_id is None or user_id == 0:
            return {"likes": set(), "bans": set()}

        async with self._lock:
            if user_id in self._shoutout_prefs_cache:
                timestamp = self._shoutout_prefs_timestamps.get(user_id)
                if timestamp is not None:
                    cache_age = datetime.now() - timestamp
                    if cache_age < self._shoutout_prefs_ttl:
                        return self._shoutout_prefs_cache[user_id].copy()

        prefs = await self._fetch_shoutout_preferences_from_db(user_id)
        async with self._lock:
            self._shoutout_prefs_cache[user_id] = prefs
            self._shoutout_prefs_timestamps[user_id] = datetime.now()
        return prefs.copy()

    def get_stats(self) -> dict:
        total_user = self._user_hits + self._user_misses
        user_hit_rate = (self._user_hits / total_user * 100) if total_user > 0 else 0
        
        total_prefs = self._prefs_hits + self._prefs_misses
        prefs_hit_rate = (self._prefs_hits / total_prefs * 100) if total_prefs > 0 else 0
        
        memory_mb = (len(self._user_cache) * 5 + len(self._prefs_cache) * 1) / 1024
        
        return {
            "cached_users": len(self._user_cache),
            "cached_preferences": len(self._prefs_cache),
            "user_hit_rate": f"{user_hit_rate:.1f}%",
            "prefs_hit_rate": f"{prefs_hit_rate:.1f}%",
            "user_hits": self._user_hits,
            "user_misses": self._user_misses,
            "prefs_hits": self._prefs_hits,
            "prefs_misses": self._prefs_misses,
            "memory_estimate_mb": f"{memory_mb:.1f}"
        }
    
    async def clear_all(self):
        async with self._lock:
            self._user_cache.clear()
            self._username_index.clear()
            self._user_cache_timestamps.clear()
            self._prefs_cache.clear()
            self._prefs_cache_timestamps.clear()
            self._shoutout_prefs_cache.clear()
            self._shoutout_prefs_timestamps.clear()
            self._user_hits = 0
            self._user_misses = 0
            self._prefs_hits = 0
            self._prefs_misses = 0
        log_service.system("UserDataCache cleared all caches")

    async def _warm_user_cache(self) -> None:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User))
            users = result.scalars().all()

            async with self._lock:
                for user in users:
                    user_id = int(user.id)  # type: ignore
                    username = str(user.username)  # type: ignore
                    self._user_cache[user_id] = user
                    self._username_index[username.lower()] = user_id
                    self._user_cache_timestamps[user_id] = datetime.now()

            log_service.system(f"UserDataCache warmed with {len(users)} users")
    
    async def _fetch_user_from_db(self, user_id: int) -> Optional[User]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.id == user_id)
            )
            return result.scalar_one_or_none()
    
    async def _add_user_to_cache(self, user: User) -> None:
        async with self._lock:
            user_id = int(user.id)  # type: ignore
            username = str(user.username)  # type: ignore
            self._user_cache[user_id] = user
            self._username_index[username.lower()] = user_id
            self._user_cache_timestamps[user_id] = datetime.now()
    
    def _remove_user_from_cache(self, user_id: int) -> None:
        if user_id in self._user_cache:
            user = self._user_cache[user_id]
            username = str(user.username)  # type: ignore
            if username and username.lower() in self._username_index:
                del self._username_index[username.lower()]
            del self._user_cache[user_id]
            if user_id in self._user_cache_timestamps:
                del self._user_cache_timestamps[user_id]
    
    async def _fetch_preferences_from_db(self, user_id: int) -> Dict[str, Set[str]]:
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(TrackPreference).where(TrackPreference.user_id == user_id)  # type: ignore
                )
                preferences = result.scalars().all()

                prefs_dict: Dict[str, Set[str]] = {"likes": set(), "super_likes": set(), "bans": set()}

                for pref in preferences:
                    pref_type = pref.preference_type
                    track_id_val = str(pref.track_id)  # type: ignore
                    if pref_type == PreferenceType.LIKE:  # type: ignore
                        prefs_dict["likes"].add(track_id_val)
                    elif pref_type == PreferenceType.SUPER_LIKE:  # type: ignore
                        prefs_dict["super_likes"].add(track_id_val)
                    elif pref_type == PreferenceType.BAN:  # type: ignore
                        prefs_dict["bans"].add(track_id_val)

                return prefs_dict
        except Exception as e:
            log_service.error(f"Error fetching preferences for user {user_id}: {e}")
            return {"likes": set(), "super_likes": set(), "bans": set()}
    
    async def _fetch_shoutout_preferences_from_db(self, user_id: int) -> Dict[str, Set[str]]:
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(ShoutoutPreference).where(ShoutoutPreference.user_id == user_id)  # type: ignore
                )
                preferences = result.scalars().all()

                prefs_dict: Dict[str, Set[str]] = {"likes": set(), "bans": set()}

                for pref in preferences:
                    pref_type = pref.preference_type
                    shoutout_id_val = str(pref.shoutout_id)  # type: ignore
                    if pref_type == ShoutoutPreferenceType.LIKE:  # type: ignore
                        prefs_dict["likes"].add(shoutout_id_val)
                    elif pref_type == ShoutoutPreferenceType.BAN:  # type: ignore
                        prefs_dict["bans"].add(shoutout_id_val)

                return prefs_dict
        except Exception as e:
            log_service.error(f"Error fetching shoutout preferences for user {user_id}: {e}")
            return {"likes": set(), "bans": set()}

user_data_cache = UserDataCacheService()