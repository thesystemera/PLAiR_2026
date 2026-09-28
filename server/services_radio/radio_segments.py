import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import pytz

from services import log_service
from services.user_content_database_service import coarse_location
import time

from config import settings
from services_radio import area_geocode, area_signals, dj_bank_sources, pulse_agent
from services_radio import regional_knowledge as regional_kb
from services_radio.dj_content_bank import artist_key, clip, fact_sentences
from services_radio.external_news_service import resolve_country
from services_radio.radio_schedule import ClockRule

BUILD_TIMEOUT_S = 20.0
BIOGRAPHY_WAIT_S = 12.0
NEWS_ITEMS = 5
TRIVIA_MAX_CHARS = 650


@dataclass
class SegmentContext:
    session_id: str
    user_id: Optional[int]
    user: Any
    tz_name: Optional[str]
    region: Optional[regional_kb.Region]
    now_local: datetime
    next_track: dict
    upcoming_track: dict
    current_track: dict
    dj_service: Any
    async_session_maker: Any
    catalog_service: Any
    aired: set = field(default_factory=set)
    location: Any = None
    prefs: Any = None

    @property
    def is_guest(self) -> bool:
        return self.user is None

    @property
    def precise(self) -> bool:
        return bool(self.location is not None and self.location.precise)

    @property
    def coords(self) -> Optional[tuple]:
        if self.location is not None and self.location.coords:
            return tuple(self.location.coords)
        if self.region is not None and self.region.center:
            return tuple(self.region.center)
        return None

    @property
    def aired_regional_ids(self) -> set:
        return {key.split(":", 1)[1] for key in self.aired if key.startswith("regional:")}

    @property
    def place_name(self) -> str:
        if self.region is not None:
            return self.region.name
        if self.location is not None and self.location.city:
            return self.location.city
        location = getattr(self.user, "location", None) if self.user is not None else None
        return (location or "").split(",")[0].strip()


@dataclass
class SegmentContent:
    facts: list
    keys: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    shareable: bool = False
    moods: tuple = ()
    title: str = ""
    on_air: list = field(default_factory=list)

    def facts_text(self) -> str:
        return "\n".join(f"- {fact}" for fact in self.facts if fact)

    def fingerprint(self) -> str:
        return hashlib.sha1("\n".join(self.facts + self.notes).encode("utf-8", "ignore")).hexdigest()[:16]


class RadioSegment:
    kind = ""
    label = ""
    pref = ""
    clock_minutes: Optional[tuple] = None
    clock_hours: Optional[tuple] = None
    priority = 50
    target_s = (35, 55)
    guest_allowed = True
    tease_next_track = True
    moods: tuple = ()
    instruction = ""

    @property
    def is_clock(self) -> bool:
        return bool(self.clock_minutes)

    def clock_rule(self) -> Optional[ClockRule]:
        if not self.is_clock:
            return None
        return ClockRule(kind=self.kind, pref=self.pref, minutes=tuple(self.clock_minutes), priority=self.priority,
                         hours=self.clock_hours)

    async def build(self, ctx: SegmentContext) -> Optional[SegmentContent]:
        raise NotImplementedError

    def word_range(self, words_per_second: float) -> tuple:
        low, high = self.target_s
        return int(low * words_per_second), int(high * words_per_second)


_registry: dict[str, RadioSegment] = {}


def register(segment: RadioSegment) -> RadioSegment:
    _registry[segment.kind] = segment
    return segment


def get_segment(kind: str) -> Optional[RadioSegment]:
    return _registry.get(kind)


def segments() -> list[RadioSegment]:
    return list(_registry.values())


def clock_rules(disabled: frozenset = frozenset()) -> list[ClockRule]:
    return [rule for segment in segments() if segment.kind not in disabled
            for rule in (segment.clock_rule(),) if rule is not None]


def feature_kinds(prefs, disabled: frozenset = frozenset()) -> list[str]:
    return [segment.kind for segment in segments()
            if not segment.is_clock and segment.kind not in disabled and prefs.allows(segment.pref)]


def _key(prefix: str, text: str) -> str:
    return f"{prefix}:{hashlib.sha1(text.lower().encode('utf-8', 'ignore')).hexdigest()[:12]}"


def _event_line(item, tz_name: Optional[str]) -> str:
    venue = item.text.split(",", 1)[0].strip() if item.text else ""
    when = ""
    if item.starts_at:
        try:
            local = item.starts_at.astimezone(pytz.timezone(tz_name)) if tz_name else item.starts_at
        except pytz.UnknownTimeZoneError:
            local = item.starts_at
        when = local.strftime("%A %d %B, %I:%M %p").replace(" 0", " ")
    tags = " / ".join(item.tags[1:] or item.tags)
    parts = [item.title]
    if venue:
        parts.append(f"at {venue}")
    if when:
        parts.append(when)
    line = ", ".join(parts)
    return f"{line} ({tags})" if tags else line


async def _taste(ctx: SegmentContext):
    return await dj_bank_sources.listener_taste(ctx.user, ctx.user_id, ctx.session_id, ctx.async_session_maker,
                                                ctx.catalog_service)


async def _neighbourhood(ctx: SegmentContext) -> str:
    if ctx.location is not None and ctx.location.description:
        return ctx.location.description
    coords = ctx.location.coords if ctx.location is not None else None
    if not coords:
        return ""
    try:
        around = await area_geocode.describe(coords[0], coords[1])
    except Exception as e:
        log_service.warning(f"[RADIO] neighbourhood lookup failed: {type(e).__name__}: {e}")
        return ""
    return (around or {}).get("description") or ""


class NewsSegment(RadioSegment):
    kind = "news"
    label = "News"
    pref = "news"
    clock_minutes = (0,)
    priority = 100
    target_s = (40, 60)
    tease_next_track = False
    moods = ("news", "urgent", "steady")
    instruction = (
        "THE TOP-OF-THE-HOUR NEWS BULLETIN. [LEO] reads a tight bulletin of the headlines in SEGMENT DATA, most "
        "important first: one or two sentences per story, plain facts, names and places exactly as given. [TARA] "
        "reacts briefly between stories. Open with a quick ident (\"PLAiR news, it's {hour}\"), close by handing back "
        "to the music. Keep the jokes off serious or tragic stories. Never add facts, numbers or quotes that are not "
        "in SEGMENT DATA."
    )

    async def build(self, ctx: SegmentContext) -> Optional[SegmentContent]:
        news_service = getattr(ctx.dj_service, "news_service", None)
        if news_service is None:
            return None
        location = getattr(ctx.user, "location", None) if ctx.user is not None else None
        home = ctx.location.country_code if ctx.location is not None else ""
        country = home or resolve_country(location) or (ctx.region.country if ctx.region else None)
        stored = bool(getattr(news_service, "store_enabled", False))
        extra = {"subject": ctx.session_id} if stored and ctx.session_id else {}
        stories = []
        for query in ("NATION", "WORLD"):
            try:
                articles, _ = await news_service.get_top_news(query=query, is_topic=True, country=country,
                                                              top_n=8 if stored else 6, **extra)
            except Exception as e:
                log_service.warning(f"[RADIO] news fetch failed ({query}): {type(e).__name__}: {e}")
                articles = []
            stories.extend(articles or [])
        facts, keys, seen, picked = [], [], set(), []
        for article in stories:
            title = (article.get("title") or "").strip()
            if not title or article.get("aired"):
                continue
            key = _key("news", title)
            story = ("id", article.get("id")) if article.get("id") is not None else key
            if key in seen or key in ctx.aired or story in seen:
                continue
            seen.add(story)
            picked.append(article)
            seen.add(key)
            source = ((article.get("source") or {}).get("name") or "").strip()
            summary = clip((article.get("description") or "").strip(), 220)
            line = title + (f" ({source})" if source else "")
            if summary and summary.lower() not in title.lower():
                line += f": {summary}"
            facts.append(line)
            keys.append(key)
            if len(facts) >= NEWS_ITEMS:
                break
        if len(facts) < 2:
            return None
        notes = [f"Bulletin time: {ctx.now_local.strftime('%I:%M %p').lstrip('0')} local"]
        if country:
            notes.append(f"Listener's country: {country}")
        on_air = [news_service.airing_marker(ctx.session_id, picked)] if stored and ctx.session_id else []
        return SegmentContent(facts=facts, keys=keys, notes=notes, shareable=True, moods=self.moods,
                              title=f"News at {ctx.now_local.strftime('%I %p').lstrip('0')}", on_air=on_air)


class CitySegment(RadioSegment):
    kind = "city"
    label = "Weather & City"
    pref = "city"
    clock_minutes = (30,)
    priority = 80
    target_s = (35, 50)
    tease_next_track = True
    moods = ("weather", "chill", "bright")
    instruction = (
        "THE HALF-HOUR CITY UPDATE for {place}. [TARA] leads: the weather right now and what the next few hours "
        "look like, in everyday words (\"grab a jacket\", \"patio weather\"), then any cues in SEGMENT DATA (sunset or "
        "sunrise, air quality, pollen, the neighbourhood), then one thing on tonight if listed. [LEO] reacts "
        "and banters. Numbers only as given. Close by handing back to the music."
    )

    async def build(self, ctx: SegmentContext) -> Optional[SegmentContent]:
        facts, keys, on_air = [], [], []
        coords = ctx.coords
        web_service = getattr(ctx.dj_service, "web_service", None)
        if coords and web_service is not None:
            for forecast in ("current", "today"):
                try:
                    report = await web_service.retrieve_weather_data(coords[0], coords[1], forecast)
                except Exception as e:
                    log_service.warning(f"[RADIO] weather failed ({forecast}): {type(e).__name__}: {e}")
                    report = None
                if report:
                    facts.append(f"Weather {forecast}: {clip(' '.join(report.split()), 420)}")
        if coords:
            for point in dj_bank_sources.sky_points(coords[0], coords[1], ctx.tz_name):
                facts.append(point.text)
                keys.append(point.key)
        if ctx.precise:
            points = await area_signals.talking_points(
                area_signals.location_context(ctx.location, ctx.tz_name, ctx.session_id or ctx.user_id))
            for point in points[:3]:
                if point.text:
                    facts.append(point.text)
                    keys.append(point.key)
                    if point.on_pick:
                        on_air.append(point.on_pick)
        neighbourhood = await _neighbourhood(ctx)
        if neighbourhood:
            facts.append(f"Listener is around: {neighbourhood} (mention the neighbourhood casually, never an address)")
        regional = regional_kb.get_regional_knowledge()
        if regional is not None and ctx.region is not None and regional_kb.EVENTS_COLLECTOR_ENABLED:
            now = datetime.now(timezone.utc)
            tonight = await regional.query(ctx.region, (regional_kb.KIND_EVENT,), await _taste(ctx),
                                           window=(now, now + timedelta(hours=18)), limit=1,
                                           exclude=ctx.aired_regional_ids, min_score=0.1)
            for _, item in tonight:
                facts.append(f"On tonight: {_event_line(item, ctx.tz_name)}")
                keys.append(f"regional:{item.item_id}")
        if not facts:
            return None
        notes = [f"City: {ctx.place_name or 'unknown'}",
                 f"Local time: {ctx.now_local.strftime('%I:%M %p').lstrip('0')}"]
        return SegmentContent(facts=facts, keys=keys, notes=notes, shareable=ctx.is_guest and not ctx.precise,
                              moods=self.moods,
                              title=f"{ctx.place_name} update" if ctx.place_name else "City update", on_air=on_air)


class LocalSegment(RadioSegment):
    kind = "local"
    label = "Local & Gigs"
    pref = "local"
    target_s = (40, 60)
    moods = ("upbeat", "indie", "warm")
    instruction = (
        "LOCAL SCENE & GIGS around {place}. [LEO] leads a proper feature on two or three things from SEGMENT "
        "DATA: gigs and events (say the day and the venue exactly as given, and why it fits this listener's taste) "
        "and, if listed, a local spot worth checking out. [TARA] reacts, has opinions, asks the obvious question. "
        "Never invent prices, times or line-ups. Close by handing back to the music."
    )

    async def build(self, ctx: SegmentContext) -> Optional[SegmentContent]:
        regional = regional_kb.get_regional_knowledge()
        if regional is None or ctx.region is None:
            return None
        taste = await _taste(ctx)
        facts, keys = [], []
        if regional_kb.EVENTS_COLLECTOR_ENABLED:
            now = datetime.now(timezone.utc)
            for _, item in await regional.query(ctx.region, (regional_kb.KIND_EVENT,), taste,
                                                window=(now, now + timedelta(days=10)), limit=3,
                                                exclude=ctx.aired_regional_ids, min_score=0.1, record_hit=True):
                facts.append(f"Gig/event: {_event_line(item, ctx.tz_name)}")
                keys.append(f"regional:{item.item_id}")
        if regional_kb.PLACES_COLLECTOR_ENABLED:
            for _, item in await regional.query(ctx.region, (regional_kb.KIND_PLACE,), taste, limit=1,
                                                exclude=ctx.aired_regional_ids):
                hydrated = await regional.hydrate(item)
                if hydrated and hydrated.title:
                    kind = f", {hydrated.text}" if hydrated.text else ""
                    facts.append(f"Local spot: {hydrated.title}{kind} (via Google Maps)")
                    keys.append(f"regional:{item.item_id}")
        if not facts:
            return None
        neighbourhood = await _neighbourhood(ctx)
        notes = [f"City: {ctx.place_name}"]
        if neighbourhood:
            notes.append(f"Listener is around: {neighbourhood}")
        if taste.genres:
            notes.append(f"Listener's taste: {', '.join(list(taste.genres)[:5])}")
        return SegmentContent(facts=facts, keys=keys, notes=notes, moods=self.moods,
                              title=f"What's on in {ctx.place_name}")


class CommunitySegment(RadioSegment):
    kind = "community"
    label = "Community"
    pref = "community"
    target_s = (40, 60)
    moods = ("warm", "community", "chill")
    instruction = (
        "COMMUNITY CORNER. The hosts celebrate the PLAiR crowd: one or two listener shoutouts from SEGMENT DATA "
        "(paraphrase them warmly, say who and roughly where from), plus any station or listener stats listed. You may "
        "play AT MOST ONE shoutout recording by inserting its exact audio path from SEGMENT DATA wrapped in $ signs "
        "with no spaces ($/api/user_content/shoutouts/audio/1/123.mp3$), then react to it. Never read out anything "
        "private. Close by handing back to the music."
    )

    async def build(self, ctx: SegmentContext) -> Optional[SegmentContent]:
        facts, keys = [], []
        search = getattr(ctx.dj_service, "user_content_vector_search_service", None)
        if search is not None:
            user_location = ctx.location.coords if ctx.location is not None else None
            try:
                shoutouts = await search.search(query="Recent community messages and shoutouts", n_results=6,
                                                user_location=user_location, use_ai_analysis=False)
            except Exception as e:
                log_service.warning(f"[RADIO] shoutout search failed: {type(e).__name__}: {e}")
                shoutouts = []
            for shoutout in shoutouts or []:
                audio_url = (shoutout.get("audio_url") or "").strip()
                transcription = " ".join((shoutout.get("transcription") or "").split())
                key = f"shoutout:{audio_url or _key('t', transcription)}"
                if not transcription or key in ctx.aired:
                    continue
                user_data = shoutout.get("user_data") or {}
                username = user_data.get("username") or shoutout.get("username") or "a listener"
                place = coarse_location(user_data.get("location") or shoutout.get("location")) or "somewhere out there"
                line = f"Shoutout from {username} ({place}): \"{clip(transcription, 260)}\""
                if audio_url:
                    line += f" Audio: ${audio_url}$"
                facts.append(line)
                keys.append(key)
                if len(keys) >= 2:
                    break
        for point in await dj_bank_sources.station_stat_points(ctx.async_session_maker, ctx.catalog_service):
            if point.key not in ctx.aired:
                facts.append(point.text)
                keys.append(point.key)
        for point in await dj_bank_sources.listener_stat_points(ctx.user_id, ctx.session_id, ctx.async_session_maker,
                                                                ctx.catalog_service, ctx.tz_name):
            if point.key not in ctx.aired:
                facts.append(point.text)
                keys.append(point.key)
        if not facts or not any(k.startswith(("shoutout:", "station:", "streak:")) for k in keys):
            return None
        return SegmentContent(facts=facts, keys=keys, notes=[f"City: {ctx.place_name}"] if ctx.place_name else [],
                              moods=self.moods, title="Community corner")


class TriviaSegment(RadioSegment):
    kind = "trivia"
    label = "Feature"
    pref = "features"
    target_s = (35, 55)
    moods = ("curious", "retro", "groove")
    instruction = (
        "BEHIND THE MUSIC FEATURE on {artist}, the artist credited on the track coming up next ({next_title}). The "
        "hosts tell the story from SEGMENT DATA: two or three facts, in their own words, like music nerds who can't "
        "help themselves. [LEO] tells, [TARA] reacts and adds the punchline. Only facts from SEGMENT DATA. "
        "Finish by throwing to the track."
    )

    async def build(self, ctx: SegmentContext) -> Optional[SegmentContent]:
        web_service = getattr(ctx.dj_service, "web_service", None)
        if web_service is None:
            return None
        for track in (ctx.next_track, ctx.upcoming_track):
            artist = (track or {}).get("artist") or ""
            akey = artist_key(artist)
            if not akey or f"trivia:{akey}" in ctx.aired:
                continue
            biography = web_service.cached_artist_biography(artist)
            if biography is None:
                try:
                    biography = await asyncio.wait_for(web_service.retrieve_artist_biography(artist), BIOGRAPHY_WAIT_S)
                except (asyncio.TimeoutError, Exception) as e:
                    log_service.warning(f"[RADIO] biography for '{artist}' unavailable: {type(e).__name__}")
                    biography = None
            sentences = fact_sentences(biography or "")
            if len(sentences) < 2:
                continue
            picked, size = [], 0
            for sentence in sentences:
                if picked and size + len(sentence) > TRIVIA_MAX_CHARS:
                    break
                picked.append(sentence)
                size += len(sentence) + 1
            facts = [f"About {artist}: {' '.join(picked)}"]
            if track.get("genre"):
                facts.append(f"The track '{track.get('title')}' is {track.get('genre')}"
                             + (f", {clip(track.get('style') or '', 160)}" if track.get("style") else ""))
            return SegmentContent(facts=facts, keys=[f"trivia:{akey}"], moods=self.moods,
                                  notes=[f"Artist: {artist}", f"Track coming up: {track.get('title') or 'next'}"],
                                  title=f"Behind the music: {artist}")
        return None


_for_you_runs: dict[str, float] = {}


def for_you_due(session_id: str, now: Optional[float] = None) -> bool:
    last = _for_you_runs.get(session_id)
    return last is None or (now or time.time()) - last >= settings.RADIO_FOR_YOU_INTERVAL_S


class ForYouSegment(RadioSegment):
    kind = "for_you"
    label = "For You"
    pref = "features"
    target_s = (100, 140)
    moods = ("warm", "upbeat", "chill")
    instruction = (
        "FOR YOU - a two-minute narrative the station's producer built just for this listener (its angle is the "
        "SEGMENT title). Tell it as a story, following the beats in SEGMENT DATA in order and connecting them the "
        "way a friend who knows the city and their taste would; talk to the listener directly. Warm and specific, never creepy: show what the station knows, don't "
        "recite it. Only facts from SEGMENT DATA. Close by handing back to the music."
    )

    async def build(self, ctx: SegmentContext) -> Optional[SegmentContent]:
        if not for_you_due(ctx.session_id):
            return None
        _for_you_runs[ctx.session_id] = time.time()
        avoid = []
        if ctx.prefs is not None and not getattr(ctx.prefs, "local", True):
            avoid.append("gigs, events and places")
        if ctx.prefs is not None and not getattr(ctx.prefs, "community", True):
            avoid.append("listener shoutouts")
        brief = (
            f"FOR YOU: research a two-minute narrative feature made for this one listener. It's "
            f"{ctx.now_local.strftime('%A %I:%M %p').replace(' 0', ' ')} where they are. Find out who they are, what "
            "they've been talking about and what's on air, then dig through everything the station knows and build "
            "the story you think they'd genuinely love right now. Vary the angle; don't default to gigs."
        )
        if avoid:
            brief += " The listener has switched off " + " and ".join(avoid) + "; leave those out."
        result = await pulse_agent.gather_facts(brief, ctx.user_id, ctx.session_id, ctx.aired,
                                                timeout_s=settings.RADIO_FOR_YOU_TIMEOUT_S,
                                                max_rounds=settings.RADIO_FOR_YOU_MAX_ROUNDS)
        if len(result.facts) < 3:
            _for_you_runs.pop(ctx.session_id, None)
            return None
        return SegmentContent(facts=result.facts, keys=result.keys, moods=self.moods,
                              notes=[f"City: {ctx.place_name}"] if ctx.place_name else [],
                              title=result.angle or "Made for you")


for _segment in (NewsSegment(), CitySegment(), ForYouSegment(), LocalSegment(), CommunitySegment(),
                 TriviaSegment()):
    register(_segment)


def segment_prompt(segment: RadioSegment, content: SegmentContent, ctx: SegmentContext,
                   words_per_second: float) -> dict:
    low_words, high_words = segment.word_range(words_per_second)
    seconds = sum(segment.target_s) / 2
    next_title = (ctx.next_track or {}).get("title") or ""
    next_artist = (ctx.next_track or {}).get("artist") or ""
    instruction = segment.instruction.format(
        hour=ctx.now_local.strftime("%I %p").lstrip("0").lower(),
        place=ctx.place_name or "the listener's city",
        artist=next_artist or "the next artist",
        next_title=next_title or "the next track",
    )
    return {
        "label": segment.label,
        "title": content.title or segment.label,
        "instruction": instruction,
        "seconds": seconds,
        "min_words": low_words,
        "max_words": high_words,
        "target_words": int(seconds * words_per_second),
        "notes": content.notes,
        "next_track": f"'{next_title}'" + (f" (credited to {next_artist})" if next_artist else "")
        if segment.tease_next_track and next_title else "",
    }


def spoken_word_count(script: str, spoken_text: Callable[[str], str]) -> int:
    return len(spoken_text(script).split())
