from typing import Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import TrackPreference, PreferenceType, ShoutoutPreference, ShoutoutPreferenceType, User
from services import log_service
from services.base_service import SingletonService
from services.user_data_cache_service import user_data_cache

class PreferencesService(SingletonService):
    def __init__(self):
        super().__init__()
        if self._initialized:
            return

        self._initialized = True

    async def set_track_preference(
        self,
        user_id: int,
        track_id: str,
        preference_type: str,
        db: AsyncSession,
        playback_service=None,
        broadcast_callback=None
    ) -> Dict:

        pref_type_map = {
            "like": PreferenceType.LIKE,
            "super_like": PreferenceType.SUPER_LIKE,
            "ban": PreferenceType.BAN
        }

        if preference_type not in pref_type_map:
            raise ValueError("Invalid preference type")

        result = await db.execute(
            select(TrackPreference).where(
                TrackPreference.user_id == user_id,
                TrackPreference.track_id == track_id
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.preference_type = pref_type_map[preference_type]  # type: ignore  # type: ignore
        else:
            new_pref = TrackPreference(
                user_id=user_id,
                track_id=track_id,
                preference_type=pref_type_map[preference_type]
            )
            db.add(new_pref)

        await db.commit()

        await user_data_cache.invalidate_preferences(user_id)
        log_service.api(f"Preference set: {preference_type} on track {track_id}")

        if playback_service:
            session_id = str(user_id)
            await playback_service.handle_preference_change(
                session_id, user_id, track_id, preference_type
            )

        if broadcast_callback:
            await broadcast_callback(user_id, track_id, preference_type)

        return {"status": "success", "preference": preference_type}

    async def remove_track_preference(
        self,
        user_id: int,
        track_id: str,
        db: AsyncSession,
        playback_service=None,
        broadcast_callback=None
    ) -> Dict:

        result = await db.execute(
            select(TrackPreference).where(
                TrackPreference.user_id == user_id,
                TrackPreference.track_id == track_id
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            await db.delete(existing)
            await db.commit()

            await user_data_cache.invalidate_preferences(user_id)
            log_service.api(f"Preference removed for track {track_id}")

            if playback_service:
                session_id = str(user_id)
                await playback_service.handle_preference_change(
                    session_id, user_id, track_id, "none"
                )

            if broadcast_callback:
                await broadcast_callback(user_id, track_id, "none")

        return {"status": "success"}

    async def set_shoutout_preference(
        self,
        user_id: int,
        shoutout_id: str,
        preference_type: str,
        db: AsyncSession,
        broadcast_callback=None
    ) -> Dict:

        pref_type_map = {
            "like": ShoutoutPreferenceType.LIKE,
            "super_like": ShoutoutPreferenceType.SUPER_LIKE,
            "ban": ShoutoutPreferenceType.BAN
        }

        if preference_type not in pref_type_map:
            raise ValueError("Invalid preference type. Use 'super_like', 'like', or 'ban'")

        result = await db.execute(
            select(ShoutoutPreference).where(
                ShoutoutPreference.user_id == user_id,
                ShoutoutPreference.shoutout_id == shoutout_id
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.preference_type = pref_type_map[preference_type]  # type: ignore
        else:
            new_pref = ShoutoutPreference(
                user_id=user_id,
                shoutout_id=shoutout_id,
                preference_type=pref_type_map[preference_type]
            )
            db.add(new_pref)

        await db.commit()
        log_service.user_content(f"Shoutout preference set: {preference_type} on {shoutout_id}")

        if broadcast_callback:
            await broadcast_callback(user_id, shoutout_id, preference_type)

        return {"status": "success", "preference": preference_type}

    async def remove_shoutout_preference(
        self,
        user_id: int,
        shoutout_id: str,
        db: AsyncSession,
        broadcast_callback=None
    ) -> Dict:
        result = await db.execute(
            select(ShoutoutPreference).where(
                ShoutoutPreference.user_id == user_id,
                ShoutoutPreference.shoutout_id == shoutout_id
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            await db.delete(existing)
            await db.commit()
            log_service.user_content(f"Shoutout preference removed for {shoutout_id}")

            if broadcast_callback:
                await broadcast_callback(user_id, shoutout_id, "none")

        return {"status": "success"}

    async def get_enriched_track_preferences(
        self,
        user_id: int,
        catalog_service
    ) -> Dict[str, List[Dict]]:
        prefs = await user_data_cache.get_preferences(user_id)

        result: Dict[str, List[Dict]] = {
            "likes": [],
            "super_likes": [],
            "bans": []
        }

        for track_id in prefs.get("likes", []):
            track = catalog_service.get_track(track_id)
            if track:
                track_copy = track.copy()
                track_copy["has_artwork"] = catalog_service.has_artwork(track_id)
                result["likes"].append(track_copy)

        for track_id in prefs.get("super_likes", []):
            track = catalog_service.get_track(track_id)
            if track:
                track_copy = track.copy()
                track_copy["has_artwork"] = catalog_service.has_artwork(track_id)
                result["super_likes"].append(track_copy)

        for track_id in prefs.get("bans", []):
            track = catalog_service.get_track(track_id)
            if track:
                track_copy = track.copy()
                track_copy["has_artwork"] = catalog_service.has_artwork(track_id)
                result["bans"].append(track_copy)

        return result

    async def get_enriched_shoutout_preferences(
        self,
        user_id: int,
        user_content_service,
        db: AsyncSession
    ) -> Dict[str, List[Dict]]:
        result_db = await db.execute(
            select(ShoutoutPreference).where(
                ShoutoutPreference.user_id == user_id
            )
        )
        preferences = result_db.scalars().all()

        pref_ids: Dict[str, List[str]] = {
            "likes": [],
            "super_likes": [],
            "bans": []
        }

        for pref in preferences:
            if pref.preference_type == ShoutoutPreferenceType.LIKE:  # type: ignore
                pref_ids["likes"].append(str(pref.shoutout_id))  # type: ignore
            elif pref.preference_type == ShoutoutPreferenceType.SUPER_LIKE:  # type: ignore
                pref_ids["super_likes"].append(str(pref.shoutout_id))  # type: ignore
            elif pref.preference_type == ShoutoutPreferenceType.BAN:  # type: ignore
                pref_ids["bans"].append(str(pref.shoutout_id))  # type: ignore

        result: Dict[str, List[Dict]] = {
            "likes": [],
            "super_likes": [],
            "bans": []
        }

        for pref_type in ["likes", "super_likes", "bans"]:
            for shoutout_id in pref_ids[pref_type]:
                shoutout = user_content_service.get_enriched_shoutout(shoutout_id)
                if shoutout:
                    try:
                        uid = int(shoutout.get('user_data', {}).get('user_id', 0))
                        user_res = await db.execute(select(User).where(User.id == uid))
                        u: Optional[User] = user_res.scalar_one_or_none()

                        shoutout['username'] = u.username if u else "Unknown"
                        shoutout['profile_picture'] = u.profile_picture if u else None
                        result[pref_type].append(shoutout)
                    except Exception as e:
                        log_service.error(f"Error enriching shoutout {shoutout_id}: {e}")

        return result

preferences_service = PreferencesService()
