from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db, User
from routers.deps import get_current_user, require_admin
from services import listener_limits, log_service, usage_report_service as reports

router = APIRouter()

PERIOD_PATTERN = "^(day|week|month|custom)$"


def is_admin_user(user: Optional[User]) -> bool:
    return user is not None and int(user.id) in settings.ADMIN_USER_IDS  # type: ignore


def usage_stats_visible(user: Optional[User]) -> bool:
    return is_admin_user(user) or (user is not None and settings.USAGE_STATS_VISIBILITY == "all")


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
    subject = reports.usage_tracking.user_subject(int(user.id))  # type: ignore
    return await reports.build_subject_detail(await db.connection(), subject, period)


async def _limits_view(db: AsyncSession) -> dict:
    price = settings.SUBSCRIPTION_PRICE_USD
    return {
        "limits": listener_limits.limits(),
        "defaults": listener_limits.validate({}),
        "tiers": listener_limits.TIERS,
        "chat_cost_usd": await reports.chat_cost(await db.connection()),
        "song_cost_usd": settings.SUNO_COST_PER_GENERATION_USD,
        "premium_price_usd": price,
        "premium_fee_usd": round(price * settings.STRIPE_FEE_PERCENT / 100 + settings.STRIPE_FEE_FIXED_USD, 2),
    }


@router.get("/api/admin/limits")
async def get_limits(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await _limits_view(db)


@router.put("/api/admin/limits")
async def save_limits(limits: dict = Body(...), admin: User = Depends(require_admin),
                      db: AsyncSession = Depends(get_db)):
    try:
        listener_limits.store.save(limits)
    except (ValueError, TypeError, KeyError) as e:
        _bad_request(ValueError(str(e)))
    log_service.system(f"[LIMITS] {admin.username} saved the listener limits: " + ", ".join(
        f"{row['label']} ${row['ai_usd_per_day']}/day ${row['ai_usd_per_month']}/month {row['songs']} songs"
        for row in listener_limits.limits()["tiers"].values()))
    return await _limits_view(db)
