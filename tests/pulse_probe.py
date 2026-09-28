import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from database.connection import AsyncSessionLocal  # noqa: E402
from service_registry import services  # noqa: E402
from services_radio import area_signals, listener_location  # noqa: E402
from services_radio import pulse as pulse_kb  # noqa: E402
from services_radio import regional_knowledge as regional_kb  # noqa: E402
from services_radio.area_geocode import ReverseGeocodeSignal  # noqa: E402
from services_radio.external_events_service import EventsService  # noqa: E402
from services_radio.external_location_service import LocationService  # noqa: E402
from services_radio.external_news_service import NewsService  # noqa: E402
from services_radio.external_web_service import WebService  # noqa: E402
from services_radio.news_store import NewsStore  # noqa: E402

PROBES = [
    ("", ["community"], None),
    ("family", ["community"], None),
    ("", None, None),
    ("jazz", None, "weekend"),
    ("jazz soul gigs", ["event"], "week"),
    ("late night food", ["place"], None),
    ("coffee", None, None),
    ("weather tonight", None, "tonight"),
    ("All Blacks", ["news"], None),
    ("", ["chart", "trend"], None),
    ("Radiohead", ["artist"], None),
]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Query City Pulse directly for a located guest (no LLM)")
    parser.add_argument("--lat", type=float, default=-36.8570)
    parser.add_argument("--lon", type=float, default=174.7600)
    parser.add_argument("--tz", default="Pacific/Auckland")
    parser.add_argument("--fetch", action="store_true", help="allow live fetches")
    parser.add_argument("--embed", action="store_true", help="load the T5 embedder (GPU)")
    parser.add_argument("--refresh", action="store_true", help="run the region's collectors first (live APIs)")
    parser.add_argument("--links", action="store_true", help="print cross-links for each result")
    parser.add_argument("queries", nargs="*")
    args = parser.parse_args()

    embedder = None
    if args.embed:
        from models_global import run_on_gpu_executor
        from services.catalog_vector_database_service import CatalogVectorDatabaseService
        vector_db = CatalogVectorDatabaseService()

        def embedder(text):
            return run_on_gpu_executor(vector_db._generate_embedding, text)

    area_store = area_signals.AreaCacheStore(AsyncSessionLocal)
    services.web_service = WebService(area_store=area_store, session_maker=AsyncSessionLocal)
    services.news_service = NewsService(None, store=NewsStore(AsyncSessionLocal), embedder=embedder)
    services.location_service = LocationService()
    services.events_service = EventsService()
    from services.user_content_database_service import UserContentDatabaseService
    services.user_content_service = UserContentDatabaseService()
    await services.user_content_service.initialize()
    regional_kb.set_regional_knowledge(regional_kb.RegionalKnowledgeService(
        regional_kb.RegionalKnowledgeStore(AsyncSessionLocal),
        [regional_kb.TicketmasterEventsCollector(services.events_service),
         regional_kb.CommunityCollector(lambda: services.user_content_service)], embedder=embedder))
    area_signals.install([ReverseGeocodeSignal(area_store)])
    pulse = pulse_kb.Pulse(pulse_kb.default_nodes())
    pulse_kb.install(pulse)

    session_id = f"guest_{uuid.uuid4()}"
    listener_location.guest_locations.update(session_id, args.lat, args.lon, 30, args.tz)
    listener = await pulse.listener(None, session_id)
    print(f"region={listener.region} tz={listener.tz_name} place={listener.location.description!r}")
    if args.refresh and listener.region:
        print("refresh:", await regional_kb.get_regional_knowledge().refresh(listener.region, force=True))

    probes = [(q, None, None) for q in args.queries] if args.queries else PROBES
    for text, kinds, when in probes:
        items = await pulse.query(pulse_kb.PulseQuery(listener=listener, text=text, kinds=set(kinds) if kinds else None,
                                                      when=when, allow_fetch=args.fetch, record_demand=False))
        print(f"\n== '{text}' kinds={kinds} when={when}: {len(items)}")
        for item in items:
            print(f"  {item.score:.2f} {json.dumps(item.brief(listener.tz_name), ensure_ascii=False)[:260]}")
            if args.links:
                for link in await pulse.related(listener, item.id):
                    print(f"       -> {link['reason']}: {link['title'][:80]}")


if __name__ == "__main__":
    asyncio.run(main())
