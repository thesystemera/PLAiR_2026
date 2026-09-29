import asyncio
import copy
import re
import unicodedata
from typing import Dict, Optional, Set

from services import log_service
from services.task_utils import spawn

PARALANGUAGE_TAG = re.compile(r"~([^~\n]{1,60})~")
DISPLAY_FIELDS = ("bot_response", "content", "text")
MAX_EMOJI_CODEPOINTS = 8


def clean_emoji(raw: Optional[str]) -> Optional[str]:
    kept = "".join(ch for ch in (raw or "").strip()
                   if unicodedata.category(ch).startswith("S") or ch in "‍️")
    kept = kept[:MAX_EMOJI_CODEPOINTS]
    return kept if any(unicodedata.category(ch).startswith("S") for ch in kept) else None


class ParalanguageEmoji:
    """Shows paralanguage tags (~laughs~) as emojis in the chat, the LifeSpan way.

    Every paralanguage title in the voice cache gets an emoji from a small LLM call, stored on its
    meta_embeddings rows. A tag in outgoing chat text is shown as the emoji of the closest cached
    title. Stored conversation text keeps the raw tags.
    """

    def __init__(self, vector_db_service, prompt_service):
        self.db = vector_db_service
        self.prompts = prompt_service
        self._by_title: Optional[Dict[str, str]] = None
        self._by_tag: Dict[str, str] = {}
        self._pending: Set[str] = set()

    async def _titles(self) -> Dict[str, str]:
        if self._by_title is None:
            self._by_title = await asyncio.to_thread(self.db.paralanguage_emojis)
        return self._by_title

    def note_new_title(self, title: str):
        key = (title or "").strip().lower()
        if key and (self._by_title is None or key not in self._by_title):
            self._schedule(key)

    def _schedule(self, title: str):
        if title in self._pending:
            return
        self._pending.add(title)
        spawn(self._generate(title), name="paralanguage_emoji")

    async def _generate(self, title: str):
        try:
            emoji = clean_emoji(await self.prompts.generate_paralanguage_emoji(title))
            if not emoji:
                return
            await asyncio.to_thread(self.db.set_paralanguage_emoji, title, emoji)
            (await self._titles())[title] = emoji
            self._by_tag.clear()
            log_service.detail(f"Paralanguage emoji: '{title}' -> {emoji}", "tts_generation")
        except Exception as e:
            log_service.warning(f"Paralanguage emoji for '{title}' failed: {e}")
        finally:
            self._pending.discard(title)

    async def emoji_for(self, tag: str) -> Optional[str]:
        key = tag.strip().lower()
        if key in self._by_tag:
            return self._by_tag[key]
        titles = await self._titles()
        if key in titles:
            self._by_tag[key] = titles[key]
            return titles[key]
        for title in await asyncio.to_thread(self.db.nearest_titles, 'meta_embeddings', key, 5):
            known = titles.get(title.strip().lower())
            if known:
                self._by_tag[key] = known
                return known
            self._schedule(title.strip().lower())
        return None

    async def render(self, text: Optional[str]) -> Optional[str]:
        if not text or "~" not in text:
            return text
        tags = {match.group(1) for match in PARALANGUAGE_TAG.finditer(text)}
        emojis = {tag: await self.emoji_for(tag) for tag in tags}
        return PARALANGUAGE_TAG.sub(lambda m: emojis.get(m.group(1)) or m.group(0), text)

    async def render_message(self, message: dict) -> dict:
        data = message.get("data")
        if message.get("type") not in ("conversation_update", "dj_activity") or not isinstance(data, dict):
            return message
        if not any(isinstance(data.get(field), str) and "~" in data[field] for field in DISPLAY_FIELDS):
            return message
        message = copy.deepcopy(message)
        for field in DISPLAY_FIELDS:
            if isinstance(message["data"].get(field), str):
                message["data"][field] = await self.render(message["data"][field])
        return message
