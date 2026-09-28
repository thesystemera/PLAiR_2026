from datetime import datetime
from typing import Any, Dict, Optional

import psycopg2
import pytz

from config import settings
from services.semantic_source import Category, SemanticSearch, SemanticVectorDatabaseService, field_text, \
    make_prompt_cache

VIEW_SQL = """
CREATE OR REPLACE VIEW local_nuggets AS
SELECT id AS rowid,
       kind || ':' || source || ':' || external_id AS nugget_id,
       json_build_object(
           'id', kind || ':' || source || ':' || external_id, 'kind', kind, 'source', source,
           'external_id', external_id, 'region_key', region_key, 'title', title, 'text', text,
           'tags', tags::json, 'entities', entities::json, 'area', area, 'latitude', latitude,
           'longitude', longitude, 'starts_at', starts_at, 'published_at', published_at,
           'expires_at', expires_at, 'url', url, 'attribution', attribution
       )::text AS metadata_json
FROM regional_items
WHERE title <> '' AND expires_at > now()
"""

KIND_LABELS = {"event": "gig, concert, show or event", "place": "place, venue, shop, bar or cafe",
               "news": "local news story", "community": "listener shoutout"}


def region_zone(region_key: str):
    if region_key.startswith("tz:"):
        try:
            return pytz.timezone(region_key[3:])
        except pytz.UnknownTimeZoneError:
            return None
    return None


def when_phrase(item: Dict[str, Any]) -> str:
    raw = item.get("starts_at")
    if not raw:
        return "recent" if item.get("kind") == "news" else ""
    try:
        moment = datetime.fromisoformat(str(raw))
    except ValueError:
        return ""
    zone = region_zone(item.get("region_key") or "")
    local = moment.astimezone(zone) if zone else moment
    hour = local.hour
    part = "morning" if 5 <= hour < 12 else "afternoon" if hour < 17 else "evening" if hour < 22 else "late night"
    weekend = "weekend" if local.weekday() >= 4 else "weeknight" if part in ("evening", "late night") else "weekday"
    return f"{local.strftime('%A')} {part}, {weekend}"


def place_text(item: Dict[str, Any]) -> str:
    venue = (item.get("text") or "").split(",", 1)[0] if item.get("kind") == "event" else ""
    return ", ".join(part for part in (venue, item.get("area") or "") if part)


class LocalKnowledgeVectorDatabaseService(SemanticVectorDatabaseService):
    category_specs = (
        Category("nugget_title", 0.25, field_text("title"), "The item's name: event title, place name or headline"),
        Category("nugget_tags", 0.20, field_text("tags"), "Genre, category or topic tags (jazz, comedy, bar, rugby)"),
        Category("nugget_people", 0.15, field_text("entities"), "Performers, artists, teams and venue names involved"),
        Category("nugget_place", 0.12, place_text, "Where it is: venue, street, suburb"),
        Category("nugget_details", 0.10, field_text("text"), "Details: venue, date, type, source"),
        Category("nugget_kind", 0.08, lambda item: KIND_LABELS.get(item.get("kind"), item.get("kind") or ""),
                 "What sort of thing it is: event, place, news"),
        Category("nugget_when", 0.10, when_phrase, "When it happens: day, time of day, weekend or weeknight"),
    )
    encoder_name = settings.PULSE_ENCODER
    embedding_dim = settings.PULSE_ENCODER_DIM
    log_channel = "system"
    service_label = "Local knowledge"
    display_name = "Local Knowledge"
    tables_setting_name = ""
    index_dir_setting_name = "EMBEDDINGS_DIR"
    index_file_prefix = "local_knowledge"
    source_table = "local_nuggets"
    source_id_column = "nugget_id"
    source_label = "local nuggets"
    source_db_label = "ai_radio"
    item_noun = "nuggets"
    single_item_noun = "nugget"


class LocalNuggetSource:
    def _get_connection(self):
        return psycopg2.connect(settings.DATABASE_URL)

    def initialize(self) -> None:
        conn = self._get_connection()
        try:
            conn.cursor().execute(VIEW_SQL)
            conn.commit()
        finally:
            conn.close()


LocalKnowledgePromptCache = make_prompt_cache(
    LocalKnowledgeVectorDatabaseService, "local_knowledge_query_intent_cache",
    "local knowledge in a listener's city (gigs and events, places, local news)",
    "- 'jazz this weekend': nugget_tags and nugget_when high, nugget_title some.\n"
    "- 'anything on at the Tuning Fork': nugget_people and nugget_place high.\n"
    "- 'Radiohead': nugget_people and nugget_title high.\n"
    "- 'late night food near K Road': nugget_tags, nugget_place and nugget_kind high.\n"
    "- 'what's happening with the All Blacks': nugget_title and nugget_people high.")

local_vector_db: Optional[LocalKnowledgeVectorDatabaseService] = None
local_search: Optional[SemanticSearch] = None


def install(vector_db: LocalKnowledgeVectorDatabaseService, search: SemanticSearch) -> None:
    global local_vector_db, local_search
    local_vector_db, local_search = vector_db, search


def mark_dirty() -> None:
    if local_vector_db is not None:
        local_vector_db.dirty = True
