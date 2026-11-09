"""Probe the app guide: which entries come back for the ways listeners ask about the app, and what the hosts read.

Loads server/services_radio/app_guide.md into the real guide store on the shared encoder, then prints, for each
question, the closest entries with their scores, followed by the app_guide and listener_guest nodes as the hosts get
them. No backend and no LLM call.

Usage: python tests/app_guide_probe.py ["your own question" ...]
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from service_registry import services  # noqa: E402

QUESTIONS = [
    "what can this app do",
    "how do I talk to you guys",
    "can I just type instead of talking",
    "can I upload my own songs",
    "how do I sign up",
    "why would I make an account",
    "how do I skip this song",
    "can I listen without internet",
    "play it on my phone instead",
    "what are shoutouts",
    "can you stop talking so much",
    "where do I see the lyrics",
    "how do I share this track",
    "is this music made by AI",
    "how do you know where I am",
    "can I make my own song",
    "how do I find something like this",
    "the visuals are lagging on my phone",
]


async def main(questions):
    import models_global
    await models_global.initialize_semantic_encoder()
    from services.semantic_source import SemanticSearch
    from services_radio.app_guide import AppGuideVectorDatabaseService, guide_entries
    from services_radio.context_nodes_listener import get_listener_guest
    from services_radio.context_nodes_station import get_app_guide
    guide = AppGuideVectorDatabaseService()
    await asyncio.to_thread(guide.load)
    services.app_guide = SemanticSearch(guide)
    print(f"{len(guide_entries())} entries\n")
    for question in questions:
        matches = await services.app_guide.search(question, n=4)
        print(f"\"{question}\"")
        for match in matches:
            print(f"   {match.score:.3f}  {match.meta['topic']}")
    print("\n--- app_guide node for the first question ---")
    print(await get_app_guide(user_input=questions[0]))
    print("\n--- listener_guest node ---")
    print(await get_listener_guest(user=None))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or QUESTIONS))
