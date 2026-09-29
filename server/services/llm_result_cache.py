import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from config.settings import settings
from services import log_service
from services.llm_telemetry import record_cache_hit

FLUSH_DELAY_S = 5.0


def cache_key(*parts: Any) -> str:
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class LLMResultCache:
    def __init__(self, namespace: str, ttl_s: int, persist: bool = False, max_entries: int = 5000):
        self.namespace = namespace
        self.ttl_s = ttl_s
        self.persist = persist
        self.max_entries = max_entries
        self._entries: dict[str, tuple[float, Any]] = {}
        self._loaded = not persist
        self._flush_task: Optional[asyncio.Task] = None
        self._locks: dict[str, asyncio.Lock] = {}
        self.hits = 0
        self.misses = 0

    def lock(self, key: str) -> asyncio.Lock:
        if len(self._locks) > 256:
            self._locks = {k: lock for k, lock in self._locks.items() if lock.locked()}
        return self._locks.setdefault(key, asyncio.Lock())

    @property
    def path(self) -> Path:
        return Path(settings.LLM_RESULT_CACHE_DIR) / f"{self.namespace}.json"

    def _load(self) -> None:
        self._loaded = True
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            now = time.time()
            self._entries = {k: (ts, v) for k, (ts, v) in raw.items() if now - ts < self.ttl_s}
            log_service.ai(f"[LLM CACHE] Loaded {len(self._entries)} {self.namespace} entries")
        except FileNotFoundError:
            self._entries = {}
        except (OSError, ValueError, TypeError) as e:
            log_service.warning(f"[LLM CACHE] Could not load {self.path}: {e}")
            self._entries = {}

    def get(self, key: str) -> Optional[Any]:
        if not self._loaded:
            self._load()
        entry = self._entries.get(key)
        if entry and time.time() - entry[0] < self.ttl_s:
            self.hits += 1
            record_cache_hit(self.namespace)
            return entry[1]
        if entry:
            self._entries.pop(key, None)
        self.misses += 1
        return None

    def set(self, key: str, value: Any) -> None:
        if value is None or value == "":
            return
        if not self._loaded:
            self._load()
        self._entries[key] = (time.time(), value)
        if len(self._entries) > self.max_entries:
            for stale in sorted(self._entries, key=lambda k: self._entries[k][0])[:len(self._entries) - self.max_entries]:
                self._entries.pop(stale, None)
        if self.persist:
            self._schedule_flush()

    def _schedule_flush(self) -> None:
        if self._flush_task and not self._flush_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._write(self._snapshot())
            return
        self._flush_task = loop.create_task(self._delayed_flush())

    async def _delayed_flush(self) -> None:
        await asyncio.sleep(FLUSH_DELAY_S)
        await asyncio.to_thread(self._write, self._snapshot())

    def _snapshot(self) -> dict:
        now = time.time()
        return {k: [ts, v] for k, (ts, v) in self._entries.items() if now - ts < self.ttl_s}

    def _write(self, snapshot: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError as e:
            log_service.warning(f"[LLM CACHE] Could not save {self.path}: {e}")


breath_script_cache = LLMResultCache("breath_scripts", settings.LLM_CACHE_BREATH_TTL_S, persist=True)
interpretation_caches = {
    "news": LLMResultCache("news_interpretations", settings.LLM_CACHE_NEWS_TTL_S),
    "weather": LLMResultCache("weather_interpretations", settings.LLM_CACHE_WEATHER_TTL_S),
    "biography": LLMResultCache("biography_interpretations", settings.LLM_CACHE_BIOGRAPHY_TTL_S, persist=True),
    "lyrics": LLMResultCache("lyrics_interpretations", settings.LLM_CACHE_LYRICS_TTL_S, persist=True),
}
