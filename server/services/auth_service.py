from datetime import datetime, timedelta, timezone
from typing import Optional
from functools import lru_cache
from jose import JWTError, jwt
import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database import User
from config import settings
from services import log_service
from services.user_data_cache_service import user_data_cache

MAX_PASSWORD_BYTES = 72

def validate_password_length(password: str) -> tuple[bool, Optional[str]]:
    password_bytes = password.encode('utf-8')

    if len(password_bytes) > MAX_PASSWORD_BYTES:
        return False, f"Password is too long (max {MAX_PASSWORD_BYTES} bytes)"

    if len(password) < 6:
        return False, "Password must be at least 6 characters"

    if len(password) > 128:
        return False, "Password must be less than 128 characters"

    return True, None

def hash_password(password: str) -> str:
    is_valid, error_msg = validate_password_length(password)
    if not is_valid:
        raise ValueError(error_msg)

    password_bytes = password.encode('utf-8')[:MAX_PASSWORD_BYTES]

    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)

    return hashed.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        password_bytes = plain_password.encode('utf-8')[:MAX_PASSWORD_BYTES]
        hashed_bytes = hashed_password.encode('utf-8')

        return bcrypt.checkpw(password_bytes, hashed_bytes)
    except Exception as e:
        log_service.warning(f"Password verification failed: {str(e)}")
        return False

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.JWT_ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt

@lru_cache(maxsize=1024)
def _decode_token_cached(token: str) -> Optional[tuple]:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload.get("sub"), payload.get("exp")
    except JWTError:
        return None

def decode_token(token: str) -> Optional[dict]:
    cached = _decode_token_cached(token)
    if cached is None:
        return None

    sub, exp = cached
    if exp and datetime.now(timezone.utc).timestamp() > exp:
        _decode_token_cached.cache_clear()
        return None

    return {"sub": sub, "exp": exp}

async def register_user(db: AsyncSession, username: str, password: str) -> Optional[User]:
    if not username or len(username.strip()) < 3:
        log_service.warning("Registration failed: Username too short")
        raise ValueError("Username must be at least 3 characters")

    if len(username) > 50:
        log_service.warning("Registration failed: Username too long")
        raise ValueError("Username must be less than 50 characters")

    result = await db.execute(select(User).where(User.username == username))
    existing_user = result.scalar_one_or_none()
    if existing_user:
        log_service.warning(f"Registration failed: Username '{username}' already exists")
        return None

    hashed_password = hash_password(password)
    new_user = User(username=username, password_hash=hashed_password)
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    log_service.success(f"New user registered: {username} (ID: {new_user.id})")
    return new_user

async def authenticate_user(db: AsyncSession, username: str, password: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        log_service.warning(f"Login failed: User '{username}' not found")
        return None
    if not verify_password(password, user.password_hash):
        log_service.warning(f"Login failed: Invalid password for user '{username}'")
        return None
    log_service.info(f"User authenticated: {username} (ID: {user.id})")
    return user

async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    cached_user = await user_data_cache.get_user(user_id)
    if cached_user:
        return await db.merge(cached_user)
    return None