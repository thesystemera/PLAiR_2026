import asyncio
import datetime
import re
from sqlalchemy import select
from services_radio.conversation_service import save_conversation_to_database
from services_radio import listener_location as location_resolver
from services_radio.dj_prompt_helper_service import UnavailableSegment
from services_radio.dj_content_bank import content_bank
from services.task_utils import spawn
from database.models import User
from services import log_service
from services.user_data_cache_service import user_data_cache

SEARCH_CATEGORY_PREFIXES = {
    "primary_artist": "Artist",
    "similar_artists": "Similar Artists",
    "song_title": "Song",
    "primary_genre": "Genre",
    "secondary_genres": "Subgenre",
    "mood": "Mood",
    "style": "Style",
    "theme": "Theme",
    "vocal": "Vocal",
    "lyrics": "Lyrics",
}

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

NEWS_CATEGORIES = ["world", "nation", "business", "technology", "entertainment", "sports", "science", "health"]

TRACK_TARGET_LABELS = {"current": "current track", "previous": "previous track", "next": "next track"}

INTERPRETATION_GATE_TIMEOUT_S = 90.0

SAVE_COMMAND_PATTERN = re.compile(r'\{(?:save_shoutout|save_shoutout_reply|save_opinion|opinion)[}:]')
SHOUTOUT_REPLY_INLINE = re.compile(r'\{save_shoutout_reply:([0-9_]+)\}')
SHOUTOUT_ID = re.compile(r'^\d+_\d+$')


class CommandExecutorService:
    def __init__(self, dj_prompt_service, news_service, location_service, events_service, web_service,
                 user_content_speech_enhancement_service, user_content_service, user_content_vector_search_service,
                 tts_queue_manager, sio, async_session_maker,
                 vector_search_service, playback_service, catalog_service, gemini_ai_service=None,
                 user_content_vector_db_service=None, broadcast_content_func=None, broadcast_playback_state_callback=None):
        self.dj_prompt_service = dj_prompt_service
        self.gemini_ai_service = gemini_ai_service
        self.news_service = news_service
        self.location_service = location_service
        self.events_service = events_service
        self.web_service = web_service
        self.user_content_speech_enhancement_service = user_content_speech_enhancement_service
        self.user_content_service = user_content_service
        self.user_content_vector_search_service = user_content_vector_search_service
        self.tts_queue_manager = tts_queue_manager
        self.sio = sio
        self.async_session_maker = async_session_maker
        self.vector_search_service = vector_search_service
        self.playback_service = playback_service
        self.catalog_service = catalog_service
        self.user_content_vector_db_service = user_content_vector_db_service
        self.broadcast_content_func = broadcast_content_func
        self.broadcast_playback_state_callback = broadcast_playback_state_callback

    def _extract_value_from_action(self, action, prefix):
        start_index = action.find(f"{prefix}}}") + len(f"{prefix}}}")
        if start_index != -1:
            start_index = action.find('"', start_index)
            if start_index != -1:
                end_index = action.find('"', start_index + 1)
                if end_index != -1:
                    return action[start_index + 1:end_index].strip()
        return ""

    def _parse_search_query(self, command):
        for category, prefix in SEARCH_CATEGORY_PREFIXES.items():
            if f"{{{category}}}" in command:
                return f"{prefix}: {self._extract_value_from_action(command, category)}"
        return ""

    @staticmethod
    def _brace_target(command):
        if "{current}" in command:
            return "current"
        if "{earlier}" in command:
            return "previous"
        if "{later}" in command:
            return "next"
        return None

    @staticmethod
    def _track_id_of(item):
        if isinstance(item, dict):
            return item.get('id')
        return item

    def _resolve_track_id(self, session_id, target):
        state = self.playback_service.get_state(session_id) or {}
        if target == "current":
            return self._track_id_of(state.get('current_track') or {})
        if target == "previous":
            history = state.get('history') or []
            return self._track_id_of(history[-1]) if history else None
        if target == "next":
            queue = state.get('queue') or []
            next_index = (state.get('current_index') or 0) + 1 if state.get('current_track') else 0
            return self._track_id_of(queue[next_index]) if next_index < len(queue) else None
        return None

    def _track_label(self, track_id):
        track = self.catalog_service.get_track(track_id) if track_id else None
        if not track:
            return None
        title = track.get('generation_params', {}).get('title', '')
        artist = track.get('generation_params', {}).get('artist_name', '')
        if title and artist:
            return f"'{title}' by {artist}"
        if title:
            return f"'{title}'"
        return None

    def _upcoming_labels(self, session_id, limit=3):
        state = self.playback_service.get_state(session_id) or {}
        queue = state.get('queue') or []
        start = (state.get('current_index') or 0) + 1 if state.get('current_track') else 0
        labels = []
        for item in queue[start:start + limit]:
            label = self._track_label(self._track_id_of(item))
            if label:
                labels.append(label)
        return labels

    @staticmethod
    async def _await_gate(gate):
        if gate is None:
            return
        try:
            await asyncio.wait_for(gate.wait(), timeout=INTERPRETATION_GATE_TIMEOUT_S)
        except asyncio.TimeoutError:
            log_service.warning("[COMMAND EXECUTOR] Interpretation gate timed out - releasing segment")

    async def process_commands(self, commands, session_dict):
        session_id = session_dict.get('session_id')

        log_service.detail(f"[COMMAND EXECUTOR] Processing commands for session {session_id}", "commands")

        grouped_commands = {
            "play": [],
            "cue": [],
            "other": []
        }

        for command in commands.split('\n'):
            command = command.replace('[HAL11000]', '').strip()
            if command:
                log_service.detail(f"[COMMAND EXECUTOR] Parsing: {command}", "commands")
            if "{play}" in command and "{play_shoutouts}" not in command and "{seed}" not in command and "{playlist}" not in command:
                grouped_commands["play"].append(command)
            elif "{cue}" in command:
                grouped_commands["cue"].append(command)
            else:
                grouped_commands["other"].append(command)

        log_service.detail(
            f"[COMMAND EXECUTOR] Grouped: {len(grouped_commands['play'])} Play, {len(grouped_commands['cue'])} Cue, {len(grouped_commands['other'])} Other", "commands")

        searches = [(self._parse_search_query(command), True) for command in grouped_commands["play"]]
        searches += [(self._parse_search_query(command), False) for command in grouped_commands["cue"]]
        if searches:
            await self.execute_searches(session_dict, searches)

        for command in grouped_commands["other"]:
            if "{next}" in command and session_id:
                await self.execute_playback_control(session_dict, "next")
            elif "{previous}" in command and session_id:
                await self.execute_playback_control(session_dict, "previous")
            elif "{mute}" in command and session_id:
                await self.execute_playback_control(session_dict, "pause")
            elif "{activate}" in command and session_id:
                await self.execute_playback_control(session_dict, "resume")
            elif "{continue}" in command and session_id:
                await self._continue_playback(session_dict)
            elif "{seed}" in command and session_id:
                log_service.detail(f"[COMMAND EXECUTOR] Executing: Seed radio - {command}", "commands")
                spawn(self._handle_seed_radio(command, session_dict), name="_handle_seed_radio")
            elif "{playlist}" in command and session_id:
                log_service.detail(f"[COMMAND EXECUTOR] Executing: Playlist mode - {command}", "commands")
                spawn(self._handle_playlist(command, session_dict), name="_handle_playlist")
            elif "{like}" in command or "{dislike}" in command or "{ban}" in command or "{superstar}" in command:
                log_service.detail(f"[COMMAND EXECUTOR] Executing: Track preference - {command}", "commands")
                spawn(self._handle_track_preference(command, session_dict), name="_handle_track_preference")
            elif "{lyrics}" in command:
                log_service.detail("[COMMAND EXECUTOR] Executing: Fetch lyrics from catalog", "commands")
                self.spawn_segment(self._trigger_lyrics_interpretation(command, session_dict), session_dict,
                                   "_trigger_lyrics_interpretation")
            elif "{biography}" in command:
                query = self._extract_value_from_action(command, "biography")
                log_service.detail(f"[COMMAND EXECUTOR] Executing: Fetch biography for '{query}'", "commands")
                self.spawn_segment(self._trigger_biography_interpretation(query, session_dict), session_dict,
                                   "_trigger_biography_interpretation")
            elif "{news}" in command:
                log_service.detail(f"[COMMAND EXECUTOR] Executing: Fetch news - {command}", "commands")
                self.spawn_segment(self._process_news_command(command, session_dict), session_dict,
                                   "_process_news_command")
            elif "{events}" in command:
                log_service.detail(f"[COMMAND EXECUTOR] Executing: Fetch events - {command}", "commands")
                self.spawn_segment(self._process_events_command(command, session_dict), session_dict,
                                   "_process_events_command")
            elif "{find_amenities}" in command:
                query = self._extract_value_from_action(command, "find_amenities")
                log_service.detail(f"[COMMAND EXECUTOR] Executing: Find amenities for '{query}'", "commands")
                self.spawn_segment(self._trigger_location_search_interpretation(query, session_dict), session_dict,
                                   "_trigger_location_search_interpretation")
            elif "{weather}" in command:
                forecast_type = "current"
                if "{today}" in command:
                    forecast_type = "today"
                elif "{tomorrow}" in command:
                    forecast_type = "tomorrow"
                elif "{this_week}" in command:
                    forecast_type = "week"
                log_service.detail(f"[COMMAND EXECUTOR] Executing: Fetch weather forecast ({forecast_type})", "commands")
                self.spawn_segment(self._trigger_weather_interpretation(forecast_type, session_dict), session_dict,
                                   "_trigger_weather_interpretation")
            elif SAVE_COMMAND_PATTERN.search(command) and not self._voice_turn(session_dict):
                log_service.warning(f"[COMMAND EXECUTOR] Skipping {command} - saving needs the listener's own voice recording")
            elif "{save_shoutout}" in command:
                log_service.detail("[COMMAND EXECUTOR] Executing: Save user shoutout", "commands")
                spawn(self._process_shoutout("save", session_dict), name="_process_shoutout")
            elif "{save_shoutout_reply" in command:
                parent_id = self._shoutout_reply_parent(command)
                if parent_id:
                    log_service.detail(f"[COMMAND EXECUTOR] Executing: Save shoutout reply to {parent_id}", "commands")
                    spawn(self._save_shoutout_reply(session_dict, parent_id), name="_save_shoutout_reply")
                else:
                    log_service.error(f"[COMMAND EXECUTOR] save_shoutout_reply missing or invalid parent_id: {command}")
            elif "{play_shoutouts}" in command:
                query = self._extract_value_from_action(command, "play_shoutouts")
                log_service.detail(
                    f"[COMMAND EXECUTOR] Executing: Play community shoutouts (Query: {query if query else 'Generic'})", "commands")
                self.spawn_segment(self._trigger_shoutouts_interpretation(session_dict, query), session_dict,
                                   "_trigger_shoutouts_interpretation")
            elif "{save_opinion}" in command or "{opinion}" in command:
                opinion = "opinion"
                if "{current}" in command:
                    opinion += " current"
                elif "{earlier}" in command:
                    opinion += " previous"
                elif "{later}" in command:
                    opinion += " next"
                log_service.detail(f"[COMMAND EXECUTOR] Executing: Save user opinion ({opinion})", "commands")
                spawn(self._process_opinion(opinion, session_dict), name="_process_opinion")

    @staticmethod
    def spawn_segment(coro, session_dict, name):
        task = spawn(coro, name=name)
        turn_tasks = session_dict.get('_turn_tasks')
        if turn_tasks is not None:
            turn_tasks.add(task)
            task.add_done_callback(turn_tasks.discard)
        return task

    @staticmethod
    def _voice_turn(session_dict):
        return session_dict.get('origin', 'voice') == 'voice'

    def _shoutout_reply_parent(self, command):
        match = SHOUTOUT_REPLY_INLINE.search(command)
        parent_id = match.group(1) if match else self._extract_value_from_action(command, "save_shoutout_reply")
        return parent_id if SHOUTOUT_ID.match(parent_id or "") else None

    async def _continue_playback(self, session_dict):
        state = self.playback_service.get_state(session_dict.get('session_id')) or {}
        if state.get('current_track') and not state.get('is_playing'):
            await self.execute_playback_control(session_dict, "resume")
        else:
            log_service.detail("[COMMAND EXECUTOR] Continue: playback already running - nothing to do", "commands")

    async def execute_searches(self, session_dict, searches):
        session_id = session_dict.get('session_id')
        user_id = session_dict.get('user_id')

        banned_ids = set()
        if user_id:
            banned_ids = await user_data_cache.get_banned_ids(user_id)

        tracks_to_add = []
        play_first = False
        per_query = []

        for query, play in searches:
            if not query:
                continue
            log_service.detail(f"[COMMAND EXECUTOR] Category search: {query}", "commands")
            results = await self.vector_search_service.search(
                query=query,
                n_results=5,
                banned_ids=banned_ids if banned_ids else None
            )
            track_ids = [track["id"] for track in results]
            tracks_to_add.extend(track_ids)
            per_query.append({"query": query, "found": len(track_ids)})
            if play and not play_first:
                play_first = True
            log_service.detail(f"[COMMAND EXECUTOR] Found {len(track_ids)} tracks for: {query}", "commands")

            if session_id and track_ids:
                feedback_msg = f"Found {len(track_ids)} track{'s' if len(track_ids) != 1 else ''} for '{query}'"
                await self.sio.emit('conversation_update', {'info': feedback_msg, 'message_type': 'interactive'}, room=session_id)

        now_playing = None
        if tracks_to_add and session_id:
            log_service.detail(f"[COMMAND EXECUTOR] Adding {len(tracks_to_add)} tracks to queue", "commands")
            await self.playback_service.add_to_queue(session_id, tracks_to_add, user_id=user_id)
            if play_first and tracks_to_add:
                log_service.detail(f"[COMMAND EXECUTOR] Playing first track: {tracks_to_add[0]}", "commands")
                await self.playback_service.play(session_id, tracks_to_add[0], user_id=user_id)
                now_playing = self._track_label(tracks_to_add[0])
            log_service.detail(f"[COMMAND EXECUTOR] Successfully added {len(tracks_to_add)} tracks to queue", "commands")

        if searches:
            feedback_type = 'info'

            if tracks_to_add:
                track_details = []
                for track_id in tracks_to_add[:3]:
                    track = self.catalog_service.get_track(track_id)
                    if track:
                        title = track.get('generation_params', {}).get('title', '')
                        artist = track.get('generation_params', {}).get('artist_name', '')
                        if title and artist:
                            track_details.append(f"'{title}' by {artist}")
                        elif title:
                            track_details.append(f"'{title}'")

                track_count = len(tracks_to_add)
                remaining = track_count - len(track_details)

                if track_count == 1:
                    feedback_msg = f"Added {track_details[0]} to your queue"
                elif track_count == 2:
                    feedback_msg = f"Added {track_details[0]} and {track_details[1]} to your queue"
                elif track_count == 3:
                    feedback_msg = f"Added {', '.join(track_details[:2])}, and {track_details[2]} to your queue"
                else:
                    feedback_msg = f"Added {', '.join(track_details)}, and {remaining} more tracks to your queue"

                if track_count < 3:
                    feedback_type = 'warning'
            else:
                feedback_msg = "No tracks found matching your search"
                feedback_type = 'error'

            if feedback_msg:
                if user_id:
                    async with self.async_session_maker() as db:
                        await save_conversation_to_database(
                            user_id=user_id,
                            db=db,
                            **{f'{feedback_type}': feedback_msg},
                            message_type='interactive'
                        )
                await self.sio.emit('conversation_update', {
                    feedback_type: feedback_msg,
                    'message_type': 'interactive'
                }, room=session_id)

        queued = [label for label in (self._track_label(track_id) for track_id in tracks_to_add[:5]) if label]
        return {
            "status": "ok" if tracks_to_add else "no_results",
            "found": len(tracks_to_add),
            "searches": per_query,
            "queued": queued,
            "now_playing": now_playing
        }

    async def execute_playback_control(self, session_dict, action):
        session_id = session_dict.get('session_id')
        user_id = session_dict.get('user_id')

        if not session_id:
            return {"status": "error", "reason": "No active playback session"}

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

    async def _handle_track_preference(self, command, session_dict):
        rating = None
        if "{dislike}" in command:
            rating = "dislike"
        elif "{like}" in command:
            rating = "like"
        elif "{superstar}" in command:
            rating = "superstar"
        elif "{ban}" in command:
            rating = "ban"
        await self.execute_track_preference(session_dict, rating, self._brace_target(command))

    async def execute_track_preference(self, session_dict, rating, target):
        from database.models import TrackPreference, PreferenceType

        session_id = session_dict.get('session_id')
        user_id = session_dict.get('user_id')

        if not user_id or not session_id:
            return {"status": "refused", "reason": "Only signed-in listeners can rate tracks"}

        track_id = self._resolve_track_id(session_id, target) if target else None

        if not track_id:
            if user_id:
                async with self.async_session_maker() as db:
                    await save_conversation_to_database(
                        user_id=user_id,
                        db=db,
                        error="No track currently available to save preference",
                        message_type='interactive'
                    )
            await self.sio.emit('conversation_update', {
                'error': "No track currently available to save preference",
                'message_type': 'interactive'
            }, room=session_id)
            return {"status": "error", "reason": "No track available for that position"}

        is_removal = rating == "dislike"
        preference_type = {
            "like": PreferenceType.LIKE,
            "superstar": PreferenceType.SUPER_LIKE,
            "ban": PreferenceType.BAN,
        }.get(rating)
        preference_name = "none" if is_removal else {
            "like": "like",
            "superstar": "super_like",
            "ban": "ban",
        }.get(rating)

        if not is_removal and not preference_type:
            return {"status": "error", "reason": f"Unknown rating '{rating}'"}

        async with self.async_session_maker() as db:
            result = await db.execute(
                select(TrackPreference).where(
                    TrackPreference.user_id == user_id,
                    TrackPreference.track_id == track_id
                )
            )
            existing = result.scalar_one_or_none()

            if is_removal:
                if existing:
                    await db.delete(existing)
                    await db.commit()
                    log_service.info(f"Preference: Removed preference for track {track_id}")
                else:
                    log_service.info(f"Preference: No preference to remove for track {track_id}")
            else:
                if existing:
                    existing.preference_type = preference_type
                else:
                    new_pref = TrackPreference(
                        user_id=user_id,
                        track_id=track_id,
                        preference_type=preference_type
                    )
                    db.add(new_pref)

                await db.commit()
                if preference_type is not None:
                    log_service.info(f"Preference: Set {preference_type.value} for track {track_id}")

        track_name = self._track_label(track_id) or "this track"

        feedback_msg = None
        if is_removal:
            if existing:
                feedback_msg = f"Removed preference for {track_name}"
        else:
            if preference_type == PreferenceType.BAN:
                feedback_msg = f"Banned {track_name}"
            elif preference_type == PreferenceType.SUPER_LIKE:
                feedback_msg = f"Added {track_name} to your favorites"
            elif preference_type == PreferenceType.LIKE:
                feedback_msg = f"Liked {track_name}"

        if feedback_msg:
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

        await user_data_cache.invalidate_user(user_id)

        await self.playback_service.handle_preference_change(
            session_id,
            user_id,
            track_id,
            preference_name
        )

        return {
            "status": "ok",
            "rating": rating,
            "track": track_name,
            "changed": bool(feedback_msg)
        }

    async def _process_opinion(self, opinion_type, session_dict):
        target = None
        if "current" in opinion_type:
            target = "current"
        elif "previous" in opinion_type:
            target = "previous"
        elif "next" in opinion_type:
            target = "next"
        await self.execute_save_opinion(session_dict, target)

    async def execute_save_opinion(self, session_dict, target):
        session_id = session_dict.get('session_id')
        if not session_id:
            log_service.error("No session_id provided")
            return {"status": "error", "reason": "No active session"}

        track_id = self._resolve_track_id(session_id, target) if target else None
        if not track_id:
            log_service.error(f"No track found for opinion target: {target}")
            return {"status": "error", "reason": "No track available for that position"}

        track = self.catalog_service.get_track(track_id)
        if not track:
            log_service.error(f"Track {track_id} not found in catalog")
            return {"status": "error", "reason": "Track not found in catalog"}

        return await self._save_opinion(track, session_dict)

    async def _save_opinion(self, track, session_dict):
        user_id = session_dict.get('user_id')
        if not user_id:
            return {"status": "refused", "reason": "Only signed-in listeners can save opinions"}

        if not self.user_content_service:
            log_service.error("User Content Service not available")
            return {"status": "error", "reason": "User content service unavailable"}

        log_service.detail(f"Delegating opinion processing for user {user_id} on track {track['id']}", "commands")

        success = await self.user_content_service.process_opinion_upload(
            user_id=user_id,
            track_info=track,
            enhancement_service=self.user_content_speech_enhancement_service,
            gemini_service=self.gemini_ai_service
        )

        session_id = session_dict.get('session_id')
        track_title = track.get('generation_params', {}).get('title', 'this track')
        track_artist = track.get('generation_params', {}).get('artist_name', '')
        track_name = f"'{track_title}' by {track_artist}" if track_artist else f"'{track_title}'"

        if success:
            feedback_msg = f"Your opinion on {track_name} has been saved"
            async with self.async_session_maker() as db:
                await save_conversation_to_database(
                    user_id=user_id,
                    db=db,
                    info=feedback_msg,
                    message_type='interactive'
                )
            if session_id:
                await self.sio.emit('conversation_update', {
                    'info': feedback_msg,
                    'message_type': 'interactive'
                }, room=session_id)
            return {"status": "ok", "track": track_name}

        feedback_msg = f"Failed to save opinion on {track_name} - no recent audio found"
        async with self.async_session_maker() as db:
            await save_conversation_to_database(
                user_id=user_id,
                db=db,
                error=feedback_msg,
                message_type='interactive'
            )
        if session_id:
            await self.sio.emit('conversation_update', {
                'error': feedback_msg,
                'message_type': 'interactive'
            }, room=session_id)
        return {"status": "error", "reason": "No recent voice recording found"}

    async def _process_shoutout(self, shoutout_type, session_dict):
        if "save" in shoutout_type:
            await self._save_shoutout(session_dict)
        else:
            log_service.error(f"Invalid shoutout type: {shoutout_type}")

    async def _save_shoutout(self, session_dict):
        user_id = session_dict.get('user_id')
        if not user_id:
            return {"status": "refused", "reason": "Only signed-in listeners can save shoutouts"}

        if not self.user_content_service:
            log_service.error("User Content Service not available")
            return {"status": "error", "reason": "User content service unavailable"}

        log_service.detail(f"Delegating shoutout processing for user {user_id}", "commands")

        success, transcription = await self.user_content_service.process_shoutout_upload(
            user_id=user_id,
            enhancement_service=self.user_content_speech_enhancement_service,
            gemini_service=self.gemini_ai_service,
            vector_db_service=self.user_content_vector_db_service,
            broadcast_callback=self.broadcast_content_func
        )

        session_id = session_dict.get('session_id')
        if success:
            feedback_msg = f"Shoutout saved: \"{transcription}\""
            async with self.async_session_maker() as db:
                await save_conversation_to_database(
                    user_id=user_id,
                    db=db,
                    info=feedback_msg,
                    message_type='shoutouts'
                )
            if session_id:
                await self.sio.emit('conversation_update', {
                    'info': feedback_msg,
                    'message_type': 'shoutouts'
                }, room=session_id)
            return {"status": "ok"}

        feedback_msg = "Failed to save shoutout - no recent audio found"
        async with self.async_session_maker() as db:
            await save_conversation_to_database(
                user_id=user_id,
                db=db,
                error=feedback_msg,
                message_type='shoutouts'
            )
        if session_id:
            await self.sio.emit('conversation_update', {
                'error': feedback_msg,
                'message_type': 'shoutouts'
            }, room=session_id)
        return {"status": "error", "reason": "No recent voice recording found"}

    async def _save_shoutout_reply(self, session_dict, parent_id: str):
        user_id = session_dict.get('user_id')
        if not user_id:
            return {"status": "refused", "reason": "Only signed-in listeners can reply to shoutouts"}

        if not self.user_content_service:
            log_service.error("User Content Service not available")
            return {"status": "error", "reason": "User content service unavailable"}

        if not await asyncio.to_thread(self.user_content_service.is_root_shoutout, parent_id):
            log_service.error(f"Cannot reply to {parent_id} - not a root shoutout or doesn't exist")
            session_id = session_dict.get('session_id')
            if session_id:
                await self.sio.emit('conversation_update', {
                    'error': "Cannot reply - parent shoutout not found or is already a reply",
                    'message_type': 'shoutouts'
                }, room=session_id)
            return {"status": "error", "reason": "Parent shoutout not found or is already a reply"}

        log_service.detail(f"Delegating shoutout reply processing for user {user_id} to parent {parent_id}", "commands")

        success, transcription = await self.user_content_service.process_shoutout_upload(
            user_id=user_id,
            enhancement_service=self.user_content_speech_enhancement_service,
            gemini_service=self.gemini_ai_service,
            vector_db_service=None,
            broadcast_callback=self.broadcast_content_func,
            parent_id=parent_id
        )

        session_id = session_dict.get('session_id')
        if success:
            feedback_msg = f"Reply saved: \"{transcription}\""
            async with self.async_session_maker() as db:
                await save_conversation_to_database(
                    user_id=user_id,
                    db=db,
                    info=feedback_msg,
                    message_type='shoutouts'
                )
            if session_id:
                await self.sio.emit('conversation_update', {
                    'info': feedback_msg,
                    'message_type': 'shoutouts'
                }, room=session_id)
            return {"status": "ok"}

        feedback_msg = "Failed to save reply - no recent audio found"
        async with self.async_session_maker() as db:
            await save_conversation_to_database(
                user_id=user_id,
                db=db,
                error=feedback_msg,
                message_type='shoutouts'
            )
        if session_id:
            await self.sio.emit('conversation_update', {
                'error': feedback_msg,
                'message_type': 'shoutouts'
            }, room=session_id)
        return {"status": "error", "reason": "No recent voice recording found"}

    async def _process_news_command(self, command, session_dict):
        query = self._extract_value_from_action(command, "news")
        scope = "world"
        if "{national}" in command:
            scope = "national"
        elif "{local}" in command:
            scope = "local"
        categories = [cat for cat in NEWS_CATEGORIES if f"{{{cat}}}" in command]
        await self.execute_news(session_dict, scope, categories, query)

    async def _listener_location(self, session_dict):
        user = None
        if session_dict.get('user_id'):
            async with self.async_session_maker() as db:
                result = await db.execute(select(User).where(User.id == session_dict.get('user_id')))
                user = result.scalar_one_or_none()
        return user, await location_resolver.resolve(user, session_dict.get('session_id'))

    async def execute_news(self, session_dict, scope, categories, query, gate=None):
        location = 'WORLD'
        if scope == "national":
            location = 'NATIONAL'
        elif scope == "local":
            _, listener = await self._listener_location(session_dict)
            location = listener.address or listener.news_location or 'NATIONAL'
        is_topic = bool(categories)
        if not query and categories:
            query = categories[0].upper()
        await self._trigger_news_interpretation(query or "general news", is_topic, [c.lower() for c in categories],
                                                location, session_dict, gate=gate)

    async def _process_events_command(self, command, session_dict):
        query = self._extract_value_from_action(command, "events")
        when = "month"
        if "{today}" in command:
            when = "today"
        elif "{tomorrow}" in command:
            when = "tomorrow"
        elif "{this_week}" in command:
            when = "week"
        await self.execute_events(session_dict, when, query)

    async def execute_events(self, session_dict, when, query, gate=None):
        _, listener = await self._listener_location(session_dict)
        location = listener.query_point() or listener.city or None
        country_code = listener.country_code or None
        start_date = datetime.datetime.now(datetime.timezone.utc)
        end_date = start_date + datetime.timedelta(days=30)
        if when == "today":
            end_date = start_date + datetime.timedelta(days=1)
        elif when == "tomorrow":
            start_date += datetime.timedelta(days=1)
            end_date = start_date + datetime.timedelta(days=1)
        elif when == "week":
            end_date = start_date + datetime.timedelta(days=7)
        await self._trigger_event_interpretation(query, location, country_code, start_date, end_date, session_dict,
                                                 gate=gate)

    async def _trigger_biography_interpretation(self, artist_name, session_dict, gate=None):
        user_id = session_dict.get('user_id')
        session_id = session_dict.get('session_id')

        gpt_call = self.dj_prompt_service.gpt_biography_interpretation(artist_name, session_dict)
        gpt_task = asyncio.ensure_future(gpt_call) if gate else None
        try:
            await self._await_gate(gate)
            gpt_response = await (gpt_task if gpt_task else gpt_call)
        finally:
            if gpt_task is not None and not gpt_task.done():
                gpt_task.cancel()
        if not gpt_response:
            return
        await self._air_segment(gpt_response, 'biography', f"Retrieved biography for {artist_name or 'this artist'}",
                                user_id, session_id)

    async def _air_segment(self, gpt_response, message_type, feedback_msg, user_id, session_id):
        feedback_key = 'info'
        if isinstance(gpt_response, UnavailableSegment):
            feedback_key, feedback_msg = 'warning', gpt_response.feedback
        script = str(gpt_response)

        async with self.async_session_maker() as db:
            await save_conversation_to_database(user_id, db, message_type=message_type, **{feedback_key: feedback_msg})
        if session_id:
            await self.sio.emit('conversation_update', {feedback_key: feedback_msg, 'message_type': message_type},
                                room=session_id)

        await self.tts_queue_manager.add_tts_request(script, user_id, message_type, is_broadcast=True,
                                                     is_temp_user=not user_id, session_id=session_id)
        if not isinstance(gpt_response, UnavailableSegment):
            content_bank.record_airing(session_id, script)
        async with self.async_session_maker() as db:
            await save_conversation_to_database(user_id, db, bot_response=script, message_type=message_type)
        await self.sio.emit('conversation_update', {'bot_response': script, 'message_type': message_type},
                            room=session_id)

    async def _trigger_lyrics_interpretation(self, command, session_dict):
        session_id = session_dict.get('session_id')
        if not session_id:
            return

        target = self._brace_target(command)
        query = "" if target else self._extract_value_from_action(command, "lyrics")
        track, track_title = await self.resolve_lyrics_track(session_dict, target, query)

        if not track:
            log_service.error(f"Lyrics: No track found for command: {command}")
            return

        await self.execute_lyrics_interpretation(track, track_title, session_dict)

    async def resolve_lyrics_track(self, session_dict, target, query):
        session_id = session_dict.get('session_id')
        if not session_id:
            return None, None

        if target:
            track_id = self._resolve_track_id(session_id, target)
            if track_id:
                return self.catalog_service.get_track(track_id), TRACK_TARGET_LABELS[target]
            return None, None

        if query:
            search_results = await self.vector_search_service.search(
                query=query,
                n_results=1,
                banned_ids=None
            )
            if search_results:
                return self.catalog_service.get_track(search_results[0]['id']), query

        return None, None

    async def execute_lyrics_interpretation(self, track, track_title, session_dict, gate=None):
        user_id = session_dict.get('user_id')
        session_id = session_dict.get('session_id')

        lyrics = track.get('generation_params', {}).get('prompt', '')
        lyrical_interpretation = track.get('derived_tags', {}).get('lyrical_interpretation', '')

        if not lyrics:
            log_service.error(f"Lyrics: No lyrics found in catalog for track {track.get('id')}")
            return

        artist_name = track.get('generation_params', {}).get('artist_name', 'Unknown Artist')
        if not track_title:
            track_title = track.get('generation_params', {}).get('title', 'Unknown Track')

        lyrics_context = f"LYRICS:\n{lyrics}"
        if lyrical_interpretation:
            lyrics_context += f"\n\nLYRICAL THEMES & INTERPRETATION:\n{lyrical_interpretation}"

        log_service.external(f"Lyrics: Retrieved from catalog for '{track_title}' by {artist_name}")

        gpt_call = self.dj_prompt_service.gpt_lyrics_interpretation(lyrics_context, artist_name, session_dict)
        gpt_task = asyncio.ensure_future(gpt_call) if gate else None
        try:
            await self._await_gate(gate)
            gpt_response = await (gpt_task if gpt_task else gpt_call)
        finally:
            if gpt_task is not None and not gpt_task.done():
                gpt_task.cancel()
        if not gpt_response:
            return
        await self._air_segment(gpt_response, 'lyrics', f"Found lyrics for '{track_title}' by {artist_name}",
                                user_id, session_id)

    async def _trigger_weather_interpretation(self, forecast_type, session_dict, gate=None):
        gpt_response = await self.dj_prompt_service.gpt_weather_interpretation(session_dict, forecast_type)
        if not gpt_response:
            return
        await self._await_gate(gate)

        forecast_display = forecast_type if forecast_type != "current" else "current conditions"
        await self._air_segment(gpt_response, 'weather', f"Retrieved {forecast_display} forecast",
                                session_dict.get('user_id'), session_dict.get('session_id'))

    async def _trigger_news_interpretation(self, query, is_topic, categories, location, session_dict, gate=None):
        gpt_response = await self.dj_prompt_service.gpt_news_interpretation(query, is_topic, categories, location, session_dict)
        if not gpt_response:
            return
        await self._await_gate(gate)

        feedback_msg = f"Retrieved news for '{query}'" if query else f"Retrieved latest {location} news"
        await self._air_segment(gpt_response, 'news', feedback_msg,
                                session_dict.get('user_id'), session_dict.get('session_id'))

    async def _trigger_location_search_interpretation(self, query, session_dict, gate=None):
        gpt_response = await self.dj_prompt_service.gpt_location_search_interpretation(query, session_dict)
        if not gpt_response:
            return
        await self._await_gate(gate)

        await self._air_segment(gpt_response, 'location_search', f"Found locations for '{query}'",
                                session_dict.get('user_id'), session_dict.get('session_id'))

    async def _trigger_event_interpretation(self, query, location, country_code, start_date, end_date, session_dict,
                                            gate=None):
        gpt_response = await self.dj_prompt_service.gpt_events_interpretation(location, country_code, start_date,
                                                                              end_date, session_dict, keyword=query)
        if not gpt_response:
            return
        await self._await_gate(gate)

        await self._air_segment(gpt_response, 'events', f"Found events in {location}",
                                session_dict.get('user_id'), session_dict.get('session_id'))

    async def _trigger_shoutouts_interpretation(self, session_dict, specific_query=None, gate=None):
        user_id = session_dict.get('user_id')
        session_id = session_dict.get('session_id')

        gpt_response = await self.dj_prompt_service.gpt_shoutouts_interpretation(
            session_dict=session_dict,
            query=specific_query,
            n_results=10
        )
        await self._await_gate(gate)

        async with self.async_session_maker() as db:
            if not gpt_response:
                feedback_msg = "No shoutouts found"
                if specific_query:
                    feedback_msg = f"No shoutouts found for '{specific_query}'"

                await save_conversation_to_database(
                    user_id=user_id,
                    db=db,
                    warning=feedback_msg,
                    message_type='shoutouts'
                )
                if session_id:
                    await self.sio.emit('conversation_update', {
                        'warning': feedback_msg,
                        'message_type': 'shoutouts'
                    }, room=session_id)
                return

            await self.tts_queue_manager.add_tts_request(
                gpt_response, user_id, "shoutouts",
                is_broadcast=True, is_temp_user=not user_id, session_id=session_id
            )

            await save_conversation_to_database(user_id, db, bot_response=gpt_response, message_type='shoutouts')
            await self.sio.emit('conversation_update', {'bot_response': gpt_response, 'message_type': 'shoutouts'},
                                room=session_id)

    @staticmethod
    def _parse_seed_mode(mode_text):
        if "mood" in mode_text:
            return "mood"
        if "style" in mode_text:
            return "style"
        if "theme" in mode_text:
            return "theme"
        if "lyric" in mode_text:
            return "lyrics"
        if "vocal" in mode_text:
            return "vocal"
        if "secondary" in mode_text and "genre" in mode_text:
            return "secondary_genres"
        if "genre" in mode_text:
            return "primary_genre"
        if "similar" in mode_text and "artist" in mode_text:
            return "similar_artists"
        if "artist" in mode_text:
            return "primary_artist"
        if "all" in re.split(r'[\s_]+', mode_text) or mode_text in ("everything", "balanced", "mix"):
            return "all"
        return None

    async def _handle_seed_radio(self, command, session_dict):
        if not session_dict.get('session_id'):
            return

        match = re.search(r'"([^"]+)"', command)
        if not match:
            log_service.error(f"No quoted mode found in seed command: {command}")
            return

        mode = self._parse_seed_mode(match.group(1).strip().lower())
        if not mode:
            log_service.error(f"Invalid seed mode '{match.group(1)}' in command: {command}")
            return

        await self.execute_seed_radio(session_dict, mode)

    async def execute_seed_radio(self, session_dict, mode):
        session_id = session_dict.get('session_id')
        user_id = session_dict.get('user_id')

        if not session_id:
            return {"status": "error", "reason": "No active playback session"}

        mode_display = SEED_MODE_DISPLAY.get(mode)
        if not mode_display:
            return {"status": "error", "reason": f"Unknown seed mode '{mode}'"}

        track_id = self._resolve_track_id(session_id, "current")
        if not track_id:
            log_service.error("No current track available for seeding")
            return {"status": "error", "reason": "Nothing is playing to seed the radio from"}

        track_name = self._track_label(track_id) or "this track"

        log_service.detail(f"[COMMAND EXECUTOR] Seeding radio based on {track_name} ({mode_display})", "commands")

        seeded = await self.playback_service.seed_radio(session_id, category=mode, user_id=user_id)

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
            "mode": mode,
            "seed_track": track_name,
            "up_next": self._upcoming_labels(session_id)
        }

    @staticmethod
    def _parse_playlist_mode(playlist_text):
        if "favorite" in playlist_text:
            return "favorites"
        if "discover" in playlist_text:
            return "discovery"
        if "top" in playlist_text and "all" in playlist_text:
            return "top_hits_all"
        if "top" in playlist_text and "week" in playlist_text:
            return "top_hits_week"
        if "top" in playlist_text and "day" in playlist_text:
            return "top_hits_day"
        return None

    async def _handle_playlist(self, command, session_dict):
        if not session_dict.get('session_id'):
            return

        match = re.search(r'"([^"]+)"', command)
        if not match:
            log_service.error(f"No quoted playlist found in command: {command}")
            return

        mode = self._parse_playlist_mode(match.group(1).strip().lower())
        if not mode:
            log_service.error(f"Invalid playlist '{match.group(1)}' in command: {command}")
            return

        await self.execute_playlist(session_dict, mode)

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
