import argparse
import asyncio
import json
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.catalog_credit import credit_settled, is_ai_track, known_artists, settle_ai_credit
from services.catalog_database_service import CatalogDatabaseService


async def main():
    parser = argparse.ArgumentParser(description="Give every AI track one artist name in generation_params."
                                                 "artist_name, track_info.artist and derived_tags.inspired_artist")
    parser.add_argument("--apply", action="store_true", help="write the changes (default is a dry run)")
    parser.add_argument("--set", action="append", default=[], metavar="TRACK_ID=ARTIST",
                        help="use this artist for one track instead of the worked-out one (repeatable)")
    args = parser.parse_args()
    chosen = dict(item.split("=", 1) for item in args.set)

    catalog = CatalogDatabaseService()
    await catalog.initialize()
    tracks = catalog.tracks
    known = known_artists(tracks)
    todo = [t for t in tracks.values() if is_ai_track(t) and not credit_settled(t)]
    backup_dir = catalog.metadata_dir.parent / f"metadata_backup_{date.today():%Y-%m-%d}_artist_credit"
    counts = {}
    for track in sorted(todo, key=lambda t: t.get("created_at", "")):
        before = (track.get("generation_params") or {}).get("artist_name"), \
            (track.get("derived_tags") or {}).get("inspired_artist")
        if track["id"] in chosen:
            track.setdefault("generation_params", {})["artist_name"] = chosen[track["id"]]
        artist, source, _ = settle_ai_credit(track, tracks, known)
        source = "set by hand" if track["id"] in chosen else source
        counts[source] = counts.get(source, 0) + 1
        title = (track.get("generation_params") or {}).get("title", "?")
        print(f"{track['id'][:12]}  {title[:30]:30}  {before[0]!s:24} / {before[1]!s:24} -> {artist!s:28} [{source}]")
        if not args.apply or not artist:
            continue
        backup_dir.mkdir(parents=True, exist_ok=True)
        original = catalog.metadata_dir / f"{track['id']}.json"
        if original.is_file() and not (backup_dir / original.name).exists():
            shutil.copy2(original, backup_dir / original.name)
        await asyncio.to_thread(catalog.write_track_metadata, track["id"], track)

    print(f"\n{len(todo)} AI track(s) without one settled artist credit: {json.dumps(counts)}")
    print(f"{'Written; originals backed up to ' + str(backup_dir) if args.apply else 'Dry run: add --apply to write.'}")


if __name__ == "__main__":
    asyncio.run(main())
