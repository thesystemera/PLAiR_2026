import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from google.genai import types

from config.settings import settings
from services import log_service
from services.task_utils import spawn


def is_cache_missing_error(err: Exception) -> bool:
    message = str(err).lower()
    return ("cached" in message and ("not found" in message or "permission_denied" in message
                                     or "expired" in message or "does not exist" in message))


class SystemCacheManager:
    def __init__(self):
        self._memo: Dict[str, Tuple[Optional[str], float]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    @staticmethod
    def _key(label: str, model: str, system_instruction: str, tools) -> str:
        tools_json = json.dumps([tool.model_dump(exclude_none=True) for tool in tools or []], sort_keys=True,
                                default=str)
        digest = hashlib.sha256(f"{system_instruction}\n{tools_json}".encode("utf-8")).hexdigest()[:12]
        return f"{label}:{model}:{digest}"

    async def get(self, client, label: str, model: str, system_instruction: Optional[str], tools) -> Optional[str]:
        if not settings.GEMINI_CACHE_ENABLED or client is None or not system_instruction:
            return None
        key = self._key(label, model, system_instruction, tools)
        name = self._fresh(key, client)
        if name is not None or key in self._memo and self._memo[key][1] > time.monotonic():
            return name
        async with self._locks.setdefault(key, asyncio.Lock()):
            name = self._fresh(key, client)
            if name is not None or key in self._memo and self._memo[key][1] > time.monotonic():
                return name
            return await self._create(client, key, label, model, system_instruction, tools)

    def _fresh(self, key: str, client) -> Optional[str]:
        name, expires = self._memo.get(key, (None, 0.0))
        remaining = expires - time.monotonic()
        if name is None or remaining <= 0:
            return None
        if remaining < settings.GEMINI_CACHE_TTL_S / 2:
            self._memo[key] = (name, time.monotonic() + settings.GEMINI_CACHE_TTL_S * 0.9)
            spawn(self._renew(client, name), name=f"gemini_cache_renew:{key}")
        return name

    async def _renew(self, client, name: str) -> None:
        try:
            await client.aio.caches.update(
                name=name, config=types.UpdateCachedContentConfig(ttl=f"{settings.GEMINI_CACHE_TTL_S}s"))
        except Exception as e:
            log_service.warning(f"[GEMINI CACHE] renew failed for {name}: {type(e).__name__}: {e}")
            self.invalidate(name)

    async def _create(self, client, key: str, label: str, model: str, system_instruction: str, tools) -> Optional[str]:
        from services.llm_telemetry import record_usage

        display_name = key.replace(":", "_")[:120]
        existing = await self._find_existing(client, display_name)
        if existing is not None:
            name, seconds_left = existing
            self._memo[key] = (name, time.monotonic() + seconds_left * 0.9)
            log_service.system(f"[GEMINI CACHE] {label} on {model}: reusing {name} ({int(seconds_left)}s left)")
            return name

        started = time.perf_counter()
        try:
            cache = await client.aio.caches.create(model=model, config=types.CreateCachedContentConfig(
                system_instruction=system_instruction, tools=tools, display_name=display_name,
                ttl=f"{settings.GEMINI_CACHE_TTL_S}s"))
        except Exception as e:
            log_service.warning(f"[GEMINI CACHE] {label} on {model} not cached "
                                f"(retry in {settings.GEMINI_CACHE_RETRY_S}s): {type(e).__name__}: {str(e)[:160]}")
            self._memo[key] = (None, time.monotonic() + settings.GEMINI_CACHE_RETRY_S)
            return None
        tokens = int(getattr(getattr(cache, "usage_metadata", None), "total_token_count", 0) or 0)
        record_usage(role=f"{label}_cache", provider="gemini", model=model, prompt_tokens=tokens,
                     ms=(time.perf_counter() - started) * 1000)
        self._memo[key] = (cache.name, time.monotonic() + settings.GEMINI_CACHE_TTL_S * 0.9)
        log_service.system(f"[GEMINI CACHE] {label} on {model}: {tokens} tokens cached as {cache.name}")
        return cache.name

    @staticmethod
    async def _find_existing(client, display_name: str) -> Optional[Tuple[str, float]]:
        try:
            pager = await client.aio.caches.list()
            async for cache in pager:
                if getattr(cache, "display_name", None) != display_name or not getattr(cache, "expire_time", None):
                    continue
                seconds_left = (cache.expire_time - datetime.now(timezone.utc)).total_seconds()
                if seconds_left > 120:
                    return cache.name, seconds_left
        except Exception as e:
            log_service.warning(f"[GEMINI CACHE] listing caches failed: {type(e).__name__}: {e}")
        return None

    def invalidate(self, name: str) -> None:
        for key in [key for key, (cached, _) in self._memo.items() if cached == name]:
            self._memo.pop(key, None)


system_caches = SystemCacheManager()
