from typing import Any, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from database.models import User, Conversation, WeatherData, AiredTalk
from services import log_service
from services.user_data_cache_service import user_data_cache

class UserProfileService:

    def __init__(self):
        self._initialized = False

    async def initialize(self):
        if self._initialized:
            return
        self._initialized = True
        log_service.success("✓ User profile service initialized")

    async def update_username(self, user_id: int, new_username: str, db: AsyncSession) -> Dict:
        if not new_username or len(new_username) < 3 or len(new_username) > 30:
            raise ValueError("Username must be between 3 and 30 characters")

        result = await db.execute(select(User).where(User.username == new_username))  # type: ignore
        existing_user = result.scalar_one_or_none()
        if existing_user is not None and int(existing_user.id) != user_id:  # type: ignore
            raise ValueError("Username already exists")

        result = await db.execute(select(User).where(User.id == user_id))  # type: ignore
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError("User not found")

        old_username = str(user.username)  # type: ignore
        user.username = new_username  # type: ignore[assignment]
        await db.commit()
        await db.refresh(user)

        log_service.api(f"Username updated: {old_username} -> {new_username}")
        return {"username": user.username}

    async def get_profile(self, user_id: int, db: AsyncSession) -> Dict[str, Any]:
        result = await db.execute(select(User).where(User.id == user_id))  # type: ignore
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError("User not found")

        weather_result = await db.execute(
            select(WeatherData)
            .where(WeatherData.user_id == user.id)  # type: ignore
            .order_by(WeatherData.timestamp.desc())  # type: ignore
        )
        weather_data = weather_result.scalar_one_or_none()

        last_login_val = user.last_login
        created_at_val = user.created_at
        last_login_str = last_login_val.isoformat() if last_login_val is not None else None  # type: ignore
        created_at_str = created_at_val.isoformat() if created_at_val is not None else None  # type: ignore
        weather_desc = weather_data.description if weather_data else None

        return {
            "username": user.username,
            "persona": user.persona,
            "profile": user.profile,
            "location": user.location,
            "latitude": user.latitude,
            "longitude": user.longitude,
            "timezone": user.timezone,
            "engagements_since_last_update": user.engagements_since_last_update,
            "last_login": last_login_str,
            "created_at": created_at_str,
            "weather": weather_desc
        }

    async def update_profile(self, user_id: int, updates: Dict[str, Any], db: AsyncSession) -> Dict[str, Any]:
        result = await db.execute(select(User).where(User.id == user_id))  # type: ignore
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError("User not found")

        if updates.get("location") is not None:
            user.location = updates["location"]  # type: ignore
        if updates.get("latitude") is not None:
            user.latitude = updates["latitude"]  # type: ignore
        if updates.get("longitude") is not None:
            user.longitude = updates["longitude"]  # type: ignore
        if updates.get("timezone") is not None:
            user.timezone = updates["timezone"]  # type: ignore
        if updates.get("persona") is not None:
            user.persona = updates["persona"]  # type: ignore
        if updates.get("profile") is not None:
            user.profile = updates["profile"]  # type: ignore

        await db.commit()
        await user_data_cache.invalidate_user(user_id)
        log_service.api(f"User profile updated for {user.username}")
        return {"status": "success"}

    async def delete_conversations(self, user_id: int, db: AsyncSession) -> None:
        await db.execute(delete(Conversation).where(Conversation.user_id == user_id))  # type: ignore
        await db.execute(delete(AiredTalk).where(AiredTalk.user_id == user_id))
        await db.commit()

    async def reset_persona(self, user_id: int, db: AsyncSession) -> None:
        result = await db.execute(select(User).where(User.id == user_id))  # type: ignore
        user = result.scalar_one_or_none()
        if user:
            user.persona = None  # type: ignore[assignment]
            user.profile = None  # type: ignore[assignment]
            user.shoutout_interests = None  # type: ignore[assignment]
            await db.commit()