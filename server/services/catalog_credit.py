import re
from typing import Any, Dict, Optional, Tuple

NAME_MAX_CHARS = 60
SOURCE_DEPTH = 5
CONTAINED_NAME_MIN_CHARS = 4
NON_WORD = re.compile(r"[^a-z0-9]+")


def is_ai_track(track: Dict[str, Any]) -> bool:
    return track.get("is_ai_generated") is not False


def name_like(text: Any) -> bool:
    text = text.strip() if isinstance(text, str) else ""
    return bool(text) and len(text) <= NAME_MAX_CHARS and "\n" not in text and "[" not in text


def known_artists(tracks: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    names: Dict[str, str] = {}
    for track in tracks.values():
        params, derived = track.get("generation_params") or {}, track.get("derived_tags") or {}
        for name in [params.get("artist_name"), (track.get("track_info") or {}).get("artist"),
                     derived.get("inspired_artist")] + list(derived.get("similar_artists") or []):
            if name_like(name):
                names.setdefault(name.strip().lower(), name.strip())
    return names


def artist_in_request(request: str, known: Dict[str, str]) -> Optional[str]:
    text = request.strip().lower().rstrip(".!")
    if text in known:
        return known[text]
    words = f" {NON_WORD.sub(' ', text)} "
    found = [name for key, name in known.items()
             if len(key) >= CONTAINED_NAME_MIN_CHARS and f" {NON_WORD.sub(' ', key).strip()} " in words]
    return max(found, key=len) if found else None


def ai_artist(track: Dict[str, Any], tracks: Optional[Dict[str, Dict[str, Any]]] = None,
              known: Optional[Dict[str, str]] = None, depth: int = 0) -> Tuple[Optional[str], str]:
    params = track.get("generation_params") or {}
    derived = track.get("derived_tags") or {}
    for value, source in ((params.get("artist_name"), "artist name"),
                          ((track.get("track_info") or {}).get("artist"), "track info"),
                          (derived.get("inspired_artist"), "inspired artist")):
        if name_like(value):
            return value.strip(), source
    request = track.get("user_request")
    request = request.get("original_text") if isinstance(request, dict) else None
    if known is not None and name_like(request):
        artist = artist_in_request(request, known)
        if artist:
            return artist, "original request"
    parent = (tracks or {}).get(track.get("generated_from") or "")
    if parent is not None and depth < SOURCE_DEPTH:
        artist, _ = ai_artist(parent, tracks, known, depth + 1)
        if artist:
            return artist, "source track"
    similar = [name for name in derived.get("similar_artists") or [] if name_like(name)]
    if similar:
        return similar[0].strip(), "first similar artist"
    return None, "nothing to go on"


def credit_settled(track: Dict[str, Any]) -> bool:
    names = (((track.get("generation_params") or {}).get("artist_name")),
             ((track.get("track_info") or {}).get("artist")),
             ((track.get("derived_tags") or {}).get("inspired_artist")))
    return name_like(names[0]) and all(isinstance(n, str) and n.strip() == names[0].strip() for n in names)


def settle_ai_credit(track: Dict[str, Any], tracks: Optional[Dict[str, Dict[str, Any]]] = None,
                     known: Optional[Dict[str, str]] = None) -> Tuple[Optional[str], str, bool]:
    if not is_ai_track(track) or credit_settled(track):
        return (track.get("generation_params") or {}).get("artist_name"), "settled", False
    artist, source = ai_artist(track, tracks, known)
    if not artist:
        return None, source, False
    track.setdefault("generation_params", {})["artist_name"] = artist
    track.setdefault("track_info", {})["artist"] = artist
    track.setdefault("derived_tags", {})["inspired_artist"] = artist
    return artist, source, True


def search_artist_text(track: Dict[str, Any]) -> str:
    params, derived = track.get("generation_params") or {}, track.get("derived_tags") or {}
    credit = (params.get("artist_name") or "").strip()
    if not is_ai_track(track):
        return credit
    inspired = (derived.get("inspired_artist") or "").strip()
    names = [name for name in (credit, inspired) if name]
    return ", ".join(dict.fromkeys(names))
