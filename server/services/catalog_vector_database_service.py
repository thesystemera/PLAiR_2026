import re
from functools import lru_cache
from typing import Any, Dict, List, Optional

import numpy as np
from services import log_service
from services.base_vector_database_service import BaseVectorDatabaseService
from services.catalog_credit import search_artist_text
from services.catalog_vocals import VOCALS_TEXT, vocals_of


VOCAL_WORD = re.compile(r"\b(vocals?|vocalists?|singers?|singing|sung|sings|voices?|duets?|rapp\w*|MCs?)\b", re.I)
SENTENCE_BREAK = re.compile(r"[.\n]+")
VOCAL_TEXT_MAX_CHARS = 400

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


@lru_cache(maxsize=8192)
def _vocal_text(vocals: str, style_text: str, keywords: str) -> str:
    if vocals == "instrumental":
        return ", ".join(part for part in (VOCALS_TEXT["instrumental"], keywords) if part)
    parts = [VOCALS_TEXT.get(vocals, "")]
    parts += [sentence.strip(" ,;:") for sentence in SENTENCE_BREAK.split(style_text) if VOCAL_WORD.search(sentence)]
    parts.append(keywords)
    return ". ".join(dict.fromkeys(part for part in parts if part))[:VOCAL_TEXT_MAX_CHARS]


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
    @classmethod
    def weights_for(cls, category: str) -> Dict[str, float]:
        return {category: 1.0} if category in cls.categories else dict(cls.default_weights)

    def category_tags(self, track: Dict[str, Any]) -> Dict[str, List[str]]:
        texts = self._extract_category_texts(track)
        derived = track.get("derived_tags", {}) or {}
        tags = {category: [text.strip()] if text and text.strip() else [] for category, text in texts.items()}
        for category, field in TAG_LISTS.items():
            tags[category] = _tag_list(derived.get(field))
        theme = (derived.get("lyrical_interpretation") or "").strip()
        tags["theme"] = [theme] if theme else []
        tags["lyrics"] = _lyric_sections((track.get("generation_params") or {}).get("prompt") or "")
        tags["primary_artist"] = _tag_list(log_service.track_artists(track))
        who = VOCALS_TEXT.get(vocals_of(track))
        tags["vocal"] = list(dict.fromkeys(([who] if who else []) + _tag_list(derived.get("vocal_style_keywords"))))
        return tags

    def category_vectors(self, track: Dict[str, Any]) -> Dict[str, np.ndarray]:
        if self._vector_source is not self._metadata_cache:
            self._vector_source, self._vectors = self._metadata_cache, {}
        key = track.get("id")
        vectors = self._vectors.get(key) if key else None
        if vectors is None:
            vectors = {category: _unit_mean(self.ensure_embeddings(category, tags))
                       for category, tags in self.category_tags(track).items() if tags}
            if key:
                self._vectors[key] = vectors
        return vectors

    def weighted(self, item: Dict[str, Any], weights: Optional[Dict[str, float]] = None) -> np.ndarray:
        return self._create_weighted_embedding(self.category_vectors(item), weights)

    log_channel = "vector_music"
    builds_index = False
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
        self._vector_source = None
        self._vectors: Dict[str, Dict[str, np.ndarray]] = {}

    @property
    def catalog_service(self):
        return self.source_service

    @catalog_service.setter
    def catalog_service(self, value):
        self.source_service = value

    def _trigger_rebuild(self):
        self.refresh()

    def add_single_track(self, track_data: dict) -> bool:
        for category, tags in self.category_tags(track_data).items():
            if tags:
                self.ensure_embeddings(category, tags)
        self.refresh()
        return True

    def refresh(self, catalog_service=None):
        if catalog_service is not None:
            self.catalog_service = catalog_service
        if not self.catalog_service:
            log_service.warning("catalog_service is None - cannot refresh the catalog vectors")
            return
        self.load_metadata()
        self._log(f"✓ Catalog vectors refreshed: {len(self._metadata_cache)} tracks")

    def _extract_category_texts(self, track: Dict[str, Any]) -> Dict[str, str]:
        params = track.get("generation_params", {}) or {}
        derived_tags = track.get("derived_tags", {}) or {}
        track_info = track.get("track_info", {}) or {}

        song_title = params.get("title") or track_info.get("title") or ""
        primary_genre_text = derived_tags.get("primary_genre") or ""
        secondary_genres = derived_tags.get("secondary_genres") or []
        secondary_genres_text = ', '.join(secondary_genres) if isinstance(secondary_genres, list) else ""
        mood_keywords = derived_tags.get("mood_keywords") or []
        mood_text = ', '.join(mood_keywords) if isinstance(mood_keywords, list) else ""
        primary_artist_text = search_artist_text(track)
        similar_artists = derived_tags.get("similar_artists") or []
        similar_artists_text = ', '.join(similar_artists) if isinstance(similar_artists, list) else ""
        style_text = params.get("style_canonical") or params.get("style") or ""
        theme_text = (derived_tags.get("lyrical_interpretation") or "")[:300]
        lyrics_text = (params.get("prompt") or "")[:200]
        vocal_keywords = derived_tags.get("vocal_style_keywords") or []
        vocal_text = self._vocal_text(track, style_text,
                                      ', '.join(vocal_keywords) if isinstance(vocal_keywords, list) else "")

        return {
            "song_title": song_title,
            "primary_genre": primary_genre_text,
            "secondary_genres": secondary_genres_text,
            "mood": mood_text,
            "primary_artist": primary_artist_text,
            "similar_artists": similar_artists_text,
            "style": style_text,
            "theme": theme_text,
            "lyrics": lyrics_text,
            "vocal": vocal_text
        }

    @staticmethod
    def _vocal_text(track: Dict[str, Any], style_text: str, keywords: str) -> str:
        return _vocal_text(vocals_of(track), style_text, keywords)
