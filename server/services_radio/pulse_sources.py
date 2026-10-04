"""City Pulse sources: one KnowledgeNode per kind of thing the station knows (events, places, news, music, weather, air
and pollen, artists, listener shoutouts and reviews, charts, trends)."""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from config import settings
from database.connection import AsyncSessionLocal
from database.models import ArtistBiography
from service_registry import services
from services import log_service
from services_radio import area_signals, geo, local_knowledge, place_memory
from services_radio import regional_knowledge as regional_kb
from services_radio.news_store import normalize_query
from services_radio.pulse_items import (
    KIND_AREA, KIND_ARTIST, KIND_CHART, KIND_COMMUNITY, KIND_EVENT, KIND_NEWS, KIND_PLACE, KIND_REVIEW, KIND_TRACK,
    KIND_TREND, KIND_WEATHER, KnowledgeNode, PulseItem, PulseListener, PulseQuery, SHOUTOUT_BROWSE, _clip,
    _parse_time, _where_dict, from_meta, from_regional, taste_boost,
)
from services_radio.pulse_demand import charts, demand


class LocalNuggetsNode(KnowledgeNode):
    name = "local"
    kinds = (KIND_EVENT,)

    def _keep(self, q: PulseQuery):
        region_key = q.listener.region.key
        start, end = q.window()
        wanted = {kind for kind in self.kinds if q.wants(kind)}

        def keep(meta: Dict[str, Any]) -> bool:
            if meta.get("region_key") != region_key or meta.get("kind") not in wanted:
                return False
            if meta.get("kind") == KIND_EVENT:
                starts = _parse_time(meta.get("starts_at"))
                return starts is None or start <= starts <= end
            return True
        return keep

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        if q.listener.region is None:
            return []
        if not q.text:
            regional = regional_kb.get_regional_knowledge()
            if regional is None or not q.wants(KIND_EVENT):
                return []
            scored = await regional.query(q.listener.region, (regional_kb.KIND_EVENT,), q.listener.taste,
                                          window=q.window(), limit=q.per_kind * 3)
            return [from_regional(item, value) for value, item in scored]
        search = local_knowledge.local_search
        if search is None:
            return []
        results = await search.search(q.text, n=q.per_kind * 6, keep=self._keep(q),
                                      boost=lambda meta: taste_boost(meta, q.listener.taste), use_ai=q.use_ai)
        items: Dict[str, PulseItem] = {}
        for match in results:
            item = from_meta(match.meta, match.score)
            key = f"{item.kind}:{item.title.lower()}"
            other = items.get(key)
            if other is None or (item.when and other.when and item.when < other.when and item.score >= other.score - 0.02):
                items[key] = item
        return list(items.values())[:q.per_kind]

    def can_fetch(self, q: PulseQuery) -> bool:
        return services.events_service is not None and q.wants(KIND_EVENT) \
            and bool(q.listener.location.query_point() or q.listener.location.city)

    async def fetch(self, q: PulseQuery) -> list[PulseItem]:
        regional = regional_kb.get_regional_knowledge()
        location = q.listener.location
        start, end = q.window()
        events = await services.events_service.get_ticketmaster_events(
            location.query_point() or location.city, location.country_code or None, start, end, q.text or None)
        region = q.listener.region
        if not events or region is None:
            return []
        now = datetime.now(timezone.utc)
        items = [i for i in (regional_kb.TicketmasterEventsCollector.to_item(region, e, now) for e in events) if i]
        if regional is not None and items:
            await regional.ingest(region, items)
        found = [from_regional(item, 0.5) for item in items]
        for item in found:
            item.live = True
        return found


class PlacesNode(KnowledgeNode):
    name = "places"
    kinds = (KIND_PLACE,)

    @staticmethod
    def _item(place_id: str, title: str, kind: str, rating, price, where: Optional[geo.Where], score: float,
              website: Optional[str] = None, about: Optional[dict] = None) -> PulseItem:
        about = about or {}
        details = [kind or ""]
        if rating:
            details.append(f"rated {rating}")
        if price:
            details.append(price)
        details += (about.get("features") or [])[:5]
        text = ", ".join(d for d in details if d)
        if about.get("summary"):
            text = f"{text}. {about['summary']}"
        return PulseItem(id=f"place:{place_id}", kind=KIND_PLACE, title=title or "", text=text[:300],
                         source="Google Maps", score=score, where=where,
                         payload={"website": website, **{k: about[k] for k in (
                             "summary", "review_summary", "features", "suburb", "maps_url") if about.get(k)}},
                         entities=[title or ""])

    @classmethod
    def _from_result(cls, result: dict, score: float) -> PulseItem:
        return cls._item(result["place_id"], result.get("name"), result.get("type"), result.get("rating"),
                         result.get("price_level"),
                         geo.from_row(result.get("address") or result.get("name"), result.get("latitude"),
                                      result.get("longitude")), score, result.get("website"), result.get("details"))

    @classmethod
    def _from_meta(cls, meta: Dict[str, Any], score: float) -> PulseItem:
        return cls._item(meta.get("place_id") or "", meta.get("title"), meta.get("type"), meta.get("rating"),
                         meta.get("price_level"), geo.Where.from_dict(meta.get("where")), score, meta.get("website"),
                         meta.get("details") if isinstance(meta.get("details"), dict) else None)

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        search = local_knowledge.place_search
        if search is None:
            return []
        here = q.listener.where
        city_m = settings.PULSE_CITY_RADIUS_KM * 1000

        def keep(meta: Dict[str, Any]) -> bool:
            where = geo.Where.from_dict(meta.get("where"))
            return here is None or where is None or geo.gap_m(here, where) <= city_m

        results = await search.search(q.text, n=q.per_kind, keep=keep, use_ai=q.use_ai)
        return [self._from_meta(match.meta, match.score) for match in results]

    def can_fetch(self, q: PulseQuery) -> bool:
        return bool(q.text) and q.kinds is not None and KIND_PLACE in q.kinds \
            and q.listener.location.coords is not None and services.location_service is not None \
            and services.location_service.available()

    async def wants_fetch(self, q: PulseQuery, found: list[PulseItem]) -> bool:
        coords = q.listener.location.coords
        return not await place_memory.searched_near(q.text, coords[0], coords[1])

    async def fetch(self, q: PulseQuery) -> list[PulseItem]:
        results = await services.location_service.get_nearby_places(q.text, q.listener.location.coords,
                                                                     radius=settings.PULSE_PLACE_RADIUS_M,
                                                                     max_results=q.limit)
        items = [self._from_result(r, 0.7) for r in results or [] if r.get("place_id")]
        for item in items:
            item.live = True
        return items


def _news_text(source: str, tone: Optional[str]) -> str:
    return " · ".join(part for part in (source, tone if tone and tone != "neutral" else "") if part)


def _news_score(score: float, worth: Optional[float]) -> float:
    return score * (0.9 + 0.2 * (worth if worth is not None else 0.5))


class NewsNode(KnowledgeNode):
    name = "news"
    kinds = (KIND_NEWS,)

    @staticmethod
    def _items(articles: list[dict], score: float) -> list[PulseItem]:
        items = []
        for rank, article in enumerate(articles):
            published = _parse_time(article.get("publishedAt"))
            items.append(PulseItem(
                id=f"news:{article.get('id')}", kind=KIND_NEWS, title=article.get("title") or "",
                text=_news_text((article.get("source") or {}).get("name", ""), article.get("tone")),
                when=published, source="Google News", url=article.get("url") or "",
                score=_news_score(max(0.1, score - rank * 0.04), article.get("worth")),
                aired=bool(article.get("aired")),
                payload={"article_id": article.get("id")}, published=published,
                where=geo.Where.from_dict(article.get("where"))))
        return items

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        news = services.news_service
        if news is None or not news.store_enabled:
            return []
        country = (q.listener.location.country_code or settings.NEWS_DEFAULT_COUNTRY).upper()
        search = local_knowledge.news_search
        if not q.text or search is None:
            articles = await news.stored_articles(q.text, country, subject=q.listener.session_id, limit=q.limit)
            return self._items(articles, 0.5)
        here = q.listener.where if q.near_me else None
        radius = q.radius_m or settings.PULSE_NEAR_RADIUS_M
        oldest = (q.listener.now - timedelta(days=q.max_age_days or 7)).isoformat()

        def keep(meta: Dict[str, Any]) -> bool:
            if here is not None:
                if not geo.near(here, geo.Where.from_dict(meta.get("where")), radius):
                    return False
            elif (meta.get("country") or "").upper() != country:
                return False
            return not meta.get("published_at") or meta["published_at"] >= oldest

        results = await search.search(q.text, n=q.per_kind, keep=keep, use_ai=q.use_ai)
        items = []
        for match in results[:q.per_kind]:
            meta = match.meta
            published = _parse_time(meta.get("published_at"))
            items.append(PulseItem(
                id=meta["id"], kind=KIND_NEWS, title=meta.get("title") or "",
                text=_news_text(meta.get("source") or "", meta.get("tone")),
                when=published, source="Google News", url=meta.get("url") or "",
                score=_news_score(match.score, meta.get("worth")), published=published,
                where=geo.Where.from_dict(meta.get("where")),
                payload={"article_id": meta.get("article_id")}))
        return items

    def can_fetch(self, q: PulseQuery) -> bool:
        return bool(q.text) and services.news_service is not None

    async def fetch(self, q: PulseQuery) -> list[PulseItem]:
        country = q.listener.location.country_code or None
        articles, _ = await services.news_service.get_top_news(query=q.text, country=country,
                                                               subject=q.listener.session_id, top_n=q.limit)
        items = self._items(articles, 0.75)
        for item in items:
            item.live = True
        return items


class MusicNode(KnowledgeNode):
    name = "music"
    kinds = (KIND_TRACK, KIND_ARTIST)
    browsable = False

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        search = services.vector_search_service
        if not q.text or search is None:
            return []
        from services.listener_filters import excluded_ids, scope_ids
        banned = await excluded_ids(q.listener.user_id, q.listener.session_id)
        only = await scope_ids(q.listener.user_id, q.within)
        tracks = await search.search(q.text, n_results=q.per_kind, use_ai_analysis=q.use_ai,
                                     banned_ids=banned, only_ids=only)
        items, seen = [], set()
        for track in tracks:
            score = float(track.get("similarity_score") or 0.0)
            params = track.get("generation_params") or {}
            tags = track.get("derived_tags") or {}
            title = params.get("title") or "Untitled"
            artist = (log_service.track_artists(track) or [""])[0]
            if (title.lower(), artist.lower()) in seen:
                continue
            seen.add((title.lower(), artist.lower()))
            details = [tags.get("primary_genre") or ""]
            if tags.get("inspired_artist") and tags["inspired_artist"] != artist:
                details.append(f"in the style of {tags['inspired_artist']}")
            if only is not None and tags.get("vocal_style_keywords"):
                details.append("vocal style: " + ", ".join(tags["vocal_style_keywords"][:5]))
            items.append(PulseItem(
                id=f"track:{track.get('id')}", kind=KIND_TRACK, title=f"{title} by {artist}" if artist else title,
                text=", ".join(d for d in details if d),
                source="the listener's liked tracks" if only is not None else "PLAiR catalog", score=score,
                payload={"track_id": track.get("id"), "play_hint": "search_and_play with this track_id plays it"}))
        return items


class WeatherNode(KnowledgeNode):
    name = "weather"
    kinds = (KIND_WEATHER,)
    browsable = False

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        coords = q.listener.location.coords
        if coords is None or services.web_service is None:
            return []
        forecast = {"today": "today", "tonight": "today", "tomorrow": "tomorrow", "weekend": "week",
                    "week": "week", "month": "week"}.get(q.when or "", "current")
        report = await services.web_service.retrieve_weather_data(coords[0], coords[1], forecast)
        if not report:
            return []
        return [PulseItem(id=f"weather:{forecast}", kind=KIND_WEATHER, title=f"Weather ({forecast})",
                          text=_clip(report, 400), source="OpenWeatherMap", score=0.8, where=q.listener.where)]


class AreaNode(KnowledgeNode):
    name = "area"
    kinds = (KIND_AREA,)
    browsable = False

    def matches(self, q: PulseQuery) -> bool:
        if q.kinds is not None and KIND_WEATHER in q.kinds:
            return True
        return super().matches(q)

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        context = area_signals.location_context(q.listener.location, q.listener.tz_name,
                                                subject=q.listener.session_id)
        points = await area_signals.talking_points(context)
        return [PulseItem(id=f"area:{point.key}", kind=KIND_AREA, title=point.category or "area", text=point.text,
                          source=point.source, score=0.6, where=q.listener.where) for point in points]


class ArtistNode(KnowledgeNode):
    name = "artists"
    kinds = (KIND_ARTIST,)
    browsable = False

    @staticmethod
    def _item(name: str, biography: str, live: bool = False) -> PulseItem:
        return PulseItem(id=f"artist:{name.lower()}", kind=KIND_ARTIST, title=name, text=_clip(biography, 420),
                         source="MusicBrainz / Wikipedia", score=0.9, live=live)

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        if not q.text or services.web_service is None:
            return []
        cached = services.web_service.cached_artist_biography(q.text)
        if cached:
            return [self._item(q.text, cached)]
        from services_radio.external_web_service import _normalize_name
        async with AsyncSessionLocal() as db:
            row = await db.get(ArtistBiography, _normalize_name(q.text))
        if row is None or row.status != "found" or not row.biography or row.expires_at < datetime.now(timezone.utc):
            return []
        return [self._item(row.artist_name or q.text, row.biography)]

    def can_fetch(self, q: PulseQuery) -> bool:
        return bool(q.text) and q.kinds == {KIND_ARTIST} and services.web_service is not None

    async def fetch(self, q: PulseQuery) -> list[PulseItem]:
        biography = await services.web_service.retrieve_artist_biography(q.text)
        return [self._item(q.text, biography, live=True)] if biography else []


def shoutout_meta(shoutout: Dict[str, Any]) -> Dict[str, Any]:
    from services.user_content_database_service import coarse_location
    user_data = shoutout.get("user_data") or {}
    meta = shoutout.get("transcription_metadata") or shoutout.get("metadata") or {}
    address = user_data.get("location") or ""
    parts = [p.strip() for p in address.split(",") if p.strip() and not any(ch.isdigit() for ch in p)]
    shoutout_id = str(shoutout.get("id") or "")
    where = geo.Where.from_dict(meta.get("where")) or geo.resolver.cached(shoutout_place(shoutout))
    replies = shoutout.get("reply_count") or len((getattr(services.user_content_service, "children", None) or {}).get(shoutout_id, ()))
    return {
        "id": f"community:shoutouts:{shoutout_id}", "shoutout_id": shoutout_id,
        "title": f"{'Reply' if shoutout.get('parent_id') else 'Shoutout'} from {user_data.get('username') or 'a listener'}"
                 + (f" ({replies} {'reply' if replies == 1 else 'replies'})" if replies else ""),
        "text": " ".join((shoutout.get("transcription") or shoutout.get("full_transcription") or "").split()),
        "tags": [t for t in [meta.get("category"), *(meta.get("tags") or [])] if t],
        "area": ", ".join(dict.fromkeys(parts[1:3] if len(parts) > 2 else parts[:2])) or coarse_location(address) or "",
        "published_at": shoutout.get("timestamp"),
        "audio": "" if shoutout.get("text_only") or shoutout.get("has_audio") is False else shoutout.get("audio_url") or (
            f"/api/user_content/shoutouts/audio/{shoutout_id.replace('_', '/', 1)}.mp3" if "_" in shoutout_id else ""),
        "where": _where_dict(where),
    }


async def _top_reply_entry(listener: PulseListener, shoutout_id: str) -> dict:
    from services_radio import community_on_air
    store = services.user_content_service
    replies = store.get_replies(shoutout_id) if store is not None else []
    if not replies:
        return {}
    from services.community_engagement import community_engagement
    top = await community_engagement.top_reply(replies, listener.user_id)
    if not top:
        return {"replies": len(replies)}
    def entry(item):
        reply = {"from": community_on_air.speaker(item), "said": community_on_air.text_of(item)}
        marker = community_on_air.audio_marker(item)
        if marker:
            reply["audio"] = marker
        return reply

    allowed = await community_engagement.airable([r.get("id") for r in replies], listener.user_id)
    others = [r for r in await community_engagement.rank(list(replies))
              if r.get("id") != top.get("id") and r.get("id") in allowed][:settings.PULSE_TOOL_MAX_PER_KIND]
    result = {"replies": len(replies), "top_reply": entry(top),
              "how_to_play_reply": "Play or read the top reply straight after the shoutout, introduced as a reply."}
    if others:
        result["more_replies"] = [entry(r) for r in others]
    return result


def shoutout_place(shoutout: Dict[str, Any]) -> str:
    from services.user_content_database_service import coarse_location
    meta = shoutout.get("transcription_metadata") or shoutout.get("metadata") or {}
    return meta.get("about_place") or coarse_location((shoutout.get("user_data") or {}).get("location")) or ""


async def place_shoutouts() -> int:
    placed = 0
    for shoutout in list((getattr(services.user_content_service, "shoutouts", None) or {}).values()):
        meta = shoutout.get("transcription_metadata") or shoutout.get("metadata") or {}
        phrase = shoutout_place(shoutout)
        if not meta.get("where") and phrase and await geo.resolver.resolve(phrase):
            placed += 1
    return placed


def shoutout_item(shoutout: Dict[str, Any], score: float, listener: PulseListener) -> PulseItem:
    meta = shoutout_meta(shoutout)
    return PulseItem(
        id=meta["id"], kind=KIND_COMMUNITY, title=meta["title"], text=f'"{_clip(meta["text"], 160)}"',
        source="PLAiR listeners", score=score, published=_parse_time(meta["published_at"]), area=meta["area"],
        where=geo.Where.from_dict(meta["where"]),
        payload={"audio_path": meta["audio"], "shoutout_id": meta["shoutout_id"]})


def shoutout_in_region(shoutout: Dict[str, Any], listener: PulseListener) -> bool:
    distance = shoutout.get("distance_km")
    if isinstance(distance, (int, float)):
        return distance <= settings.PULSE_COMMUNITY_RADIUS_KM
    region = listener.region
    location = ((shoutout.get("user_data") or {}).get("location") or "").lower()
    return bool(region and region.name.lower() in location)


def _own_posts(q: PulseQuery, kind: str) -> list:
    store = services.user_content_service
    if store is None or not q.listener.user_id:
        return []
    mine = store.items_by_user(int(q.listener.user_id))
    posts = mine.get("review", []) if kind == KIND_REVIEW else mine.get("shoutout", []) + mine.get("reply", [])
    return sorted(posts, key=lambda post: str(post.get("timestamp") or ""), reverse=True)[:q.per_kind]


class CommunityNode(KnowledgeNode):
    name = "community"
    kinds = (KIND_COMMUNITY,)

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        search = services.user_content_vector_search_service
        if search is None:
            return []
        from services_radio import community_on_air
        if q.mine:
            return [shoutout_item(post, 1.0, q.listener) for post in _own_posts(q, KIND_COMMUNITY)]
        results = await community_on_air.pick(
            search, services.user_content_service, query=q.text or SHOUTOUT_BROWSE, n=q.per_kind * 4,
            user_id=q.listener.user_id, session_id=q.listener.session_id,
            user_location=q.listener.location.coords, fresh_only=False)
        items = []
        for shoutout in results or []:
            if shoutout_in_region(shoutout, q.listener):
                items.append(shoutout_item(shoutout, float(shoutout.get("final_score") or 0.0), q.listener))
        return items[:q.per_kind]


def review_item(review: Dict[str, Any], score: float) -> PulseItem:
    from services_radio import community_on_air
    track = review.get("track") or {}
    song = f"'{track.get('title')}'" + (f" by {track.get('artist')}" if track.get("artist") else "")
    audio = community_on_air.audio_marker(review) or ""
    return PulseItem(
        id=f"{KIND_REVIEW}:{review.get('id')}", kind=KIND_REVIEW,
        title=f"Review of {song} from {community_on_air.speaker(review)}",
        text=f'"{_clip(community_on_air.text_of(review), 160)}"', source="PLAiR listeners", score=score,
        published=_parse_time(review.get("timestamp")),
        payload={"audio_path": audio.strip("$"), "shoutout_id": review.get("id"), "track_id": track.get("id")})


class ReviewsNode(KnowledgeNode):
    name = "reviews"
    kinds = (KIND_REVIEW,)

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        from services_radio import community_on_air
        if q.mine:
            return [review_item(review, 1.0) for review in _own_posts(q, KIND_REVIEW)]
        results = await community_on_air.pick(
            services.user_content_vector_search_service, services.user_content_service,
            query=q.text or "what listeners think of songs", n=q.per_kind, user_id=q.listener.user_id,
            session_id=q.listener.session_id, kinds="review", fresh_only=False, track_id=q.track_id)
        return [review_item(review, float(review.get("final_score") or 0.0)) for review in results]


class ChartsNode(KnowledgeNode):
    name = "charts"
    kinds = (KIND_CHART,)

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        region = q.listener.region
        catalog = services.catalog_service
        if region is None or catalog is None:
            return []
        return await charts.region_chart(region, catalog, q)


class TrendsNode(KnowledgeNode):
    name = "trends"
    kinds = (KIND_TREND,)

    async def search(self, q: PulseQuery) -> list[PulseItem]:
        region = q.listener.region
        if region is None:
            return []
        topics = await demand.hot(region.key, days=7, min_askers=settings.PULSE_TREND_MIN_ASKERS, limit=q.per_kind * 3)
        if q.text and topics:
            matched = {match.meta.get("topic") for match in await demand.search(q.listener, q.text, days=7, limit=10)}
            topics = [topic for topic in topics if topic["topics"] & matched] or []
        items = []
        for topic in topics[:q.per_kind]:
            text = f"{topic['askers']} listeners asked this week"
            if topic["top_answers"]:
                text += f"; what kept coming up: {', '.join(topic['top_answers'])}"
            items.append(PulseItem(id=f"trend:{topic['node']}:{normalize_query(topic['label'])}", kind=KIND_TREND,
                                   title=f"People in {region.name} have been asking about {topic['label']} "
                                         f"({topic['node']})",
                                   text=text, source="PLAiR listeners", score=min(1.0, 0.3 + topic["askers"] / 10),
                                   area=topic.get("top_area") or ""))
        return items
