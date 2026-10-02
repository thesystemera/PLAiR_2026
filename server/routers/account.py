import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from database import User, get_db
from routers.deps import enforce_rate_limit, get_current_user, real_client_ip
from routers.schemas import DeviceLinkPollRequest, PasskeyFinishRequest, PasskeySignupOptionsRequest, SetPasswordRequest
from services import account_deletion_service, auth_service, device_link_service, log_service, passkey_service
from services.user_data_cache_service import user_data_cache

router = APIRouter()

DEVICE_LINK_START_LIMIT = (10, 60)
DEVICE_LINK_POLL_LIMIT = (90, 60)


def _signed_in(user: User) -> dict:
    log_service.remember_user(user.id, user.username)
    return {
        "user": {"id": user.id, "username": user.username},
        "token": auth_service.create_access_token({"sub": str(user.id)}),
    }


def _require(user: User | None) -> User:
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def _limit_ip(request: Request, bucket: str = "auth_ip", default: tuple | None = None) -> None:
    ip = real_client_ip(request)
    if ip:
        enforce_rate_limit(bucket, ip, default)


@router.post("/api/auth/passkey/signup/options")
async def passkey_signup_options(body: PasskeySignupOptionsRequest, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        return await passkey_service.signup_options(db, body.username, request.headers.get("origin"))
    except passkey_service.PasskeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/auth/passkey/signup")
async def passkey_signup(body: PasskeyFinishRequest, request: Request, db: AsyncSession = Depends(get_db)):
    _limit_ip(request)
    try:
        user = await passkey_service.finish_signup(db, body.request_id, body.credential, request.headers.get("user-agent", ""))
    except passkey_service.PasskeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    log_service.listener(f"{log_service.who(user_id=user.id)}: created an account with a passkey")
    return _signed_in(user)


@router.post("/api/auth/passkey/login/options")
async def passkey_login_options(request: Request):
    try:
        return passkey_service.login_options(request.headers.get("origin"))
    except passkey_service.PasskeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/auth/passkey/login")
async def passkey_login(body: PasskeyFinishRequest, request: Request, db: AsyncSession = Depends(get_db)):
    _limit_ip(request)
    try:
        user = await passkey_service.finish_login(db, body.request_id, body.credential)
    except passkey_service.PasskeyError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    log_service.listener(f"{log_service.who(user_id=user.id)}: logged in with a passkey")
    return _signed_in(user)


@router.get("/api/auth/passkeys")
async def list_passkeys(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user = _require(current_user)
    return {"passkeys": await passkey_service.list_for(db, int(user.id))}  # type: ignore


@router.post("/api/auth/passkeys/options")
async def add_passkey_options(request: Request, auto: bool = False, current_user: User = Depends(get_current_user),
                              db: AsyncSession = Depends(get_db)):
    user = _require(current_user)
    try:
        return await passkey_service.add_options(db, user, request.headers.get("origin"), auto)
    except passkey_service.PasskeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/auth/passkeys")
async def add_passkey(body: PasskeyFinishRequest, request: Request, current_user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    user = _require(current_user)
    try:
        passkey = await passkey_service.finish_add(db, user, body.request_id, body.credential, request.headers.get("user-agent", ""))
    except passkey_service.PasskeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    log_service.listener(f"{log_service.who(user_id=user.id)}: added a passkey ({passkey['name']})")
    return passkey


@router.delete("/api/auth/passkeys/{passkey_id}")
async def delete_passkey(passkey_id: int, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user = _require(current_user)
    try:
        await passkey_service.remove(db, user, passkey_id)
    except passkey_service.PasskeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}


@router.put("/api/auth/password")
async def set_password(body: SetPasswordRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user = _require(current_user)
    try:
        hashed = await asyncio.to_thread(auth_service.hash_password, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db_user = await db.get(User, user.id)
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    db_user.password_hash = hashed  # type: ignore
    await db.commit()
    await user_data_cache.invalidate_user(int(user.id))  # type: ignore
    log_service.listener(f"{log_service.who(user_id=user.id)}: set a new password")
    return {"ok": True}


@router.post("/api/auth/link/start")
async def device_link_start(request: Request):
    _limit_ip(request, "device_link_start", DEVICE_LINK_START_LIMIT)
    return device_link_service.start(request.headers.get("user-agent", ""))


@router.post("/api/auth/link/poll")
async def device_link_poll(body: DeviceLinkPollRequest, request: Request, db: AsyncSession = Depends(get_db)):
    _limit_ip(request, "device_link_poll", DEVICE_LINK_POLL_LIMIT)
    try:
        user_id = device_link_service.poll(body.code, body.poll_key)
    except device_link_service.DeviceLinkError:
        return {"status": "expired"}
    if user_id is None:
        return {"status": "pending"}
    user = await db.get(User, user_id)
    if not user:
        return {"status": "expired"}
    log_service.listener(f"{log_service.who(user_id=user.id)}: signed in a new device by QR code")
    return {"status": "approved", **_signed_in(user)}


@router.get("/api/auth/link/{code}")
async def device_link_describe(code: str, current_user: User = Depends(get_current_user)):
    _require(current_user)
    try:
        return device_link_service.describe(code)
    except device_link_service.DeviceLinkError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/api/auth/link/{code}/approve")
async def device_link_approve(code: str, current_user: User = Depends(get_current_user)):
    user = _require(current_user)
    try:
        return device_link_service.approve(code, int(user.id))  # type: ignore
    except device_link_service.DeviceLinkError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/api/auth/account")
async def delete_account(current_user: User = Depends(get_current_user)):
    user = _require(current_user)
    user_id = int(user.id)  # type: ignore
    from routers.user_music import UPLOAD_JOBS
    for job in list(UPLOAD_JOBS.values()):
        task = job.get("task")
        if job.get("user_id") == user_id and task is not None and not task.done():
            task.cancel()
    try:
        summary = await account_deletion_service.delete_account(user_id)
    except account_deletion_service.AccountDeletionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True, **summary}
