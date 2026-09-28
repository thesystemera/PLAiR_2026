import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import numpy as np
from sqlalchemy import and_, bindparam, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import defer

from config import settings
from database.models import NewsAired, NewsItem, NewsPull

KIND_TOP = "top"
KIND_TOPIC = "topic"
KIND_GEO = "geo"
KIND_SEARCH = "search"

_WORD = re.compile(r"[a-z0-9]+")
_POSSESSIVE = re.compile(r"['\u2019]s\b")
_STOP = {
    "news", "latest", "headline", "headlines", "update", "updates", "happening", "happened", "going", "on", "with",
    "the", "a", "an", "about", "any", "anything", "whats", "what", "is", "are", "was", "were", "in", "of", "for",
    "to", "me", "tell", "give", "story", "stories", "today", "tonight", "current", "currently", "right",
    "now", "top", "big", "biggest", "recent", "recently", "lately", "this", "that", "week", "there", "some", "s",
    "and", "or", "please", "hey", "can", "could", "you", "get", "report", "reports", "breaking", "at", "from",
    "over", "things", "stuff", "info", "information", "up", "rundown", "brief", "bulletin", "general", "do",
    "does", "did", "hear", "heard", "know", "much", "lot", "more", "i", "my", "our",
}
_KEEP_S = {"news", "us", "was", "is", "has", "his", "this", "gas", "bus", "plus", "yes", "series", "species"}



EMBEDDING_BYTES = settings.SEMANTIC_ENCODER_DIM * 4

def fold(text: Optional[str]) -> str:
    text = (text or "").replace("’", "'").replace("‘", "'")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return text.replace("'", "")


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss") and word not in _KEEP_S:
        return word[:-1]
    return word


def tokens(text: Optional[str]) -> list[str]:
    return [_stem(word) for word in _WORD.findall(fold(text))]


def normalize_query(text: Optional[str]) -> str:
    seen = []
    for word in tokens(text):
        if word not in _STOP and word not in seen:
            seen.append(word)
    return " ".join(seen)


def search_terms(text: Optional[str]) -> str:
    kept = []
    for word in _WORD.findall(fold(_POSSESSIVE.sub("", text or ""))):
        if word not in _STOP and _stem(word) not in _STOP and word not in kept:
            kept.append(word)
    return " ".join(kept)


def lexical_similarity(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


def covers(ask: set, text_tokens: set) -> bool:
    if not ask:
        return False
    need = len(ask) if len(ask) <= 2 else math.ceil(len(ask) * 2 / 3)
    return len(ask & text_tokens) >= need


def item_key(title: str, source: str) -> str:
    raw = " ".join(tokens(title)) + "|" + " ".join(tokens(source))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def pack(vector) -> Optional[bytes]:
    if vector is None:
        return None
    return np.asarray(vector, dtype=np.float32).tobytes()


def unpack(blob: Optional[bytes]) -> Optional[np.ndarray]:
    if not blob:
        return None
    vector = np.frombuffer(blob, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else None


def cosine(a: Optional[np.ndarray], b: Optional[np.ndarray]) -> float:
    if a is None or b is None or a.shape != b.shape:
        return 0.0
    return float(np.dot(a, b))


def _embed_text(title: str, description: Optional[str]) -> str:
    return title if not description else f"{title}. {description[:200]}"


def _iso(value: Optional[datetime]) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if value else ""


def parse_published(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class StoredItem:
    id: int
    item_key: str
    title: str
    source: str
    url: str
    description: str
    published_at: Optional[datetime]
    tags: list = field(default_factory=list)
    first_seen_at: Optional[datetime] = None
    embedding: Optional[np.ndarray] = None

    @property
    def token_set(self) -> set:
        return set(tokens(" ".join([self.title, self.description, " ".join(self.tags)])))

    @property
    def embed_text(self) -> str:
        return _embed_text(self.title, self.description)

    def as_article(self, aired: bool = False) -> dict:
        return {
            "id": self.id,
            "source": {"id": None, "name": self.source},
            "title": self.title,
            "description": self.description,
            "url": self.url,
            "publishedAt": _iso(self.published_at),
            "tags": list(self.tags),
            "aired": aired,
        }


@dataclass
class StoredPull:
    id: int
    kind: str
    country: str
    region_key: Optional[str]
    query: str
    query_norm: str
    period: str
    item_ids: list
    ranked_ids: Optional[list]
    rank_source: str
    fetched_at: datetime
    status: str
    embedding: Optional[np.ndarray] = None

    @property
    def order(self) -> list:
        ranked = list(self.ranked_ids or [])
        return ranked + [i for i in self.item_ids if i not in set(ranked)]


def _pull(row: NewsPull) -> StoredPull:
    return StoredPull(
        id=row.id, kind=row.kind, country=row.country, region_key=row.region_key, query=row.query or "",
        query_norm=row.query_norm or "", period=row.period or "", item_ids=json.loads(row.item_ids or "[]"),
        ranked_ids=json.loads(row.ranked_ids) if row.ranked_ids else None, rank_source=row.rank_source or "",
        fetched_at=row.fetched_at, status=row.status or "ok", embedding=unpack(row.embedding),
    )


def _item(row: NewsItem, with_embedding: bool) -> StoredItem:
    return StoredItem(
        id=row.id, item_key=row.item_key, title=row.title, source=row.source or "", url=row.url or "",
        description=row.description or "", published_at=row.published_at, tags=json.loads(row.tags or "[]"),
        first_seen_at=row.first_seen_at, embedding=unpack(row.embedding) if with_embedding else None,
    )


async def _update_many(db, column: str, values: list[tuple]) -> None:
    if not values:
        return
    table = NewsItem.__table__
    stmt = update(table).where(table.c.id == bindparam("row_id")).values({column: bindparam("row_value")})
    connection = await db.connection()
    await connection.execute(stmt, [{"row_id": row_id, "row_value": value} for row_id, value in values])


class NewsStore:
    def __init__(self, session_maker=None):
        self._session_maker = session_maker

    def _sessions(self):
        if self._session_maker is None:
            from database import AsyncSessionLocal
            self._session_maker = AsyncSessionLocal
        return self._session_maker

    async def latest_pull(self, kind: str, country: str, query_norm: str, period: str,
                          since: datetime) -> Optional[StoredPull]:
        async with self._sessions()() as db:
            row = (await db.execute(
                select(NewsPull).where(and_(
                    NewsPull.kind == kind, NewsPull.country == country, NewsPull.query_norm == query_norm,
                    NewsPull.period == period, NewsPull.fetched_at >= since))
                .order_by(NewsPull.fetched_at.desc()).limit(1))).scalar_one_or_none()
        return _pull(row) if row is not None else None

    async def previous_ranked_pull(self, pull: StoredPull) -> Optional[StoredPull]:
        async with self._sessions()() as db:
            row = (await db.execute(
                select(NewsPull).where(and_(
                    NewsPull.kind == pull.kind, NewsPull.country == pull.country,
                    NewsPull.query_norm == pull.query_norm, NewsPull.period == pull.period,
                    NewsPull.id != pull.id, NewsPull.ranked_ids.is_not(None),
                    NewsPull.rank_source.in_(("llm", "reused"))))
                .order_by(NewsPull.fetched_at.desc()).limit(1))).scalar_one_or_none()
        return _pull(row) if row is not None else None

    async def recent_pulls(self, country: str, since: datetime) -> list[StoredPull]:
        async with self._sessions()() as db:
            rows = (await db.execute(
                select(NewsPull).where(and_(NewsPull.country == country, NewsPull.fetched_at >= since,
                                            NewsPull.status == "ok"))
                .order_by(NewsPull.fetched_at.desc()))).scalars().all()
        return [_pull(row) for row in rows]

    async def get_pull(self, pull_id: int) -> Optional[StoredPull]:
        async with self._sessions()() as db:
            row = await db.get(NewsPull, pull_id)
        return _pull(row) if row is not None else None

    @staticmethod
    def _mark_dirty() -> None:
        from services_radio import local_knowledge
        local_knowledge.mark_news_dirty()

    async def save_items(self, articles: list[dict], country: str, region_key: Optional[str],
                         tags: Iterable[str]) -> list[int]:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=settings.NEWS_RETENTION_S)
        pull_tags = [t for t in dict.fromkeys(tags) if t]
        rows, seen_keys, seen_urls = [], set(), set()
        for article in articles:
            title = (article.get("title") or "").strip()
            if not title:
                continue
            source = ((article.get("source") or {}).get("name") or "").strip()
            url = (article.get("url") or "").strip()
            key = item_key(title, source)
            if key in seen_keys or (url and url in seen_urls):
                continue
            seen_keys.add(key)
            if url:
                seen_urls.add(url)
            rows.append({
                "item_key": key, "title": title[:500], "source": source[:200], "url": url[:2000],
                "description": (article.get("description") or "")[:2000],
                "published_at": parse_published(article.get("publishedAt")), "country": country,
                "region_key": region_key, "tags": pull_tags, "first_seen_at": now, "last_seen_at": now,
                "expires_at": expires,
            })
        if not rows:
            return []
        async with self._sessions()() as db:
            existing = {row.item_key: row for row in (await db.execute(
                select(NewsItem.item_key, NewsItem.tags).where(
                    NewsItem.item_key.in_([r["item_key"] for r in rows])))).all()}
            for row in rows:
                prior = existing.get(row["item_key"])
                merged = list(dict.fromkeys((json.loads(prior.tags or "[]") if prior else []) + row["tags"]))
                row["tags"] = json.dumps(merged[:24])
            stmt = pg_insert(NewsItem).values(rows)
            stmt = stmt.on_conflict_do_update(index_elements=["item_key"], set_={
                "last_seen_at": stmt.excluded.last_seen_at, "expires_at": stmt.excluded.expires_at,
                "tags": stmt.excluded.tags, "url": stmt.excluded.url, "published_at": stmt.excluded.published_at,
            }).returning(NewsItem.id, NewsItem.item_key)
            ids = {key: item_id for item_id, key in (await db.execute(stmt)).all()}
            await db.commit()
        self._mark_dirty()
        return [ids[r["item_key"]] for r in rows if r["item_key"] in ids]

    async def add_pull(self, kind: str, country: str, region_key: Optional[str], query: str, query_norm: str,
                       period: str, item_ids: list, embedding=None, status: str = "ok") -> StoredPull:
        now = datetime.now(timezone.utc)
        row = NewsPull(kind=kind, country=country, region_key=region_key, query=query[:300],
                       query_norm=query_norm[:300], period=period, embedding=pack(embedding),
                       item_ids=json.dumps(item_ids), status=status, fetched_at=now,
                       expires_at=now + timedelta(seconds=settings.NEWS_RETENTION_S))
        async with self._sessions()() as db:
            db.add(row)
            await db.commit()
            await db.refresh(row)
        return _pull(row)

    async def set_ranking(self, pull_id: int, ranked_ids: list, source: str) -> None:
        async with self._sessions()() as db:
            await db.execute(update(NewsPull).where(NewsPull.id == pull_id).values(
                ranked_ids=json.dumps(ranked_ids), rank_source=source, ranked_at=datetime.now(timezone.utc)))
            await db.commit()

    async def add_tags(self, tags_by_id: dict) -> None:
        if not tags_by_id:
            return
        async with self._sessions()() as db:
            rows = (await db.execute(select(NewsItem.id, NewsItem.tags).where(
                NewsItem.id.in_(list(tags_by_id))))).all()
            await _update_many(db, "tags", [(item_id, json.dumps(list(dict.fromkeys(
                json.loads(current or "[]") + list(tags_by_id[item_id])))[:24])) for item_id, current in rows])
            await db.commit()

    async def items(self, ids: Iterable[int], with_embeddings: bool = False) -> dict:
        ids = list(dict.fromkeys(ids))
        if not ids:
            return {}
        query = select(NewsItem).where(NewsItem.id.in_(ids))
        if not with_embeddings:
            query = query.options(defer(NewsItem.embedding, raiseload=True))
        async with self._sessions()() as db:
            rows = (await db.execute(query)).scalars().all()
        return {row.id: _item(row, with_embeddings) for row in rows}

    async def missing_embeddings(self, ids: Iterable[int]) -> list[tuple]:
        ids = list(ids)
        if not ids:
            return []
        async with self._sessions()() as db:
            rows = (await db.execute(select(NewsItem.id, NewsItem.title, NewsItem.description).where(
                NewsItem.id.in_(ids), (NewsItem.embedding.is_(None)) |
                (func.octet_length(NewsItem.embedding) != EMBEDDING_BYTES)))).all()
        return [(item_id, _embed_text(title, description)) for item_id, title, description in rows]

    async def set_embeddings(self, vectors: dict) -> None:
        if not vectors:
            return
        async with self._sessions()() as db:
            await _update_many(db, "embedding", [(item_id, pack(vector)) for item_id, vector in vectors.items()])
            await db.commit()

    async def set_pull_embedding(self, pull_id: int, vector) -> None:
        async with self._sessions()() as db:
            await db.execute(update(NewsPull).where(NewsPull.id == pull_id).values(embedding=pack(vector)))
            await db.commit()

    async def aired(self, subject: str, since: datetime) -> dict:
        async with self._sessions()() as db:
            rows = (await db.execute(
                select(NewsAired.item_id, NewsItem.embedding)
                .join(NewsItem, NewsItem.id == NewsAired.item_id, isouter=True)
                .where(NewsAired.subject == subject, NewsAired.aired_at >= since))).all()
        return {item_id: unpack(blob) for item_id, blob in rows}

    async def mark_aired(self, subject: str, ids: Iterable[int]) -> None:
        now = datetime.now(timezone.utc)
        rows = [{"subject": subject, "item_id": int(i), "aired_at": now} for i in dict.fromkeys(ids)]
        if not rows:
            return
        stmt = pg_insert(NewsAired).values(rows)
        stmt = stmt.on_conflict_do_update(index_elements=["subject", "item_id"],
                                          set_={"aired_at": stmt.excluded.aired_at})
        async with self._sessions()() as db:
            await db.execute(stmt)
            await db.commit()

    async def prune(self) -> None:
        now = datetime.now(timezone.utc)
        async with self._sessions()() as db:
            await db.execute(delete(NewsPull).where(NewsPull.expires_at < now))
            await db.execute(delete(NewsItem).where(NewsItem.expires_at < now))
            await db.execute(delete(NewsAired).where(
                NewsAired.aired_at < now - timedelta(seconds=settings.NEWS_AIRED_TTL_S)))
            await db.commit()
