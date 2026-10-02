import json
import secrets
import time
from typing import Any, Optional
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from config import settings
from database import User
from database.models import Passkey, utc_now
from services import log_service

RP_NAME = "PLAiR"
CHALLENGE_TTL_S = 300
MAX_PENDING = 2000

_pending: dict[str, dict[str, Any]] = {}


class PasskeyError(ValueError):
    pass


def relying_party(origin: Optional[str]) -> tuple[str, str]:
    parsed = urlparse(origin or settings.PUBLIC_BASE_URL)
    host = (parsed.hostname or "").lower()
    rp_id = next((h for h in settings.PASSKEY_RP_HOSTS if host == h or host.endswith(f".{h}")), None)
    if not rp_id:
        raise PasskeyError("Passkeys aren't available on this address")
    return rp_id, f"{parsed.scheme}://{parsed.netloc}"


def _remember(kind: str, challenge: bytes, rp_id: str, origin: str, **data: Any) -> str:
    now = time.monotonic()
    for key in [k for k, v in _pending.items() if v["expires"] < now]:
        _pending.pop(key, None)
    if len(_pending) >= MAX_PENDING:
        _pending.pop(next(iter(_pending)))
    key = secrets.token_urlsafe(18)
    _pending[key] = {"kind": kind, "challenge": challenge, "rp_id": rp_id, "origin": origin,
                     "expires": now + CHALLENGE_TTL_S, **data}
    return key


def _take(key: str, kind: str) -> dict[str, Any]:
    entry = _pending.pop(key or "", None)
    if not entry or entry["kind"] != kind or entry["expires"] < time.monotonic():
        raise PasskeyError("That sign-in request expired. Please try again.")
    return entry


def _creation_options(rp_id: str, user_handle: bytes, username: str, exclude: list[str]) -> Any:
    return generate_registration_options(
        rp_id=rp_id,
        rp_name=RP_NAME,
        user_id=user_handle,
        user_name=username,
        user_display_name=username,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c)) for c in exclude],
    )


def _user_handle(user_id: int) -> bytes:
    return f"plair-user-{user_id}".encode()


def _device_name(user_agent: str) -> str:
    ua = user_agent or ""
    for needle, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"),
                         ("Macintosh", "Mac"), ("CrOS", "Chromebook"), ("Linux", "Linux")):
        if needle in ua:
            return name
    return "Passkey"


async def username_taken(db: AsyncSession, username: str) -> bool:
    result = await db.execute(select(User.id).where(User.username == username))
    return result.scalar_one_or_none() is not None


def validate_username(username: str) -> str:
    name = " ".join((username or "").split())
    if len(name) < 3:
        raise PasskeyError("Username must be at least 3 characters")
    if len(name) > 50:
        raise PasskeyError("Username must be less than 50 characters")
    return name


async def signup_options(db: AsyncSession, username: str, origin: Optional[str]) -> dict:
    name = validate_username(username)
    if await username_taken(db, name):
        raise PasskeyError("That username is taken")
    rp_id, expected_origin = relying_party(origin)
    handle = secrets.token_bytes(16)
    options = _creation_options(rp_id, handle, name, [])
    key = _remember("signup", options.challenge, rp_id, expected_origin, username=name)
    return {"request_id": key, "options": json.loads(options_to_json(options))}


async def add_options(db: AsyncSession, user: User, origin: Optional[str], auto: bool = False) -> dict:
    rp_id, expected_origin = relying_party(origin)
    existing = (await db.execute(select(Passkey.credential_id).where(Passkey.user_id == user.id))).scalars().all()
    options = _creation_options(rp_id, _user_handle(int(user.id)), str(user.username), list(existing))  # type: ignore
    key = _remember("add", options.challenge, rp_id, expected_origin, user_id=int(user.id), auto=auto)  # type: ignore
    return {"request_id": key, "options": json.loads(options_to_json(options))}


def _verified_registration(entry: dict, credential: dict) -> Any:
    try:
        return verify_registration_response(
            credential=credential,
            expected_challenge=entry["challenge"],
            expected_rp_id=entry["rp_id"],
            expected_origin=entry["origin"],
            require_user_presence=not entry.get("auto"),
        )
    except Exception as exc:
        log_service.warning(f"Passkey registration rejected: {exc}")
        raise PasskeyError("That passkey couldn't be saved. Please try again.")


def _store(db: AsyncSession, user_id: int, verified: Any, credential: dict, user_agent: str) -> Passkey:
    transports = (credential.get("response") or {}).get("transports") or []
    passkey = Passkey(
        user_id=user_id,
        credential_id=bytes_to_base64url(verified.credential_id),
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        transports=json.dumps(transports),
        name=_device_name(user_agent),
    )
    db.add(passkey)
    return passkey


async def finish_signup(db: AsyncSession, request_id: str, credential: dict, user_agent: str) -> User:
    entry = _take(request_id, "signup")
    verified = _verified_registration(entry, credential)
    if await username_taken(db, entry["username"]):
        raise PasskeyError("That username is taken")
    user = User(username=entry["username"], password_hash=None)
    db.add(user)
    await db.flush()
    _store(db, int(user.id), verified, credential, user_agent)  # type: ignore
    await db.commit()
    await db.refresh(user)
    log_service.success(f"New user registered with a passkey: {user.username} (ID: {user.id})")
    return user


async def finish_add(db: AsyncSession, user: User, request_id: str, credential: dict, user_agent: str) -> dict:
    entry = _take(request_id, "add")
    if entry["user_id"] != int(user.id):  # type: ignore
        raise PasskeyError("That request belongs to another account")
    verified = _verified_registration(entry, credential)
    passkey = _store(db, int(user.id), verified, credential, user_agent)  # type: ignore
    await db.commit()
    await db.refresh(passkey)
    return describe(passkey)


def login_options(origin: Optional[str]) -> dict:
    rp_id, expected_origin = relying_party(origin)
    options = generate_authentication_options(rp_id=rp_id, user_verification=UserVerificationRequirement.PREFERRED)
    key = _remember("login", options.challenge, rp_id, expected_origin)
    return {"request_id": key, "options": json.loads(options_to_json(options))}


async def finish_login(db: AsyncSession, request_id: str, credential: dict) -> User:
    entry = _take(request_id, "login")
    credential_id = str(credential.get("id") or "")
    passkey = (await db.execute(select(Passkey).where(Passkey.credential_id == credential_id))).scalar_one_or_none()
    if not passkey:
        raise PasskeyError("This passkey isn't linked to a PLAiR account any more")
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=entry["challenge"],
            expected_rp_id=entry["rp_id"],
            expected_origin=entry["origin"],
            credential_public_key=passkey.public_key,  # type: ignore
            credential_current_sign_count=int(passkey.sign_count or 0),  # type: ignore
        )
    except Exception as exc:
        log_service.warning(f"Passkey sign-in rejected: {exc}")
        raise PasskeyError("That passkey didn't work. Please try again.")
    passkey.sign_count = verified.new_sign_count  # type: ignore
    passkey.last_used_at = utc_now()  # type: ignore
    user = await db.get(User, passkey.user_id)
    if not user:
        raise PasskeyError("This passkey isn't linked to a PLAiR account any more")
    await db.commit()
    return user


def describe(passkey: Passkey) -> dict:
    return {
        "id": passkey.id,
        "name": passkey.name or "Passkey",
        "created_at": passkey.created_at.isoformat() if passkey.created_at else None,  # type: ignore
        "last_used_at": passkey.last_used_at.isoformat() if passkey.last_used_at else None,  # type: ignore
    }


async def count_for(db: AsyncSession, user_id: int) -> int:
    rows = await db.execute(select(Passkey.id).where(Passkey.user_id == user_id))
    return len(rows.all())


async def list_for(db: AsyncSession, user_id: int) -> list[dict]:
    rows = (await db.execute(select(Passkey).where(Passkey.user_id == user_id).order_by(Passkey.created_at))).scalars().all()
    return [describe(p) for p in rows]


async def remove(db: AsyncSession, user: User, passkey_id: int) -> None:
    passkey = await db.get(Passkey, passkey_id)
    if not passkey or passkey.user_id != user.id:
        raise PasskeyError("Passkey not found")
    others = (await db.execute(select(Passkey.id).where(Passkey.user_id == user.id, Passkey.id != passkey_id))).first()
    if not others and not user.password_hash:
        raise PasskeyError("This is your only way to sign in. Add another passkey or a password first.")
    await db.delete(passkey)
    await db.commit()
