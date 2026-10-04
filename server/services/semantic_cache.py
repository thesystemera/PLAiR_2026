import asyncio
import hashlib
import time
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np

from config.settings import settings
from database.pg_pool import get_pooled_connection
from models_global import run_on_gpu_executor
from services import log_service, usage_tracking
from services.task_utils import spawn
from services.vector_store import EMBEDDING_DIM, query_vector


class SemanticCache:
    """Earlier answers to what was asked, reused: the exact words first, then the closest earlier ask by meaning
    above a threshold. Loaded at boot and re-embedded when the encoder changes; a hit is counted in the background;
    a new answer is usable at once and written to its table in the background. Used by the search-intent caches and
    the Producer's route cache."""

    def __init__(self, table: str, key_column: str, text_column: str, columns: Dict[str, str], label: str,
                 log: Callable[[str], None]):
        self.table = table
        self.key_column = key_column
        self.text_column = text_column
        self.columns = columns
        self.label = label
        self.log = log
        self.entries: Dict[str, Dict[str, Any]] = {}
        self.exact_hits = 0
        self.semantic_hits = 0
        self.misses = 0

    @staticmethod
    def key(text: str) -> str:
        return hashlib.md5(text.lower().strip().encode()).hexdigest()

    @staticmethod
    def _connection():
        return get_pooled_connection(settings.EMBEDDINGS_DATABASE_URL)

    def _prepare_table(self, migrate: Optional[Callable[[Any], None]]) -> None:
        conn = self._connection()
        try:
            c = conn.cursor()
            c.execute(f"CREATE TABLE IF NOT EXISTS {self.table} ({self.key_column} TEXT PRIMARY KEY, "
                      f"{self.text_column} TEXT, embedding BYTEA, created_at REAL, times_reused INTEGER DEFAULT 0, "
                      f"last_used REAL)")
            for column, sql_type in self.columns.items():
                c.execute(f"ALTER TABLE {self.table} ADD COLUMN IF NOT EXISTS {column} {sql_type}")
            if migrate is not None:
                migrate(c)
            conn.commit()
        finally:
            conn.close()

    def _load_rows(self) -> None:
        conn = self._connection()
        try:
            c = conn.cursor()
            columns = list(self.columns)
            c.execute(f"SELECT {self.key_column}, {self.text_column}, embedding, times_reused, last_used"
                      f"{''.join(', ' + column for column in columns)} FROM {self.table}")
            rows = c.fetchall()
        finally:
            conn.close()
        for key, text, embedding, times_reused, last_used, *values in rows:
            self.entries[key] = {
                "text": text,
                "embedding": np.frombuffer(bytes(embedding), dtype=np.float32) if embedding else None,
                "times_reused": times_reused or 0,
                "last_used": last_used,
                "row": dict(zip(columns, values)),
            }

    def _reembed_stale(self) -> int:
        stale = [(key, entry) for key, entry in self.entries.items()
                 if entry["embedding"] is None or entry["embedding"].shape[0] != EMBEDDING_DIM]
        if not stale:
            return 0
        conn = self._connection()
        try:
            c = conn.cursor()
            for key, entry in stale:
                entry["embedding"] = query_vector(entry["text"] or "")
                c.execute(f"UPDATE {self.table} SET embedding = %s WHERE {self.key_column} = %s",
                          (entry["embedding"].tobytes(), key))
            conn.commit()
        finally:
            conn.close()
        return len(stale)

    async def prepare(self, migrate: Optional[Callable[[Any], None]] = None) -> None:
        started = time.perf_counter()
        await asyncio.to_thread(self._prepare_table, migrate)
        await asyncio.to_thread(self._load_rows)
        reembedded = await run_on_gpu_executor(self._reembed_stale)
        self.log(f"✓ {self.label}: {len(self.entries)} cached entries ({time.perf_counter() - started:.2f}s)"
                 + (f", {reembedded} re-embedded for the current encoder" if reembedded else ""))

    async def find(self, text: str, threshold: float,
                   usable: Callable[[Dict[str, Any]], bool] = lambda entry: True
                   ) -> Optional[Tuple[str, Dict[str, Any], float]]:
        key = self.key(text)
        entry = self.entries.get(key)
        if entry is not None and usable(entry):
            self.exact_hits += 1
            self._note_hit(key, entry)
            return "exact", entry, 1.0
        vector = await run_on_gpu_executor(query_vector, text)
        best_key, best, best_similarity = None, None, 0.0
        for candidate_key, candidate in list(self.entries.items()):
            if candidate["embedding"] is None or not usable(candidate):
                continue
            similarity = float(np.dot(vector, candidate["embedding"]))
            if similarity > best_similarity:
                best_key, best, best_similarity = candidate_key, candidate, similarity
        if best is not None and best_similarity >= threshold:
            self.semantic_hits += 1
            self._note_hit(best_key, best)
            return "semantic", best, best_similarity
        if best is not None:
            self.log(f"  {self.label}: closest earlier ask '{(best['text'] or '')[:60]}' at {best_similarity:.3f} "
                     f"(below {threshold})")
        self.misses += 1
        return None

    def _note_hit(self, key: str, entry: Dict[str, Any]) -> None:
        usage_tracking.record_cache_hit(self.table)
        entry["times_reused"] += 1
        entry["last_used"] = time.time()
        spawn(asyncio.to_thread(self._write_stats, key, entry["times_reused"], entry["last_used"]),
              name=f"{self.table}_stats")

    def _write_stats(self, key: str, times_reused: int, last_used: float) -> None:
        conn = self._connection()
        try:
            c = conn.cursor()
            c.execute(f"UPDATE {self.table} SET times_reused = %s, last_used = %s WHERE {self.key_column} = %s",
                      (times_reused, last_used, key))
            conn.commit()
        except Exception as e:
            conn.rollback()
            log_service.warning(f"{self.label}: hit count not saved: {e}")
        finally:
            conn.close()

    async def save(self, text: str, row: Dict[str, Any]) -> None:
        vector = await run_on_gpu_executor(query_vector, text)
        key, now = self.key(text), time.time()
        self.entries[key] = {"text": text, "embedding": vector, "times_reused": 0, "last_used": now, "row": dict(row)}
        spawn(asyncio.to_thread(self._write_entry, key, text, vector, now, row), name=f"{self.table}_save")

    def _write_entry(self, key: str, text: str, vector: np.ndarray, now: float, row: Dict[str, Any]) -> None:
        columns = list(row)
        names = [self.key_column, self.text_column, "embedding", "created_at", "times_reused", "last_used", *columns]
        values = [key, text, vector.tobytes(), now, 0, now, *(row[column] for column in columns)]
        updates = ", ".join(f"{name} = EXCLUDED.{name}" for name in names[1:])
        conn = self._connection()
        try:
            c = conn.cursor()
            c.execute(f"INSERT INTO {self.table} ({', '.join(names)}) VALUES ({', '.join(['%s'] * len(names))}) "
                      f"ON CONFLICT ({self.key_column}) DO UPDATE SET {updates}", values)
            conn.commit()
        except Exception as e:
            conn.rollback()
            log_service.warning(f"{self.label}: new entry not saved: {e}")
        finally:
            conn.close()

    def hit_rate_line(self) -> Optional[str]:
        total = self.exact_hits + self.semantic_hits + self.misses
        if not total or total % 5:
            return None
        return (f"📊 {self.label}: {(self.exact_hits + self.semantic_hits) / total:.0%} hit rate "
                f"(exact {self.exact_hits}, by meaning {self.semantic_hits}, new {self.misses})")
