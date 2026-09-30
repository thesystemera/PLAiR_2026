import asyncio
import re
from typing import Optional

import httpx
import trafilatura

from config import settings
from services import log_service, web_fetch

READ_OK = "ok"
READ_EMPTY = "empty"
READ_BLOCKED = "blocked"
READ_FAILED = "failed"

SENTENCE_END = re.compile(r"(?:(?<=[.!?])|(?<=[.!?][\"'”’]))\s+(?=[\"'“‘]?[A-Z0-9])")
MIN_SUMMARY_CHARS = 40
USAGE = ("news", "article_read")


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
        self._gate = asyncio.Semaphore(settings.NEWS_READ_PARALLEL)

    async def read(self, url: str, title: str) -> tuple[Optional[str], str]:
        policy = web_fetch.page_policy()
        async with self._gate:
            try:
                response = await web_fetch.get(url, policy, USAGE, retries=1)
            except (web_fetch.HostResting, web_fetch.Disallowed):
                return None, READ_BLOCKED
            except httpx.HTTPError as e:
                log_service.throttled(f"news_read:{url.split('/')[2]}", f"News: could not read {url.split('/')[2]} "
                                                                        f"({type(e).__name__}: {e})")
                return None, READ_FAILED
        if response.status_code in web_fetch.BLOCKED_STATUSES:
            return None, READ_BLOCKED
        if response.status_code >= 400 or "html" not in response.headers.get("content-type", "html"):
            return None, READ_FAILED
        description, text = await asyncio.to_thread(_extract, response.text, str(response.url))
        summary = summarize(title, description, text, settings.NEWS_SUMMARY_CHARS)
        if len(summary) < MIN_SUMMARY_CHARS:
            web_fetch.report(url, False, policy)
            return None, READ_EMPTY
        return summary, READ_OK
