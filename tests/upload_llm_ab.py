import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from config import settings
from services import human_metadata_extraction_service as extraction
from services import llm_telemetry
from services.catalog_database_service import CatalogDatabaseService
from services.human_music_upload_service import read_embedded_tags

usage_log = []
_record = extraction.record_gemini_usage


def capture(agent, model, usage, latency_ms):
    usage_log.append({"model": model, "in": getattr(usage, "prompt_token_count", 0) or 0,
                      "out": (getattr(usage, "candidates_token_count", 0) or 0)
                      + (getattr(usage, "thoughts_token_count", 0) or 0),
                      "ms": latency_ms})
    return _record(agent, model, usage, latency_ms)


extraction.record_gemini_usage = capture


def price(model, tokens_in, tokens_out):
    rates = llm_telemetry.PRICES.get(model) if hasattr(llm_telemetry, "PRICES") else None
    if not rates:
        for name in dir(llm_telemetry):
            table = getattr(llm_telemetry, name)
            if isinstance(table, dict) and model in table and isinstance(table[model], tuple):
                rates = table[model]
                break
    if not rates:
        return 0.0
    return tokens_in / 1e6 * rates[0] + tokens_out / 1e6 * rates[1]


async def main():
    parser = argparse.ArgumentParser(description="A/B the upload analysis model on real human uploads")
    parser.add_argument("--models", default="gemini-3.5-flash,gemini-3.5-flash-lite")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--out", default="upload_llm_ab.json")
    args = parser.parse_args()
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    catalog = CatalogDatabaseService()
    await catalog.initialize()
    service = extraction.HumanMetadataExtractionService()
    await service.initialize()

    seen, tracks = set(), []
    for t in sorted(catalog.tracks.values(), key=lambda t: t.get("created_at", "")):
        if t.get("is_ai_generated") is False and t.get("fingerprint", "")[:40] not in seen:
            seen.add(t.get("fingerprint", "")[:40])
            tracks.append(t)
    tracks = tracks[:args.limit]

    report = []
    with tempfile.TemporaryDirectory() as tmp:
        for track in tracks:
            src = settings.AUDIO_DIR / f"{track['id']}.mp3"
            clip = Path(tmp) / f"{track['id']}.mp3"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-b:a", "128k", str(clip)], check=True)
            originals = sorted((settings.USERS_DIR / str(track["uploaded_by_user_id"]) / "tracks").glob(f"{track['id']}_original.*"))
            tags = read_embedded_tags(originals[0]) if originals else {}
            row = {"id": track["id"], "title": track["generation_params"].get("title"),
                   "file": track.get("original_filename"), "tags": tags, "runs": {}}
            for model in models:
                before = len(usage_log)
                started = time.perf_counter()
                result, error = await service.extract_metadata(clip, user_provided_artist="The System ERA",
                                                               filename=track.get("original_filename"), tags=tags,
                                                               model=model)
                used = usage_log[before:]
                tokens_in = sum(u["in"] for u in used)
                tokens_out = sum(u["out"] for u in used)
                row["runs"][model] = {
                    "error": error, "seconds": round(time.perf_counter() - started, 1),
                    "tokens_in": tokens_in, "tokens_out": tokens_out,
                    "usd": round(price(model, tokens_in, tokens_out), 4),
                    "result": result,
                }
                r = result or {}
                print(f"{track['id'][-18:]} {model:24} {row['runs'][model]['seconds']:5}s "
                      f"${row['runs'][model]['usd']:.4f}  genre={r.get('primary_genre')!r:24} "
                      f"title={r.get('title')!r:22} lyrics={len(r.get('transcribed_lyrics') or '')}ch "
                      f"mix={r.get('mastering_blend')}/{r.get('sonic_master_blend')}/{r.get('enhance_vocals')} "
                      f"{'ERR ' + error if error else ''}")
            report.append(row)

    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    for model in models:
        runs = [r["runs"][model] for r in report if model in r["runs"]]
        ok = [r for r in runs if not r["error"]]
        print(f"\n{model}: {len(ok)}/{len(runs)} ok, avg {sum(r['seconds'] for r in runs) / max(1, len(runs)):.1f}s, "
              f"avg ${sum(r['usd'] for r in runs) / max(1, len(runs)):.4f}/track")


if __name__ == "__main__":
    asyncio.run(main())
