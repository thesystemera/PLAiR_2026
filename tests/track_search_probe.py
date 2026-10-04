"""Probe the DJs' track search with clue-style requests and report where the intended artist ranks.

Runs the real catalog search (`CatalogVectorSearchService.search`, the one behind `search_and_play` and the app's
search box) without a backend: once with the plain category weights and once with the AI weighting
(`use_ai_analysis=True`, one small Gemini call per new query, cached after that).

Usage: python tests/track_search_probe.py [--no-ai] [--top N] ["query=>Expected Artist" ...]
Set EMBEDDINGS_DIR to a copy of data/embeddings when the production backend is running (two processes must not
share the Annoy files).
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from service_registry import services  # noqa: E402
from services import log_service  # noqa: E402
from services.ai_service import AIService  # noqa: E402

SCENARIOS = [
    ("90s band male and female singer starting with S", "Sonic Youth"),
    ("90s band with a male and a female singer, grungy rock", "Sonic Youth"),
    ("noisy New York guitar band, Daydream Nation era", "Sonic Youth"),
    ("that band with Kim Gordon and Thurston Moore", "Sonic Youth"),
    ("Sonic Youth", "Sonic Youth"),
    ("dreamy shoegaze with whispered vocals and walls of guitar", "Slowdive"),
    ("the krautrock pop band with the French singer and the Moog", "Stereolab"),
    ("riot grrrl band with two guitars and no bass player", "Sleater-Kinney"),
    ("90s Britpop band with the glam singer", "Suede"),
    ("really slow droning doom metal band", "Sunn O)))"),
    ("Boston band, loud quiet loud, screaming singer", "Pixies"),
]


async def setup():
    import models_global
    await models_global.initialize_semantic_encoder()
    services.ai_service = AIService()
    await services.ai_service.initialize()
    from services.catalog_database_service import CatalogDatabaseService
    from services.catalog_vector_database_service import CatalogVectorDatabaseService
    from services.catalog_vector_search_prompt_cache_service import CatalogVectorSearchPromptCacheService
    from services.catalog_vector_search_service import CatalogVectorSearchService
    services.catalog_service = CatalogDatabaseService()
    await services.catalog_service.initialize()
    catalog_db = CatalogVectorDatabaseService(services.catalog_service)
    await asyncio.to_thread(catalog_db.load)
    cache = CatalogVectorSearchPromptCacheService()
    await cache.initialize(services.ai_service, catalog_db)
    return CatalogVectorSearchService(catalog_db, services.catalog_service, cache)


def artist_of(track) -> str:
    return " / ".join(log_service.track_artists(track)) or "?"


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-ai", action="store_true")
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("scenarios", nargs="*")
    args = parser.parse_args()
    scenarios = [tuple(s.split("=>", 1)) for s in args.scenarios] if args.scenarios else SCENARIOS
    search = await setup()
    arms = [("plain", False)] + ([] if args.no_ai else [("ai", True)])
    for query, expected in scenarios:
        print(f"\n== '{query}'  (want {expected})")
        for name, ai in arms:
            found = await search.search(query, n_results=60, use_ai_analysis=ai)
            ranks = [i + 1 for i, track in enumerate(found) if expected.lower() in artist_of(track).lower()]
            first = ranks[0] if ranks else None
            print(f"  {name:5s} first {expected} at #{first if first else '-'} of {len(found)} | weights "
                  f"{ {k: round(v, 2) for k, v in (found[0].get('match_weights') or {}).items() if v} if found else {} }")
            for i, track in enumerate(found[:args.top], 1):
                params = track.get("generation_params", {})
                print(f"     {i:2d}. {params.get('title', '?')[:28]:28s} {artist_of(track)[:40]:40s} "
                      f"{track.get('similarity_score', 0):.3f}")


if __name__ == "__main__":
    asyncio.run(main())
