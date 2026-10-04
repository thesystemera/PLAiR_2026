from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from services.catalog_names import NameLookup, track_names
from services.catalog_vector_database_service import TAG_LISTS

LABEL_ASPECTS = set(TAG_LISTS) | {"primary_genre", "vocal"}
NAME_ASPECTS = {"primary_artist", "similar_artists"}


@dataclass(frozen=True)
class Aspect:
    category: str
    weight: float = 1.0
    words: Optional[str] = None


@dataclass
class TagIndex:
    rows: np.ndarray
    starts: np.ndarray
    counts: np.ndarray
    vectors: np.ndarray


def _unit(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return np.divide(vectors, norms, out=np.zeros_like(vectors), where=norms > 0)


def _words(category: str, words: str) -> List[str]:
    parts = words.split(",") if category in LABEL_ASPECTS | NAME_ASPECTS else [words]
    return [part.strip() for part in parts if part.strip()]


def _match(similarity: np.ndarray, starts: np.ndarray, counts: np.ndarray, both: bool = True) -> np.ndarray:
    backward = np.add.reduceat(similarity.max(axis=0), starts) / counts
    if not both:
        return backward
    forward = np.maximum.reduceat(similarity, starts, axis=1).mean(axis=0)
    return (forward + backward) / 2


def station_categories(vector_db) -> List[str]:
    return [category for category in vector_db.categories if category != "song_title"]


def build_tag_indexes(vector_db, metas: List[Dict[str, Any]]) -> Dict[str, Optional[TagIndex]]:
    indexes: Dict[str, Optional[TagIndex]] = {}
    for category in station_categories(vector_db):
        if category in NAME_ASPECTS:
            continue
        rows, starts, texts = [], [], []
        for row, meta in enumerate(metas):
            tags = vector_db.category_tags(meta).get(category) or []
            if tags:
                rows.append(row)
                starts.append(len(texts))
                texts.extend(tags)
        indexes[category] = TagIndex(
            np.array(rows), np.array(starts), np.diff(np.array(starts + [len(texts)])),
            _unit(np.array(vector_db.ensure_embeddings(category, texts), dtype=np.float32))) if texts else None
    return indexes


class AspectRanker:
    """Stations built from aspects of songs (or from words): each aspect scores every song on its own - genre,
    mood, style and the rest by meaning, tag by tag; artists by spelling - and a blend is those scores weighted.
    Every anchor (the seed, each recent song) counts equally. Reads the live catalog index slot."""

    def __init__(self, vector_db, names: NameLookup):
        self.vector_db = vector_db
        self.names = names

    def categories(self) -> List[str]:
        return station_categories(self.vector_db)

    def _meaning(self, slot, category: str, anchors: List[Tuple[List[str], bool]]) -> Optional[np.ndarray]:
        index = slot.tags.get(category)
        anchors = [(tags, whole) for tags, whole in anchors if tags]
        if index is None or not anchors:
            return None
        total = np.zeros(len(index.rows), dtype=np.float32)
        for tags, whole in anchors:
            anchor = _unit(np.array(self.vector_db.ensure_embeddings(category, tags), dtype=np.float32))
            similarity = anchor @ index.vectors.T
            total += (_match(similarity, index.starts, index.counts) if whole
                      else np.maximum.reduceat(similarity, index.starts, axis=1).mean(axis=0))
        scores = np.full(len(slot.metas), np.nan, dtype=np.float32)
        scores[index.rows] = total / len(anchors)
        return scores

    def _spelling(self, slot, field: str, anchors: List[List[str]], both: bool = True) -> Optional[np.ndarray]:
        anchors = [names for names in anchors if names]
        index = slot.names.indexes.get(field)
        if index is None or not anchors:
            return None
        total = np.zeros(len(index.rows), dtype=np.float32)
        for names in anchors:
            total += _match(self.names.similarity(field, names, slot)[1], index.starts, index.counts, both)
        scores = np.full(len(slot.metas), np.nan, dtype=np.float32)
        scores[index.rows] = total / len(anchors)
        return scores

    def _named_artist_scene(self, slot, words: List[str]) -> List[str]:
        artist = self._spelling(slot, "artist", [words])
        if artist is None or np.all(np.isnan(artist)):
            return []
        best = np.nanmax(artist)
        return list(dict.fromkeys(name for i in np.flatnonzero(artist == best)
                                  for name in track_names(slot.metas[i], "similar_artists")))

    def _names(self, slot, aspect: Aspect, seed: Optional[Dict[str, Any]],
               recent: List[Dict[str, Any]]) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        if aspect.words:
            artists = [_words(aspect.category, aspect.words)]
            similar = [self._named_artist_scene(slot, artists[0])]
        else:
            artists = [track_names(seed, "artist")] if seed else []
            similar = [track_names(seed, "similar_artists")] if seed else []
        artists += [track_names(track, "artist") for track in recent]
        similar += [track_names(track, "similar_artists") for track in recent]

        same = self._spelling(slot, "artist", artists)
        named = self._spelling(slot, "artist", similar, both=False)
        scene = self._spelling(slot, "similar_artists", similar)
        parts = [part for part in ((same,) if aspect.category == "primary_artist" else ()) + (named, scene)
                 if part is not None]
        if not parts:
            return None, None
        with np.errstate(invalid="ignore"):
            scores = np.fmax.reduce(np.vstack(parts), axis=0)
        if aspect.category == "similar_artists":
            own = {name.lower() for names in artists for name in names}
            for i, meta in enumerate(slot.metas):
                if own & {name.lower() for name in track_names(meta, "artist")}:
                    scores[i] = np.nan
        return scores, same

    def _scores(self, slot, aspect: Aspect, seed: Optional[Dict[str, Any]],
                recent: List[Dict[str, Any]]) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        if aspect.category in NAME_ASPECTS:
            return self._names(slot, aspect, seed, recent)
        tags = self.vector_db.category_tags
        anchors = [(_words(aspect.category, aspect.words), False)] if aspect.words else (
            [(tags(seed).get(aspect.category) or [], True)] if seed else [])
        anchors += [(tags(track).get(aspect.category) or [], True) for track in recent]
        return self._meaning(slot, aspect.category, anchors), None

    def rank(self, aspects: List[Aspect], seed: Optional[Dict[str, Any]], recent: List[Dict[str, Any]],
             n_results: int, allowed: Callable[[Dict[str, Any]], bool]) -> List[Dict[str, Any]]:
        slot = self.vector_db.current()
        if slot is None:
            return []
        metas = slot.metas
        weighted, weights, artist = [], [], np.zeros(len(metas), dtype=np.float32)
        for aspect in aspects:
            scores, same = self._scores(slot, aspect, seed, recent)
            if scores is None or aspect.weight <= 0:
                continue
            weighted.append(scores * aspect.weight)
            weights.append(np.where(np.isnan(scores), 0.0, aspect.weight))
            if same is not None:
                artist += np.nan_to_num(same) * aspect.weight
        if not weighted:
            return []
        total_weight = np.sum(weights, axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            final = np.nansum(np.vstack(weighted), axis=0) / total_weight
        final[total_weight == 0] = np.nan
        out = []
        for i in np.lexsort((-artist, -np.nan_to_num(final, nan=-np.inf))):
            if np.isnan(final[i]) or not allowed(metas[i]):
                continue
            out.append({**metas[i], "similarity_score": float(final[i])})
            if len(out) >= n_results:
                break
        return out


def aspects_for(mode: str, ranker: AspectRanker) -> List[Aspect]:
    return [Aspect(category) for category in ranker.categories()] if mode == "all" else [Aspect(mode)]
