import argparse
import asyncio
import json
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import llm_router, log_service
from services.catalog_database_service import CatalogDatabaseService
from services.youtube_clip_service import VIDEO_SEARCH_TERMS_PROMPT

BATCH = 15
STYLE_CHARS = 300
LYRIC_CHARS = 300
MIN_TERMS = 8
MAX_TERMS = 15
TERM_CHARS = 80
MAX_TOKENS = 4000
ATTEMPTS = 2


def missing(track: dict) -> bool:
    derived = track.get("derived_tags") or {}
    return not (derived.get("video_search_terms") or track.get("video_search_terms")
                or (track.get("generation_params") or {}).get("video_search_terms"))


def brief(track: dict) -> dict:
    params, derived = track.get("generation_params") or {}, track.get("derived_tags") or {}
    lyrics = "" if params.get("instrumental") else (derived.get("lyrical_interpretation") or params.get("prompt") or "")
    return {
        "id": track["id"],
        "title": params.get("title") or "",
        "artist": (log_service.track_artists(track) or [""])[0],
        "genre": ", ".join([derived.get("primary_genre") or ""] + list(derived.get("secondary_genres") or [])[:3]),
        "mood": ", ".join(derived.get("mood_keywords") or []),
        "style": (params.get("style_canonical") or params.get("style") or "")[:STYLE_CHARS],
        "lyrics": " ".join(lyrics.split())[:LYRIC_CHARS],
    }


def prompt_for(batch: list) -> str:
    return (f"{VIDEO_SEARCH_TERMS_PROMPT}\n"
            "For EACH track below, write its video_search_terms following the guidance above. Reply with one JSON "
            "object mapping each track id to its list of terms, and nothing else: "
            '{"<track id>": ["term", ...], ...}\n\nTRACKS:\n'
            + "\n".join(json.dumps(item, ensure_ascii=False) for item in batch))


def clean_terms(value) -> list:
    if not isinstance(value, list):
        return []
    terms = [" ".join(str(term).split())[:TERM_CHARS] for term in value if isinstance(term, str) and term.strip()]
    return list(dict.fromkeys(terms))[:MAX_TERMS]


async def ask(model: str, batch: list) -> dict:
    for attempt in range(ATTEMPTS):
        try:
            reply = await llm_router.deepseek_chat(
                spec=llm_router.LLM_BACKGROUND, model=model, temperature=0.7, max_tokens=MAX_TOKENS, json_mode=True,
                messages=[{"role": "system", "content": "You are a VJ curating background video for a music catalog."},
                          {"role": "user", "content": prompt_for(batch)}])
            return json.loads(reply["text"])
        except (ValueError, *llm_router.LLM_ERRORS) as e:
            print(f"  batch failed (attempt {attempt + 1}): {type(e).__name__}: {e}")
    return {}


async def main():
    parser = argparse.ArgumentParser(description="Fill derived_tags.video_search_terms on tracks that have none, "
                                                 f"{BATCH} tracks per DeepSeek call (never Gemini)")
    parser.add_argument("--limit", type=int, default=0, help="at most this many tracks (0 = all)")
    parser.add_argument("--apply", action="store_true", help="write the results (default: dry run, no LLM calls)")
    args = parser.parse_args()

    deepseek = next((model for provider, model in llm_router.resolve_llm(llm_router.LLM_BACKGROUND)
                     if provider == "deepseek"), None)
    if not deepseek:
        raise SystemExit("No DeepSeek model in LLM_BACKGROUND; refusing to run on Gemini.")

    catalog = CatalogDatabaseService()
    await catalog.initialize()
    todo = sorted((t for t in catalog.tracks.values() if missing(t)), key=lambda t: t.get("created_at", ""))
    if args.limit:
        todo = todo[:args.limit]
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    print(f"{len(todo)} track(s) without video search terms -> {len(batches)} DeepSeek call(s) on {deepseek}")
    if not args.apply:
        print("Dry run: add --apply to call DeepSeek and write.")
        return

    backup_dir = catalog.metadata_dir.parent / f"metadata_backup_{date.today():%Y-%m-%d}_video_search_terms"
    backup_dir.mkdir(parents=True, exist_ok=True)
    written = short = 0
    for number, batch in enumerate(batches, 1):
        answer = await ask(deepseek, [brief(track) for track in batch])
        for track in batch:
            terms = clean_terms(answer.get(track["id"]))
            if len(terms) < MIN_TERMS:
                short += 1
                continue
            original = catalog.metadata_dir / f"{track['id']}.json"
            if original.is_file() and not (backup_dir / original.name).exists():
                shutil.copy2(original, backup_dir / original.name)
            track.setdefault("derived_tags", {})["video_search_terms"] = terms
            await asyncio.to_thread(catalog.write_track_metadata, track["id"], track)
            written += 1
        print(f"batch {number}/{len(batches)}: {written} written so far, {short} skipped (fewer than {MIN_TERMS} terms)")
    print(f"\nDone: {written} track(s) written, {short} left for another run. Originals backed up to {backup_dir}")


if __name__ == "__main__":
    asyncio.run(main())
