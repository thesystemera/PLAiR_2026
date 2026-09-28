import asyncio
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Awaitable, Callable, Optional
from urllib.parse import quote, quote_plus

import numpy as np
from babel import Locale

from config import settings
from services import log_service
from services.http_client import fetch
from services import usage_tracking
from services.llm_router import LLM_BACKGROUND
from services.task_utils import spawn
from services_radio.news_store import (KIND_GEO, KIND_SEARCH, KIND_TOP, KIND_TOPIC, NewsStore, StoredPull, cosine,
                                       covers, lexical_similarity, normalize_query, search_terms)

GOOGLE_NEWS_RSS = "https://news.google.com/rss"
TOPICS = ["WORLD", "NATION", "BUSINESS", "TECHNOLOGY", "ENTERTAINMENT", "SPORTS", "SCIENCE", "HEALTH"]
CACHE_TTL_SECONDS = 20 * 60
CACHE_MAX_ENTRIES = 200
FEED_ITEMS = 30
RANK_FEATURE = "NewsService.get_top_news"
PRUNE_INTERVAL_S = 600
PREFETCH_GAP_S = 1.0
LOCAL_ITEMS = 15

_TOPIC_ALIASES = {
    "world": "WORLD", "international": "WORLD", "national": "NATION", "nation": "NATION",
    "business": "BUSINESS", "economy": "BUSINESS", "technology": "TECHNOLOGY", "tech": "TECHNOLOGY",
    "entertainment": "ENTERTAINMENT", "sport": "SPORTS", "science": "SCIENCE", "health": "HEALTH",
}
_TOPIC_TAGS = {
    "WORLD": ["world", "international"], "NATION": ["national"], "BUSINESS": ["business", "economy"],
    "TECHNOLOGY": ["technology", "tech"], "ENTERTAINMENT": ["entertainment"], "SPORTS": ["sport"],
    "SCIENCE": ["science"], "HEALTH": ["health"],
}

_COUNTRY_ALIASES = {"usa": "US", "united states of america": "US", "uk": "GB", "england": "GB",
                    "scotland": "GB", "wales": "GB", "aotearoa": "NZ"}
_TERRITORIES = dict(Locale("en").territories)
_COUNTRY_BY_NAME = {name.lower(): code for code, name in _TERRITORIES.items() if code.isalpha()}


def resolve_country(location: Optional[str]) -> Optional[str]:
    if not location:
        return None
    candidate = location.strip()
    if len(candidate) == 2 and candidate.isalpha():
        return candidate.upper()
    name = candidate.split(",")[-1].strip().lower()
    return _COUNTRY_ALIASES.get(name) or _COUNTRY_BY_NAME.get(name)


def resolve_city(location: Optional[str]) -> Optional[str]:
    if not location:
        return None
    parts = [p.strip() for p in location.split(",") if p.strip()]
    if len(parts) >= 4:
        return parts[-3]
    return parts[0] if parts else None


def country_name(code: str) -> str:
    return _TERRITORIES.get(code, "") if code else ""


class NewsService:
    def __init__(self, ai_service, store: Optional[NewsStore] = None,
                 embedder: Optional[Callable[[str], Awaitable[np.ndarray]]] = None):
        self.ai_service = ai_service
        self.store = store
        self.embedder = embedder
        self._cache: dict[tuple, tuple[float, list, str]] = {}
        self._locks: dict[tuple, asyncio.Lock] = {}
        self._embedding: set[int] = set()
        self._last_prune = 0.0
        self._embed_failed_at = 0.0
        log_service.external("News Service initialized (Google News RSS"
                             + (", persistent semantic store)" if self.store_enabled else ")"))

    @property
    def store_enabled(self) -> bool:
        return self.store is not None and settings.NEWS_STORE_ENABLED

    @staticmethod
    def _edition(country: str) -> str:
        return f"hl=en-{country}&gl={country}&ceid={country}:en"

    @staticmethod
    def _feed_url(query: str, is_topic: bool, country: str, period: str) -> str:
        edition = NewsService._edition(country)
        if is_topic and query.upper() in TOPICS:
            return f"{GOOGLE_NEWS_RSS}/headlines/section/topic/{query.upper()}?{edition}"
        if not query:
            return f"{GOOGLE_NEWS_RSS}?{edition}"
        return f"{GOOGLE_NEWS_RSS}/search?q={quote_plus(f'{query} when:{period}')}&{edition}"

    @staticmethod
    def _geo_url(place: str, country: str) -> str:
        return f"{GOOGLE_NEWS_RSS}/headlines/section/geo/{quote(place.strip(), safe='')}?{NewsService._edition(country)}"

    @staticmethod
    def _parse_feed(xml_text: str, limit: Optional[int] = None) -> list[dict]:
        articles = []
        root = ET.fromstring(xml_text)
        for item in root.iter("item"):
            if limit is not None and len(articles) >= limit:
                break
            source_el = item.find("source")
            source = (source_el.text or "").strip() if source_el is not None else ""
            title = (item.findtext("title") or "").strip()
            if source and title.endswith(f" - {source}"):
                title = title[: -len(source) - 3]
            description = html.unescape(re.sub(r"<[^>]+>", " ", item.findtext("description") or ""))
            description = re.sub(r"\s+", " ", description).strip()
            if description.startswith(title):
                description = ""
            published = item.findtext("pubDate") or ""
            try:
                published = parsedate_to_datetime(published).strftime("%Y-%m-%dT%H:%M:%SZ")
            except (TypeError, ValueError):
                pass
            articles.append({
                "source": {"id": None, "name": source},
                "title": title,
                "description": description,
                "url": item.findtext("link") or "",
                "publishedAt": published,
            })
        return articles

    async def _fetch_url(self, url: str, label: str) -> list[dict]:
        try:
            response = await fetch("GET", url, circuit=True)
            response.raise_for_status()
        except Exception:
            usage_tracking.record_api_call("news", "google_news_rss", error=True)
            raise
        usage_tracking.record_api_call("news", "google_news_rss")
        articles = await asyncio.to_thread(self._parse_feed, response.text, FEED_ITEMS)
        log_service.external(f"News: {len(articles)} items for {label}")
        return articles

    async def fetch_news(self, query: str, is_topic: bool = False, country: str = "US", period: str = "7d") -> list[dict]:
        return await self._fetch_url(self._feed_url(query, is_topic, country, period),
                                     f"'{query}' ({'topic' if is_topic else 'search'}, {country})")

    @staticmethod
    def _rank_prompt(listing: str, query: str, top_n: int, with_tags: bool) -> str:
        prompt = (
            f"Query or category: {query}\n\nHeadlines:\n{listing}\n\n"
            f"Pick the {top_n} best headlines for a radio news bulletin: most relevant to the query, most significant, "
            "credible sources, recent, and no two covering the same story. "
        )
        if with_tags:
            return prompt + (
                "For each pick also give 2-5 short lowercase topic tags a listener might use to ask about it, from "
                "specific to general (people, teams, places, subject, e.g. [\"all blacks\", \"rugby\", \"sport\"]). "
                "Respond with ONLY JSON: {\"picks\": [3, 0, 7], \"tags\": {\"3\": [\"...\"], \"0\": [\"...\"]}}"
            )
        return prompt + "Respond with ONLY a JSON array of the chosen numbers, best first, e.g. [3, 0, 7]."

    @staticmethod
    def _parse_rank(text: str, count: int) -> tuple[list[int], dict]:
        text = text or ""
        picks, tags = [], {}
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(text[start:end + 1])
                picks = [int(i) for i in data.get("picks") or [] if isinstance(i, (int, float, str)) and str(i).isdigit()]
                for key, values in (data.get("tags") or {}).items():
                    if str(key).isdigit() and isinstance(values, list):
                        tags[int(key)] = [str(v).strip().lower()[:40] for v in values if str(v).strip()][:5]
            except (ValueError, AttributeError, TypeError):
                picks = []
        if not picks:
            match = re.search(r"\[[\d,\s]*\]", text)
            picks = json.loads(match.group(0)) if match else []
        picks = [i for i in dict.fromkeys(picks) if 0 <= i < count]
        return picks, {i: t for i, t in tags.items() if 0 <= i < count and t}

    async def _rank_llm(self, articles: list[dict], query: str, top_n: int, with_tags: bool) -> tuple[list[int], dict]:
        listing = "\n".join(
            f"{i}. {a['title']} | {a['source']['name']} | {a['publishedAt']}" for i, a in enumerate(articles)
        )
        with usage_tracking.feature_scope(RANK_FEATURE):
            response = await self.ai_service.call_gemini(
                prompt=self._rank_prompt(listing, query, top_n, with_tags),
                system_instruction="You are an experienced radio news editor.",
                model=settings.GEMINI_COMMAND_MODEL,
                temperature=0,
                max_tokens=700 if with_tags else 100,
                role=LLM_BACKGROUND,
                validate=lambda text: bool(re.search(r"\[[\d,\s]*\]", text or "")),
            )
        return self._parse_rank(response or "", len(articles))

    async def _rank(self, articles: list[dict], query: str, top_n: int) -> list[dict]:
        if len(articles) <= 1:
            return articles
        try:
            picks, _ = await self._rank_llm(articles, query, top_n, False)
            ranked = [articles[i] for i in picks]
            if ranked:
                return ranked[:top_n]
        except Exception as e:
            log_service.warning(f"News: ranking failed, using feed order: {e}")
        return articles[:top_n]

    @staticmethod
    def generate_final_report(articles: list[dict]) -> str:
        if not articles:
            return "No news articles found."
        lines = []
        for i, article in enumerate(articles, 1):
            lines.append(f"{i}. {article['title']}")
            lines.append(f"   Source: {article['source']['name']} | Published: {article['publishedAt']}")
            if article["description"]:
                lines.append(f"   Summary: {article['description'][:400]}")
            if article.get("aired"):
                lines.append("   (Already covered for this listener earlier: only mention it as a quick follow-up)")
        return "\n".join(lines)

    @staticmethod
    def classify(query: Optional[str], is_topic: bool = False, geo: Optional[str] = None) -> tuple[str, str, str]:
        if geo and geo.strip():
            return KIND_GEO, normalize_query(geo) or geo.strip().lower(), geo.strip()
        raw = (query or "").strip()
        if is_topic and raw.upper() in TOPICS:
            return KIND_TOPIC, raw.upper(), raw.upper()
        norm = normalize_query(raw)
        if not norm:
            return KIND_TOP, "", ""
        if norm in _TOPIC_ALIASES:
            topic = _TOPIC_ALIASES[norm]
            return KIND_TOPIC, topic, topic
        return KIND_SEARCH, norm, search_terms(raw) or norm

    @staticmethod
    def window_s(kind: str) -> int:
        if kind == KIND_TOP:
            return settings.NEWS_TOP_FRESH_S
        if kind == KIND_GEO:
            return settings.NEWS_GEO_FRESH_S
        return settings.NEWS_TOPIC_FRESH_S

    @staticmethod
    def _pull_tags(kind: str, query_norm: str, fetch_query: str, country: str) -> list[str]:
        if kind == KIND_TOP:
            return ["top stories", country_name(country).lower()]
        if kind == KIND_TOPIC:
            tags = list(_TOPIC_TAGS.get(query_norm, [query_norm.lower()]))
            if query_norm == "NATION":
                tags.append(country_name(country).lower())
            return tags
        if kind == KIND_GEO:
            return [fetch_query.lower(), "local"]
        return [query_norm]

    @staticmethod
    def _label(kind: str, fetch_query: str, country: str) -> str:
        if kind == KIND_TOP:
            return f"top stories ({country})"
        if kind == KIND_TOPIC:
            return f"{fetch_query.lower()} news ({country})"
        if kind == KIND_GEO:
            return f"local news for {fetch_query} ({country})"
        return fetch_query

    def _lock(self, key: tuple) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def _trim_locks(self) -> None:
        if len(self._locks) > 500:
            self._locks = {k: lock for k, lock in self._locks.items() if lock.locked()}

    async def _embed(self, text: str) -> Optional[np.ndarray]:
        if self.embedder is None or not text or time.monotonic() - self._embed_failed_at < 60:
            return None
        try:
            vector = np.asarray(await self.embedder(text), dtype=np.float32)
        except Exception as e:
            self._embed_failed_at = time.monotonic()
            log_service.warning(f"News: embedding unavailable: {type(e).__name__}: {e}")
            return None
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm > 0 else None

    async def _embed_pending(self, ids: list[int], pull_id: Optional[int] = None, pull_text: str = "") -> None:
        if self.embedder is None:
            return
        todo = [i for i in ids if i not in self._embedding]
        self._embedding.update(todo)
        try:
            vectors = {}
            for item_id, text in await self.store.missing_embeddings(todo):
                vector = await self._embed(text)
                if vector is None:
                    break
                vectors[item_id] = vector
            await self.store.set_embeddings(vectors)
            if pull_id and pull_text:
                vector = await self._embed(pull_text)
                if vector is not None:
                    await self.store.set_pull_embedding(pull_id, vector)
        except Exception as e:
            log_service.warning(f"News: background embedding failed: {type(e).__name__}: {e}")
        finally:
            self._embedding.difference_update(todo)

    async def _maybe_prune(self) -> None:
        if time.monotonic() - self._last_prune < PRUNE_INTERVAL_S:
            return
        self._last_prune = time.monotonic()
        try:
            await self.store.prune()
        except Exception as e:
            log_service.warning(f"News: prune failed: {type(e).__name__}: {e}")

    async def _fetch_pull(self, kind: str, query_norm: str, fetch_query: str, country: str, period: str,
                          region_key: Optional[str], ask_vector: Optional[np.ndarray]) -> StoredPull:
        if kind == KIND_GEO:
            url = self._geo_url(fetch_query, country)
        else:
            url = self._feed_url(fetch_query, kind == KIND_TOPIC, country, period or "7d")
        articles = await self._fetch_url(url, self._label(kind, fetch_query, country))
        ids = await self.store.save_items(articles, country, region_key,
                                          self._pull_tags(kind, query_norm, fetch_query, country))
        pull = await self.store.add_pull(kind, country, region_key, fetch_query, query_norm, period, ids,
                                         ask_vector, "ok" if ids else "empty")
        pull_text = "" if ask_vector is not None or kind == KIND_TOP else fetch_query
        spawn(self._embed_pending(ids, pull.id, pull_text), name="news_embed")
        spawn(self._maybe_prune(), name="news_prune")
        return pull

    async def _ranked(self, pull: StoredPull) -> StoredPull:
        if pull.ranked_ids is not None or len(pull.item_ids) <= 1:
            return pull
        async with self._lock(("rank", pull.id)):
            fresh = await self.store.get_pull(pull.id)
            if fresh is None or fresh.ranked_ids is not None:
                return fresh or pull
            pull = fresh
            previous = await self.store.previous_ranked_pull(pull)
            if previous is not None:
                prior = set(previous.item_ids)
                kept = [i for i in previous.ranked_ids if i in set(pull.item_ids)]
                if set(pull.item_ids[:settings.NEWS_RANK_REUSE_TOP_K]) <= prior and \
                        len(kept) >= max(1, len(previous.ranked_ids) - 2):
                    await self.store.set_ranking(pull.id, kept, "reused")
                    usage_tracking.record_cache_hit("news_rank", feature=RANK_FEATURE)
                    log_service.external(f"News: ranking reused for {self._label(pull.kind, pull.query, pull.country)}")
                    pull.ranked_ids, pull.rank_source = kept, "reused"
                    return pull
            items = await self.store.items(pull.item_ids)
            ordered = [items[i] for i in pull.item_ids if i in items]
            articles = [item.as_article() for item in ordered]
            ranked, source = [], "feed"
            try:
                with usage_tracking.system_scope("news"):
                    picks, tags = await self._rank_llm(articles, self._label(pull.kind, pull.query, pull.country),
                                                       settings.NEWS_RANK_TOP_N, settings.NEWS_TAGS_ENABLED)
                ranked = [ordered[i].id for i in picks][:settings.NEWS_RANK_TOP_N]
                if ranked:
                    source = "llm"
                    await self.store.add_tags({ordered[i].id: t for i, t in tags.items()})
            except Exception as e:
                log_service.warning(f"News: ranking failed, using feed order: {type(e).__name__}: {e}")
            if not ranked:
                ranked = [item.id for item in ordered[:settings.NEWS_RANK_TOP_N]]
            await self.store.set_ranking(pull.id, ranked, source)
            pull.ranked_ids, pull.rank_source = ranked, source
            return pull

    async def _serve(self, pull: StoredPull, cached: bool, prefer: Optional[set] = None) -> list[dict]:
        was_ranked = pull.ranked_ids is not None
        pull = await self._ranked(pull)
        order = pull.order
        if prefer:
            order = [i for i in order if i in prefer] + [i for i in order if i not in prefer]
        items = await self.store.items(order)
        if cached:
            usage_tracking.record_api_call("news", "news_store", cached=True)
            if was_ranked and pull.rank_source in ("llm", "reused"):
                usage_tracking.record_cache_hit("news_rank", feature=RANK_FEATURE)
        return [items[i].as_article() for i in order if i in items]

    async def _get_pull(self, kind: str, query_norm: str, fetch_query: str, country: str, period: str,
                        region_key: Optional[str] = None,
                        ask_vector: Optional[np.ndarray] = None) -> tuple[Optional[StoredPull], bool]:
        since = datetime.now(timezone.utc) - timedelta(seconds=self.window_s(kind))
        pull = await self.store.latest_pull(kind, country, query_norm, period, since)
        if pull is not None:
            return pull, True
        async with self._lock(("pull", kind, country, query_norm, period)):
            pull = await self.store.latest_pull(kind, country, query_norm, period, since)
            if pull is not None:
                return pull, True
            try:
                return await self._fetch_pull(kind, query_norm, fetch_query, country, period, region_key,
                                              ask_vector), False
            except Exception as e:
                log_service.error(f"News: fetch failed for {self._label(kind, fetch_query, country)}: "
                                  f"{type(e).__name__}: {e}")
                stale_since = datetime.now(timezone.utc) - timedelta(seconds=settings.NEWS_RETENTION_S)
                return await self.store.latest_pull(kind, country, query_norm, period, stale_since), True
            finally:
                self._trim_locks()

    async def _semantic_match(self, query_norm: str, country: str, period: str,
                              ask_vector: Optional[np.ndarray]) -> Optional[list[dict]]:
        now = datetime.now(timezone.utc)
        pulls = await self.store.recent_pulls(country, now - timedelta(seconds=settings.NEWS_TOPIC_FRESH_S))
        if not pulls:
            return None
        ask = set(query_norm.split())
        items = await self.store.items({i for pull in pulls for i in pull.item_ids})
        oldest = now - _period_delta(period)
        relevant = {item_id for item_id, item in items.items()
                    if covers(ask, item.token_set) and (item.published_at is None or item.published_at >= oldest)}
        best = None
        for pull in pulls:
            similarity = max(lexical_similarity(query_norm, pull.query_norm.lower()), cosine(ask_vector, pull.embedding))
            if similarity < settings.NEWS_REUSE_SIMILARITY:
                continue
            count = sum(1 for i in pull.item_ids if i in relevant)
            if count >= settings.NEWS_REUSE_MIN_ITEMS and (best is None or (count, similarity) > best[:2]):
                best = (count, similarity, pull)
        if best is not None:
            log_service.external(f"News: '{query_norm}' ({country}) reused pull '{best[2].query or best[2].kind}' "
                                 f"(similarity {best[1]:.2f}, {best[0]} matching items)")
            return await self._serve(best[2], cached=True, prefer=relevant)
        if len(relevant) < settings.NEWS_REUSE_MIN_ITEMS:
            return None
        ranked_positions = {}
        for pull in pulls:
            for position, item_id in enumerate(pull.ranked_ids or []):
                ranked_positions[item_id] = min(ranked_positions.get(item_id, 99), position)
        epoch = datetime.min.replace(tzinfo=timezone.utc)
        chosen = sorted(relevant, key=lambda i: (ranked_positions.get(i, 99),
                                                 -(items[i].published_at or epoch).timestamp()))
        usage_tracking.record_api_call("news", "news_store", cached=True)
        log_service.external(f"News: '{query_norm}' ({country}) answered from {len(chosen)} stored items")
        return [items[i].as_article() for i in chosen]

    async def _stored_news(self, kind: str, query_norm: str, fetch_query: str, country: str, period: str,
                           region_key: Optional[str]) -> list[dict]:
        if kind == KIND_SEARCH:
            since = datetime.now(timezone.utc) - timedelta(seconds=self.window_s(kind))
            exact = await self.store.latest_pull(kind, country, query_norm, period, since)
            if exact is not None:
                return await self._serve(exact, cached=True)
            ask_vector = await self._embed(query_norm)
            reused = await self._semantic_match(query_norm, country, period, ask_vector)
            if reused is not None:
                return reused
            pull, cached = await self._get_pull(kind, query_norm, fetch_query, country, period, region_key, ask_vector)
        else:
            pull, cached = await self._get_pull(kind, query_norm, fetch_query, country, period, region_key)
        if pull is None:
            return []
        return await self._serve(pull, cached=cached)

    async def _flag_aired(self, subject: str, articles: list[dict]) -> list[dict]:
        since = datetime.now(timezone.utc) - timedelta(seconds=settings.NEWS_AIRED_TTL_S)
        aired = await self.store.aired(subject, since)
        if not aired:
            return articles
        vectors = [v for v in aired.values() if v is not None]
        candidates = await self.store.items([a["id"] for a in articles if a.get("id") not in aired],
                                            with_embeddings=bool(vectors))
        for article in articles:
            item = candidates.get(article.get("id"))
            same_story = item is not None and item.embedding is not None and any(
                cosine(item.embedding, v) >= settings.NEWS_SAME_STORY_SIMILARITY for v in vectors)
            article["aired"] = article.get("id") in aired or same_story
        return [a for a in articles if not a["aired"]] + [a for a in articles if a["aired"]]

    async def get_top_news(self, query: str, is_topic: bool = False, country: Optional[str] = None,
                           period: str = "7d", top_n: int = 8, subject: Optional[str] = None,
                           geo: Optional[str] = None, region_key: Optional[str] = None) -> tuple[list[dict], str]:
        country = (country or settings.NEWS_DEFAULT_COUNTRY).upper()
        if not self.store_enabled:
            return await self._memory_top_news((query or "").strip(), is_topic, country, period, top_n)
        kind, query_norm, fetch_query = self.classify(query, is_topic, geo)
        try:
            articles = await self._stored_news(kind, query_norm, fetch_query, country,
                                               period if kind == KIND_SEARCH else "", region_key)
            if subject and articles:
                articles = await self._flag_aired(str(subject), articles)
        except Exception as e:
            log_service.error(f"News: store unavailable ({type(e).__name__}: {e}), fetching directly")
            return await self._memory_top_news(fetch_query, kind == KIND_TOPIC, country, period, top_n)
        articles = articles[:top_n]
        if not articles:
            return [], ""
        return articles, self.generate_final_report(articles)

    async def get_local_news(self, place: str, country: Optional[str] = None, top_n: int = 8,
                             subject: Optional[str] = None, region_key: Optional[str] = None) -> tuple[list[dict], str]:
        if not settings.NEWS_GEO_ENABLED:
            return await self.get_top_news(place, country=country, top_n=top_n, subject=subject)
        return await self.get_top_news("", country=country, top_n=top_n, subject=subject, geo=place,
                                       region_key=region_key)

    async def mark_aired(self, subject: Optional[str], articles) -> None:
        if not self.store_enabled or not subject:
            return
        ids = [a["id"] if isinstance(a, dict) else a for a in articles or ()]
        ids = [i for i in ids if isinstance(i, int)]
        if not ids:
            return
        try:
            await self.store.mark_aired(str(subject), ids)
        except Exception as e:
            log_service.warning(f"News: could not record aired stories: {type(e).__name__}: {e}")

    def airing_marker(self, subject: Optional[str], articles) -> Callable[[], None]:
        snapshot = list(articles or ())

        def mark():
            spawn(self.mark_aired(subject, snapshot), name="news_mark_aired")
        return mark

    async def refresh_region(self, country: Optional[str], place: Optional[str],
                             region_key: Optional[str] = None) -> list[dict]:
        if not self.store_enabled:
            return []
        country = (country or settings.NEWS_DEFAULT_COUNTRY).upper()
        plan = [(KIND_TOP, "", "")] + [(KIND_TOPIC, t, t) for t in settings.NEWS_PREFETCH_TOPICS if t in TOPICS]
        for kind, query_norm, fetch_query in plan:
            _, cached = await self._get_pull(kind, query_norm, fetch_query, country, "")
            if not cached:
                await asyncio.sleep(PREFETCH_GAP_S)
        if not place or not settings.NEWS_GEO_ENABLED:
            return []
        kind, query_norm, fetch_query = self.classify("", geo=place)
        pull, _ = await self._get_pull(kind, query_norm, fetch_query, country, "", region_key)
        if pull is None:
            return []
        items = await self.store.items(pull.item_ids[:LOCAL_ITEMS])
        return [items[i].as_article() for i in pull.item_ids[:LOCAL_ITEMS] if i in items]

    async def _memory_top_news(self, query: str, is_topic: bool, country: str, period: str,
                               top_n: int) -> tuple[list[dict], str]:
        key = (query.lower(), is_topic, country, period, top_n)

        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
            return cached[1], cached[2]

        async with self._locks.setdefault(key, asyncio.Lock()):
            cached = self._cache.get(key)
            if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
                return cached[1], cached[2]
            try:
                articles = await self.fetch_news(query, is_topic, country, period)
            except Exception as e:
                log_service.error(f"News: fetch failed for '{query}' ({country}): {e}")
                return [], ""
            with usage_tracking.system_scope("news"):
                top_articles = await self._rank(articles, query or "top stories", top_n)
            report = self.generate_final_report(top_articles)
            self._cache[key] = (time.monotonic(), top_articles, report)
            for stale_key in [k for k, v in self._cache.items() if time.monotonic() - v[0] >= CACHE_TTL_SECONDS]:
                self._cache.pop(stale_key, None)
            while len(self._cache) > CACHE_MAX_ENTRIES:
                self._cache.pop(next(iter(self._cache)))
            self._locks = {k: lock for k, lock in self._locks.items() if lock.locked() or k in self._cache}
            return top_articles, report


def _period_delta(period: str) -> timedelta:
    match = re.fullmatch(r"(\d+)([hdm])", (period or "").strip().lower())
    if not match:
        return timedelta(days=7)
    value, unit = int(match.group(1)), match.group(2)
    return timedelta(hours=value) if unit == "h" else timedelta(days=value * (30 if unit == "m" else 1))

