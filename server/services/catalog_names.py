import itertools
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer

from services.catalog_credit import credited_artists

NAME_FIELDS = ("artist", "title", "similar_artists")
INDEX_SERIALS = itertools.count()


def track_names(track: Dict[str, Any], field: str) -> List[str]:
    if field == "artist":
        names = credited_artists(track)
    elif field == "title":
        names = [(track.get("generation_params") or {}).get("title"), (track.get("track_info") or {}).get("title")]
    else:
        names = (track.get("derived_tags") or {}).get("similar_artists") or []
        names = names.split(",") if isinstance(names, str) else names
    return list(dict.fromkeys(name.strip() for name in names if isinstance(name, str) and name.strip()))


@dataclass
class NameIndex:
    rows: np.ndarray
    starts: np.ndarray
    counts: np.ndarray
    vectors: sparse.csr_matrix
    names: List[str]
    serial: int = field(default_factory=lambda: next(INDEX_SERIALS))
    columns: Optional[sparse.csr_matrix] = None

    def __post_init__(self):
        self.columns = self.vectors.T.tocsr()


@dataclass
class NameSpace:
    spelling: TfidfVectorizer
    indexes: Dict[str, Optional[NameIndex]]
    artists: List[set] = field(default_factory=list)


def build_name_space(metas: List[Dict[str, Any]], spelling: Optional[TfidfVectorizer] = None) -> NameSpace:
    if spelling is None:
        every = sorted({name for meta in metas for kind in NAME_FIELDS for name in track_names(meta, kind)})
        spelling = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True,
                                   strip_accents="unicode").fit(every or [" "])
    indexes: Dict[str, Optional[NameIndex]] = {}
    for kind in NAME_FIELDS:
        rows, starts, names = [], [], []
        for row, meta in enumerate(metas):
            found = track_names(meta, kind)
            if found:
                rows.append(row)
                starts.append(len(names))
                names.extend(found)
        indexes[kind] = NameIndex(np.array(rows), np.array(starts), np.diff(np.array(starts + [len(names)])),
                                   spelling.transform(names), names) if names else None
    artists = [{name.lower() for name in track_names(meta, "artist")} for meta in metas]
    return NameSpace(spelling, indexes, artists)


class NameLookup:
    """Names compared by spelling, not meaning: character n-gram vectors, so typos, spacing, accents and a leading
    'the' still find the name, and two different names never match just because their words mean something alike.
    Reads the catalog store's live slot and the tracks added since."""

    def __init__(self, vector_db):
        self.vector_db = vector_db

    @staticmethod
    def similarity(field: str, names: List[str], slot) -> Optional[Tuple[NameIndex, np.ndarray]]:
        index = slot.names.indexes.get(field) if slot is not None and slot.names is not None else None
        names = [name.strip() for name in names if name and name.strip()]
        if index is None or not names:
            return None
        return index, (slot.names.spelling.transform(names) @ index.columns).toarray()

    def closest(self, field: str, text: str, how_many: int) -> List[Tuple[str, float]]:
        slots, _hidden = self.vector_db.views()
        best: Dict[str, float] = {}
        for slot in slots:
            found = self.similarity(field, [text], slot)
            if found is None:
                continue
            index, similarity = found
            for name, score in zip(index.names, similarity[0]):
                best[name] = max(best.get(name, 0.0), float(score))
        return sorted(best.items(), key=lambda item: item[1], reverse=True)[:how_many]

    def best_match(self, text: str, candidates: Dict[str, List[str]]) -> Optional[Tuple[str, float]]:
        slot = self.vector_db.live()
        keys = [key for key, names in candidates.items() for name in names if name and name.strip()]
        names = [name for key, names in candidates.items() for name in names if name and name.strip()]
        if slot is None or slot.names is None or not names or not text or not text.strip():
            return None
        spelling = slot.names.spelling
        scores = (spelling.transform([text]) @ spelling.transform(names).T).toarray()[0]
        best = int(np.argmax(scores))
        return keys[best], float(scores[best])

    def find(self, field: str, text: str, n_results: int,
             allowed: Callable[[Dict[str, Any]], bool]) -> List[Dict[str, Any]]:
        slots, hidden = self.vector_db.views()
        scored: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        for slot in slots:
            found = self.similarity(field, [text], slot)
            if found is None:
                continue
            index, similarity = found
            per_track = np.maximum.reduceat(similarity[0], index.starts)
            for i, score in enumerate(per_track):
                entry = slot.entries[index.rows[i]]
                if entry.key not in hidden:
                    scored[entry.key] = (float(score), entry.meta)
        out = []
        for score, meta in sorted(scored.values(), key=lambda item: item[0], reverse=True):
            if allowed(meta):
                out.append({**meta, "similarity_score": score})
                if len(out) >= n_results:
                    break
        return out


def spelled_alike(score: float) -> bool:
    return bool(np.isclose(score, 1.0))
