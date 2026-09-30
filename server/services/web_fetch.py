import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from config import settings
from services import log_service, usage_tracking
from services.http_client import USER_AGENT, fetch, get_http_client

BROWSER_ACCEPT = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                  "Accept-Language": "en;q=0.9"}
BROWSER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 "
    "Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 "
    "Edg/151.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:155.0) Gecko/20100101 Firefox/155.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:155.0) Gecko/20100101 Firefox/155.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 "
    "Safari/605.1.15",
)
BROWSER_LANGUAGES = ("en-NZ,en;q=0.9", "en-US,en;q=0.9", "en-GB,en;q=0.9", "en-AU,en;q=0.9,en-US;q=0.8")
RETRY_STATUSES = {500, 502, 503, 504}
BLOCKED_STATUSES = {401, 403, 451}


class HostResting(httpx.TransportError):
    pass


class RateLimited(HostResting):
    pass


class Disallowed(httpx.TransportError):
    pass


@dataclass(frozen=True)
class HostPolicy:
    gap_s: float = 0.0
    jitter_s: float = 0.0
    parallel: int = 4
    daily_cap: int = 0
    robots: bool = False
    rest_after_failures: int = 0
    rotate: bool = False


@dataclass
class _Host:
    gate: asyncio.Semaphore
    pace: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_at: float = 0.0
    failures: int = 0
    limited: int = 0
    resting_until: float = 0.0
    day: str = ""
    used_today: int = 0
    robots: Optional[RobotFileParser] = None
    robots_at: float = 0.0
    identity: Optional[dict] = None
    identity_uses: int = 0


def page_policy() -> HostPolicy:
    return HostPolicy(gap_s=settings.WEB_PAGE_GAP_S, jitter_s=settings.WEB_PAGE_JITTER_S,
                      parallel=settings.WEB_PAGE_PARALLEL, robots=True,
                      rest_after_failures=settings.WEB_REST_AFTER_FAILURES, rotate=True)


def google_news_policy() -> HostPolicy:
    return HostPolicy(gap_s=settings.WEB_GOOGLE_NEWS_GAP_S, jitter_s=settings.WEB_GOOGLE_NEWS_JITTER_S,
                      parallel=1, daily_cap=settings.WEB_GOOGLE_NEWS_DAILY_CAP,
                      rest_after_failures=settings.WEB_REST_AFTER_FAILURES, rotate=True)


MUSICBRAINZ = HostPolicy(gap_s=1.1, parallel=1)

_hosts: dict[str, _Host] = {}


def _host(name: str, policy: HostPolicy) -> _Host:
    state = _hosts.get(name)
    if state is None:
        state = _hosts[name] = _Host(gate=asyncio.Semaphore(max(1, policy.parallel)))
    return state


def resting(url: str) -> bool:
    state = _hosts.get(urlsplit(url).netloc.lower())
    return state is not None and time.monotonic() < state.resting_until


def _forget_cookies(name: str) -> None:
    jar = get_http_client().cookies.jar
    for domain in {cookie.domain for cookie in jar if name.endswith(cookie.domain.lstrip("."))}:
        try:
            jar.clear(domain)
        except KeyError:
            pass


def _new_identity(name: str, state: _Host) -> None:
    agents = settings.WEB_USER_AGENTS or BROWSER_AGENTS
    current = (state.identity or {}).get("User-Agent")
    state.identity = {"User-Agent": random.choice([a for a in agents if a != current] or list(agents)),
                      "Accept-Language": random.choice(BROWSER_LANGUAGES), "Upgrade-Insecure-Requests": "1"}
    state.identity_uses = 0
    _forget_cookies(name)


def _identity(name: str, state: _Host, policy: HostPolicy) -> dict:
    if not policy.rotate:
        return {}
    if state.identity is None or state.identity_uses >= settings.WEB_IDENTITY_REQUESTS:
        _new_identity(name, state)
    state.identity_uses += 1
    return state.identity


def _rest(name: str, state: _Host, seconds: float, why: str) -> None:
    state.resting_until = time.monotonic() + seconds
    log_service.warning(f"[WEB] {name}: {why}, resting {seconds / 60:.0f} min")


def report(url: str, ok: bool, policy: HostPolicy) -> None:
    name = urlsplit(url).netloc.lower()
    state = _host(name, policy)
    if ok:
        state.failures = 0
        return
    state.failures += 1
    if policy.rest_after_failures and state.failures >= policy.rest_after_failures:
        state.failures = 0
        _rest(name, state, settings.WEB_REST_S, f"{policy.rest_after_failures} failures in a row")


async def _allowed(url: str, name: str, state: _Host) -> bool:
    if state.robots is None or time.monotonic() - state.robots_at > settings.WEB_ROBOTS_TTL_S:
        parts = urlsplit(url)
        robots = RobotFileParser()
        try:
            response = await fetch("GET", f"{parts.scheme}://{name}/robots.txt", retries=1)
        except (httpx.HTTPError, OSError):
            return False
        if response.status_code in BLOCKED_STATUSES:
            robots.disallow_all = True
        elif response.status_code >= 400:
            robots.allow_all = True
        else:
            robots.parse(response.text.splitlines())
        state.robots, state.robots_at = robots, time.monotonic()
    return state.robots.can_fetch(USER_AGENT, url)


async def _paced(state: _Host, policy: HostPolicy) -> None:
    async with state.pace:
        wait = state.last_at + policy.gap_s + random.uniform(0, policy.jitter_s) - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        state.last_at = time.monotonic()


def _retry_after(response: httpx.Response) -> Optional[float]:
    value = response.headers.get("retry-after", "")
    return float(value) if value.isdigit() else None


def _record(usage: Optional[tuple[str, str]], error: bool) -> None:
    if usage:
        usage_tracking.record_api_call(*usage, error=error)


async def request(method: str, url: str, policy: HostPolicy, usage: Optional[tuple[str, str]], retries: int = 2,
                  **kwargs) -> httpx.Response:
    name = urlsplit(url).netloc.lower()
    state = _host(name, policy)
    today = time.strftime("%Y-%m-%d")
    if state.day != today:
        state.day, state.used_today = today, 0
    if time.monotonic() < state.resting_until:
        raise HostResting(f"{name} is resting")
    if policy.daily_cap and state.used_today >= policy.daily_cap:
        raise HostResting(f"{name} reached its daily cap of {policy.daily_cap}")
    extra = kwargs.pop("headers", {})
    async with state.gate:
        if policy.robots and not await _allowed(url, name, state):
            raise Disallowed(f"{name} robots.txt disallows this page")
        delay = 1.0
        for attempt in range(retries + 1):
            await _paced(state, policy)
            state.used_today += 1
            headers = {**BROWSER_ACCEPT, **_identity(name, state, policy), **extra}
            try:
                response = await fetch(method, url, retries=0, headers=headers, **kwargs)
            except (httpx.HTTPError, OSError):
                if attempt == retries:
                    _record(usage, True)
                    report(url, False, policy)
                    raise
            else:
                if response.status_code == 429 or "/sorry/" in str(response.url):
                    state.limited += 1
                    _record(usage, True)
                    _rest(name, state, _retry_after(response) or
                          settings.WEB_RATE_LIMIT_REST_S * 2 ** min(state.limited - 1, 3), "rate limited")
                    if policy.rotate:
                        _new_identity(name, state)
                    raise RateLimited(f"{name} is rate limiting")
                if response.status_code not in RETRY_STATUSES or attempt == retries:
                    ok = response.status_code < 400
                    _record(usage, not ok)
                    if ok:
                        state.limited = 0
                    report(url, ok or response.status_code == 404, policy)
                    return response
            await asyncio.sleep(delay + random.uniform(-0.5 * delay, 0.5 * delay))
            delay *= 2
    raise RuntimeError("unreachable")


async def get(url: str, policy: HostPolicy, usage: Optional[tuple[str, str]] = None, **kwargs) -> httpx.Response:
    return await request("GET", url, policy, usage, **kwargs)


async def post(url: str, policy: HostPolicy, usage: Optional[tuple[str, str]] = None, **kwargs) -> httpx.Response:
    return await request("POST", url, policy, usage, **kwargs)
