import asyncio
import re
from typing import Optional

import httpx
import nltk
import trafilatura

from config import settings
from services import log_service, web_fetch

READ_OK = "ok"
READ_EMPTY = "empty"
READ_BLOCKED = "blocked"
READ_FAILED = "failed"

SENTENCE_END = re.compile(r"(?:(?<=[.!?])|(?<=[.!?][\"'”’]))\s+(?=[\"'“‘]?[A-Z0-9])")
MIN_SUMMARY_CHARS = 40
MIN_BODY_CHARS = 250
MIN_SENTENCES = 3
PHOTO_CREDIT = re.compile(r"\b(Photos?|Image|Picture|Video|Graphic)\s*(/|:|by\b|credit\b)", re.IGNORECASE)
USAGE = ("news", "article_read")


def _fold(text: str) -> str:
    return " ".join(re.sub(r"[^\w ]", " ", (text or "").lower()).split())


_sentence_model_ready = False


def _ensure_sentence_model() -> None:
    global _sentence_model_ready
    if _sentence_model_ready:
        return
    try:
        nltk.data.find("tokenizers/punkt_tab")
    except LookupError:
        nltk.download("punkt_tab", quiet=True)
    _sentence_model_ready = True


def _sentences(text: str) -> list[str]:
    try:
        return [s for s in nltk.sent_tokenize(text) if s.strip()]
    except LookupError:
        return [s for s in SENTENCE_END.split(text) if s.strip()]


def summarize(title: str, description: str, text: str, limit: int) -> str:
    title_key = _fold(title)
    body = " ".join(line.strip() for line in (text or "").splitlines()
                    if line.strip() and _fold(line) != title_key and not PHOTO_CREDIT.search(line))
    sentences = _sentences(body) if body else []
    if len(body) >= MIN_BODY_CHARS and len(sentences) >= MIN_SENTENCES:
        summary = " ".join(sentences[:settings.NEWS_SUMMARY_SENTENCES])
        return summary if len(summary) <= limit else summary[:limit].rsplit(" ", 1)[0] + "..."
    description = " ".join((description or "").split())
    return description if len(description) >= MIN_SUMMARY_CHARS else ""


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
        await asyncio.to_thread(_ensure_sentence_model)
        summary = await asyncio.to_thread(summarize, title, description, text, settings.NEWS_SUMMARY_CHARS)
        if len(summary) < MIN_SUMMARY_CHARS:
            web_fetch.report(url, False, policy)
            return None, READ_EMPTY
        return summary, READ_OK
