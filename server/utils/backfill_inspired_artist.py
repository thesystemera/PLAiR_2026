import argparse
import asyncio
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.catalog_database_service import CatalogDatabaseService

NAME_MAX_CHARS = 60
SOURCE_DEPTH = 5
CONTAINED_NAME_MIN_CHARS = 4


def _name_like(text) -> bool:
    text = (text or "").strip() if isinstance(text, str) else ""
    return bool(text) and len(text) <= NAME_MAX_CHARS and "\n" not in text and "[" not in text


def known_artists(tracks: dict) -> dict:
    names = {}
    for track in tracks.values():
        params, derived = track.get("generation_params") or {}, track.get("derived_tags") or {}
        for name in [params.get("artist_name"), (track.get("track_info") or {}).get("artist"),
                     derived.get("inspired_artist")] + list(derived.get("similar_artists") or []):
            if _name_like(name):
                names.setdefault(name.strip().lower(), name.strip())
    return names


def _artist_in_request(request: str, known: dict):
    text = request.strip().lower().rstrip(".!")
    if text in known:
        return known[text]
    words = f" {re.sub(r'[^a-z0-9]+', ' ', text)} "
    found = [name for key, name in known.items()
             if len(key) >= CONTAINED_NAME_MIN_CHARS and f" {re.sub(r'[^a-z0-9]+', ' ', key).strip()} " in words]
    return max(found, key=len) if found else None


def resolve(track: dict, tracks: dict, known: dict, depth: int = 0):
    params = track.get("generation_params") or {}
    derived = track.get("derived_tags") or {}
    if derived.get("inspired_artist"):
        return derived["inspired_artist"], "already set"
    for value, source in ((params.get("artist_name"), "artist name"),
                          ((track.get("track_info") or {}).get("artist"), "track info")):
        if _name_like(value):
            return value.strip(), source
    request = track.get("user_request")
    request = request.get("original_text") if isinstance(request, dict) else None
    if _name_like(request):
        artist = _artist_in_request(request, known)
        if artist:
            return artist, "original request"
    parent = tracks.get(track.get("generated_from") or "")
    if parent is not None and depth < SOURCE_DEPTH:
        artist, _ = resolve(parent, tracks, known, depth + 1)
        if artist:
            return artist, "source track"
    similar = derived.get("similar_artists") or []
    if similar:
        return similar[0], "first similar artist"
    return None, "nothing to go on"


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
    todo = [t for t in tracks.values()
            if t.get("is_ai_generated") is not False and not (t.get("derived_tags") or {}).get("inspired_artist")]
    backup_dir = catalog.metadata_dir.parent / f"metadata_backup_{date.today():%Y-%m-%d}_inspired_artist"
    counts = {}
    for track in sorted(todo, key=lambda t: t.get("created_at", "")):
        artist, source = (chosen[track['id']], 'set by hand') if track['id'] in chosen else resolve(track, tracks, known)
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
