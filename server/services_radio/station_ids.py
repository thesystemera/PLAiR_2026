import difflib
import random
import re
from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Tuple

from config.settings import settings

ANY_HOUR = tuple(range(24))
MORNING_HOURS = tuple(range(5, 12))
DAY_HOURS = tuple(range(9, 18))
EVENING_HOURS = tuple(range(17, 22))
NIGHT_HOURS = (21, 22, 23, 0, 1, 2, 3, 4)
CITY_NAME = re.compile(r"^[A-Za-z][A-Za-z .'-]{1,40}$")


@dataclass(frozen=True)
class StationLine:
    key: str
    template: str
    hours: Tuple[int, ...] = ANY_HOUR
    short: bool = False

    @property
    def needs_city(self) -> bool:
        return "{city}" in self.template

    def text(self, city: Optional[str] = None) -> str:
        return self.template.format(station=settings.STATION_NAME_SPOKEN, city=city or "")

    def fits(self, hour24: Optional[int]) -> bool:
        return hour24 is None or hour24 in self.hours


LINES: Tuple[StationLine, ...] = (
    StationLine("listening", "You're listening to {station}.", short=True),
    StationLine("this_is", "This is {station}.", short=True),
    StationLine("alt_radio", "{station}. Alternative radio.", short=True),
    StationLine("tuned", "You're tuned to {station}.", short=True),
    StationLine("dial", "{station}. Left of the dial.", short=True),
    StationLine("turn_up", "{station}. Turn it up.", short=True),
    StationLine("back_to_back", "Back to back on {station}.", short=True),
    StationLine("more_music", "More music, right now, on {station}.", short=True),
    StationLine("mind_of_its_own", "{station}. Radio with a mind of its own."),
    StationLine("talks_back", "{station}. The station that talks back."),
    StationLine("deep_cuts", "{station}. Where the deep cuts live."),
    StationLine("rest_of_us", "{station}. Music for the rest of us."),
    StationLine("still_weird", "Still here. Still weird. {station}."),
    StationLine("keep_it_weird", "{station}. Keep it weird.", short=True),
    StationLine("stay_tuned", "Stay tuned. {station}.", short=True),
    StationLine("dont_touch", "Don't touch that dial. {station}."),
    StationLine("request_line", "Got something to say? Talk to the hosts. {station}."),
    StationLine("independent", "Independent. Alternative. {station}."),
    StationLine("no_filler", "Less filler, more killer. {station}."),
    StationLine("signal", "Signal strong. {station}.", short=True),
    StationLine("loud", "Loud, strange and local. {station}."),
    StationLine("next_up", "And now, more {station}.", short=True),
    StationLine("the_sound", "The sound of {station}.", short=True),
    StationLine("on_air", "{station}. On air.", short=True),
    StationLine("your_voice", "{station}. Your music, your voice.", short=True),
    StationLine("your_voice_lead", "Your music. Your voice. {station}.", short=True),
    StationLine("acronym", "{station}. Personalised, localised, adaptive, interactive radio."),
    StationLine("acronym_lead", "Personalised. Localised. Adaptive. Interactive. This is {station}."),
    StationLine("acronym_city", "Personalised, localised, adaptive, interactive radio. {station}, in {city}."),
    StationLine("morning", "Good morning. You're listening to {station}.", MORNING_HOURS),
    StationLine("afternoon", "Your afternoon, on {station}.", DAY_HOURS),
    StationLine("evening", "Evenings on {station}.", EVENING_HOURS, short=True),
    StationLine("late", "Late nights on {station}.", NIGHT_HOURS, short=True),
    StationLine("small_hours", "Keeping the small hours company. {station}.", (0, 1, 2, 3, 4)),
    StationLine("night_owls", "For the night owls. {station}.", NIGHT_HOURS),
    StationLine("city", "{station}, in {city}.", short=True),
    StationLine("city_hello", "Hello, {city}. This is {station}."),
    StationLine("city_listening", "{city}, you're listening to {station}."),
    StationLine("city_live", "{station}. Live in {city}.", short=True),
    StationLine("city_morning", "Good morning, {city}. This is {station}.", MORNING_HOURS),
    StationLine("city_night", "Late night, {city}. {station}.", NIGHT_HOURS),
)
LINES_BY_KEY = {line.key: line for line in LINES}


def clean_city(city: Optional[str]) -> Optional[str]:
    city = (city or "").strip()
    return city if CITY_NAME.fullmatch(city) else None


def generic_texts() -> List[str]:
    return [line.text() for line in LINES if not line.needs_city]


def city_texts(city: str) -> List[str]:
    return [line.text(city) for line in LINES if line.needs_city]


def _normalized(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()


def best_cached_take(wanted: str, cached: Iterable[Tuple[str, Optional[str]]], city: Optional[str]) -> Optional[str]:
    target = _normalized(wanted)
    best, best_score = None, -1.0
    for text, text_city in cached:
        if text_city and text_city != city:
            continue
        score = difflib.SequenceMatcher(None, target, _normalized(text)).ratio()
        if score > best_score:
            best, best_score = text, score
    return best


def eligible_lines(hour24: Optional[int], city: Optional[str], short_only: bool = False) -> List[StationLine]:
    lines = []
    for line in LINES:
        if line.needs_city and (not city or not settings.STINGS_CITY_LINES_ENABLED):
            continue
        if short_only and not line.short:
            continue
        if line.fits(hour24):
            lines.append(line)
    return lines


def pick_line(hour24: Optional[int], city: Optional[str], recent: deque, is_cached: Callable[[str], bool],
              rng: Optional[random.Random] = None, short_only: bool = False) -> Optional[Tuple[StationLine, str]]:
    rng = rng or random
    lines = eligible_lines(hour24, city, short_only)
    if not lines:
        return None
    fresh = [line for line in lines if line.key not in recent] or [line for line in lines if not recent or line.key != recent[-1]]
    if not fresh:
        return None
    cached = [line for line in fresh if is_cached(line.text(city))]
    pool = cached or fresh
    weights = [2.0 if line.needs_city or line.hours != ANY_HOUR else 1.0 for line in pool]
    line = rng.choices(pool, weights=weights, k=1)[0]
    return line, line.text(city)
