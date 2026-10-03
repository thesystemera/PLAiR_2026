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
    recent_ids: deque = field(default_factory=lambda: deque(maxlen=max(1, settings.STINGS_ID_NO_REPEAT)))
    last_seen: float = 0.0
    started_at: float = 0.0

    def record(self, kind: str, at: float, midtrack: bool = False):
        self.last_kind = kind
        self.last_sting_at = at
        if kind == TIME_CHECK:
            self.last_time_check_at = at


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

    def held_by(self) -> str:
        reasons = [name for name, on in (("the listener is talking to the hosts", self.conversing),
                                         ("the hosts are on air", self.tts_busy),
                                         ("a Radio Mode break takes it", self.radio_blocked),
                                         ("stings are switched off", not (self.enabled and self.stings_pref)))
                   if on]
        return ", ".join(reasons) or "the station is off for this listener"


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


def choose(state: StingState, space_s: float, now: float, minute: Optional[int], candidates: Iterable[tuple],
           voiced: set, time_check_ready: bool, rng: Optional[random.Random] = None) -> Decision:
    rng = rng or random.Random()
    fitting = [(kind, weight, min_window) for kind, weight, min_window in candidates if space_s >= min_window]
    if not fitting:
        return Decision(None, "nothing fits the space")
    if (time_check_ready and state.last_kind != TIME_CHECK and time_check_due(state, now, minute)
            and any(kind == TIME_CHECK for kind, _w, _m in fitting)):
        return Decision(TIME_CHECK, "time check due")
    for pool in ([(k, w) for k, w, _m in fitting if k in voiced and k != TIME_CHECK],
                 [(k, w) for k, w, _m in fitting if k not in voiced]):
        fresh = [(k, w) for k, w in pool if k != state.last_kind] or pool
        kind = weighted_choice(fresh, rng)
        if kind is not None:
            return Decision(kind, "fits the space")
    return Decision(None, "nothing fits the space")
