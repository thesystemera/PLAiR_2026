import random
from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Optional

from config.settings import settings

TIME_CHECK = "time_check"


@dataclass
class StingState:
    last_sting_at: Optional[float] = None
    last_kind: Optional[str] = None
    last_time_check_at: Optional[float] = None
    last_midtrack_at: Optional[float] = None
    breaks_since_sting: int = 0
    recent_ids: deque = field(default_factory=lambda: deque(maxlen=max(1, settings.STINGS_ID_NO_REPEAT)))
    last_seen: float = 0.0
    started_at: float = 0.0
    next_allowed_at: Optional[float] = None

    def gap_open(self, now: float) -> bool:
        if self.next_allowed_at is None:
            self.next_allowed_at = self.started_at + settings.STINGS_FIRST_DELAY_S
        return now >= self.next_allowed_at

    def record(self, kind: str, at: float, midtrack: bool = False):
        self.last_kind = kind
        if kind == TIME_CHECK:
            self.last_time_check_at = at
        if midtrack:
            self.last_midtrack_at = at
            return
        self.last_sting_at = at
        low = max(0.0, settings.STINGS_MIN_GAP_S)
        high = max(low, settings.STINGS_MAX_GAP_S)
        self.next_allowed_at = at + random.uniform(low, high)
        self.breaks_since_sting = 0


@dataclass(frozen=True)
class Gate:
    enabled: bool
    radio_mode: bool
    stings_pref: bool
    radio_blocked: bool = False
    conversing: bool = False
    tts_busy: bool = False

    def allowed(self) -> bool:
        if not (settings.STINGS_ENABLED and self.enabled and self.stings_pref):
            return False
        if not self.radio_mode and not settings.STINGS_OUTSIDE_RADIO_MODE:
            return False
        return not (self.radio_blocked or self.conversing or self.tts_busy)


@dataclass(frozen=True)
class Decision:
    kind: Optional[str]
    reason: str


def round_minute(minute: int) -> bool:
    step = max(1, settings.STINGS_TIME_CHECK_ROUND_MINUTES)
    return minute % step == 0


def time_check_due(state: StingState, now: float, minute: Optional[int]) -> bool:
    if minute is None:
        return False
    reference = state.last_time_check_at
    if reference is None:
        reference = state.started_at - settings.STINGS_TIME_CHECK_MIN_INTERVAL_S
    elapsed = now - reference
    if elapsed < settings.STINGS_TIME_CHECK_MIN_INTERVAL_S:
        return False
    return elapsed >= settings.STINGS_TIME_CHECK_MAX_INTERVAL_S or round_minute(minute)


def weighted_choice(kinds: Iterable[tuple], rng: random.Random) -> Optional[str]:
    options = [(kind, weight) for kind, weight in kinds if weight > 0]
    if not options:
        return None
    total = sum(weight for _, weight in options)
    roll = rng.random() * total
    for kind, weight in options:
        roll -= weight
        if roll <= 0:
            return kind
    return options[-1][0]


def decide_between_tracks(state: StingState, gate: Gate, window_s: float, now: float, minute: Optional[int],
                          candidates: Iterable[tuple], time_check_ready: bool,
                          rng: Optional[random.Random] = None) -> Decision:
    rng = rng or random.Random()
    if not gate.allowed():
        return Decision(None, "gated")
    if window_s < settings.STINGS_MIN_WINDOW_S:
        return Decision(None, "window_too_short")
    short = window_s < settings.STINGS_SHORT_WINDOW_S
    rotation = settings.STINGS_ROTATION_N > 0 and state.breaks_since_sting + 1 >= settings.STINGS_ROTATION_N
    if not short and not rotation:
        return Decision(None, "announcer_turn")
    if not state.gap_open(now):
        return Decision(None, "min_gap")
    candidates = [(kind, weight, min_window) for kind, weight, min_window in candidates]
    if (time_check_ready and state.last_kind != TIME_CHECK and time_check_due(state, now, minute)
            and any(kind == TIME_CHECK and window_s >= min_window for kind, _w, min_window in candidates)):
        return Decision(TIME_CHECK, "time_check_due")
    pool = [(kind, weight) for kind, weight, min_window in candidates
            if kind != TIME_CHECK and kind != state.last_kind and window_s >= min_window]
    kind = weighted_choice(pool, rng)
    if kind is None:
        return Decision(None, "nothing_fits")
    return Decision(kind, "short_window" if short else "rotation")


def decide_midtrack(state: StingState, gate: Gate, quiet_window_s: float, now: float,
                    candidates: Iterable[tuple], rng: Optional[random.Random] = None,
                    minute: Optional[int] = None, time_check_ready: bool = False) -> Decision:
    rng = rng or random.Random()
    if not settings.STINGS_MIDTRACK_ENABLED:
        return Decision(None, "midtrack_disabled")
    if not gate.radio_mode:
        return Decision(None, "radio_mode_only")
    if not gate.allowed():
        return Decision(None, "gated")
    if quiet_window_s < settings.STINGS_MIDTRACK_MIN_WINDOW_S:
        return Decision(None, "window_too_short")
    if state.last_midtrack_at is not None and now - state.last_midtrack_at < settings.STINGS_MIDTRACK_MIN_INTERVAL_S:
        return Decision(None, "midtrack_rate")
    if state.last_sting_at is not None and now - state.last_sting_at < settings.STINGS_MIDTRACK_CLEAR_S:
        return Decision(None, "recent_sting")
    if rng.random() >= settings.STINGS_MIDTRACK_PROBABILITY:
        return Decision(None, "probability")
    candidates = list(candidates)
    if (time_check_ready and state.last_kind != TIME_CHECK and time_check_due(state, now, minute)
            and any(kind == TIME_CHECK and quiet_window_s >= min_window for kind, _w, min_window in candidates)):
        return Decision(TIME_CHECK, "midtrack_time_check")
    pool = [(kind, weight) for kind, weight, min_window in candidates
            if kind != TIME_CHECK and kind != state.last_kind and quiet_window_s >= min_window]
    kind = weighted_choice(pool, rng)
    return Decision(kind, "midtrack") if kind else Decision(None, "nothing_fits")
