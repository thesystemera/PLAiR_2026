import asyncio
import json
from typing import Dict, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import TrackPreference, PreferenceType, ShoutoutPreference, ShoutoutPreferenceType, User,     UserRadioSettings, utc_now
from services import log_service
from services.base_service import SingletonService
from services.user_data_cache_service import user_data_cache
from services.user_content_database_service import public_shoutout

PREFERENCE_VERBS = {"like": "liked", "super_like": "super-liked", "ban": "banned", "dislike": "disliked"}


def _track_label(playback_service, track_id: str) -> str:
    catalog = getattr(playback_service, "catalog", None)
    track = catalog.get_track(track_id) if catalog is not None else None
    return log_service.track_label(track, f"track {track_id}")


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
        log_service.listener(
            f"{log_service.who(user_id=user_id)}: {PREFERENCE_VERBS.get(preference_type, preference_type)} "
            f"{_track_label(playback_service, track_id)}")

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
            log_service.listener(
                f"{log_service.who(user_id=user_id)}: cleared their rating of {_track_label(playback_service, track_id)}")

            if playback_service:
                session_id = str(user_id)
                await playback_service.handle_preference_change(
                    session_id, user_id, track_id, "none"
                )

            if broadcast_callback:
                await broadcast_callback(user_id, track_id, "none")

        return {"status": "success"}

    @staticmethod
    def _forget_community(user_id: int, shoutout_id: str):
        from services.community_engagement import community_engagement
        community_engagement.forget(shoutout_id)
        community_engagement.forget_bans(user_id)

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
        log_service.listener(
            f"{log_service.who(user_id=user_id)}: {PREFERENCE_VERBS.get(preference_type, preference_type)} "
            f"shoutout {shoutout_id}")
        self._forget_community(user_id, shoutout_id)

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
            log_service.listener(f"{log_service.who(user_id=user_id)}: cleared their rating of shoutout {shoutout_id}")
            self._forget_community(user_id, shoutout_id)

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

        pref_types = ["likes", "super_likes", "bans"]
        all_ids = [shoutout_id for pref_type in pref_types for shoutout_id in pref_ids[pref_type]]
        if not all_ids:
            return result

        enriched_list = await asyncio.to_thread(user_content_service.get_enriched_shoutouts, all_ids)

        user_ids = set()
        for shoutout in enriched_list:
            if shoutout:
                try:
                    user_ids.add(int(shoutout.get('user_data', {}).get('user_id', 0)))
                except (TypeError, ValueError):
                    pass

        users_map: Dict[int, User] = {}
        if user_ids:
            user_res = await db.execute(select(User).where(User.id.in_(user_ids)))
            users_map = {u.id: u for u in user_res.scalars().all()}  # type: ignore

        position = 0
        for pref_type in pref_types:
            for shoutout_id in pref_ids[pref_type]:
                shoutout = enriched_list[position]
                position += 1
                if shoutout:
                    try:
                        uid = int(shoutout.get('user_data', {}).get('user_id', 0))
                        u = users_map.get(uid)

                        shoutout['username'] = u.username if u else "Unknown"
                        shoutout['profile_picture'] = u.profile_picture if u else None
                        result[pref_type].append(public_shoutout(shoutout))
                    except Exception as e:
                        log_service.error(f"Error enriching shoutout {shoutout_id}: {e}")

        return result

    @staticmethod
    def _stored_radio_settings(row) -> Dict:
        if row is None:
            return {}
        try:
            data = json.loads(row.settings or "{}")
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    async def get_radio_settings(self, user_id: int, db: AsyncSession) -> Dict:
        from services_radio.radio_schedule import normalize_prefs
        row = await db.get(UserRadioSettings, user_id)
        return normalize_prefs(self._stored_radio_settings(row)).to_dict()

    async def set_radio_settings(self, user_id: int, updates: Dict, db: AsyncSession) -> Dict:
        from services_radio.radio_schedule import normalize_prefs
        row = await db.get(UserRadioSettings, user_id)
        merged = self._stored_radio_settings(row)
        if isinstance(updates, dict):
            merged.update(updates)
        prefs = normalize_prefs(merged).to_dict()
        if row is None:
            db.add(UserRadioSettings(user_id=user_id, settings=json.dumps(prefs), updated_at=utc_now()))
        else:
            row.settings = json.dumps(prefs)
            row.updated_at = utc_now()
        await db.commit()
        log_service.listener(
            f"{log_service.who(user_id=user_id)}: Radio Mode settings saved ({'on' if prefs['enabled'] else 'off'})")
        return prefs

preferences_service = PreferencesService()
