import asyncio
import datetime
import re
from sqlalchemy import select
from services_radio.conversation_service import save_conversation_to_database
from services_radio import listener_location as location_resolver
from services_radio.dj_prompt_helper_service import UnavailableSegment
from services_radio.dj_content_bank import content_bank
from services_radio.tts_stream_planner import spoken_text
from services.task_utils import spawn
from database.models import User
from services import log_service

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
    "description": "",
}
NAME_SEARCH_FIELDS = {"Artist": "artist", "Song": "title"}
VOCAL_GENDERS = {"m": "male", "f": "female"}
FIND_TAKES_PER_SONG = 3
CANDIDATE_SOUND_CHARS = 140
FIND_NOTE = ("Candidates from the PLAiR catalog, closest first; nothing is playing yet. The pick is yours: play the "
             "one that best fits what the listener described with search_and_play track_id and say who it is. If "
             "none fits, look up by name the real artists or songs you know fit the clues; if the station still "
             "doesn't have it, play the nearest and say so. Never read ids aloud.")

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
        if target and self.catalog_service is not None and self.catalog_service.get_track(target):
            return target
        return None

    def _track_label(self, track_id):
        track = self.catalog_service.get_track(track_id) if track_id else None
        if not track or not track.get('generation_params', {}).get('title'):
            return None
        params = track.get("generation_params") or {}
        artists = log_service.track_artists(track)
        return f"{params['title']} by {artists[0]}" if artists else params['title']

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

    @staticmethod
    def spawn_segment(coro, session_dict, name):
        task = spawn(coro, name=name)
        turn_tasks = session_dict.get('_turn_tasks')
        if turn_tasks is not None:
            turn_tasks.add(task)
            task.add_done_callback(turn_tasks.discard)
        return task

    async def _search_scope(self, session_dict, within):
        from services.listener_filters import excluded_ids, scope_ids
        banned_ids = await excluded_ids(session_dict.get('user_id'), session_dict.get('session_id'))
        only_ids = await scope_ids(session_dict.get('user_id'), within)
        return banned_ids, only_ids

    @staticmethod
    def _empty_scope(within):
        return {"status": "no_results", "within": within,
                "note": "This listener hasn't liked any tracks yet, so there is nothing to search there. Say so, "
                        "and offer to search the whole catalog."}

    async def _search_track_ids(self, query, banned_ids, only_ids, n_results=5):
        log_service.detail(f"[COMMAND EXECUTOR] Category search: {query}", "commands")
        results = await self.vector_search_service.search(
            query=query,
            n_results=n_results,
            use_ai_analysis=": " not in query,
            banned_ids=banned_ids if banned_ids else None,
            only_ids=only_ids
        )
        track_ids = [track["id"] for track in results]
        prefix, _, value = query.partition(": ")
        entry = {"query": query, "found": len(track_ids)}
        missing = None
        if prefix in NAME_SEARCH_FIELDS and track_ids:
            matches = [track_id for track_id in track_ids
                       if self._name_matches(value, track_id, NAME_SEARCH_FIELDS[prefix])]
            entry["exact"] = bool(matches)
            if matches:
                track_ids = matches
            else:
                missing = value
                entry["closest"] = self._track_label(track_ids[0])
        return track_ids, entry, missing

    def _candidate(self, track_id):
        track = self.catalog_service.get_track(track_id) if track_id else None
        if not track:
            return None
        params = track.get("generation_params") or {}
        derived = track.get("derived_tags") or {}
        style = (params.get("style_canonical") or params.get("style") or "").strip()
        candidate = {
            "track_id": track_id,
            "title": params.get("title") or "",
            "artist": (log_service.track_artists(track) or [""])[0],
            "genre": derived.get("primary_genre") or "",
            "sound": style.split(". ")[0][:CANDIDATE_SOUND_CHARS],
        }
        if params.get("instrumental"):
            candidate["vocals"] = "instrumental"
        elif params.get("vocal_gender") in VOCAL_GENDERS:
            candidate["vocals"] = VOCAL_GENDERS[params["vocal_gender"]]
        return candidate

    def _starts_with(self, track_id, prefix, by_title):
        track = self.catalog_service.get_track(track_id)
        if not track:
            return False
        title = (track.get("generation_params") or {}).get("title") or ""
        names = [title] if by_title else log_service.track_artists(track)
        wanted = self._initial_key(prefix)
        return any(self._initial_key(name).startswith(wanted) for name in names if name)

    @staticmethod
    def _initial_key(name):
        key = re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()
        return key[4:] if key.startswith("the ") else key

    async def find_tracks(self, session_dict, query, within=None, how_many=8, starts_with=None):
        banned_ids, only_ids = await self._search_scope(session_dict, within)
        if only_ids is not None and not only_ids:
            return self._empty_scope(within)
        pool = len(self.catalog_service.tracks) if starts_with else how_many * FIND_TAKES_PER_SONG
        track_ids, entry, missing = await self._search_track_ids(query, banned_ids, only_ids, n_results=pool)
        if starts_with:
            by_title = query.startswith(f"{SEARCH_CATEGORY_PREFIXES['song_title']}: ")
            track_ids = [track_id for track_id in track_ids if self._starts_with(track_id, starts_with, by_title)]
        candidates, seen = [], set()
        for candidate in map(self._candidate, track_ids):
            key = (candidate or {}).get("title", "").lower(), (candidate or {}).get("artist", "").lower()
            if candidate and key not in seen:
                seen.add(key)
                candidates.append(candidate)
            if len(candidates) >= how_many:
                break
        if not candidates:
            return {"status": "no_results", "query": query, "starts_with": starts_with,
                    "note": "Nothing in the catalog came close. Try other words, another name or no letter filter."}
        result = {"status": "ok", "query": query, "note": FIND_NOTE, "items": candidates}
        if starts_with:
            result["starts_with"] = starts_with
        if only_ids is not None:
            result["within"] = within
        if missing:
            result["not_in_catalog"] = [missing]
            result["note"] = (f"The catalog has nothing by or called {missing}; these are only the closest by "
                              f"sound. {FIND_NOTE}")
        return result

    async def execute_play_ids(self, session_dict, track_ids, play):
        known = [track_id for track_id in track_ids if self.catalog_service.get_track(track_id)]
        if not known:
            return {"status": "not_found",
                    "note": "No catalog track has that id. Use a track_id from find_tracks, what_aired or pulse_search."}
        return await self._queue_tracks(session_dict, known, play, [], [], None, None)

    async def execute_searches(self, session_dict, searches, within=None):
        session_id = session_dict.get('session_id')

        banned_ids, only_ids = await self._search_scope(session_dict, within)
        if only_ids is not None and not only_ids:
            return self._empty_scope(within)

        tracks_to_add = []
        play_first = False
        per_query = []
        missing = []

        for query, play in searches:
            if not query:
                continue
            track_ids, entry, missed = await self._search_track_ids(query, banned_ids, only_ids)
            if missed:
                missing.append(missed)
            labels = {self._track_label(track_id) for track_id in tracks_to_add}
            for track_id in track_ids:
                label = self._track_label(track_id)
                if label is None or label not in labels:
                    labels.add(label)
                    tracks_to_add.append(track_id)
            per_query.append(entry)
            if play and not play_first:
                play_first = True
            log_service.detail(f"[COMMAND EXECUTOR] Found {len(track_ids)} tracks for: {query}", "commands")

            if session_id and track_ids:
                feedback_msg = f"Found {len(track_ids)} track{'s' if len(track_ids) != 1 else ''} for '{query}'"
                await self.sio.emit('conversation_update', {'info': feedback_msg, 'message_type': 'interactive'}, room=session_id)

        return await self._queue_tracks(session_dict, tracks_to_add, play_first, per_query, missing, only_ids, within)

    async def _queue_tracks(self, session_dict, tracks_to_add, play_first, per_query, missing, only_ids, within):
        session_id = session_dict.get('session_id')
        user_id = session_dict.get('user_id')
        now_playing = None
        if tracks_to_add and session_id:
            log_service.detail(f"[COMMAND EXECUTOR] Adding {len(tracks_to_add)} tracks to queue", "commands")
            await self.playback_service.add_to_queue(session_id, tracks_to_add, user_id=user_id)
            if play_first and tracks_to_add:
                log_service.detail(f"[COMMAND EXECUTOR] Playing first track: {tracks_to_add[0]}", "commands")
                await self.playback_service.play(session_id, tracks_to_add[0], user_id=user_id)
                now_playing = self._track_label(tracks_to_add[0])
            log_service.detail(f"[COMMAND EXECUTOR] Successfully added {len(tracks_to_add)} tracks to queue", "commands")

        if tracks_to_add or per_query:
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
        result = {
            "status": "ok" if tracks_to_add else "no_results",
            "found": len(tracks_to_add),
            "searches": per_query,
            "queued": queued,
            "now_playing": now_playing
        }
        if only_ids is not None:
            result["within"] = within
            if not tracks_to_add:
                result["note"] = ("Nothing in the listener's own liked tracks matches that. Say so, and offer to "
                                  "search the whole catalog.")
        if missing:
            result["not_in_catalog"] = missing
            result["note"] = (f"The catalog has nothing by or called {', '.join(missing)}. What was found are only the "
                              "closest matches by sound. Tell the listener plainly that the station doesn't have it, "
                              "then name what's playing or queued instead.")
        return result

    def _name_matches(self, value, track_id, field):
        def norm(text):
            return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split())
        track = self.catalog_service.get_track(track_id) if self.catalog_service else None
        if field == "artist":
            names = log_service.track_artists(track)
        else:
            names = [((track or {}).get("generation_params") or {}).get("title"),
                     ((track or {}).get("track_info") or {}).get("title")]
        wanted = norm(value)
        return bool(wanted) and any(
            f" {wanted} " in f" {name} " or f" {name} " in f" {wanted} " for name in map(norm, names) if name)

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

    RATING_PREFERENCES = {"like": "like", "superstar": "super_like", "ban": "ban"}

    async def _note(self, session_dict, message, kind="info"):
        user_id = session_dict.get('user_id')
        if user_id:
            async with self.async_session_maker() as db:
                await save_conversation_to_database(user_id=user_id, db=db, message_type='interactive',
                                                    **{kind: message})
        await self.sio.emit('conversation_update', {kind: message, 'message_type': 'interactive'},
                            room=session_dict.get('session_id'))

    async def execute_track_preference(self, session_dict, rating, target):
        from service_registry import services
        from services.preferences_service import preferences_service

        session_id = session_dict.get('session_id')
        user_id = session_dict.get('user_id')

        if not user_id or not session_id:
            return {"status": "refused", "reason": "Only signed-in listeners can rate tracks"}
        if rating != "dislike" and rating not in self.RATING_PREFERENCES:
            return {"status": "error", "reason": f"Unknown rating '{rating}'"}

        track_id = self._resolve_track_id(session_id, target) if target else None
        if not track_id:
            await self._note(session_dict, "No track currently available to save preference", "error")
            return {"status": "error", "reason": "No track available for that position"}

        broadcast = services.websocket_service.broadcast_preference_change if services.websocket_service else None
        async with self.async_session_maker() as db:
            if rating == "dislike":
                await preferences_service.remove_track_preference(
                    int(user_id), track_id, db, playback_service=self.playback_service, broadcast_callback=broadcast)
            else:
                await preferences_service.set_track_preference(
                    int(user_id), track_id, self.RATING_PREFERENCES[rating], db,
                    playback_service=self.playback_service, broadcast_callback=broadcast)

        track_name = self._track_label(track_id) or "this track"
        await self._note(session_dict, {"like": f"Liked {track_name}", "superstar": f"Added {track_name} to your favorites",
                                        "ban": f"Banned {track_name}",
                                        "dislike": f"Removed preference for {track_name}"}[rating])
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
        if rating != "dislike" and rating not in self.RATING_PREFERENCES:
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
                if rating == "dislike":
                    await preferences_service.remove_shoutout_preference(int(user_id), shoutout_id, db,
                                                                         broadcast_callback=broadcast)
                else:
                    await preferences_service.set_shoutout_preference(
                        int(user_id), shoutout_id, self.RATING_PREFERENCES[rating], db, broadcast_callback=broadcast)
        except ValueError as e:
            return {"status": "refused", "reason": str(e)}

        label = f"{kind_of(post)} from {community_on_air.speaker(post)}"
        await self._note(session_dict, {"like": f"Liked the {label}", "superstar": f"Super-liked the {label}",
                                        "ban": f"Banned the {label}",
                                        "dislike": f"Removed your rating of the {label}"}[rating])
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

    async def _user_data(self, user_id):
        async with self.async_session_maker() as db:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
        return {
            "user_id": int(user_id),
            "username": getattr(user, "username", None) or "Listener",
            "location": getattr(user, "location", None) or "Unknown",
            "latitude": float(user.latitude) if user is not None and user.latitude is not None else None,
            "longitude": float(user.longitude) if user is not None and user.longitude is not None else None,
        }

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

        if not spoken_text(script):
            async with self.async_session_maker() as db:
                await save_conversation_to_database(user_id, db, bot_response=script, message_type=message_type)
            if session_id:
                await self.sio.emit('conversation_update', {'bot_response': script, 'message_type': message_type},
                                    room=session_id)
            return
        await self.tts_queue_manager.add_tts_request(script, user_id, message_type, is_broadcast=True,
                                                     is_temp_user=not user_id, session_id=session_id)
        if not isinstance(gpt_response, UnavailableSegment):
            content_bank.record_airing(session_id, script)
        async with self.async_session_maker() as db:
            await save_conversation_to_database(user_id, db, bot_response=script, message_type=message_type)
        await self.sio.emit('conversation_update', {'bot_response': script, 'message_type': message_type},
                            room=session_id)

    async def resolve_lyrics_track(self, session_dict, target, query):
        session_id = session_dict.get('session_id')
        if not session_id:
            return None, None

        if target:
            track_id = self._resolve_track_id(session_id, target)
            if track_id:
                return self.catalog_service.get_track(track_id), TRACK_TARGET_LABELS.get(target, "earlier track")
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

        periods = [forecast_type] if isinstance(forecast_type, str) else list(forecast_type)
        forecast_display = " and ".join("current conditions" if period == "current" else period for period in periods)
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

    async def execute_seed_radio(self, session_dict, mode, target="current"):
        session_id = session_dict.get('session_id')
        user_id = session_dict.get('user_id')

        if not session_id:
            return {"status": "error", "reason": "No active playback session"}

        mode_display = SEED_MODE_DISPLAY.get(mode)
        if not mode_display:
            return {"status": "error", "reason": f"Unknown seed mode '{mode}'"}

        track_id = self._resolve_track_id(session_id, target)
        if not track_id:
            log_service.error(f"No {target} track available for seeding")
            return {"status": "error", "reason": "Nothing is playing to seed the radio from" if target == "current"
                    else f"There is no {target} track to seed the radio from"}

        track_name = self._track_label(track_id) or "this track"

        log_service.detail(f"[COMMAND EXECUTOR] Seeding radio based on {track_name} ({mode_display})", "commands")

        seeded = await self.playback_service.seed_radio(
            session_id, category=mode, track_id=None if target == "current" else track_id, user_id=user_id)

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
