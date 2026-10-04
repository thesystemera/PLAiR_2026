import asyncio
import math
import re
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional
from config import settings
from database.connection import AsyncSessionLocal
from database.models import User
from service_registry import services
from services import listener_plays, log_service
from services_radio import geo, local_knowledge, place_memory
from services_radio import regional_knowledge as regional_kb
from services_radio.dj_bank_sources import listener_taste
from services_radio.pulse_items import (
    ALL_KINDS, INTENTS, KIND_ARTIST, KIND_CHART, KIND_COMMUNITY, KIND_EVENT, KIND_NEWS, KIND_PLACE, KIND_REVIEW,
    KIND_TREND, KnowledgeNode, LINK_NAME_MIN_CHARS, LINK_STOP_NAMES, LISTENER_CACHE_MAX, LISTENER_CACHE_S,
    LISTENING_NOTE, LISTENING_TOP, OFFERED_MAX, PLACED_KINDS, PulseItem, PulseListener, PulseQuery, _age,
    _base_title, _clip, _local_when, _parse_time, _recency,
)
from services_radio.pulse_sources import (
    AreaNode, ArtistNode, ChartsNode, CommunityNode, LocalNuggetsNode, MusicNode, NewsNode, PlacesNode, ReviewsNode,
    TrendsNode, WeatherNode, _top_reply_entry, shoutout_meta,
)
from services_radio.pulse_demand import demand


async def note_request(user, user_id: Optional[int], session_id: Optional[str], node: str, query: Optional[str],
                       answers: Iterable[tuple] = (), live: bool = False) -> None:
    pulse = get_pulse()
    if pulse is None or not query or not (user_id or session_id):
        return
    try:
        listener = await pulse.listener(user_id, session_id, user)
        demand.record(listener, node, query, live, answers)
    except Exception as e:
        log_service.warning(f"[PULSE] demand note failed: {type(e).__name__}: {e}")


class Pulse:
    def __init__(self, nodes: list[KnowledgeNode]):
        self.nodes = nodes
        self._listeners: OrderedDict[str, tuple[float, PulseListener]] = OrderedDict()
        self._offered: OrderedDict[tuple, float] = OrderedDict()
        self._region_keys: OrderedDict[str, tuple[float, Optional[str]]] = OrderedDict()
        self._context_memo: OrderedDict[tuple, tuple[float, asyncio.Future]] = OrderedDict()
        self._link_cache: dict[str, tuple] = {}

    def node(self, name: str) -> Optional[KnowledgeNode]:
        return next((n for n in self.nodes if n.name == name), None)

    async def listener(self, user_id: Optional[int], session_id: Optional[str],
                       user: Optional[User] = None) -> PulseListener:
        key = f"{user_id or ''}:{session_id or ''}"
        cached = self._listeners.get(key)
        if cached and time.monotonic() - cached[0] < LISTENER_CACHE_S:
            return cached[1]
        if user is None and user_id:
            async with AsyncSessionLocal() as db:
                user = await db.get(User, user_id)
        from services_radio import context_service
        location = await context_service.listener_location(user, session_id)
        tz_name = location.timezone or context_service.listener_timezone(user, session_id)
        region = regional_kb.resolve_region(user, tz_name, location=location)
        taste = await listener_taste(user, user_id, session_id, AsyncSessionLocal, services.catalog_service)
        resolved = PulseListener(user=user, user_id=user_id, session_id=session_id, location=location, region=region,
                                 taste=taste, tz_name=tz_name, where=await geo.listener_where(location))
        self._listeners[key] = (time.monotonic(), resolved)
        self._listeners.move_to_end(key)
        while len(self._listeners) > LISTENER_CACHE_MAX:
            self._listeners.popitem(last=False)
        self._remember_region(session_id, region.key if region else None)
        return resolved

    async def listener_for_session(self, session_dict: dict) -> PulseListener:
        return await self.listener(session_dict.get("user_id"), session_dict.get("session_id"))

    def _remember_region(self, session_id: Optional[str], region_key: Optional[str]) -> None:
        if not session_id:
            return
        self._region_keys[session_id] = (time.monotonic(), region_key)
        self._region_keys.move_to_end(session_id)
        while len(self._region_keys) > LISTENER_CACHE_MAX:
            self._region_keys.popitem(last=False)

    async def region_key_for(self, user_id: Optional[int], session_id: Optional[str]) -> Optional[str]:
        cached = self._region_keys.get(session_id or "")
        if cached and time.monotonic() - cached[0] < 3600:
            return cached[1]
        try:
            listener = await self.listener(user_id, session_id)
            return listener.region.key if listener.region else None
        except Exception as e:
            log_service.warning(f"[PULSE] region lookup failed: {type(e).__name__}: {e}")
            self._remember_region(session_id, None)
            return None

    def _was_offered(self, listener: PulseListener, item_id: str) -> bool:
        stamp = self._offered.get((listener.asker, item_id))
        return stamp is not None and time.monotonic() - stamp < settings.PULSE_OFFERED_MEMORY_S

    def mark_offered(self, listener: PulseListener, items: Iterable[PulseItem]) -> None:
        now = time.monotonic()
        for item in items:
            self._offered[(listener.asker, item.id)] = now
            self._offered.move_to_end((listener.asker, item.id))
        while len(self._offered) > OFFERED_MAX:
            self._offered.popitem(last=False)

    async def _search_node(self, node: KnowledgeNode, q: PulseQuery) -> list[PulseItem]:
        try:
            return await asyncio.wait_for(node.search(q), timeout=settings.PULSE_NODE_TIMEOUT_S)
        except asyncio.TimeoutError:
            log_service.warning(f"[PULSE] {node.name} search timed out")
        except Exception as e:
            log_service.warning(f"[PULSE] {node.name} search failed: {type(e).__name__}: {e}")
        return []

    async def query(self, q: PulseQuery) -> list[PulseItem]:
        started = time.perf_counter()
        if q.kinds is not None:
            q.kinds = {k for k in q.kinds if k in ALL_KINDS} or None
        nodes = [n for n in self.nodes if n.matches(q)]
        results = await asyncio.gather(*(self._search_node(node, q) for node in nodes))
        found = {node.name: items for node, items in zip(nodes, results)}
        items = [item for group in results for item in group]
        live_node = None
        if q.allow_fetch and q.text:
            for node in nodes:
                try:
                    if node.can_fetch(q) and await node.wants_fetch(q, items):
                        live_node = node
                        break
                except Exception as e:
                    log_service.warning(f"[PULSE] {node.name} fetch check failed: {type(e).__name__}: {e}")
            if live_node is not None:
                try:
                    fetched = await asyncio.wait_for(live_node.fetch(q), timeout=settings.PULSE_FETCH_TIMEOUT_S)
                except Exception as e:
                    log_service.warning(f"[PULSE] {live_node.name} live fetch failed: {type(e).__name__}: {e}")
                    fetched = []
                known = {item.id for item in items}
                items.extend(item for item in fetched if item.id not in known)
        items = self._apply_facets(q, items)
        ranked = self._rank(q, items)
        if ranked and q.listener.region is not None:
            await self.annotate_links(q.listener, ranked)
        if q.record_demand and q.text:
            answers = [item for item in ranked if item.kind not in (KIND_TREND, KIND_CHART)]
            kinds_served = [item.kind for item in answers] or sorted(q.kinds or [])
            intent = INTENTS.get(max(set(kinds_served), key=kinds_served.count), "any") if kinds_served else "any"
            demand.record(q.listener, intent, q.text, live_node is not None,
                          [(item.id, item.title) for item in answers])
        log_service.detail(
            f"[PULSE] {log_service.who(q.listener.session_id)} '{q.text or '*'}' kinds={sorted(q.kinds or [])} -> "
            f"{len(ranked)} items ({', '.join(f'{k}:{len(v)}' for k, v in found.items() if v) or 'none'}"
            f"{', live ' + live_node.name if live_node else ''}) {(time.perf_counter() - started) * 1000:.0f} ms",
            "pulse")
        return ranked


    def _apply_facets(self, q: PulseQuery, items: list[PulseItem]) -> list[PulseItem]:
        here = q.listener.where
        radius = q.radius_m or settings.PULSE_NEAR_RADIUS_M
        kept = []
        for item in items:
            if here is not None and item.where is not None:
                item.near = geo.relation(here, item.where)
                item.gap_m = geo.gap_m(here, item.where)
            if (q.near_me or q.radius_m) and here is not None and item.kind in PLACED_KINDS and \
                    not geo.near(here, item.where, radius):
                continue
            if q.max_age_days is not None and item.published is not None and \
                    (q.listener.now - item.published).total_seconds() > q.max_age_days * 86400:
                continue
            if item.kind in (KIND_NEWS, KIND_COMMUNITY):
                item.score *= 0.6 + 0.4 * _recency(item.published)
            kept.append(item)
        return kept

    def _sort(self, q: PulseQuery, items: list[PulseItem]) -> list[PulseItem]:
        epoch = datetime.min.replace(tzinfo=timezone.utc)
        far = datetime.max.replace(tzinfo=timezone.utc)
        if q.sort == "newest":
            return sorted(items, key=lambda i: i.published or i.when or epoch, reverse=True)
        if q.sort == "soonest":
            return sorted(items, key=lambda i: i.when if i.when and i.when >= q.listener.now else far)
        if q.sort == "nearest":
            return sorted(items, key=lambda i: i.gap_m if i.gap_m is not None else math.inf)
        return sorted(items, key=lambda item: item.score, reverse=True)

    def _region_index(self, region_key: str) -> dict:
        db = local_knowledge.local_vector_db
        stamp = db.version if db is not None else 0
        cached = self._link_cache.get(region_key)
        if cached and cached[0] == stamp and time.monotonic() - cached[1] < 300:
            return cached[2]
        by_id: Dict[str, Dict[str, Any]] = {}
        names: Dict[str, list] = {}
        for meta in db.metas() if db is not None else []:
            if meta.get("region_key") != region_key:
                continue
            by_id[meta["id"]] = meta
            candidates = list(meta.get("entities") or [])
            if meta.get("kind") == KIND_EVENT:
                candidates.append(meta.get("title") or "")
            for name in candidates:
                key = " ".join((name or "").lower().split())
                if len(key) >= LINK_NAME_MIN_CHARS and key not in LINK_STOP_NAMES:
                    names.setdefault(key, []).append(meta["id"])
        pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")\b",
                             re.IGNORECASE) if names else None
        index = {"by_id": by_id, "names": names, "pattern": pattern}
        self._link_cache[region_key] = (stamp, time.monotonic(), index)
        return index

    @staticmethod
    def _mentions(index: dict, text: str) -> list[tuple]:
        if index["pattern"] is None or not text:
            return []
        found = []
        for match in index["pattern"].finditer(text):
            for target in index["names"].get(" ".join(match.group(1).lower().split()), []):
                if (target, match.group(1)) not in found:
                    found.append((target, match.group(1)))
        return found

    def _region_shoutouts(self, listener: PulseListener) -> list[Dict[str, Any]]:
        store = services.user_content_service
        found = []
        for shoutout in list((getattr(store, "shoutouts", None) or {}).values()):
            if shoutout.get("content_type", "shoutout") != "shoutout":
                continue
            user_data = shoutout.get("user_data") or {}
            there = geo.from_row("", user_data.get("latitude"), user_data.get("longitude"))
            if listener.where is not None and there is not None:
                if geo.gap_m(listener.where, there) > settings.PULSE_COMMUNITY_RADIUS_KM * 1000:
                    continue
            elif not (listener.region and listener.region.name.lower() in (user_data.get("location") or "").lower()):
                continue
            found.append(shoutout)
        return found

    def _spatial_pool(self, listener: PulseListener, index: dict) -> list[dict]:
        pool = []
        for meta in index["by_id"].values():
            where = geo.Where.from_dict(meta.get("where"))
            if where is not None and where.fine:
                pool.append({"id": meta["id"], "kind": meta.get("kind"), "title": meta.get("title") or "", "where": where})
        for shoutout in self._region_shoutouts(listener):
            sm = shoutout_meta(shoutout)
            where = geo.Where.from_dict(sm["where"])
            if where is not None and where.fine:
                pool.append({"id": sm["id"], "kind": KIND_COMMUNITY, "title": f"{sm['title']}: {_clip(sm['text'], 60)}",
                             "where": where})
        for meta in self._news_pool(listener):
            where = geo.Where.from_dict(meta.get("where"))
            if where is not None and where.fine:
                pool.append({"id": meta["id"], "kind": KIND_NEWS, "title": meta.get("title") or "", "where": where})
        return pool

    @staticmethod
    def _news_pool(listener: PulseListener) -> list[Dict[str, Any]]:
        db = local_knowledge.news_vector_db
        country = (listener.location.country_code or settings.NEWS_DEFAULT_COUNTRY).upper()
        return [meta for meta in db.metas() if (meta.get("country") or "").upper() == country] \
            if db is not None else []

    @staticmethod
    def _nearby(here: Optional[geo.Where], pool: list[dict], exclude: str, limit: int = 3) -> list[dict]:
        own_kind = exclude.split(":", 1)[0]
        found = []
        for entry in pool:
            if entry["id"] == exclude or (entry["kind"] == own_kind and own_kind in (KIND_EVENT, KIND_PLACE)):
                continue
            gap = geo.overlap(here, entry["where"], settings.PULSE_LINK_DISTANCE_M)
            if gap is not None:
                found.append((gap, entry))
        found.sort(key=lambda pair: pair[0])
        return [{"id": entry["id"], "kind": entry["kind"], "title": entry["title"],
                 "reason": "same spot" if gap == 0 else f"{geo.span(gap)} away"} for gap, entry in found[:limit]]

    async def related(self, listener: PulseListener, pulse_id: str, limit: int = 6) -> list[dict]:
        if listener.region is None:
            return []
        index = self._region_index(listener.region.key)
        found: Dict[str, dict] = {}

        own_base = _base_title((index["by_id"].get(pulse_id) or {}).get("title") or "")

        def add(link_id: str, kind: str, title: str, reason: str) -> None:
            base = _base_title(title)
            if link_id == pulse_id or link_id in found or (own_base and base == own_base) or \
                    base in {_base_title(f["title"]) for f in found.values()}:
                return
            found[link_id] = {"id": link_id, "kind": kind, "title": title, "reason": reason}

        if pulse_id.startswith("community:shoutouts:"):
            shoutout = (getattr(services.user_content_service, "shoutouts", None) or {}).get(pulse_id.split(":", 2)[2])
            if shoutout is None:
                return []
            sm = shoutout_meta(shoutout)
            for target, name in self._mentions(index, sm["text"]):
                meta = index["by_id"].get(target)
                if meta:
                    add(target, meta["kind"], meta["title"], f"mentions {name}")
            for link in self._nearby(geo.Where.from_dict(sm["where"]), self._spatial_pool(listener, index), pulse_id):
                add(link["id"], link["kind"], link["title"], link["reason"])
            return list(found.values())[:limit]

        meta = index["by_id"].get(pulse_id)
        if meta is None:
            return []
        own_names = {" ".join(n.lower().split()) for n in [*(meta.get("entities") or []), meta.get("title") or ""]
                     if len(n or "") >= LINK_NAME_MIN_CHARS}
        for shoutout in self._region_shoutouts(listener):
            text = shoutout_meta(shoutout)["text"].lower()
            hit = next((name for name in own_names if name and name not in LINK_STOP_NAMES and
                        re.search(r"\b" + re.escape(name) + r"\b", text)), None)
            if hit:
                sm = shoutout_meta(shoutout)
                add(sm["id"], KIND_COMMUNITY, sm["title"], f"shoutout mentioning {hit}")
        for other in self._news_pool(listener):
            if any(re.search(r"\b" + re.escape(name) + r"\b", (other.get("title") or "").lower())
                   for name in own_names if name not in LINK_STOP_NAMES):
                add(other["id"], KIND_NEWS, other["title"], "in the news")
        for link in self._nearby(geo.Where.from_dict(meta.get("where")), self._spatial_pool(listener, index), pulse_id):
            add(link["id"], link["kind"], link["title"], link["reason"])
        return list(found.values())[:limit]

    async def annotate_links(self, listener: PulseListener, items: list[PulseItem]) -> None:
        try:
            index = self._region_index(listener.region.key)
        except Exception as e:
            log_service.warning(f"[PULSE] link index failed: {type(e).__name__}: {e}")
            return
        shoutouts = None
        pool = None
        for item in items:
            links = []
            if item.kind in (KIND_COMMUNITY, KIND_NEWS):
                text = f"{item.title} {item.text}"
                for target, name in self._mentions(index, text):
                    meta = index["by_id"].get(target)
                    if meta and meta["title"] not in {link["title"] for link in links}:
                        links.append({"id": target, "title": meta["title"], "reason": f"mentions {name}"})
            elif item.kind in (KIND_EVENT, KIND_PLACE):
                if shoutouts is None:
                    shoutouts = [shoutout_meta(s) for s in self._region_shoutouts(listener)]
                names = {" ".join(n.lower().split()) for n in [*item.entities, item.title]
                         if len(n or "") >= LINK_NAME_MIN_CHARS and " ".join(n.lower().split()) not in LINK_STOP_NAMES}
                for sm in shoutouts:
                    hit = next((n for n in names if re.search(r"\b" + re.escape(n) + r"\b", sm["text"].lower())), None)
                    if hit:
                        links.append({"id": sm["id"], "title": f"{sm['title']}: {_clip(sm['text'], 60)}",
                                      "reason": f"shoutout mentioning {hit}"})
            if item.where is not None and item.where.fine:
                if pool is None:
                    pool = self._spatial_pool(listener, index)
                known = {link["id"] for link in links}
                links.extend(link for link in self._nearby(item.where, pool, item.id) if link["id"] not in known)
            item.links = links[:3]

    def _rank(self, q: PulseQuery, items: list[PulseItem]) -> list[PulseItem]:
        seen = set()
        unique = []
        for item in items:
            if item.id in seen or not item.title:
                continue
            seen.add(item.id)
            item.aired = item.aired or self._was_offered(q.listener, item.id)
            unique.append(item)
        for item in unique:
            if item.aired:
                item.score -= settings.PULSE_AIRED_PENALTY
        unique = self._sort(q, unique)
        by_kind: dict[str, list[PulseItem]] = {}
        for item in unique:
            by_kind.setdefault(item.kind, []).append(item)
        order = [k for k in q.kind_order if k in by_kind] + [k for k in ALL_KINDS if k in by_kind and k not in q.kind_order]
        picked = [item for kind in order for item in by_kind[kind][:q.per_kind]]
        return picked[:q.limit]

    async def detail(self, listener: PulseListener, item_id: str) -> Optional[dict]:
        kind, _, key = item_id.partition(":")
        entry = None
        if kind == KIND_COMMUNITY:
            shoutout = (getattr(services.user_content_service, "shoutouts", None) or {}).get(key.split(":", 1)[-1])
            if shoutout is not None:
                sm = shoutout_meta(shoutout)
                entry = {"id": item_id, "kind": kind, "title": sm["title"], "said": sm["text"], "area": sm["area"],
                         "tags": sm["tags"], **_where_entry(listener, sm["where"])}
                published = _parse_time(sm["published_at"])
                if published:
                    entry["age"] = _age(published)
                if sm["audio"]:
                    entry["audio"] = f"${sm['audio']}$"
                    entry["how_to_play"] = "Put the audio value in your reply to play the clip on air."
                entry.update(await _top_reply_entry(listener, shoutout.get("id") or key.split(":", 1)[-1]))
        elif kind == KIND_REVIEW:
            from services_radio import community_on_air
            review = (getattr(services.user_content_service, "shoutouts", None) or {}).get(key)
            enriched = services.user_content_service.get_enriched_shoutout(key) if review is not None else None
            if enriched is not None:
                track = enriched.get("track") or {}
                entry = {"id": item_id, "kind": kind, "title": f"Review from {community_on_air.speaker(enriched)}",
                         "song": {k: track.get(k) for k in ("title", "artist") if track.get(k)},
                         "said": community_on_air.text_of(enriched)}
                published = _parse_time(enriched.get("timestamp"))
                if published:
                    entry["age"] = _age(published)
                marker = community_on_air.audio_marker(enriched)
                if marker:
                    entry["audio"] = marker
                    entry["how_to_play"] = "Put the audio value in your reply to play the review on air."
        elif kind == KIND_PLACE and ":" not in key:
            place = await place_memory.get_place(key)
            if place:
                entry = {"id": item_id, "kind": kind, **{k: v for k, v in place.items()
                                                         if v not in (None, "") and k not in ("latitude", "longitude")}}
        elif listener.region is not None:
            meta = self._region_index(listener.region.key)["by_id"].get(item_id)
            if meta is not None:
                entry = {"id": item_id, "kind": kind, "title": meta.get("title"), "details": meta.get("text"),
                         "tags": meta.get("tags"), "source": meta.get("attribution")}
                starts = _parse_time(meta.get("starts_at"))
                if starts:
                    entry["when"] = _local_when(starts, listener.tz_name)
                published = _parse_time(meta.get("published_at"))
                if published:
                    entry["age"] = _age(published)
                if meta.get("area"):
                    entry["area"] = meta["area"]
                entry.update(_where_entry(listener, meta.get("where")))
                if meta.get("entities"):
                    entry["names"] = meta["entities"]
        if entry is None and kind == KIND_NEWS and services.news_service is not None and key.isdigit():
            items = await services.news_service.store.items([int(key)])
            item = items.get(int(key))
            if item is not None:
                article = item.as_article()
                entry = {"id": item_id, "kind": kind, "title": article.get("title"),
                         "details": article.get("summary") or article.get("description") or "",
                         "source": (article.get("source") or {}).get("name"),
                         "published": article.get("publishedAt"), "tags": article.get("tags"),
                         "people": article.get("people"), "category": article.get("category"),
                         "tone": article.get("tone"),
                         **_where_entry(listener, article.get("where"))}
        if entry is None and kind == KIND_ARTIST:
            found = await self.node("artists").search(PulseQuery(listener=listener, text=key, kinds={KIND_ARTIST}))
            if found:
                entry = {"id": item_id, "kind": kind, "title": found[0].title,
                         "details": services.web_service.cached_artist_biography(key) or found[0].text}
        if entry is None:
            return None
        related = await self.related(listener, item_id)
        if related:
            entry["related"] = [{k: v for k, v in link.items() if k != "kind"} for link in related]
        return entry

    async def listener_context(self, listener: PulseListener) -> dict:
        from services_radio.dj_bank_sources import compact_listener_notes
        location = listener.location
        context = {
            "local_time": _local_when(listener.now, listener.tz_name) if listener.tz_name else None,
            "city": listener.region.name if listener.region else (location.city or None),
            "neighbourhood": location.description or location.place or None,
            "top_genres": list(listener.taste.genres)[:6],
            "favorite_artists": sorted(listener.taste.artists)[:8],
            "interests": sorted(listener.taste.interests)[:6],
            "signed_in": bool(listener.user_id),
        }
        if listener.user is not None:
            notes = compact_listener_notes(listener.user)
            if notes:
                context["notes"] = notes
        context.update(await self._listening(listener))
        return {k: v for k, v in context.items() if v not in (None, "", [])}

    @staticmethod
    async def _listening(listener: PulseListener) -> dict:
        catalog = services.catalog_service
        if catalog is None:
            return {}

        def label(track_id: str) -> Optional[str]:
            track = catalog.get_track(track_id)
            return log_service.track_label(track) if track else None

        rated = await listener_plays.ratings(listener.user_id)
        plays = await listener_plays.track_plays(listener.user_id, listener.session_id)
        loved = await listener_plays.loved_tracks(listener.user_id, listener.session_id)
        most_played = sorted(plays.items(), key=lambda item: item[1].listens, reverse=True)
        return {
            "favorites": {"super_likes": sum(1 for r in rated.values() if r == "super_like"),
                          "likes": sum(1 for r in rated.values() if r == "like")} if rated else None,
            "most_loved": [{"track": label(item.track_id), **listener_plays.brief(item.rating, item.plays)}
                           for item in loved[:LISTENING_TOP] if label(item.track_id)],
            "most_played": [{"track": label(track_id), **listener_plays.brief(rated.get(track_id), stats)}
                            for track_id, stats in most_played[:LISTENING_TOP] if stats.listens and label(track_id)],
            "listening_note": LISTENING_NOTE if plays else None,
        }


def _where_entry(listener: PulseListener, data: Any) -> dict:
    where = geo.Where.from_dict(data)
    if where is None:
        return {}
    entry = {"where": where.label, "scope": where.scope}
    near = geo.relation(listener.where, where)
    if near:
        entry["near"] = near
    return entry


pulse: Optional[Pulse] = None


def install(instance: Optional[Pulse]) -> None:
    global pulse
    pulse = instance


def get_pulse() -> Optional[Pulse]:
    return pulse


def default_nodes() -> list[KnowledgeNode]:
    return [LocalNuggetsNode(), PlacesNode(), NewsNode(), MusicNode(), WeatherNode(), AreaNode(), ArtistNode(),
            CommunityNode(), ReviewsNode(), ChartsNode(), TrendsNode()]
