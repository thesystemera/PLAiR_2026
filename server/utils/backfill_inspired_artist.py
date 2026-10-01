import argparse
import asyncio
import json
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.catalog_credit import ai_artist, is_ai_track, known_artists
from services.catalog_database_service import CatalogDatabaseService


async def main():
    parser = argparse.ArgumentParser(description="Give every AI track an inspired-by artist (derived_tags."
                                                 "inspired_artist), from its own data")
    parser.add_argument("--apply", action="store_true", help="write the changes (default is a dry run)")
    parser.add_argument("--set", action="append", default=[], metavar="TRACK_ID=ARTIST",
                        help="use this artist for one track instead of the worked-out one (repeatable)")
    args = parser.parse_args()
    chosen = dict(item.split("=", 1) for item in args.set)

    catalog = CatalogDatabaseService()
    await catalog.initialize()
    tracks = catalog.tracks
    known = known_artists(tracks)
    todo = [t for t in tracks.values() if is_ai_track(t) and not (t.get("derived_tags") or {}).get("inspired_artist")]
    backup_dir = catalog.metadata_dir.parent / f"metadata_backup_{date.today():%Y-%m-%d}_inspired_artist"
    counts = {}
    for track in sorted(todo, key=lambda t: t.get("created_at", "")):
        artist, source = (chosen[track["id"]], "set by hand") if track["id"] in chosen else \
            ai_artist(track, tracks, known)
        counts[source] = counts.get(source, 0) + 1
        similar = (track.get("derived_tags") or {}).get("similar_artists") or []
        check = "" if not artist or artist.lower() in (s.lower() for s in similar) else "   (not in its similar artists)"
        title = (track.get("generation_params") or {}).get("title", "?")
        print(f"{track['id'][:12]}  {title[:30]:30}  -> {artist!s:30} [{source}]{check}")
        if not args.apply or not artist:
            continue
        backup_dir.mkdir(parents=True, exist_ok=True)
        original = catalog.metadata_dir / f"{track['id']}.json"
        if original.is_file() and not (backup_dir / original.name).exists():
            shutil.copy2(original, backup_dir / original.name)
        track.setdefault("derived_tags", {})["inspired_artist"] = artist
        await asyncio.to_thread(catalog.write_track_metadata, track["id"], track)

    print(f"\n{len(todo)} track(s) without an inspired-by artist: {json.dumps(counts)}")
    print(f"{'Written; originals backed up to ' + str(backup_dir) if args.apply else 'Dry run: add --apply to write.'}")


if __name__ == "__main__":
    asyncio.run(main())
