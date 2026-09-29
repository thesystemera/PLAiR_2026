import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Callable, Optional

import pytz

from config.settings import settings
from services import log_service
from services.task_utils import spawn

MAX_SESSIONS = 1000
MAX_TRIVIA_OFFERS = 8000
MAX_WEATHER_SUBJECTS = 5000
AIRING_MAX_CHARS = 220
RECENT_CATEGORY_MEMORY = 4
NON_ARTISTS = {"", "n/a", "unknown", "unknown artist", "ai generated", "various artists"}
TIMEZONE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+){0,2}$")

_CHANNEL_TAG = re.compile(r"\[(?:BROADCAST|TXT)\]")
_CUE = re.compile(r"~[^~\n]*~|\*[^*\n]*\*|%[^%\n]*%|\$[^$\n]*\$|@[\d.]+@|&[\d.]+&")
_SPACES = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([;,.:!?)])")
_EMPTY_PARENS = re.compile(r"\(\s*[;,]?\s*\)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def spoken_text(script: str) -> str:
    text = (script or "").split("[INTERNAL", 1)[0]
    text = _CUE.sub(" ", _CHANNEL_TAG.sub(" ", text))
    return _SPACE_BEFORE_PUNCT.sub(r"\1", _SPACES.sub(" ", text)).strip()


def fact_sentences(text: str) -> list[str]:
    cleaned = _SPACE_BEFORE_PUNCT.sub(r"\1", _SPACES.sub(" ", _EMPTY_PARENS.sub("", text or ""))).strip()
    return [sentence.strip() for sentence in _SENTENCE_END.split(cleaned) if len(sentence.strip()) > 20]


def artist_key(artist: Optional[str]) -> str:
    key = _SPACES.sub(" ", (artist or "").strip().lower())
    return "" if key in NON_ARTISTS else key


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "..."


def valid_timezone(name: Optional[str]) -> Optional[str]:
    candidate = (name or "").strip()
    if not candidate or len(candidate) > 64 or not TIMEZONE_PATTERN.match(candidate):
        return None
    return candidate if candidate in pytz.all_timezones_set else None


def menu_for_window(window_s: Optional[float]) -> tuple[int, int]:
    seconds = window_s or 0.0
    if seconds <= settings.DJ_BANK_SHORT_WINDOW_S:
        return 2, 1
    if seconds <= settings.DJ_BANK_MEDIUM_WINDOW_S:
        return 4, 2
    return 7, 3


def items_for_window(window_s: Optional[float]) -> int:
    seconds = window_s or 0.0
    if seconds <= settings.DJ_BANK_SHORT_WINDOW_S:
        return 1
    if seconds <= settings.DJ_BANK_MEDIUM_WINDOW_S:
        return 2
    return 3


@dataclass
class TalkingPoint:
    key: str
    category: str
    text: str
    priority: float
    untrusted: bool = False
    on_pick: Optional[Callable[[], None]] = None
    source: str = ""
    payload: object = None


class DJContentBank:
    def __init__(self):
        self._airings: "OrderedDict[str, deque]" = OrderedDict()
        self._trivia_offers: "OrderedDict[tuple[str, str], tuple[float, int]]" = OrderedDict()
        self._prefetching: set[str] = set()
        self._offered: "OrderedDict[str, dict[str, float]]" = OrderedDict()
        self._recent_categories: "OrderedDict[str, deque]" = OrderedDict()
        self._timezones: "OrderedDict[str, tuple[str, float]]" = OrderedDict()
        self._weather_cues: "OrderedDict[str, tuple[float, str, str]]" = OrderedDict()

    @staticmethod
    def _touch(store: OrderedDict, key, value, limit: int = MAX_SESSIONS) -> None:
        store.pop(key, None)
        store[key] = value
        while len(store) > limit:
            store.popitem(last=False)

    def record_airing(self, session_id: Optional[str], script: str) -> None:
        if not settings.DJ_AIRED_MEMORY_ENABLED or not session_id:
            return
        text = spoken_text(script)
        if not text:
            return
        entries = self._airings.get(session_id)
        if entries is None or entries.maxlen != max(1, settings.DJ_AIRED_MEMORY_ITEMS):
            entries = deque(entries or (), maxlen=max(1, settings.DJ_AIRED_MEMORY_ITEMS))
        entries.append((time.time(), clip(text, AIRING_MAX_CHARS)))
        self._touch(self._airings, session_id, entries)

    def recent_airings(self, session_id: Optional[str]) -> list[str]:
        entries = self._airings.get(session_id or "")
        if not entries:
            return []
        cutoff = time.time() - settings.DJ_AIRED_MEMORY_TTL_S
        return [text for aired_at, text in entries if aired_at >= cutoff]

    def forget_session(self, session_id: str) -> None:
        for store in (self._airings, self._offered, self._recent_categories, self._timezones):
            store.pop(session_id, None)
        for offer_key in [k for k in self._trivia_offers if k[0] == session_id]:
            self._trivia_offers.pop(offer_key, None)

    def set_session_timezone(self, session_id: Optional[str], timezone_name: Optional[str]) -> Optional[str]:
        zone = valid_timezone(timezone_name)
        if session_id and zone:
            self._touch(self._timezones, session_id, (zone, time.time()))
        return zone

    def session_timezone(self, session_id: Optional[str]) -> Optional[str]:
        if not settings.DJ_GUEST_TIMEZONE_ENABLED:
            return None
        entry = self._timezones.get(session_id or "")
        return entry[0] if entry else None

    def active_session_timezones(self, max_age_s: float) -> dict[str, str]:
        cutoff = time.time() - max_age_s
        return {session: zone for session, (zone, seen) in self._timezones.items() if seen >= cutoff}

    def set_weather_cue(self, subject: str, key: str, text: str) -> None:
        self._touch(self._weather_cues, subject, (time.time(), key, text), MAX_WEATHER_SUBJECTS)

    def weather_cue(self, subject: Optional[str]) -> Optional[tuple[str, str]]:
        cue = self._weather_cues.get(subject or "")
        if not cue or time.time() - cue[0] > settings.DJ_WEATHER_CUE_TTL_S:
            return None
        return cue[1], cue[2]

    def prefetch_artist(self, web_service, artist: Optional[str]):
        if not settings.DJ_TRIVIA_PREFETCH_ENABLED or web_service is None:
            return None
        key = artist_key(artist)
        if not key or key in self._prefetching or web_service.cached_artist_biography(artist) is not None:
            return None
        self._prefetching.add(key)
        return spawn(self._prefetch(web_service, artist, key), name="dj_trivia_prefetch")

    async def _prefetch(self, web_service, artist: str, key: str) -> None:
        try:
            biography = await web_service.retrieve_artist_biography(artist)
            log_service.external(f"Trivia prefetch: {artist} -> {'found' if biography else 'nothing'}")
        except Exception as e:
            log_service.warning(f"Trivia prefetch failed for '{artist}': {type(e).__name__}: {e}")
        finally:
            self._prefetching.discard(key)

    def trivia_point(self, session_id: Optional[str], artist: Optional[str], web_service) -> Optional[TalkingPoint]:
        if not settings.DJ_TRIVIA_PREFETCH_ENABLED or web_service is None:
            return None
        key = artist_key(artist)
        if not key:
            return None
        biography = web_service.cached_artist_biography(artist)
        if biography is None:
            self.prefetch_artist(web_service, artist)
            return None
        sentences = fact_sentences(biography)
        if not sentences:
            return None

        offer_key = (session_id or "", key)
        last_at, offered = self._trivia_offers.get(offer_key, (0.0, 0))
        if offered and time.time() - last_at < settings.DJ_TRIVIA_ARTIST_COOLDOWN_S:
            return None

        limit = settings.DJ_TRIVIA_MAX_CHARS
        start = offered % len(sentences)
        picked: list[str] = []
        for sentence in sentences[start:] + sentences[:start]:
            if picked and len(" ".join(picked + [sentence])) > limit:
                break
            picked.append(sentence)

        def mark_offered():
            self._touch(self._trivia_offers, offer_key, (time.time(), offered + len(picked)), MAX_TRIVIA_OFFERS)

        return TalkingPoint(key=f"trivia:{key}:{start}", category="trivia",
                            text=f"About {artist} (credited on the next track): {clip(' '.join(picked), limit)}",
                            priority=0.7, untrusted=True, on_pick=mark_offered, source="wikipedia")

    def mark_offered(self, session_id: Optional[str], keys) -> None:
        session = session_id or ""
        now = time.time()
        offered = {k: at for k, at in self._offered.get(session, {}).items() if now - at < settings.DJ_BANK_REPEAT_S}
        for key in keys or ():
            offered[key] = now
        self._touch(self._offered, session, offered)

    def select_talking_points(self, session_id: Optional[str], candidates: list[TalkingPoint],
                              window_s: Optional[float], menu: bool = False) -> list[TalkingPoint]:
        session = session_id or ""
        now = time.time()
        offered = {k: at for k, at in self._offered.get(session, {}).items() if now - at < settings.DJ_BANK_REPEAT_S}
        recent = self._recent_categories.get(session) or deque(maxlen=RECENT_CATEGORY_MEMORY)

        def score(point: TalkingPoint) -> float:
            if point.category not in recent:
                return point.priority
            return point.priority - (0.6 if recent[-1] == point.category else 0.3)

        count = menu_for_window(window_s)[0] if menu else items_for_window(window_s)
        per_category = 2 if menu else 1
        chosen: list[TalkingPoint] = []
        for point in sorted((p for p in candidates if p.text and p.key not in offered),
                            key=score, reverse=True):
            if len(chosen) >= count:
                break
            if sum(1 for c in chosen if c.category == point.category) >= per_category:
                continue
            chosen.append(point)

        for point in chosen:
            offered[point.key] = now
            recent.append(point.category)
            if point.on_pick:
                point.on_pick()
        self._touch(self._offered, session, offered)
        self._touch(self._recent_categories, session, recent)
        return chosen


content_bank = DJContentBank()
