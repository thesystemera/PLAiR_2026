import asyncio
import json
import shutil

from sqlalchemy import delete, or_, update

from config import settings
from database import AsyncSessionLocal, User
from database.models import (
    AIUsageDaily, AIUsageEvent, AiredTalk, ArtistProfile, Conversation, NewsAired, Passkey, PlaybackSnapshot, PlayEvent,
    ShoutoutAnalytics, ShoutoutPreference, TrackAnalytics, TrackPreference, UserDevice, UserRadioSettings,
    WeatherData,
)
from service_registry import services
from services import log_service
from services.analytics_service import analytics_service
from services.community_engagement import community_engagement
from services.rate_limit_service import rate_limit_service
from services.user_content_database_service import KIND_REPLY, KIND_REVIEW, KIND_SHOUTOUT, kind_of
from services.user_data_cache_service import user_data_cache
from services_radio import listener_location
from services_radio.stripe_service import close_billing_for_deleted_account


class AccountDeletionError(RuntimeError):
    pass


async def _close_live_session(user_id: int, session_id: str) -> None:
    if services.websocket_service is not None:
        await services.websocket_service.broadcast_to_session(session_id, {"type": "account_deleted", "data": {}})
        services.websocket_service.close_session(session_id)
    if services.tts_queue_manager is not None:
        await services.tts_queue_manager.cancel_session(session_id, user_id)
    if services.playback_service is not None:
        services.playback_service.drop_session(session_id)
    if services.announcer_service is not None:
        await services.announcer_service.forget_session(session_id)
    if services.sting_service is not None:
        services.sting_service.forget_session(session_id)
    listener_location.forget_session(session_id)


async def _delete_uploads(user_id: int) -> list[str]:
    catalog = services.catalog_service
    uploader = services.human_music_upload_service
    if catalog is None or uploader is None:
        return []
    track_ids = [tid for tid, meta in list(catalog.tracks.items()) if meta.get("uploaded_by_user_id") == user_id]
    for track_id in track_ids:
        ok, message = await uploader.delete_user_track(user_id, track_id)
        if not ok:
            log_service.warning(f"[Account] Could not delete upload {track_id}: {message}")
    return track_ids


async def _delete_posts(user_id: int) -> int:
    store = services.user_content_service
    if store is None:
        return 0
    mine = store.items_by_user(user_id)
    ordered = mine[KIND_SHOUTOUT] + mine[KIND_REPLY] + mine[KIND_REVIEW]
    deleted = 0
    for post in ordered:
        post_id = post["id"]
        existing = store.get_shoutout(post_id)
        if not existing:
            continue
        kind = kind_of(existing)
        if await asyncio.to_thread(store.delete_shoutout, post_id):
            deleted += 1
            community_engagement.forget(post_id)
            if services.websocket_service is not None:
                await services.websocket_service.broadcast_content_updated(kind, post_id, {"deleted": True})
    return deleted


def _delete_share_videos(user_id: int) -> int:
    folder = settings.CATALOG_DIR / "share_videos"
    if not folder.is_dir():
        return 0
    removed = 0
    for meta_path in folder.glob("*.json"):
        try:
            owner = json.loads(meta_path.read_text(encoding="utf-8")).get("user_id")
        except (OSError, ValueError):
            continue
        if str(owner) != str(user_id):
            continue
        meta_path.with_suffix(".mp4").unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        removed += 1
    return removed


def _delete_user_files(user_id: int) -> None:
    user_dir = settings.USERS_DIR / str(user_id)
    if user_dir.is_dir():
        shutil.rmtree(user_dir, ignore_errors=True)
    staging = settings.USERS_DIR / "_upload_staging"
    if staging.is_dir():
        for path in staging.glob(f"{user_id}_*"):
            path.unlink(missing_ok=True)


async def _delete_rows(user_id: int, session_id: str, track_ids: list[str]) -> None:
    post_prefix = f"{user_id}\\_%"
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Passkey).where(Passkey.user_id == user_id))
        await db.execute(delete(ArtistProfile).where(ArtistProfile.owner_user_id == user_id))
        await db.execute(delete(UserDevice).where(UserDevice.user_id == user_id))
        await db.execute(delete(TrackPreference).where(TrackPreference.user_id == user_id))
        await db.execute(delete(Conversation).where(Conversation.user_id == user_id))
        await db.execute(delete(WeatherData).where(WeatherData.user_id == user_id))
        await db.execute(delete(UserRadioSettings).where(UserRadioSettings.user_id == user_id))
        await db.execute(delete(PlaybackSnapshot).where(PlaybackSnapshot.session_id == session_id))
        await db.execute(delete(ShoutoutPreference).where(or_(
            ShoutoutPreference.user_id == user_id, ShoutoutPreference.shoutout_id.like(post_prefix, escape="\\"))))
        await db.execute(delete(ShoutoutAnalytics).where(ShoutoutAnalytics.shoutout_id.like(post_prefix, escape="\\")))
        await db.execute(delete(PlayEvent).where(or_(
            PlayEvent.user_id == user_id, PlayEvent.session_id == session_id,
            PlayEvent.track_id.like(post_prefix, escape="\\"))))
        if track_ids:
            await db.execute(delete(PlayEvent).where(PlayEvent.track_id.in_(track_ids)))
            await db.execute(delete(TrackAnalytics).where(TrackAnalytics.track_id.in_(track_ids)))
        await db.execute(delete(AiredTalk).where(or_(AiredTalk.user_id == user_id, AiredTalk.session_id == session_id)))
        await db.execute(delete(NewsAired).where(NewsAired.subject == session_id))
        for table in (AIUsageEvent, AIUsageDaily):
            await db.execute(update(table).where(or_(table.user_id == user_id, table.subject_key == f"user:{user_id}"))
                             .values(user_id=None, session_id="", subject_key=f"deleted:{user_id}"))
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


def _forget_generation_usage(user_id: int) -> None:
    if rate_limit_service.generation_usage.pop(f"user_{user_id}", None) is not None:
        rate_limit_service._save_usage()


async def delete_account(user_id: int) -> dict:
    session_id = str(user_id)
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if user is None:
            raise AccountDeletionError("Account not found")
        who = log_service.who(user_id=user_id)
        try:
            await close_billing_for_deleted_account(user)
        except Exception as exc:
            log_service.error(f"[Account] {who}: could not close billing, account kept: {exc}")
            raise AccountDeletionError("Your subscription couldn't be cancelled, so nothing was deleted. Please try again.")

    await _close_live_session(user_id, session_id)
    track_ids = await _delete_uploads(user_id)
    posts = await _delete_posts(user_id)
    videos = await asyncio.to_thread(_delete_share_videos, user_id)
    await analytics_service.forget_listener(user_id, session_id)
    await _delete_rows(user_id, session_id, track_ids)
    await asyncio.to_thread(_delete_user_files, user_id)
    _forget_generation_usage(user_id)
    community_engagement.forget_bans(user_id)
    await user_data_cache.invalidate_user(user_id)

    summary = {"tracks": len(track_ids), "posts": posts, "videos": videos}
    log_service.listener(f"{who}: deleted their account and all its data "
                         f"({summary['tracks']} uploads, {summary['posts']} posts, {summary['videos']} videos)")
    return summary
