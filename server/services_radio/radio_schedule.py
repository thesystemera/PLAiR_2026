from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import pytz

from config.settings import settings

SEGMENT_PREF_KEYS = ("news", "city", "local", "community", "features")
EXTRA_PREF_KEYS = ("stings", "reviews")
SERVED_MEMORY_S = 6 * 3600


def feature_intervals() -> tuple:
    intervals = tuple(sorted({i for i in settings.RADIO_FEATURE_INTERVALS_MIN if 5 <= i <= 120}))
    return intervals or (20,)


def default_feature_interval() -> int:
    intervals = feature_intervals()
    wanted = settings.RADIO_DEFAULT_FEATURE_INTERVAL_MIN
    return wanted if wanted in intervals else min(intervals, key=lambda i: abs(i - wanted))


@dataclass
class RadioPrefs:
    enabled: bool = False
    news: bool = True
    city: bool = True
    local: bool = True
    community: bool = True
    features: bool = True
    stings: bool = True
    reviews: bool = True
    feature_interval_min: int = field(default_factory=default_feature_interval)

    def to_dict(self) -> dict:
        return asdict(self)

    def allows(self, pref_key: str) -> bool:
        return self.enabled and bool(getattr(self, pref_key, False))


def normalize_prefs(raw) -> RadioPrefs:
    prefs = RadioPrefs()
    if not isinstance(raw, dict):
        return prefs
    for key in ("enabled",) + SEGMENT_PREF_KEYS + EXTRA_PREF_KEYS:
        value = raw.get(key)
        if isinstance(value, bool):
            setattr(prefs, key, value)
    interval = raw.get("feature_interval_min")
    if isinstance(interval, (int, float)) and not isinstance(interval, bool):
        allowed = feature_intervals()
        prefs.feature_interval_min = min(allowed, key=lambda i: abs(i - int(interval)))
    return prefs


def zone(tz_name: Optional[str]):
    try:
        return pytz.timezone(tz_name) if tz_name else pytz.utc
    except pytz.UnknownTimeZoneError:
        return pytz.utc


def local_now(tz_name: Optional[str], at: float) -> datetime:
    return datetime.fromtimestamp(at, tz=timezone.utc).astimezone(zone(tz_name))


@dataclass(frozen=True)
class Slot:
    key: str
    kind: Optional[str]
    due_at: float
    expires_at: Optional[float]
    priority: int
    clock: bool


@dataclass(frozen=True)
class ClockRule:
    kind: str
    pref: str
    minutes: tuple
    priority: int
    hours: Optional[tuple] = None


def clock_slots(rules: Iterable[ClockRule], prefs: RadioPrefs, tz_name: Optional[str], around: float) -> list[Slot]:
    slots = []
    local = local_now(tz_name, around)
    for rule in rules:
        if not prefs.allows(rule.pref):
            continue
        for minute in rule.minutes:
            base = local.replace(minute=minute % 60, second=0, microsecond=0)
            for offset in (-1, 0, 1):
                at = base + timedelta(hours=offset)
                if rule.hours is not None and at.hour not in rule.hours:
                    continue
                due_at = at.timestamp()
                slots.append(Slot(
                    key=f"{rule.kind}:{at.strftime('%Y-%m-%dT%H:%M')}",
                    kind=rule.kind,
                    due_at=due_at,
                    expires_at=due_at + settings.RADIO_CLOCK_LATE_S,
                    priority=rule.priority,
                    clock=True,
                ))
    return slots


def due_clock_slot(slots: Iterable[Slot], boundary_at: float, served) -> Optional[Slot]:
    candidates = [
        slot for slot in slots
        if slot.key not in served
        and slot.due_at - settings.RADIO_CLOCK_EARLY_S <= boundary_at <= (slot.expires_at or boundary_at)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda slot: (-slot.priority, slot.due_at))


def clock_due_soon(slots: Iterable[Slot], boundary_at: float, served, window_s: float) -> bool:
    return any(
        slot.key not in served and 0 < slot.due_at - settings.RADIO_CLOCK_EARLY_S - boundary_at <= window_s
        for slot in slots
    )


def feature_slot(prefs: RadioPrefs, feature_prefs_enabled: bool, anchor: float,
                 boundary_at: float) -> Optional[Slot]:
    if not prefs.enabled or not feature_prefs_enabled:
        return None
    due_at = anchor + prefs.feature_interval_min * 60
    if boundary_at < due_at - settings.RADIO_FEATURE_EARLY_S:
        return None
    return Slot(key=f"feature:{int(due_at)}", kind=None, due_at=due_at, expires_at=None, priority=0, clock=False)


def choose_slot(rules: Iterable[ClockRule], prefs: RadioPrefs, feature_prefs_enabled: bool, tz_name: Optional[str],
                boundary_at: float, last_break_at: Optional[float], feature_anchor: float, served) -> Optional[Slot]:
    if not prefs.enabled:
        return None
    if last_break_at is not None and boundary_at - last_break_at < settings.RADIO_MIN_GAP_S:
        return None
    rules = list(rules)
    slots = clock_slots(rules, prefs, tz_name, boundary_at)
    clock = due_clock_slot(slots, boundary_at, served)
    if clock is not None:
        return clock
    if clock_due_soon(slots, boundary_at, served, settings.RADIO_FEATURE_YIELD_S):
        return None
    return feature_slot(prefs, feature_prefs_enabled, feature_anchor, boundary_at)


def prune_served(served: dict, now: float) -> dict:
    return {key: at for key, at in served.items() if now - at < SERVED_MEMORY_S}


def feature_rotation(kinds: list[str], turn: int) -> list[str]:
    if not kinds:
        return []
    start = turn % len(kinds)
    return kinds[start:] + kinds[:start]
