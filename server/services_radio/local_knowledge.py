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
           'expires_at', expires_at, 'url', url, 'attribution', attribution,
           'where', CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL THEN json_build_object(
               'label', area, 'lat', latitude, 'lon', longitude, 'radius_m', geo_radius_m, 'scope', geo_scope) END
       )::text AS metadata_json
FROM regional_items
WHERE title <> '' AND expires_at > now() AND kind <> 'news'
"""

NEWS_VIEW_SQL = """
CREATE OR REPLACE VIEW news_nuggets AS
SELECT id AS rowid,
       'news:' || id AS nugget_id,
       json_build_object(
           'id', 'news:' || id, 'kind', 'news', 'article_id', id, 'title', title,
           'text', COALESCE(NULLIF(summary, ''), description),
           'source', source, 'url', url, 'published_at', published_at, 'country', country,
           'region_key', region_key, 'tags', tags::json, 'entities', entities::json, 'category', category,
           'tone', tone, 'worth', worth,
           'where', CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL THEN json_build_object(
               'label', geo_label, 'lat', latitude, 'lon', longitude, 'radius_m', geo_radius_m, 'scope', geo_scope) END
       )::text AS metadata_json
FROM news_items
WHERE expires_at > now()
"""

PLACE_VIEW_SQL = """
CREATE OR REPLACE VIEW place_nuggets AS
SELECT row_id AS rowid,
       'place:' || place_id AS nugget_id,
       json_build_object(
           'id', 'place:' || place_id, 'kind', 'place', 'place_id', place_id, 'title', name, 'type', type,
           'address', address, 'tags', tags::json, 'rating', rating, 'rating_count', rating_count,
           'price_level', price_level, 'hours', opening_hours::json, 'website', website, 'phone', phone,
           'details', details::json,
           'fetched_at', fetched_at,
           'where', json_build_object('label', coalesce(address, name), 'lat', latitude, 'lon', longitude,
                                      'radius_m', 0, 'scope', 'spot')
       )::text AS metadata_json
FROM place_cache
"""

PRICE_WORDS = {"$": "cheap, inexpensive", "$$": "moderately priced", "$$$": "pricey, upmarket",
               "$$$$": "very expensive, fine dining"}

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


def where_label(item: Dict[str, Any]) -> str:
    return ((item.get("where") or {}).get("label") or "") if isinstance(item.get("where"), dict) else ""


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
    display_name = "Local Knowledge"
    source_table = "local_nuggets"
    source_id_column = "nugget_id"
    item_noun = "nuggets"


class NewsVectorDatabaseService(SemanticVectorDatabaseService):
    category_specs = (
        Category("news_title", 0.28, field_text("title"), "The headline"),
        Category("news_tags", 0.20, field_text("tags"), "Topics of the story (rugby, election, music, weather)"),
        Category("news_people", 0.14, field_text("entities"), "People, bands, teams and organisations in the story"),
        Category("news_details", 0.14, field_text("text", 900), "The story's summary"),
        Category("news_place", 0.10, where_label, "Where the story happens: street, suburb, city or country"),
        Category("news_kind", 0.08, lambda item: ", ".join(p for p in (item.get("category"), item.get("tone")) if p),
                 "What sort of story it is and its tone (sport, crime; good news, funny, sad)"),
        Category("news_outlet", 0.06, field_text("source"), "The publisher"),
    )
    display_name = "News"
    source_table = "news_nuggets"
    source_id_column = "nugget_id"
    item_noun = "stories"


def place_type_text(item: Dict[str, Any]) -> str:
    return ", ".join(dict.fromkeys(t for t in [item.get("type"), *(item.get("tags") or [])] if t))


def place_hours_text(item: Dict[str, Any]) -> str:
    hours = item.get("hours")
    return "; ".join(str(h) for h in hours)[:400] if isinstance(hours, list) else ""


def place_quality_text(item: Dict[str, Any]) -> str:
    parts = []
    if item.get("rating"):
        parts.append(f"rated {item['rating']} from {item.get('rating_count') or 'a few'} reviews")
    if item.get("price_level"):
        parts.append(PRICE_WORDS.get(item["price_level"], item["price_level"]))
    return ", ".join(parts)


def place_about_text(item: Dict[str, Any]) -> str:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    return ". ".join(part for part in (details.get("summary"), details.get("review_summary"),
                                       ", ".join(details.get("features") or [])) if part)[:600]


def place_area_text(item: Dict[str, Any]) -> str:
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    return ", ".join(part for part in (details.get("suburb"), item.get("address")) if part)


class PlaceVectorDatabaseService(SemanticVectorDatabaseService):
    category_specs = (
        Category("place_name", 0.25, field_text("title"), "The place's name"),
        Category("place_type", 0.25, place_type_text, "What sort of place it is (cafe, cocktail bar, record store)"),
        Category("place_about", 0.20, place_about_text,
                 "What it's like and what it offers: live music, good for kids, dog friendly, outdoor seating, "
                 "vegetarian, cocktails, wheelchair access"),
        Category("place_area", 0.15, place_area_text, "Where it is: street, suburb, city"),
        Category("place_hours", 0.075, place_hours_text, "Opening hours: early, late night, weekends"),
        Category("place_quality", 0.075, place_quality_text, "Rating and price: cheap, upmarket, well reviewed"),
    )
    display_name = "Places"
    source_table = "place_nuggets"
    source_id_column = "nugget_id"
    item_noun = "places"


class LocalNuggetSource:
    def _get_connection(self):
        return psycopg2.connect(settings.DATABASE_URL)

    def initialize(self) -> None:
        conn = self._get_connection()
        try:
            c = conn.cursor()
            c.execute(VIEW_SQL)
            c.execute(NEWS_VIEW_SQL)
            c.execute(PLACE_VIEW_SQL)
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

NewsPromptCache = make_prompt_cache(
    NewsVectorDatabaseService, "news_query_intent_cache", "news stories",
    "- 'Radiohead': news_people and news_title high.\n"
    "- 'rugby': news_tags high, news_title some.\n"
    "- 'some good news': news_kind high.\n"
    "- 'what's Luxon been up to': news_people high, news_title some.\n"
    "- 'what's RNZ saying about the election': news_outlet and news_tags high.\n"
    "- 'what happened on K Road': news_place high, news_title some.")

PlacePromptCache = make_prompt_cache(
    PlaceVectorDatabaseService, "place_query_intent_cache", "places in a listener's city (cafes, bars, shops, venues)",
    "- 'late night pizza': place_type and place_hours high.\n"
    "- 'cheap eats on K Road': place_type, place_area and place_quality high.\n"
    "- 'Brothers Beer': place_name high.\n"
    "- 'best rated coffee': place_type and place_quality high.")

local_vector_db: Optional[LocalKnowledgeVectorDatabaseService] = None
local_search: Optional[SemanticSearch] = None
news_vector_db: Optional[NewsVectorDatabaseService] = None
news_search: Optional[SemanticSearch] = None
place_vector_db: Optional[PlaceVectorDatabaseService] = None
place_search: Optional[SemanticSearch] = None


def install(vector_db: LocalKnowledgeVectorDatabaseService, search: SemanticSearch,
            news_db: Optional[NewsVectorDatabaseService] = None, news: Optional[SemanticSearch] = None,
            place_db: Optional[PlaceVectorDatabaseService] = None, places: Optional[SemanticSearch] = None) -> None:
    global local_vector_db, local_search, news_vector_db, news_search, place_vector_db, place_search
    local_vector_db, local_search = vector_db, search
    news_vector_db, news_search = news_db, news
    place_vector_db, place_search = place_db, places


def mark_dirty() -> None:
    if local_vector_db is not None:
        local_vector_db.dirty = True


def mark_news_dirty() -> None:
    if news_vector_db is not None:
        news_vector_db.dirty = True


def mark_places_dirty() -> None:
    if place_vector_db is not None:
        place_vector_db.dirty = True
