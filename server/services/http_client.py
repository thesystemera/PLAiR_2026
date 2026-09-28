import asyncio
import logging
from typing import Optional

import httpx

from services import log_service

USER_AGENT = "PLAiR/1.0 (+https://plair.live; AI radio station)"
RETRY_STATUSES = {429, 500, 502, 503, 504}

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_client: Optional[httpx.AsyncClient] = None


def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _client


async def fetch(method: str, url: str, retries: int = 2, **kwargs) -> httpx.Response:
    client = get_http_client()
    for attempt in range(retries + 1):
        try:
            response = await client.request(method, url, **kwargs)
            if response.status_code not in RETRY_STATUSES or attempt == retries:
                return response
        except (httpx.TransportError, httpx.TimeoutException) as e:
            if attempt == retries:
                raise
            log_service.external(f"HTTP retry {attempt + 1} for {url.split('?')[0]}: {type(e).__name__}")
        await asyncio.sleep(0.5 * 2 ** attempt)
    raise RuntimeError("unreachable")


async def close_http_client():
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
