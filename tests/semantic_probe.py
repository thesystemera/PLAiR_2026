import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from database.connection import AsyncSessionLocal  # noqa: E402
from service_registry import services  # noqa: E402
from services.ai_service import AIService  # noqa: E402
from services.listener_request_service import ListenerRequestPromptCache, ListenerRequestStore, \
    ListenerRequestVectorDatabaseService  # noqa: E402
from services.semantic_source import SemanticSearch  # noqa: E402
from services_radio import area_signals, listener_location, local_knowledge  # noqa: E402
from services_radio import pulse as pulse_kb  # noqa: E402
from services_radio import regional_knowledge as regional_kb  # noqa: E402
from services_radio.area_geocode import ReverseGeocodeSignal  # noqa: E402
from services_radio.external_events_service import EventsService  # noqa: E402
from services_radio.external_location_service import LocationService  # noqa: E402
from services_radio.external_news_service import NewsService  # noqa: E402
from services_radio.external_web_service import WebService  # noqa: E402
from services_radio.news_store import NewsStore  # noqa: E402

PROBES = [
    ("Radiohead", None, None),
    ("Radiohead near me", None, None),
    ("any jazz or soul on this weekend", None, "weekend"),
    ("what's on at the Tuning Fork", None, None),
    ("comedy", None, None),
    ("Radiohead", None, None),
    ("good cafes near me", None, None),
    ("is it going to rain tonight", None, None),
    ("is the air okay, I've got hay fever", None, None),
    ("what's Auckland listening to", None, None),
    ("what have people been shouting out about", None, None),
    ("hey how's it going", None, None),
    ("All Blacks", None, None),
]


async def setup(ai: bool):
    import models_global
    await models_global.initialize_semantic_encoder()
    services.ai_service = AIService()
    await services.ai_service.initialize()
    from services.catalog_database_service import CatalogDatabaseService
    services.catalog_service = CatalogDatabaseService()
    await services.catalog_service.initialize()
    from services.user_content_database_service import UserContentDatabaseService
    from services.user_content_vector_database_service import UserContentVectorDatabaseService
    from services.user_content_vector_search_service import UserContentVectorSearchService
    from services.user_content_vector_search_prompt_cache_service import UserContentVectorSearchPromptCacheService
    services.user_content_service = UserContentDatabaseService()
    await services.user_content_service.initialize()
    ucv = UserContentVectorDatabaseService(services.user_content_service)
    await asyncio.to_thread(ucv.load)
    cache = UserContentVectorSearchPromptCacheService()
    await cache.initialize(services.ai_service, ucv)
    services.user_content_vector_search_service = UserContentVectorSearchService(ucv, services.user_content_service,
                                                                                 cache)
    services.request_store = ListenerRequestStore()
    await asyncio.to_thread(services.request_store.initialize)
    services.request_vector_db_service = ListenerRequestVectorDatabaseService(services.request_store)
    await asyncio.to_thread(services.request_vector_db_service.load)
    services.request_search = SemanticSearch(services.request_vector_db_service, ListenerRequestPromptCache())
    await services.request_search.prompt_cache.initialize(services.ai_service, services.request_vector_db_service)
    source = local_knowledge.LocalNuggetSource()
    await asyncio.to_thread(source.initialize)
    vdb = local_knowledge.LocalKnowledgeVectorDatabaseService(source)
    await asyncio.to_thread(vdb.load)
    search = SemanticSearch(vdb, local_knowledge.LocalKnowledgePromptCache())
    await search.prompt_cache.initialize(services.ai_service, vdb)
    news_db = local_knowledge.NewsVectorDatabaseService(source)
    await asyncio.to_thread(news_db.load)
    news_search = SemanticSearch(news_db, local_knowledge.NewsPromptCache())
    await news_search.prompt_cache.initialize(services.ai_service, news_db)
    local_knowledge.install(vdb, search, news_db, news_search)
    from services.catalog_vector_database_service import CatalogVectorDatabaseService
    from services.catalog_vector_search_service import CatalogVectorSearchService
    from services.catalog_vector_search_prompt_cache_service import CatalogVectorSearchPromptCacheService
    catalog_db = CatalogVectorDatabaseService(services.catalog_service)
    await asyncio.to_thread(catalog_db.load)
    catalog_cache = CatalogVectorSearchPromptCacheService()
    await catalog_cache.initialize(services.ai_service, catalog_db)
    services.vector_search_service = CatalogVectorSearchService(catalog_db, services.catalog_service, catalog_cache)
    area_store = area_signals.AreaCacheStore(AsyncSessionLocal)
    services.web_service = WebService(area_store=area_store, session_maker=AsyncSessionLocal)
    services.news_service = NewsService(services.ai_service, store=NewsStore(AsyncSessionLocal))
    services.location_service = LocationService()
    services.events_service = EventsService()
    regional_kb.set_regional_knowledge(regional_kb.RegionalKnowledgeService(
        regional_kb.RegionalKnowledgeStore(AsyncSessionLocal), []))
    area_signals.install([ReverseGeocodeSignal(area_store)])
    pulse_kb.install(pulse_kb.Pulse(pulse_kb.default_nodes()))


async def main() -> None:
    parser = argparse.ArgumentParser(description="Semantic City Pulse probe (real T5, no DJ LLM)")
    parser.add_argument("--ai", action="store_true", help="use the query-intent prompt caches (Gemini on miss)")
    parser.add_argument("--links", action="store_true")
    parser.add_argument("queries", nargs="*")
    args = parser.parse_args()
    await setup(args.ai)
    pulse = pulse_kb.get_pulse()
    session_id = f"guest_{uuid.uuid4()}"
    listener_location.guest_locations.update(session_id, -36.8570, 174.7600, 30, "Pacific/Auckland")
    listener = await pulse.listener(None, session_id)
    print(f"region={listener.region.key} nuggets={len(local_knowledge.local_vector_db.metas())}")
    probes = [(q, None, None) for q in args.queries] if args.queries else PROBES
    for text, kinds, when in probes:
        items = await pulse.query(pulse_kb.PulseQuery(listener=listener, text=text, kinds=set(kinds) if kinds else None,
                                                      when=when, record_demand=False, use_ai=args.ai))
        print(f"== '{text}': {len(items)}")
        for item in items:
            print(f"  {item.score:.3f} {json.dumps(item.brief(listener.tz_name), ensure_ascii=False)[:220]}")
            if args.links:
                for link in await pulse.related(listener, item.id):
                    print(f"       -> {link['reason']}: {link['title'][:70]}")


if __name__ == "__main__":
    asyncio.run(main())
