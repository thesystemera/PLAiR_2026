"""The listener limits (services/listener_limits.py), without a server, a database, a GPU or an LLM.

Checks: the tier table (defaults, saving, bad values refused, song credits per tier); the AI allowance (ok, low, out
per day and per month, when it tops up, guests sharing one public IP, private IPs left out); only asked-for work
counts (usage_tracking's asked context; the station's own talk and Suno songs don't; GPU seconds priced); the request
limit (burst, refill, covers and streams cheaper, the per-IP bucket for public IPs only); the middleware (429 with
X-Limit, the asked context inside a request); the AIAllowance dependency (polite 429, or the allowance for routes
that decide); the listener-facing words (no digits in what the hosts say).

Usage: python tests/listener_limits_test.py
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

TEMP = Path(tempfile.mkdtemp(prefix="plair_limits_"))
os.environ["LISTENER_LIMITS_PATH"] = str(TEMP / "limits.json")
os.environ["AI_ALLOWANCE_PATH"] = str(TEMP / "allowance.json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from services import listener_limits as limits, usage_tracking  # noqa: E402
from services.rate_limit_service import rate_limit_service  # noqa: E402

failures = []


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}{f' ({detail})' if detail else ''}")
    if not ok:
        failures.append(label)


def guest(n):
    return usage_tracking.UsageSubject(usage_tracking.KIND_GUEST, None, f"guest_0000000{n}-aaaa-bbbb-cccc-dddddddddddd")


def tier_table():
    print("tier table")
    table = limits.limits()
    check("three tiers", tuple(table["tiers"]) == limits.TIERS)
    check("guests get no song credits", table["tiers"]["guest"]["songs"] == 0)
    premium = table["tiers"]["premium"]
    check("Premium's worst case fits its price",
          premium["songs"] * 0.06 + premium["ai_usd_per_month"] < 4.99 - 0.44,
          f"{premium['songs']} songs + ${premium['ai_usd_per_month']}")
    saved = limits.store.save({"tiers": {"basic": {"ai_usd_per_day": "0.2", "songs": "7", "songs_period": "day"}}})
    check("saving merges onto the defaults", saved["tiers"]["basic"]["ai_usd_per_day"] == 0.2
          and saved["tiers"]["premium"] == limits.DEFAULT_LIMITS["tiers"]["premium"])
    check("song credits come from the table", limits.song_quota(SimpleNamespace(tier="basic", id=3)) == (7, "day"))
    store = limits.LimitsStore(limits.settings.LISTENER_LIMITS_PATH)
    store.load()
    check("a saved table is read back", store.get()["tiers"]["basic"]["songs"] == 7)
    for bad in ({"tiers": {"guest": {"ai_usd_per_day": -1}}}, {"warn_at": 1.5},
                {"tiers": {"basic": {"songs_period": "year"}}}, {"requests": {"per_second": 0}}):
        try:
            limits.store.save(bad)
            check(f"refuses {bad}", False)
        except ValueError:
            check(f"refuses {bad}", True)
    limits.store.save({})
    check("Premium tier read from users.tier", limits.tier_of(SimpleNamespace(tier="premium")) == "premium"
          and limits.tier_of(SimpleNamespace(tier="basic")) == "basic" and limits.tier_of(None) == "guest")


def allowance():
    print("AI allowance")
    now = time.time()
    key = guest(1).key
    check("a new listener is ok", limits.allowance(key, "guest", now=now).state == "ok")
    limits.ledger.add(key, 0.042, now - 3600)
    low = limits.allowance(key, "guest", now=now)
    check("84% of the day is low", low.state == "low" and low.window == "day", f"{low.used:.2f}")
    limits.ledger.add(key, 0.01, now)
    out = limits.allowance(key, "guest", now=now)
    check("over the day is out", out.out)
    check("it tops up when the old spend leaves the day", 22 * 3600 < out.resets_in_s <= 23 * 3600,
          f"{out.resets_in_s / 3600:.1f} h")
    check("a day later it is ok again", limits.allowance(key, "guest", now=now + 25 * 3600).state == "ok")

    month_key = guest(2).key
    for day in range(1, 8):
        limits.ledger.add(month_key, 0.045, now - day * 86400)
    month = limits.allowance(month_key, "guest", now=now)
    check("the month cap holds a listener who stays under the day cap", month.out and month.window == "month",
          f"{month.used:.2f} of the month")

    for n in range(3, 10):
        limits.ledger.add("ip:9.9.9.9", 0.045, now)
        limits.ledger.add(guest(n).key, 0.045, now)
    shared = limits.allowance(guest(10).key, "guest", ip="9.9.9.9", now=now)
    check("guests on one public IP share a daily cap", shared.out, f"{shared.used:.2f}")
    check("a private IP has no shared cap",
          limits.allowance(guest(10).key, "guest", ip="192.168.1.1", now=now).state == "ok")
    check("a signed-in listener is not held by the IP", limits.allowance("user:77", "basic", ip="9.9.9.9",
                                                                         now=now).state == "ok")


def counting():
    print("what counts")
    subject = guest(11)
    usage_tracking.spend_hook = limits.note_spend
    token = usage_tracking.bind(subject)
    try:
        usage_tracking.record_llm(role="live", provider="gemini", model="m", cost_usd=0.01, feature="station talk")
        check("the station's own talk doesn't count", limits.ledger.spent(subject.key, "day")[0] == 0)
        client = usage_tracking.bind_client("1.0.0.1", asked=True)
        try:
            usage_tracking.record_llm(role="live", provider="gemini", model="m", cost_usd=0.01, feature="dj turn")
            usage_tracking.record_suno_generation()
            usage_tracking.record_gpu("whisper.fast", 2.0)
        finally:
            usage_tracking.unbind_client(client)
        spent = limits.ledger.spent(subject.key, "day")[0]
        expected = 0.01 + 2.0 * limits.limits()["gpu_second_usd"]
        check("asked-for work counts, GPU priced, Suno left to its credits", abs(spent - expected) < 1e-9,
              f"${spent:.5f}")
        check("and counts for the guest's IP", abs(limits.ledger.spent("ip:1.0.0.1", "day")[0] - expected) < 1e-9)
        client = usage_tracking.bind_client("1.0.0.1", asked=False)
        try:
            with usage_tracking.asked():
                check("asked() keeps the IP", usage_tracking.client_ip() == "1.0.0.1")
                usage_tracking.record_llm(role="announce", provider="deepseek", model="m", cost_usd=0.001,
                                          feature="welcome")
        finally:
            usage_tracking.unbind_client(client)
        check("a welcome the guest caused counts", abs(limits.ledger.spent(subject.key, "day")[0]
                                                      - expected - 0.001) < 1e-9)
    finally:
        usage_tracking.unbind(token)


def requests():
    print("request limit")
    rules = limits.limits()["requests"]
    allowed = sum(not limits.allow_request("guest:burst", "192.168.1.5", 1.0) for _ in range(int(rules["burst"]) + 20))
    check("a burst passes up to its size", allowed == rules["burst"], f"{allowed}")
    wait = limits.allow_request("guest:burst", "192.168.1.5", 1.0)
    check("then asks to wait about a request's worth", 1 <= wait <= 2, f"{wait}s")
    time.sleep(0.2)
    check("and refills", limits.allow_request("guest:burst", "192.168.1.5", 1.0) == 0)
    covers = sum(not limits.allow_request("guest:covers", None, limits.request_cost("/api/artwork/x/pack/512"))
                 for _ in range(500))
    check("covers count a quarter", covers >= 4 * rules["burst"] - 4, f"{covers} covers")
    check("other routes count one", limits.request_cost("/api/search/semantic") == 1.0)
    ip_allowed = sum(not limits.allow_request(f"guest:rotating-{n}", "8.8.4.4", 1.0) for n in range(400))
    check("rotating guest ids still meet the IP bucket", ip_allowed == rules["ip_burst"], f"{ip_allowed}")
    local = sum(not limits.allow_request(f"guest:lan-{n}", "127.0.0.1", 1.0) for n in range(400))
    check("local and LAN addresses have no IP bucket", local == 400)


def middleware():
    print("middleware")
    from fastapi import FastAPI, Depends, Request
    from fastapi.testclient import TestClient
    from listener_middleware import ListenerMiddleware
    from routers.deps import AIAllowance

    app = FastAPI()
    app.add_middleware(ListenerMiddleware)
    seen = {}

    @app.get("/api/probe")
    async def probe(request: Request):
        seen["client"] = usage_tracking._client.get()
        seen["subject"] = usage_tracking.current_subject().key
        return {}

    @app.get("/api/ai")
    async def ai(allowance: limits.Allowance = Depends(AIAllowance())):
        return {"state": allowance.state}

    @app.get("/api/soft")
    async def soft(allowance: limits.Allowance = Depends(AIAllowance(refuse=False))):
        return {"state": allowance.state}

    client = TestClient(app)
    gid = "guest_0000aaaa-1111-2222-3333-444455556666"
    client.get("/api/probe", headers={"X-Guest-ID": gid})
    check("a request is marked asked", seen["client"] is not None and seen["client"].asked)
    check("and attributed to the guest", seen["subject"] == f"guest:{gid}")
    limits.ledger.add(f"guest:{gid}", 1.0)
    refused = client.get("/api/ai", headers={"X-Guest-ID": gid})
    check("an AI route refuses politely when it's used up", refused.status_code == 429
          and refused.headers.get("x-limit") == "ai_allowance" and "tops up" in refused.json()["detail"],
          refused.json().get("detail"))
    check("a route that decides gets the allowance", client.get("/api/soft", headers={"X-Guest-ID": gid}).json()
          == {"state": "out"})
    other = "guest_0000bbbb-1111-2222-3333-444455556666"
    statuses = [client.get("/api/probe", headers={"X-Guest-ID": other}).status_code for _ in range(130)]
    refused = client.get("/api/probe", headers={"X-Guest-ID": other})
    check("a flood gets 429 with X-Limit: requests", 429 in statuses and refused.headers.get("x-limit") == "requests"
          and refused.headers.get("retry-after"), f"{statuses.count(200)} passed")


def words():
    print("words")
    for tier in limits.TIERS:
        out = limits.Allowance(tier, "out", 1.2, "day", 5 * 3600)
        line = limits.host_line(out)
        check(f"{tier}: the hosts' line has no digits", not any(c.isdigit() for c in line.replace("&0.2&", "")
                                                                  .replace("&0.1&", "")), line[:70])
        low = limits.Allowance(tier, "low", 0.86, "day", 3 * 3600)
        note = limits.host_note(low)
        check(f"{tier}: the hosts' note says how much and when", "86%" in note and "tops up" in note)
        check(f"{tier}: the notice reads well", limits.notice_text(low).startswith("You've used most of today's"),
              limits.notice_text(low))
    check("no note while there is plenty left", limits.host_note(limits.Allowance("guest", "ok", 0.2, "day", 0)) == "")


def main():
    rate_limit_service._buckets.clear()
    tier_table()
    allowance()
    counting()
    requests()
    middleware()
    words()
    asyncio.run(asyncio.sleep(0))
    print(f"\n{'all passed' if not failures else f'{len(failures)} failed'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
