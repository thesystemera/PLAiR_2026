"""Playback and stations for the DJs: transport, seed radio (one aspect or a weighted blend), playlists, moving playback
to another device and Radio Mode settings."""
import re
from services_radio.conversation_service import save_conversation_to_database
from database.models import User
from services import log_service

SEED_MODE_DISPLAY = {
    "mood": "mood",
    "style": "style",
    "theme": "theme",
    "lyrics": "lyrics",
    "vocal": "vocal",
    "secondary_genres": "secondary genres",
    "primary_genre": "primary genre",
    "similar_artists": "similar artists",
    "primary_artist": "primary artist",
    "all": "all categories",
}
PLAYLIST_DISPLAY = {
    "favorites": "your favorites",
    "discovery": "smart discovery",
    "top_hits_all": "all-time top hits",
    "top_hits_week": "this week's top hits",
    "top_hits_day": "today's top hits",
}


class ExecutorPlayback:
    def _upcoming_track_id(self, session_id, title):
        state = self.playback_service.get_state(session_id) or {}
        queue = state.get('queue') or []
        start = (state.get('current_index') or 0) + 1 if state.get('current_track') else 0
        upcoming = [self._track_id_of(item) for item in queue[start:]]
        if not title:
            return upcoming[0] if upcoming else None
        return next((track_id for track_id in upcoming
                     if self._name_matches(title, track_id, "title") or self._name_matches(title, track_id, "artist")),
                    None)

    async def execute_playback_control(self, session_dict, action, position_s=None, title=None):
        session_id = session_dict.get('session_id')
        user_id = session_dict.get('user_id')

        if not session_id:
            return {"status": "error", "reason": "No active playback session"}

        if action in ("restart", "seek"):
            log_service.detail(f"[COMMAND EXECUTOR] Executing: {action} {position_s or 0}s", "commands")
            if not await self.playback_service.seek(session_id, int((position_s or 0) * 1000) if action == "seek" else 0):
                return {"status": "error", "reason": "Nothing is playing"}
            state = self.playback_service.get_state(session_id) or {}
            return {"status": "ok", "action": action, "position": log_service.clock(state.get("progress_ms")),
                    "track": self._track_label(self._resolve_track_id(session_id, "current"))}

        if action == "remove":
            track_id = self._upcoming_track_id(session_id, title)
            if not track_id:
                return {"status": "not_found", "up_next": self._upcoming_labels(session_id, limit=8),
                        "reason": "No upcoming track matches that" if title else "Nothing is queued after this track"}
            label = self._track_label(track_id)
            log_service.detail(f"[COMMAND EXECUTOR] Executing: Remove {label} from the queue", "commands")
            if not await self.playback_service.remove_from_queue(session_id, track_id, user_id=user_id):
                return {"status": "error", "reason": "That track is no longer in the queue"}
            return {"status": "ok", "action": action, "removed": label, "up_next": self._upcoming_labels(session_id)}

        if action == "next":
            log_service.detail("[COMMAND EXECUTOR] Executing: Skip to next track", "commands")
            await self.playback_service.next(session_id, user_id=user_id)
        elif action == "previous":
            log_service.detail("[COMMAND EXECUTOR] Executing: Skip to previous track", "commands")
            await self.playback_service.previous(session_id)
        elif action == "pause":
            log_service.detail("[COMMAND EXECUTOR] Executing: Pause playback", "commands")
            await self.playback_service.pause(session_id)
        elif action == "resume":
            log_service.detail("[COMMAND EXECUTOR] Executing: Resume playback", "commands")
            await self.playback_service.play(session_id)
        else:
            return {"status": "error", "reason": f"Unknown playback action '{action}'"}

        return {
            "status": "ok",
            "action": action,
            "now_playing": self._track_label(self._resolve_track_id(session_id, "current"))
        }

    async def execute_seed_radio(self, session_dict, category, target="current", blend=None):
        session_id = session_dict.get('session_id')
        user_id = session_dict.get('user_id')

        if not session_id:
            return {"status": "error", "reason": "No active playback session"}

        if blend:
            mode_display = " + ".join(f"{SEED_MODE_DISPLAY.get(item['category'], item['category'])} {item['weight']:g}"
                                      + (f" ('{item['words']}')" if item.get("words") else "") for item in blend)
        else:
            mode_display = SEED_MODE_DISPLAY.get(category)
        if not mode_display:
            return {"status": "error", "reason": f"Unknown seed category '{category}'"}

        needs_song = not blend or any(not item.get("words") for item in blend)
        track_id = self._resolve_track_id(session_id, target) if needs_song else None
        if needs_song and not track_id:
            log_service.error(f"No {target} track available for seeding")
            return {"status": "error", "reason": "Nothing is playing to seed the radio from" if target == "current"
                    else f"There is no {target} track to seed the radio from"}

        track_name = (self._track_label(track_id) or "this track") if track_id else "the listener's words"

        log_service.detail(f"[COMMAND EXECUTOR] Seeding radio based on {track_name} ({mode_display})", "commands")

        seeded = await self.playback_service.seed_radio(
            session_id, category=category or blend[0]["category"],
            track_id=None if target == "current" else track_id, user_id=user_id, blend=blend)

        if not seeded:
            if self.broadcast_playback_state_callback:
                await self.broadcast_playback_state_callback(session_id, self.playback_service.get_state(session_id))
            return {"status": "error", "reason": "Couldn't build a station from the current track"}

        feedback_msg = f"Seeding radio based on {track_name} ({mode_display})"

        if user_id:
            async with self.async_session_maker() as db:
                await save_conversation_to_database(
                    user_id=user_id,
                    db=db,
                    info=feedback_msg,
                    message_type='interactive'
                )

        await self.sio.emit('conversation_update', {
            'info': feedback_msg,
            'message_type': 'interactive'
        }, room=session_id)

        return {
            "status": "ok",
            "station": mode_display,
            "seed_track": track_name,
            "up_next": self._upcoming_labels(session_id)
        }

    async def execute_move_playback(self, session_dict, device):
        from service_registry import services

        session_id = session_dict.get('session_id')
        user_id = session_dict.get('user_id')
        if not user_id or not session_id or services.device_management_service is None \
                or services.websocket_service is None:
            return {"status": "refused", "reason": "Only signed-in listeners can move playback between their devices"}

        def norm(text):
            return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split())

        online = services.websocket_service.get_online_device_ids(session_id)
        async with self.async_session_maker() as db:
            devices = await services.device_management_service.get_user_devices(
                int(user_id), db, online_device_ids=online, only_online=True)
        active_id = (self.playback_service.get_state(session_id) or {}).get("active_device_id")
        listing = [{"name": d["device_name"], "type": d["device_type"], "playing_here": d["device_id"] == active_id}
                   for d in devices]
        wanted = norm(device)
        if not wanted:
            return {"status": "ok", "online_devices": listing}
        matches = [d for d in devices
                   if wanted in norm(d["device_name"]) or norm(d["device_name"]) in wanted
                   or wanted == norm(d["device_type"])]
        if len(matches) != 1:
            return {"status": "not_found", "online_devices": listing,
                    "reason": "More than one online device fits that name" if matches
                    else "None of this listener's online devices fits that name (the app must be open on it)"}
        target = matches[0]
        if target["device_id"] == active_id:
            return {"status": "ok", "device": target["device_name"], "note": "Playback is already on that device."}
        if services.radio_mode_service is not None:
            await services.radio_mode_service.on_transfer(session_id, target["device_id"])
        if not await self.playback_service.transfer_playback(session_id, target["device_id"]):
            return {"status": "error", "reason": "Couldn't move playback to that device", "online_devices": listing}
        await self._note(session_dict, f"Playback moved to {target['device_name']}")
        return {"status": "ok", "device": target["device_name"],
                "now_playing": self._track_label(self._resolve_track_id(session_id, "current"))}

    async def execute_radio_settings(self, session_dict, changes):
        from service_registry import services
        from services.preferences_service import preferences_service
        from services_radio.radio_schedule import RadioPrefs

        session_id = session_dict.get('session_id')
        user_id = session_dict.get('user_id')
        if not session_id:
            return {"status": "error", "reason": "No active session"}

        if user_id:
            async with self.async_session_maker() as db:
                if changes:
                    user = await db.get(User, int(user_id))
                    prefs = await preferences_service.apply_radio_settings(user, changes, db)
                else:
                    prefs = await preferences_service.get_radio_settings(int(user_id), db)
        else:
            radio = services.radio_mode_service
            sess = radio.sessions.get(session_id) if radio is not None else None
            prefs = (sess.prefs if sess is not None else RadioPrefs()).to_dict()
            if changes:
                prefs.update(changes)
                if services.websocket_service is not None:
                    await services.websocket_service.broadcast_to_session(
                        session_id, {"type": "radio_mode_updated", "data": {"settings": changes}})

        if not changes:
            return {"status": "ok", "settings": prefs}
        await self._note(session_dict, "Radio Mode settings updated: " + ", ".join(
            f"{key.replace('_', ' ')} {('on' if value else 'off') if isinstance(value, bool) else value}"
            for key, value in changes.items()))
        return {"status": "ok", "changed": changes, "settings": prefs}

    async def execute_playlist(self, session_dict, mode):
        session_id = session_dict.get('session_id')
        user_id = session_dict.get('user_id')

        if not session_id:
            return {"status": "error", "reason": "No active playback session"}

        mode_display = PLAYLIST_DISPLAY.get(mode)
        if not mode_display:
            return {"status": "error", "reason": f"Unknown playlist '{mode}'"}

        log_service.detail(f"[COMMAND EXECUTOR] Playing playlist: {mode_display}", "commands")

        await self.playback_service.seed_radio(session_id, category=mode, user_id=user_id)

        feedback_msg = f"Playing {mode_display}"

        if user_id:
            async with self.async_session_maker() as db:
                await save_conversation_to_database(
                    user_id=user_id,
                    db=db,
                    info=feedback_msg,
                    message_type='interactive'
                )

        await self.sio.emit('conversation_update', {
            'info': feedback_msg,
            'message_type': 'interactive'
        }, room=session_id)

        return {
            "status": "ok",
            "playlist": mode,
            "now_playing": self._track_label(self._resolve_track_id(session_id, "current")),
            "up_next": self._upcoming_labels(session_id)
        }
