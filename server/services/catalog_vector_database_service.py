import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np

from services import log_service
from services.base_vector_database_service import BaseVectorDatabaseService
from services.catalog_credit import credited_artists
from services.catalog_vocals import VOCALS_TEXT, vocals_of

TAG_LISTS = {"secondary_genres": "secondary_genres", "mood": "mood_keywords", "similar_artists": "similar_artists"}
SECTION_BREAK = re.compile(r"\n\s*\n")
SECTION_LABEL = re.compile(r"^\s*\[[^\]]*\]\s*$")


def _tag_list(value) -> List[str]:
    if isinstance(value, str):
        value = value.split(",")
    return list(dict.fromkeys(tag.strip() for tag in value or [] if isinstance(tag, str) and tag.strip()))


def _lyric_sections(lyrics: str) -> List[str]:
    sections = []
    for block in SECTION_BREAK.split(lyrics or ""):
        lines = [line.strip() for line in block.splitlines() if line.strip() and not SECTION_LABEL.match(line)]
        if lines:
            sections.append("\n".join(lines))
    return list(dict.fromkeys(sections))


def _unit_mean(vectors: List[np.ndarray]) -> np.ndarray:
    mean = np.mean(vectors, axis=0)
    norm = np.linalg.norm(mean)
    return mean / norm if norm else mean


@dataclass
class CatalogSlot:
    """One built catalog index: the tracks, each track's per-aspect vectors, the tag matrices the stations rank
    with and the spelling vectors for names. Built in the background into the spare slot, then swapped in."""
    rowids: Dict[int, str]
    by_rowid: Dict[int, Dict[str, Any]]
    metas: List[Dict[str, Any]]
    ids: set
    vectors: Dict[str, Dict[str, np.ndarray]]
    tags: Dict[str, Any]
    names: Any


class CatalogVectorDatabaseService(BaseVectorDatabaseService):
    categories = (
        "song_title", "primary_genre", "secondary_genres", "mood", "primary_artist",
        "similar_artists", "style", "theme", "vocal", "lyrics"
    )
    default_weights = {
        "song_title": 0.0,
        "primary_genre": 0.20,
        "secondary_genres": 0.10,
        "mood": 0.20,
        "primary_artist": 0.15,
        "similar_artists": 0.10,
        "style": 0.12,
        "vocal": 0.07,
        "theme": 0.04,
        "lyrics": 0.02
    }
    log_channel = "vector_music"
    service_label = "Music"
    display_name = "Catalog"
    index_dir_setting_name = "CATALOG_EMBEDDINGS_DIR"
    index_file_prefix = "catalog"
    source_table = "tracks"
    source_id_column = "track_id"
    source_label = "catalog"
    source_db_label = "catalog"
    item_noun = "tracks"
    single_item_noun = "track"

    def __init__(self, catalog_service=None):
        super().__init__(catalog_service)
        self.slots: Dict[int, Optional[CatalogSlot]] = {1: None, 2: None}

    @property
    def catalog_service(self):
        return self.source_service

    @catalog_service.setter
    def catalog_service(self, value):
        self.source_service = value

    @classmethod
    def weights_for(cls, category: str) -> Dict[str, float]:
        return {category: 1.0} if category in cls.categories else dict(cls.default_weights)

    def category_tags(self, track: Dict[str, Any]) -> Dict[str, List[str]]:
        params = track.get("generation_params", {}) or {}
        derived = track.get("derived_tags", {}) or {}
        title = params.get("title") or (track.get("track_info", {}) or {}).get("title") or ""
        who = VOCALS_TEXT.get(vocals_of(track))
        tags = {
            "song_title": _tag_list([title]),
            "primary_genre": _tag_list([derived.get("primary_genre") or ""]),
            "primary_artist": credited_artists(track),
            "style": _tag_list([params.get("style_canonical") or params.get("style") or ""]),
            "theme": _tag_list([derived.get("lyrical_interpretation") or ""]),
            "lyrics": _lyric_sections(params.get("prompt") or ""),
            "vocal": list(dict.fromkeys(([who] if who else []) + _tag_list(derived.get("vocal_style_keywords")))),
        }
        for category, field in TAG_LISTS.items():
            tags[category] = _tag_list(derived.get(field))
        return tags

    def _extract_category_texts(self, track: Dict[str, Any]) -> Dict[str, str]:
        return {category: ", ".join(tags) for category, tags in self.category_tags(track).items()}

    def _track_vectors(self, track: Dict[str, Any]) -> Dict[str, np.ndarray]:
        return {category: _unit_mean(self.ensure_embeddings(category, tags))
                for category, tags in self.category_tags(track).items() if tags}

    def current(self) -> Optional[CatalogSlot]:
        return self.slots[self.current_index]

    def category_vectors(self, track: Dict[str, Any]) -> Dict[str, np.ndarray]:
        live = self.current()
        vectors = live.vectors.get(track.get("id")) if live is not None else None
        return vectors if vectors is not None else self._track_vectors(track)

    def weighted(self, item: Dict[str, Any], weights: Optional[Dict[str, float]] = None) -> np.ndarray:
        return self._create_weighted_embedding(self.category_vectors(item), weights)

    def _load_indexes(self):
        self._log("\nBuilding the catalog index...")
        self.rebuild_indexes()

    def _trigger_rebuild(self):
        self.rebuild_indexes()

    def add_single_track(self, track_data: dict) -> bool:
        for category, tags in self.category_tags(track_data).items():
            if tags:
                self.ensure_embeddings(category, tags)
        self.dirty = True
        return True

    def rebuild_indexes(self, catalog_service=None):
        from services.catalog_aspects import build_tag_indexes
        from services.catalog_names import build_name_space

        if catalog_service is not None:
            self.catalog_service = catalog_service
        if not self.catalog_service:
            log_service.warning("catalog_service is None - cannot rebuild indexes")
            return
        self._log_rebuild_header()
        started = time.perf_counter()
        self.dirty = False
        rowids, by_rowid = self.read_rows()
        metas = list(by_rowid.values())
        slot = CatalogSlot(
            rowids=rowids, by_rowid=by_rowid, metas=metas, ids={meta.get("id") for meta in metas},
            vectors={meta.get("id"): self._track_vectors(meta) for meta in metas if meta.get("id")},
            tags=build_tag_indexes(self, metas), names=build_name_space(metas),
        )
        self._swap_in_slot(slot)
        self._log(f"✓ Indexed {len(metas)} {self.item_noun} in {time.perf_counter() - started:.1f}s")

    def _swap_in_slot(self, slot: CatalogSlot):
        with self.index_lock:
            target = 1 if self.slots[self.current_index] is None else 3 - self.current_index
            self.slots[target] = slot
            self.current_index = target
            self._rowid_cache, self._metadata_cache = slot.rowids, slot.by_rowid
        self._log(f"  ✓ Switched to index {target}")
