"""Human uploads after the fact: a listener's own tracks - listing, deleting, editing metadata and re-crediting an
artist profile."""
import asyncio
import shutil
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timezone
import json
from services import log_service
from services.asset_integrity_service import asset_integrity_service
from services.catalog_vocals import settled_vocals
from config import settings
from services.human_music_upload_service_common import (
    DESCRIPTION_MAX, LYRICS_MAX, TAG_MAX, TITLE_MAX, VISIBILITIES, _clean_tag_list,
)


class UploadedTracks:
    async def get_user_tracks(self, user_id: int, skip: int = 0, limit: int = 50) -> list:
        if not self.catalog_db_service:
            return []

        user_tracks = []
        for _track_id, metadata in list(self.catalog_db_service.tracks.items()):
            if metadata.get("uploaded_by_user_id") == user_id:
                user_tracks.append(metadata)

        user_tracks.sort(key=lambda t: t.get("created_at", ""), reverse=True)

        return user_tracks[skip:skip + limit]

    async def delete_user_track(self, user_id: int, track_id: str) -> Tuple[bool, str]:

        if not self.catalog_db_service:
            return False, "Catalog service not available"

        track = self.catalog_db_service.tracks.get(track_id)
        if not track:
            return False, "Track not found"

        if track.get("uploaded_by_user_id") != user_id:
            return False, "You can only delete your own uploads"

        try:
            await asyncio.to_thread(
                self.catalog_db_service._delete_track_from_db,
                track_id
            )
        except Exception as e:
            log_service.error(f"Failed to delete from database: {e}")

        self.catalog_db_service.remove_track_from_memory(track_id)

        files_to_remove = self._catalog_output_paths(track_id)
        user_tracks_dir = self._get_user_tracks_dir(user_id)
        files_to_remove.extend(await asyncio.to_thread(lambda: list(user_tracks_dir.glob(f"{track_id}*"))))

        deleted_count = await self._remove_paths(files_to_remove)
        stems_dir = settings.DEMUCS_STEMS_DIR / track_id
        if stems_dir.is_dir():
            await asyncio.to_thread(shutil.rmtree, stems_dir, True)
        await self._forget_track_preferences(track_id)
        asset_integrity_service.notify_tracks_changed([track_id], "upload deleted")

        log_service.upload(f"[Upload] {log_service.who(user_id=user_id)} deleted {track_id} ({deleted_count} files)")
        return True, "Track deleted successfully"

    @staticmethod
    async def _forget_track_preferences(track_id: str):
        from sqlalchemy import delete
        from database import AsyncSessionLocal, TrackPreference
        from services.user_data_cache_service import user_data_cache
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(delete(TrackPreference).where(TrackPreference.track_id == track_id)
                                          .returning(TrackPreference.user_id))
                user_ids = {row[0] for row in result.all()}
                await db.commit()
            for uid in user_ids:
                await user_data_cache.invalidate_preferences(uid)
        except Exception as e:
            log_service.warning(f"[Upload] Clearing likes/bans for deleted {track_id} failed: {e}")

    async def update_track_metadata(
        self,
        user_id: int,
        track_id: str,
        updates: Dict[str, Any],
        artist: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str]:

        if not self.catalog_db_service:
            return False, "Catalog service not available"

        current = self.catalog_db_service.tracks.get(track_id)
        if not current:
            return False, "Track not found"

        if current.get("uploaded_by_user_id") != user_id:
            return False, "You can only edit your own uploads"

        track = json.loads(json.dumps(current))
        params = track.setdefault("generation_params", {})
        info = track.setdefault("track_info", {})
        tags = track.setdefault("derived_tags", {})

        if "title" in updates:
            title = " ".join(str(updates["title"] or "").split())[:TITLE_MAX]
            if not title:
                return False, "Title can't be empty"
            params["title"] = info["title"] = title
        if artist is not None:
            params["artist_name"] = info["artist"] = artist["name"]
            track["artist_profile_id"] = artist["id"]
            track["artist_slug"] = artist["slug"]
        if "primary_genre" in updates:
            genre = " ".join(str(updates["primary_genre"] or "").split())[:TAG_MAX]
            if not genre:
                return False, "Genre can't be empty"
            tags["primary_genre"] = genre
        for key in ("secondary_genres", "mood_keywords"):
            if key in updates:
                tags[key] = _clean_tag_list(updates[key])
        if "lyrics" in updates:
            lyrics = str(updates["lyrics"] or "").strip()[:LYRICS_MAX]
            track["transcribed_lyrics"] = lyrics or None
            params["prompt"] = lyrics
            params["instrumental"] = not lyrics
            if not lyrics or tags.get("vocals") in (None, "instrumental"):
                tags["vocals"] = settled_vocals(track) or "unknown"
        if "visibility" in updates:
            if updates["visibility"] not in VISIBILITIES:
                return False, "Visibility must be public, unlisted or private"
            track["visibility"] = updates["visibility"]
        if "explicit" in updates:
            track["explicit"] = bool(updates["explicit"])
        if "description" in updates:
            track["description"] = str(updates["description"] or "").strip()[:DESCRIPTION_MAX] or None

        track["updated_at"] = datetime.now(timezone.utc).isoformat()

        try:
            await asyncio.to_thread(self.catalog_db_service.write_track_metadata, track_id, track)
        except OSError as e:
            return False, f"Failed to save updates: {e}"
        except Exception as e:
            log_service.warning(f"Failed to update database: {e}")

        self.catalog_db_service.add_track_to_memory(track_id, track)
        if self.vector_db_service:
            try:
                await asyncio.to_thread(self.vector_db_service.add_single_track, track)
            except Exception as e:
                log_service.warning(f"[Upload] Re-indexing {track_id} after edit failed: {e}")
        if "lyrics" in updates:
            await self._remove_paths([settings.LYRIC_TIMESTAMPS_DIR / f"{track_id}.json"])
            asset_integrity_service.notify_tracks_changed([track_id], "lyrics edited")

        log_service.upload(f"[Upload] {log_service.who(user_id=user_id)} edited {track_id}: "
                           f"{', '.join(sorted(set(updates) | ({'artist'} if artist else set())))}")
        return True, "Track updated successfully"

    async def retag_artist(self, profile: Dict[str, Any]) -> int:
        if not self.catalog_db_service:
            return 0
        changed = 0
        for track_id, metadata in list(self.catalog_db_service.tracks.items()):
            if metadata.get("artist_profile_id") != profile["id"]:
                continue
            ok, _message = await self.update_track_metadata(metadata.get("uploaded_by_user_id"), track_id, {},
                                                            artist=profile)
            changed += int(ok)
        return changed
