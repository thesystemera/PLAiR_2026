import json
from typing import Any, Dict, Optional, Tuple, Type

from pydantic import BaseModel

from config import settings
from services import log_service
from services.ai_service import AIService
from services.base_service import SingletonService
from services.llm_router import LLM_LIVE
from services.semantic_cache import SemanticCache
from services.task_utils import spawn

INTENT_COLUMNS = {
    "intent_category": "TEXT",
    "weights_json": "TEXT",
    "confidence": "REAL",
    "cleaned_query": "TEXT",
    "reasoning": "TEXT",
    "filters_json": "TEXT",
}


class BasePromptCacheService(SingletonService):
    """What a search query is about (its category weights and filters), worked out once by the LLM and reused for
    the same or a similar query (SemanticCache)."""

    table_name: str = ""
    ready_message: str = ""
    log_channel: str = ""
    analysis_model: Type[BaseModel]
    weights_model: Type[BaseModel]
    filter_fields: Tuple[str, ...] = ()

    def __init__(self):
        if getattr(self, '_initialized', False):
            return
        self.ai_service: Optional[AIService] = None
        self.cache = SemanticCache(self.table_name, "query_hash", "query_text", INTENT_COLUMNS, self.table_name,
                                   self._log)
        self._initialized = True

    def _log(self, message: str):
        log_service.detail(message, self.log_channel)

    def _build_system_prompt(self) -> str:
        raise NotImplementedError

    def _build_user_prompt(self, query: str) -> str:
        raise NotImplementedError

    async def initialize(self, ai_service: AIService):
        self.ai_service = ai_service
        await self.cache.prepare()
        self._log(self.ready_message)

    def _usable(self, entry: Dict[str, Any]) -> bool:
        if not self.filter_fields:
            return True
        filters = json.loads(entry["row"].get("filters_json") or "null")
        return filters is not None and all(name in filters for name in self.filter_fields)

    def _filters_of(self, analysis) -> Optional[Dict[str, Any]]:
        if not self.filter_fields:
            return None
        return {name: getattr(analysis, name, None) for name in self.filter_fields}

    def _to_analysis(self, entry: Dict[str, Any]):
        row = entry["row"]
        return self.analysis_model(
            intent_category=row["intent_category"],
            category_weights=self.weights_model(**json.loads(row["weights_json"])),
            cleaned_query=row["cleaned_query"],
            confidence=row["confidence"],
            reasoning=row.get("reasoning"),
            **(json.loads(row.get("filters_json") or "null") or {})
        )

    async def analyze_query(self, query: str, use_cache: bool = True, similarity_threshold: float = 0.95):
        if not query or not query.strip():
            return None
        query = query.strip()

        if use_cache:
            found = await self.cache.find(query, similarity_threshold, self._usable)
            if found is not None:
                kind, entry, similarity = found
                self._log(f"  ✅ [CACHE HIT - {kind.upper()}] '{query}' -> '{entry['text']}' ({similarity:.3f}), "
                          f"intent {entry['row']['intent_category']}")
                return self._to_analysis(entry)

        self._log(f"  🤖 [CACHE MISS - NEW LLM CALL] Sending '{query}' to Gemini...")
        analysis = await self._call_gemini_for_analysis(query)
        if analysis:
            filters = self._filters_of(analysis)
            spawn(self.cache.save(query, {
                "intent_category": analysis.intent_category,
                "weights_json": json.dumps(analysis.category_weights.model_dump()),
                "confidence": analysis.confidence,
                "cleaned_query": analysis.cleaned_query,
                "reasoning": analysis.reasoning,
                "filters_json": json.dumps(filters) if filters is not None else None,
            }), name=f"{self.table_name}_save")
            line = self.cache.hit_rate_line()
            if line:
                self._log(line)
        return analysis

    async def _call_gemini_for_analysis(self, query: str):
        if not self.ai_service or not self.ai_service.gemini_configured:
            log_service.error("AI service not configured for query analysis")
            return None

        system_instruction = self._build_system_prompt()
        user_prompt = self._build_user_prompt(query)

        try:
            result = await self.ai_service.call_gemini_structured(
                prompt=user_prompt,
                response_schema=self.analysis_model,
                model=settings.GEMINI_DJ_MODEL,
                temperature=0.3,
                system_instruction=system_instruction,
                role=LLM_LIVE
            )

            if result:
                return self.analysis_model(**result)

            return None

        except Exception as e:
            log_service.error(f"Gemini query analysis error: {str(e)}")
            return None
