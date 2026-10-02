import argparse
import asyncio
import json
import re
import shutil
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import llm_router, log_service
from services.catalog_database_service import CatalogDatabaseService
from services.catalog_vocals import VOCALS, settled_vocals

BATCH = 15
STYLE_CHARS = 600
MARKERS = 20
MAX_TOKENS = 2000
ATTEMPTS = 2
MARKER = re.compile(r"\[([^\[\]]{1,60})\]")

PROMPT = """Who sings each of these tracks? Answer from the text only, one of:
- instrumental: no vocals at all
- male: male voice(s) only
- female: female voice(s) only
- duet: both male and female voices (a duet, call-and-response, a male lead with female backing or the other way round)
- unknown: the text doesn't say who sings, or only describes the vocals without a gender (e.g. "processed vocals")

Use the style description, the vocal keywords and the section markers from the lyrics (e.g. "[Female Vocal]",
"[Verse 2: Male]", "[Duet]"). Real artist names in the style only count when their singer's gender is certain.
Reply with one JSON object mapping each track id to its answer, and nothing else: {"<track id>": "female", ...}

TRACKS:
"""


def needs_vocals(track: dict) -> bool:
    return (track.get("derived_tags") or {}).get("vocals") not in VOCALS


def brief(track: dict) -> dict:
    params, derived = track.get("generation_params") or {}, track.get("derived_tags") or {}
    lyrics = f"{params.get('prompt') or ''} {track.get('transcribed_lyrics') or ''}"
    markers = list(dict.fromkeys(" ".join(m.split()) for m in MARKER.findall(lyrics)))[:MARKERS]
    return {
        "id": track["id"],
        "title": params.get("title") or "",
        "artist": (log_service.track_artists(track) or [""])[0],
        "style": " ".join((params.get("style_canonical") or params.get("style") or "").split())[:STYLE_CHARS],
        "vocal_keywords": derived.get("vocal_style_keywords") or [],
        "lyric_markers": markers,
    }


async def ask(model: str, batch: list) -> dict:
    for attempt in range(ATTEMPTS):
        try:
            reply = await llm_router.deepseek_chat(
                spec=llm_router.LLM_BACKGROUND, model=model, temperature=0.0, max_tokens=MAX_TOKENS, json_mode=True,
                messages=[{"role": "system", "content": "You are a music cataloguer. You only state what the text says."},
                          {"role": "user", "content": PROMPT + "\n".join(json.dumps(item, ensure_ascii=False)
                                                                          for item in batch)}])
            return json.loads(reply["text"])
        except (ValueError, *llm_router.LLM_ERRORS) as e:
            print(f"  batch failed (attempt {attempt + 1}): {type(e).__name__}: {e}")
    return {}


async def main():
    parser = argparse.ArgumentParser(description="Fill derived_tags.vocals (instrumental / male / female / duet / "
                                                 "unknown): from the instrumental/vocal_gender settings where they "
                                                 f"settle it, else {BATCH} tracks per DeepSeek call (never Gemini)")
    parser.add_argument("--apply", action="store_true", help="write the results (default: dry run, no LLM calls)")
    parser.add_argument("--show", action="store_true", help="print each DeepSeek answer with its brief")
    args = parser.parse_args()

    deepseek = next((model for provider, model in llm_router.resolve_llm(llm_router.LLM_BACKGROUND)
                     if provider == "deepseek"), None)
    if not deepseek:
        raise SystemExit("No DeepSeek model in LLM_BACKGROUND; refusing to run on Gemini.")

    catalog = CatalogDatabaseService()
    await catalog.initialize()
    todo = sorted((t for t in catalog.tracks.values() if needs_vocals(t)), key=lambda t: t.get("created_at", ""))
    settled = [t for t in todo if settled_vocals(t)]
    asked = [t for t in todo if not settled_vocals(t)]
    batches = [asked[i:i + BATCH] for i in range(0, len(asked), BATCH)]
    print(f"{len(todo)} track(s) without derived_tags.vocals: {len(settled)} settled by their settings "
          f"{dict(Counter(settled_vocals(t) for t in settled))}, {len(asked)} for DeepSeek -> "
          f"{len(batches)} call(s) on {deepseek}")
    if not args.apply:
        print("Dry run: add --apply to call DeepSeek and write.")
        return

    backup_dir = catalog.metadata_dir.parent / f"metadata_backup_{date.today():%Y-%m-%d}_vocals"
    backup_dir.mkdir(parents=True, exist_ok=True)
    counts = Counter()

    async def write(track: dict, vocals: str):
        original = catalog.metadata_dir / f"{track['id']}.json"
        if original.is_file() and not (backup_dir / original.name).exists():
            shutil.copy2(original, backup_dir / original.name)
        track.setdefault("derived_tags", {})["vocals"] = vocals
        await asyncio.to_thread(catalog.write_track_metadata, track["id"], track)

    for track in settled:
        await write(track, settled_vocals(track))
        counts[f"settings:{settled_vocals(track)}"] += 1
    print(f"settings: {len(settled)} written")

    for number, batch in enumerate(batches, 1):
        briefs = [brief(track) for track in batch]
        answer = await ask(deepseek, briefs)
        for track, item in zip(batch, briefs):
            vocals = answer.get(track["id"])
            if vocals not in VOCALS:
                counts["no answer"] += 1
                continue
            if args.show:
                print(f"  {vocals:12} {item['title'][:30]!r:32} {item['style'][:90]!r} {item['lyric_markers'][:6]}")
            await write(track, vocals)
            counts[f"deepseek:{vocals}"] += 1
        print(f"batch {number}/{len(batches)} done")
    print(f"\nDone: {dict(counts)}. Originals backed up to {backup_dir}")


if __name__ == "__main__":
    asyncio.run(main())
