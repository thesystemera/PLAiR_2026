"""City Pulse demand: what listeners ask (DemandLedger, saved as listener requests and clustered into trends) and the
region charts."""
import asyncio
import hashlib
import time
import uuid
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional
import pytz
from sqlalchemy import String, cast, func, select
from config import settings
from database.connection import AsyncSessionLocal
from database.models import PlayEvent
from models_global import run_on_gpu_executor
from service_registry import services
from services import log_service
from services.listener_request_service import daypart
from services.task_utils import spawn
from services_radio import geo
from services_radio.news_store import normalize_query
from services_radio.pulse_items import KIND_CHART, PulseItem, PulseListener, PulseQuery, _clip


class RegionCharts:
    def __init__(self):
        self._cache: dict[str, tuple[float, list]] = {}

    async def region_chart(self, region, catalog, q: PulseQuery) -> list[PulseItem]:
        cached = self._cache.get(region.key)
        if cached and time.monotonic() - cached[0] < settings.PULSE_CHART_CACHE_S:
            rows = cached[1]
        else:
            since = datetime.now(timezone.utc) - timedelta(days=7)
            async with AsyncSessionLocal() as db:
                plays = (await db.execute(
                    select(PlayEvent.track_id, func.count(), func.count(func.distinct(
                        func.coalesce(cast(PlayEvent.user_id, String), PlayEvent.session_id))))
                    .where(PlayEvent.region_key == region.key, PlayEvent.event_type == "play",
                           PlayEvent.started_at >= since)
                    .group_by(PlayEvent.track_id).order_by(func.count().desc()).limit(40))).all()
            rows = [(track_id, count, listeners) for track_id, count, listeners in plays]
            self._cache[region.key] = (time.monotonic(), rows)
        if not rows:
            return []
        genres: dict[str, float] = {}
        items = []
        for rank, (track_id, count, listeners) in enumerate(rows):
            track = catalog.get_track(track_id)
            if not track or track_id in getattr(catalog, "hidden_ids", set()):
                continue
            tags = track.get("derived_tags") or {}
            genre = str(tags.get("primary_genre") or "").strip()
            if genre:
                genres[genre] = genres.get(genre, 0.0) + count
            if rank < 5 and listeners >= settings.PULSE_CHART_MIN_LISTENERS:
                params = track.get("generation_params") or {}
                title, artist = (params.get("title") or "").strip(), (params.get("artist_name") or "").strip()
                if not title:
                    continue
                label = f"{title} by {artist}" if artist else title
                value = 0.7 - rank * 0.08
                if value is not None:
                    items.append(PulseItem(id=f"chart:{region.key}:{track_id}", kind=KIND_CHART,
                                           title=f"#{rank + 1} in {region.name} this week: {label}",
                                           text=f"{count} plays by {listeners} listeners", source="PLAiR plays",
                                           score=value, payload={"track_id": track_id}))
        total = sum(genres.values())
        if total and len(rows) >= settings.PULSE_CHART_MIN_LISTENERS:
            top = sorted(genres.items(), key=lambda kv: kv[1], reverse=True)[:3]
            summary = ", ".join(f"{g} ({v / total:.0%})" for g, v in top)
            value = 0.65
            if value is not None:
                items.append(PulseItem(id=f"chart:{region.key}:genres", kind=KIND_CHART,
                                       title=f"What {region.name} is playing this week", text=summary,
                                       source="PLAiR plays", score=value))
        return items


class DemandLedger:
    def __init__(self):
        self._salt_day: Optional[date] = None
        self._salt = ""
        self._topics: dict[tuple, tuple[float, list]] = {}
        self._recent: OrderedDict[tuple, float] = OrderedDict()

    def _asker_hash(self, asker: str, day: date) -> str:
        if self._salt_day != day:
            self._salt_day, self._salt = day, hashlib.sha256(f"{settings.JWT_SECRET_KEY}:{day}".encode()).hexdigest()
        return hashlib.sha256(f"{self._salt}:{asker}".encode()).hexdigest()[:24]

    def record(self, listener: PulseListener, node: str, query_text: str, live: bool,
               answers: Iterable[tuple] = ()) -> None:
        topic = normalize_query(query_text)[:120]
        if not topic or listener.region is None or not settings.PULSE_DEMAND_ENABLED:
            return
        if services.request_store is None or services.request_vector_db_service is None:
            return
        recent_key = (listener.asker, topic)
        last = self._recent.get(recent_key)
        if last is not None and time.monotonic() - last < settings.PULSE_REQUEST_DEDUPE_S:
            return
        self._recent[recent_key] = time.monotonic()
        while len(self._recent) > 2000:
            self._recent.popitem(last=False)
        now = datetime.now(timezone.utc)
        local = now
        if listener.tz_name:
            try:
                local = now.astimezone(pytz.timezone(listener.tz_name))
            except pytz.UnknownTimeZoneError:
                pass
        served = [(str(key)[:200], str(title or "")[:200]) for key, title in answers if key][:settings.PULSE_DEMAND_ANSWERS]
        meta = {
            "request_id": uuid.uuid4().hex,
            "text": query_text.strip()[:200],
            "intent": node,
            "topic": topic,
            "answers": [title for _, title in served if title],
            "answer_keys": [key for key, _ in served],
            "region_key": listener.region.key,
            "area": _clip(listener.location.description or listener.location.city or "", 80),
            "daypart": daypart(local),
            "weekday": local.strftime("%A"),
            "asked_at": now.isoformat(),
            "asker": self._asker_hash(listener.asker, now.date()),
            "live": bool(live),
        }
        spawn(self._record(meta), name="pulse_request")

    async def _record(self, meta: dict) -> None:
        try:
            where = await geo.resolver.resolve(meta.get("area")) if meta.get("area") else None
            if where is not None:
                meta["where"] = where.as_dict()
            rowid = await asyncio.to_thread(services.request_store.add, meta)
            await run_on_gpu_executor(services.request_vector_db_service.add_request, meta, rowid)
            self._topics = {k: v for k, v in self._topics.items() if k[0] != meta["region_key"]}
        except Exception as e:
            log_service.warning(f"[PULSE] request record failed: {type(e).__name__}: {e}")

    async def topics(self, region_key: str, days: int = 7, node: Optional[str] = None) -> list[dict]:
        vectors = services.request_vector_db_service
        if vectors is None:
            return []
        cache_key = (region_key, days, node)
        cached = self._topics.get(cache_key)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        since = datetime.now(timezone.utc) - timedelta(days=days)
        rows = [(rowid, meta) for rowid, meta in vectors.rows(region_key, since) if not node or meta.get("intent") == node]
        embedded = await run_on_gpu_executor(lambda: [(meta, vectors.weighted(meta)) for _, meta in rows])
        topics = _cluster_requests(embedded)
        self._topics[cache_key] = (time.monotonic(), topics)
        return topics

    async def hot(self, region_key: str, days: int = 7, min_askers: int = 3, limit: int = 10,
                  node: Optional[str] = None) -> list[dict]:
        return [topic for topic in await self.topics(region_key, days, node) if topic["askers"] >= min_askers][:limit]

    async def search(self, listener: PulseListener, text: str, days: int = 30, limit: int = 8,
                     use_ai: bool = False) -> list[tuple]:
        search = services.request_search
        if search is None or listener.region is None or not text:
            return []
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        region_key = listener.region.key
        return await search.search(text, n=limit, use_ai=use_ai,
                                   keep=lambda meta: meta.get("region_key") == region_key and
                                   (meta.get("asked_at") or "") >= since)

    async def prune(self) -> None:
        if services.request_store is not None:
            removed = await asyncio.to_thread(services.request_store.prune, settings.PULSE_DEMAND_KEEP_DAYS)
            if removed and services.request_vector_db_service is not None:
                services.request_vector_db_service.dirty = True


def _cluster_requests(rows: list[tuple]) -> list[dict]:
    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (a, _) in enumerate(rows):
        keys_a = set(a.get("answer_keys") or [])
        for j in range(i + 1, len(rows)):
            b, _ = rows[j]
            if a.get("intent") == b.get("intent") and (
                    a.get("topic") == b.get("topic") or keys_a & set(b.get("answer_keys") or [])):
                parent[find(j)] = find(i)
    groups: dict[int, dict] = {}
    for i, (meta, _) in enumerate(rows):
        group = groups.setdefault(find(i), {"node": meta.get("intent"), "queries": {}, "askers": set(), "asks": 0,
                                            "live": 0, "answers": {}, "areas": {}, "topics": set(),
                                            "last": meta.get("asked_at")})
        group["topics"].add(meta.get("topic"))
        group["queries"][meta.get("text")] = group["queries"].get(meta.get("text"), 0) + 1
        group["askers"].add(meta.get("asker"))
        group["asks"] += 1
        group["live"] += 1 if meta.get("live") else 0
        group["last"] = max(group["last"], meta.get("asked_at") or "")
        for title in meta.get("answers") or []:
            group["answers"][title] = group["answers"].get(title, 0) + 1
        if meta.get("area"):
            group["areas"][meta["area"]] = group["areas"].get(meta["area"], 0) + 1
    topics = []
    for group in groups.values():
        topics.append({
            "node": group["node"],
            "label": max(group["queries"].items(), key=lambda kv: kv[1])[0],
            "askers": len(group["askers"]),
            "asks": group["asks"],
            "live": group["live"],
            "top_answers": [t for t, _ in sorted(group["answers"].items(), key=lambda kv: kv[1], reverse=True)][:3],
            "top_area": max(group["areas"].items(), key=lambda kv: kv[1])[0] if group["areas"] else "",
            "topics": group["topics"],
            "last": group["last"],
        })
    topics.sort(key=lambda t: (t["askers"], t["asks"]), reverse=True)
    return topics


demand = DemandLedger()
charts = RegionCharts()
