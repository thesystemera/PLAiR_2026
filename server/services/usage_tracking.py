import asyncio
import sys
import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any, Awaitable, Callable, Optional

from config.settings import settings
from services import log_service

KIND_USER = "user"
KIND_GUEST = "guest"
KIND_SYSTEM = "system"

CATEGORY_LLM = "llm"
CATEGORY_LLM_CACHE = "llm_cache"
CATEGORY_API = "api"
CATEGORY_SUNO = "suno"
CATEGORY_GPU = "gpu"

_PLUMBING_MODULES = frozenset({
    "services.llm_router",
    "services.ai_service",
    "services.llm_telemetry",
    "services.usage_tracking",
    "services.llm_result_cache",
    "services.task_utils",
    "services.http_client",
})
_PLUMBING_PREFIXES = ("asyncio", "contextlib", "concurrent", "threading", "anyio", "starlette", "fastapi")
_GENERIC_NAMES = frozenset({"wrapper", "<lambda>", "<module>", "<genexpr>", "<listcomp>", "<dictcomp>", "run", "inner"})
_MAX_FRAME_DEPTH = 60


@dataclass(frozen=True)
class UsageSubject:
    kind: str
    user_id: Optional[int]
    session_id: str

    @property
    def key(self) -> str:
        if self.kind == KIND_USER:
            return f"user:{self.user_id}"
        return f"{self.kind}:{self.session_id}"


UNATTRIBUTED = UsageSubject(KIND_SYSTEM, None, "unattributed")

_subject: ContextVar[Optional[UsageSubject]] = ContextVar("usage_subject", default=None)
_feature: ContextVar[Optional[str]] = ContextVar("usage_feature", default=None)


def _is_guest_id(value: str) -> bool:
    from security_middleware import is_valid_guest_id
    return is_valid_guest_id(value)


def user_subject(user_id: int) -> UsageSubject:
    return UsageSubject(KIND_USER, int(user_id), str(int(user_id)))


def system_subject(label: str = "system") -> UsageSubject:
    return UsageSubject(KIND_SYSTEM, None, label or "system")


def subject_for_session(session_id: Any = None, user_id: Any = None) -> UsageSubject:
    if user_id not in (None, "", 0):
        try:
            return user_subject(int(user_id))
        except (TypeError, ValueError):
            pass
    sid = str(session_id or "").strip()
    if sid.startswith("user:"):
        sid = sid[5:]
    if sid.isdigit():
        return user_subject(int(sid))
    if sid and _is_guest_id(sid):
        return UsageSubject(KIND_GUEST, None, sid)
    return system_subject(sid[:64] or "system")


def current_subject() -> UsageSubject:
    return _subject.get() or UNATTRIBUTED


def bind(subject: UsageSubject):
    return _subject.set(subject)


def bind_session(session_id: Any = None, user_id: Any = None):
    return _subject.set(subject_for_session(session_id, user_id))


def unbind(token) -> None:
    try:
        _subject.reset(token)
    except (ValueError, RuntimeError):
        pass


@contextmanager
def subject_scope(subject: Optional[UsageSubject] = None, *, session_id: Any = None, user_id: Any = None):
    token = _subject.set(subject or subject_for_session(session_id, user_id))
    try:
        yield
    finally:
        unbind(token)


@contextmanager
def system_scope(label: str = "system"):
    with subject_scope(system_subject(label)):
        yield


@contextmanager
def feature_scope(label: str):
    token = _feature.set(label)
    try:
        yield
    finally:
        try:
            _feature.reset(token)
        except (ValueError, RuntimeError):
            pass


def _frame_label(frame, module: str, name: str) -> str:
    code = frame.f_code
    if code.co_argcount and code.co_varnames and code.co_varnames[0] in ("self", "cls"):
        owner = frame.f_locals.get(code.co_varnames[0])
        if owner is not None:
            owner_name = owner.__name__ if isinstance(owner, type) else type(owner).__name__
            return f"{owner_name}.{name}"
    return f"{module.rsplit('.', 1)[-1]}.{name}"


def derive_feature(skip: int = 1) -> str:
    explicit = _feature.get()
    if explicit:
        return explicit
    try:
        frame = sys._getframe(skip + 1)
    except ValueError:
        return "unknown"
    fallback = None
    depth = 0
    while frame is not None and depth < _MAX_FRAME_DEPTH:
        module = frame.f_globals.get("__name__", "") or ""
        name = frame.f_code.co_name
        if module not in _PLUMBING_MODULES and not module.startswith(_PLUMBING_PREFIXES):
            if fallback is None:
                fallback = _frame_label(frame, module, name)
            if not name.startswith("_") and name not in _GENERIC_NAMES:
                return _frame_label(frame, module, name)
        frame = frame.f_back
        depth += 1
    return fallback or "unknown"


@dataclass
class UsageEventData:
    created_at: datetime
    subject_key: str
    subject_kind: str
    user_id: Optional[int]
    session_id: str
    category: str
    feature: str
    role: str = ""
    provider: str = ""
    model: str = ""
    prompt_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_hit: bool = False
    error: bool = False
    cost_usd: float = 0.0
    saved_usd: float = 0.0
    gpu_seconds: float = 0.0
    audio_seconds: float = 0.0
    units: float = 0.0
    latency_ms: float = 0.0

    def as_row(self) -> dict:
        return asdict(self)


Sink = Callable[[list[UsageEventData]], Awaitable[None]]


def rollup_key(event: UsageEventData) -> tuple:
    return (event.created_at.astimezone(timezone.utc).date(), event.subject_key, event.category, event.feature,
            event.role, event.provider, event.model)


def aggregate_rollups(events: list[UsageEventData]) -> list[dict]:
    buckets: dict[tuple, dict] = {}
    for event in events:
        key = rollup_key(event)
        row = buckets.get(key)
        if row is None:
            row = buckets[key] = {
                "day": key[0], "subject_key": event.subject_key, "subject_kind": event.subject_kind,
                "user_id": event.user_id, "session_id": event.session_id, "category": event.category,
                "feature": event.feature, "role": event.role, "provider": event.provider, "model": event.model,
                "calls": 0, "cache_hits": 0, "errors": 0, "prompt_tokens": 0, "cached_tokens": 0,
                "output_tokens": 0, "reasoning_tokens": 0, "cost_usd": 0.0, "saved_usd": 0.0,
                "gpu_seconds": 0.0, "audio_seconds": 0.0, "units": 0.0,
            }
        if event.cache_hit:
            row["cache_hits"] += 1
        else:
            row["calls"] += 1
        if event.error:
            row["errors"] += 1
        for name in ("prompt_tokens", "cached_tokens", "output_tokens", "reasoning_tokens"):
            row[name] += int(getattr(event, name) or 0)
        for name in ("cost_usd", "saved_usd", "gpu_seconds", "audio_seconds", "units"):
            row[name] += float(getattr(event, name) or 0.0)
    return list(buckets.values())


async def postgres_sink(events: list[UsageEventData], engine=None, schema_map: Optional[dict] = None) -> None:
    from sqlalchemy import func
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from database.models import AIUsageEvent, AIUsageDaily

    if engine is None:
        from database.connection import engine as default_engine
        engine = default_engine

    daily = AIUsageDaily.__table__
    rollups = aggregate_rollups(events)
    stmt = pg_insert(daily).values(rollups)
    excluded = stmt.excluded
    additive = ("calls", "cache_hits", "errors", "prompt_tokens", "cached_tokens", "output_tokens",
                "reasoning_tokens", "cost_usd", "saved_usd", "gpu_seconds", "audio_seconds", "units")
    stmt = stmt.on_conflict_do_update(
        constraint="uq_ai_usage_daily_bucket",
        set_={**{name: daily.c[name] + excluded[name] for name in additive}, "updated_at": func.now()},
    )
    async with engine.begin() as conn:
        if schema_map:
            conn = await conn.execution_options(schema_translate_map=schema_map)
        await conn.execute(AIUsageEvent.__table__.insert(), [event.as_row() for event in events])
        await conn.execute(stmt)


async def postgres_prune(engine=None, retention_days: Optional[int] = None) -> int:
    from sqlalchemy import delete
    from database.models import AIUsageEvent

    if engine is None:
        from database.connection import engine as default_engine
        engine = default_engine
    days = settings.USAGE_RAW_RETENTION_DAYS if retention_days is None else retention_days
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(days, 1))
    async with engine.begin() as conn:
        result = await conn.execute(delete(AIUsageEvent.__table__).where(AIUsageEvent.__table__.c.created_at < cutoff))
        return int(result.rowcount or 0)


@dataclass
class RecorderStats:
    recorded: int = 0
    written: int = 0
    dropped_overflow: int = 0
    dropped_write_failure: int = 0
    write_failures: int = 0
    last_error: str = ""
    last_flush_at: Optional[float] = None
    pruned: int = 0
    feature_avg_cost: dict = field(default_factory=dict)


class UsageRecorder:
    PRUNE_INTERVAL_S = 6 * 3600
    WARN_INTERVAL_S = 300

    def __init__(self, sink: Optional[Sink] = None, prune: Optional[Callable[[], Awaitable[int]]] = None):
        self.sink: Sink = sink or postgres_sink
        self.prune = prune or postgres_prune
        self.buffer: deque[UsageEventData] = deque()
        self.stats = RecorderStats()
        self._avg_cost: dict[str, float] = {}
        self._task: Optional[asyncio.Task] = None
        self._wake: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._last_warn = 0.0
        self._last_prune = 0.0
        self._flush_lock: Optional[asyncio.Lock] = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def add(self, event: UsageEventData) -> None:
        if len(self.buffer) >= max(settings.USAGE_BUFFER_MAX, 1):
            self.stats.dropped_overflow += 1
            return
        self.buffer.append(event)
        self.stats.recorded += 1
        if len(self.buffer) >= settings.USAGE_FLUSH_BATCH and self._wake is not None:
            try:
                if asyncio.get_running_loop() is self._loop:
                    self._wake.set()
            except RuntimeError:
                pass

    def note_cost(self, feature: str, cost: float) -> None:
        previous = self._avg_cost.get(feature)
        self._avg_cost[feature] = cost if previous is None else previous * 0.8 + cost * 0.2

    def expected_cost(self, feature: str) -> float:
        return self._avg_cost.get(feature, 0.0)

    def seed_costs(self, averages: dict[str, float]) -> None:
        for feature, cost in averages.items():
            self._avg_cost.setdefault(feature, float(cost or 0.0))

    def _warn(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last_warn >= self.WARN_INTERVAL_S:
            self._last_warn = now
            log_service.warning(message)

    async def flush(self) -> int:
        if self._flush_lock is None:
            self._flush_lock = asyncio.Lock()
        written = 0
        async with self._flush_lock:
            while self.buffer:
                batch = [self.buffer.popleft() for _ in range(min(len(self.buffer), max(settings.USAGE_FLUSH_BATCH, 1)))]
                try:
                    await asyncio.wait_for(self.sink(batch), timeout=settings.USAGE_WRITE_TIMEOUT_S)
                except asyncio.CancelledError:
                    self.stats.dropped_write_failure += len(batch)
                    raise
                except Exception as e:
                    self.stats.write_failures += 1
                    self.stats.dropped_write_failure += len(batch)
                    self.stats.last_error = f"{type(e).__name__}: {str(e)[:200]}"
                    self._warn(f"[USAGE] Dropped {len(batch)} usage event(s) - write failed: {self.stats.last_error} "
                               f"(total dropped {self.stats.dropped_write_failure})")
                    break
                written += len(batch)
                self.stats.written += len(batch)
            self.stats.last_flush_at = time.time()
        return written

    async def _maybe_prune(self) -> None:
        if time.monotonic() - self._last_prune < self.PRUNE_INTERVAL_S:
            return
        self._last_prune = time.monotonic()
        try:
            removed = await asyncio.wait_for(self.prune(), timeout=60)
            self.stats.pruned += removed
            if removed:
                log_service.system(f"[USAGE] Pruned {removed} raw usage event(s) older than {settings.USAGE_RAW_RETENTION_DAYS} days")
        except Exception as e:
            self._warn(f"[USAGE] Retention prune failed: {type(e).__name__}: {e}")

    async def _run(self) -> None:
        with system_scope("usage_writer"):
            while True:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=max(settings.USAGE_FLUSH_INTERVAL_S, 0.05))
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                try:
                    await self.flush()
                    await self._maybe_prune()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._warn(f"[USAGE] Writer loop error: {type(e).__name__}: {e}")

    def start(self) -> None:
        if self.running or not settings.USAGE_TRACKING_ENABLED:
            return
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._flush_lock = asyncio.Lock()
        self._last_prune = time.monotonic() - self.PRUNE_INTERVAL_S + 600
        self._task = self._loop.create_task(self._run(), name="usage_writer")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        try:
            await asyncio.wait_for(self.flush(), timeout=settings.USAGE_WRITE_TIMEOUT_S + 2)
        except Exception as e:
            log_service.warning(f"[USAGE] Final flush failed: {type(e).__name__}: {e}")

    def snapshot(self) -> dict:
        return {
            "running": self.running,
            "buffered": len(self.buffer),
            "recorded": self.stats.recorded,
            "written": self.stats.written,
            "dropped_overflow": self.stats.dropped_overflow,
            "dropped_write_failure": self.stats.dropped_write_failure,
            "write_failures": self.stats.write_failures,
            "last_error": self.stats.last_error,
            "last_flush_at": self.stats.last_flush_at,
            "pruned": self.stats.pruned,
        }


recorder = UsageRecorder()
observed_suno_credits: deque[float] = deque(maxlen=50)


def _emit(category: str, feature: str, subject: Optional[UsageSubject] = None, **fields) -> None:
    if not settings.USAGE_TRACKING_ENABLED:
        return
    subject = subject or current_subject()
    recorder.add(UsageEventData(
        created_at=datetime.now(timezone.utc),
        subject_key=subject.key,
        subject_kind=subject.kind,
        user_id=subject.user_id,
        session_id=subject.session_id,
        category=category,
        feature=feature[:120],
        **fields,
    ))


def record_llm(*, role: str, provider: str, model: str, prompt_tokens: int = 0, cached_tokens: int = 0,
               output_tokens: int = 0, reasoning_tokens: int = 0, cost_usd: float = 0.0, saved_usd: float = 0.0,
               latency_ms: float = 0.0, error: bool = False, feature: Optional[str] = None) -> None:
    try:
        label = feature or derive_feature()
        if not error:
            recorder.note_cost(label, cost_usd)
        _emit(CATEGORY_LLM, label, role=role or "", provider=provider or "", model=model or "",
              prompt_tokens=int(prompt_tokens or 0), cached_tokens=int(cached_tokens or 0),
              output_tokens=int(output_tokens or 0) + int(reasoning_tokens or 0),
              reasoning_tokens=int(reasoning_tokens or 0), cost_usd=float(cost_usd or 0.0),
              saved_usd=float(saved_usd or 0.0), latency_ms=float(latency_ms or 0.0), error=bool(error))
    except Exception as e:
        recorder._warn(f"[USAGE] record_llm failed: {type(e).__name__}: {e}")


def record_cache_hit(namespace: str, feature: Optional[str] = None) -> None:
    try:
        label = feature or derive_feature()
        _emit(CATEGORY_LLM_CACHE, label, role=namespace[:60], cache_hit=True, saved_usd=recorder.expected_cost(label))
    except Exception as e:
        recorder._warn(f"[USAGE] record_cache_hit failed: {type(e).__name__}: {e}")


def record_api_call(api: str, provider: str, error: bool = False, cached: bool = False) -> None:
    try:
        cost = 0.0 if (error or cached) else float(settings.API_COST_PER_CALL_USD.get(api, 0.0))
        _emit(CATEGORY_API, f"api.{api}", provider=provider, cache_hit=cached, error=error,
              cost_usd=cost, units=0.0 if cached else 1.0,
              saved_usd=float(settings.API_COST_PER_CALL_USD.get(api, 0.0)) if cached else 0.0)
    except Exception as e:
        recorder._warn(f"[USAGE] record_api_call failed: {type(e).__name__}: {e}")


def record_suno_generation(model: str = "", subject: Optional[UsageSubject] = None) -> None:
    try:
        _emit(CATEGORY_SUNO, "suno.generation", subject=subject, provider="sunoapi", model=model or "",
              cost_usd=float(settings.SUNO_COST_PER_GENERATION_USD), units=1.0)
    except Exception as e:
        recorder._warn(f"[USAGE] record_suno_generation failed: {type(e).__name__}: {e}")


def note_suno_credit_delta(delta: float) -> None:
    if delta > 0:
        observed_suno_credits.append(float(delta))


def record_gpu(feature: str, gpu_seconds: float, audio_seconds: float = 0.0, provider: str = "local",
               model: str = "", cache_hit: bool = False, error: bool = False) -> None:
    try:
        _emit(CATEGORY_GPU, feature, provider=provider, model=model, cache_hit=cache_hit, error=error,
              gpu_seconds=max(float(gpu_seconds or 0.0), 0.0), audio_seconds=max(float(audio_seconds or 0.0), 0.0))
    except Exception as e:
        recorder._warn(f"[USAGE] record_gpu failed: {type(e).__name__}: {e}")


async def seed_feature_costs(days: int = 30) -> None:
    from sqlalchemy import select, func
    from database.connection import engine
    from database.models import AIUsageDaily

    daily = AIUsageDaily.__table__
    since = datetime.now(timezone.utc).date() - timedelta(days=days)
    query = (
        select(daily.c.feature, (func.sum(daily.c.cost_usd) / func.nullif(func.sum(daily.c.calls - daily.c.errors), 0)))
        .where(daily.c.category == CATEGORY_LLM, daily.c.day >= since)
        .group_by(daily.c.feature)
    )
    try:
        async with engine.connect() as conn:
            rows = (await asyncio.wait_for(conn.execute(query), timeout=10)).all()
        recorder.seed_costs({feature: float(avg or 0.0) for feature, avg in rows if avg is not None})
    except Exception as e:
        log_service.warning(f"[USAGE] Could not seed cache-savings estimates: {type(e).__name__}: {e}")


async def start() -> None:
    if not settings.USAGE_TRACKING_ENABLED:
        log_service.system("[USAGE] Usage tracking disabled (USAGE_TRACKING_ENABLED=false)")
        return
    recorder.start()
    asyncio.get_running_loop().create_task(seed_feature_costs(), name="usage_seed_costs")
    log_service.success("✓ AI usage tracking started")


async def stop() -> None:
    await recorder.stop()
