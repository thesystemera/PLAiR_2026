import asyncio
import hashlib
import html as html_lib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin, urlsplit

import httpx
import pytz
import trafilatura
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import settings
from database.models import EventSource, PlaceCache, RegionalItem
from services import log_service, usage_tracking, web_fetch
from services.llm_router import LLM_BACKGROUND
from services_radio import geo, news_links
from services_radio.local_knowledge import region_zone
from services_radio.news_store import fold
from services_radio.regional_knowledge import KIND_EVENT, Collector, KnowledgeItem, Region, _haversine_km, \
    hot_topics

SOURCE = "web"
ARTICLE = "article"
LISTING = "listing"
CALENDAR = "calendar"
VENUE = "venue"
READ_ORDER = {CALENDAR: 0, LISTING: 1, ARTICLE: 2, VENUE: 3}
USAGE = ("events", "page_read")
FEATURE = "EventHarvest.extract"
LLM_MIN_CHARS = 200
LISTING_MIN_EVENTS = 3
ARTICLE_DONE_S = 365 * 86400
RETRY_SOON_S = 3600
WAVES = 3
LEADS_PER_PAGE = 5
SOURCE_KEEP_DAYS = 120
LOCAL_SCOPES = {"spot", "street", "neighbourhood"}

EVENT_KINDS = ("music", "nightlife", "comedy", "theatre", "dance", "film", "arts", "exhibition", "books", "festival",
               "market", "food & drink", "community", "charity", "health", "sport", "fitness", "family", "workshop",
               "talk", "other")
SCHEMA_KINDS = {"MusicEvent": "music", "SportsEvent": "sport", "TheaterEvent": "theatre", "ComedyEvent": "comedy",
                "DanceEvent": "dance", "ExhibitionEvent": "exhibition", "VisualArtsEvent": "arts",
                "Festival": "festival", "FoodEvent": "food & drink", "ChildrensEvent": "family",
                "EducationEvent": "workshop", "LiteraryEvent": "books", "ScreeningEvent": "film",
                "SocialEvent": "community", "SaleEvent": "market", "CourseInstance": "workshop"}
EVENT_TYPE = re.compile(r"(Event|Festival)$")
CALENDAR_HREF = re.compile(r"(\.ics(\?|$)|^webcal:|[?&](ical|ics)=1?|/ical/?$|format=ical)", re.IGNORECASE)
EVENTS_LINK = re.compile(
    r"\b(events?|gigs?|whats[-_ ]?on|what-s-on|calendar|programme|program|shows|line-?up|concerts?|agenda|"
    r"veranstaltungen|evenements|eventos|eventi|kalender|upcoming)\b", re.IGNORECASE)
TRIBE_PATH = "/wp-json/tribe/events/v1/events"


@dataclass
class FoundEvent:
    title: str
    starts_at: datetime
    ends_at: Optional[datetime] = None
    timed: bool = True
    venue: str = ""
    address: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    tags: list = field(default_factory=list)
    description: str = ""
    url: str = ""
    people: list = field(default_factory=list)
    leads: list = field(default_factory=list)


@dataclass
class PageRead:
    status: str
    method: str = ""
    events: list = field(default_factory=list)
    links: list = field(default_factory=list)
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_hash: Optional[str] = None


class PageEvent(BaseModel):
    title: str = Field(description="The event's name as a listener would say it")
    date: str = Field(description="Start date, YYYY-MM-DD")
    time: Optional[str] = Field(default=None, description="Start time, 24-hour HH:MM; null when the page gives none")
    end_date: Optional[str] = Field(default=None, description="Last day, YYYY-MM-DD, for multi-day events; else null")
    venue: Optional[str] = Field(default=None, description="Venue or meeting point name")
    address: Optional[str] = Field(default=None, description="Where it is, written so a map search finds it: "
                                                              "venue, street, suburb, city")
    kind: str = Field(description="One of: " + ", ".join(EVENT_KINDS))
    free: Optional[bool] = Field(default=None, description="true when entry is free, false when paid, null unknown")
    summary: str = Field(description="One short sentence on what it is, from the page")
    people: list[str] = Field(default_factory=list, description="Named performers, bands, speakers or organisers")


class PageEvents(BaseModel):
    events: list[PageEvent]


SYSTEM = "You are a careful local events editor for a city radio station."


def url_key(url: str) -> str:
    return news_links.identity(url)


def _host(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def skipped_host(url: str) -> bool:
    host = _host(url)
    return not host or any(host == h or host.endswith("." + h) for h in settings.EVENT_SOURCE_SKIP_HOSTS)


def _clean(value, limit: int = 300) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("@value") or ""
    if isinstance(value, list):
        value = next((v for v in value if v), "")
        return _clean(value, limit)
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return " ".join(text.split())[:limit]


def _float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def zone_for(region: Region):
    return region_zone(region.key) or pytz.utc


def parse_moment(value, zone) -> tuple[Optional[datetime], bool]:
    raw = _clean(value, 64)
    if not raw:
        return None, False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        day = datetime.strptime(raw, "%Y-%m-%d")
        return zone.localize(day.replace(hour=12)), False
    try:
        moment = date_parser.isoparse(raw)
    except (ValueError, OverflowError):
        try:
            moment = date_parser.parse(raw)
        except (ValueError, OverflowError):
            return None, False
    if moment.tzinfo is None:
        moment = zone.localize(moment)
    return moment, True


def _types(node: dict) -> list[str]:
    value = node.get("@type") or []
    return [str(t).rsplit("/", 1)[-1] for t in (value if isinstance(value, list) else [value])]


def _walk(node):
    if isinstance(node, list):
        for child in node:
            yield from _walk(child)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "itemListElement", "item", "subEvent", "event", "events", "mainEntity"):
            if key in node:
                yield from _walk(node[key])


def _address(value) -> str:
    if isinstance(value, list):
        value = next((v for v in value if v), "")
    if isinstance(value, dict):
        parts = [_clean(value.get(k), 120) for k in ("streetAddress", "addressLocality", "addressRegion",
                                                       "postalCode", "addressCountry")]
        return ", ".join(dict.fromkeys(p for p in parts if p))[:200]
    return _clean(value, 200)


def _ld_event(node: dict, zone, page_url: str) -> Optional[FoundEvent]:
    title = _clean(node.get("name"), 200)
    starts_at, timed = parse_moment(node.get("startDate"), zone)
    if not title or starts_at is None:
        return None
    if re.search(r"(Cancelled|Postponed|Rescheduled)", str(node.get("eventStatus") or "")):
        return None
    if "Online" in str(node.get("eventAttendanceMode") or "") and "Mixed" not in str(node.get("eventAttendanceMode")):
        return None
    ends_at, _ = parse_moment(node.get("endDate"), zone)
    location = node.get("location")
    if isinstance(location, list):
        location = next((loc for loc in location if isinstance(loc, dict) and "Virtual" not in str(loc.get("@type"))),
                        location[0] if location else None)
    venue, address, lat, lon = "", "", None, None
    if isinstance(location, dict):
        if "VirtualLocation" in _types(location):
            return None
        venue = _clean(location.get("name"), 120)
        address = _address(location.get("address"))
        point = location.get("geo") if isinstance(location.get("geo"), dict) else {}
        lat, lon = _float(point.get("latitude")), _float(point.get("longitude"))
    elif location:
        address = _clean(location, 200)
    tags = [SCHEMA_KINDS[t] for t in _types(node) if t in SCHEMA_KINDS]
    offers = node.get("offers")
    offers = offers if isinstance(offers, list) else [offers] if isinstance(offers, dict) else []
    if node.get("isAccessibleForFree") in (True, "true", "True") or any(
            str(o.get("price")).strip() in ("0", "0.0", "0.00") for o in offers if isinstance(o, dict)):
        tags.append("free")
    people = []
    for key in ("performer", "organizer"):
        value = node.get(key)
        for person in value if isinstance(value, list) else [value] if value else []:
            name = _clean(person, 80)
            if name:
                people.append(name)
    link = node.get("url") if isinstance(node.get("url"), str) else ""
    leads = []
    for holder in [location if isinstance(location, dict) else {}, *(
            o for o in (node.get("organizer") if isinstance(node.get("organizer"), list) else [node.get("organizer")])
            if isinstance(o, dict))]:
        for key in ("url", "sameAs"):
            value = holder.get(key)
            for candidate in value if isinstance(value, list) else [value]:
                if isinstance(candidate, str) and candidate.startswith("http"):
                    leads.append(urljoin(page_url, candidate))
    return FoundEvent(title=title, starts_at=starts_at, ends_at=ends_at, timed=timed, venue=venue, address=address,
                      lat=lat, lon=lon, tags=tags, description=_clean(node.get("description"), 240),
                      url=urljoin(page_url, link) if link else "", people=people[:6], leads=leads)


def parse_json_ld(soup: BeautifulSoup, zone, page_url: str) -> list[FoundEvent]:
    events = []
    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.IGNORECASE)):
        try:
            data = json.loads(script.string or script.get_text() or "null", strict=False)
        except (ValueError, TypeError):
            continue
        for node in _walk(data):
            if any(EVENT_TYPE.search(t) for t in _types(node)):
                event = _ld_event(node, zone, page_url)
                if event:
                    events.append(event)
    return events


def _ics_lines(text: str) -> list[str]:
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        elif line:
            lines.append(line)
    return lines


def _ics_text(value: str) -> str:
    return " ".join(value.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";")
                    .replace("\\\\", "\\").split())


def _ics_moment(params: list[str], value: str, zone) -> tuple[Optional[datetime], bool]:
    value = value.strip()
    if re.fullmatch(r"\d{8}", value):
        return zone.localize(datetime.strptime(value, "%Y%m%d").replace(hour=12)), False
    match = re.fullmatch(r"(\d{8}T\d{4,6})(Z?)", value)
    if not match:
        return None, False
    stamp = match.group(1).ljust(15, "0")
    moment = datetime.strptime(stamp, "%Y%m%dT%H%M%S")
    if match.group(2):
        return moment.replace(tzinfo=timezone.utc), True
    local = zone
    tzid = next((p.split("=", 1)[1].strip('"') for p in params if p.upper().startswith("TZID=")), None)
    if tzid:
        try:
            local = pytz.timezone(tzid)
        except pytz.UnknownTimeZoneError:
            pass
    return local.localize(moment), True


def parse_ics(text: str, zone) -> list[FoundEvent]:
    events, current = [], None
    for line in _ics_lines(text):
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            current = {}
        elif upper == "END:VEVENT":
            if current is not None:
                event = _ics_event(current, zone)
                if event:
                    events.append(event)
            current = None
        elif current is not None and ":" in line:
            head, value = line.split(":", 1)
            name, *params = head.split(";")
            current.setdefault(name.upper(), (params, value))
    return events


def _ics_event(fields: dict, zone) -> Optional[FoundEvent]:
    if "CANCELLED" in fields.get("STATUS", ([], ""))[1].upper():
        return None
    title = _ics_text(fields.get("SUMMARY", ([], ""))[1])[:200]
    if "DTSTART" not in fields or not title:
        return None
    starts_at, timed = _ics_moment(*fields["DTSTART"], zone)
    if starts_at is None:
        return None
    ends_at = _ics_moment(*fields["DTEND"], zone)[0] if "DTEND" in fields else None
    lat = lon = None
    if "GEO" in fields and ";" in fields["GEO"][1]:
        lat, lon = (_float(v) for v in fields["GEO"][1].split(";", 1))
    location = _ics_text(fields.get("LOCATION", ([], ""))[1])[:200]
    return FoundEvent(title=title, starts_at=starts_at, ends_at=ends_at, timed=timed,
                      venue=location.split(",", 1)[0][:120], address=location, lat=lat, lon=lon,
                      description=_ics_text(fields.get("DESCRIPTION", ([], ""))[1])[:240],
                      url=fields.get("URL", ([], ""))[1].strip(), tags=[])


def parse_tribe(data: dict, zone) -> list[FoundEvent]:
    events = []
    for node in (data or {}).get("events") or []:
        local = zone
        try:
            local = pytz.timezone(node.get("timezone")) if node.get("timezone") else zone
        except pytz.UnknownTimeZoneError:
            pass
        starts_at, timed = parse_moment(node.get("start_date"), local)
        title = _clean(node.get("title"), 200)
        if starts_at is None or not title:
            continue
        timed = timed and not node.get("all_day")
        venue = node.get("venue") if isinstance(node.get("venue"), dict) else {}
        address = ", ".join(p for p in (_clean(venue.get(k), 120) for k in ("address", "city", "country")) if p)
        tags = [_clean(c.get("name"), 40).lower() for c in node.get("categories") or [] if isinstance(c, dict)]
        if str(node.get("cost") or "").strip().lower() in ("free", "0", "$0"):
            tags.append("free")
        events.append(FoundEvent(
            title=title, starts_at=starts_at, ends_at=parse_moment(node.get("end_date"), local)[0], timed=timed,
            venue=_clean(venue.get("venue"), 120), address=address, lat=_float(venue.get("geo_lat")),
            lon=_float(venue.get("geo_lng")), tags=[t for t in tags if t][:4],
            description=_clean(node.get("excerpt") or node.get("description"), 240), url=node.get("url") or ""))
    return events


def page_links(soup: BeautifulSoup, page: str, page_url: str, want_listing: bool) -> list[tuple[str, str]]:
    found, host = [], _host(page_url)
    for tag in soup.find_all(["a", "link"], href=True):
        href = tag["href"].strip()
        if href.lower().startswith("webcal:"):
            href = "https:" + href[7:]
        target = urljoin(page_url, href)
        if not target.startswith("http") or url_key(target) == url_key(page_url):
            continue
        if CALENDAR_HREF.search(href) or "text/calendar" in (tag.get("type") or ""):
            found.append((CALENDAR, target))
        elif want_listing and tag.name == "a" and _host(target) == host and (
                EVENTS_LINK.search(urlsplit(target).path.replace("/", " ")) or EVENTS_LINK.search(tag.get_text(" "))):
            found.append((LISTING, target.split("#", 1)[0]))
    if "tribe-events" in page or "/wp-json/tribe/" in page:
        root = f"{urlsplit(page_url).scheme}://{urlsplit(page_url).netloc}"
        found.append((CALENDAR, f"{root}{TRIBE_PATH}?per_page=50"))
    calendars = list(dict.fromkeys(url for kind, url in found if kind == CALENDAR))[:2]
    listings = list(dict.fromkeys(url for kind, url in found if kind == LISTING))[:2]
    return [(CALENDAR, url) for url in calendars] + [(LISTING, url) for url in listings]


def _listing_text(page: str) -> str:
    soup = BeautifulSoup(page, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "header", "footer", "form", "iframe"]):
        tag.decompose()
    root = soup.find("main") or soup.body or soup
    lines = (" ".join(line.split()) for line in root.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)


def _page_text(page: str, url: str, listing: bool) -> tuple[str, str]:
    if listing:
        text = _listing_text(page)
    else:
        text = trafilatura.extract(page, url=url, include_tables=True, include_links=False, include_comments=False,
                                   favor_recall=True) or ""
    metadata = trafilatura.extract_metadata(page, default_url=url)
    published = (metadata.date or "") if metadata is not None else ""
    return text, published


def _prompt(region: Region, zone, title: str, published: str, text: str) -> str:
    today = datetime.now(zone)
    return (
        f"City: {region.name}{f' ({region.country})' if region.country else ''}\n"
        f"Today: {today.strftime('%A %Y-%m-%d')}\n"
        f"Page: {title or '(untitled)'}" + (f", published {published}" if published else "") + "\n\n"
        f"Page text:\n{text}\n\n"
        "List the events on this page that people can go to in or near the city: gigs, club nights, shows, "
        "exhibitions, markets, festivals, fun runs, sports days, fundraisers, blood drives, workshops, talks, "
        "community meetings.\n"
        "- Only events that happen today or later. Work out relative dates ('this Saturday', 'next week') from the "
        "publish date. For something that repeats ('every Sunday'), give the next date.\n"
        "- Leave out past events, online-only events, adverts, and anything without a date.\n"
        "- address: the most specific place the page gives, written so a map search finds it.\n"
        "- Use only what the page says. Return [] when there are none."
    )


async def extract_with_llm(ai_service, region: Region, zone, title: str, published: str,
                           text: str) -> list[FoundEvent]:
    with usage_tracking.feature_scope(FEATURE):
        result = await ai_service.call_gemini_structured(
            prompt=_prompt(region, zone, title, published, text[:settings.EVENT_HARVEST_LLM_CHARS]),
            response_schema=PageEvents, system_instruction=SYSTEM, temperature=0, role=LLM_BACKGROUND)
    events = []
    for card in (result or {}).get("events") or []:
        clock = card.get("time") if re.fullmatch(r"\d{1,2}:\d{2}", str(card.get("time") or "")) else None
        starts_at, _ = parse_moment(f"{card.get('date')}T{clock}" if clock else card.get("date"), zone)
        title_text = _clean(card.get("title"), 200)
        if starts_at is None or not title_text:
            continue
        ends_at = parse_moment(card.get("end_date"), zone)[0] if card.get("end_date") else None
        kind = card.get("kind") if card.get("kind") in EVENT_KINDS else "other"
        tags = [kind] if kind != "other" else []
        if card.get("free") is True:
            tags.append("free")
        events.append(FoundEvent(
            title=title_text, starts_at=starts_at, ends_at=ends_at, timed=clock is not None,
            venue=_clean(card.get("venue"), 120), address=_clean(card.get("address"), 200), tags=tags,
            description=_clean(card.get("summary"), 240),
            people=[_clean(p, 80) for p in card.get("people") or [] if _clean(p, 80)][:6]))
    return events


def event_id(title: str, starts_at: datetime, zone) -> str:
    day = starts_at.astimezone(zone).strftime("%Y-%m-%d")
    return hashlib.sha1(f"{' '.join(fold(title).split())}|{day}".encode()).hexdigest()[:20]


def describe(event: FoundEvent, zone, now: datetime) -> str:
    local = event.starts_at.astimezone(zone)
    when = local.strftime("%a %d %b") + (local.strftime(" %H:%M") if event.timed else "")
    if event.starts_at < now and event.ends_at:
        when = "on now until " + event.ends_at.astimezone(zone).strftime("%a %d %b")
    head = ", ".join(p for p in (event.venue, when) if p)
    return (f"{head}. {event.description}" if event.description else head)[:200]


class WebEventsCollector(Collector):
    name = "web_events"
    kind = KIND_EVENT
    refresh_s = settings.EVENT_HARVEST_REFRESH_S
    enabled = settings.EVENT_HARVEST_ENABLED

    def __init__(self, async_session_maker, news_service, ai_service):
        self.async_session_maker = async_session_maker
        self.news_service = news_service
        self.ai_service = ai_service

    def available(self) -> bool:
        return self.enabled and self.news_service is not None and self.ai_service is not None

    async def fetch(self, region: Region) -> list[KnowledgeItem]:
        if not region.center:
            return []
        zone = zone_for(region)
        discovered = await self.discover(region)
        budget = {"llm": settings.EVENT_HARVEST_LLM_PAGES_PER_RUN, "geocode": settings.EVENT_HARVEST_GEOCODES_PER_RUN}
        gate = asyncio.Semaphore(settings.EVENT_HARVEST_READ_PARALLEL)

        async def one(source: EventSource):
            async with gate:
                try:
                    return await self.read(source, region, zone, budget)
                except Exception as e:
                    log_service.warning(f"[EVENTS] read failed for {source.url}: {type(e).__name__}: {e}")
                    return PageRead("failed")

        pages, added, reads = settings.EVENT_HARVEST_PAGES_PER_RUN, 0, []
        for _ in range(WAVES):
            due = await self._due(region.key, pages)
            if not due:
                break
            wave = await asyncio.gather(*(one(source) for source in due))
            new_sources = []
            for source, result in zip(due, wave):
                for event in result.events:
                    event.url = event.url or source.url
                await self._record(source, result)
                new_sources += [(kind, url, source.url) for kind, url in result.links]
            added += await self._add_sources(region.key, new_sources)
            reads += wave
            pages -= len(due)
            if pages <= 0 or not new_sources:
                break
        found = [event for result in reads for event in result.events]
        items = await self.to_items(region, zone, found, budget)
        methods: dict[str, int] = {}
        for result in reads:
            if result.events:
                methods[result.method] = methods.get(result.method, 0) + 1
        log_service.external(
            f"[EVENTS] {region.name}: read {len(reads)} pages, events from {sum(methods.values())} ("
            + (", ".join(f"{n} {m}" for m, n in sorted(methods.items())) or "none") + f"), {len(items)} events kept, "
            f"{discovered + added} new sources")
        return items

    async def discover(self, region: Region) -> int:
        queries = settings.EVENT_HARVEST_QUERIES
        added = 0
        if queries:
            per_run = max(1, min(settings.EVENT_HARVEST_QUERIES_PER_RUN, len(queries)))
            start = int(datetime.now(timezone.utc).timestamp() // max(self.refresh_s, 1)) * per_run % len(queries)
            chosen = [queries[(start + i) % len(queries)].format(city=region.name) for i in range(per_run)]
            chosen += [f"{region.name} {topic}" for topic in (await hot_topics(region, "events"))[:2]]
            for query in chosen:
                await self._search(query, region)
            await self.news_service.resolve_pending()
            for query in chosen:
                added += await self._add_sources(region.key, [
                    (ARTICLE, story.url, query) for story in await self._search(query, region)
                    if story.url and not news_links.is_google(story.url)])
        added += await self._add_sources(region.key, [
            (VENUE, website, name) for name, website in await self._venues(region)])
        return added

    async def _search(self, query: str, region: Region) -> list:
        try:
            return await self.news_service.search_items(query, region.country, region.key)
        except Exception as e:
            log_service.warning(f"[EVENTS] news search failed for '{query}': {type(e).__name__}: {e}")
            return []

    async def _venues(self, region: Region) -> list[tuple[str, str]]:
        lat, lon = region.center
        span = settings.PULSE_CITY_RADIUS_KM / 111.0
        async with self.async_session_maker() as db:
            rows = (await db.execute(
                select(PlaceCache.name, PlaceCache.website, PlaceCache.latitude, PlaceCache.longitude)
                .where(PlaceCache.website.is_not(None), PlaceCache.latitude.between(lat - span, lat + span),
                       PlaceCache.longitude.between(lon - span * 2, lon + span * 2)))).all()
            candidates = {url_key(website): (name, website) for name, website, plat, plon in rows
                          if website.startswith("http") and not skipped_host(website)
                          and _haversine_km((plat, plon), region.center) <= settings.PULSE_CITY_RADIUS_KM}
            if not candidates:
                return []
            known = set((await db.execute(select(EventSource.url_key).where(
                EventSource.url_key.in_(list(candidates))))).scalars().all())
        return [candidates[key] for key in candidates if key not in known][:settings.EVENT_HARVEST_VENUES_PER_RUN]

    async def _add_sources(self, region_key: str, sources: list[tuple[str, str, str]]) -> int:
        now = datetime.now(timezone.utc)
        rows = {}
        for kind, url, via in sources:
            if not url or not url.startswith("http") or skipped_host(url):
                continue
            key = url_key(url)
            rows.setdefault(key, {"url_key": key, "url": url[:2000], "region_key": region_key, "kind": kind,
                                  "found_via": (via or "")[:300], "next_read_at": now, "first_seen_at": now})
        if not rows:
            return 0
        async with self.async_session_maker() as db:
            result = await db.execute(pg_insert(EventSource).values(list(rows.values()))
                                      .on_conflict_do_nothing().returning(EventSource.url_key))
            added = len(result.all())
            await db.commit()
        return added

    async def _due(self, region_key: str, limit: int) -> list[EventSource]:
        now = datetime.now(timezone.utc)
        async with self.async_session_maker() as db:
            await db.execute(delete(EventSource).where(
                EventSource.kind == ARTICLE, EventSource.first_seen_at < now - timedelta(days=SOURCE_KEEP_DAYS)))
            await db.commit()
            rows = (await db.execute(
                select(EventSource).where(EventSource.region_key == region_key, EventSource.next_read_at <= now)
                .order_by(EventSource.next_read_at).limit(limit * 3))).scalars().all()
        rows = sorted(rows, key=lambda r: (READ_ORDER.get(r.kind, 9), r.next_read_at))
        return rows[:limit]

    async def _record(self, source: EventSource, result: PageRead) -> None:
        now = datetime.now(timezone.utc)
        if result.status in ("resting", "deferred"):
            async with self.async_session_maker() as db:
                row = await db.get(EventSource, source.url_key)
                if row is not None:
                    row.next_read_at = now + timedelta(seconds=RETRY_SOON_S)
                    await db.commit()
            return
        kind = source.kind
        if kind == ARTICLE and len(result.events) >= LISTING_MIN_EVENTS:
            kind = LISTING
        empty = 0 if result.events or result.status == "unchanged" else (source.empty_reads or 0) + 1
        if result.status in ("blocked", "disallowed"):
            wait = settings.EVENT_SOURCE_RETRY_S
        elif kind == ARTICLE:
            wait = ARTICLE_DONE_S
        elif empty >= settings.EVENT_SOURCE_MAX_EMPTY_READS or (kind == VENUE and not result.events):
            wait = settings.EVENT_SOURCE_RETRY_S
        else:
            wait = settings.EVENT_SOURCE_REFRESH_S
        values = {"kind": kind, "status": result.status, "reads": (source.reads or 0) + 1, "empty_reads": empty,
                  "last_read_at": now, "next_read_at": now + timedelta(seconds=wait)}
        if result.status != "unchanged":
            values.update(method=result.method, events_found=len(result.events),
                          events_total=(source.events_total or 0) + len(result.events))
        for column in ("etag", "last_modified", "content_hash"):
            if getattr(result, column):
                values[column] = getattr(result, column)
        async with self.async_session_maker() as db:
            row = await db.get(EventSource, source.url_key)
            if row is not None:
                for column, value in values.items():
                    setattr(row, column, value)
                await db.commit()

    async def read(self, source: EventSource, region: Region, zone, budget: dict) -> PageRead:
        headers = {}
        if source.etag:
            headers["If-None-Match"] = source.etag
        if source.last_modified:
            headers["If-Modified-Since"] = source.last_modified
        try:
            response = await web_fetch.get(source.url, web_fetch.page_policy(), USAGE, retries=1, headers=headers)
        except web_fetch.Disallowed:
            return PageRead("disallowed")
        except web_fetch.HostResting:
            return PageRead("resting")
        except httpx.HTTPError:
            return PageRead("failed")
        if response.status_code == 304:
            return PageRead("unchanged")
        if response.status_code in web_fetch.BLOCKED_STATUSES:
            return PageRead("blocked")
        if response.status_code >= 400:
            return PageRead("failed")
        content_hash = hashlib.sha1(response.content[:500000]).hexdigest()
        result = PageRead("ok", etag=response.headers.get("etag"),
                          last_modified=response.headers.get("last-modified"), content_hash=content_hash)
        content_type = response.headers.get("content-type", "").lower()
        final_url = str(response.url)
        if "calendar" in content_type or final_url.split("?", 1)[0].lower().endswith(".ics"):
            result.method, result.events = "ics", await asyncio.to_thread(parse_ics, response.text, zone)
            return result
        if "json" in content_type and TRIBE_PATH in final_url:
            try:
                data = response.json()
            except ValueError:
                return PageRead("failed")
            result.method, result.events = "wp-events", parse_tribe(data, zone)
            return result
        if "html" not in content_type:
            return PageRead("failed")
        page = response.text
        soup = await asyncio.to_thread(BeautifulSoup, page, "lxml")
        result.events = parse_json_ld(soup, zone, final_url)
        result.method = "json-ld" if result.events else ""
        result.links = page_links(soup, page, final_url, want_listing=source.kind == VENUE and not result.events)
        leads = dict.fromkeys(url for event in result.events if self._local(event, region) for url in event.leads
                              if url_key(url) != url_key(final_url) and not skipped_host(url))
        result.links += [(VENUE, url) for url in list(leads)[:LEADS_PER_PAGE]]
        if result.events or source.kind == VENUE:
            return result
        if source.content_hash == content_hash and source.method == "llm":
            result.status = "unchanged"
            return result
        if budget["llm"] <= 0:
            result.status = "deferred"
            return result
        text, published = await asyncio.to_thread(_page_text, page, final_url, source.kind == LISTING)
        if len(text) < LLM_MIN_CHARS:
            return result
        budget["llm"] -= 1
        title = _clean(soup.title.string if soup.title else "", 200)
        result.events = await extract_with_llm(self.ai_service, region, zone, title, published, text)
        result.method = "llm"
        return result

    @staticmethod
    def _local(event: FoundEvent, region: Region) -> bool:
        return event.lat is None or event.lon is None or _haversine_km(
            (event.lat, event.lon), region.center) <= settings.PULSE_CITY_RADIUS_KM

    async def _known_elsewhere(self, region: Region, zone) -> set:
        async with self.async_session_maker() as db:
            rows = (await db.execute(select(RegionalItem.title, RegionalItem.starts_at).where(
                RegionalItem.region_key == region.key, RegionalItem.kind == KIND_EVENT,
                RegionalItem.source != SOURCE, RegionalItem.starts_at.is_not(None)))).all()
        return {event_id(title, starts_at, zone) for title, starts_at in rows}

    async def _place(self, event: FoundEvent, region: Region, budget: dict) -> None:
        if event.lat is not None and event.lon is not None:
            return
        phrase = event.address or event.venue
        if not phrase or budget["geocode"] <= 0 or not geo.resolver.available():
            return
        if region.name.lower() not in phrase.lower():
            phrase = f"{phrase}, {region.name}"
        if geo.resolver.cached(phrase, region.country) is None:
            budget["geocode"] -= 1
        where = await geo.resolver.resolve(phrase, region.country)
        if where is not None and where.scope in LOCAL_SCOPES:
            event.lat, event.lon = where.lat, where.lon

    async def to_items(self, region: Region, zone, events: list[FoundEvent], budget: dict) -> list[KnowledgeItem]:
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(days=settings.REGIONAL_EVENTS_DAYS_AHEAD)
        known = await self._known_elsewhere(region, zone)
        chosen: dict[str, FoundEvent] = {}
        for event in events:
            ends = event.ends_at or event.starts_at + timedelta(hours=6)
            if ends < now or event.starts_at > horizon:
                continue
            key = event_id(event.title, event.starts_at, zone)
            if key in known:
                continue
            prior = chosen.get(key)
            if prior is None or (prior.lat is None and event.lat is not None) or len(event.description) > len(
                    prior.description):
                chosen[key] = event
        items = []
        for key, event in chosen.items():
            await self._place(event, region, budget)
            if event.lat is not None and event.lon is not None and _haversine_km(
                    (event.lat, event.lon), region.center) > settings.PULSE_CITY_RADIUS_KM:
                continue
            starts_at = event.starts_at if event.starts_at >= now else now.replace(minute=0, second=0, microsecond=0)
            tags = list(dict.fromkeys(t for t in event.tags if t))[:6]
            items.append(KnowledgeItem(
                source=SOURCE, kind=KIND_EVENT, region_key=region.key, external_id=key, title=event.title,
                text=describe(event, zone, now), tags=tags, starts_at=starts_at,
                expires_at=(event.ends_at or event.starts_at) + timedelta(hours=6), url=event.url[:2000],
                attribution=_host(event.url) if event.url else "", latitude=event.lat, longitude=event.lon,
                area=(event.address or event.venue)[:120],
                entities=list(dict.fromkeys(n for n in [event.venue, *event.people] if n))))
        return items
