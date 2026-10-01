import re
from functools import lru_cache
from typing import Dict, Any
from services import log_service
from services.base_vector_database_service import BaseVectorDatabaseService


VOCAL_WORD = re.compile(r"\b(vocals?|vocalists?|singers?|singing|sung|sings|voices?|duets?|rapp\w*|MCs?)\b", re.I)
SENTENCE_BREAK = re.compile(r"[.\n]+")
WHO_SINGS = {"m": "male vocals", "f": "female vocals"}
VOCAL_TEXT_MAX_CHARS = 400


@lru_cache(maxsize=8192)
def _vocal_text(instrumental: bool, vocal_gender: str, style_text: str, keywords: str) -> str:
    if instrumental:
        return ", ".join(part for part in ("instrumental, no vocals", keywords) if part)
    parts = [WHO_SINGS.get(vocal_gender, "")]
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

    @property
    def catalog_service(self):
        return self.source_service

    @catalog_service.setter
    def catalog_service(self, value):
        self.source_service = value

    @property
    def annoy_index_tracks_1(self):
        return self.annoy_index_1

    @property
    def annoy_index_tracks_2(self):
        return self.annoy_index_2

    @property
    def _track_rowid_cache(self) -> Dict[int, str]:
        return self._rowid_cache

    @property
    def _track_metadata_cache(self) -> Dict[int, Dict]:
        return self._metadata_cache

    def _trigger_rebuild(self):
        self.rebuild_indexes()

    def add_single_track(self, track_data: dict) -> bool:
        return self._add_single_item(track_data)

    def rebuild_indexes(self, catalog_service=None):
        self._log_rebuild_header()

        catalog_svc = catalog_service or self.catalog_service
        if not catalog_svc:
            log_service.warning("catalog_service is None - cannot rebuild indexes")
            return
        if not catalog_svc.tracks:
            log_service.warning("catalog_service.tracks is empty - cannot rebuild indexes. Will retry when catalog is loaded.")
            return

        total_tracks = len(catalog_svc.tracks)
        self._log(f"📊 Total tracks to index: {total_tracks}")

        self._rebuild_from_source(catalog_svc, total_tracks)

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
        human_artist = params.get("artist_name") if track.get("is_ai_generated") is False else None
        primary_artist_text = human_artist or derived_tags.get("inspired_artist") or ""
        similar_artists = derived_tags.get("similar_artists") or []
        similar_artists_text = ', '.join(similar_artists) if isinstance(similar_artists, list) else ""
        style_text = params.get("style_canonical") or params.get("style") or ""
        theme_text = (derived_tags.get("lyrical_interpretation") or "")[:300]
        lyrics_text = (params.get("prompt") or "")[:200]
        vocal_keywords = derived_tags.get("vocal_style_keywords") or []
        vocal_text = self._vocal_text(params, style_text,
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
    def _vocal_text(params: Dict[str, Any], style_text: str, keywords: str) -> str:
        return _vocal_text(bool(params.get("instrumental")), params.get("vocal_gender") or "", style_text, keywords)
