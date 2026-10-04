"""Checking a DJ tool call before it runs: normalising its arguments and the plain limits on what may run."""
from typing import Any, Dict, List, Optional
from config.settings import settings
from services_radio import talk_clock
from services_radio.talk_clock import DEFAULT_DEPTH
from services_radio.dj_command_executor_segments import NEWS_CATEGORIES
from services_radio.dj_tools_registry import (
    AIRED_KINDS, BLEND_CATEGORIES, EXTRA_TOOL_NAMES, FIND_DEFAULT, FIND_MAX, LOVED_SCOPES, MAX_TEXT_ARG_CHARS,
    MUSIC_SOURCES, PLAYBACK_ACTIONS, PLAYLISTS, PULSE_KINDS, PULSE_SORT, PULSE_WHEN, RADIO_TOGGLES, RATINGS,
    RATING_TARGETS, SEARCH_CATEGORIES, SEARCH_SCOPES, SEED_CATEGORIES, SEGMENT_TOOLS, STARTS_WITH_MAX_CHARS,
    TRACK_TARGETS, VOCALS, WEATHER_PERIODS, WEATHER_SERVICE_PERIODS, _PARENT_ID, _TRACK_ID,
)


def normalize_tool_args(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_tool_args(name, args)
    if name in SEGMENT_TOOLS:
        depth = str(args.get("depth") or DEFAULT_DEPTH).strip().lower()
        if depth not in talk_clock.DEPTHS:
            raise ValueError(f"'depth' must be one of {', '.join(talk_clock.DEPTHS)}")
        normalized["depth"] = depth
    return normalized


def _normalize_tool_args(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
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

    def track_target(default: Optional[str] = "current", allowed: List[str] = TRACK_TARGETS) -> str:
        track_id = (text("track_id") or "").split(":")[-1]
        if track_id:
            if not _TRACK_ID.match(track_id):
                raise ValueError("'track_id' must be a track id from what_aired")
            return track_id
        return choice("target", allowed, default)

    def number(key: str, default: float, top: float, low: float = 1.0) -> float:
        try:
            value = float(args[key]) if args.get(key) not in (None, "") else default
        except (TypeError, ValueError):
            raise ValueError(f"'{key}' must be a number")
        return max(low, min(value, top))

    if name == "what_aired":
        kinds = args.get("kinds") or []
        kinds = [str(k).strip().lower() for k in ([kinds] if isinstance(kinds, str) else kinds) if str(k).strip()]
        if any(k not in AIRED_KINDS for k in kinds):
            raise ValueError(f"'kinds' must be from {', '.join(AIRED_KINDS)}")
        top = settings.DJ_TIMELINE_MAX_MINUTES
        return {"kinds": kinds, "id": text("id"),
                "around_minutes_ago": number("around_minutes_ago", 0, top, low=0.0)
                if args.get("around_minutes_ago") not in (None, "") else None,
                "from_minutes_ago": number("from_minutes_ago", settings.DJ_TIMELINE_DEFAULT_MINUTES, top),
                "to_minutes_ago": number("to_minutes_ago", 0, top, low=0.0),
                "how_many": int(number("how_many", 10, settings.DJ_TIMELINE_MAX_ENTRIES))}
    if name == "pulse_search":
        kinds = args.get("kinds") or []
        if isinstance(kinds, str):
            kinds = [kinds]
        kinds = [str(k).strip().lower() for k in kinds if str(k).strip()]
        if any(k not in PULSE_KINDS for k in kinds):
            raise ValueError(f"'kinds' must be from {', '.join(PULSE_KINDS)}")
        when = args.get("when")
        try:
            max_age = float(args["max_age_days"]) if args.get("max_age_days") not in (None, "") else None
        except (TypeError, ValueError):
            raise ValueError("'max_age_days' must be a number")
        try:
            how_many = int(float(args["how_many"])) if args.get("how_many") not in (None, "") else None
        except (TypeError, ValueError):
            raise ValueError("'how_many' must be a number")
        how_many = max(1, min(how_many or settings.PULSE_TOOL_PER_KIND, settings.PULSE_TOOL_MAX_PER_KIND))
        return {"query": text("query") or "", "kinds": kinds,
                "when": choice("when", PULSE_WHEN) if when else None,
                "near_me": bool(args.get("near_me")), "max_age_days": max_age,
                "sort": choice("sort", PULSE_SORT, "relevance"), "how_many": how_many,
                "within": choice("within", SEARCH_SCOPES, "catalog"), "mine": bool(args.get("mine")),
                "about_track": choice("about_track", TRACK_TARGETS) if args.get("about_track") else None}
    if name == "pulse_detail":
        return {"item_id": text("item_id", True)}
    if name == "listener_context":
        return {}
    if name == "request_tools":
        names = args.get("names") or []
        if isinstance(names, str):
            names = [names]
        names = [str(n).strip() for n in names if str(n).strip() in EXTRA_TOOL_NAMES]
        if not names:
            raise ValueError(f"'names' must be from {', '.join(EXTRA_TOOL_NAMES)}")
        return {"names": names, "reason": text("reason") or ""}
    if name == "city_trends":
        return {"topic": text("topic") or ""}
    if name == "search_and_play":
        track_id = (text("track_id") or "").split(":")[-1]
        if track_id and not _TRACK_ID.match(track_id):
            raise ValueError("'track_id' must be a track id from find_tracks, what_aired or pulse_search")
        within = choice("within", SEARCH_SCOPES, "catalog")
        return {"category": choice("category", SEARCH_CATEGORIES) if args.get("category") else None,
                "query": text("query", required=not track_id and within not in LOVED_SCOPES),
                "track_id": track_id or None,
                "vocals": choice("vocals", VOCALS) if args.get("vocals") else None,
                "mode": choice("mode", ["play", "queue"], "play"), "within": within}
    if name == "find_tracks":
        within = choice("within", SEARCH_SCOPES, "catalog")
        return {"query": text("query", required=within not in LOVED_SCOPES),
                "category": choice("category", SEARCH_CATEGORIES) if args.get("category") else None,
                "within": within,
                "starts_with": (text("starts_with") or "")[:STARTS_WITH_MAX_CHARS] or None,
                "vocals": choice("vocals", VOCALS) if args.get("vocals") else None,
                "how_many": int(number("how_many", FIND_DEFAULT, FIND_MAX))}
    if name == "playback_control":
        action = choice("action", PLAYBACK_ACTIONS)
        if action == "seek":
            try:
                return {"action": action, "position_s": max(0.0, float(args.get("position_s")))}
            except (TypeError, ValueError):
                raise ValueError("'position_s' is required for seek: seconds from the start of the track")
        if action == "remove":
            return {"action": action, "title": text("title")}
        return {"action": action}
    if name == "seed_radio":
        raw = args.get("blend") or []
        blend = []
        for item in [raw] if isinstance(raw, dict) else raw:
            if not isinstance(item, dict):
                raise ValueError("'blend' is a list of {category, weight, words}")
            category = str(item.get("category") or "").strip().lower()
            if category not in BLEND_CATEGORIES:
                raise ValueError(f"each blend 'category' must be one of {', '.join(BLEND_CATEGORIES)}")
            try:
                weight = float(item["weight"]) if item.get("weight") not in (None, "") else 1.0
            except (TypeError, ValueError):
                raise ValueError("each blend 'weight' must be a number above 0")
            if weight <= 0:
                raise ValueError("each blend 'weight' must be a number above 0")
            words = str(item.get("words") or "").strip()[:MAX_TEXT_ARG_CHARS] or None
            blend.append({"category": category, "weight": weight, "words": words})
        if not blend and not args.get("category"):
            raise ValueError("give 'category' (one aspect) or 'blend' (several aspects with weights)")
        return {"category": None if blend else choice("category", SEED_CATEGORIES), "blend": blend or None,
                "target": track_target()}
    if name == "move_playback":
        return {"device": text("device")}
    if name == "radio_settings":
        changes: Dict[str, Any] = {}
        for key in RADIO_TOGGLES:
            value = args.get(key)
            if isinstance(value, str) and value.strip().lower() in ("true", "false"):
                value = value.strip().lower() == "true"
            if value is None:
                continue
            if not isinstance(value, bool):
                raise ValueError(f"'{key}' must be true or false")
            changes[key] = value
        if args.get("music_source"):
            changes["music_source"] = choice("music_source", MUSIC_SOURCES)
        if args.get("feature_interval_min") not in (None, ""):
            try:
                changes["feature_interval_min"] = int(float(args["feature_interval_min"]))
            except (TypeError, ValueError):
                raise ValueError("'feature_interval_min' must be a number of minutes")
        return changes
    if name == "play_playlist":
        return {"name": choice("name", PLAYLISTS)}
    if name == "rate_track":
        shoutout_id = (text("shoutout_id") or "").split(":")[-1]
        if not shoutout_id and _PARENT_ID.match((text("track_id") or "").split(":")[-1]):
            shoutout_id = text("track_id").split(":")[-1]
        if shoutout_id and not _PARENT_ID.match(shoutout_id):
            raise ValueError("shoutout_id must look like '<userId>_<timestamp>', or be left out")
        target = "shoutout" if shoutout_id else track_target(allowed=RATING_TARGETS)
        return {"rating": choice("rating", RATINGS), "target": target,
                "shoutout_id": (shoutout_id or None) if target == "shoutout" else None}
    if name == "get_news":
        category = args.get("category")
        return {"scope": choice("scope", ["world", "national", "local"], "world"),
                "category": choice("category", NEWS_CATEGORIES) if category else None,
                "query": text("query")}
    if name == "get_weather":
        when = args.get("when") or ["now"]
        when = [when] if isinstance(when, str) else list(when)
        periods = list(dict.fromkeys(str(period).strip().lower() for period in when if str(period).strip()))
        if not periods or any(period not in WEATHER_PERIODS for period in periods):
            raise ValueError(f"'when' must be one or more of {', '.join(WEATHER_PERIODS)}")
        return {"when": [WEATHER_SERVICE_PERIODS.get(period, period) for period in periods]}
    if name == "get_events":
        when = choice("when", ["today", "tonight", "tomorrow", "weekend", "week", "month"], "month")
        return {"when": {"tonight": "today", "weekend": "week"}.get(when, when), "query": text("query")}
    if name == "find_places":
        return {"query": text("query", True)}
    if name == "get_artist_biography":
        return {"artist": text("artist")}
    if name == "explain_lyrics":
        song = text("song")
        return {"song": song, "target": None if song else track_target()}
    if name == "play_shoutouts":
        return {"query": text("query")}
    if name == "save_shoutout":
        return {}
    if name == "save_shoutout_reply":
        parent_id = (text("parent_id") or "").split(":")[-1]
        if parent_id and not _PARENT_ID.match(parent_id):
            raise ValueError("parent_id must look like '<userId>_<timestamp>', or be left out")
        return {"parent_id": parent_id or None}
    if name == "save_review":
        return {"target": track_target()}
    raise ValueError(f"Unknown tool '{name}'")
