from typing import Any, Dict, Optional

VOCALS = ("instrumental", "male", "female", "duet", "unknown")
FILTERABLE_VOCALS = ("instrumental", "male", "female", "duet")
GENDER_VOCALS = {"m": "male", "f": "female", "mixed": "duet", "duet": "duet"}
VOCALS_TEXT = {"instrumental": "instrumental, no vocals", "male": "male vocals", "female": "female vocals",
               "duet": "male and female vocals"}


def from_settings(instrumental: Any = None, vocal_gender: Any = None) -> Optional[str]:
    if instrumental is True:
        return "instrumental"
    return GENDER_VOCALS.get(str(vocal_gender or "").strip().lower())


def settled_vocals(track: Dict[str, Any]) -> Optional[str]:
    params = track.get("generation_params") or {}
    return from_settings(params.get("instrumental"), params.get("vocal_gender"))


def vocals_of(track: Dict[str, Any]) -> str:
    stored = (track.get("derived_tags") or {}).get("vocals")
    if stored in VOCALS:
        return stored
    return settled_vocals(track) or "unknown"
