import json
import re
from typing import Any, Dict, Optional

from config import settings
from config.settings import BASE_DIR

SCHEMA: Dict[str, Dict[str, Any]] = json.loads(
    (BASE_DIR / "client" / "src" / "lib" / "settingsSchema.json").read_text(encoding="utf-8")
)
DEFAULTS: Dict[str, Any] = {key: spec["default"] for key, spec in SCHEMA.items()}
LEGACY_KEY = "_legacy"
KIND_PATTERN = re.compile(r"^[a-z][a-z-]{1,23}$")


def valid_kind(kind: Optional[str]) -> Optional[str]:
    return kind if kind and KIND_PATTERN.match(kind) else None


def is_valid(key: str, value: Any) -> bool:
    spec = SCHEMA.get(key)
    if spec is None or type(value) is not type(spec["default"]):
        return False
    return "options" not in spec or value in spec["options"]


def pick_valid(values: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {key: value for key, value in (values or {}).items() if is_valid(key, value)}


def stored_for(user, kind: str) -> Optional[Dict[str, Any]]:
    saved = (getattr(user, "device_settings", None) or {}).get(kind)
    return pick_valid(saved) if isinstance(saved, dict) else None


def settings_for(user, kind: Optional[str]) -> Dict[str, Any]:
    stored = stored_for(user, kind) if kind else None
    if stored is None:
        stored = pick_valid((getattr(user, "device_settings", None) or {}).get(LEGACY_KEY))
    return {**DEFAULTS, **stored}


def with_changes(user, kind: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    all_kinds = dict(getattr(user, "device_settings", None) or {})
    all_kinds[kind] = {**settings_for(user, kind), **pick_valid(changes)}
    return all_kinds


class DeviceKinds:
    def __init__(self):
        self._kinds: Dict[tuple, str] = {}

    def note(self, session_id: Optional[str], device_id: Optional[str], kind: Optional[str]) -> None:
        kind = valid_kind(kind)
        if session_id and device_id and kind:
            self._kinds[(session_id, device_id)] = kind
            if len(self._kinds) > settings.DEVICE_KIND_MEMORY:
                self._kinds.pop(next(iter(self._kinds)))

    def kind_of(self, session_id: str, device_id: Optional[str]) -> Optional[str]:
        return self._kinds.get((session_id, device_id)) if device_id else None

    def playing_kind(self, session_id: str) -> Optional[str]:
        from service_registry import services
        playback = services.playback_service
        state = playback.sessions.get(session_id) if playback is not None else None
        return self.kind_of(session_id, getattr(state, "active_device_id", None))


device_kinds = DeviceKinds()


def playing_setting(user, session_id: str, key: str) -> Any:
    return settings_for(user, device_kinds.playing_kind(session_id))[key]
