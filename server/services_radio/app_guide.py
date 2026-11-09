"""The app guide: what PLAiR can do and where each feature is, written for listeners in app_guide.md (one entry per
`## ` heading, an `Asks:` line with the ways listeners put the question, then the answer). A small semantic source on
the shared encoder; the app_guide context node gives the hosts the entries closest to what the listener asked, and
the listener_guest node reads the account entry from it."""
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.semantic_source import Category, SemanticVectorDatabaseService, field_text

GUIDE_PATH = Path(__file__).with_name("app_guide.md")
GUIDE_MATCHES = 4
ACCOUNT_ENTRY = "making-an-account"
HEADING = re.compile(r"^## (.+)$", re.MULTILINE)


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


@lru_cache(maxsize=1)
def guide_entries() -> Tuple[Dict[str, Any], ...]:
    text = GUIDE_PATH.read_text(encoding="utf-8")
    heads = list(HEADING.finditer(text))
    entries = []
    for head, after in zip(heads, heads[1:] + [None]):
        lines = [line.strip() for line in text[head.end():after.start() if after else len(text)].strip().splitlines()]
        asks = lines[0][len("Asks:"):].strip() if lines and lines[0].startswith("Asks:") else ""
        answer = " ".join(line for line in lines[1 if asks else 0:] if line)
        title = head.group(1).strip()
        entries.append({"key": slug(title), "topic": title, "asks": asks, "answer": answer})
    return tuple(entries)


def entry(key: str) -> Optional[Dict[str, Any]]:
    return next((item for item in guide_entries() if item["key"] == key), None)


class AppGuideVectorDatabaseService(SemanticVectorDatabaseService):
    category_specs = (
        Category("guide_asks", 0.45, field_text("asks"), "The ways listeners ask about this part of the app"),
        Category("guide_answer", 0.35, field_text("answer", 1200), "What it does and where to find it"),
        Category("guide_topic", 0.20, field_text("topic"), "The name of this part of the app"),
    )
    display_name = "App Guide"
    key_field = "key"
    item_noun = "entries"

    def load_rows(self, keys: Optional[List[str]] = None) -> List[Tuple[int, str, Any]]:
        rows = [(index, item["key"], dict(item)) for index, item in enumerate(guide_entries(), start=1)]
        return rows if keys is None else [row for row in rows if row[1] in set(keys)]


async def closest(text: str, n: int = GUIDE_MATCHES) -> List[Dict[str, Any]]:
    from service_registry import services
    if services.app_guide is None or not text or not text.strip():
        return []
    return [match.meta for match in await services.app_guide.search(text, n=n)]
