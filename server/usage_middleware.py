from typing import Optional
from urllib.parse import parse_qs

from services import usage_tracking
from security_middleware import is_valid_guest_id


def _user_id_from_token(token: Optional[str]) -> Optional[int]:
    if not token:
        return None
    from services import auth_service
    try:
        payload = auth_service.decode_token(token)
    except Exception:
        return None
    sub = (payload or {}).get("sub")
    try:
        return int(sub) if sub is not None else None
    except (TypeError, ValueError):
        return None


def subject_from_scope(scope) -> usage_tracking.UsageSubject:
    token = None
    guest_id = None
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            raw = value.decode("latin-1")
            if raw.startswith("Bearer "):
                token = raw[7:].strip()
        elif name == b"x-guest-id":
            guest_id = value.decode("latin-1")
    query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
    token = token or (query.get("token") or [None])[0]
    guest_id = guest_id or (query.get("guest_id") or [None])[0]

    user_id = _user_id_from_token(token)
    if user_id is not None:
        return usage_tracking.user_subject(user_id)
    if guest_id and is_valid_guest_id(guest_id):
        return usage_tracking.UsageSubject(usage_tracking.KIND_GUEST, None, guest_id)
    return usage_tracking.system_subject("anonymous")


class UsageAttributionMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        token = usage_tracking.bind(subject_from_scope(scope))
        try:
            return await self.app(scope, receive, send)
        finally:
            usage_tracking.unbind(token)
