import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from google.genai import types

from config.settings import settings
from services import log_service
from services.task_utils import spawn
from services_radio.dj_command_executor import (
    SEARCH_CATEGORY_PREFIXES,
    SEED_MODE_DISPLAY,
    PLAYLIST_DISPLAY,
    NEWS_CATEGORIES,
)

SEARCH_CATEGORIES = list(SEARCH_CATEGORY_PREFIXES.keys())
SEED_MODES = list(SEED_MODE_DISPLAY.keys())
PLAYLISTS = list(PLAYLIST_DISPLAY.keys())
TRACK_TARGETS = ["current", "previous", "next"]
BRACE_TARGETS = {"current": "current", "previous": "earlier", "next": "later"}

SAVE_TOOLS = {"save_shoutout", "save_shoutout_reply", "save_opinion"}
SEGMENT_TOOLS = {"get_news", "get_weather", "get_events", "find_places", "get_artist_biography", "explain_lyrics",
                 "play_shoutouts"}
MAX_SEGMENTS_PER_TURN = 3
MAX_TEXT_ARG_CHARS = 200

TOOL_MODE_REPLACED_NODES = {"station_capabilities_detailed"}
UNTRUSTED_NODE_KEYS = {
    "conversation_recent",
    "conversation_last_turn",
    "data_shoutouts_data",
    "data_news_report",
    "data_biography",
    "data_location_report",
    "data_events_report",
    "data_weather_report",
    "data_lyrics",
    "track_lyrics_preview",
    "shoutout_interests",
}

FAILED_STATUSES = {"refused", "error", "no_results", "not_found", "no_lyrics"}
FAILED_ACTION_NOTE = "This did NOT happen. Don't pretend it did - tell the listener honestly, in character."

SEGMENT_NOTE = ("A dedicated segment with the full details airs right after your reply. "
                "Acknowledge it briefly and hand off - do not invent the details.")

_SHOUTOUT_INTENT = re.compile(
    r"\b(shout[\s-]?outs?|save|record|post|publish|share|broadcast|on (the )?air|"
    r"tell (everyone|everybody|the community|all)|message (to|for) (everyone|everybody|the community|listeners))\b",
    re.IGNORECASE)
_REPLY_INTENT = re.compile(r"\b(repl(y|ies|ying)|respond|response|answer|get back to|shout[\s-]?back)\b", re.IGNORECASE)
_OPINION_INTENT = re.compile(r"\b(opinion|review|feedback|verdict|rate|rating|save|record)\b", re.IGNORECASE)
_TRACK_REFERENCE = re.compile(
    r"\b(song|track|tune|beat|jam|banger|album|chorus|verse|vocals?|lyrics|melody|drop|production|this one|that one)\b",
    re.IGNORECASE)
_NEGATIVE_INTENT = re.compile(
    r"\b(ban|never|hate|dislike|don'?t (like|want|enjoy)|do not (like|want)|not (a )?fan|can'?t stand|remove|block|"
    r"awful|terrible|sucks?|crap|shit|garbage|trash|annoying|worst|thumbs down|unlike|undo|clear|get rid)\b",
    re.IGNORECASE)
_PARENT_ID = re.compile(r"^\d+_\d+$")


def _schema(properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _enum(values: List[str], description: str) -> Dict[str, Any]:
    return {"type": "string", "enum": values, "description": description}


def _string(description: str) -> Dict[str, Any]:
    return {"type": "string", "description": description}


DJ_FUNCTION_DECLARATIONS = [
    types.FunctionDeclaration(
        name="search_and_play",
        description="Search the station catalog in one category and either start playing the best match now or add matches to the queue. Returns how many tracks were found and their titles.",
        parameters_json_schema=_schema({
            "category": _enum(SEARCH_CATEGORIES, "Which catalog field to search: song_title, primary_artist, similar_artists, primary_genre, secondary_genres (sub-genres/tags), mood, style (production), theme (lyrical subject), vocal (delivery), lyrics (lyric content)."),
            "query": _string("What to search for, e.g. 'Nine Inch Nails', 'melancholic', 'TR-808 drums'."),
            "mode": _enum(["play", "queue"], "play = start the first match immediately; queue = add matches after the current track."),
        }, ["category", "query", "mode"]),
    ),
    types.FunctionDeclaration(
        name="playback_control",
        description="Control playback for this listener's session: skip to the next track, go back, pause, or resume.",
        parameters_json_schema=_schema({
            "action": _enum(["next", "previous", "pause", "resume"], "Playback action."),
        }, ["action"]),
    ),
    types.FunctionDeclaration(
        name="seed_radio",
        description="Turn the radio into a station built from the CURRENT track, matched on one aspect (or 'all' for a balanced mix).",
        parameters_json_schema=_schema({
            "mode": _enum(SEED_MODES, "Aspect of the current track to match."),
        }, ["mode"]),
    ),
    types.FunctionDeclaration(
        name="play_playlist",
        description="Switch to a playlist: the listener's favorites, discovery (favorites plus similar new tracks), or the station's top hits (all time, this week, today).",
        parameters_json_schema=_schema({
            "name": _enum(PLAYLISTS, "Playlist to play."),
        }, ["name"]),
    ),
    types.FunctionDeclaration(
        name="rate_track",
        description="Record the listener's rating of a track. like = enjoys it; superstar = an all-time favourite; dislike = clear any existing rating; ban = never play it again.",
        parameters_json_schema=_schema({
            "rating": _enum(["like", "superstar", "dislike", "ban"], "Rating to record."),
            "target": _enum(TRACK_TARGETS, "Which track: the one playing now, the previous one, or the next one."),
        }, ["rating", "target"]),
    ),
    types.FunctionDeclaration(
        name="get_news",
        description="Schedule a news segment that airs right after your reply.",
        parameters_json_schema=_schema({
            "scope": _enum(["world", "national", "local"], "Geographic scope."),
            "category": _enum(NEWS_CATEGORIES, "Optional news category."),
            "query": _string("Optional specific topic."),
        }, ["scope"]),
    ),
    types.FunctionDeclaration(
        name="get_weather",
        description="Schedule a weather segment for the listener's location that airs right after your reply.",
        parameters_json_schema=_schema({
            "when": _enum(["current", "today", "tomorrow", "week"], "Forecast period."),
        }, ["when"]),
    ),
    types.FunctionDeclaration(
        name="get_events",
        description="Schedule a segment about concerts, festivals and local events near the listener that airs right after your reply.",
        parameters_json_schema=_schema({
            "when": _enum(["today", "tomorrow", "week", "month"], "Time window."),
            "query": _string("Optional kind of event, e.g. 'techno', 'comedy'."),
        }, ["when"]),
    ),
    types.FunctionDeclaration(
        name="find_places",
        description="Schedule a segment about nearby places (restaurants, bars, venues, shops, amenities) that airs right after your reply.",
        parameters_json_schema=_schema({
            "query": _string("What kind of place, e.g. 'late night pizza'."),
        }, ["query"]),
    ),
    types.FunctionDeclaration(
        name="get_artist_biography",
        description="Schedule an artist biography segment that airs right after your reply.",
        parameters_json_schema=_schema({
            "artist": _string("Artist name. Omit for the artist of the current track."),
        }),
    ),
    types.FunctionDeclaration(
        name="explain_lyrics",
        description="Schedule a lyrics breakdown segment that airs right after your reply. Returns whether the track and its lyrics were found.",
        parameters_json_schema=_schema({
            "target": _enum(TRACK_TARGETS, "Which queued track, when no song is named."),
            "song": _string("Optional song title (and artist) to look up instead of a queued track."),
        }),
    ),
    types.FunctionDeclaration(
        name="play_shoutouts",
        description="Schedule a segment that plays community shoutouts from other listeners right after your reply.",
        parameters_json_schema=_schema({
            "query": _string("Optional topic to find relevant shoutouts."),
        }),
    ),
    types.FunctionDeclaration(
        name="save_shoutout",
        description="Post the listener's own voice message from THIS turn as a public shoutout to the PLAiR community. Only when the listener explicitly asks to save/post/share their message.",
    ),
    types.FunctionDeclaration(
        name="save_shoutout_reply",
        description="Post the listener's own voice message from THIS turn as a public reply to an existing shoutout. Only when the listener explicitly asks to reply to that shoutout.",
        parameters_json_schema=_schema({
            "parent_id": _string("ID of the shoutout being answered, '<userId>_<timestamp>' as seen in its audio path /shoutouts/audio/<userId>/<timestamp>.mp3."),
        }, ["parent_id"]),
    ),
    types.FunctionDeclaration(
        name="save_opinion",
        description="Save the listener's own spoken review of a track from THIS turn so other listeners can hear it. Only when the listener gives a substantial opinion about the track or asks to save their review.",
        parameters_json_schema=_schema({
            "target": _enum(TRACK_TARGETS, "Which track the opinion is about."),
        }, ["target"]),
    ),
]

TOOL_NAMES = {declaration.name for declaration in DJ_FUNCTION_DECLARATIONS}


def _brace(*tokens: str, value: Optional[str] = None) -> str:
    command = "(" + "".join("{" + token + "}" for token in tokens if token) + ")"
    if value:
        command += '"' + value.replace('"', "'") + '"'
    return command


def command_string(name: str, args: Dict[str, Any]) -> str:
    if name == "search_and_play":
        return _brace("play" if args["mode"] == "play" else "cue", args["category"], value=args["query"])
    if name == "playback_control":
        return _brace({"next": "next", "previous": "previous", "pause": "mute", "resume": "activate"}[args["action"]])
    if name == "seed_radio":
        return _brace("play", "seed", value=args["mode"])
    if name == "play_playlist":
        return _brace("play", "playlist", value=args["name"])
    if name == "rate_track":
        return _brace(args["rating"], BRACE_TARGETS[args["target"]])
    if name == "get_news":
        scope = {"national": "national", "local": "local"}.get(args["scope"], "")
        return _brace("news", scope, args.get("category") or "", value=args.get("query"))
    if name == "get_weather":
        return _brace("weather", {"today": "today", "tomorrow": "tomorrow", "week": "this_week"}.get(args["when"], ""))
    if name == "get_events":
        return _brace("events", {"today": "today", "tomorrow": "tomorrow", "week": "this_week"}.get(args["when"], ""),
                      value=args.get("query"))
    if name == "find_places":
        return _brace("find_amenities", value=args["query"])
    if name == "get_artist_biography":
        return _brace("biography", value=args.get("artist"))
    if name == "explain_lyrics":
        if args.get("song"):
            return _brace("lyrics", value=args["song"])
        return _brace("lyrics", BRACE_TARGETS[args["target"]])
    if name == "play_shoutouts":
        return _brace("play_shoutouts", value=args.get("query"))
    if name == "save_shoutout":
        return _brace("save_shoutout")
    if name == "save_shoutout_reply":
        return _brace("save_shoutout_reply", value=args["parent_id"])
    if name == "save_opinion":
        return _brace("save_opinion", BRACE_TARGETS[args["target"]])
    return _brace(name)


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

    @property
    def user_id(self) -> Optional[int]:
        return self.session_dict.get("user_id")

    @property
    def executed(self) -> List[Dict[str, Any]]:
        return [record for record in self.records if record["status"] == "executed"]


def normalize_tool_args(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    def text(key: str, required: bool = False) -> Optional[str]:
        value = args.get(key)
        value = str(value).strip()[:MAX_TEXT_ARG_CHARS] if value is not None else ""
        if required and not value:
            raise ValueError(f"'{key}' is required")
        return value or None

    def choice(key: str, allowed: List[str], default: Optional[str] = None) -> Optional[str]:
        value = args.get(key)
        value = str(value).strip().lower() if value is not None else ""
        if not value:
            if default is None:
                raise ValueError(f"'{key}' is required (one of {', '.join(allowed)})")
            return default
        if value not in allowed:
            raise ValueError(f"'{key}' must be one of {', '.join(allowed)}")
        return value

    if name == "search_and_play":
        return {"category": choice("category", SEARCH_CATEGORIES), "query": text("query", True),
                "mode": choice("mode", ["play", "queue"], "play")}
    if name == "playback_control":
        return {"action": choice("action", ["next", "previous", "pause", "resume"])}
    if name == "seed_radio":
        return {"mode": choice("mode", SEED_MODES)}
    if name == "play_playlist":
        return {"name": choice("name", PLAYLISTS)}
    if name == "rate_track":
        return {"rating": choice("rating", ["like", "superstar", "dislike", "ban"]),
                "target": choice("target", TRACK_TARGETS, "current")}
    if name == "get_news":
        category = args.get("category")
        return {"scope": choice("scope", ["world", "national", "local"], "world"),
                "category": choice("category", NEWS_CATEGORIES) if category else None,
                "query": text("query")}
    if name == "get_weather":
        return {"when": choice("when", ["current", "today", "tomorrow", "week"], "current")}
    if name == "get_events":
        return {"when": choice("when", ["today", "tomorrow", "week", "month"], "month"), "query": text("query")}
    if name == "find_places":
        return {"query": text("query", True)}
    if name == "get_artist_biography":
        return {"artist": text("artist")}
    if name == "explain_lyrics":
        song = text("song")
        return {"song": song, "target": None if song else choice("target", TRACK_TARGETS, "current")}
    if name == "play_shoutouts":
        return {"query": text("query")}
    if name == "save_shoutout":
        return {}
    if name == "save_shoutout_reply":
        parent_id = text("parent_id", True) or ""
        if not _PARENT_ID.match(parent_id):
            raise ValueError("parent_id must look like '<userId>_<timestamp>'")
        return {"parent_id": parent_id}
    if name == "save_opinion":
        return {"target": choice("target", TRACK_TARGETS, "current")}
    raise ValueError(f"Unknown tool '{name}'")


def authorize_tool_call(name: str, args: Dict[str, Any], ctx: DJTurnContext) -> Optional[str]:
    if ctx.origin not in ("voice", "text"):
        return "Actions can only be taken in direct response to the listener's own message."
    if ctx.calls_made >= settings.DJ_TOOL_MAX_CALLS_PER_TURN:
        return "Too many actions in one turn."

    listener_text = ctx.transcription or ""

    if name in SAVE_TOOLS:
        if not ctx.user_id:
            return "Only signed-in listeners can save shoutouts, replies or opinions."
        if ctx.origin != "voice":
            return "Saving needs the listener's own voice recording from this turn; ask them to record it with the mic."
        if ctx.saves:
            return "Only one shoutout, reply or opinion can be saved per turn."
        if name == "save_shoutout" and not _SHOUTOUT_INTENT.search(listener_text):
            return "The listener did not ask to save or post a shoutout in their own message."
        if name == "save_shoutout_reply" and not _REPLY_INTENT.search(listener_text):
            return "The listener did not ask to reply to a shoutout in their own message."
        if name == "save_opinion" and not (
                _OPINION_INTENT.search(listener_text)
                or (_TRACK_REFERENCE.search(listener_text) and len(listener_text.split()) >= 8)):
            return "The listener did not give or ask to save an opinion about a track in their own message."

    if name == "rate_track":
        if not ctx.user_id:
            return "Only signed-in listeners can rate tracks."
        if args["rating"] in ("ban", "dislike") and not _NEGATIVE_INTENT.search(listener_text):
            return "The listener did not ask to ban or clear the rating of a track in their own message."

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
            "playback_control": self._playback_control,
            "seed_radio": self._seed_radio,
            "play_playlist": self._play_playlist,
            "rate_track": self._rate_track,
            "get_news": self._get_news,
            "get_weather": self._get_weather,
            "get_events": self._get_events,
            "find_places": self._find_places,
            "get_artist_biography": self._get_artist_biography,
            "explain_lyrics": self._explain_lyrics,
            "play_shoutouts": self._play_shoutouts,
            "save_shoutout": self._save_shoutout,
            "save_shoutout_reply": self._save_shoutout_reply,
            "save_opinion": self._save_opinion,
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
            return {"status": "error", "reason": str(e)}

        refusal = authorize_tool_call(name, args, self.ctx)
        self.ctx.calls_made += 1
        if refusal:
            log_service.warning(f"[DJ TOOLS] Blocked {name}({args}) for session {self.session_dict.get('session_id')}: {refusal}")
            self.ctx.records.append({"name": name, "args": args, "status": "blocked", "reason": refusal})
            return {"status": "refused", "reason": refusal, "on_air": FAILED_ACTION_NOTE}

        if name in SEGMENT_TOOLS:
            self.ctx.segments.append(name)
        if name in SAVE_TOOLS:
            self.ctx.saves.append(name)

        record = {"name": name, "args": args, "status": "executed", "command": command_string(name, args)}
        self.ctx.records.append(record)
        log_service.commands(f"[DJ TOOLS] Executing {record['command']} for session {self.session_dict.get('session_id')}")
        result = await handler(args)
        record["result"] = result
        if isinstance(result, dict) and result.get("status") in FAILED_STATUSES:
            result = {**result, "on_air": FAILED_ACTION_NOTE}
        return result

    def commands_for_display(self) -> Optional[str]:
        commands = [record["command"] for record in self.ctx.executed]
        if not commands:
            return None
        return "[HAL11000]" + "\n".join(commands)

    def _schedule(self, coro, name: str) -> Dict[str, Any]:
        self.executor.spawn_segment(coro, self.session_dict, f"dj_tool_{name}")
        return {"status": "scheduled", "note": SEGMENT_NOTE}

    async def _search_and_play(self, args):
        query = f"{SEARCH_CATEGORY_PREFIXES[args['category']]}: {args['query']}"
        async with self.ctx.playback_lock:
            return await self.executor.execute_searches(self.session_dict, [(query, args["mode"] == "play")])

    async def _playback_control(self, args):
        async with self.ctx.playback_lock:
            return await self.executor.execute_playback_control(self.session_dict, args["action"])

    async def _seed_radio(self, args):
        async with self.ctx.playback_lock:
            return await self.executor.execute_seed_radio(self.session_dict, args["mode"])

    async def _play_playlist(self, args):
        async with self.ctx.playback_lock:
            return await self.executor.execute_playlist(self.session_dict, args["name"])

    async def _rate_track(self, args):
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

    async def _save_shoutout(self, _args):
        spawn(self.executor._save_shoutout(self.session_dict), name="dj_tool_save_shoutout")
        return {"status": "saving", "note": "The listener's voice message from this turn is being posted as a shoutout; a confirmation appears when it's done."}

    async def _save_shoutout_reply(self, args):
        content_service = self.executor.user_content_service
        if content_service is not None and not await asyncio.to_thread(content_service.is_root_shoutout,
                                                                       args["parent_id"]):
            return {"status": "error", "reason": "That shoutout doesn't exist or is already a reply"}
        spawn(self.executor._save_shoutout_reply(self.session_dict, args["parent_id"]),
              name="dj_tool_save_shoutout_reply")
        return {"status": "saving", "note": "The listener's voice reply from this turn is being posted; a confirmation appears when it's done."}

    async def _save_opinion(self, args):
        session_id = self.session_dict.get("session_id")
        track_id = self.executor._resolve_track_id(session_id, args["target"]) if session_id else None
        if not track_id:
            return {"status": "error", "reason": "No track at that position"}
        spawn(self.executor.execute_save_opinion(self.session_dict, args["target"]), name="dj_tool_save_opinion")
        return {"status": "saving", "track": self.executor._track_label(track_id),
                "note": "The listener's spoken opinion from this turn is being saved for this track."}
