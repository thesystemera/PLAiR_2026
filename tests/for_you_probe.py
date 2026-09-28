import argparse
import asyncio
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

import pytz  # noqa: E402

from database.connection import AsyncSessionLocal  # noqa: E402
from service_registry import services  # noqa: E402
from services.ai_service import AIService  # noqa: E402
from services_radio import area_signals, listener_location, radio_segments  # noqa: E402
from services_radio import pulse as pulse_kb  # noqa: E402
from services_radio import regional_knowledge as regional_kb  # noqa: E402
from services_radio.area_geocode import ReverseGeocodeSignal  # noqa: E402
from services_radio.external_events_service import EventsService  # noqa: E402
from services_radio.external_location_service import LocationService  # noqa: E402
from services_radio.external_news_service import NewsService  # noqa: E402
from services_radio.external_web_service import WebService  # noqa: E402
from services_radio.news_store import NewsStore  # noqa: E402


async def main() -> None:
    from services import log_service
    log_service.warning = lambda msg, *a, **k: print("WARN", msg)
    log_service.detail = lambda msg, *a, **k: print("DETAIL", msg)
    log_service.error = lambda msg, *a, **k: print("ERROR", msg)
    parser = argparse.ArgumentParser(description="Build one Radio Mode 'For You' feature for a located guest")
    parser.add_argument("--lat", type=float, default=-36.8570)
    parser.add_argument("--lon", type=float, default=174.7600)
    parser.add_argument("--tz", default="Pacific/Auckland")
    parser.add_argument("--kind", default="for_you")
    parser.add_argument("--user", type=int, default=None, help="signed-in user id (uses their profile location)")
    args = parser.parse_args()

    from services.catalog_database_service import CatalogDatabaseService
    services.catalog_service = CatalogDatabaseService()
    await services.catalog_service.initialize()
    services.ai_service = AIService()
    await services.ai_service.initialize()
    area_store = area_signals.AreaCacheStore(AsyncSessionLocal)
    services.web_service = WebService(area_store=area_store, session_maker=AsyncSessionLocal)
    services.news_service = NewsService(services.ai_service, store=NewsStore(AsyncSessionLocal))
    services.location_service = LocationService()
    services.events_service = EventsService()
    from services.user_content_database_service import UserContentDatabaseService
    services.user_content_service = UserContentDatabaseService()
    await services.user_content_service.initialize()
    regional_kb.set_regional_knowledge(regional_kb.RegionalKnowledgeService(
        regional_kb.RegionalKnowledgeStore(AsyncSessionLocal), []))
    area_signals.install([ReverseGeocodeSignal(area_store)])
    pulse_kb.install(pulse_kb.Pulse(pulse_kb.default_nodes()))

    session_id = str(args.user) if args.user else f"guest_{uuid.uuid4()}"
    if not args.user:
        listener_location.guest_locations.update(session_id, args.lat, args.lon, 30, args.tz)
    listener = await pulse_kb.get_pulse().listener(args.user, session_id)
    ctx = radio_segments.SegmentContext(
        session_id=session_id, user_id=args.user, user=listener.user, tz_name=args.tz, region=listener.region,
        now_local=datetime.now(pytz.timezone(args.tz)).replace(tzinfo=None),
        next_track={"title": "Karma Police", "artist": "Radiohead", "genre": "Alternative Rock"},
        upcoming_track={}, current_track={}, dj_service=None, async_session_maker=AsyncSessionLocal,
        catalog_service=services.catalog_service, location=listener.location)
    segment = radio_segments.get_segment(args.kind)
    started = asyncio.get_running_loop().time()
    content = await segment.build(ctx)
    elapsed = asyncio.get_running_loop().time() - started
    if content is None:
        print(f"no content ({elapsed:.1f}s)")
        await asyncio.sleep(1.5)
        return
    print(f"TITLE: {content.title}  ({elapsed:.1f}s)")
    print(content.facts_text())
    print("keys:", content.keys)


if __name__ == "__main__":
    asyncio.run(main())
