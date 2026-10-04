import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from config.settings import settings
from database.pg_pool import get_pooled_connection
from services.vector_store import EMBEDDING_DIM, Entry, Slot, VectorStore, encode, query_vector, unit


def embeddings_table(category: str) -> str:
    return f"{category}_{settings.SEMANTIC_ENCODER_SLUG}_embeddings"


@dataclass
class Match:
    score: float
    similarity: float
    rowid: int
    meta: Dict[str, Any]
    key: str = ""


@dataclass
class CategorySlot(Slot):
    matrices: Dict[str, np.ndarray] = field(default_factory=dict)


class CategoryStore(VectorStore):
    """Items described by named categories (title, tags, mood, ...). Each category text (or tag) is embedded once
    and kept in its own table; an item's vector per category is the mean of its tags; a search weights the
    categories per query. Follows the shared VectorStore pattern (live slot + pending, background rebuild)."""

    embedding_dim: int = EMBEDDING_DIM
    categories: Tuple[str, ...] = ()
    default_weights: Dict[str, float] = {}
    display_name: str = ""
    source_table: str = ""
    source_id_column: str = ""
    key_field: str = "id"
    item_noun: str = "items"

    def __init__(self, source_service=None):
        super().__init__()
        self.name = self.display_name
        self.source_service = source_service
        self.caches: Dict[str, Dict[str, np.ndarray]] = {category: {} for category in self.categories}

    def _get_connection(self):
        return get_pooled_connection(settings.EMBEDDINGS_DATABASE_URL)

    def load(self) -> None:
        self._load_tables()
        super().load()

    def _load_tables(self) -> None:
        conn = self._get_connection()
        try:
            c = conn.cursor()
            for category, cache in self.caches.items():
                table = embeddings_table(category)
                c.execute(f"CREATE TABLE IF NOT EXISTS {table} (text TEXT PRIMARY KEY, embedding BYTEA NOT NULL)")
                conn.commit()
                c.execute(f"SELECT text, embedding FROM {table}")
                for text, embedding in c.fetchall():
                    cache[text] = np.frombuffer(bytes(embedding), dtype=np.float32)
        finally:
            conn.close()

    def load_rows(self, keys: Optional[List[str]] = None) -> List[Tuple[int, str, Any]]:
        conn = self.source_service._get_connection()
        try:
            c = conn.cursor()
            query = f"SELECT rowid, {self.source_id_column}, metadata_json FROM {self.source_table}"
            if keys is None:
                c.execute(query)
            else:
                c.execute(f"{query} WHERE {self.source_id_column} = ANY(%s)", (list(keys),))
            rows = c.fetchall()
        finally:
            conn.close()
        return [(rowid, key, json.loads(metadata_json)) for rowid, key, metadata_json in rows]

    def _extract_category_texts(self, item: Dict[str, Any]) -> Dict[str, str]:
        raise NotImplementedError

    def category_tags(self, item: Dict[str, Any]) -> Dict[str, List[str]]:
        return {category: [text.strip()] if text and text.strip() else []
                for category, text in self._extract_category_texts(item).items()}

    def ensure_embeddings(self, category: str, texts: List[str], persist: bool = True) -> List[np.ndarray]:
        cache = self.caches[category]
        texts = [text.strip() for text in texts]
        missing = list(dict.fromkeys(text for text in texts if text and text not in cache))
        if missing and not persist:
            found = {text: query_vector(text) for text in missing}
            return [cache.get(text, found.get(text)) if text else np.zeros(EMBEDDING_DIM, dtype=np.float32)
                    for text in texts]
        if missing:
            vectors = encode(missing)
            conn = self._get_connection()
            try:
                c = conn.cursor()
                for text, vector in zip(missing, vectors):
                    c.execute(f"INSERT INTO {embeddings_table(category)} (text, embedding) VALUES (%s, %s) "
                              f"ON CONFLICT (text) DO NOTHING", (text, vector.tobytes()))
                    cache[text] = vector
                conn.commit()
            finally:
                conn.close()
        return [cache[text] if text else np.zeros(EMBEDDING_DIM, dtype=np.float32) for text in texts]

    def embed(self, meta: Dict[str, Any], persist: bool) -> Dict[str, np.ndarray]:
        return {category: unit(np.mean(self.ensure_embeddings(category, tags, persist), axis=0))
                for category, tags in self.category_tags(meta).items() if tags}

    def build_slot(self, entries: List[Entry], target: Optional[int], basis: Optional[Slot]) -> CategorySlot:
        zero = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        matrices = {category: np.array([entry.payload.get(category, zero) for entry in entries], dtype=np.float32)
                    .reshape(len(entries), EMBEDDING_DIM) for category in self.categories}
        return CategorySlot(entries, matrices)

    def vectors_for(self, meta: Dict[str, Any]) -> Dict[str, np.ndarray]:
        key = meta.get(self.key_field)
        entry = self.find(str(key)) if key else None
        return entry.payload if entry is not None else self.embed(meta, persist=False)

    def weighted(self, meta: Dict[str, Any], weights: Optional[Dict[str, float]] = None) -> np.ndarray:
        return self.combine(self.vectors_for(meta), weights)

    def combine(self, vectors: Dict[str, np.ndarray], weights: Optional[Dict[str, float]] = None) -> np.ndarray:
        combined = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        for category, weight in (weights or self.default_weights).items():
            if category in vectors:
                combined += vectors[category] * weight
        return unit(combined)

    def _generate_embedding(self, text: str) -> np.ndarray:
        if not text or not text.strip():
            return np.zeros(EMBEDDING_DIM, dtype=np.float32)
        return query_vector(text)

    def rank(self, vector: np.ndarray, weights: Dict[str, float], n: int,
             keep: Optional[Callable[[Dict[str, Any]], bool]] = None,
             boost: Optional[Callable[[Dict[str, Any]], float]] = None, exclude: Tuple[str, ...] = ()) -> List[Match]:
        slots, hidden = self.views()
        seen = set(hidden) | set(exclude)
        matches: List[Match] = []
        for slot in reversed(slots):
            if not slot.entries:
                continue
            combined = sum(slot.matrices[category] * weight for category, weight in weights.items()
                           if category in slot.matrices and weight)
            if isinstance(combined, int):
                continue
            norms = np.linalg.norm(combined, axis=1)
            similarity = np.divide(combined @ vector, norms, out=np.zeros(len(norms), dtype=np.float32),
                                   where=norms > 0)
            for i, entry in enumerate(slot.entries):
                if entry.key in seen:
                    continue
                seen.add(entry.key)
                if keep is not None and not keep(entry.meta):
                    continue
                score = float(similarity[i])
                matches.append(Match(score + (boost(entry.meta) if boost else 0.0), score, entry.row_id,
                                     entry.meta, entry.key))
        matches.sort(key=lambda match: match.score, reverse=True)
        return matches[:n]
