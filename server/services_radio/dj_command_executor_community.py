"""Ratings and listener posts for the DJs: liking or banning tracks and shoutouts, and saving the listener's own
shoutouts, replies and reviews."""
from services_radio.conversation_service import save_conversation_to_database



class ExecutorCommunity:
    async def execute_track_preference(self, session_dict, rating, target):
        from service_registry import services
        from services.preferences_service import preferences_service

        session_id = session_dict.get('session_id')
        user_id = session_dict.get('user_id')

        if not user_id or not session_id:
            return {"status": "refused", "reason": "Only signed-in listeners can rate tracks"}
        if rating not in self.RATINGS:
            return {"status": "error", "reason": f"Unknown rating '{rating}'"}

        track_id = self._resolve_track_id(session_id, target) if target else None
        if not track_id:
            await self._note(session_dict, "No track currently available to save preference", "error")
            return {"status": "error", "reason": "No track available for that position"}

        broadcast = services.websocket_service.broadcast_preference_change if services.websocket_service else None
        async with self.async_session_maker() as db:
            if rating == "clear":
                await preferences_service.remove_track_preference(
                    int(user_id), track_id, db, playback_service=self.playback_service, broadcast_callback=broadcast)
            else:
                await preferences_service.set_track_preference(
                    int(user_id), track_id, rating, db,
                    playback_service=self.playback_service, broadcast_callback=broadcast)

        track_name = self._track_label(track_id) or "this track"
        await self._note(session_dict, {"like": f"Liked {track_name}", "super_like": f"Super-liked {track_name}",
                                        "ban": f"Banned {track_name}",
                                        "clear": f"Cleared your rating of {track_name}"}[rating])
        return {"status": "ok", "rating": rating, "track": track_name}

    async def execute_shoutout_preference(self, session_dict, rating, shoutout_id=None):
        from service_registry import services
        from services.community_engagement import community_engagement
        from services.preferences_service import preferences_service
        from services.user_content_database_service import kind_of
        from services_radio import community_on_air

        session_id = session_dict.get('session_id')
        user_id = session_dict.get('user_id')
        if not user_id or not session_id or self.user_content_service is None:
            return {"status": "refused", "reason": "Only signed-in listeners can rate shoutouts"}
        if rating not in self.RATINGS:
            return {"status": "error", "reason": f"Unknown rating '{rating}'"}

        own = f"{user_id}_"
        if not shoutout_id:
            aired = [sid for sid in community_engagement.last_aired(session_id) if not sid.startswith(own)]
            if len(aired) != 1:
                return {"status": "not_found",
                        "reason": "Several listener posts have played recently: call what_aired to see them and pass "
                                  "the shoutout_id of the one they mean" if aired
                        else "No listener post has played recently: call what_aired to look further back, or ask "
                             "which one they mean"}
            shoutout_id = aired[0]
        post = self.user_content_service.get_shoutout(shoutout_id)
        if not post:
            return {"status": "not_found", "reason": "No shoutout, reply or review with that id"}

        broadcast = services.websocket_service.broadcast_preference_change if services.websocket_service else None
        try:
            async with self.async_session_maker() as db:
                if rating == "clear":
                    await preferences_service.remove_shoutout_preference(int(user_id), shoutout_id, db,
                                                                         broadcast_callback=broadcast)
                else:
                    await preferences_service.set_shoutout_preference(
                        int(user_id), shoutout_id, rating, db, broadcast_callback=broadcast)
        except ValueError as e:
            return {"status": "refused", "reason": str(e)}

        label = f"{kind_of(post)} from {community_on_air.speaker(post)}"
        await self._note(session_dict, {"like": f"Liked the {label}", "super_like": f"Super-liked the {label}",
                                        "ban": f"Banned the {label}",
                                        "clear": f"Cleared your rating of the {label}"}[rating])
        return {"status": "ok", "rating": rating, "rated": label, "said": community_on_air.text_of(post)[:160]}

    async def save_community_item(self, session_dict, kind: str, text: str = "", parent_id=None, track_id=None):
        from pathlib import Path
        from services.user_content_database_service import track_ref
        user_id = session_dict.get('user_id')
        session_id = session_dict.get('session_id')
        labels = {"shoutout": "Shoutout", "reply": "Reply", "review": "Review"}
        track = track_ref(self.catalog_service.get_track(track_id)) if track_id else None
        item = None
        if user_id and self.user_content_service:
            common = dict(enhancement_service=self.user_content_speech_enhancement_service,
                          ai_service=self.gemini_ai_service, vector_db_service=self.user_content_vector_db_service,
                          broadcast_callback=self.broadcast_content_func, parent_id=parent_id, track=track)
            recording = session_dict.get('recording')
            if recording:
                item = await self.user_content_service.create_voice_item(int(user_id), kind, Path(recording), **common)
            else:
                user_data = await self._user_data(user_id)
                item = await self.user_content_service.create_text_item(int(user_id), kind, text, user_data, **common)

        what = f"{labels.get(kind, kind)}" + (f" on {track['title']}" if track else "")
        if item:
            message = {'info': f"{what} saved: \"{item.get('full_transcription', '')}\""}
        else:
            message = {'error': f"Couldn't save that {kind.lower()}"}
        async with self.async_session_maker() as db:
            await save_conversation_to_database(user_id=user_id, db=db, message_type='shoutouts', **message)
        if session_id:
            await self.sio.emit('conversation_update', {**message, 'message_type': 'shoutouts'}, room=session_id)
        return {"status": "ok" if item else "error", "id": (item or {}).get("id")}
