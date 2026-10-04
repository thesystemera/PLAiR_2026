"""The one pattern every vector engine follows (the TTS clip cache's, generalised).

- Live: searches read the live slot plus the items added since it was built (the pending slot). Nothing is ever
  rebuilt or bulk-embedded while someone waits.
- Ingest: a new or edited item is embedded on its own (`add_entries`) and is searchable at once; a removed one is
  hidden at once (`remove`). Either marks the store dirty.
- Background: `BackgroundTasksService.vector_store_maintainer` rebuilds every dirty store every
  `VECTOR_REBUILD_INTERVAL_S`: the fresh slot is built in a worker thread into the spare slot (1/2) and swapped in
  under the lock, and the pending items it now holds are dropped.
- Boot: `load()` checks the stored data (`restore`, e.g. index files against the database) and builds slot 1 if
  there is nothing valid to restore.
"""
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

from config.settings import settings
from models_global import get_sentence_encoder
from services import log_service

EMBEDDING_DIM = settings.SEMANTIC_ENCODER_DIM
QUERY_VECTOR_CACHE_MAX = 4096

STORES: List["VectorStore"] = []

_query_vectors: "OrderedDict[str, np.ndarray]" = OrderedDict()
_query_lock = Lock()


def encode(texts: List[str]) -> List[np.ndarray]:
    if not texts:
        return []
    vectors = get_sentence_encoder(settings.SEMANTIC_ENCODER).encode(
        [text if text and text.strip() else " " for text in texts], batch_size=64, normalize_embeddings=True,
        convert_to_numpy=True)
    return [np.zeros(EMBEDDING_DIM, dtype=np.float32) if not (text and text.strip())
            else np.asarray(vector, dtype=np.float32) for text, vector in zip(texts, vectors)]


def query_vector(text: str) -> np.ndarray:
    with _query_lock:
        cached = _query_vectors.get(text)
        if cached is not None:
            _query_vectors.move_to_end(text)
            return cached
    vector = encode([text])[0]
    vector.setflags(write=False)
    with _query_lock:
        _query_vectors[text] = vector
        while len(_query_vectors) > QUERY_VECTOR_CACHE_MAX:
            _query_vectors.popitem(last=False)
    return vector


def prime_query_vectors(texts: List[str]) -> None:
    with _query_lock:
        missing = list(dict.fromkeys(text for text in texts if text and text not in _query_vectors))
    if not missing:
        return
    vectors = encode(missing)
    with _query_lock:
        for text, vector in zip(missing, vectors):
            vector.setflags(write=False)
            _query_vectors[text] = vector
        while len(_query_vectors) > QUERY_VECTOR_CACHE_MAX:
            _query_vectors.popitem(last=False)


def unit(vector: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector


@dataclass
class Entry:
    row_id: int
    key: str
    meta: Any
    payload: Any
    seq: int = 0


@dataclass
class Slot:
    entries: List[Entry] = field(default_factory=list)

    def by_key(self) -> Dict[str, Entry]:
        return {entry.key: entry for entry in self.entries}


class VectorStore:
    name = "vectors"
    log_channel = "system"

    def __init__(self):
        self._lock = Lock()
        self._build_lock = Lock()
        self._slots: Dict[int, Optional[Slot]] = {1: None, 2: None}
        self._live = 1
        self._pending: Dict[str, Entry] = {}
        self._pending_slot: Optional[Slot] = None
        self._hidden: Dict[str, int] = {}
        self._seq = 0
        self.dirty = False
        self.version = 0
        STORES.append(self)

    def _log(self, message: str):
        getattr(log_service, self.log_channel)(message)

    def load_rows(self, keys: Optional[List[str]] = None) -> List[Tuple[int, str, Any]]:
        raise NotImplementedError

    def embed(self, meta: Any, persist: bool) -> Any:
        raise NotImplementedError

    def build_slot(self, entries: List[Entry], target: Optional[int], basis: Optional[Slot]) -> Slot:
        return Slot(entries)

    def restore(self) -> bool:
        return False

    def release_slot(self, slot: Slot) -> None:
        pass

    def load(self) -> None:
        started = time.perf_counter()
        if not self.restore():
            self.rebuild()
        self._log(f"{self.name}: ready ({len(self.entries())} items, {time.perf_counter() - started:.1f}s)")

    def live(self) -> Optional[Slot]:
        return self._slots[self._live]

    def views(self) -> Tuple[List[Slot], set]:
        with self._lock:
            slots = [slot for slot in (self._slots[self._live], self._pending_slot) if slot is not None]
            return slots, set(self._hidden)

    def entries(self) -> List[Entry]:
        slots, hidden = self.views()
        found: Dict[str, Entry] = {}
        for slot in slots:
            for entry in slot.entries:
                if entry.key not in hidden:
                    found[entry.key] = entry
        return list(found.values())

    def metas(self) -> List[Any]:
        return [entry.meta for entry in self.entries()]

    def keys(self) -> set:
        return {entry.key for entry in self.entries()}

    def find(self, key: str) -> Optional[Entry]:
        slots, hidden = self.views()
        if key in hidden:
            return None
        for slot in reversed(slots):
            for entry in slot.entries:
                if entry.key == key:
                    return entry
        return None

    def add_entries(self, entries: Iterable[Tuple[int, str, Any, Any]]) -> int:
        fresh = list(entries)
        if not fresh:
            return 0
        with self._lock:
            for row_id, key, meta, payload in fresh:
                self._seq += 1
                self._pending[key] = Entry(row_id, key, meta, payload, self._seq)
                self._hidden.pop(key, None)
            self._refresh_pending()
        return len(fresh)

    def add_rows(self, keys: List[str]) -> int:
        rows = self.load_rows(list(keys)) if keys else []
        return self.add_entries((row_id, key, meta, self.embed(meta, persist=True)) for row_id, key, meta in rows)

    def add_meta(self, key: str, meta: Any, row_id: int = 0) -> int:
        return self.add_entries([(row_id, key, meta, self.embed(meta, persist=True))])

    def remove(self, key: str) -> None:
        with self._lock:
            self._seq += 1
            self._hidden[key] = self._seq
            self._pending.pop(key, None)
            self._refresh_pending()

    def _refresh_pending(self) -> None:
        self._pending_slot = self.build_slot(list(self._pending.values()), None, self._slots[self._live]) \
            if self._pending else None
        self.dirty = True
        self.version += 1

    def rebuild(self) -> None:
        if not self._build_lock.acquire(blocking=False):
            return
        try:
            started = time.perf_counter()
            with self._lock:
                seq = self._seq
                self.dirty = False
                target = 1 if self._slots[self._live] is None else 3 - self._live
                spare = self._slots[target]
            if spare is not None:
                with self._lock:
                    self._slots[target] = None
                    self.release_slot(spare)
            entries = [Entry(row_id, key, meta, self.embed(meta, persist=True))
                       for row_id, key, meta in self.load_rows()]
            slot = self.build_slot(entries, target, None)
            with self._lock:
                self._slots[target] = slot
                self._live = target
                self._pending = {key: entry for key, entry in self._pending.items() if entry.seq > seq}
                self._hidden = {key: at for key, at in self._hidden.items() if at > seq}
                self._pending_slot = self.build_slot(list(self._pending.values()), None, slot) \
                    if self._pending else None
                self.version += 1
            self._log(f"{self.name}: rebuilt {len(entries)} items into slot {target} "
                      f"({time.perf_counter() - started:.1f}s)")
        except Exception:
            self.dirty = True
            raise
        finally:
            self._build_lock.release()
