from fastapi import HTTPException, Depends, Header, Request, UploadFile
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from services import auth_service, log_service
from services.rate_limit_service import rate_limit_service
from security_middleware import is_valid_guest_id
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

    result: dict = {
        "user": user,
        "session_id": session_id,
        "device_id": session_device_id,
        "device_name": session_device_name,
        "device_type": session_device_type,
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
    peer = _peer_ip(request)
    if peer not in settings.TRUSTED_PROXY_IPS:
        return peer
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[-1].strip() or None
    return None


def client_ip(request: Request) -> str:
    return real_client_ip(request) or _peer_ip(request) or "unknown"


def request_identity(request: Request, user: Optional[User]) -> str:
    if user is not None:
        return f"user:{int(user.id)}"  # type: ignore
    guest = request.headers.get("x-guest-id") or request.query_params.get("guest_id")
    if guest and is_valid_guest_id(guest):
        return f"guest:{guest}"
    return f"ip:{client_ip(request)}"


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


class RateLimit:
    def __init__(self, name: str, user_limit: Optional[tuple] = None, guest_limit: Optional[tuple] = None):
        self.name = name
        self.user_limit = user_limit
        self.guest_limit = guest_limit

    async def __call__(
            self,
            request: Request,
            user: Optional[User] = Depends(get_current_user)
    ) -> Optional[User]:
        identity = request_identity(request, user)
        if user is not None:
            enforce_rate_limit(f"{self.name}_user", identity, self.user_limit)
            return user

        enforce_rate_limit(f"{self.name}_guest", identity, self.guest_limit)
        ip_bucket = f"{self.name}_guest_ip"
        ip = real_client_ip(request)
        if ip and not identity.startswith("ip:"):
            enforce_rate_limit(ip_bucket, ip)
        return None


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
