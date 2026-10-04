"""Probe seed radio: does each mode hold its own thread as the station rolls on, song after song?

Drives the real PlaybackState (seed_radio, then next() song by song) on the real catalog search, without a backend
and without any LLM call. For every mode it reports, over the first N songs after the seed:
  step   how close each song is to the one before it on the mode's own aspect (cosine of the aspect's average tag
         vector, an independent check: the station itself matches tag by tag)
  seed   the same against the seed song, for songs 1-5 / 6-10 / 11-N (the seed stays in every match, so this
         should fall only a little)
  genre  share of songs with the same main genre as the song before
  sub    share of songs that share a subgenre tag with the song before
  artist share of songs by the seed song's artist
  dupes  titles heard twice
The random line is the baseline: two catalog songs picked at random.

Usage: python tests/seed_probe.py [--seeds 6] [--songs 20] [--modes primary_genre,mood]
Set EMBEDDINGS_DIR to a copy of data/embeddings when the production backend is running (two processes must not
share the Annoy files).
"""
import argparse
import asyncio
import random
import sys
import time
import uuid
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from service_registry import services  # noqa: E402
from services import log_service  # noqa: E402

MODES = ["primary_genre", "secondary_genres", "mood", "style", "theme", "lyrics", "vocal",
         "primary_artist", "similar_artists", "all"]
RANDOM_PAIRS = 400


async def setup():
    import models_global
    await models_global.initialize_semantic_encoder()
    from services.catalog_database_service import CatalogDatabaseService
    from services.catalog_vector_database_service import CatalogVectorDatabaseService
    from services.catalog_vector_search_service import CatalogVectorSearchService
    from services.playback_population_service import PlaybackPopulationService
    services.catalog_service = CatalogDatabaseService()
    await services.catalog_service.initialize()
    catalog_db = CatalogVectorDatabaseService(services.catalog_service)
    await asyncio.to_thread(catalog_db.load)
    search = CatalogVectorSearchService(catalog_db, services.catalog_service, None)
    return catalog_db, search, PlaybackPopulationService(services.catalog_service, search)


def category_vectors(db, track):
    return db.vectors_for(track)


def aspect(db, track, mode):
    if mode == "all":
        return db.weighted(track)
    return category_vectors(db, track).get(mode)


def cosine(a, b):
    if a is None or b is None:
        return None
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(np.dot(a, b) / (na * nb)) if na and nb else None


def genre(track):
    return ((track.get("derived_tags") or {}).get("primary_genre") or "").strip().lower()


def subgenres(track):
    return {g.strip().lower() for g in (track.get("derived_tags") or {}).get("secondary_genres") or [] if g}


def artist(track):
    return (log_service.track_artists(track) or [""])[0].strip().lower()


def title(track):
    return ((track.get("generation_params") or {}).get("title") or "").strip().lower()


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else float("nan")


async def run_station(population, search, seed, mode, songs):
    from services.playback_state import PlaybackState
    state = PlaybackState(f"guest_{uuid.uuid4()}", services.catalog_service, search, population)
    state._spawn_play_event = lambda **_: None
    state.radio_mode = mode
    await state.play(seed["id"])
    started = time.perf_counter()
    await state.seed_radio(mode, seed["id"])
    seed_ms = (time.perf_counter() - started) * 1000
    heard = [state.current_track]
    while len(heard) <= songs:
        if not await state.next():
            break
        heard.append(state.current_track)
    return heard, seed_ms


def score(db, mode, heard):
    seed, rest = heard[0], heard[1:]
    seed_vec = aspect(db, seed, mode)
    vecs = [aspect(db, t, mode) for t in heard]
    step = [cosine(vecs[i - 1], vecs[i]) for i in range(1, len(vecs))]
    vs_seed = [cosine(seed_vec, v) for v in vecs[1:]]
    pairs = list(zip(heard, rest))
    titles = [title(t) for t in heard if title(t)]
    return {
        "step": step,
        "seed": (vs_seed[:5], vs_seed[5:10], vs_seed[10:]),
        "genre": [genre(a) == genre(b) for a, b in pairs],
        "sub": [bool(subgenres(a) & subgenres(b)) for a, b in pairs],
        "artist": [artist(t) == artist(seed) for t in rest],
        "dupes": len(titles) - len(set(titles)),
        "songs": len(rest),
    }


def baseline(db, tracks, mode):
    rng = random.Random(7)
    pairs = [rng.sample(tracks, 2) for _ in range(RANDOM_PAIRS)]
    return {
        "step": [cosine(aspect(db, a, mode), aspect(db, b, mode)) for a, b in pairs],
        "genre": [genre(a) == genre(b) for a, b in pairs],
        "sub": [bool(subgenres(a) & subgenres(b)) for a, b in pairs],
    }


def pct(flags):
    return f"{100 * mean([float(f) for f in flags]):3.0f}%"


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=6)
    parser.add_argument("--songs", type=int, default=20)
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--show", action="store_true", help="print every station's songs")
    args = parser.parse_args()

    db, search, population = await setup()
    hidden = services.catalog_service.hidden_ids
    tracks = [t for tid, t in services.catalog_service.tracks.items() if tid not in hidden]
    seeds = random.Random(42).sample(tracks, args.seeds)

    print(f"\n{len(tracks)} tracks, {args.seeds} seeds, {args.songs} songs per station")
    print(f"{'mode':17s} {'step':>5s} {'rand':>5s}   {'seed 1-5':>8s} {'6-10':>5s} {'11+':>5s}   "
          f"{'genre':>5s} {'rand':>4s}  {'sub':>4s} {'rand':>4s}  {'artist':>6s} {'dupes':>5s} {'seed ms':>7s}")
    for mode in args.modes.split(","):
        runs = []
        for seed in seeds:
            heard, seed_ms = await run_station(population, search, seed, mode, args.songs)
            runs.append({**score(db, mode, heard), "seed_ms": seed_ms})
            if args.show:
                print(f"\n  [{mode}] seed {log_service.track_label(seed)} ({genre(seed)})")
                for i, t in enumerate(heard[1:], 1):
                    print(f"    {i:2d}. {log_service.track_label(t)[:60]:60s} {genre(t)}")
        base = baseline(db, tracks, mode)
        flat = lambda key: [v for r in runs for v in r[key]]  # noqa: E731
        seed_parts = [[v for r in runs for v in r["seed"][i]] for i in range(3)]
        print(f"{mode:17s} {mean(flat('step')):5.2f} {mean(base['step']):5.2f}   "
              f"{mean(seed_parts[0]):8.2f} {mean(seed_parts[1]):5.2f} {mean(seed_parts[2]):5.2f}   "
              f"{pct(flat('genre')):>5s} {pct(base['genre']):>4s}  {pct(flat('sub')):>4s} {pct(base['sub']):>4s}  "
              f"{pct(flat('artist')):>6s} {sum(r['dupes'] for r in runs):5d} {mean([r['seed_ms'] for r in runs]):7.0f}")


if __name__ == "__main__":
    asyncio.run(main())
