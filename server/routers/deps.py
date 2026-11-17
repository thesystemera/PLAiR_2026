from fastapi import HTTPException, Depends, Header, Request, UploadFile
from services.device_settings_service import device_kinds, valid_kind
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from services import auth_service, listener_limits, log_service
from services.rate_limit_service import rate_limit_service
from security_middleware import is_valid_guest_id, resolve_client_ip
from database import get_db, User
from config import settings


def _token_user_id(authorization: Optional[str], token: Optional[str]) -> Optional[int]:
    jwt_token: Optional[str] = None
    if authorization and authorization.startswith("Bearer "):
        jwt_token = authorization.replace("Bearer ", "")
    elif token:
        jwt_token = token

    if not jwt_token:
        return None

    payload = auth_service.decode_token(jwt_token)
    if not payload:
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None
    return int(user_id)


async def get_session_info(
        x_guest_id: Optional[str] = Header(None),
        x_device_id: Optional[str] = Header(None),
        x_device_name: Optional[str] = Header(None),
        x_device_type: Optional[str] = Header(None),
        x_device_kind: Optional[str] = Header(None),
        authorization: Optional[str] = Header(None),
        token: Optional[str] = None,
        guest_id: Optional[str] = None,
        device_id: Optional[str] = None,
        db: AsyncSession = Depends(get_db)
) -> dict:
    session_guest_id = x_guest_id or guest_id
    if session_guest_id and not is_valid_guest_id(session_guest_id):
        raise HTTPException(status_code=403, detail="Invalid guest id")
    session_device_id = x_device_id or device_id
    session_device_name = x_device_name or "Unknown Device"
    session_device_type = x_device_type or "desktop"

    user: Optional[User] = None
    user_id = _token_user_id(authorization, token)
    if user_id is not None:
        user = await auth_service.get_user_by_id(db, user_id)

    session_id = str(user.id) if user else session_guest_id

    if not session_id:
        raise HTTPException(
            status_code=400,
            detail="Missing session headers. Please include X-Guest-ID, X-Device-ID headers."
        )

    if not session_device_id:
        raise HTTPException(
            status_code=400,
            detail="Missing device ID. Please include X-Device-ID header."
        )

    device_kinds.note(session_id, session_device_id, x_device_kind)

    result: dict = {
        "user": user,
        "session_id": session_id,
        "device_id": session_device_id,
        "device_name": session_device_name,
        "device_type": session_device_type,
        "device_kind": valid_kind(x_device_kind),
        "is_authenticated": user is not None
    }
    return result

async def get_current_user(
        authorization: Optional[str] = Header(None),
        token: Optional[str] = None,
        db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    user_id = _token_user_id(authorization, token)
    if user_id is None:
        return None
    return await auth_service.get_user_by_id(db, user_id)


async def get_cached_current_user(
        authorization: Optional[str] = Header(None),
        token: Optional[str] = None
) -> Optional[User]:
    user_id = _token_user_id(authorization, token)
    if user_id is None:
        return None
    return await auth_service.get_cached_user(user_id)


def _peer_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


def real_client_ip(request: Request) -> Optional[str]:
    return resolve_client_ip(_peer_ip(request), request.headers.get("x-real-ip") or "",
                             request.headers.get("x-forwarded-for") or "")


def enforce_rate_limit(bucket: str, key: str, default: Optional[tuple] = None) -> None:
    spec = settings.RATE_LIMITS.get(bucket, default)
    if spec is None:
        return
    limit, window_s = spec
    allowed, retry_after = rate_limit_service.consume(bucket, key, limit, window_s)
    if not allowed:
        log_service.warning(f"[RateLimit] {bucket} exceeded for {key}")
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please slow down and try again later.",
            headers={"Retry-After": str(retry_after)}
        )


class AIAllowance:
    """The listener's AI allowance (listener_limits) for a route that starts AI work. With refuse, a listener who has
    used it up gets a polite 429 the app shows as a notice; without, the route decides (smart search falls back to
    the plain search)."""

    def __init__(self, refuse: bool = True):
        self.refuse = refuse

    async def __call__(self, request: Request,
                       user: Optional[User] = Depends(get_cached_current_user)) -> listener_limits.Allowance:
        guest = request.headers.get("x-guest-id") or request.query_params.get("guest_id")
        allowance = await listener_limits.allowance_for(user, guest if guest and is_valid_guest_id(guest) else None,
                                                        real_client_ip(request))
        if self.refuse and allowance.out:
            raise HTTPException(status_code=429, detail=listener_limits.notice_text(allowance),
                                headers={"Retry-After": str(allowance.resets_in_s), "X-Limit": "ai_allowance"})
        return allowance


def enforce_auth_rate_limit(request: Request, username: str) -> None:
    ip = real_client_ip(request)
    if ip:
        enforce_rate_limit("auth_ip", ip)
    enforce_rate_limit("auth_username", (username or "").strip().lower()[:64])


async def read_upload_limited(upload: UploadFile, max_bytes: int, chunk_size: int = 1024 * 1024) -> bytes:
    buffer = bytearray()
    while True:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File too large (max {max_bytes // (1024 * 1024)}MB)"
            )
    return bytes(buffer)


async def require_admin(user: Optional[User] = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if int(user.id) not in settings.ADMIN_USER_IDS:  # type: ignore
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
