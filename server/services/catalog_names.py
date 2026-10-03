from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer

from services import log_service

NAME_FIELDS = ("artist", "title", "similar_artists")


def track_names(track: Dict[str, Any], field: str) -> List[str]:
    if field == "artist":
        names = log_service.track_artists(track)
    elif field == "title":
        names = [(track.get("generation_params") or {}).get("title"), (track.get("track_info") or {}).get("title")]
    else:
        names = (track.get("derived_tags") or {}).get("similar_artists") or []
        names = names.split(",") if isinstance(names, str) else names
    return list(dict.fromkeys(name.strip() for name in names if isinstance(name, str) and name.strip()))


@dataclass
class NameIndex:
    metas: List[Dict[str, Any]]
    rows: np.ndarray
    starts: np.ndarray
    counts: np.ndarray
    vectors: sparse.csr_matrix
    names: List[str]


class NameLookup:
    """Names compared by spelling, not meaning: character n-gram vectors, so typos, spacing, accents and a leading
    'the' still find the name, and two different names never match just because their words mean something alike."""

    def __init__(self, vector_db):
        self.vector_db = vector_db
        self._source = None
        self._spelling: Optional[TfidfVectorizer] = None
        self._indexes: Dict[str, NameIndex] = {}

    def _fresh(self) -> Dict[int, Dict[str, Any]]:
        source = self.vector_db._metadata_cache
        if self._source is not source:
            self._source, self._spelling, self._indexes = source, None, {}
        return source

    def spell(self, names: List[str]) -> sparse.csr_matrix:
        self._fresh()
        if self._spelling is None:
            every = {name for meta in self._source.values() for field in NAME_FIELDS
                     for name in track_names(meta, field)}
            self._spelling = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True,
                                             strip_accents="unicode").fit(sorted(every) or [" "])
        return self._spelling.transform(names)

    def index(self, field: str) -> Optional[NameIndex]:
        source = self._fresh()
        if field not in self._indexes:
            metas = list(source.values())
            rows, starts, names = [], [], []
            for row, meta in enumerate(metas):
                found = track_names(meta, field)
                if found:
                    rows.append(row)
                    starts.append(len(names))
                    names.extend(found)
            self._indexes[field] = NameIndex(
                metas, np.array(rows), np.array(starts), np.diff(np.array(starts + [len(names)])),
                self.spell(names) if names else sparse.csr_matrix((0, 0)), names) if names else None
        return self._indexes[field]

    def similarity(self, field: str, names: List[str]) -> Optional[Tuple[NameIndex, np.ndarray]]:
        index = self.index(field)
        names = [name.strip() for name in names if name and name.strip()]
        if index is None or not names:
            return None
        return index, (self.spell(names) @ index.vectors.T).toarray()

    def closest(self, field: str, text: str, how_many: int) -> List[Tuple[str, float]]:
        found = self.similarity(field, [text])
        if found is None:
            return []
        index, similarity = found
        best: Dict[str, float] = {}
        for name, score in zip(index.names, similarity[0]):
            best[name] = max(best.get(name, 0.0), float(score))
        return sorted(best.items(), key=lambda item: item[1], reverse=True)[:how_many]

    def find(self, field: str, text: str, n_results: int,
             allowed: Callable[[Dict[str, Any]], bool]) -> List[Dict[str, Any]]:
        found = self.similarity(field, [text])
        if found is None:
            return []
        index, similarity = found
        per_track = np.maximum.reduceat(similarity[0], index.starts)
        order = np.argsort(-per_track, kind="stable")
        out = []
        for i in order:
            meta = index.metas[index.rows[i]]
            if allowed(meta):
                out.append({**meta, "similarity_score": float(per_track[i])})
                if len(out) >= n_results:
                    break
        return out


def spelled_alike(score: float) -> bool:
    return bool(np.isclose(score, 1.0))
