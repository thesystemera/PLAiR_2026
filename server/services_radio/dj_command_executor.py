import asyncio
import datetime
from sqlalchemy import select
from services_radio.conversation_service import save_conversation_to_database
from database.models import User
from services import log_service
from services.user_data_cache_service import user_data_cache

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

    async def process_commands(self, commands, session_dict):
        session_id = session_dict.get('session_id')
        user_id = session_dict.get('user_id')

        log_service.commands(f"[COMMAND EXECUTOR] Processing commands for session {session_id}")

        banned_ids = set()
        if user_id:
            banned_ids = await user_data_cache.get_banned_ids(user_id)

        grouped_commands = {
            "play": [],
            "cue": [],
            "other": []
        }

        for command in commands.split('\n'):
            command = command.replace('[HAL11000]', '').strip()
            if command:
                log_service.commands(f"[COMMAND EXECUTOR] Parsing: {command}")
            if "{play}" in command and "{play_shoutouts}" not in command and "{seed}" not in command and "{playlist}" not in command:
                grouped_commands["play"].append(command)
            elif "{cue}" in command:
                grouped_commands["cue"].append(command)
            else:
                grouped_commands["other"].append(command)

        log_service.commands(
            f"[COMMAND EXECUTOR] Grouped: {len(grouped_commands['play'])} Play, {len(grouped_commands['cue'])} Cue, {len(grouped_commands['other'])} Other")

        tracks_to_add = []
        play_first = False
        had_search_commands = False

        async def process_search_command(command, play=False):
            nonlocal tracks_to_add, play_first, had_search_commands
            had_search_commands = True
            query = ""

            if "{primary_artist}" in command:
                artist = self._extract_value_from_action(command, "primary_artist")
                query = f"Artist: {artist}"
            elif "{similar_artists}" in command:
                artist = self._extract_value_from_action(command, "similar_artists")
                query = f"Similar Artists: {artist}"
            elif "{song_title}" in command:
                track = self._extract_value_from_action(command, "song_title")
                query = f"Song: {track}"

            elif "{primary_genre}" in command:
                genre = self._extract_value_from_action(command, "primary_genre")
                query = f"Genre: {genre}"
            elif "{secondary_genres}" in command:
                subgenre = self._extract_value_from_action(command, "secondary_genres")
                query = f"Subgenre: {subgenre}"
            elif "{mood}" in command:
                mood = self._extract_value_from_action(command, "mood")
                query = f"Mood: {mood}"
            elif "{style}" in command:
                style = self._extract_value_from_action(command, "style")
                query = f"Style: {style}"
            elif "{theme}" in command:
                theme = self._extract_value_from_action(command, "theme")
                query = f"Theme: {theme}"
            elif "{vocal}" in command:
                vocal = self._extract_value_from_action(command, "vocal")
                query = f"Vocal: {vocal}"
            elif "{lyrics}" in command:
                lyrics = self._extract_value_from_action(command, "lyrics")
                query = f"Lyrics: {lyrics}"

            if query:
                log_service.commands(f"[COMMAND EXECUTOR] Category search: {query}")
                results = await self.vector_search_service.search(
                    query=query,
                    n_results=5,
                    banned_ids=banned_ids if banned_ids else None
                )
                track_ids = [track["id"] for track in results]
                tracks_to_add.extend(track_ids)
                if play and not play_first:
                    play_first = True
                log_service.commands(f"[COMMAND EXECUTOR] Found {len(track_ids)} tracks for: {query}")

                if session_id and track_ids:
                    feedback_msg = f"Found {len(track_ids)} track{'s' if len(track_ids) != 1 else ''} for '{query}'"
                    await self.sio.emit('conversation_update', {'info': feedback_msg, 'message_type': 'interactive'}, room=session_id)

        for command in grouped_commands["play"]:
            await process_search_command(command, play=True)

        for command in grouped_commands["cue"]:
            await process_search_command(command, play=False)

        if tracks_to_add and session_id:
            log_service.commands(f"[COMMAND EXECUTOR] Adding {len(tracks_to_add)} tracks to queue")
            await self.playback_service.add_to_queue(session_id, tracks_to_add, user_id=user_id)
            if play_first and tracks_to_add:
                log_service.commands(f"[COMMAND EXECUTOR] Playing first track: {tracks_to_add[0]}")
                await self.playback_service.play(session_id, tracks_to_add[0], user_id=user_id)
            log_service.commands(f"[COMMAND EXECUTOR] Successfully added {len(tracks_to_add)} tracks to queue")

        if had_search_commands:
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

        for command in grouped_commands["other"]:
            if "{next}" in command and session_id:
                log_service.commands("[COMMAND EXECUTOR] Executing: Skip to next track")
                await self.playback_service.next(session_id, user_id=user_id)
            elif "{previous}" in command and session_id:
                log_service.commands("[COMMAND EXECUTOR] Executing: Skip to previous track")
                await self.playback_service.previous(session_id)
            elif "{mute}" in command and session_id:
                log_service.commands("[COMMAND EXECUTOR] Executing: Pause playback")
                await self.playback_service.pause(session_id)
            elif "{activate}" in command and session_id:
                log_service.commands("[COMMAND EXECUTOR] Executing: Resume playback")
                await self.playback_service.play(session_id)
            elif "{seed}" in command and session_id:
                log_service.commands(f"[COMMAND EXECUTOR] Executing: Seed radio - {command}")
                asyncio.create_task(self._handle_seed_radio(command, session_dict))
            elif "{playlist}" in command and session_id:
                log_service.commands(f"[COMMAND EXECUTOR] Executing: Playlist mode - {command}")
                asyncio.create_task(self._handle_playlist(command, session_dict))
            elif "{like}" in command or "{dislike}" in command or "{ban}" in command or "{superstar}" in command:
                log_service.commands(f"[COMMAND EXECUTOR] Executing: Track preference - {command}")
                asyncio.create_task(self._handle_track_preference(command, session_dict))
            elif "{lyrics}" in command:
                log_service.commands("[COMMAND EXECUTOR] Executing: Fetch lyrics from catalog")
                asyncio.create_task(self._trigger_lyrics_interpretation(command, session_dict))
            elif "{biography}" in command:
                query = self._extract_value_from_action(command, "biography")
                log_service.commands(f"[COMMAND EXECUTOR] Executing: Fetch biography for '{query}'")
                asyncio.create_task(self._trigger_biography_interpretation(query, session_dict))
            elif "{news}" in command:
                log_service.commands(f"[COMMAND EXECUTOR] Executing: Fetch news - {command}")
                asyncio.create_task(self._process_news_command(command, session_dict))
            elif "{events}" in command:
                log_service.commands(f"[COMMAND EXECUTOR] Executing: Fetch events - {command}")
                asyncio.create_task(self._process_events_command(command, session_dict))
            elif "{find_amenities}" in command:
                query = self._extract_value_from_action(command, "find_amenities")
                log_service.commands(f"[COMMAND EXECUTOR] Executing: Find amenities for '{query}'")
                asyncio.create_task(self._trigger_location_search_interpretation(query, session_dict))
            elif "{weather}" in command:
                forecast_type = "current"
                if "{today}" in command:
                    forecast_type = "today"
                elif "{tomorrow}" in command:
                    forecast_type = "tomorrow"
                elif "{this_week}" in command:
                    forecast_type = "week"
                log_service.commands(f"[COMMAND EXECUTOR] Executing: Fetch weather forecast ({forecast_type})")
                asyncio.create_task(self._trigger_weather_interpretation(forecast_type, session_dict))
            elif "{save_shoutout}" in command:
                log_service.commands("[COMMAND EXECUTOR] Executing: Save user shoutout")
                asyncio.create_task(self._process_shoutout("save", session_dict))
            elif "{save_shoutout_reply:" in command:
                # Extract parent_id from command: {save_shoutout_reply:parent_id}
                parent_id = self._extract_value_from_action(command, "save_shoutout_reply")
                if parent_id:
                    log_service.commands(f"[COMMAND EXECUTOR] Executing: Save shoutout reply to {parent_id}")
                    asyncio.create_task(self._save_shoutout_reply(session_dict, parent_id))
                else:
                    log_service.error("[COMMAND EXECUTOR] save_shoutout_reply missing parent_id")
            elif "{play_shoutouts}" in command:
                query = self._extract_value_from_action(command, "play_shoutouts")
                log_service.commands(
                    f"[COMMAND EXECUTOR] Executing: Play community shoutouts (Query: {query if query else 'Generic'})")
                asyncio.create_task(self._trigger_shoutouts_interpretation(session_dict, query))
            elif "{save_opinion}" in command:
                opinion = "opinion"
                if "{current}" in command:
                    opinion += " current"
                elif "{earlier}" in command:
                    opinion += " previous"
                elif "{later}" in command:
                    opinion += " next"
                log_service.commands(f"[COMMAND EXECUTOR] Executing: Save user opinion ({opinion})")
                asyncio.create_task(self._process_opinion(opinion, session_dict))

    async def _handle_track_preference(self, command, session_dict):
        from database.models import TrackPreference, PreferenceType

        session_id = session_dict.get('session_id')
        user_id = session_dict.get('user_id')

        if not user_id or not session_id:
            return

        state = self.playback_service.get_state(session_id)
        track_id = None

        if "{current}" in command:
            track_id = state.get('current_track', {}).get('id')
        elif "{earlier}" in command:
            history = state.get('history', [])
            if history:
                track_id = history[-1]
        elif "{later}" in command:
            queue = state.get('queue', [])
            if queue:
                track_id = queue[0]

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
            return

        is_removal = "{dislike}" in command
        preference_type = None
        preference_name = None

        if is_removal:
            preference_name = "none"
        elif "{like}" in command:
            preference_type = PreferenceType.LIKE
            preference_name = "like"
        elif "{superstar}" in command:
            preference_type = PreferenceType.SUPER_LIKE
            preference_name = "super_like"
        elif "{ban}" in command:
            preference_type = PreferenceType.BAN
            preference_name = "ban"

        if not is_removal and not preference_type:
            return

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

        track = self.catalog_service.get_track(track_id)
        track_name = "this track"
        if track:
            title = track.get('generation_params', {}).get('title', '')
            artist = track.get('generation_params', {}).get('artist_name', '')
            if title and artist:
                track_name = f"'{title}' by {artist}"
            elif title:
                track_name = f"'{title}'"

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

    async def _process_opinion(self, opinion_type, session_dict):
        session_id = session_dict.get('session_id')
        if not session_id:
            log_service.error("No session_id provided")
            return

        state = self.playback_service.get_state(session_id)
        track_id = None

        if "current" in opinion_type:
            track_id = state.get('current_track', {}).get('id')
        elif "previous" in opinion_type:
            history = state.get('history', [])
            if history:
                track_id = history[-1]
        elif "next" in opinion_type:
            queue = state.get('queue', [])
            if queue:
                track_id = queue[0]

        if track_id:
            track = self.catalog_service.get_track(track_id)
            if track:
                await self._save_opinion(track, session_dict)
            else:
                log_service.error(f"Track {track_id} not found in catalog")
        else:
            log_service.error(f"No track found for opinion type: {opinion_type}")

    async def _save_opinion(self, track, session_dict):
        user_id = session_dict.get('user_id')
        if not user_id:
            return

        if not self.user_content_service:
            log_service.error("User Content Service not available")
            return

        log_service.commands(f"Delegating opinion processing for user {user_id} on track {track['id']}")

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
        else:
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

    async def _process_shoutout(self, shoutout_type, session_dict):
        if "save" in shoutout_type:
            await self._save_shoutout(session_dict)
        else:
            log_service.error(f"Invalid shoutout type: {shoutout_type}")

    async def _save_shoutout(self, session_dict):
        user_id = session_dict.get('user_id')
        if not user_id:
            return

        if not self.user_content_service:
            log_service.error("User Content Service not available")
            return

        log_service.commands(f"Delegating shoutout processing for user {user_id}")

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
        else:
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

    async def _save_shoutout_reply(self, session_dict, parent_id: str):
        user_id = session_dict.get('user_id')
        if not user_id:
            return

        if not self.user_content_service:
            log_service.error("User Content Service not available")
            return

        if not self.user_content_service.is_root_shoutout(parent_id):
            log_service.error(f"Cannot reply to {parent_id} - not a root shoutout or doesn't exist")
            session_id = session_dict.get('session_id')
            if session_id:
                await self.sio.emit('conversation_update', {
                    'error': "Cannot reply - parent shoutout not found or is already a reply",
                    'message_type': 'shoutouts'
                }, room=session_id)
            return

        log_service.commands(f"Delegating shoutout reply processing for user {user_id} to parent {parent_id}")

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
        else:
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

    async def _process_news_command(self, command, session_dict):
        query = self._extract_value_from_action(command, "news")
        location = 'WORLD'
        if "{national}" in command:
            location = 'US'
        elif "{local}" in command:
            async with self.async_session_maker() as db:
                result = await db.execute(select(User).where(User.id == session_dict.get('user_id')))
                user = result.scalar_one_or_none()
                if user and user.location:
                    location = user.location
        categories = [cat for cat in
                      ["world", "nation", "business", "technology", "entertainment", "sports", "science", "health"] if
                      f"{{{cat}}}" in command]
        is_topic = bool(categories)
        if not query and categories:
            query = categories[0].upper()
        await self._trigger_news_interpretation(query or "general news", is_topic, [c.lower() for c in categories],
                                                location, session_dict)

    async def _process_events_command(self, command, session_dict):
        query = self._extract_value_from_action(command, "events")
        async with self.async_session_maker() as db:
            result = await db.execute(select(User).where(User.id == session_dict.get('user_id')))
            user = result.scalar_one_or_none()
            location = user.location if user and user.location else 'Auckland'
        country_code = 'NZ'
        start_date = datetime.datetime.now(datetime.timezone.utc)
        end_date = start_date + datetime.timedelta(days=30)
        if "{today}" in command:
            end_date = start_date + datetime.timedelta(days=1)
        elif "{tomorrow}" in command:
            start_date += datetime.timedelta(days=1)
            end_date = start_date + datetime.timedelta(days=1)
        elif "{this_week}" in command:
            end_date = start_date + datetime.timedelta(days=7)
        await self._trigger_event_interpretation(query, location, country_code, start_date, end_date, session_dict)

    async def _trigger_biography_interpretation(self, artist_name, session_dict):
        user_id = session_dict.get('user_id')
        session_id = session_dict.get('session_id')
        if not user_id:
            return

        feedback_msg = f"Retrieved biography for {artist_name}"
        async with self.async_session_maker() as db:
            await save_conversation_to_database(user_id, db, info=feedback_msg, message_type='biography')
        if session_id:
            await self.sio.emit('conversation_update', {'info': feedback_msg, 'message_type': 'biography'}, room=session_id)

        gpt_response = await self.dj_prompt_service.gpt_biography_interpretation(artist_name, session_dict)
        if not gpt_response:
            return
        await self.tts_queue_manager.add_tts_request(gpt_response, user_id, "biography", is_broadcast=True, is_temp_user=not user_id)
        async with self.async_session_maker() as db:
            await save_conversation_to_database(user_id, db, bot_response=gpt_response, message_type='biography')
        await self.sio.emit('conversation_update', {'bot_response': gpt_response, 'message_type': 'biography'}, room=str(user_id))

    async def _trigger_lyrics_interpretation(self, command, session_dict):
        user_id = session_dict.get('user_id')
        session_id = session_dict.get('session_id')
        if not user_id or not session_id:
            return

        track = None
        track_title = None

        if "{current}" in command:
            state = self.playback_service.get_state(session_id)
            track_id = state.get('current_track', {}).get('id')
            if track_id:
                track = self.catalog_service.get_track(track_id)
                track_title = "current track"
        elif "{earlier}" in command:
            state = self.playback_service.get_state(session_id)
            history = state.get('history', [])
            if history:
                track = self.catalog_service.get_track(history[-1])
                track_title = "previous track"
        elif "{later}" in command:
            state = self.playback_service.get_state(session_id)
            queue = state.get('queue', [])
            if queue:
                track = self.catalog_service.get_track(queue[0])
                track_title = "next track"
        else:
            query = self._extract_value_from_action(command, "lyrics")
            if query:
                search_results = await self.vector_search_service.search(
                    query=query,
                    n_results=1,
                    banned_ids=None
                )
                if search_results:
                    track = self.catalog_service.get_track(search_results[0]['id'])
                    track_title = query

        if not track:
            log_service.error(f"Lyrics: No track found for command: {command}")
            return

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

        feedback_msg = f"Found lyrics for '{track_title}' by {artist_name}"
        async with self.async_session_maker() as db:
            await save_conversation_to_database(user_id, db, info=feedback_msg, message_type='lyrics')
        if session_id:
            await self.sio.emit('conversation_update', {'info': feedback_msg, 'message_type': 'lyrics'}, room=session_id)

        gpt_response = await self.dj_prompt_service.gpt_lyrics_interpretation(lyrics_context, artist_name, session_dict)
        if not gpt_response:
            return

        await self.tts_queue_manager.add_tts_request(gpt_response, user_id, "lyrics", is_broadcast=True,
                                                     is_temp_user=not user_id)
        async with self.async_session_maker() as db:
            await save_conversation_to_database(user_id, db, bot_response=gpt_response, message_type='lyrics')
        await self.sio.emit('conversation_update', {'bot_response': gpt_response, 'message_type': 'lyrics'},
                            room=str(user_id))

    async def _trigger_weather_interpretation(self, forecast_type, session_dict):
        user_id = session_dict.get('user_id')
        session_id = session_dict.get('session_id')
        if not user_id:
            return

        gpt_response = await self.dj_prompt_service.gpt_weather_interpretation(session_dict, forecast_type)
        if not gpt_response:
            return

        forecast_display = forecast_type if forecast_type != "current" else "current conditions"
        feedback_msg = f"Retrieved {forecast_display} forecast"
        async with self.async_session_maker() as db:
            await save_conversation_to_database(user_id, db, info=feedback_msg, message_type='weather')
        if session_id:
            await self.sio.emit('conversation_update', {'info': feedback_msg, 'message_type': 'weather'}, room=session_id)

        async with self.async_session_maker() as db:
            await self.tts_queue_manager.add_tts_request(gpt_response, user_id, "weather", is_broadcast=True, is_temp_user=not user_id)
            await save_conversation_to_database(user_id, db, bot_response=gpt_response, message_type='weather')
            await self.sio.emit('conversation_update', {'bot_response': gpt_response, 'message_type': 'weather'}, room=str(user_id))

    async def _trigger_news_interpretation(self, query, is_topic, categories, location, session_dict):
        user_id = session_dict.get('user_id')
        session_id = session_dict.get('session_id')
        if not user_id:
            return

        gpt_response = await self.dj_prompt_service.gpt_news_interpretation(query, is_topic, categories, location, session_dict)
        if not gpt_response:
            return

        feedback_msg = f"Retrieved news for '{query}'" if query else f"Retrieved latest {location} news"
        async with self.async_session_maker() as db:
            await save_conversation_to_database(user_id, db, info=feedback_msg, message_type='news')
        if session_id:
            await self.sio.emit('conversation_update', {'info': feedback_msg, 'message_type': 'news'}, room=session_id)

        await self.tts_queue_manager.add_tts_request(gpt_response, user_id, "news", is_broadcast=True, is_temp_user=not user_id)
        async with self.async_session_maker() as db:
            await save_conversation_to_database(user_id, db, bot_response=gpt_response, message_type='news')
        await self.sio.emit('conversation_update', {'bot_response': gpt_response, 'message_type': 'news'}, room=str(user_id))

    async def _trigger_location_search_interpretation(self, query, session_dict):
        user_id = session_dict.get('user_id')
        session_id = session_dict.get('session_id')
        if not user_id:
            return

        gpt_response = await self.dj_prompt_service.gpt_location_search_interpretation(query, session_dict)
        if not gpt_response:
            return

        feedback_msg = f"Found locations for '{query}'"
        async with self.async_session_maker() as db:
            await save_conversation_to_database(user_id, db, info=feedback_msg, message_type='location_search')
        if session_id:
            await self.sio.emit('conversation_update', {'info': feedback_msg, 'message_type': 'location_search'}, room=session_id)

        async with self.async_session_maker() as db:
            await self.tts_queue_manager.add_tts_request(gpt_response, user_id, "location_search", is_broadcast=True, is_temp_user=not user_id)
            await save_conversation_to_database(user_id, db, bot_response=gpt_response, message_type='location_search')
            await self.sio.emit('conversation_update', {'bot_response': gpt_response, 'message_type': 'location_search'}, room=str(user_id))

    async def _trigger_event_interpretation(self, _query, location, country_code, start_date, end_date, session_dict):
        user_id = session_dict.get('user_id')
        session_id = session_dict.get('session_id')
        if not user_id:
            return

        gpt_response = await self.dj_prompt_service.gpt_events_interpretation(location, country_code, start_date, end_date, session_dict)
        if not gpt_response:
            return

        feedback_msg = f"Found events in {location}"
        async with self.async_session_maker() as db:
            await save_conversation_to_database(user_id, db, info=feedback_msg, message_type='events')
        if session_id:
            await self.sio.emit('conversation_update', {'info': feedback_msg, 'message_type': 'events'}, room=session_id)

        await self.tts_queue_manager.add_tts_request(gpt_response, user_id, "events", is_broadcast=True, is_temp_user=not user_id)
        async with self.async_session_maker() as db:
            await save_conversation_to_database(user_id, db, bot_response=gpt_response, message_type='events')
        await self.sio.emit('conversation_update', {'bot_response': gpt_response, 'message_type': 'events'}, room=str(user_id))

    async def _trigger_shoutouts_interpretation(self, session_dict, specific_query=None):
        user_id = session_dict.get('user_id')
        session_id = session_dict.get('session_id')
        if not user_id:
            return

        gpt_response = await self.dj_prompt_service.gpt_shoutouts_interpretation(
            session_dict=session_dict,
            query=specific_query,
            n_results=10
        )

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
                is_broadcast=True, is_temp_user=not user_id
            )

            await save_conversation_to_database(user_id, db, bot_response=gpt_response, message_type='shoutouts')
            await self.sio.emit('conversation_update', {'bot_response': gpt_response, 'message_type': 'shoutouts'},
                                room=str(user_id))

    async def _handle_seed_radio(self, command, session_dict):
        session_id = session_dict.get('session_id')
        user_id = session_dict.get('user_id')

        if not session_id:
            return

        state = self.playback_service.get_state(session_id)
        track_id = state.get('current_track', {}).get('id')

        if not track_id:
            log_service.error("No current track available for seeding")
            return

        track = self.catalog_service.get_track(track_id)
        track_name = "this track"
        if track:
            title = track.get('generation_params', {}).get('title', '')
            artist = track.get('generation_params', {}).get('artist_name', '')
            if title and artist:
                track_name = f"'{title}' by {artist}"
            elif title:
                track_name = f"'{title}'"

        import re
        match = re.search(r'"([^"]+)"', command)
        if not match:
            log_service.error(f"No quoted mode found in seed command: {command}")
            return

        mode_text = match.group(1).strip().lower()

        if "mood" in mode_text:
            mode = "mood"
            mode_display = "mood"
        elif "style" in mode_text:
            mode = "style"
            mode_display = "style"
        elif "theme" in mode_text:
            mode = "theme"
            mode_display = "theme"
        elif "lyric" in mode_text:
            mode = "lyrics"
            mode_display = "lyrics"
        elif "vocal" in mode_text:
            mode = "vocal"
            mode_display = "vocal"
        elif "secondary" in mode_text and "genre" in mode_text:
            mode = "secondary_genres"
            mode_display = "secondary genres"
        elif "genre" in mode_text:
            mode = "primary_genre"
            mode_display = "primary genre"
        elif "similar" in mode_text and "artist" in mode_text:
            mode = "similar_artists"
            mode_display = "similar artists"
        elif "artist" in mode_text:
            mode = "primary_artist"
            mode_display = "primary artist"
        else:
            log_service.error(f"Invalid seed mode '{mode_text}' in command: {command}")
            return

        log_service.commands(f"[COMMAND EXECUTOR] Seeding radio based on {track_name} ({mode_display})")

        await self.playback_service.seed_radio(session_id, category=mode, user_id=user_id)

        if self.broadcast_playback_state_callback:
            state = self.playback_service.get_state(session_id)
            await self.broadcast_playback_state_callback(session_id, state)

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

    async def _handle_playlist(self, command, session_dict):
        session_id = session_dict.get('session_id')
        user_id = session_dict.get('user_id')

        if not session_id:
            return

        import re
        match = re.search(r'"([^"]+)"', command)
        if not match:
            log_service.error(f"No quoted playlist found in command: {command}")
            return

        playlist_text = match.group(1).strip().lower()

        if "favorite" in playlist_text:
            mode = "favorites"
            mode_display = "your favorites"
        elif "discover" in playlist_text:
            mode = "discovery"
            mode_display = "smart discovery"
        elif "top" in playlist_text and "all" in playlist_text:
            mode = "top_hits_all"
            mode_display = "all-time top hits"
        elif "top" in playlist_text and "week" in playlist_text:
            mode = "top_hits_week"
            mode_display = "this week's top hits"
        elif "top" in playlist_text and "day" in playlist_text:
            mode = "top_hits_day"
            mode_display = "today's top hits"
        else:
            log_service.error(f"Invalid playlist '{playlist_text}' in command: {command}")
            return

        log_service.commands(f"[COMMAND EXECUTOR] Playing playlist: {mode_display}")

        await self.playback_service.seed_radio(session_id, category=mode, user_id=user_id)

        if self.broadcast_playback_state_callback:
            state = self.playback_service.get_state(session_id)
            await self.broadcast_playback_state_callback(session_id, state)

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