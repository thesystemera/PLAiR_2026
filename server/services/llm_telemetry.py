import time
from datetime import datetime, timezone
from typing import Any, Optional

from config.settings import settings
from services import log_service
from services import usage_tracking

PRICING: dict[str, tuple[float, float, float]] = {
    "gemini-3.5-flash-lite":   (0.30, 2.50, 0.03),
    "gemini-3.5-flash":        (1.50, 9.00, 0.15),
    "gemini-2.5-flash":        (0.30, 2.50, 0.03),
    "gemini-2.5-flash-lite":   (0.10, 0.40, 0.01),
    "deepseek-flash":          (0.30, 1.20, 0.006),
    "deepseek-v4-flash":       (0.30, 1.20, 0.006),
    "deepseek-v4-pro":         (1.32, 3.96, 0.044),
}

OFFPEAK_MULTIPLIER: dict[str, float] = {
    "deepseek-flash": 0.5,
    "deepseek-v4-flash": 0.5,
    "deepseek-v4-pro": 0.5,
}

DEEPSEEK_PEAK_HOURS_UTC = ((1, 4), (6, 10))

PRICING_SOURCES = {
    "gemini": "ai.google.dev/gemini-api/docs/pricing (Standard paid tier, checked 2026-09-27)",
    "deepseek": "api-docs.deepseek.com/quick_start/pricing (peak rates; off-peak is half, checked 2026-09-27)",
}

HOURS_KEPT = 48

_USAGE: dict[tuple[str, str, str, str], dict[str, Any]] = {}
_FALLBACKS: dict[str, int] = {}
_CACHE_HITS: dict[str, int] = {}
_STARTED_AT = time.time()
_last_flush = time.monotonic()


def _empty_row() -> dict[str, Any]:
    return {
        "calls": 0,
        "prompt_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "ms_total": 0.0,
        "errors": 0,
        "est_usd": 0.0,
    }


def is_deepseek_peak(at: datetime) -> bool:
    at = at.astimezone(timezone.utc)
    if at.weekday() >= 5:
        return False
    return any(start <= at.hour < end for start, end in DEEPSEEK_PEAK_HOURS_UTC)


def price_multiplier(model: str, at: Optional[datetime] = None) -> float:
    multiplier = OFFPEAK_MULTIPLIER.get(model)
    if multiplier is None or not settings.LLM_DEEPSEEK_OFFPEAK_PRICING:
        return 1.0
    return 1.0 if is_deepseek_peak(at or datetime.now(timezone.utc)) else multiplier


def estimate_usd(model: str, prompt: int, output: int, cached: int, at: Optional[datetime] = None) -> float:
    p_in, p_out, p_cached = PRICING.get(model, (0.0, 0.0, 0.0))
    billable_in = max(prompt - cached, 0)
    base = (billable_in * p_in + cached * p_cached + output * p_out) / 1_000_000
    return base * price_multiplier(model, at)


def prompt_cache_savings_usd(model: str, cached: int, at: Optional[datetime] = None) -> float:
    p_in, _, p_cached = PRICING.get(model, (0.0, 0.0, 0.0))
    return cached * max(p_in - p_cached, 0.0) / 1_000_000 * price_multiplier(model, at)


def pricing_table() -> list[dict[str, Any]]:
    return [
        {
            "model": model,
            "provider": "deepseek" if model.startswith("deepseek") else "gemini",
            "input_per_m": p_in,
            "output_per_m": p_out,
            "cached_input_per_m": p_cached,
            "offpeak_multiplier": OFFPEAK_MULTIPLIER.get(model),
        }
        for model, (p_in, p_out, p_cached) in PRICING.items()
    ]


def _hour_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:00Z")


def record_usage(
    *,
    role: str,
    provider: str,
    model: str,
    prompt_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    reasoning_tokens: int = 0,
    ms: float = 0.0,
    error: bool = False,
) -> None:
    call_usd = estimate_usd(model, prompt_tokens, output_tokens + reasoning_tokens, cached_tokens)
    usage_tracking.record_llm(
        role=role, provider=provider, model=model, prompt_tokens=prompt_tokens, cached_tokens=cached_tokens,
        output_tokens=output_tokens, reasoning_tokens=reasoning_tokens, cost_usd=call_usd,
        saved_usd=prompt_cache_savings_usd(model, cached_tokens), latency_ms=ms, error=error,
    )
    row = _USAGE.setdefault((_hour_key(), role, provider, model), _empty_row())
    row["calls"] += 1
    row["prompt_tokens"] += prompt_tokens
    row["output_tokens"] += output_tokens
    row["cached_tokens"] += cached_tokens
    row["reasoning_tokens"] += reasoning_tokens
    row["ms_total"] += ms
    row["est_usd"] += call_usd
    if error:
        row["errors"] += 1
    else:
        log_service.debug(
            f"[LLM] role={role} provider={provider} model={model} prompt={prompt_tokens} cached={cached_tokens} "
            f"output={output_tokens} reasoning={reasoning_tokens} ms={ms:.0f} usd={call_usd:.6f}"
        )
    maybe_log_aggregate()


def record_gemini_usage(role: str, model: str, usage_metadata: Any, ms: float) -> dict[str, int]:
    um = usage_metadata
    usage = {
        "prompt_tokens": int(getattr(um, "prompt_token_count", 0) or 0) if um else 0,
        "output_tokens": int(getattr(um, "candidates_token_count", 0) or 0) if um else 0,
        "cached_tokens": int(getattr(um, "cached_content_token_count", 0) or 0) if um else 0,
        "reasoning_tokens": int(getattr(um, "thoughts_token_count", 0) or 0) if um else 0,
    }
    record_usage(role=role, provider="gemini", model=model, ms=ms, **usage)
    return usage


def record_deepseek_usage(role: str, model: str, usage: dict | None, ms: float) -> dict[str, int]:
    u = usage or {}
    details = u.get("completion_tokens_details") or {}
    reasoning = int(details.get("reasoning_tokens", 0) or 0)
    result = {
        "prompt_tokens": int(u.get("prompt_tokens", 0) or 0),
        "output_tokens": int(u.get("completion_tokens", 0) or 0) - reasoning,
        "cached_tokens": int(u.get("prompt_cache_hit_tokens", 0) or 0),
        "reasoning_tokens": reasoning,
    }
    record_usage(role=role, provider="deepseek", model=model, ms=ms, **result)
    return result


def record_error(role: str, provider: str, model: str, ms: float) -> None:
    record_usage(role=role, provider=provider, model=model, ms=ms, error=True)


def record_fallback(role: str, from_candidate: str, to_candidate: str, reason: str) -> None:
    key = f"{role}:{from_candidate}->{to_candidate}"
    _FALLBACKS[key] = _FALLBACKS.get(key, 0) + 1
    log_service.throttled(
        f"llm_fallback:{key}",
        f"[LLM] {role}: {from_candidate} failed ({reason}) - using {to_candidate} "
        f"({_FALLBACKS[key]} fallback(s) since start)")


def record_cache_hit(namespace: str) -> None:
    _CACHE_HITS[namespace] = _CACHE_HITS.get(namespace, 0) + 1
    usage_tracking.record_cache_hit(namespace)


def get_snapshot() -> dict[str, Any]:
    rows = []
    total_usd = 0.0
    for (hour, role, provider, model), row in _USAGE.items():
        rows.append({"hour": hour, "role": role, "provider": provider, "model": model, **row})
        total_usd += row["est_usd"]
    rows.sort(key=lambda r: (r["hour"], r["est_usd"]), reverse=True)
    return {
        "started_at": _STARTED_AT,
        "uptime_seconds": time.time() - _STARTED_AT,
        "total_est_usd": total_usd,
        "fallbacks": dict(_FALLBACKS),
        "result_cache_hits": dict(_CACHE_HITS),
        "rows": rows,
    }


def _prune() -> None:
    hours = sorted({key[0] for key in _USAGE})
    for stale in hours[:-HOURS_KEPT]:
        for key in [k for k in _USAGE if k[0] == stale]:
            _USAGE.pop(key, None)


def maybe_log_aggregate(force: bool = False) -> None:
    global _last_flush
    now = time.monotonic()
    if not force and now - _last_flush < settings.LLM_USAGE_LOG_INTERVAL_S:
        return
    _last_flush = now
    _prune()
    hour = _hour_key()
    per_role: dict[str, dict[str, Any]] = {}
    for (row_hour, role, provider, model), row in _USAGE.items():
        if row_hour != hour:
            continue
        agg = per_role.setdefault(role, _empty_row())
        for field in ("calls", "prompt_tokens", "output_tokens", "cached_tokens", "reasoning_tokens", "errors", "est_usd"):
            agg[field] += row[field]
    if not per_role:
        return
    parts = [
        f"{role}: {r['calls']} calls, {r['prompt_tokens']} in ({r['cached_tokens']} cached), "
        f"{r['output_tokens'] + r['reasoning_tokens']} out, {r['errors']} err, ${r['est_usd']:.4f}"
        for role, r in sorted(per_role.items())
    ]
    total = sum(r["est_usd"] for r in per_role.values())
    log_service.system(
        f"[LLM USAGE {hour}] ${total:.4f} this hour | " + " | ".join(parts)
        + (f" | fallbacks={_FALLBACKS}" if _FALLBACKS else "")
        + (f" | cache_hits={_CACHE_HITS}" if _CACHE_HITS else "")
    )


def reset() -> None:
    _USAGE.clear()
    _FALLBACKS.clear()
    _CACHE_HITS.clear()
