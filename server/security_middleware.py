import json
import logging
import re
from urllib.parse import parse_qs

GUEST_ID_PATTERN = re.compile(r"^guest_[0-9A-Fa-f-]{8,64}$")
FORBIDDEN_PATH_PARTS = ("\\", "..", "\x00", ":")
MULTIPART_OVERHEAD_BYTES = 1024 * 1024


def is_valid_guest_id(guest_id: str) -> bool:
    return bool(GUEST_ID_PATTERN.match(guest_id))


SENSITIVE_QUERY_PATTERN = re.compile(r"((?:token|access_token|apikey|api_key|key)=)[^&\s\"']+", re.IGNORECASE)


class RedactSecretsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = SENSITIVE_QUERY_PATTERN.sub(r"\1[redacted]", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def install_log_redaction():
    redact = RedactSecretsFilter()
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactSecretsFilter) for f in logger.filters):
            logger.addFilter(redact)
        for handler in logger.handlers:
            if not any(isinstance(f, RedactSecretsFilter) for f in handler.filters):
                handler.addFilter(redact)


def _default_body_limits():
    from config import settings

    upload_cap = max(settings.UPLOAD_MAX_AUDIO_BYTES, settings.UPLOAD_MAX_VIDEO_BYTES)
    return [
        (re.compile(r"^/api/user/music/upload/?$"), upload_cap + MULTIPART_OVERHEAD_BYTES),
        (re.compile(r"^/api/user/music/tracks/[^/]+/artwork/?$"), settings.MAX_ARTWORK_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES),
        (re.compile(r"^/api/user/profile-picture/?$"), settings.MAX_PROFILE_PICTURE_BYTES + MULTIPART_OVERHEAD_BYTES),
        (re.compile(r"^/api/transcribe/?$"), settings.MAX_TRANSCRIBE_AUDIO_BYTES + MULTIPART_OVERHEAD_BYTES),
        (re.compile(r"^/api/share/video/?$"), settings.MAX_SHARE_VIDEO_BYTES + MULTIPART_OVERHEAD_BYTES),
        (re.compile(r"^/api/user_content/shoutouts/[^/]+/reply/upload/?$"), settings.MAX_BASE64_AUDIO_CHARS + MULTIPART_OVERHEAD_BYTES),
    ]


class RequestGuardMiddleware:
    def __init__(self, app, body_limits=None):
        self.app = app
        self._body_limits = body_limits

    @property
    def body_limits(self):
        if self._body_limits is None:
            self._body_limits = _default_body_limits()
        return self._body_limits

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        reason = self._rejection_reason(scope)
        if reason is not None:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return

            status = 400 if reason == "path" else 403
            await self._send_json(send, status, "Invalid request" if reason == "path" else "Invalid guest id")
            return

        if scope["type"] == "http":
            limit = self._body_limit_for(scope)
            if limit is not None:
                return await self._call_with_body_limit(scope, receive, send, limit)

        return await self.app(scope, receive, send)

    @staticmethod
    async def _send_json(send, status: int, detail: str):
        body = json.dumps({"detail": detail}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    def _body_limit_for(self, scope):
        if scope.get("method", "GET").upper() not in ("POST", "PUT", "PATCH"):
            return None
        path = scope.get("path", "")
        for pattern, limit in self.body_limits:
            if pattern.match(path):
                return limit
        return None

    @staticmethod
    def _too_large_detail(limit: int) -> str:
        limit_mb = max(1, (limit - MULTIPART_OVERHEAD_BYTES) // (1024 * 1024))
        if limit_mb >= 1024:
            return f"Request body too large (max {limit_mb / 1024:.0f}GB)"
        return f"Request body too large (max {limit_mb}MB)"

    async def _call_with_body_limit(self, scope, receive, send, limit: int):
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value.decode("latin-1"))
                except ValueError:
                    await self._send_json(send, 400, "Invalid Content-Length")
                    return
                if declared > limit:
                    await self._send_json(send, 413, self._too_large_detail(limit))
                    return

        received = 0
        exceeded = False
        response_started = False

        async def limited_receive():
            nonlocal received, exceeded
            if exceeded:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            nonlocal response_started
            if exceeded and not response_started:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not exceeded:
                raise

        if exceeded and not response_started:
            await self._send_json(send, 413, self._too_large_detail(limit))

    @staticmethod
    def _rejection_reason(scope):
        path = scope.get("path", "")
        if any(part in path for part in FORBIDDEN_PATH_PARTS):
            return "path"

        guest_ids = []
        for name, value in scope.get("headers", []):
            if name == b"x-guest-id":
                guest_ids.append(value.decode("latin-1"))
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        guest_ids.extend(query.get("guest_id", []))

        if any(gid and not is_valid_guest_id(gid) for gid in guest_ids):
            return "guest"
        return None
