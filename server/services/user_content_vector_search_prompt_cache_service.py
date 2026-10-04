from typing import Optional
from pydantic import BaseModel, Field
from services.base_prompt_cache_service import BasePromptCacheService

class CategoryWeights(BaseModel):
    transcription: float = Field(ge=0.0, le=1.0, description="Weight for transcription content")
    category: float = Field(ge=0.0, le=1.0, description="Weight for content category")
    urgency: float = Field(ge=0.0, le=1.0, description="Weight for urgency level")
    importance: float = Field(ge=0.0, le=1.0, description="Weight for importance score")
    tags: float = Field(ge=0.0, le=1.0, description="Weight for content tags")
    username: float = Field(ge=0.0, le=1.0, description="Weight for username")
    location: float = Field(ge=0.0, le=1.0, description="Weight for location")
    target_audience: float = Field(ge=0.0, le=1.0, description="Weight for target audience")
    sentiment: float = Field(ge=0.0, le=1.0, description="Weight for sentiment")
    content_theme: float = Field(ge=0.0, le=1.0, description="Weight for content theme")

class QueryIntentAnalysis(BaseModel):
    intent_category: str = Field(
        description="Primary category detected: category, urgency, username, location, tags, target_audience, or general"
    )
    category_weights: CategoryWeights = Field(
        description="Weight distribution across categories (must sum to 1.0)"
    )
    cleaned_query: str = Field(
        description="Cleaned search terms with category prefixes removed"
    )
    confidence: float = Field(
        description="Confidence score 0.0-1.0 for this analysis",
        ge=0.0,
        le=1.0
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Brief explanation of the analysis (optional, for debugging)"
    )

class UserContentVectorSearchPromptCacheService(BasePromptCacheService):
    table_name = "user_content_query_intent_cache"
    ready_message = "✓ User Content Cache Service Ready"
    log_channel = "user_content"
    analysis_model = QueryIntentAnalysis
    weights_model = CategoryWeights

    @staticmethod
    def _build_system_prompt() -> str:
        return """You are a user content search intent analyzer for an AI-powered radio station.

Your job is to analyze user search queries for SHOUTOUTS and USER CONTENT and determine:
1. What category they're searching for.
2. The optimal weight distribution across ALL 10 categories (must sum to 1.0).
3. Clean search terms with any category prefixes removed.

Categories (10 total):
- transcription: The actual text content of the shoutout.
- category: Type of shoutout (birthday_wishes, safety_alert, event_invitation, family_greeting, etc.).
- urgency: How time-sensitive the content is (casual, upcoming, urgent, emergency).
- importance: Reach/relevance (personal, local, citywide, public_safety).
- tags: Keywords extracted from content (family, melbourne, christmas, fire, safety, etc.).
- username: Who posted the shoutout.
- location: Geographic location (city, neighborhood, etc.).
- target_audience: Who the message is for (personal, local, citywide, everyone).
- sentiment: Emotional tone (positive, negative, neutral).
- content_theme: Overall theme/topic of the content.

Important Instructions:
- **Dynamic Weighting:** Do NOT use hardcoded presets. Analyze the nuance of the request.
- **Sum to 1.0:** Weights must sum to exactly 1.0.
- **Cleaned Query:** Remove prefixes like "Category:" or "From:". Keep natural language if relevant.
- **Confidence:** Rate 0.0-1.0 based on query clarity.

Examples of Logic:
- If user searches "birthday wishes", heavily weight `category` and `tags`.
- If user searches "from John", heavily weight `username`.
- If user searches "urgent safety alert", weight `urgency` and `category`.
- If user searches "Melbourne shoutouts", weight `location` and `tags`.
- If user searches "family messages", weight `tags`, `category`, and `target_audience`.
"""

    @staticmethod
    def _build_user_prompt(query: str) -> str:
        return f"""Analyze this user content search query:

"{query}"

Determine the user's intent, optimal category weights, and cleaned search terms."""

user_content_vector_search_prompt_cache_service = UserContentVectorSearchPromptCacheService()
