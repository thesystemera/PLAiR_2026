from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import json

import numpy as np
import psycopg2
from pydantic import Field, create_model

from config import settings
from models_global import run_on_gpu_executor
from services import log_service
from services.base_prompt_cache_service import BasePromptCacheService
from services.base_vector_database_service import BaseVectorDatabaseService


@dataclass(frozen=True)
class Category:
    name: str
    weight: float
    extract: Callable[[Dict[str, Any]], str]
    description: str


def field_text(key: str, limit: int = 400) -> Callable[[Dict[str, Any]], str]:
    def extract(item: Dict[str, Any]) -> str:
        value = item.get(key)
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value if v)
        return str(value or "")[:limit]
    return extract


class SemanticVectorDatabaseService(BaseVectorDatabaseService):
    category_specs: Tuple[Category, ...] = ()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.category_specs:
            cls.categories = tuple(spec.name for spec in cls.category_specs)
            total = sum(spec.weight for spec in cls.category_specs) or 1.0
            cls.default_weights = {spec.name: spec.weight / total for spec in cls.category_specs}

    def _extract_category_texts(self, item: Dict[str, Any]) -> Dict[str, str]:
        return {spec.name: spec.extract(item) or "" for spec in self.category_specs}

    def _trigger_rebuild(self):
        self.rebuild()


def normalize_weights(weights: Dict[str, float], categories: Iterable[str]) -> Dict[str, float]:
    kept = {name: max(0.0, float(weights.get(name, 0.0))) for name in categories}
    total = sum(kept.values())
    return {name: value / total for name, value in kept.items()} if total > 0 else {}


def make_prompt_cache(vector_cls, table_name: str, domain: str, examples: str, log_channel: str = "system"):
    fields = {spec.name: (float, Field(ge=0.0, le=1.0, description=spec.description))
              for spec in vector_cls.category_specs}
    weights_model = create_model(f"{vector_cls.__name__}Weights", **fields)
    analysis_model = create_model(
        f"{vector_cls.__name__}QueryAnalysis",
        intent_category=(str, Field(description="The category that matters most for this query, or 'general'")),
        category_weights=(weights_model, Field(description="Weights across all categories (sum to 1.0)")),
        cleaned_query=(str, Field(description="The search terms, without filler or prefixes")),
        confidence=(float, Field(ge=0.0, le=1.0, description="Confidence 0.0-1.0")),
        reasoning=(Optional[str], Field(default=None, description="Brief explanation")),
    )
    category_lines = "\n".join(f"- {spec.name}: {spec.description}" for spec in vector_cls.category_specs)

    class PromptCache(BasePromptCacheService):
        pass

    PromptCache.__name__ = f"{vector_cls.__name__}PromptCache"
    PromptCache.table_name = table_name
    PromptCache.stats_task_name = f"{table_name}_stats"
    PromptCache.ready_message = f"✓ {vector_cls.display_name} query intent cache ready"
    PromptCache.log_channel = log_channel
    PromptCache.analysis_model = analysis_model
    PromptCache.weights_model = weights_model
    PromptCache._json_cache_dir = lambda self: settings.USER_CONTENT_QUERY_CACHE_DIR.parent / table_name

    def _insert_cache_row(self, conn, params: tuple):
        c = conn.cursor()
        try:
            c.execute(f"INSERT INTO {table_name} VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", params)
            conn.commit()
        except psycopg2.IntegrityError:
            conn.rollback()

    PromptCache._insert_cache_row = _insert_cache_row
    PromptCache._stats_database_entry = lambda self: ("database_url", settings.EMBEDDINGS_DATABASE_URL)
    PromptCache._build_system_prompt = staticmethod(lambda: (
        f"You analyse search queries for {domain} at PLAiR, an AI radio station.\n\n"
        f"Decide which aspects of an item the query is about and weight these categories (weights sum to 1.0):\n"
        f"{category_lines}\n\n"
        "Weigh the nuance of the query, don't use fixed presets. Clean the query of filler and prefixes but keep "
        f"its meaning.\n\nExamples:\n{examples}"))
    PromptCache._build_user_prompt = staticmethod(lambda query: (
        f'Analyse this search query:\n\n"{query}"\n\nReturn the intent, the category weights and the cleaned query.'))
    return PromptCache


@dataclass
class Match:
    score: float
    similarity: float
    rowid: int
    meta: Dict[str, Any]


class SemanticSearch:
    def __init__(self, vector_db: BaseVectorDatabaseService, prompt_cache=None):
        self.vector_db = vector_db
        self.prompt_cache = prompt_cache

    async def weights_for(self, query: str, use_ai: bool) -> Tuple[Dict[str, float], str]:
        if use_ai and self.prompt_cache is not None:
            try:
                analysis = await self.prompt_cache.analyze_query(query)
            except Exception as e:
                log_service.warning(f"[SEMANTIC] {self.vector_db.display_name} intent analysis failed: {e}")
                analysis = None
            if analysis is not None:
                weights = normalize_weights(analysis.category_weights.model_dump(), self.vector_db.categories)
                if weights:
                    return weights, analysis.cleaned_query or query
        return dict(self.vector_db.default_weights), query

    def _rank(self, vector: np.ndarray, weights: Dict[str, float], n: int,
              keep: Optional[Callable[[Dict[str, Any]], bool]], boost: Optional[Callable[[Dict[str, Any]], float]],
              exclude: Optional[Dict[str, Any]] = None) -> List[Match]:
        db = self.vector_db
        matches = []
        for rowid, meta in list(db._metadata_cache.items()):
            if meta is exclude or (keep is not None and not keep(meta)):
                continue
            similarity = float(np.dot(vector, db.weighted(meta, weights)))
            matches.append(Match(similarity + (boost(meta) if boost else 0.0), similarity, rowid, meta))
        matches.sort(key=lambda match: match.score, reverse=True)
        return matches[:n]

    async def search(self, query: str, n: int = 8, keep: Optional[Callable[[Dict[str, Any]], bool]] = None,
                     boost: Optional[Callable[[Dict[str, Any]], float]] = None, use_ai: bool = False,
                     weights: Optional[Dict[str, float]] = None) -> List[Match]:
        db = self.vector_db
        if not db._metadata_cache:
            return []
        if weights is None:
            weights, query = await self.weights_for(query, use_ai)
        if not query:
            return [Match(boost(meta) if boost else 0.0, 0.0, rowid, meta)
                    for rowid, meta in list(db._metadata_cache.items()) if keep is None or keep(meta)][:n]
        query_vector = await run_on_gpu_executor(db._generate_embedding, query)
        return await run_on_gpu_executor(self._rank, query_vector, weights, n, keep, boost)

    async def similar_to(self, item: Dict[str, Any], weights: Dict[str, float], n: int = 6,
                         keep: Optional[Callable[[Dict[str, Any]], bool]] = None) -> List[Match]:
        db = self.vector_db
        if not db._metadata_cache:
            return []

        def run():
            return self._rank(db.weighted(item, weights), weights, n, keep, None, exclude=item)

        return await run_on_gpu_executor(run)


async def rebuild_if_dirty(db: Optional[SemanticVectorDatabaseService]) -> None:
    if db is not None and db.dirty:
        await run_on_gpu_executor(db.rebuild)
