import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional
from config.settings import settings
from services import log_service
from services.task_utils import spawn
from services_radio.community_judge import judge, post_context
from services_radio import talk_clock
from services_radio.dj_command_executor_search import SEARCH_CATEGORY_PREFIXES
from services_radio.dj_tools_registry import (
    accepted_arguments, AIRED_NOTE, AIRED_SAID_NOTE, EMPTY_NOTE, EXTRA_TOOL_NAMES, FAILED_ACTION_NOTE,
    FAILED_STATUSES, INVALID_CALL_NOTE, LOVED_SCOPES, MAX_SEGMENTS_PER_TURN, next_options, PERSONAL_PLAYLISTS,
    PLAY_TOOLS, READ_NOTE, READ_TOOLS, SAVE_TOOLS, SEGMENT_NOTE, SEGMENT_TOOLS, SHORTFALL_OUTCOMES, TOOL_COSTS,
    TOOLS_PREFIX,
)
from services_radio.dj_tools_display import activity_label, activity_summary, command_string
from services_radio.dj_tools_args import normalize_tool_args


@dataclass
class DJTurnContext:
    session_dict: Dict[str, Any]
    transcription: str
    origin: str
    gate: asyncio.Event = field(default_factory=asyncio.Event)
    playback_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    records: List[Dict[str, Any]] = field(default_factory=list)
    segments: List[str] = field(default_factory=list)
    saves: List[str] = field(default_factory=list)
    calls_made: int = 0
    live_fetches: int = 0
    pulse_listener: Any = None
    notify: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    activity_sent: bool = False
    planned: Optional[set] = None
    granted: set = field(default_factory=set)

    async def activity(self, phase: str, **data) -> None:
        if self.notify is None:
            return
        self.activity_sent = True
        try:
            await self.notify({"turn_id": self.turn_id, "phase": phase, **data})
        except Exception as e:
            log_service.detail(f"[DJ TOOLS] activity notice failed: {type(e).__name__}: {e}", "commands")

    @property
    def user_id(self) -> Optional[int]:
        return self.session_dict.get("user_id")

    @property
    def executed(self) -> List[Dict[str, Any]]:
        return [record for record in self.records if record["status"] == "executed"]


def authorize_tool_call(name: str, args: Dict[str, Any], ctx: DJTurnContext) -> Optional[str]:
    if ctx.calls_made >= settings.DJ_TOOL_MAX_CALLS_PER_TURN:
        return "That's the limit of tool calls for one turn: work with what you already have."
    if args.get("within") in LOVED_SCOPES and not ctx.user_id:
        return "Only signed-in listeners have liked tracks to search; search the whole catalog instead."
    if name == "pulse_search" and args.get("mine") and not ctx.user_id:
        return "Only signed-in listeners have posts of their own."
    if name in READ_TOOLS:
        return None
    if ctx.origin not in ("voice", "text"):
        return "Actions can only be taken in direct response to the listener's own message."

    if name in SAVE_TOOLS:
        if not ctx.user_id:
            return "Only signed-in listeners can save shoutouts, replies or reviews."
        if ctx.saves:
            return "Only one shoutout, reply or review can be saved per turn."

    if name == "rate_track":
        if not ctx.user_id:
            return "Only signed-in listeners can rate tracks or shoutouts."

    if name == "play_playlist" and args.get("name") in PERSONAL_PLAYLISTS and not ctx.user_id:
        return "Favorites and discovery are personal playlists for signed-in listeners; offer the top hits instead."

    if name == "move_playback" and not ctx.user_id:
        return "Only signed-in listeners can move playback between their devices."

    if name in SEGMENT_TOOLS:
        if name in ctx.segments:
            return "That segment is already scheduled for this turn."
        if len(ctx.segments) >= MAX_SEGMENTS_PER_TURN:
            return "Enough segments are already scheduled for this turn."

    return None


class DJToolRuntime:
    def __init__(self, executor, ctx: DJTurnContext):
        self.executor = executor
        self.ctx = ctx
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {
            "search_and_play": self._search_and_play,
            "find_tracks": self._find_tracks,
            "playback_control": self._playback_control,
            "seed_radio": self._seed_radio,
            "play_playlist": self._play_playlist,
            "rate_track": self._rate_track,
            "move_playback": self._move_playback,
            "radio_settings": self._radio_settings,
            "get_news": self._get_news,
            "get_weather": self._get_weather,
            "get_events": self._get_events,
            "find_places": self._find_places,
            "get_artist_biography": self._get_artist_biography,
            "explain_lyrics": self._explain_lyrics,
            "play_shoutouts": self._play_shoutouts,
            "save_shoutout": self._save_shoutout,
            "save_shoutout_reply": self._save_shoutout_reply,
            "save_review": self._save_review,
            "pulse_search": self._pulse_search,
            "pulse_detail": self._pulse_detail,
            "listener_context": self._listener_context,
            "city_trends": self._city_trends,
            "what_aired": self._what_aired,
            "request_tools": self._request_tools,
        }

    @property
    def session_dict(self) -> Dict[str, Any]:
        return self.ctx.session_dict

    async def dispatch(self, name: str, raw_args: Dict[str, Any]) -> Dict[str, Any]:
        handler = self._handlers.get(name)
        if handler is None:
            return {"status": "error", "reason": f"Unknown tool '{name}'"}

        try:
            args = normalize_tool_args(name, raw_args or {})
        except ValueError as e:
            self.ctx.records.append({"name": name, "args": raw_args, "status": "invalid", "reason": str(e)})
            await self._flash(name, raw_args or {}, "failed", f"bad arguments: {e}"[:80])
            return {"status": "invalid_call", "reason": str(e), "accepts": accepted_arguments(name),
                    "note": INVALID_CALL_NOTE}

        refusal = authorize_tool_call(name, args, self.ctx)
        self.ctx.calls_made += 1
        if refusal:
            log_service.warning(f"[DJ TOOLS] Blocked {name}({args}) for session {self.session_dict.get('session_id')}: {refusal}")
            self.ctx.records.append({"name": name, "args": args, "status": "blocked", "reason": refusal})
            await self._flash(name, args, "blocked", refusal[:80])
            return {"status": "refused", "reason": refusal, "on_air": FAILED_ACTION_NOTE}

        if name in SEGMENT_TOOLS:
            self.ctx.segments.append(name)
        if name in SAVE_TOOLS:
            self.ctx.saves.append(name)

        record = {"name": name, "args": args, "status": "executed", "command": command_string(name, args)}
        self.ctx.records.append(record)
        call_id = f"{self.ctx.turn_id}:{self.ctx.calls_made}"
        await self.ctx.activity("start", call_id=call_id, tool=name, source="tool", label=activity_label(name, args),
                                command=record["command"], query=str(args.get("query") or "")[:80],
                                kinds=list(args.get("kinds") or []), cost=TOOL_COSTS.get(name, "memory"))
        log_service.detail(f"[DJ TOOLS] {record['command']} for session {self.session_dict.get('session_id')}",
                           "commands")
        depth = talk_clock.segment_depth.set(args.get("depth"))
        try:
            result = await handler(args)
            if name in PLAY_TOOLS and isinstance(result, dict) and result.get("now_playing"):
                result = {**result, **await talk_clock.started_note(self._current_track_id())}
        except Exception:
            record["outcome"] = "failed"
            await self.ctx.activity("result", call_id=call_id, tool=name, source="tool", outcome="failed",
                                    summary="couldn't")
            raise
        finally:
            talk_clock.segment_depth.reset(depth)
        record["result"] = result
        outcome, summary = activity_summary(name, result)
        record["outcome"], record["summary"] = outcome, summary
        live = bool(isinstance(result, dict) and result.get("live"))
        await self.ctx.activity("result", call_id=call_id, tool=name, source="tool", outcome=outcome, summary=summary,
                                live=live)
        if isinstance(result, dict):
            result = {**result, "came_from": "live" if live else TOOL_COSTS.get(name, "memory")}
            if result.get("status") in FAILED_STATUSES:
                result["on_air"] = FAILED_ACTION_NOTE
            options = [option for option in next_options(name, args, result) if option in EXTRA_TOOL_NAMES]
            if outcome in SHORTFALL_OUTCOMES and options:
                self.ctx.granted.update(options)
                result["could_try_next"] = options
        return result

    async def _flash(self, name: str, args: Dict[str, Any], outcome: str, summary: str) -> None:
        call_id = f"{self.ctx.turn_id}:x{len(self.ctx.records)}"
        await self.ctx.activity("start", call_id=call_id, tool=name, source="tool", label=activity_label(name, args))
        await self.ctx.activity("result", call_id=call_id, tool=name, source="tool", outcome=outcome, summary=summary)

    def commands_for_display(self) -> Optional[str]:
        commands = [record["command"] for record in self.ctx.executed]
        if not commands:
            return None
        return TOOLS_PREFIX + "\n".join(commands)

    def summary(self) -> str:
        parts = []
        for record in self.ctx.records:
            if record["status"] == "executed":
                parts.append(f"{record['command']} -> {record.get('summary') or record.get('outcome') or 'done'}")
            else:
                parts.append(f"{record['name']} {record['status']} ({record.get('reason') or ''})")
        return "; ".join(parts)

    async def _pulse_listener(self):
        from services_radio.pulse import get_pulse
        pulse = get_pulse()
        if pulse is None:
            return None, None
        if self.ctx.pulse_listener is None:
            self.ctx.pulse_listener = await pulse.listener_for_session(self.session_dict)
        return pulse, self.ctx.pulse_listener

    async def _pulse_search(self, args):
        from services_radio.pulse_items import PulseQuery
        pulse, listener = await self._pulse_listener()
        if pulse is None:
            return {"status": "empty", "note": EMPTY_NOTE}
        track_id = None
        if args.get("about_track"):
            track_id = self.executor._resolve_track_id(self.session_dict.get("session_id"), args["about_track"])
            if not track_id:
                return {"status": "empty", "note": "There is no track at that position."}
        if args.get("mine"):
            args["kinds"] = ["community", "review"]
        elif track_id:
            args["kinds"] = ["review"]
        personal = bool(args.get("mine") or track_id)
        allow_fetch = bool(args["query"]) and not personal and \
            self.ctx.live_fetches < settings.DJ_TOOL_MAX_LIVE_FETCHES
        query = PulseQuery(listener=listener, text=args["query"], kinds=set(args["kinds"]) or None,
                           mine=bool(args.get("mine")), track_id=track_id,
                           kind_order=list(args["kinds"]), when=args.get("when"),
                           limit=args["how_many"] * 4, per_kind=args["how_many"], within=args.get("within"),
                           use_ai=bool(args["query"]) and args["kinds"] == ["track"],
                           allow_fetch=allow_fetch, near_me=args.get("near_me", False),
                           record_demand=self.ctx.origin in ("voice", "text") and not personal,
                           max_age_days=args.get("max_age_days"), sort=args.get("sort") or "relevance")
        items = await pulse.query(query)
        if any(item.live for item in items):
            self.ctx.live_fetches += 1
        if not items:
            if personal:
                return {"status": "empty", "note": "This listener hasn't posted anything yet." if args.get("mine")
                        else "No listener has reviewed that track yet."}
            return {"status": "empty", "note": EMPTY_NOTE}
        pulse.mark_offered(listener, items)
        grouped: Dict[str, list] = {}
        for item in items:
            grouped.setdefault(item.kind, []).append(item.brief(listener.tz_name))
        return {"status": "ok", "note": READ_NOTE, "results": grouped, "live": any(item.live for item in items)}

    async def _pulse_detail(self, args):
        pulse, listener = await self._pulse_listener()
        detail = await pulse.detail(listener, args["item_id"]) if pulse is not None else None
        if not detail:
            return {"status": "empty", "note": "No details on hand for that id."}
        return {"status": "ok", "note": READ_NOTE, "item": detail}

    async def _listener_context(self, _args):
        pulse, listener = await self._pulse_listener()
        if pulse is None:
            return {"status": "empty"}
        return {"status": "ok", "note": READ_NOTE, "listener": await pulse.listener_context(listener)}

    async def _what_aired(self, args):
        from datetime import datetime, timezone
        from services import listener_timeline
        if args.get("id"):
            said = await listener_timeline.talk_detail(self.ctx.user_id, self.session_dict.get("session_id"),
                                                       args["id"])
            if said is None:
                return {"status": "empty", "note": "No segment or talk with that id for this listener."}
            return {"status": "ok", "note": AIRED_SAID_NOTE, "item": said}
        now = datetime.now(timezone.utc)
        span = listener_timeline.window(now, args["from_minutes_ago"], args["to_minutes_ago"],
                                        args.get("around_minutes_ago"))
        entries, more = await listener_timeline.timeline(self.ctx.user_id, self.session_dict.get("session_id"),
                                                         kinds=args["kinds"], span=span, limit=args["how_many"])
        looked_at = span.describe(now)
        if not entries:
            return {"status": "empty", "looked_at": looked_at,
                    "note": "Nothing like that aired for this listener in that stretch. Widen the range only if the "
                            "listener's words allow it."}
        _, listener = await self._pulse_listener()
        result = {"status": "ok", "note": AIRED_NOTE, "looked_at": looked_at,
                  "items": [entry.brief(now, getattr(listener, "tz_name", None)) for entry in entries]}
        if more:
            result["not_shown"] = (f"{more} more in that stretch, the ones closest to the moment are shown"
                                   if span.around else f"{more} older ones in that stretch: narrow the range to see them")
        return result

    async def _request_tools(self, args):
        self.ctx.granted.update(args["names"])
        return {"status": "ok", "granted": args["names"],
                "note": "Those tools are now available: call them now."}

    async def _city_trends(self, args):
        from services_radio.pulse_items import KIND_CHART, KIND_TREND, PulseQuery
        pulse, listener = await self._pulse_listener()
        if pulse is None:
            return {"status": "empty", "note": EMPTY_NOTE}
        topic = args.get("topic") or ""
        items = await pulse.query(PulseQuery(listener=listener, text=topic,
                                             kinds={KIND_TREND} if topic else {KIND_CHART, KIND_TREND}, limit=8,
                                             per_kind=4,
                                             record_demand=False))
        if not items:
            return {"status": "empty",
                    "note": "No city trends on hand yet - the station is still getting to know this city."}
        return {"status": "ok", "note": READ_NOTE, "city": listener.region.name if listener.region else None,
                "items": [item.brief(listener.tz_name) for item in items]}

    def _current_track_id(self) -> Optional[str]:
        playback = self.executor.playback_service if self.executor is not None else None
        state = playback.get_state(self.session_dict.get("session_id")) if playback is not None else None
        queue, index = (state or {}).get("queue") or [], (state or {}).get("current_index") or 0
        return (queue[index] or {}).get("id") if 0 <= index < len(queue) else None

    def _schedule(self, coro, name: str) -> Dict[str, Any]:
        self.executor.spawn_segment(coro, self.session_dict, f"dj_tool_{name}")
        return {"status": "scheduled", "note": SEGMENT_NOTE}

    @staticmethod
    def _catalog_query(args) -> str:
        if not args.get("query"):
            return ""
        prefix = SEARCH_CATEGORY_PREFIXES.get(args.get("category") or "")
        return f"{prefix}: {args['query']}" if prefix else args["query"].replace(": ", " ")

    async def _search_and_play(self, args):
        async with self.ctx.playback_lock:
            if args.get("track_id"):
                return await self.executor.execute_play_ids(self.session_dict, [args["track_id"]],
                                                            args["mode"] == "play")
            if not args.get("query"):
                return await self.executor.execute_play_loved(self.session_dict, args["within"],
                                                              args["mode"] == "play", vocals=args.get("vocals"))
            return await self.executor.execute_searches(self.session_dict,
                                                        [(self._catalog_query(args), args["mode"] == "play")],
                                                        within=args.get("within"), vocals=args.get("vocals"))

    async def _find_tracks(self, args):
        return await self.executor.find_tracks(self.session_dict, self._catalog_query(args),
                                               within=args.get("within"), how_many=args["how_many"],
                                               starts_with=args.get("starts_with"), vocals=args.get("vocals"))

    async def _playback_control(self, args):
        async with self.ctx.playback_lock:
            return await self.executor.execute_playback_control(
                self.session_dict, args["action"], position_s=args.get("position_s"), title=args.get("title"))

    async def _seed_radio(self, args):
        async with self.ctx.playback_lock:
            return await self.executor.execute_seed_radio(self.session_dict, args["category"], args["target"],
                                                          blend=args["blend"])

    async def _move_playback(self, args):
        async with self.ctx.playback_lock:
            return await self.executor.execute_move_playback(self.session_dict, args.get("device"))

    async def _radio_settings(self, args):
        return await self.executor.execute_radio_settings(self.session_dict, args)

    async def _play_playlist(self, args):
        async with self.ctx.playback_lock:
            return await self.executor.execute_playlist(self.session_dict, args["name"])

    async def _rate_track(self, args):
        if args["target"] == "shoutout":
            return await self.executor.execute_shoutout_preference(self.session_dict, args["rating"],
                                                                   args.get("shoutout_id"))
        return await self.executor.execute_track_preference(self.session_dict, args["rating"], args["target"])

    async def _get_news(self, args):
        categories = [args["category"]] if args.get("category") else []
        return self._schedule(
            self.executor.execute_news(self.session_dict, args["scope"], categories, args.get("query") or "",
                                       gate=self.ctx.gate), "get_news")

    async def _get_weather(self, args):
        return self._schedule(
            self.executor._trigger_weather_interpretation(args["when"], self.session_dict, gate=self.ctx.gate),
            "get_weather")

    async def _get_events(self, args):
        return self._schedule(
            self.executor.execute_events(self.session_dict, args["when"], args.get("query") or "", gate=self.ctx.gate),
            "get_events")

    async def _find_places(self, args):
        return self._schedule(
            self.executor._trigger_location_search_interpretation(args["query"], self.session_dict, gate=self.ctx.gate),
            "find_places")

    async def _get_artist_biography(self, args):
        return self._schedule(
            self.executor._trigger_biography_interpretation(args.get("artist") or "", self.session_dict,
                                                            gate=self.ctx.gate),
            "get_artist_biography")

    async def _explain_lyrics(self, args):
        track, track_title = await self.executor.resolve_lyrics_track(self.session_dict, args.get("target"),
                                                                      args.get("song") or "")
        if not track:
            return {"status": "not_found", "reason": "No matching track in the catalog"}
        label = self.executor._track_label(track.get("id"))
        if not track.get("generation_params", {}).get("prompt"):
            return {"status": "no_lyrics", "track": label, "reason": "This track has no lyrics on file (it may be instrumental)"}
        result = self._schedule(
            self.executor.execute_lyrics_interpretation(track, track_title, self.session_dict, gate=self.ctx.gate),
            "explain_lyrics")
        result["track"] = label
        return result

    async def _play_shoutouts(self, args):
        return self._schedule(
            self.executor._trigger_shoutouts_interpretation(self.session_dict, args.get("query"), gate=self.ctx.gate),
            "play_shoutouts")

    def _own_words(self) -> str:
        return "voice message" if self.session_dict.get("recording") else "typed message"

    async def _editor(self, kind: str, context: str = "") -> Optional[Dict[str, Any]]:
        return await judge(self.executor.gemini_ai_service, kind, self.ctx.transcription or "", context)

    @staticmethod
    def _scrapped(kind: str, verdict: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "scrapped", "feedback": verdict.get("feedback"),
                "note": f"The editor scrapped this {kind}, so nothing was saved. Tell the listener honestly, "
                        f"in your own words, with the feedback."}

    async def _save_shoutout(self, _args):
        verdict = await self._editor("shoutout")
        if verdict and not verdict.get("keep"):
            return self._scrapped("shoutout", verdict)
        spawn(self.executor.save_community_item(self.session_dict, "shoutout", text=self.ctx.transcription),
              name="dj_tool_save_shoutout")
        return {"status": "saving", "feedback": (verdict or {}).get("feedback"),
                "note": f"The listener's {self._own_words()} from this turn is being posted as a shoutout; a confirmation appears when it's done."}

    async def _save_shoutout_reply(self, args):
        from services.community_engagement import community_engagement
        content_service = self.executor.user_content_service
        parent_id = args.get("parent_id")
        if not parent_id and content_service is not None:
            candidates = []
            for aired_id in community_engagement.last_aired(self.session_dict.get("session_id")):
                aired = content_service.get_shoutout(aired_id) or {}
                candidate = aired.get("parent_id") or aired_id
                if candidate not in candidates and content_service.parent_problem(candidate) is None:
                    candidates.append(candidate)
            if len(candidates) > 1:
                return {"status": "error",
                        "reason": "Several shoutouts have played recently: call what_aired to see them and pass the "
                                  "parent_id of the one they mean"}
            parent_id = candidates[0] if candidates else None
        if not parent_id:
            return {"status": "error",
                    "reason": "No shoutout has played recently: call what_aired to look further back, or ask which "
                              "one they mean"}
        problem = content_service.parent_problem(parent_id) if content_service is not None else "unavailable"
        if problem:
            return {"status": "error", "reason": problem}
        parent = content_service.get_shoutout(parent_id) or {}
        parent_name = (parent.get("user_data") or {}).get("username") or "a listener"
        verdict = await self._editor("reply", post_context(parent=parent))
        if verdict and not verdict.get("keep"):
            return self._scrapped("reply", verdict)
        spawn(self.executor.save_community_item(self.session_dict, "reply", text=self.ctx.transcription,
                                                parent_id=parent_id), name="dj_tool_save_shoutout_reply")
        return {"status": "saving", "replying_to": parent_name, "feedback": (verdict or {}).get("feedback"),
                "note": f"The listener's {self._own_words()} from this turn is being posted as a reply; a confirmation appears when it's done."}

    async def _save_review(self, args):
        session_id = self.session_dict.get("session_id")
        track_id = self.executor._resolve_track_id(session_id, args["target"]) if session_id else None
        if not track_id:
            return {"status": "error", "reason": "No track at that position"}
        verdict = await self._editor("review", f"Song: {self.executor._track_label(track_id)}")
        if verdict and not verdict.get("keep"):
            return self._scrapped("review", verdict)
        spawn(self.executor.save_community_item(self.session_dict, "review", text=self.ctx.transcription,
                                                track_id=track_id), name="dj_tool_save_review")
        return {"status": "saving", "track": self.executor._track_label(track_id), "feedback": (verdict or {}).get("feedback"),
                "note": f"The listener's {self._own_words()} from this turn is being saved as a review of this track."}
