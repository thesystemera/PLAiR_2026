import asyncio
import re
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import trafilatura

from config import settings
from services import log_service, usage_tracking
from services.http_client import USER_AGENT, fetch

READ_OK = "ok"
READ_EMPTY = "empty"
READ_BLOCKED = "blocked"
READ_FAILED = "failed"

SENTENCE_END = re.compile(r"(?:(?<=[.!?])|(?<=[.!?][\"'”’]))\s+(?=[\"'“‘]?[A-Z0-9])")
MIN_SUMMARY_CHARS = 40
PAGE_HEADERS = {"Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"}


@dataclass
class _Site:
    robots: Optional[RobotFileParser] = None
    robots_at: float = 0.0
    failures: int = 0
    resting_until: float = 0.0


def _fold(text: str) -> str:
    return " ".join(re.sub(r"[^\w ]", " ", (text or "").lower()).split())


def summarize(title: str, description: str, text: str, limit: int) -> str:
    title_key = _fold(title)
    lines = [line.strip() for line in (text or "").splitlines() if line.strip() and _fold(line) != title_key]
    body = " ".join(lines)
    description = (description or "").strip().rstrip(".").strip()
    parts = []
    if description and _fold(description)[:60] not in _fold(body)[:len(description) + 40]:
        parts.append(description + ".")
    for sentence in SENTENCE_END.split(body):
        if sum(len(p) + 1 for p in parts) + len(sentence) > limit:
            break
        parts.append(sentence)
    summary = " ".join(parts).strip()
    if not summary and body:
        summary = body[:limit].rsplit(" ", 1)[0]
    return summary


def _extract(page: str, url: str) -> tuple[str, str]:
    document = trafilatura.bare_extraction(page, url=url, with_metadata=True, include_comments=False,
                                           include_tables=False, favor_precision=True)
    if document is not None and (document.text or document.description):
        return document.description or "", document.text or ""
    metadata = trafilatura.extract_metadata(page, default_url=url)
    return (metadata.description or "") if metadata is not None else "", ""


class NewsReader:
    def __init__(self):
        self._sites: dict[str, _Site] = {}
        self._gate = asyncio.Semaphore(settings.NEWS_READ_PARALLEL)

    def _site(self, host: str) -> _Site:
        return self._sites.setdefault(host, _Site())

    async def _allowed(self, url: str, site: _Site) -> bool:
        if site.robots is None or time.monotonic() - site.robots_at > settings.NEWS_READ_ROBOTS_TTL_S:
            parts = urlsplit(url)
            robots = RobotFileParser()
            try:
                response = await fetch("GET", f"{parts.scheme}://{parts.netloc}/robots.txt", retries=0, circuit=True)
                if response.status_code in (401, 403):
                    robots.disallow_all = True
                elif response.status_code >= 400:
                    robots.allow_all = True
                else:
                    robots.parse(response.text.splitlines())
            except Exception:
                return False
            site.robots, site.robots_at = robots, time.monotonic()
        return site.robots.can_fetch(USER_AGENT, url)

    def _note(self, site: _Site, ok: bool) -> None:
        if ok:
            site.failures = 0
            return
        site.failures += 1
        if site.failures >= settings.NEWS_READ_SITE_FAILURES:
            site.resting_until = time.monotonic() + settings.NEWS_READ_SITE_REST_S
            site.failures = 0

    async def read(self, url: str, title: str) -> tuple[Optional[str], str]:
        host = urlsplit(url).netloc.lower()
        site = self._site(host)
        if not host or time.monotonic() < site.resting_until:
            return None, READ_BLOCKED
        async with self._gate:
            try:
                if not await self._allowed(url, site):
                    return None, READ_BLOCKED
                response = await fetch("GET", url, retries=1, circuit=True, headers=PAGE_HEADERS)
                if response.status_code in (401, 403, 451):
                    self._note(site, False)
                    usage_tracking.record_api_call("news", "article_read", error=True)
                    return None, READ_BLOCKED
                response.raise_for_status()
                if "html" not in response.headers.get("content-type", "html"):
                    self._note(site, False)
                    return None, READ_EMPTY
                description, text = await asyncio.to_thread(_extract, response.text, str(response.url))
            except Exception as e:
                self._note(site, False)
                usage_tracking.record_api_call("news", "article_read", error=True)
                log_service.throttled(f"news_read:{host}", f"News: could not read {host} ({type(e).__name__}: {e})")
                return None, READ_FAILED
        usage_tracking.record_api_call("news", "article_read")
        summary = summarize(title, description, text, settings.NEWS_SUMMARY_CHARS)
        if len(summary) < MIN_SUMMARY_CHARS:
            self._note(site, False)
            return None, READ_EMPTY
        self._note(site, True)
        return summary, READ_OK
