from typing import Optional

from pydantic import BaseModel

from services import log_service
from services.llm_router import LLM_LIVE

WHAT_IT_IS = {
    "review": "a review of a song. Other listeners read it on the song, and its best line can play over the song",
    "shoutout": "a shoutout that plays on air to everyone listening",
    "reply": "a reply to another listener's shoutout. The best replies play on air straight after that shoutout. "
             "Replies are naturally short: a warm, funny or genuine response to that shoutout is worth keeping",
}

SYSTEM = (
    "You are the station's editor for listener posts. The DJs want to save the listener's words below as {what}. "
    "Decide whether it's worth keeping. This is a friendly community, not a writing contest, so lean towards "
    "keeping: keep it when it says something real to the people who'll hear or read it (an opinion, a reason, a "
    "message, a story, a joke, a feeling, a congratulation), even if it's short or rough. Scrap it only when "
    "there's nothing in it for anyone else (just 'cool song', 'ok' or 'yeah'), when it's really a request to the "
    "DJs, or when it isn't fit to broadcast. "
    "feedback: one short, specific sentence the DJs will pass on in their own words - what makes it good, or what "
    "the listener would need to add for it to be worth keeping."
)


class CommunityVerdict(BaseModel):
    keep: bool
    feedback: str


def post_context(track: Optional[dict] = None, parent: Optional[dict] = None) -> str:
    """What the editor needs to know about where the post is going: the song, or the shoutout being answered."""
    if track:
        artist = track.get("artist") or ""
        return f"Song: {track.get('title') or 'unknown'}" + (f" by {artist}" if artist else "")
    if parent:
        name = (parent.get("user_data") or {}).get("username") or "a listener"
        return f"Shoutout being replied to, from {name}: \"{parent.get('full_transcription') or ''}\""
    return ""


async def judge(ai_service, kind: str, text: str, context: str = "") -> Optional[dict]:
    """One small structured call on the live chain. None when the editor couldn't answer."""
    if ai_service is None or not (text or "").strip():
        return None
    prompt = (f"{context}\n" if context else "") + f"Listener's words: \"{text.strip()}\""
    try:
        verdict = await ai_service.call_gemini_structured(
            prompt=prompt, response_schema=CommunityVerdict, system_instruction=SYSTEM.format(what=WHAT_IT_IS[kind]),
            temperature=0.2, role=LLM_LIVE)
    except Exception as e:
        log_service.warning(f"Community editor failed for a {kind}: {e}")
        return None
    if verdict:
        log_service.detail(f"Community editor: {kind} {'kept' if verdict.get('keep') else 'scrapped'} - "
                           f"{verdict.get('feedback')}", "commands")
    return verdict
