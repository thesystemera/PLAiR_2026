"""How DJ tool calls show up: the brace-style command strings, the activity chips and their short result summaries."""
from typing import Any, Dict, Optional
from services_radio.talk_clock import DEFAULT_DEPTH
from services_radio.dj_tools_registry import BRACE_TARGETS, FAILED_STATUSES, SEGMENT_TOOLS


def _brace(*tokens: str, value: Optional[str] = None) -> str:
    command = "(" + "".join("{" + token + "}" for token in tokens if token) + ")"
    if value:
        command += '"' + value.replace('"', "'") + '"'
    return command


ACTIVITY = {
    "pulse_search": "checking the station's notes on {query}",
    "pulse_detail": "reading the details",
    "request_tools": "grabbing more studio tools",
    "listener_context": "remembering what this listener's into",
    "what_aired": "checking the log of what's been on air",
    "city_trends": "checking what the whole city's been playing",
    "search_and_play": "digging through the crates for {query}",
    "find_tracks": "flipping through the records for {query}",
    "seed_radio": "building a station around this track",
    "move_playback": "checking the listener's devices",
    "play_playlist": "lining up the playlist",
    "get_news": "pulling the news wire",
    "get_weather": "checking the sky",
    "get_events": "flicking through the gig guide",
    "find_places": "scouting spots nearby",
    "get_artist_biography": "digging up the artist's story",
    "explain_lyrics": "reading the lyric sheet",
    "play_shoutouts": "going through the listener shoutouts",
}
LABELS = {
    "playback_control": "working the transport",
    "rate_track": "saving the rating",
    "radio_settings": "at the Radio Mode desk",
    "save_shoutout": "posting the shoutout",
    "save_shoutout_reply": "posting the reply",
    "save_review": "saving the review",
}


def activity_label(name: str, args: Dict[str, Any]) -> str:
    return tool_activity([(name, args)]) or LABELS.get(name, name.replace("_", " "))


def tool_activity(calls) -> str:
    phrases = []
    for name, args in calls or ():
        template = ACTIVITY.get(name)
        if not template:
            continue
        query = str((args or {}).get("query") or "").strip()[:60]
        phrase = template.format(query=query) if query or "{query}" not in template else \
            template.replace(" on {query}", "").replace(" for {query}", "")
        if phrase not in phrases:
            phrases.append(phrase)
    return " and ".join(phrases[:2])


KIND_NOUNS = {"event": ("gig", "gigs"), "place": ("place", "places"), "news": ("story", "stories"),
              "weather": ("forecast", "forecasts"), "area": ("area note", "area notes"),
              "artist": ("artist bio", "artist bios"), "track": ("track", "tracks"),
              "community": ("shoutout", "shoutouts"), "review": ("review", "reviews"), "chart": ("chart", "charts"),
              "trend": ("trend", "trends")}


def activity_summary(name: str, result: Any) -> tuple[str, str]:
    if not isinstance(result, dict):
        return "done", ""
    status = result.get("status") or "ok"
    if result.get("granted"):
        return "done", "now has " + ", ".join(result["granted"])
    if status == "refused":
        return "blocked", str(result.get("reason") or "refused")[:80]
    if status in FAILED_STATUSES:
        return "failed", "nothing doing" if status in ("no_results", "not_found", "no_lyrics") else "couldn't"
    if status == "empty":
        return "empty", "nothing on hand"
    grouped = result.get("results")
    if isinstance(grouped, dict):
        parts = []
        for kind, items in grouped.items():
            singular, plural = KIND_NOUNS.get(kind, (kind, kind))
            parts.append(f"{len(items)} {singular if len(items) == 1 else plural}")
        return ("found", " · ".join(parts[:4])) if parts else ("empty", "nothing on hand")
    if result.get("not_spelled_exactly"):
        instead = result.get("now_playing") or (result.get("queued") or [""])[0]
        return "found", (f"no exact {', '.join(result['not_spelled_exactly'])}"
                         + (f" · closest: {instead}" if instead else ""))[:100]
    if result.get("now_playing"):
        return "found", f"playing {result['now_playing']}"
    if result.get("queued"):
        return "found", f"queued {len(result['queued'])} track{'s' if len(result['queued']) != 1 else ''}"
    items = result.get("items")
    if isinstance(items, list):
        return ("found", f"{len(items)} found") if items else ("empty", "nothing on hand")
    if status == "scheduled":
        return "done", "segment airs after the reply" if name in SEGMENT_TOOLS else "on it"
    return "done", "on it" if name in SEGMENT_TOOLS else "done"


def _target(target: Optional[str]) -> str:
    return BRACE_TARGETS.get(target or "", "earlier track")


def command_string(name: str, args: Dict[str, Any]) -> str:
    if name == "what_aired":
        if args.get("id"):
            span = args["id"]
        elif args.get("around_minutes_ago") is not None:
            span = f"around {args['around_minutes_ago']:g} min ago"
        elif args.get("to_minutes_ago"):
            span = f"{args['from_minutes_ago']:g} to {args['to_minutes_ago']:g} min ago"
        else:
            span = f"last {args['from_minutes_ago']:g} min"
        return _brace("what_aired", *(args.get("kinds") or []), value=span)
    if name == "request_tools":
        return _brace("request_tools", *(args.get("names") or []), value=args.get("reason"))
    if name == "pulse_search":
        return _brace("pulse_search", *(args.get("kinds") or []), args.get("when") or "",
                      args["within"] if args.get("within") not in (None, "catalog") else "",
                      "mine" if args.get("mine") else "",
                      BRACE_TARGETS[args["about_track"]] if args.get("about_track") else "", value=args.get("query"))
    if name == "pulse_detail":
        return _brace("pulse_detail", value=args["item_id"])
    if name in ("listener_context", "city_trends"):
        return _brace(name, value=args.get("topic"))
    if name == "search_and_play":
        if args.get("track_id"):
            return _brace("play" if args["mode"] == "play" else "cue", "track", value=args["track_id"])
        return _brace("play" if args["mode"] == "play" else "cue", args.get("category") or "",
                      args["within"] if args.get("within") != "catalog" else "", args.get("vocals") or "",
                      value=args.get("query"))
    if name == "find_tracks":
        return _brace("find", args.get("category") or "", args["within"] if args.get("within") != "catalog" else "",
                      args.get("vocals") or "", f"starts with {args['starts_with']}" if args.get("starts_with") else "",
                      value=args.get("query"))
    if name == "playback_control":
        action = args["action"]
        if action == "seek":
            return _brace("seek", value=f"{args['position_s']:g}s")
        if action == "remove":
            return _brace("remove", value=args.get("title") or "next")
        return _brace(action)
    if name == "seed_radio":
        value = " + ".join(f"{item['category']} {item['weight']:g}" + (f" '{item['words']}'" if item["words"] else "")
                           for item in args["blend"]) if args["blend"] else args["category"]
        return _brace("play", "seed", _target(args["target"]) if args["target"] != "current" else "", value=value)
    if name == "move_playback":
        return _brace("move_playback", value=args.get("device") or "list devices")
    if name == "radio_settings":
        return _brace("radio_settings", value=", ".join(f"{key}={value}" for key, value in args.items()) or "read")
    if name == "play_playlist":
        return _brace("play", "playlist", value=args["name"])
    if name == "rate_track":
        return _brace(args["rating"], _target(args["target"]), value=args.get("shoutout_id"))
    if name == "get_news":
        scope = {"national": "national", "local": "local"}.get(args["scope"], "")
        depth = args.get("depth") if args.get("depth") != DEFAULT_DEPTH else ""
        return _brace("news", scope, args.get("category") or "", depth or "", value=args.get("query"))
    if name == "get_weather":
        return _brace("weather", *({"week": "this_week"}.get(period, period) for period in args["when"]
                                   if period != "current"))
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
        return _brace("lyrics", _target(args["target"]))
    if name == "play_shoutouts":
        return _brace("play_shoutouts", value=args.get("query"))
    if name == "save_shoutout":
        return _brace("save_shoutout")
    if name == "save_shoutout_reply":
        return _brace("save_shoutout_reply", value=args.get("parent_id") or "just played")
    if name == "save_review":
        return _brace("save_review", _target(args["target"]))
    return _brace(name)
