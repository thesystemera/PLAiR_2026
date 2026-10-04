"""The catalog's ways in, without a backend or an LLM: name lookup by spelling and stations from blended aspects.

Name lookup: typed names (typos, spacing, accents, a missing 'the') against the catalog's artists and titles, with
the closest names and their spelling scores (1.0 = the same spelling).
Blends: a few stations built from words and weights, first songs listed.

Usage: python tests/catalog_ways_probe.py ["name" ...]
Set EMBEDDINGS_DIR to a copy of data/embeddings when the production backend is running.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from service_registry import services  # noqa: E402
from services import log_service  # noqa: E402
from services.catalog_aspects import Aspect  # noqa: E402

NAMES = ["Sonic Yuoth", "jesus lizard", "Nuetral Milk Hotel", "LCD Sound System", "radio head", "Bjork", "wu tang",
         "Propagandhi", "The Chariot"]
BLENDS = [
    ("vocal 'husky'", [Aspect("vocal", 1.0, "husky")]),
    ("genre 'alternative rock'", [Aspect("primary_genre", 1.0, "alternative rock")]),
    ("style 0.7 'lo-fi bedroom' + mood 0.5 'rainy, slow'",
     [Aspect("style", 0.7, "lo-fi bedroom"), Aspect("mood", 0.5, "rainy, slow")]),
    ("artist 'Sonic Yuoth'", [Aspect("primary_artist", 1.0, "Sonic Yuoth")]),
]


async def setup():
    import models_global
    await models_global.initialize_semantic_encoder()
    from services.catalog_database_service import CatalogDatabaseService
    from services.catalog_vector_database_service import CatalogVectorDatabaseService
    from services.catalog_vector_search_service import CatalogVectorSearchService
    services.catalog_service = CatalogDatabaseService()
    await services.catalog_service.initialize()
    catalog_db = CatalogVectorDatabaseService(services.catalog_service)
    await asyncio.to_thread(catalog_db.load)
    return CatalogVectorSearchService(catalog_db, services.catalog_service, None)


async def main():
    search = await setup()
    print("\nName lookup (artist):")
    for name in sys.argv[1:] or NAMES:
        closest = await search.closest_names("artist", name, 3)
        print(f"  {name:20s} -> " + ", ".join(f"{n} {s:.2f}" for n, s in closest))
    print("\nStations:")
    for label, aspects in BLENDS:
        found = await search.station(aspects, None, [], 6)
        print(f"  {label}")
        for track in found:
            tags = track.get("derived_tags") or {}
            print(f"    {track['similarity_score']:.2f} {log_service.track_label(track)[:55]:55s} "
                  f"{tags.get('primary_genre', '')} | {', '.join((tags.get('mood_keywords') or [])[:3])}")


if __name__ == "__main__":
    asyncio.run(main())
