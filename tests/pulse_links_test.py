import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from service_registry import services  # noqa: E402
from services_radio import geo, local_knowledge  # noqa: E402
from services_radio import pulse as pulse_kb  # noqa: E402
from services_radio import pulse_items, pulse_sources  # noqa: E402
from services_radio import regional_knowledge as rk  # noqa: E402
from services_radio.listener_location import ListenerLocation  # noqa: E402

NOW = datetime.now(timezone.utc)
REGION = rk.Region(key="tz:Test/City", name="Testville", country="NZ", center=(-36.85, 174.76))


def nugget(kind, ext, title, text="", entities=(), lat=None, lon=None):
    return {"id": f"{kind}:src:{ext}", "kind": kind, "region_key": REGION.key, "title": title, "text": text,
            "tags": [], "entities": list(entities), "country": "NZ",
            "where": {"label": text, "lat": lat, "lon": lon, "radius_m": 0, "scope": "spot"} if lat is not None else None,
            "starts_at": (NOW + timedelta(days=2)).isoformat() if kind == "event" else None}


class FakeVectorDb:
    version = 1

    def __init__(self, metas):
        self._metas = list(metas)

    def metas(self):
        return list(self._metas)


class FakeSearch:
    async def search(self, *args, **kwargs):
        return []

    async def similar_to(self, *args, **kwargs):
        return []


class FakeShoutouts:
    shoutouts = {
        "1_1": {"id": "1_1", "content_type": "shoutout", "full_transcription": "Who's going to Mountain Boy on Saturday? "
                "Meet at the Tuning Fork bar!", "timestamp": NOW.isoformat(),
                "user_data": {"username": "kiri", "location": "Karangahape Road, Testville", "latitude": -36.85,
                              "longitude": 174.76}},
        "1_2": {"id": "1_2", "content_type": "shoutout", "full_transcription": "Happy birthday mum",
                "timestamp": NOW.isoformat(), "user_data": {"username": "sam", "location": "Testville",
                                                            "latitude": -36.85, "longitude": 174.76}},
    }


async def main():
    local_knowledge.install(FakeVectorDb([
        nugget("event", "e1", "Mountain Boy - The Nights Tour", "The Tuning Fork, Sat", ["The Tuning Fork", "Mountain Boy"],
               -36.8481, 174.7722),
        nugget("event", "e2", "Sonu Nigam Live", "Spark Arena", ["Spark Arena", "Sonu Nigam"], -36.8485, 174.7720),
        nugget("place", "p1", "Brothers Beer", "Bar", ["Brothers Beer"], -36.8490, 174.7725),
    ]), FakeSearch(), FakeVectorDb([
        nugget("news", "n9", "Sonu Nigam adds second Auckland show", "RNZ"),
        nugget("news", "n10", "Burst pipe floods City Works Depot", "RNZ", (), -36.8491, 174.7726),
    ]), FakeSearch())
    services.user_content_service = FakeShoutouts()
    pulse = pulse_kb.Pulse([])
    listener = pulse_items.PulseListener(user=None, user_id=None, session_id="guest_x",
                                      location=ListenerLocation(latitude=-36.85, longitude=174.76, country_code="NZ"),
                                      region=REGION, taste=rk.Taste(), tz_name="Pacific/Auckland",
                                      where=geo.Where("Testville", -36.85, 174.76, 0, "spot"))
    checks = []

    def check(name, ok, detail=""):
        checks.append(ok)
        print(("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail else ""))

    shout = await pulse.related(listener, "community:shoutouts:1_1")
    check("shoutout links to the gig it mentions", any(l["id"] == "event:src:e1" for l in shout), str(shout))
    gig = await pulse.related(listener, "event:src:e1")
    check("gig links back to the shoutout", any(l["id"] == "community:shoutouts:1_1" for l in gig), str(gig))
    check("gig links to the bar next door", any(l["id"] == "place:src:p1" for l in gig), str(gig))
    check("gig does not link to another gig just for being close", not any(l["id"] == "event:src:e2" for l in gig))
    check("gig links to news on the same street", any(l["id"] == "news:src:n10" for l in gig), str(gig))
    sonu = await pulse.related(listener, "event:src:e2")
    check("gig links to news that names it", any(l["id"] == "news:src:n9" for l in sonu), str(sonu))
    birthday = await pulse.related(listener, "community:shoutouts:1_2")
    check("unrelated shoutout has no links", not birthday, str(birthday))
    items = [pulse_sources.shoutout_item(s, 0.5, listener) for s in FakeShoutouts.shoutouts.values()]
    await pulse.annotate_links(listener, items)
    check("search results carry link notes", bool(items[0].links) and not items[1].links, str([i.links for i in items]))
    print(f"{sum(checks)}/{len(checks)} passed")
    return all(checks)


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
