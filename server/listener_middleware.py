import json
from typing import Optional
from urllib.parse import parse_qs

from services import listener_limits, usage_tracking
from security_middleware import client_ip_from_scope, is_valid_guest_id


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


def identity_of(subject: usage_tracking.UsageSubject, ip: Optional[str]) -> str:
    return subject.key if subject.kind != usage_tracking.KIND_SYSTEM else f"ip:{ip or 'unknown'}"


class ListenerMiddleware:
    """Every request and WebSocket handshake: who is asking (usage attribution), whether the AI work it causes counts
    toward their allowance (HTTP requests do), and the app-wide request limit (listener_limits)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        subject = subject_from_scope(scope)
        ip = client_ip_from_scope(scope)
        wait = listener_limits.allow_request(identity_of(subject, ip), ip,
                                             listener_limits.request_cost(scope.get("path", "")))
        if wait:
            await self._refuse(scope, send, wait)
            return
        subject_token = usage_tracking.bind(subject)
        client_token = usage_tracking.bind_client(ip, asked=scope["type"] == "http")
        try:
            return await self.app(scope, receive, send)
        finally:
            usage_tracking.unbind_client(client_token)
            usage_tracking.unbind(subject_token)

    @staticmethod
    async def _refuse(scope, send, wait: int):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1013})
            return
        body = json.dumps({"detail": listener_limits.REQUEST_REFUSAL}).encode()
        await send({"type": "http.response.start", "status": 429,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                (b"retry-after", str(wait).encode()), (b"x-limit", b"requests")]})
        await send({"type": "http.response.body", "body": body})
