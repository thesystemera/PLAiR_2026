import asyncio
import logging
import time
from typing import Optional
from urllib.parse import urlsplit

import httpx

from services import log_service

USER_AGENT = "PLAiR/1.0 (+https://plair.live; AI radio station)"
RETRY_STATUSES = {429, 500, 502, 503, 504}
CIRCUIT_STATUSES = {500, 502, 503, 504}
CIRCUIT_FAILURES = 3
CIRCUIT_OPEN_S = 30.0
CIRCUIT_MAX_OPEN_S = 300.0
CIRCUIT_PROBE_S = 45.0
ATTEMPT_DEADLINE_S = 20.0

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_client: Optional[httpx.AsyncClient] = None
_circuits: dict = {}


class HostUnavailable(httpx.TransportError):
    pass


def _circuit_allows(host: str) -> bool:
    state = _circuits.get(host)
    if state is None or state["open_until"] is None:
        return True
    now = time.monotonic()
    if now < state["open_until"]:
        return False
    state["open_until"] = now + CIRCUIT_PROBE_S
    return True


def _circuit_record(host: str, ok: bool):
    if ok:
        state = _circuits.pop(host, None)
        if state is not None and state["open_until"] is not None:
            log_service.external(f"HTTP host {host} reachable again")
        return
    state = _circuits.setdefault(host, {"failures": 0, "open_until": None})
    state["failures"] += 1
    if state["failures"] >= CIRCUIT_FAILURES:
        open_s = min(CIRCUIT_MAX_OPEN_S, CIRCUIT_OPEN_S * 2 ** min(state["failures"] - CIRCUIT_FAILURES, 8))
        state["open_until"] = time.monotonic() + open_s
        log_service.throttled(f"http_circuit:{host}",
                              f"HTTP host {host} failing ({state['failures']}x), pausing requests for {open_s:.0f}s")


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


async def _attempt(client: httpx.AsyncClient, method: str, url: str, circuit: bool, **kwargs) -> httpx.Response:
    if not circuit:
        return await client.request(method, url, **kwargs)
    try:
        async with asyncio.timeout(ATTEMPT_DEADLINE_S):
            return await client.request(method, url, **kwargs)
    except TimeoutError:
        raise httpx.ReadTimeout(f"no complete response within {ATTEMPT_DEADLINE_S:.0f}s")


async def fetch(method: str, url: str, retries: int = 2, circuit: bool = False, **kwargs) -> httpx.Response:
    client = get_http_client()
    host = urlsplit(url).netloc if circuit else ""
    if circuit and not _circuit_allows(host):
        raise HostUnavailable(f"{host} is backing off after repeated failures")
    for attempt in range(retries + 1):
        try:
            response = await _attempt(client, method, url, circuit, **kwargs)
            if response.status_code not in RETRY_STATUSES or attempt == retries:
                if circuit:
                    _circuit_record(host, response.status_code not in CIRCUIT_STATUSES)
                return response
        except (httpx.TransportError, httpx.TimeoutException) as e:
            if attempt == retries:
                if circuit:
                    _circuit_record(host, False)
                raise
            log_service.external(f"HTTP retry {attempt + 1} for {url.split('?')[0]}: {type(e).__name__}")
        await asyncio.sleep(0.5 * 2 ** attempt)
    raise RuntimeError("unreachable")


async def close_http_client():
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
