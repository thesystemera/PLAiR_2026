import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from services import artist_profile_service as artists
from services.catalog_database_service import CatalogDatabaseService
from services.human_music_upload_service import read_embedded_tags

JUNK_NAME = re.compile(r"youtube|ytdown|\b(360|480|720|1080)p\b|\bmedia\b", re.I)
NOISE = re.compile(r"^(orig(i|io)nal|final|master(ed)?|mix(down)?|demo|draft|copy|v\d+|\d{2,4}|remaster(ed)?)$", re.I)


def title_from_filename(filename: str, artist: str) -> str:
    stem = Path(filename or "").stem.replace("_", " ")
    parts = [p.strip() for p in re.split(r"\s+-\s+", stem) if p.strip()]
    if len(parts) > 1 and parts[0].lower() == (artist or "").lower():
        parts = parts[1:]
    while len(parts) > 1 and NOISE.match(parts[-1].replace(" ", "")):
        parts.pop()
    title = re.sub(r"^\d{1,2}[\s.]+", "", parts[0] if parts else stem).strip()
    return " ".join(title.split()) or "Untitled"


def original_file(track: dict) -> Path:
    uid = track.get("uploaded_by_user_id")
    matches = sorted((settings.USERS_DIR / str(uid) / "tracks").glob(f"{track['id']}_original.*"))
    return matches[0] if matches else Path()


async def main():
    parser = argparse.ArgumentParser(description="Fix artist credits and titles on human uploads")
    parser.add_argument("--user", type=int, required=True, help="uploader user id")
    parser.add_argument("--artist", required=True, help="artist profile name to credit (created if missing)")
    parser.add_argument("--titles", action="store_true", help="also reset titles from tags, else the filename")
    parser.add_argument("--apply", action="store_true", help="write the changes (default is a dry run)")
    args = parser.parse_args()

    catalog = CatalogDatabaseService()
    await catalog.initialize()
    tracks = [t for t in catalog.tracks.values()
              if t.get("uploaded_by_user_id") == args.user and t.get("is_ai_generated") is False]

    profile = None
    if args.apply:
        mine = await artists.list_for_user(args.user)
        profile = next((p for p in mine if p["name"].lower() == args.artist.lower()), None)
        profile = profile or await artists.create(args.user, args.artist)

    for track in sorted(tracks, key=lambda t: t.get("created_at", "")):
        params = track.setdefault("generation_params", {})
        info = track.setdefault("track_info", {})
        old_title, old_artist = params.get("title"), params.get("artist_name") or info.get("artist")
        tags = read_embedded_tags(original_file(track)) if original_file(track).is_file() else {}
        title = tags.get("title") or title_from_filename(track.get("original_filename", ""), args.artist)
        if JUNK_NAME.search(title) or len(title) > 60:
            title = old_title
        new_title = title if args.titles else old_title
        print(f"{track['id'][:34]:34}  {old_artist!s:22} -> {args.artist}  |  {old_title!s:28} -> {new_title}")
        if not args.apply:
            continue
        params["artist_name"] = info["artist"] = profile["name"]
        track["artist_profile_id"], track["artist_slug"] = profile["id"], profile["slug"]
        track["embedded_tags"] = tags
        params["title"] = info["title"] = new_title
        await asyncio.to_thread(catalog.write_track_metadata, track["id"], track)

    print(f"\n{len(tracks)} upload(s) {'updated' if args.apply else 'would change (dry run, add --apply)'}")


if __name__ == "__main__":
    asyncio.run(main())
