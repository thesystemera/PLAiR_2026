import argparse
import asyncio
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import log_service
from services.catalog_credit import credit_settled
from services.catalog_database_service import CatalogDatabaseService
from services.catalog_vocals import VOCALS, settled_vocals

LIST_FIELDS = ("secondary_genres", "mood_keywords", "vocal_style_keywords", "similar_artists")
EXAMPLES = 4


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or (isinstance(value, (list, dict))
                                                                               and not value)


def _parses(value) -> bool:
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def audit(tracks: dict) -> dict:
    problems = defaultdict(list)
    shapes = Counter()

    def flag(name, track):
        problems[name].append(track["id"])

    for track_id, track in tracks.items():
        params = track.get("generation_params") or {}
        info = track.get("track_info") or {}
        derived = track.get("derived_tags") or {}
        human = track.get("is_ai_generated") is False
        shapes[tuple(sorted(track.keys()))] += 1
        if track.get("id") != track_id:
            flag("id does not match its file name", track)
        if _blank(params.get("title") or info.get("title")):
            flag("no title", track)
        if not log_service.track_artists(track):
            flag("no artist at all", track)
        if not human and _blank(derived.get("inspired_artist")):
            flag("AI track without inspired_artist", track)
        if not human and not credit_settled(track):
            flag("AI track whose artist_name, track_info.artist and inspired_artist differ", track)
        if human and _blank(params.get("artist_name")):
            flag("upload without an artist credit", track)
        if track.get("is_ai_generated") not in (True, False, None):
            flag("is_ai_generated is not true/false", track)
        duration = info.get("duration")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0:
            flag("no duration", track)
        if _blank(params.get("style")) and _blank(params.get("style_canonical")):
            flag("no style text", track)
        if not human and _blank(params.get("style_canonical")):
            flag("no canonical style", track)
        if not isinstance(params.get("instrumental"), bool):
            flag(f"instrumental is {type(params.get('instrumental')).__name__}, not true/false", track)
        instrumental = params.get("instrumental") is True
        if derived.get("vocals") not in VOCALS:
            flag("no derived_tags.vocals", track)
        elif settled_vocals(track) not in (None, derived.get("vocals")):
            flag("derived_tags.vocals disagrees with instrumental/vocal_gender", track)
        if not instrumental and _blank(params.get("prompt")):
            flag("sung track without lyrics", track)
        if not instrumental and _blank(derived.get("lyrical_interpretation")):
            flag("sung track without lyrical_interpretation", track)
        if _blank(derived.get("primary_genre")) or str(derived.get("primary_genre")).lower() == "unknown":
            flag("no primary_genre", track)
        for name in LIST_FIELDS:
            value = derived.get(name)
            if value is not None and not isinstance(value, list):
                flag(f"{name} is {type(value).__name__}, not a list", track)
            elif not value and not (name == "vocal_style_keywords" and instrumental):
                flag(f"no {name}", track)
        if _blank(derived.get("enriched_at")):
            flag("never enriched (no enriched_at)", track)
        if _blank(derived.get("video_search_terms")) and _blank(track.get("video_search_terms")):
            flag("no video_search_terms", track)
        if track.get("video_search_terms") and not derived.get("video_search_terms"):
            flag("video_search_terms at the top level instead of derived_tags", track)
        if not _parses(track.get("created_at")):
            flag("no usable created_at", track)
        if params.get("video_search_terms"):
            flag("video_search_terms in generation_params instead of derived_tags", track)
        artists = [a.lower() for a in log_service.track_artists(track)]
        if len(set(artists)) > 2:
            flag("three or more different artist names", track)
    return {"problems": problems, "shapes": shapes}


async def main():
    parser = argparse.ArgumentParser(description="Read-only consistency audit of the track catalog metadata")
    parser.add_argument("--ids", action="store_true", help="list every affected track id")
    args = parser.parse_args()
    catalog = CatalogDatabaseService()
    await catalog.initialize()
    tracks = catalog.tracks
    result = audit(tracks)
    print(f"{len(tracks)} tracks ({sum(1 for t in tracks.values() if t.get('is_ai_generated') is False)} uploads), "
          f"{len(result['shapes'])} different top-level shapes")
    for name, ids in sorted(result["problems"].items(), key=lambda item: -len(item[1])):
        examples = ", ".join((tracks[i].get("generation_params") or {}).get("title") or i[:8] for i in ids[:EXAMPLES])
        print(f"{len(ids):5d}  {name}  (e.g. {examples})")
        if args.ids:
            print("       " + " ".join(ids))
    print("\nTop-level shapes:")
    for shape, count in result["shapes"].most_common():
        print(f"{count:5d}  {', '.join(shape)}")


if __name__ == "__main__":
    asyncio.run(main())
