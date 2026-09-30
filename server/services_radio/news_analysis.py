from typing import Optional

from pydantic import BaseModel, Field

from services import usage_tracking
from services.llm_router import LLM_BACKGROUND

CATEGORIES = ("politics", "crime", "business", "economy", "weather", "sport", "music", "arts", "entertainment",
              "science", "technology", "health", "environment", "community", "transport", "education", "world",
              "lifestyle", "other")
TONES = ("good news", "bad news", "sad", "serious", "funny", "quirky", "neutral")
FEATURE = "NewsService.analyse"


class StoryCard(BaseModel):
    n: int = Field(description="The story's number from the list")
    tags: list[str] = Field(description="3-6 short lowercase topic tags a listener might use to ask about it, from "
                                        "specific to general, e.g. ['all blacks', 'rugby', 'sport']")
    people: list[str] = Field(description="Named people, bands, teams, companies and organisations in the story")
    place: Optional[str] = Field(default=None, description="Where it happens, written so a map search finds it")
    category: str = Field(description="One of: " + ", ".join(CATEGORIES))
    tone: str = Field(description="One of: " + ", ".join(TONES))
    worth: int = Field(ge=1, le=10, description="How good this is for a local radio host to talk about, 1-10")


class StoryCards(BaseModel):
    stories: list[StoryCard]


SYSTEM = "You are a careful radio news editor and news geographer."


def _prompt(stories: list[dict], edition: str) -> str:
    listing = "\n\n".join(
        f"{i}. {story['title']} ({story['source'] or 'unknown outlet'})\n{story['text'] or '(headline only)'}"
        for i, story in enumerate(stories))
    return (
        f"News stories (edition: {edition}):\n\n{listing}\n\n"
        "Give each story a card:\n"
        "- tags: 3-6 short lowercase topic tags, specific to general.\n"
        "- people: the named people, bands, teams, companies and organisations; [] if none.\n"
        "- place: the most specific real-world place where the story happens, written so a map search finds it, "
        "from street to country: 'Karangahape Road, Auckland, New Zealand', 'Ponsonby, Auckland, New Zealand', "
        "'Los Angeles, California, USA', 'Turkey'. The edition only helps disambiguate: an international story is "
        "placed where it happens. null when the story isn't about a place (markets, technology, celebrity, opinion, "
        "results with no venue).\n"
        f"- category: one of {', '.join(CATEGORIES)}.\n"
        f"- tone: one of {', '.join(TONES)}.\n"
        "- worth: 1-10, how good it is for a local radio host to bring up: local relevance, human interest, "
        "something listeners would talk about. Dry process stories and paywall teasers score low.\n"
        "Use only what the text says. Return one card per story, with its number."
    )


def _clean(card: dict) -> dict:
    tags = [" ".join(str(t).lower().split())[:40] for t in card.get("tags") or [] if str(t).strip()][:6]
    people = [" ".join(str(p).split())[:80] for p in card.get("people") or [] if str(p).strip()][:10]
    place = " ".join((card.get("place") or "").split())[:160] or None
    category = card.get("category") if card.get("category") in CATEGORIES else "other"
    tone = card.get("tone") if card.get("tone") in TONES else "neutral"
    return {"tags": tags, "people": people, "place": place, "category": category, "tone": tone,
            "worth": max(1, min(10, int(card.get("worth") or 1))) / 10}


async def analyse(ai_service, stories: list[dict], edition: str) -> dict:
    if not stories:
        return {}
    with usage_tracking.feature_scope(FEATURE):
        result = await ai_service.call_gemini_structured(
            prompt=_prompt(stories, edition), response_schema=StoryCards, system_instruction=SYSTEM,
            temperature=0, role=LLM_BACKGROUND)
    cards = {}
    for card in (result or {}).get("stories") or []:
        n = card.get("n")
        if isinstance(n, int) and 0 <= n < len(stories):
            cards[stories[n]["id"]] = _clean(card)
    return cards
