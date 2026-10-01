import calendar
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select, func, and_

from config.settings import settings
from database.models import AIUsageDaily, User
from services import llm_telemetry, usage_tracking

MAX_CUSTOM_DAYS = 366
ACTIVE_SUBSCRIPTION_STATUSES = ("active",)

FEATURE_LABELS = {
    "DJPromptService.gpt_dj_interactive": "DJ reply",
    "DJPromptService.gpt_dj_interactive_tools": "DJ reply (tool mode)",
    "DJPromptService.gpt_dj_announcements": "Track announcements",
    "DJPromptService.gpt_news_interpretation": "News segment",
    "DJPromptService.gpt_weather_interpretation": "Weather segment",
    "DJPromptService.gpt_biography_interpretation": "Artist biography segment",
    "DJPromptService.gpt_lyrics_interpretation": "Lyrics segment",
    "DJPromptService.gpt_events_interpretation": "Events segment",
    "DJPromptService.gpt_location_search_interpretation": "Places segment",
    "DJPromptService.gpt_shoutouts_interpretation": "Shoutouts segment",
    "DJPromptSystemService.write_impulse_script": "Impulse scripts",
    "DJPromptSystemService.write_interlude_scripts": "Interlude scripts",
    "DJPromptSystemService.generate_paralanguage_gpt_response": "Reaction scripts",
    "DJPromptSystemService.generate_breath_gpt_response": "Breath scripts",
    "ContextRouterService.determine_nodes": "Context router",
    "CatalogVectorSearchPromptCacheService.analyze_query": "Music search intent",
    "UserContentVectorSearchPromptCacheService.analyze_query": "Shoutout search intent",
    "NewsService.get_top_news": "News ranking",
    "persona_service.generate_user_persona_and_profile": "Listener persona update",
    "UserContentSpeechEnhancementService.process_transcription_with_gpt": "Shoutout analysis",
    "MusicPromptService.generate_music_params": "Song prompt writing",
    "EnrichedMetadataService.enrich_metadata": "Song metadata enrichment",
    "HumanMetadataExtractionService.extract_metadata": "Upload audio analysis",
    "suno.generation": "Suno song generation",
    "tts.orpheus": "Orpheus TTS (GPU)",
    "tts.clip_cache.tts": "TTS clip cache",
    "tts.clip_cache.paralanguage": "Reaction clip cache",
    "tts.clip_cache.breath": "Breath clip cache",
    "whisper.fast": "Whisper fast (GPU)",
    "whisper.quality": "Whisper quality (GPU)",
    "api.weather": "Weather API",
    "api.news": "News feed",
    "api.events": "Ticketmaster events",
    "api.places": "Google Places",
    "api.biography": "MusicBrainz / Wikipedia",
}

SUM_FIELDS = ("calls", "cache_hits", "errors", "prompt_tokens", "cached_tokens", "output_tokens",
              "reasoning_tokens", "cost_usd", "saved_usd", "gpu_seconds", "audio_seconds", "units")


def _feature_category(category: str) -> str:
    return usage_tracking.CATEGORY_LLM if category == usage_tracking.CATEGORY_LLM_CACHE else category


def feature_label(feature: str) -> str:
    return FEATURE_LABELS.get(feature) or feature


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def resolve_period(period: str, start: Optional[str] = None, end: Optional[str] = None,
                   now: Optional[datetime] = None) -> tuple[date, date, str]:
    today = (now or utc_now()).astimezone(timezone.utc).date()
    period = (period or "month").lower()
    if period == "day":
        return today, today, "day"
    if period == "week":
        return today - timedelta(days=6), today, "week"
    if period == "custom":
        try:
            start_day = date.fromisoformat(start) if start else today.replace(day=1)
            end_day = date.fromisoformat(end) if end else today
        except ValueError as e:
            raise ValueError("Dates must be YYYY-MM-DD") from e
        if end_day < start_day:
            start_day, end_day = end_day, start_day
        end_day = min(end_day, today)
        if (end_day - start_day).days + 1 > MAX_CUSTOM_DAYS:
            start_day = end_day - timedelta(days=MAX_CUSTOM_DAYS - 1)
        return start_day, end_day, "custom"
    if period != "month":
        raise ValueError("period must be day, week, month or custom")
    return today.replace(day=1), today, "month"


def day_fraction(now: datetime) -> float:
    now = now.astimezone(timezone.utc)
    return max((now.hour * 3600 + now.minute * 60 + now.second) / 86400, 1 / 24)


def elapsed_days(start_day: date, end_day: date, now: datetime) -> float:
    today = now.astimezone(timezone.utc).date()
    full = (end_day - start_day).days + 1
    if end_day >= today:
        return max(full - 1 + day_fraction(now), day_fraction(now))
    return float(full)


def project_month(daily_costs: dict[date, float], now: datetime) -> dict[str, Any]:
    now = now.astimezone(timezone.utc)
    today = now.date()
    fraction = day_fraction(now)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    month_start = today.replace(day=1)
    month_days = {d: c for d, c in daily_costs.items() if month_start <= d <= today}
    mtd = sum(month_days.values())

    active_days = sum(1 for d, c in month_days.items() if c > 0 and d < today)
    if month_days.get(today, 0) > 0:
        active_days += fraction
    linear = mtd / active_days * days_in_month if active_days > 0 else 0.0

    window_start = today - timedelta(days=6)
    window_cost = sum(c for d, c in daily_costs.items() if window_start <= d <= today)
    rate_7d = window_cost / (6 + fraction)
    remaining_days = max(days_in_month - (today.day - 1) - fraction, 0.0)
    last_7d = mtd + rate_7d * remaining_days

    return {
        "month_to_date_usd": mtd,
        "days_in_month": days_in_month,
        "days_elapsed": (today.day - 1) + fraction,
        "active_days": active_days,
        "projected_linear_usd": linear,
        "daily_rate_7d_usd": rate_7d,
        "projected_7d_rate_usd": last_7d,
    }


def electricity_usd(gpu_seconds: float) -> float:
    return gpu_seconds / 3600 * settings.ELECTRICITY_COST_PER_GPU_HOUR_USD


def _empty_totals() -> dict[str, float]:
    return {name: 0 for name in SUM_FIELDS}


def _accumulate(target: dict, row: dict) -> None:
    for name in SUM_FIELDS:
        target[name] = target.get(name, 0) + (row.get(name) or 0)


def _finish(totals: dict) -> dict:
    out = dict(totals)
    out["cost_usd"] = float(out.get("cost_usd") or 0.0)
    out["electricity_usd"] = electricity_usd(float(out.get("gpu_seconds") or 0.0))
    out["total_usd"] = out["cost_usd"] + out["electricity_usd"]
    out["tokens"] = int(out.get("prompt_tokens") or 0) + int(out.get("output_tokens") or 0)
    return out


def _sums():
    daily = AIUsageDaily.__table__
    return [func.coalesce(func.sum(daily.c[name]), 0).label(name) for name in SUM_FIELDS]


def subject_from_param(subject: str) -> usage_tracking.UsageSubject:
    subject = (subject or "").strip()
    if subject.isdigit():
        return usage_tracking.user_subject(int(subject))
    if subject.startswith("system:"):
        return usage_tracking.system_subject(subject[7:] or "system")
    parsed = usage_tracking.subject_for_session(subject.removeprefix("guest:"))
    if parsed.kind != usage_tracking.KIND_GUEST:
        raise ValueError("Unknown subject")
    return parsed


async def _daily_costs(conn, start_day: date, end_day: date, subject_key: Optional[str] = None) -> dict[date, float]:
    daily = AIUsageDaily.__table__
    conditions = [daily.c.day >= start_day, daily.c.day <= end_day]
    if subject_key:
        conditions.append(daily.c.subject_key == subject_key)
    rows = (await conn.execute(
        select(daily.c.day, func.sum(daily.c.cost_usd), func.sum(daily.c.gpu_seconds))
        .where(and_(*conditions)).group_by(daily.c.day)
    )).all()
    return {row[0]: float(row[1] or 0.0) + electricity_usd(float(row[2] or 0.0)) for row in rows}


async def subscription_counts(conn) -> dict[str, int]:
    users = User.__table__
    active = (await conn.execute(
        select(func.count()).select_from(users).where(users.c.subscription_status.in_(ACTIVE_SUBSCRIPTION_STATUSES))
    )).scalar() or 0
    total = (await conn.execute(select(func.count()).select_from(users))).scalar() or 0
    return {"active_subscribers": int(active), "registered_users": int(total)}


def revenue_context(active_subscribers: int, projected_cost: float, projected_cost_7d: float,
                    active_users: int) -> dict[str, Any]:
    price = settings.SUBSCRIPTION_PRICE_USD
    fee = price * settings.STRIPE_FEE_PERCENT / 100 + settings.STRIPE_FEE_FIXED_USD
    net_per_sub = max(price - fee, 0.0)
    gross = active_subscribers * price
    net = active_subscribers * net_per_sub
    return {
        "price_label": settings.STRIPE_PRICE_LABEL,
        "price_usd": price,
        "stripe_fee_per_sub_usd": fee,
        "net_per_subscriber_usd": net_per_sub,
        "active_subscribers": active_subscribers,
        "gross_monthly_revenue_usd": gross,
        "net_monthly_revenue_usd": net,
        "estimated_margin_usd": net - projected_cost,
        "estimated_margin_7d_rate_usd": net - projected_cost_7d,
        "break_even_subscribers": (projected_cost / net_per_sub) if net_per_sub > 0 else None,
        "projected_cost_per_active_user_usd": (projected_cost / active_users) if active_users else None,
    }


async def build_summary(conn, period: str = "month", start: Optional[str] = None, end: Optional[str] = None,
                        now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or utc_now()
    start_day, end_day, period = resolve_period(period, start, end, now)
    daily = AIUsageDaily.__table__
    today = now.astimezone(timezone.utc).date()

    rows = (await conn.execute(
        select(daily.c.category, daily.c.feature, daily.c.provider, daily.c.model, daily.c.subject_kind, *_sums())
        .where(daily.c.day >= start_day, daily.c.day <= end_day)
        .group_by(daily.c.category, daily.c.feature, daily.c.provider, daily.c.model, daily.c.subject_kind)
    )).mappings().all()

    totals = _empty_totals()
    by_model: dict[tuple, dict] = {}
    by_feature: dict[tuple, dict] = {}
    by_kind: dict[str, dict] = {}
    by_category: dict[str, dict] = {}
    prompt_cache_saved = 0.0
    result_cache_saved = 0.0
    for row in rows:
        row = dict(row)
        _accumulate(totals, row)
        if row["category"] == usage_tracking.CATEGORY_LLM:
            _accumulate(by_model.setdefault((row["provider"], row["model"]), _empty_totals()), row)
            prompt_cache_saved += float(row["saved_usd"] or 0.0)
        elif row["category"] == usage_tracking.CATEGORY_LLM_CACHE:
            result_cache_saved += float(row["saved_usd"] or 0.0)
        _accumulate(by_feature.setdefault((_feature_category(row["category"]), row["feature"]), _empty_totals()), row)
        _accumulate(by_kind.setdefault(row["subject_kind"], _empty_totals()), row)
        _accumulate(by_category.setdefault(row["category"], _empty_totals()), row)

    active = (await conn.execute(
        select(daily.c.subject_kind, func.count(func.distinct(daily.c.subject_key)))
        .where(daily.c.day >= start_day, daily.c.day <= end_day)
        .group_by(daily.c.subject_kind)
    )).all()
    active_counts = {kind: int(count) for kind, count in active}
    active_users = active_counts.get(usage_tracking.KIND_USER, 0)

    history_start = min(today.replace(day=1), today - timedelta(days=6), start_day)
    history = await _daily_costs(conn, history_start, today)
    projection = project_month(history, now)
    series_days = [start_day + timedelta(days=i) for i in range((end_day - start_day).days + 1)]

    finished = _finish(totals)
    user_cost = _finish(by_kind.get(usage_tracking.KIND_USER, _empty_totals()))["total_usd"]
    days = elapsed_days(start_day, end_day, now)
    subs = await subscription_counts(conn)
    suno = _finish(by_category.get(usage_tracking.CATEGORY_SUNO, _empty_totals()))
    credits_observed = sorted(usage_tracking.observed_suno_credits)

    return {
        "period": {"name": period, "start": start_day.isoformat(), "end": end_day.isoformat(), "days": days},
        "generated_at": now.isoformat(),
        "totals": finished,
        "per_day_usd": finished["total_usd"] / days if days else 0.0,
        "by_model": sorted(
            [{"provider": p, "model": m, **_finish(t)} for (p, m), t in by_model.items()],
            key=lambda r: r["total_usd"], reverse=True),
        "by_feature": sorted(
            [{"category": c, "feature": f, "label": feature_label(f), **_finish(t)} for (c, f), t in by_feature.items()],
            key=lambda r: (r["total_usd"], r["saved_usd"], r["calls"]), reverse=True),
        "by_subject_kind": {kind: _finish(t) for kind, t in by_kind.items()},
        "by_category": {cat: _finish(t) for cat, t in by_category.items()},
        "cache_savings": {
            "prompt_cache_usd": prompt_cache_saved,
            "result_cache_usd": result_cache_saved,
            "total_usd": prompt_cache_saved + result_cache_saved,
            "result_cache_hits": int(_finish(by_category.get(usage_tracking.CATEGORY_LLM_CACHE, _empty_totals()))["cache_hits"]),
            "tts_clip_cache_hits": int(sum(t["cache_hits"] for (c, f), t in by_feature.items()
                                           if c == usage_tracking.CATEGORY_GPU and f.startswith("tts.clip_cache"))),
        },
        "active_subjects": active_counts,
        "cost_per_active_user_usd": (user_cost / active_users) if active_users else None,
        "all_in_cost_per_active_user_usd": (finished["total_usd"] / active_users) if active_users else None,
        "month": projection,
        "suno": {
            "generations": int(suno["units"]),
            "cost_usd": suno["cost_usd"],
            "cost_per_generation_usd": settings.SUNO_COST_PER_GENERATION_USD,
            "credits_per_generation_setting": settings.SUNO_CREDITS_PER_GENERATION,
            "observed_credits_per_generation": credits_observed[len(credits_observed) // 2] if credits_observed else None,
        },
        "revenue": revenue_context(subs["active_subscribers"], projection["projected_linear_usd"],
                                   projection["projected_7d_rate_usd"], active_users),
        "registered_users": subs["registered_users"],
        "daily": [{"day": d.isoformat(), "total_usd": history.get(d, 0.0)} for d in series_days],
        "pricing": {
            "models": llm_telemetry.pricing_table(),
            "sources": llm_telemetry.PRICING_SOURCES,
            "deepseek_offpeak_enabled": settings.LLM_DEEPSEEK_OFFPEAK_PRICING,
            "deepseek_peak_hours_utc": [list(w) for w in llm_telemetry.DEEPSEEK_PEAK_HOURS_UTC],
            "electricity_per_gpu_hour_usd": settings.ELECTRICITY_COST_PER_GPU_HOUR_USD,
            "api_cost_per_call_usd": settings.API_COST_PER_CALL_USD,
        },
        "recorder": usage_tracking.recorder.snapshot(),
    }


async def build_users(conn, period: str = "month", start: Optional[str] = None, end: Optional[str] = None,
                      now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or utc_now()
    start_day, end_day, period = resolve_period(period, start, end, now)
    daily = AIUsageDaily.__table__
    rows = (await conn.execute(
        select(daily.c.subject_key, daily.c.subject_kind, daily.c.user_id, daily.c.session_id, daily.c.category,
               *_sums())
        .where(daily.c.day >= start_day, daily.c.day <= end_day)
        .group_by(daily.c.subject_key, daily.c.subject_kind, daily.c.user_id, daily.c.session_id, daily.c.category)
    )).mappings().all()

    subjects: dict[str, dict] = {}
    for row in rows:
        row = dict(row)
        entry = subjects.setdefault(row["subject_key"], {
            "subject_key": row["subject_key"], "kind": row["subject_kind"], "user_id": row["user_id"],
            "session_id": row["session_id"], "generations": 0, **_empty_totals(),
        })
        _accumulate(entry, row)
        if row["category"] == usage_tracking.CATEGORY_SUNO:
            entry["generations"] += int(row["units"] or 0)

    user_ids = [e["user_id"] for e in subjects.values() if e["kind"] == usage_tracking.KIND_USER and e["user_id"]]
    profiles: dict[int, dict] = {}
    if user_ids:
        users = User.__table__
        for uid, username, tier, subscribed, status in (await conn.execute(
            select(users.c.id, users.c.username, users.c.tier, users.c.subscribed, users.c.subscription_status)
            .where(users.c.id.in_(user_ids))
        )).all():
            premium = status in ACTIVE_SUBSCRIPTION_STATUSES or bool(subscribed) or tier == "premium"
            profiles[int(uid)] = {"username": username, "plan": "premium" if premium else "free",
                                  "subscription_status": status}

    days = elapsed_days(start_day, end_day, now)
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    out = []
    for entry in subjects.values():
        finished = _finish(entry)
        profile = profiles.get(entry["user_id"] or -1, {})
        finished.update({
            "label": profile.get("username") or (entry["session_id"] if entry["kind"] != usage_tracking.KIND_USER
                                                  else f"user {entry['user_id']}"),
            "plan": profile.get("plan") or ("guest" if entry["kind"] == usage_tracking.KIND_GUEST else
                                            "system" if entry["kind"] == usage_tracking.KIND_SYSTEM else "free"),
            "subscription_status": profile.get("subscription_status"),
            "projected_month_usd": finished["total_usd"] / days * days_in_month if days else 0.0,
        })
        out.append(finished)
    out.sort(key=lambda r: r["total_usd"], reverse=True)

    guests = [r for r in out if r["kind"] == usage_tracking.KIND_GUEST]
    kept = [r for r in out if r["kind"] != usage_tracking.KIND_GUEST] + guests[:max(settings.USAGE_TOP_GUESTS, 0)]
    kept.sort(key=lambda r: r["total_usd"], reverse=True)
    hidden = guests[max(settings.USAGE_TOP_GUESTS, 0):]
    return {
        "period": {"name": period, "start": start_day.isoformat(), "end": end_day.isoformat(), "days": days},
        "subjects": kept,
        "hidden_guests": {"count": len(hidden), "total_usd": sum(r["total_usd"] for r in hidden)},
    }


async def build_subject_detail(conn, subject: usage_tracking.UsageSubject, period: str = "month",
                               start: Optional[str] = None, end: Optional[str] = None,
                               now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or utc_now()
    start_day, end_day, period = resolve_period(period, start, end, now)
    daily = AIUsageDaily.__table__
    today = now.astimezone(timezone.utc).date()
    series_start = min(start_day, today.replace(day=1), today - timedelta(days=29))

    feature_rows = (await conn.execute(
        select(daily.c.category, daily.c.feature, daily.c.provider, daily.c.model, *_sums())
        .where(daily.c.subject_key == subject.key, daily.c.day >= start_day, daily.c.day <= end_day)
        .group_by(daily.c.category, daily.c.feature, daily.c.provider, daily.c.model)
    )).mappings().all()
    series_rows = (await conn.execute(
        select(daily.c.day, *_sums())
        .where(daily.c.subject_key == subject.key, daily.c.day >= series_start, daily.c.day <= end_day)
        .group_by(daily.c.day)
    )).mappings().all()

    totals = _empty_totals()
    merged: dict[tuple, dict] = {}
    generations = 0
    for row in feature_rows:
        row = dict(row)
        _accumulate(totals, row)
        if row["category"] == usage_tracking.CATEGORY_SUNO:
            generations += int(row["units"] or 0)
        key = (_feature_category(row["category"]), row["feature"])
        entry = merged.setdefault(key, {"category": key[0], "feature": row["feature"], "provider": "", "model": "",
                                        **_empty_totals()})
        if row["category"] != usage_tracking.CATEGORY_LLM_CACHE:
            entry["provider"] = entry["provider"] or row["provider"]
            entry["model"] = entry["model"] or row["model"]
        _accumulate(entry, row)
    features = [{**entry, "label": feature_label(entry["feature"]), **_finish(entry)} for entry in merged.values()]
    features.sort(key=lambda r: (r["total_usd"], r["calls"]), reverse=True)

    by_day = {row["day"]: _finish(dict(row)) for row in series_rows}
    series = []
    for i in range((end_day - series_start).days + 1):
        d = series_start + timedelta(days=i)
        item = by_day.get(d)
        series.append({"day": d.isoformat(), "total_usd": item["total_usd"] if item else 0.0,
                       "calls": int(item["calls"]) if item else 0, "tokens": int(item["tokens"]) if item else 0})

    profile = None
    if subject.kind == usage_tracking.KIND_USER:
        users = User.__table__
        row = (await conn.execute(
            select(users.c.username, users.c.tier, users.c.subscribed, users.c.subscription_status)
            .where(users.c.id == subject.user_id)
        )).first()
        if row:
            premium = row[3] in ACTIVE_SUBSCRIPTION_STATUSES or bool(row[2]) or row[1] == "premium"
            profile = {"username": row[0], "plan": "premium" if premium else "free", "subscription_status": row[3]}

    days = elapsed_days(start_day, end_day, now)
    finished = _finish(totals)
    daily_costs = {date.fromisoformat(item["day"]): item["total_usd"] for item in series}
    return {
        "subject": {"key": subject.key, "kind": subject.kind, "user_id": subject.user_id,
                    "session_id": subject.session_id, **(profile or {})},
        "period": {"name": period, "start": start_day.isoformat(), "end": end_day.isoformat(), "days": days},
        "totals": finished,
        "generations": generations,
        "features": features,
        "daily": series,
        "month": project_month(daily_costs, now),
    }
