"""Finding and queueing music for the DJs: the app's search (meaning and names by spelling), the listener's own loved
tracks, candidates to choose from, and queueing what was found."""
import re
import numpy as np
from services_radio.conversation_service import save_conversation_to_database
from services.catalog_names import spelled_alike
from services.catalog_vocals import vocals_of
from services import listener_plays
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
}
LOVED_PICKS = 5
NAME_CLOSEST_SHOWN = 3
NOT_SPELLED_NOTE = ("No name in the catalog is spelled exactly like {names}. What came back is by the closest spelling "
                    "(closest_names, 1.0 = the same spelling). If that's who the listener meant (a typo or a "
                    "mishearing), carry on. If not, tell them plainly the station doesn't have it, then name what's "
                    "playing or queued instead.")
NAME_SEARCH_FIELDS = {"Artist": "artist", "Song": "title"}
FIND_TAKES_PER_SONG = 3
CANDIDATE_SOUND_CHARS = 140
FIND_NOTE = ("Candidates from the PLAiR catalog, closest first; nothing is playing yet. The pick is yours: play the "
             "one that best fits what the listener described with search_and_play track_id and say who it is. If "
             "none fits, look up by name the real artists or songs you know fit the clues; if the station still "
             "doesn't have it, play the nearest and say so. Never read ids aloud.")
LOVED_LIST_NOTE = ("The listener's own tracks, the ones they love most first: their rating plus how often they "
                   "listen all the way through rather than skip. Nothing is playing yet: play your pick with "
                   "search_and_play track_id. Never read ids aloud.")
LOVED_PLAY_NOTE = ("A weighted shuffle of the listener's own tracks: the ones they love most (their rating, plus how "
                   "often they listen all the way through rather than skip) come up most often. 'picks' shows each "
                   "one's listens and skips.")
YOURS_NOTE = ("'yours' is this listener's own history with a track: their rating (like or super_like), listens "
              "(played at least halfway), skips (cut short) and when they last played it.")


class ExecutorSearch:
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

    async def _search_track_ids(self, query, banned_ids, only_ids, n_results=5, vocals=None):
        log_service.detail(f"[COMMAND EXECUTOR] Category search: {query}", "commands")
        results = await self.vector_search_service.search(
            query=query,
            n_results=n_results,
            use_ai_analysis=": " not in query,
            banned_ids=banned_ids if banned_ids else None,
            only_ids=only_ids,
            vocals=vocals
        )
        track_ids = [track["id"] for track in results]
        prefix, _, value = query.partition(": ")
        entry = {"query": query, "found": len(track_ids)}
        missing = None
        if prefix in NAME_SEARCH_FIELDS and track_ids:
            closest = await self.vector_search_service.closest_names(NAME_SEARCH_FIELDS[prefix], value,
                                                                     NAME_CLOSEST_SHOWN)
            entry["closest_names"] = [{"name": name, "spelling": round(score, 2)} for name, score in closest]
            entry["exact"] = bool(closest) and spelled_alike(closest[0][1])
            best = results[0].get("similarity_score", 0.0)
            track_ids = [track["id"] for track in results if np.isclose(track.get("similarity_score", 0.0), best)]
            entry["found"] = len(track_ids)
            if not entry["exact"]:
                missing = value
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
        candidate["vocals"] = vocals_of(track)
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

    def _sings(self, track_id, vocals):
        track = self.catalog_service.get_track(track_id)
        return bool(track) and (not vocals or vocals_of(track) == vocals)

    async def _loved_ids(self, session_dict, within, banned_ids, vocals=None, skip=()):
        loved = await listener_plays.loved_tracks(session_dict.get('user_id'), session_dict.get('session_id'), within)
        return [item for item in loved
                if item.track_id not in banned_ids and item.track_id not in skip and self._sings(item.track_id, vocals)]

    async def _yours(self, session_dict, track_ids):
        rated = await listener_plays.ratings(session_dict.get('user_id'))
        plays = await listener_plays.track_plays(session_dict.get('user_id'), session_dict.get('session_id'), track_ids)
        return {track_id: listener_plays.brief(rated.get(track_id), plays.get(track_id)) for track_id in track_ids
                if rated.get(track_id) or track_id in plays}

    async def execute_play_loved(self, session_dict, within, play, vocals=None):
        session_id = session_dict.get('session_id')
        banned_ids, only_ids = await self._search_scope(session_dict, within)
        if not only_ids:
            return self._empty_scope(within)
        current = self._resolve_track_id(session_id, "current") if session_id else None
        loved = {item.track_id: item for item in await self._loved_ids(session_dict, within, banned_ids, vocals,
                                                                       skip={current})}
        if not loved:
            return {"status": "no_results", "within": within,
                    "note": "None of the listener's own tracks fit that (the one playing now is left out). Say so, "
                            "and offer to search the whole catalog."}
        picks = listener_plays.weighted_pick({track_id: item.weight for track_id, item in loved.items()}, LOVED_PICKS)
        result = await self._queue_tracks(session_dict, picks, play, [], [], set(loved), within)
        result["picks"] = [{"track": self._track_label(track_id),
                            **listener_plays.brief(loved[track_id].rating, loved[track_id].plays)} for track_id in picks]
        result["note"] = LOVED_PLAY_NOTE
        return result

    async def find_tracks(self, session_dict, query, within=None, how_many=8, starts_with=None, vocals=None):
        banned_ids, only_ids = await self._search_scope(session_dict, within)
        if only_ids is not None and not only_ids:
            return self._empty_scope(within)
        missing = None
        if query:
            pool = len(self.catalog_service.tracks) if starts_with else how_many * FIND_TAKES_PER_SONG
            track_ids, entry, missing = await self._search_track_ids(query, banned_ids, only_ids, n_results=pool,
                                                                     vocals=vocals)
        else:
            track_ids = [item.track_id for item in await self._loved_ids(session_dict, within, banned_ids, vocals)]
        if starts_with:
            by_title = (query or "").startswith(f"{SEARCH_CATEGORY_PREFIXES['song_title']}: ")
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
        yours = await self._yours(session_dict, [candidate["track_id"] for candidate in candidates])
        for candidate in candidates:
            if candidate["track_id"] in yours:
                candidate["yours"] = yours[candidate["track_id"]]
        result = {"status": "ok", "query": query, "note": FIND_NOTE if query else LOVED_LIST_NOTE,
                  "items": candidates}
        if yours:
            result["yours"] = YOURS_NOTE
        if starts_with:
            result["starts_with"] = starts_with
        if only_ids is not None:
            result["within"] = within
        if missing:
            result["not_spelled_exactly"] = [missing]
            result["closest_names"] = entry.get("closest_names", [])
            result["note"] = f"{NOT_SPELLED_NOTE.format(names=missing)} {FIND_NOTE}"
        return result

    async def execute_play_ids(self, session_dict, track_ids, play):
        known = [track_id for track_id in track_ids if self.catalog_service.get_track(track_id)]
        if not known:
            return {"status": "not_found",
                    "note": "No catalog track has that id. Use a track_id from find_tracks, what_aired or pulse_search."}
        return await self._queue_tracks(session_dict, known, play, [], [], None, None)

    async def execute_searches(self, session_dict, searches, within=None, vocals=None):
        session_id = session_dict.get('session_id')

        banned_ids, only_ids = await self._search_scope(session_dict, within)
        if only_ids is not None and not only_ids:
            return self._empty_scope(within)

        tracks_to_add = []
        play_first = False
        per_query = []
        missing = []
        current = self._resolve_track_id(session_id, "current") if session_id else None

        for query, play in searches:
            if not query:
                continue
            smart = ": " not in query
            skip = banned_ids | {current} if smart and current else banned_ids
            track_ids, entry, missed = await self._search_track_ids(query, skip, only_ids, vocals=vocals)
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
            await self.playback_service.add_to_queue(session_id, tracks_to_add, user_id=user_id, play_next=play_first)
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
            result["not_spelled_exactly"] = missing
            result["note"] = NOT_SPELLED_NOTE.format(names=", ".join(missing))
        return result
