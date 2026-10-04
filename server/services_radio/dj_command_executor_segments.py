"""Produced segments for the DJs: news, weather, events, places, artist biographies, lyrics and shoutouts, each
scheduled to air after the reply."""
import asyncio
import datetime
from sqlalchemy import select
from services_radio.conversation_service import save_conversation_to_database
from services_radio import listener_location as location_resolver
from services_radio.dj_prompt_helper_service import UnavailableSegment
from services_radio.dj_content_bank import content_bank
from services_radio.tts_stream_planner import spoken_text
from database.models import User
from services import log_service

NEWS_CATEGORIES = ["world", "nation", "business", "technology", "entertainment", "sports", "science", "health"]
INTERPRETATION_GATE_TIMEOUT_S = 90.0
TRACK_TARGET_LABELS = {"current": "current track", "previous": "previous track", "next": "next track"}


class ExecutorSegments:
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
