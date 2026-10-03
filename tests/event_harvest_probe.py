import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from database.connection import AsyncSessionLocal  # noqa: E402
from database.models import EventSource  # noqa: E402
from services.ai_service import AIService  # noqa: E402
from services_radio import event_harvest, news_links  # noqa: E402
from services_radio.external_news_service import NewsService  # noqa: E402
from services_radio.regional_knowledge import Region  # noqa: E402


async def discover(city: str, country: str, queries: int, per_query: int) -> list[tuple[str, str]]:
    news = NewsService(None)
    found = []
    for template in list(event_harvest.settings.EVENT_HARVEST_QUERIES)[:queries]:
        query = template.format(city=city)
        articles = await news.fetch_news(news.classify(query)[2], country=country, period="7d")
        for article in articles[:per_query]:
            link = news_links.google_id(article["url"])
            url = await news_links.decode(link) if link else article["url"]
            if url and not event_harvest.skipped_host(url):
                found.append((event_harvest.ARTICLE, url))
                print(f"  [{query}] {article['title'][:70]} -> {url[:90]}")
    return found


async def main() -> None:
    parser = argparse.ArgumentParser(description="Harvest events for one city without saving anything")
    parser.add_argument("--city", default="Auckland")
    parser.add_argument("--country", default="NZ")
    parser.add_argument("--tz", default="Pacific/Auckland")
    parser.add_argument("--lat", type=float, default=-36.8485)
    parser.add_argument("--lon", type=float, default=174.7633)
    parser.add_argument("--queries", type=int, default=2, help="news searches to run")
    parser.add_argument("--per-query", type=int, default=4, help="articles to read per search")
    parser.add_argument("--venues", type=int, default=4, help="venue websites from place memory to read")
    parser.add_argument("--llm", type=int, default=3, help="most pages the LLM may read")
    parser.add_argument("--geocode", type=int, default=5)
    parser.add_argument("--url", action="append", default=[], help="read this page instead of discovering")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    region = Region(key=f"tz:{args.tz}", name=args.city, country=args.country, center=(args.lat, args.lon))
    collector = event_harvest.WebEventsCollector(AsyncSessionLocal, None, AIService())
    zone = event_harvest.zone_for(region)
    if args.url:
        sources = [(event_harvest.LISTING, url) for url in args.url]
    else:
        print("Discovering through news search:")
        sources = await discover(args.city, args.country, args.queries, args.per_query)
        venues = (await collector._venues(region))[:args.venues]
        sources += [(event_harvest.VENUE, website) for _, website in venues]
    budget = {"llm": args.llm, "geocode": args.geocode}
    events, links = [], []
    for kind, url in sources:
        source = EventSource(url=url, kind=kind, url_key=event_harvest.url_key(url))
        result = await collector.read(source, region, zone, budget)
        for event in result.events:
            event.url = event.url or url
        print(f"{kind:8} {result.status:9} {result.method or '-':9} {len(result.events):3} events  {url[:90]}")
        for link_kind, link in result.links:
            print(f"         + {link_kind}: {link[:100]}")
        events += result.events
        links += result.links
    for kind, url in links:
        source = EventSource(url=url, kind=kind, url_key=event_harvest.url_key(url))
        result = await collector.read(source, region, zone, budget)
        for event in result.events:
            event.url = event.url or url
        print(f"{kind:8} {result.status:9} {result.method or '-':9} {len(result.events):3} events  {url[:90]}")
        events += result.events
    items = await collector.to_items(region, zone, events, budget)
    print(f"\n{len(events)} events read, {len(items)} kept:")
    for item in sorted(items, key=lambda i: i.starts_at):
        where = f"{item.latitude:.3f},{item.longitude:.3f}" if item.latitude is not None else "no coords"
        print(f"- {item.title[:60]} | {item.text[:80]} | {', '.join(item.tags)} | {where} | {item.attribution}")


if __name__ == "__main__":
    asyncio.run(main())
