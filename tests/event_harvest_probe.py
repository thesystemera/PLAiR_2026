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
    parser = argparse.ArgumentParser(description="Crawl for events in one city without saving anything")
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
    parser.add_argument("--pages", type=int, default=20, help="most pages to read in total")
    parser.add_argument("--url", action="append", default=[], help="start from this page instead of discovering")
    parser.add_argument("--kind", default=event_harvest.VENUE, help="source kind for --url pages")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    region = Region(key=f"tz:{args.tz}", name=args.city, country=args.country, center=(args.lat, args.lon))
    collector = event_harvest.WebEventsCollector(AsyncSessionLocal, None, AIService())
    zone = event_harvest.zone_for(region)
    if args.url:
        queue = [(args.kind, url, 1.0, 0) for url in args.url]
    else:
        print("Discovering through news search:")
        queue = [(kind, url, 1.0, 0) for kind, url in await discover(args.city, args.country, args.queries,
                                                                       args.per_query)]
        queue += [(event_harvest.VENUE, website, score, 0) for _, website, score in (await collector._venues(region))[:args.venues]]
    budget = {"llm": args.llm, "geocode": args.geocode}
    events, seen, read = [], set(), 0
    while queue and read < args.pages:
        queue.sort(key=lambda entry: (event_harvest.READ_ORDER.get(entry[0], 9), -entry[2]))
        kind, url, score, depth = queue.pop(0)
        if event_harvest.url_key(url) in seen:
            continue
        seen.add(event_harvest.url_key(url))
        source = EventSource(url=url, kind=kind, url_key=event_harvest.url_key(url), score=score, depth=depth)
        result = await collector.read(source, region, zone, budget)
        read += 1
        for event in result.events:
            event.url = event.url or url
        print(f"{'  ' * depth}{kind:8} {score:.2f} {result.status:9} {result.method or '-':9} "
              f"{len(result.events):3} events  {url[:90]}")
        for link in result.links:
            print(f"{'  ' * depth}         + {link.kind} {link.score:.2f} {link.url[:100]}")
            queue.append((link.kind, link.url, link.score, depth + 1))
        events += result.events
    items = await collector.to_items(region, zone, events, budget)
    print(f"\n{read} pages, {len(events)} events read, {len(items)} kept:")
    for item in sorted(items, key=lambda i: i.starts_at):
        where = f"{item.latitude:.3f},{item.longitude:.3f}" if item.latitude is not None else "no coords"
        print(f"- {item.title[:60]} | {item.text[:80]} | {', '.join(item.tags)} | {where} | {item.attribution}")


if __name__ == "__main__":
    asyncio.run(main())
