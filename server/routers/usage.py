from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db, User
from routers.deps import get_current_user, require_admin, enforce_rate_limit
from services import usage_report_service as reports

router = APIRouter()

PERIOD_PATTERN = "^(day|week|month|custom)$"


def is_admin_user(user: Optional[User]) -> bool:
    return user is not None and int(user.id) in settings.ADMIN_USER_IDS  # type: ignore


def usage_stats_visible(user: Optional[User]) -> bool:
    return is_admin_user(user) or (user is not None and settings.USAGE_STATS_VISIBILITY == "all")


def _limit(user: User) -> None:
    enforce_rate_limit("usage_stats", f"user:{int(user.id)}")  # type: ignore


def _bad_request(err: ValueError):
    raise HTTPException(status_code=400, detail=str(err))


@router.get("/api/admin/usage/summary")
async def usage_summary(
        period: str = Query("month", pattern=PERIOD_PATTERN),
        start: Optional[str] = None,
        end: Optional[str] = None,
        admin: User = Depends(require_admin),
        db: AsyncSession = Depends(get_db)
):
    _limit(admin)
    try:
        return await reports.build_summary(await db.connection(), period, start, end)
    except ValueError as e:
        _bad_request(e)


@router.get("/api/admin/usage/users")
async def usage_users(
        period: str = Query("month", pattern=PERIOD_PATTERN),
        start: Optional[str] = None,
        end: Optional[str] = None,
        admin: User = Depends(require_admin),
        db: AsyncSession = Depends(get_db)
):
    _limit(admin)
    try:
        return await reports.build_users(await db.connection(), period, start, end)
    except ValueError as e:
        _bad_request(e)


@router.get("/api/admin/usage/user/{subject}")
async def usage_subject(
        subject: str,
        period: str = Query("month", pattern=PERIOD_PATTERN),
        start: Optional[str] = None,
        end: Optional[str] = None,
        admin: User = Depends(require_admin),
        db: AsyncSession = Depends(get_db)
):
    _limit(admin)
    try:
        parsed = reports.subject_from_param(subject)
        return await reports.build_subject_detail(await db.connection(), parsed, period, start, end)
    except ValueError as e:
        _bad_request(e)


@router.get("/api/usage/me")
async def usage_me(
        period: str = Query("month", pattern=PERIOD_PATTERN),
        user: Optional[User] = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not usage_stats_visible(user):
        raise HTTPException(status_code=403, detail="Usage stats are not available")
    _limit(user)
    subject = reports.usage_tracking.user_subject(int(user.id))  # type: ignore
    return await reports.build_subject_detail(await db.connection(), subject, period)
