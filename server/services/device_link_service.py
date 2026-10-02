import secrets
import time
from typing import Any, Optional

from config import settings

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6
MAX_PENDING = 2000

_links: dict[str, dict[str, Any]] = {}


class DeviceLinkError(ValueError):
    pass


def normalize_code(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch in CODE_ALPHABET)


def _sweep() -> None:
    now = time.monotonic()
    for code in [c for c, v in _links.items() if v["expires"] < now]:
        _links.pop(code, None)
    while len(_links) >= MAX_PENDING:
        _links.pop(next(iter(_links)))


def device_label(user_agent: str) -> str:
    ua = user_agent or ""
    system = next((name for needle, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android phone"),
                                             ("Windows", "Windows PC"), ("Macintosh", "Mac"), ("CrOS", "Chromebook"),
                                             ("Linux", "Linux computer")) if needle in ua), "device")
    browser = next((name for needle, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
                                              ("CriOS", "Chrome"), ("Chrome/", "Chrome"), ("Safari/", "Safari"))
                    if needle in ua), None)
    return f"{browser} on {system}" if browser else system


def start(user_agent: str) -> dict:
    _sweep()
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    while code in _links:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    poll_key = secrets.token_urlsafe(24)
    _links[code] = {"poll_key": poll_key, "device": device_label(user_agent), "user_id": None,
                    "expires": time.monotonic() + settings.DEVICE_LINK_TTL_S}
    return {"code": code, "poll_key": poll_key, "expires_in": settings.DEVICE_LINK_TTL_S}


def _live(code: str) -> Optional[dict]:
    entry = _links.get(normalize_code(code))
    if not entry or entry["expires"] < time.monotonic():
        return None
    return entry


def describe(code: str) -> dict:
    entry = _live(code)
    if not entry or entry["user_id"] is not None:
        raise DeviceLinkError("That code has expired. Show a new one on the other device.")
    return {"code": normalize_code(code), "device": entry["device"]}


def approve(code: str, user_id: int) -> dict:
    entry = _live(code)
    if not entry or entry["user_id"] is not None:
        raise DeviceLinkError("That code has expired. Show a new one on the other device.")
    entry["user_id"] = user_id
    return {"device": entry["device"]}


def poll(code: str, poll_key: str) -> Optional[int]:
    norm = normalize_code(code)
    entry = _live(norm)
    if not entry or not secrets.compare_digest(entry["poll_key"], poll_key or ""):
        raise DeviceLinkError("expired")
    if entry["user_id"] is None:
        return None
    _links.pop(norm, None)
    return int(entry["user_id"])
