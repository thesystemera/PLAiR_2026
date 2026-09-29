from typing import Dict, Iterable, List, Optional, Tuple

from services import log_service
from services.community_engagement import community_engagement
from services.user_content_database_service import coarse_location, kind_of, KIND_REVIEW

REPLY_HINT = ("play it straight after the shoutout (introduce it as a reply, say who it's from), "
              "or read it out if it has no audio")


def speaker(item: Dict) -> str:
    user_data = item.get("user_data") or {}
    username = user_data.get("username") or item.get("username") or "a listener"
    place = coarse_location(user_data.get("location") or item.get("location"))
    return f"{username} ({place})" if place else username


def text_of(item: Dict) -> str:
    return " ".join((item.get("transcription") or item.get("full_transcription") or "").split())


def audio_marker(item: Dict) -> Optional[str]:
    url = (item.get("audio_url") or "").strip()
    return f"${url}$" if url and item.get("has_audio", True) else None


def describe(item: Dict, index: Optional[int] = None, max_chars: int = 400) -> str:
    text = text_of(item)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "..."
    label = "Review" if kind_of(item) == KIND_REVIEW else "Shoutout"
    prefix = f"{index}. " if index is not None else ""
    marker = audio_marker(item)
    lines = [f"{prefix}{label} from {speaker(item)}: \"{text}\"",
             f"   Audio: {marker}" if marker else "   (typed, no audio: read it out)"]
    reply = item.get("top_reply")
    if reply:
        count = item.get("reply_count") or 1
        reply_marker = audio_marker(reply)
        lines.append(f"   Top reply (of {count}) from {speaker(reply)}: \"{text_of(reply)[:max_chars]}\""
                     + (f" Audio: {reply_marker}" if reply_marker else "") + f" - {REPLY_HINT}")
    elif kind_of(item) != KIND_REVIEW:
        lines.append("   No replies yet.")
    return "\n".join(lines)


async def with_top_replies(items: List[Dict], content_service, user_id: Optional[int]) -> List[Dict]:
    for item in items:
        replies = content_service.get_replies(item["id"]) if content_service is not None and item.get("id") else []
        item["reply_count"] = len(replies)
        item["top_reply"] = await community_engagement.top_reply(replies, user_id) if replies else None
    return items


async def pick(search, content_service, *, query: str, n: int, user_id: Optional[int], session_id: Optional[str],
               user_location: Optional[Tuple[float, float]] = None, skip: Iterable[str] = (),
               kinds="shoutout", track_id: Optional[str] = None, fresh_only: bool = True) -> List[Dict]:
    if search is None:
        return []
    exclude = set(skip)
    if fresh_only:
        exclude |= set(community_engagement.last_aired(session_id))
    try:
        found = await search.search(query=query, n_results=max(n * 3, n + 4), user_location=user_location,
                                    use_ai_analysis=False, content_type=kinds, exclude=exclude, track_id=track_id)
    except Exception as e:
        log_service.warning(f"[Community] search failed: {type(e).__name__}: {e}")
        return []
    allowed = await community_engagement.airable([f.get("id") for f in found], user_id)
    picked = [f for f in found if f.get("id") in allowed][:n]
    return await with_top_replies(picked, content_service, user_id)
