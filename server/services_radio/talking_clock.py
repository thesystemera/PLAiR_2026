import random
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

from pydub import AudioSegment

from config.settings import settings

ONES = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
        "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty"}
INTRO_TEMPLATES = (
    "{station} time.",
    "The time on {station} is",
    "It's",
    "{station} time check.",
    "Right now on {station}, it's",
)
GAP_AFTER_INTRO_MS = 230
GAP_HOUR_MINUTE_MS = 45
GAP_BEFORE_DAYPART_MS = 150
MORNING = "In the morning."
AFTERNOON = "In the afternoon."
EVENING = "In the evening."
TONIGHT = "Tonight."
AM = "A.M."
PM = "P.M."
MIDNIGHT = "Midnight."
MIDDAY = "Midday."


def number_words(n: int) -> str:
    if not 0 <= n <= 59:
        raise ValueError(f"number out of range: {n}")
    if n < 20:
        return ONES[n]
    tens, ones = divmod(n, 10)
    return TENS[tens] if ones == 0 else f"{TENS[tens]}-{ONES[ones]}"


def intro_texts() -> Tuple[str, ...]:
    return tuple(t.format(station=settings.STATION_NAME_SPOKEN) for t in INTRO_TEMPLATES)


def hour_text(hour24: int) -> str:
    hour12 = hour24 % 12 or 12
    return f"{number_words(hour12).capitalize()}."


def minute_text(minute: int) -> str:
    if minute == 0:
        return "O'clock."
    if minute < 10:
        return f"Oh {ONES[minute]}."
    return f"{number_words(minute).capitalize()}."


def daypart_options(hour24: int, minute: int) -> Tuple[str, ...]:
    if minute == 0 and hour24 == 0:
        return MIDNIGHT, AM
    if minute == 0 and hour24 == 12:
        return MIDDAY, PM
    if hour24 < 5:
        return (AM,)
    if hour24 < 12:
        return MORNING, AM
    if hour24 < 17:
        return AFTERNOON, PM
    if hour24 < 21:
        return EVENING, PM
    return TONIGHT, PM


def all_part_texts() -> List[str]:
    texts = list(intro_texts())
    texts += [hour_text(h) for h in range(1, 13)]
    texts += [minute_text(m) for m in range(60)]
    texts += [MORNING, AFTERNOON, EVENING, TONIGHT, AM, PM, MIDNIGHT, MIDDAY]
    return list(dict.fromkeys(texts))


def core_texts(hour24: int, minute: int) -> List[str]:
    return [hour_text(hour24), minute_text(minute)]


@dataclass(frozen=True)
class ClockReading:
    hour24: int
    minute: int
    intro: str
    daypart: Optional[str]

    @property
    def parts(self) -> List[Tuple[str, int]]:
        parts = [(self.intro, GAP_AFTER_INTRO_MS), (hour_text(self.hour24), GAP_HOUR_MINUTE_MS),
                 (minute_text(self.minute), GAP_BEFORE_DAYPART_MS if self.daypart else 0)]
        if self.daypart:
            parts.append((self.daypart, 0))
        return parts

    @property
    def texts(self) -> List[str]:
        return [text for text, _gap in self.parts]

    @property
    def spoken(self) -> str:
        return " ".join(self.texts)


def choose_reading(hour24: int, minute: int, available, rng: Optional[random.Random] = None) -> Optional[ClockReading]:
    rng = rng or random
    if not all(available(text) for text in core_texts(hour24, minute)):
        return None
    intros = [text for text in intro_texts() if available(text)]
    if not intros:
        return None
    dayparts = [text for text in daypart_options(hour24, minute) if available(text)]
    choices = list(dayparts)
    if not choices or minute != 0 or hour24 not in (0, 12):
        choices.append(None)
    daypart = rng.choice(choices)
    return ClockReading(hour24=hour24, minute=minute, intro=rng.choice(intros), daypart=daypart)


def parse_reading(texts: List[str]) -> Tuple[int, int, Optional[str]]:
    words = {number_words(n).capitalize() + ".": n for n in range(1, 13)}
    minutes = {minute_text(m): m for m in range(60)}
    hour12 = minute = None
    daypart = None
    for text in texts:
        if hour12 is None and text in words:
            hour12 = words[text]
        elif hour12 is not None and minute is None and text in minutes:
            minute = minutes[text]
        elif text in (MORNING, AFTERNOON, EVENING, TONIGHT, AM, PM, MIDNIGHT, MIDDAY):
            daypart = text
    if hour12 is None or minute is None:
        raise ValueError(f"unparseable reading: {texts}")
    return hour12, minute, daypart


def daypart_matches(hour24: int, minute: int, daypart: Optional[str]) -> bool:
    if daypart is None:
        return True
    if daypart == MIDNIGHT:
        return hour24 == 0 and minute == 0
    if daypart == MIDDAY:
        return hour24 == 12 and minute == 0
    if daypart == AM:
        return hour24 < 12
    if daypart == PM:
        return hour24 >= 12
    if daypart == MORNING:
        return 5 <= hour24 < 12
    if daypart == AFTERNOON:
        return 12 <= hour24 < 17
    if daypart == EVENING:
        return 17 <= hour24 < 21
    if daypart == TONIGHT:
        return hour24 >= 21
    return False


def stitch(parts: List[Tuple[AudioSegment, int]]) -> AudioSegment:
    if not parts:
        return AudioSegment.empty()
    rate = parts[0][0].frame_rate
    out = AudioSegment.silent(duration=0, frame_rate=rate)
    for audio, gap_ms in parts:
        out += audio.set_frame_rate(rate).set_channels(1)
        if gap_ms:
            out += AudioSegment.silent(duration=gap_ms, frame_rate=rate)
    return out


def local_time(now_local: datetime) -> Tuple[int, int]:
    return now_local.hour, now_local.minute


NUMBER_WORDS = set(ONES) | set(TENS.values())
HOMOPHONES = {"for": "four", "to": "two", "too": "two", "won": "one", "ate": "eight", "tree": "three", "nigh": "nine"}


def _digit_words(token: str) -> List[str]:
    value = int(token)
    if len(token) == 2 and token.startswith("0"):
        return ["oh"] + number_words(value).split("-")
    if value < 60:
        return number_words(value).split("-")
    hours, minutes = divmod(value, 100)
    if 1 <= hours <= 12 and minutes < 60:
        return number_words(hours).split("-") + (number_words(minutes).split("-") if minutes else ["oclock"])
    return [token]


def spoken_numbers(text: str) -> List[str]:
    words = []
    for token in re.findall(r"[a-z]+|\d+", (text or "").lower().replace("o'clock", "oclock")):
        if token.isdigit():
            words.extend(_digit_words(token))
        else:
            words.append(HOMOPHONES.get(token, token))
    return [word for word in words if word in NUMBER_WORDS]


def numbers_match(expected_text: str, heard_text: str) -> bool:
    return spoken_numbers(expected_text) == spoken_numbers(heard_text)
