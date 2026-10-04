import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from services import log_service
from services.catalog_credit import credited_artists
from services.catalog_vocals import VOCALS_TEXT, vocals_of
from services.category_store import CategorySlot, CategoryStore
from services.vector_store import Entry, Slot

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


@dataclass
class CatalogSlot(CategorySlot):
    """A built catalog slot: the per-aspect matrices (free-text search), the tag matrices the stations rank with
    and the spelling vectors for names."""
    metas: List[Dict[str, Any]] = field(default_factory=list)
    tags: Dict[str, Any] = field(default_factory=dict)
    names: Any = None


class CatalogVectorDatabaseService(CategoryStore):
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
    display_name = "Catalog"
    source_table = "tracks"
    source_id_column = "track_id"
    item_noun = "tracks"

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
        for category, field_name in TAG_LISTS.items():
            tags[category] = _tag_list(derived.get(field_name))
        return tags

    def _extract_category_texts(self, track: Dict[str, Any]) -> Dict[str, str]:
        return {category: ", ".join(tags) for category, tags in self.category_tags(track).items()}

    def build_slot(self, entries: List[Entry], target: Optional[int], basis: Optional[Slot]) -> CatalogSlot:
        from services.catalog_aspects import build_tag_indexes
        from services.catalog_names import build_name_space

        base = super().build_slot(entries, target, basis)
        metas = [entry.meta for entry in entries]
        spelling = basis.names.spelling if isinstance(basis, CatalogSlot) and basis.names is not None else None
        return CatalogSlot(entries, base.matrices, metas, build_tag_indexes(self, metas),
                           build_name_space(metas, spelling))

    def add_single_track(self, track_data: dict) -> bool:
        track_id = track_data.get("id")
        if not track_id:
            log_service.warning("add_single_track: track has no id - left for the background rebuild")
            self.dirty = True
            return False
        self.add_meta(str(track_id), track_data)
        return True
