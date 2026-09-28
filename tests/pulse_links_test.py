import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from services_radio import pulse as pulse_kb  # noqa: E402
from services_radio import regional_knowledge as rk  # noqa: E402

NOW = datetime.now(timezone.utc)
REGION = rk.Region(key="tz:Test/City", name="Testville", country="NZ", center=(-36.85, 174.76))


def item(kind, source, ext, title, text="", entities=(), lat=None, lon=None, published=None, starts=None):
    return rk.KnowledgeItem(source=source, kind=kind, region_key=REGION.key, external_id=ext, title=title, text=text,
                            expires_at=NOW + timedelta(days=5), entities=list(entities), latitude=lat, longitude=lon,
                            published_at=published, starts_at=starts)


POOL = [
    item("event", "ticketmaster", "e1", "Mountain Boy - The Nights Tour", "The Tuning Fork, Sat",
         ["The Tuning Fork", "Mountain Boy"], -36.8481, 174.7722, starts=NOW + timedelta(days=2)),
    item("event", "ticketmaster", "e2", "Sonu Nigam Live", "Spark Arena", ["Spark Arena", "Sonu Nigam"],
         -36.8485, 174.7720, starts=NOW + timedelta(days=3)),
    item("place", "google_places", "p1|bar", "Brothers Beer", "Bar", ["Brothers Beer"], -36.8490, 174.7725),
    item("community", "shoutouts", "1_1", "Shoutout from kiri", "Who's going to Mountain Boy on Saturday? Meet at "
         "the Tuning Fork bar!", published=NOW - timedelta(hours=5)),
    item("community", "shoutouts", "1_2", "Shoutout from sam", "Happy birthday mum", published=NOW - timedelta(days=40)),
    item("news", "google_news", "9", "Sonu Nigam adds second Auckland show", "RNZ", published=NOW - timedelta(days=1)),
]


class FakeStore:
    _generations = {}
    _read_cache = {}

    async def items(self, region_key, kinds):
        return [i for i in POOL if i.kind in kinds]


class FakeRegional:
    store = FakeStore()


async def main():
    rk.set_regional_knowledge(FakeRegional())
    pulse = pulse_kb.Pulse([])
    listener = pulse_kb.PulseListener(user=None, user_id=None, session_id="guest_x", location=None, region=REGION,
                                      taste=rk.Taste(), tz_name="Pacific/Auckland")
    checks = []

    def check(name, ok, detail=""):
        checks.append(ok)
        print(("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail else ""))

    shout = await pulse.related(listener, "community:shoutouts:1_1")
    check("shoutout links to the gig it mentions", any(l["id"] == "event:ticketmaster:e1" for l in shout), str(shout))
    gig = await pulse.related(listener, "event:ticketmaster:e1")
    check("gig links back to the shoutout", any(l["id"] == "community:shoutouts:1_1" for l in gig), str(gig))
    check("gig links to the bar nearby", any(l["id"] == "place:google_places:p1|bar" and l["reason"] == "nearby"
                                             for l in gig), str(gig))
    news = await pulse.related(listener, "news:google_news:9")
    check("news links to the event it mentions", any(l["id"] == "event:ticketmaster:e2" for l in news), str(news))
    birthday = await pulse.related(listener, "community:shoutouts:1_2")
    check("unrelated shoutout has no links", not birthday, str(birthday))
    items = [pulse_kb.from_regional(i, 0.5) for i in POOL if i.kind == "community"]
    await pulse.annotate_links(listener, items)
    check("search results carry link notes", bool(items[0].links) and not items[1].links,
          str([i.links for i in items]))
    print(f"{sum(checks)}/{len(checks)} passed")
    return all(checks)


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
