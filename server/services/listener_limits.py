"""What each kind of listener gets, and the two limits that hold it (one place for all of it).

Tiers: guest (no account), basic (free account) and premium (users.tier). Each tier has an AI allowance in dollars per
rolling day and per rolling 30 days, and its song credits. The allowance counts only the AI work a listener's own
actions cause (the request context usage_tracking marks as asked: chatting with the hosts, smart search, dictation,
posts, uploads, the welcome a new guest triggers), priced as usage_tracking prices it (LLM and paid API calls, plus
local GPU time at gpu_second_usd), never the station's own talk, voice or shared prompt cache
(usage_tracking.station_work()). Guests also share a daily allowance per IP, because
guest ids are free to make. Every request and WebSocket message also spends from a request bucket per listener and per
public IP (media a quarter of a request).

The numbers live in DEFAULT_LIMITS, overridden by what the admin saves in LISTENER_LIMITS_PATH (GET/PUT
/api/admin/limits, the Limits section of the usage panel). The listener-facing wording (notices, the hosts' note and
their line when the allowance is used up) is here too, so the app and the hosts say the same thing."""
import asyncio
import ipaddress
import json
import os
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from config import settings
from security_middleware import UNCOMPRESSED_PATH_PREFIXES
from services import log_service, usage_tracking
from services.rate_limit_service import rate_limit_service
from services.task_utils import spawn

TIERS = ("guest", "basic", "premium")
WINDOWS = {"day": 24 * 3600, "month": 30 * 24 * 3600}
SONG_PERIODS = ("day", "month")
LEDGER_SAVE_INTERVAL_S = 60

DEFAULT_LIMITS: Dict[str, Any] = {
    "tiers": {
        "guest": {"label": "Guest", "ai_usd_per_day": 0.05, "ai_usd_per_month": 0.30, "songs": 0,
                  "songs_period": "month"},
        "basic": {"label": "Free account", "ai_usd_per_day": 0.10, "ai_usd_per_month": 0.50, "songs": 5,
                  "songs_period": "month"},
        "premium": {"label": "Premium", "ai_usd_per_day": 0.30, "ai_usd_per_month": 1.00, "songs": 50,
                    "songs_period": "month"},
    },
    "guest_ai_usd_per_ip_per_day": 0.30,
    "gpu_second_usd": 0.0001,
    "warn_at": 0.8,
    "requests": {"per_second": 20, "burst": 100, "ip_per_second": 60, "ip_burst": 300, "media_cost": 0.25},
}
TIER_NUMBERS = ("ai_usd_per_day", "ai_usd_per_month", "songs")
TOP_NUMBERS = ("guest_ai_usd_per_ip_per_day", "gpu_second_usd", "warn_at")
REQUEST_NUMBERS = ("per_second", "burst", "ip_per_second", "ip_burst", "media_cost")

REQUEST_REFUSAL = "Easy, that's a lot at once. Give it a few seconds and try again."
UPSELL = {"guest": "A free account gives you more.", "basic": "Premium gives you more.", "premium": ""}
HOST_UPSELL = {"guest": "a free account gives them more", "basic": "Premium gives them more", "premium": ""}
HOST_OUT_LINES = {
    "guest": (
        "[BROADCAST] [JESS] &0.2& ~sighs~ Ah, that's all the chat time we've got for you for now, it tops up {when}. "
        "&0.1& Grab a free account and you get more of us.",
        "[BROADCAST] [LEO] &0.2& ~chuckles~ We've talked your ear off, that's our chat time for you for now. "
        "&0.1& It tops up {when}, and a free account gets you more.",
    ),
    "basic": (
        "[BROADCAST] [JESS] &0.2& ~sighs~ That's all our chat time for you for now, it tops up {when}. "
        "&0.1& Premium gets you plenty more, just saying.",
        "[BROADCAST] [LEO] &0.2& ~chuckles~ You've used up our chat time for now, it tops up {when}. "
        "&0.1& The music keeps rolling, though.",
    ),
    "premium": (
        "[BROADCAST] [JESS] &0.2& ~laughs softly~ We've talked a lot today, that's our chat time for now. "
        "&0.1& It tops up {when}, and the music keeps rolling.",
        "[BROADCAST] [LEO] &0.2& ~chuckles~ That's the chat time used up for now, it tops up {when}. "
        "&0.1& We'll keep the tunes coming.",
    ),
}


@dataclass(frozen=True)
class Allowance:
    tier: str
    state: str
    used: float
    window: str
    resets_in_s: int

    @property
    def out(self) -> bool:
        return self.state == "out"

    def public(self) -> Dict[str, Any]:
        return {"tier": self.tier, "state": self.state, "used": round(min(self.used, 1.0), 3), "window": self.window,
                "resets_in_s": self.resets_in_s, "message": notice_text(self)}


def _number(value: Any, minimum: float = 0.0) -> float:
    number = float(value)
    if number != number or number < minimum:
        raise ValueError(f"{value!r} is not a number of at least {minimum}")
    return number


def _merge(stored: Dict[str, Any]) -> Dict[str, Any]:
    merged = json.loads(json.dumps(DEFAULT_LIMITS))
    for tier, values in (stored.get("tiers") or {}).items():
        if tier in merged["tiers"] and isinstance(values, dict):
            merged["tiers"][tier].update({k: v for k, v in values.items() if k in merged["tiers"][tier]})
    merged.update({k: stored[k] for k in TOP_NUMBERS if k in stored})
    merged["requests"].update({k: v for k, v in (stored.get("requests") or {}).items() if k in REQUEST_NUMBERS})
    return merged


def validate(limits: Dict[str, Any]) -> Dict[str, Any]:
    merged = _merge(limits)
    for tier in TIERS:
        row = merged["tiers"][tier]
        row["label"] = str(row.get("label") or tier).strip()[:40] or tier
        for key in TIER_NUMBERS:
            row[key] = int(_number(row[key])) if key == "songs" else round(_number(row[key]), 4)
        if row["songs_period"] not in SONG_PERIODS:
            raise ValueError(f"songs_period must be one of {', '.join(SONG_PERIODS)}")
    for key in TOP_NUMBERS:
        merged[key] = round(_number(merged[key]), 6)
    if not 0 < merged["warn_at"] < 1:
        raise ValueError("warn_at must be between 0 and 1")
    for key in REQUEST_NUMBERS:
        merged["requests"][key] = round(_number(merged["requests"][key], 0.01), 3)
    return merged


class LimitsStore:
    def __init__(self, path):
        self.path = path
        self._limits = validate({})

    def load(self) -> None:
        try:
            if self.path.exists():
                self._limits = validate(json.loads(self.path.read_text(encoding="utf-8")))
        except Exception as e:
            log_service.error(f"[LIMITS] {self.path} is not valid, using the defaults: {e}")

    def get(self) -> Dict[str, Any]:
        return self._limits

    def save(self, limits: Dict[str, Any]) -> Dict[str, Any]:
        checked = validate(limits)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(checked, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
        self._limits = checked
        return checked


class SpendLedger:
    """Dollars of asked-for AI work per listener (and per IP for guests): hour buckets for the rolling day, day buckets
    for the rolling month. Thread-safe, saved to a JSON file so a restart keeps everyone's allowance."""

    def __init__(self, path):
        self.path = path
        self._hours: Dict[str, Dict[int, float]] = {}
        self._days: Dict[str, Dict[int, float]] = {}
        self._lock = threading.Lock()
        self._dirty = False

    def add(self, key: str, usd: float, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            hours = self._hours.setdefault(key, {})
            hour = int(now // 3600)
            hours[hour] = hours.get(hour, 0.0) + usd
            days = self._days.setdefault(key, {})
            day = int(now // 86400)
            days[day] = days.get(day, 0.0) + usd
            self._dirty = True

    def spent(self, key: str, window: str, now: Optional[float] = None) -> Tuple[float, list]:
        now = time.time() if now is None else now
        size, buckets = (3600, self._hours) if window == "day" else (86400, self._days)
        oldest = int((now - WINDOWS[window]) // size) + 1
        with self._lock:
            entries = sorted((index, usd) for index, usd in buckets.get(key, {}).items() if index >= oldest)
        return sum(usd for _, usd in entries), [(index * size + WINDOWS[window], usd) for index, usd in entries]

    def prune(self, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        oldest_hour, oldest_day = int((now - WINDOWS["day"]) // 3600), int((now - WINDOWS["month"]) // 86400)
        with self._lock:
            self._hours = {k: kept for k, v in self._hours.items()
                           if (kept := {i: usd for i, usd in v.items() if i >= oldest_hour})}
            self._days = {k: kept for k, v in self._days.items()
                          if (kept := {i: usd for i, usd in v.items() if i >= oldest_day})}

    def load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._hours = {k: {int(i): float(usd) for i, usd in v.items()} for k, v in data.get("hours", {}).items()}
                self._days = {k: {int(i): float(usd) for i, usd in v.items()} for k, v in data.get("days", {}).items()}
                self.prune()
        except Exception as e:
            log_service.error(f"[LIMITS] Could not read the AI allowance ledger {self.path}: {e}")

    def save(self, force: bool = False) -> None:
        if not (self._dirty or force):
            return
        self.prune()
        with self._lock:
            data = {"hours": self._hours, "days": self._days}
            self._dirty = False
            text = json.dumps(data)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception as e:
            self._dirty = True
            log_service.error(f"[LIMITS] Could not save the AI allowance ledger: {e}")


store = LimitsStore(settings.LISTENER_LIMITS_PATH)
ledger = SpendLedger(settings.AI_ALLOWANCE_PATH)
_announced: Dict[str, str] = {}
_loop: Optional[asyncio.AbstractEventLoop] = None
_saver: Optional[asyncio.Task] = None


def limits() -> Dict[str, Any]:
    return store.get()


def tier_limits(tier: str) -> Dict[str, Any]:
    return limits()["tiers"][tier if tier in TIERS else "guest"]


def tier_of(user) -> str:
    if user is None:
        return "guest"
    return "premium" if getattr(user, "tier", None) == "premium" else "basic"


async def tier_of_subject(subject: usage_tracking.UsageSubject) -> Optional[str]:
    if subject.kind == usage_tracking.KIND_GUEST:
        return "guest"
    if subject.kind != usage_tracking.KIND_USER or subject.user_id is None:
        return None
    from services.auth_service import get_cached_user
    return tier_of(await get_cached_user(subject.user_id))


def song_quota(user) -> Tuple[int, str]:
    row = tier_limits(tier_of(user))
    return int(row["songs"]), row["songs_period"]


def is_public_ip(ip: Optional[str]) -> bool:
    try:
        return ipaddress.ip_address(ip or "").is_global
    except ValueError:
        return False


def allow_request(identity: str, ip: Optional[str], cost: float) -> int:
    """0 when the request may go ahead, else the seconds to wait."""
    rules = limits()["requests"]
    allowed, retry = rate_limit_service.consume("requests", identity, rules["burst"],
                                                rules["burst"] / rules["per_second"], cost)
    if allowed and is_public_ip(ip):
        allowed, retry = rate_limit_service.consume("requests_ip", ip, rules["ip_burst"],  # type: ignore[arg-type]
                                                    rules["ip_burst"] / rules["ip_per_second"], cost)
    if not allowed:
        log_service.throttled(f"requests:{identity}", f"[LIMITS] {identity} ({ip or 'no ip'}) is over the request "
                                                      f"limit, asked to wait {retry}s")
    return 0 if allowed else max(int(retry), 1)


def request_cost(path: str) -> float:
    return limits()["requests"]["media_cost"] if path.startswith(UNCOMPRESSED_PATH_PREFIXES) else 1.0


def _window_state(spent: float, buckets: list, limit: float, now: float) -> Tuple[float, int]:
    if limit <= 0:
        return float("inf"), 0
    used = spent / limit
    if used < 1:
        return used, 0
    remaining = spent
    for frees_at, usd in buckets:
        remaining -= usd
        if remaining < limit:
            return used, max(int(frees_at - now), 60)
    return used, 60


def allowance(subject_key: str, tier: str, ip: Optional[str] = None, now: Optional[float] = None) -> Allowance:
    now = time.time() if now is None else now
    config, row = limits(), tier_limits(tier)
    checks = [(subject_key, "day", row["ai_usd_per_day"]), (subject_key, "month", row["ai_usd_per_month"])]
    if tier == "guest" and is_public_ip(ip):
        checks.append((f"ip:{ip}", "day", config["guest_ai_usd_per_ip_per_day"]))
    worst = (0.0, 0, "day", [])
    for key, window, limit in checks:
        spent, buckets = ledger.spent(key, window, now)
        used, resets_in = _window_state(spent, buckets, limit, now)
        if used > worst[0]:
            worst = (used, resets_in, window, buckets)
    used, resets_in, window, buckets = worst
    state = "out" if used >= 1 else "low" if used >= config["warn_at"] else "ok"
    if state == "low":
        resets_in = max(int(buckets[0][0] - now), 60) if buckets else WINDOWS[window]
    return Allowance(tier, state, used, window, resets_in)


async def allowance_for(user, guest_id: Optional[str], ip: Optional[str]) -> Allowance:
    if user is not None:
        return allowance(usage_tracking.user_subject(int(user.id)).key, tier_of(user), ip)
    return allowance(usage_tracking.subject_for_session(guest_id).key, "guest", ip)


async def allowance_for_session(session_id: str, user_id: Optional[int]) -> Optional[Allowance]:
    subject = usage_tracking.subject_for_session(session_id, user_id)
    tier = await tier_of_subject(subject)
    if tier is None:
        return None
    return allowance(subject.key, tier, usage_tracking.client_ip())


def _when(seconds: int) -> str:
    if seconds < 45 * 60:
        return "in a little while"
    if seconds < 6 * 3600:
        return "in a few hours"
    if seconds < 26 * 3600:
        return "by tomorrow"
    return "in a few days"


def _about(seconds: int) -> str:
    if seconds < 90 * 60:
        return f"about {max(seconds // 60, 1)} minutes"
    if seconds < 36 * 3600:
        return f"about {round(seconds / 3600)} hours"
    return f"about {round(seconds / 86400)} days"


def notice_text(allowance_: Allowance) -> str:
    period = "today's" if allowance_.window == "day" else "this month's"
    upsell = UPSELL[allowance_.tier]
    if allowance_.out:
        text = f"That's {period} AI time used up. It tops up in {_about(allowance_.resets_in_s)}."
    else:
        text = f"You've used most of {period} AI time. It tops up in {_about(allowance_.resets_in_s)}."
    return f"{text} {upsell}".strip()


def host_line(allowance_: Allowance) -> str:
    return random.choice(HOST_OUT_LINES[allowance_.tier]).format(when=_when(allowance_.resets_in_s))


def host_note(allowance_: Allowance) -> str:
    if allowance_.state != "low":
        return ""
    period = "today's" if allowance_.window == "day" else "this month's"
    upsell = HOST_UPSELL[allowance_.tier]
    return (
        f"ALLOWANCE: this listener ({tier_limits(allowance_.tier)['label']}) has used about "
        f"{min(round(allowance_.used * 100), 99)}% of {period} chat time with you; it tops up "
        f"{_when(allowance_.resets_in_s)}. Mention it once, briefly and kindly, in your own words, so running out "
        f"doesn't surprise them{'; ' + upsell if upsell else ''}. Never as an ad and never instead of answering; the "
        "conversation shows whether you already did."
    )


def note_spend(subject: usage_tracking.UsageSubject, ip: Optional[str], cost_usd: float, gpu_seconds: float) -> None:
    usd = cost_usd + gpu_seconds * limits()["gpu_second_usd"]
    if usd <= 0 or subject.kind not in (usage_tracking.KIND_USER, usage_tracking.KIND_GUEST):
        return
    ledger.add(subject.key, usd)
    if subject.kind == usage_tracking.KIND_GUEST and is_public_ip(ip):
        ledger.add(f"ip:{ip}", usd)
    if _loop is not None and not _loop.is_closed():
        _loop.call_soon_threadsafe(lambda: spawn(_review(subject, ip), name="allowance_review"))


async def _review(subject: usage_tracking.UsageSubject, ip: Optional[str]) -> None:
    tier = await tier_of_subject(subject)
    if tier is None:
        return
    current = allowance(subject.key, tier, ip)
    previous = _announced.get(subject.key, "ok")
    if current.state == previous:
        return
    _announced[subject.key] = current.state
    if current.state == "ok":
        return
    log_service.listener(f"{log_service.who(subject.session_id)}: AI allowance {current.state} "
                         f"({current.used:.0%} of the {current.window}, {tier})")
    from service_registry import services
    if services.websocket_service is not None:
        await services.websocket_service.broadcast_to_session(subject.session_id,
                                                              {"type": "allowance", "data": current.public()})


async def _save_loop() -> None:
    while True:
        await asyncio.sleep(LEDGER_SAVE_INTERVAL_S)
        await asyncio.to_thread(ledger.save)


async def start() -> None:
    global _loop, _saver
    _loop = asyncio.get_running_loop()
    store.load()
    ledger.load()
    usage_tracking.spend_hook = note_spend
    _saver = spawn(_save_loop(), name="allowance_ledger_save")
    tiers = ", ".join(f"{row['label']} ${row['ai_usd_per_day']}/day ${row['ai_usd_per_month']}/month"
                      for row in limits()["tiers"].values())
    log_service.success(f"✓ Listener limits: {tiers}")


async def stop() -> None:
    if _saver is not None:
        _saver.cancel()
    await asyncio.to_thread(ledger.save, True)
