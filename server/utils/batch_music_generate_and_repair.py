import sys
import asyncio
import random
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
from pydantic import BaseModel  # type: ignore
import json

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.log_service import start_log_worker, stop_worker
from services.ai_service import AIService
from services.suno_service import SunoService
from services.suno_metadata_service import MusicMetadata
from services.suno_prompt_service import MusicPromptService
from services.catalog_database_service import CatalogDatabaseService
from services.playback_service import PlaybackService
from services.catalog_vector_database_service import CatalogVectorDatabaseService
from services.catalog_vector_search_service import CatalogVectorSearchService
from services.suno_service_orchestrator import SunoServiceOrchestrator
from services.suno_generation_queue_service import SunoGenerationQueueService
from services.suno_enriched_metadata_service import EnrichedMetadataService
from services import log_service
from config import settings
from database import init_db
import models_global

class ArtistList(BaseModel):
    artists: List[str]

def truncate_list(items: list, limit: int = 5) -> None:
    if not items:
        return
    for item in items[:limit]:
        msg = f"    - {item[0]}" if isinstance(item, tuple) and len(item) > 1 else f"    - {item}"
        log_service.batch_music(msg)
    if len(items) > limit:
        log_service.batch_music(f"    ... and {len(items) - limit} more")

def file_exists_and_valid(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0

def check_track_missing_files(tid: str, stats: dict, transcoding) -> None:
    if not file_exists_and_valid(settings.AUDIOFEATURES_DIR / f"{tid}.json"):
        stats["missing_features"].append(tid)

    if not file_exists_and_valid(settings.LYRIC_TIMESTAMPS_DIR / f"{tid}.json"):
        stats["missing_lyrics"].append(tid)

    webm_valid = all(file_exists_and_valid(transcoding.get_webm_path(tid, br)) for br in ['128k', '192k', '256k'])
    if not webm_valid:
        stats["missing_webm"].append(tid)

    opus_valid = all(file_exists_and_valid(transcoding.get_opus_path(tid, br)) for br in ['128k', '192k', '256k'])
    if not opus_valid:
        stats["missing_opus"].append(tid)

async def initialize_generation_stack(
        metadata_service: MusicMetadata,
        orchestrator: SunoServiceOrchestrator
) -> Optional[Tuple[SunoGenerationQueueService, CatalogDatabaseService]]:
    log_service.batch_music("Initializing Generation Stack...")

    ai_service = AIService()
    if not getattr(ai_service, '_initialized', False):
        await ai_service.initialize()

    suno_service = SunoService()
    if not getattr(suno_service, '_initialized', False):
        await suno_service.initialize()

    prompt_service = MusicPromptService(ai_service)
    if not getattr(prompt_service, '_initialized', False):
        await prompt_service.initialize()

    catalog_service = CatalogDatabaseService()
    if not getattr(catalog_service, '_initialized', False):
        await catalog_service.initialize()

    await models_global.initialize_tts_models()

    catalog_db = CatalogVectorDatabaseService()
    catalog_db.load_initial_data()

    vec_search = CatalogVectorSearchService(catalog_db, catalog_service)
    playback = PlaybackService(catalog_service, vec_search)

    enriched_metadata_service = EnrichedMetadataService()
    if not getattr(enriched_metadata_service, '_initialized', False):
        await enriched_metadata_service.initialize()

    background_service = SunoGenerationQueueService()
    if not getattr(background_service, '_initialized', False):
        await background_service.initialize(
            suno_service=suno_service,
            prompt_service=prompt_service,
            metadata_service=metadata_service,
            catalog_service=catalog_service,
            playback_service=playback,
            vector_search_service=vec_search,
            orchestrator=orchestrator,
            enriched_metadata_service=enriched_metadata_service
        )

    return background_service, catalog_service

async def comprehensive_system_check_and_repair(
        metadata_service: MusicMetadata,
        orchestrator: SunoServiceOrchestrator
) -> None:
    log_service.batch_music("=" * 80)
    log_service.batch_music("COMPREHENSIVE SYSTEM CHECK & REPAIR")
    log_service.batch_music("=" * 80)

    master_wav_dir = settings.ENHANCED_WAV_DIR
    mp3_dir = settings.AUDIO_DIR
    metadata_dir = settings.METADATA_DIR

    stats: Dict[str, Any] = {
        "total_valid_tracks": 0,
        "tracks_with_master_wav": 0,
        "missing_metadata": [],
        "missing_artwork": [],
        "missing_features": [],
        "missing_lyrics": [],
        "missing_webm": [],
        "missing_opus": [],
        "needs_audio_pipeline": [],
        "corrupt_metadata": 0,
        "orphaned_metadata": []
    }

    from services.audio_transcoding_service import AudioTranscodingService
    transcoding = AudioTranscodingService()

    mp3_files = list(mp3_dir.glob("*.mp3"))
    log_service.batch_music(f"Scanning {len(mp3_files)} MP3/JSON pairs...")

    for mp3 in mp3_files:
        tid = mp3.stem
        meta_path = metadata_dir / f"{tid}.json"

        if not meta_path.exists():
            stats["missing_metadata"].append(tid)
            continue

        try:
            meta = await metadata_service.load_metadata(tid)
            if not meta:
                stats["corrupt_metadata"] += 1
                continue
            stats["total_valid_tracks"] += 1

            if file_exists_and_valid(master_wav_dir / f"{tid}.wav"):
                stats["tracks_with_master_wav"] += 1
            else:
                stats["needs_audio_pipeline"].append(tid)

            track_info = meta.get("track_info") if meta else None
            image_url = track_info.get("image_url") if track_info else None
            if not file_exists_and_valid(settings.ARTWORK_DIR / f"{tid}.jpeg") and image_url:
                stats["missing_artwork"].append(tid)

            check_track_missing_files(tid, stats, transcoding)

        except Exception as e:
            log_service.error(f"Error scanning {tid}: {e}")
            stats["corrupt_metadata"] += 1

    mp3_ids = {f.stem for f in mp3_files}
    for json_file in metadata_dir.glob("*.json"):
        if json_file.stem not in mp3_ids:
            stats["orphaned_metadata"].append(json_file.stem)

    await asyncio.sleep(0.1)
    log_service.batch_music("-" * 60)
    log_service.batch_music(f"Valid MP3/JSON Pairs:   {stats['total_valid_tracks']}")
    log_service.batch_music(f"Tracks with Master WAV: {stats['tracks_with_master_wav']}")
    log_service.batch_music("-" * 60)

    if stats["needs_audio_pipeline"]:
        log_service.warning(f"⚠  Incomplete audio pipeline (Missing Master): {len(stats['needs_audio_pipeline'])}")
        truncate_list(stats["needs_audio_pipeline"])
    if stats["missing_artwork"]:
        log_service.warning(f"⚠  Missing artwork: {len(stats['missing_artwork'])}")
        truncate_list(stats["missing_artwork"])
    if stats["missing_features"]:
        log_service.warning(f"⚠  Missing audio features: {len(stats['missing_features'])}")
        truncate_list(stats["missing_features"])
    if stats["missing_lyrics"]:
        log_service.warning(f"⚠  Missing lyric timestamps: {len(stats['missing_lyrics'])}")
        truncate_list(stats["missing_lyrics"])
    if stats["missing_webm"]:
        log_service.warning(f"⚠  Missing WebM variants: {len(stats['missing_webm'])}")
        truncate_list(stats["missing_webm"])
    if stats["missing_opus"]:
        log_service.warning(f"⚠  Missing Opus variants: {len(stats['missing_opus'])}")
        truncate_list(stats["missing_opus"])

    if stats["missing_metadata"]:
        log_service.error(f"✗ MP3s WITHOUT metadata: {len(stats['missing_metadata'])}")
        truncate_list(stats["missing_metadata"])
    if stats["orphaned_metadata"]:
        log_service.warning(f"⚠  Orphaned metadata: {len(stats['orphaned_metadata'])}")
        truncate_list(stats["orphaned_metadata"])

    total_issues = (
            len(stats['needs_audio_pipeline']) +
            len(stats['missing_artwork']) +
            len(stats['missing_features']) +
            len(stats['missing_lyrics']) +
            len(stats['missing_webm']) +
            len(stats['missing_opus'])
    )

    if total_issues == 0:
        log_service.batch_music("\n✓ System is in perfect condition!")
        await asyncio.sleep(0.5)
        return

    await asyncio.sleep(0.5)
    if input(f"\nProceed with Smart Repair for {total_issues} issues? (y/n): ").lower() != 'y':
        return

    if orchestrator is None:
        log_service.error("Orchestrator is not initialized")
        return

    repair_ids = set(stats["needs_audio_pipeline"])
    repair_ids.update(stats["missing_artwork"])
    repair_ids.update(stats["missing_features"])
    repair_ids.update(stats["missing_lyrics"])
    repair_ids.update(stats["missing_webm"])
    repair_ids.update(stats["missing_opus"])

    repair_list = list(repair_ids)
    log_service.batch_music(f"\nPreparing {len(repair_list)} tracks for Highway...")

    batch_data = []
    for tid in repair_list:
        meta = await metadata_service.load_metadata(tid)
        if meta:
            batch_data.append((tid, mp3_dir / f"{tid}.mp3", meta))

    jobs = await orchestrator.submit_batch(batch_data)

    log_service.batch_music("Monitoring progress...")
    while any(not j.is_complete() for j in jobs):
        await asyncio.sleep(2)

    log_service.batch_music("✓ Repair Complete")

async def validate_media_file(file_path: Path) -> bool:
    try:
        process = await asyncio.create_subprocess_exec(
            "C:/ffmpeg/bin/ffprobe.exe",
            "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(file_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            return False

        return len(stdout.decode().strip().split('\n')) >= 2
    except Exception as e:
        log_service.debug(f"Validation failed for {file_path}: {e}")
        return False

async def cleanup_failed_and_orphaned(metadata_service: MusicMetadata) -> None:
    log_service.batch_music("=" * 80)
    log_service.batch_music("CLEANUP SCAN")
    log_service.batch_music("=" * 80)

    to_delete = []

    log_service.batch_music("Scanning media files for 0-byte corruption...")
    media_dirs = [settings.AUDIO_DIR, settings.ENHANCED_WAV_DIR, settings.SONIC_WAV_DIR, settings.WAV_DIR]
    files_to_check = []

    for d in media_dirs:
        if d.exists():
            for media_file in d.glob("*"):
                if media_file.is_file():
                    if media_file.stat().st_size == 0:
                        to_delete.append(media_file)
                    else:
                        files_to_check.append(media_file)

    total_files = len(files_to_check)
    log_service.batch_music(f"Validating {total_files} media files (FFprobe)...")

    import os
    sem = asyncio.Semaphore(os.cpu_count() or 4)
    completed_count = 0

    async def check(file_to_check: Path) -> None:
        nonlocal completed_count
        async with sem:
            is_valid = await validate_media_file(file_to_check)
            if not is_valid:
                to_delete.append(file_to_check)

            completed_count += 1
            if completed_count % 25 == 0:
                log_service.batch_music(f"Validated {completed_count}/{total_files} files...")

    await asyncio.gather(*[check(fp) for fp in files_to_check])
    log_service.batch_music(f"Validated {total_files}/{total_files} files - Done.")

    log_service.batch_music("Scanning metadata...")
    mp3_ids = {audio_file.stem for audio_file in settings.AUDIO_DIR.glob("*.mp3")}

    for json_file in settings.METADATA_DIR.glob("*.json"):
        try:
            if json_file.stat().st_size == 0:
                to_delete.append(json_file)
                continue
            meta = await metadata_service.load_metadata(json_file.stem)
            if not meta:
                to_delete.append(json_file)
                continue
            status = meta.get("generation_status", "unknown") if meta else "unknown"
            if status in ["suno_failed", "upscaling_failed"]:
                to_delete.append(json_file)
            elif status == "unknown" and meta.get("id") not in mp3_ids:
                to_delete.append(json_file)
        except Exception as e:
            log_service.error(f"Error checking metadata {json_file}: {e}")
            to_delete.append(json_file)

    if not to_delete:
        log_service.batch_music("✓ Clean. No garbage found.")
        return

    log_service.batch_music(f"Found {len(to_delete)} garbage files.")
    await asyncio.sleep(0.5)
    if input("Delete all? (y/n): ").lower() == 'y':
        for f in to_delete:
            try:
                f.unlink()
            except OSError as e:
                log_service.error(f"Failed to delete {f}: {e}")
        log_service.batch_music("✓ Deleted.")

async def resume_incomplete_generations(
        metadata_service: MusicMetadata,
        background_service: SunoGenerationQueueService
) -> None:
    log_service.batch_music("Scanning for incomplete generations...")
    incomplete = []

    for json_file in metadata_service.metadata_dir.glob("*.json"):
        try:
            meta = await metadata_service.load_metadata(json_file.stem)
            if not meta:
                continue

            status = meta.get("generation_status")
            tid = meta.get("id")
            if status in ["params_generated", "suno_completed"] and not (
                    settings.ENHANCED_WAV_DIR / f"{tid}.wav").exists():
                incomplete.append(meta)
        except Exception as e:
            log_service.debug(f"Skipping corrupt metadata {json_file}: {e}")

    if not incomplete:
        log_service.batch_music("✓ No incomplete generations.")
        return

    truncate_list([m.get("generation_params", {}).get("title", "Unknown") for m in incomplete])

    await asyncio.sleep(0.5)
    if input(f"Resume {len(incomplete)} tracks? (y/n): ").lower() != 'y':
        return

    if background_service is None:
        log_service.error("Background service is not initialized")
        return

    background_service.reset_generation_stats()
    resume_tasks = []

    for meta in incomplete:
        try:
            user_request = meta.get("user_request", {}) if meta else {}
            generation_params = meta.get("generation_params", {}) if meta else {}
            _, tasks = await background_service.start_generation_job(
                session_id="resume", original_params={}, batch_count=1,
                user_id=None, source_track_id=None,
                user_request=user_request.get("original_text", ""),
                pregenerated_params=generation_params
            )
            resume_tasks.extend(tasks)
        except Exception as e:
            meta_id = meta.get('id') if meta else 'unknown'
            log_service.error(f"Failed to resume {meta_id}: {e}")
        await asyncio.sleep(1)

    if resume_tasks:
        log_service.batch_music(f"Waiting for {len(resume_tasks)} resume tasks to complete...")
        await asyncio.gather(*resume_tasks, return_exceptions=True)
        log_service.batch_music("✓ Resume operations complete.")

async def batch_generate_webm_variants() -> None:
    log_service.batch_music("Scanning catalog for missing variants...")

    from services.audio_transcoding_service import AudioTranscodingService
    transcoder = AudioTranscodingService()
    if not getattr(transcoder, '_initialized', False):
        await transcoder.initialize()

    targets = []
    wav_files = list(settings.ENHANCED_WAV_DIR.glob("*.wav"))

    for wav in wav_files:
        webm_path = transcoder.get_webm_path(wav.stem, "256k")
        if webm_path is None or not webm_path.exists():
            targets.append(wav.stem)

    if not targets:
        log_service.batch_music(f"✓ Scanned {len(wav_files)} tracks - All variants up to date.")
        return

    log_service.batch_music(f"Found {len(targets)} tracks needing variants.")
    await asyncio.sleep(0.5)
    if input("Generate missing variants? (y/n): ").lower() == 'y':
        await transcoder.batch_create_webm_variants(targets, ['128k', '192k', '256k'])
        log_service.batch_music("✓ Done.")

async def catalog_polish(
        metadata_service: MusicMetadata,
        orchestrator: SunoServiceOrchestrator,
        catalog_service: CatalogDatabaseService
) -> None:
    log_service.batch_music("=" * 80)
    log_service.batch_music("CATALOG POLISH - DATABASE TRACKS")
    log_service.batch_music("=" * 80)

    from services.audio_transcoding_service import AudioTranscodingService
    transcoding = AudioTranscodingService()

    if catalog_service is None:
        log_service.error("Catalog service is not initialized")
        return

    db_track_ids = catalog_service.track_ids

    if not db_track_ids:
        log_service.batch_music("⚠ No tracks found in database catalog.")
        return

    log_service.batch_music(f"Scanning {len(db_track_ids)} database tracks...")

    stats: Dict[str, Any] = {
        "total_tracks": len(db_track_ids),
        "missing_features": [],
        "missing_lyrics": [],
        "missing_artwork": [],
        "missing_enriched_artwork": [],
        "missing_webm": [],
        "missing_opus": [],
        "skipped_no_master": [],
    }

    for tid in db_track_ids:
        if not file_exists_and_valid(settings.ENHANCED_WAV_DIR / f"{tid}.wav"):
            stats["skipped_no_master"].append(tid)
            continue

        if not file_exists_and_valid(settings.ARTWORK_DIR / f"{tid}.jpeg"):
            stats["missing_artwork"].append(tid)

        if not file_exists_and_valid(settings.ARTWORK_ENRICHED_DIR / f"{tid}.jpeg"):
            stats["missing_enriched_artwork"].append(tid)

        check_track_missing_files(tid, stats, transcoding)

    await asyncio.sleep(0.1)
    log_service.batch_music("-" * 60)
    log_service.batch_music(f"Database Tracks:        {stats['total_tracks']}")
    log_service.batch_music("-" * 60)

    if stats["skipped_no_master"]:
        log_service.warning(f"⚠  Skipped (no master WAV): {len(stats['skipped_no_master'])}")
        truncate_list(stats["skipped_no_master"])

    categories = [
        ("missing_features", "Audio features"),
        ("missing_lyrics", "Lyric timestamps"),
        ("missing_artwork", "Artwork"),
        ("missing_enriched_artwork", "Enriched artwork"),
        ("missing_webm", "WebM variants"),
        ("missing_opus", "Opus variants"),
    ]

    for key, label in categories:
        count = len(stats[key])
        if count > 0:
            log_service.warning(f"⚠  Missing {label}: {count}")
            truncate_list(stats[key])
        else:
            log_service.batch_music(f"✓  {label}: OK")

    repair_ids = set()
    repair_ids.update(stats["missing_features"])
    repair_ids.update(stats["missing_lyrics"])
    repair_ids.update(stats["missing_artwork"])
    repair_ids.update(stats["missing_enriched_artwork"])
    repair_ids.update(stats["missing_webm"])
    repair_ids.update(stats["missing_opus"])

    if not repair_ids:
        log_service.batch_music("\n✓ Catalog is fully polished!")
        await asyncio.sleep(0.5)
        return

    log_service.batch_music(f"\n{len(repair_ids)} tracks need polishing.")
    await asyncio.sleep(0.5)
    if input("Proceed with polish? (y/n): ").lower() != 'y':
        return

    if orchestrator is None:
        log_service.error("Orchestrator is not initialized")
        return

    repair_list = list(repair_ids)
    log_service.batch_music(f"\nSubmitting {len(repair_list)} tracks to pipeline...")

    batch_data = []
    for tid in repair_list:
        meta = await metadata_service.load_metadata(tid)
        mp3_path = settings.AUDIO_DIR / f"{tid}.mp3"
        if meta and mp3_path.exists():
            batch_data.append((tid, mp3_path, meta))

    if not batch_data:
        log_service.error("No valid tracks to process.")
        return

    jobs = await orchestrator.submit_batch(batch_data)

    log_service.batch_music("Monitoring progress...")
    while any(not j.is_complete() for j in jobs):
        await asyncio.sleep(2)

    log_service.batch_music("✓ Catalog Polish Complete")

async def batch_enrich_artwork(artwork_enrichment) -> None:
    log_service.batch_music("=" * 80)
    log_service.batch_music("🎨 ARTWORK ENRICHMENT - 3D PARALLAX")
    log_service.batch_music("=" * 80)

    artwork_files = []
    if settings.ARTWORK_DIR.exists():
        artwork_files = list(settings.ARTWORK_DIR.glob("*.jpeg")) + list(settings.ARTWORK_DIR.glob("*.jpg"))

    if not artwork_files:
        log_service.batch_music("⚠️  No artwork found in catalog.")
        log_service.batch_music(f"   Expected location: {settings.ARTWORK_DIR}")
        log_service.batch_music("   Artwork will be enriched automatically when tracks are generated.")
        return

    already_enriched = 0
    if settings.ARTWORK_ENRICHED_DIR.exists():
        already_enriched = len(list(settings.ARTWORK_ENRICHED_DIR.glob("*.jpeg")))

    to_process = len(artwork_files) - already_enriched

    log_service.batch_music("📊 Artwork Status:")
    log_service.batch_music(f"   Total artwork:      {len(artwork_files)}")
    log_service.batch_music(f"   Already enriched:   {already_enriched}")
    log_service.batch_music(f"   Need processing:    {to_process}")
    log_service.batch_music("")
    log_service.batch_music("📦 Processing Info:")
    log_service.batch_music("   Model: Depth Anything V2 (ViT)")
    log_service.batch_music("   Output: Side-by-side JPEG [Color | Depth]")
    log_service.batch_music("   Quality: 90 (JPEG)")
    log_service.batch_music("   Concurrent: 2 jobs (adjust in code if needed)")
    log_service.batch_music("")

    if to_process == 0:
        log_service.batch_music("✅ All artwork already enriched!")
        return

    await asyncio.sleep(0.5)
    user_input = input(f"Process {to_process} artworks? (y/n): ").lower().strip()
    if user_input != 'y':
        log_service.batch_music("❌ Cancelled.")
        return

    if artwork_enrichment is None:
        log_service.error("Artwork enrichment service is not initialized")
        return

    log_service.batch_music("")
    log_service.batch_music("🚀 Starting batch enrichment...")

    stats = await artwork_enrichment.batch_enrich_catalog(
        max_concurrent=2,
        quality=90
    )

    log_service.batch_music("")
    log_service.batch_music("=" * 80)
    log_service.batch_music("📊 ENRICHMENT COMPLETE")
    log_service.batch_music("=" * 80)
    log_service.batch_music(f"✅ Success:  {stats['success']} artworks enriched")
    log_service.batch_music(f"⏭️  Skipped:  {stats['skipped']} already enriched")
    log_service.batch_music(f"❌ Failed:   {stats['failed']} errors")
    log_service.batch_music(f"📁 Total:    {stats['total']} processed")
    log_service.batch_music("")

    if stats['success'] > 0:
        log_service.batch_music(f"💾 Enriched artwork saved to: {settings.ARTWORK_ENRICHED_DIR}")
        log_service.batch_music("🎉 Test the 3D parallax effect in NowPlaying on mobile!")

    if stats['failed'] > 0:
        log_service.batch_music("⚠️  Some artworks failed - check logs for details")

    log_service.batch_music("")

async def batch_upscale_suno_artwork() -> None:
    log_service.batch_music("=" * 80)
    log_service.batch_music("🎨 SUNO ARTWORK UPSCALE - SDXL img2img")
    log_service.batch_music("=" * 80)

    from PIL import Image

    artwork_files = []
    if settings.ARTWORK_DIR.exists():
        artwork_files = list(settings.ARTWORK_DIR.glob("*.jpeg")) + list(settings.ARTWORK_DIR.glob("*.jpg"))

    if not artwork_files:
        log_service.batch_music("⚠️  No artwork found in catalog.")
        log_service.batch_music(f"   Expected location: {settings.ARTWORK_DIR}")
        return

    low_res_count = 0
    high_res_count = 0
    low_res_tracks = []

    log_service.batch_music(f"Scanning {len(artwork_files)} artwork files...")

    for art_file in artwork_files:
        if "_original" in art_file.stem:
            continue

        try:
            with Image.open(art_file) as img:
                width, height = img.size
                if width < 1024 or height < 1024:
                    low_res_count += 1
                    low_res_tracks.append(art_file.stem)
                else:
                    high_res_count += 1
        except Exception as e:
            log_service.warning(f"Could not read {art_file.name}: {e}")

    log_service.batch_music("")
    log_service.batch_music("📊 Artwork Resolution Status:")
    log_service.batch_music(f"   High-res (≥1024px): {high_res_count}")
    log_service.batch_music(f"   Low-res (<1024px):  {low_res_count}")
    log_service.batch_music("")
    log_service.batch_music("⚙️  Upscale Settings:")
    log_service.batch_music("   Model: SDXL Lightning (4-step img2img)")
    log_service.batch_music("   Output: 1024x1024 JPEG")
    log_service.batch_music("   Strength: 0.35 (preserves composition, adds detail)")
    log_service.batch_music("   Backup: Original saved as {track_id}_original.jpeg")
    log_service.batch_music("")

    if low_res_count == 0:
        log_service.batch_music("✅ All artwork is already high-resolution!")
        return

    await asyncio.sleep(0.5)
    strength_input = input(f"Strength (0.25-0.50, default 0.35, lower = more faithful): ").strip()
    strength = 0.35
    if strength_input:
        try:
            strength = float(strength_input)
            strength = max(0.1, min(0.6, strength))
        except ValueError:
            log_service.batch_music("Invalid input, using default 0.35")

    await asyncio.sleep(0.2)
    user_input = input(f"Upscale {low_res_count} low-res artworks? (y/n): ").lower().strip()
    if user_input != 'y':
        log_service.batch_music("❌ Cancelled.")
        return

    from services.artwork_generation_service import artwork_generation_service

    log_service.batch_music("")
    log_service.batch_music("🚀 Starting batch upscale...")
    log_service.batch_music(f"   Processing {low_res_count} tracks with strength={strength}")
    log_service.batch_music("")

    processed = 0
    for i, track_id in enumerate(low_res_tracks):
        log_service.batch_music(f"[{i+1}/{low_res_count}] Upscaling: {track_id}")

        source_path = settings.ARTWORK_DIR / f"{track_id}.jpeg"
        if not source_path.exists():
            source_path = settings.ARTWORK_DIR / f"{track_id}.jpg"

        result = await artwork_generation_service.upscale_artwork(
            track_id=track_id,
            source_image_path=source_path,
            strength=strength,
            quality=90
        )

        if result:
            processed += 1

        if (i + 1) % 10 == 0:
            log_service.batch_music(f"   Progress: {i+1}/{low_res_count} completed")

    log_service.batch_music("")
    log_service.batch_music("=" * 80)
    log_service.batch_music("📊 UPSCALE COMPLETE")
    log_service.batch_music("=" * 80)
    log_service.batch_music(f"✅ Successfully upscaled: {processed}")
    log_service.batch_music(f"❌ Failed: {low_res_count - processed}")
    log_service.batch_music("")
    log_service.batch_music(f"💾 Originals backed up as *_original.jpeg in {settings.ARTWORK_DIR}")
    log_service.batch_music("")

    await asyncio.sleep(0.5)
    enrich_input = input("Run depth enrichment on upscaled artwork? (y/n): ").lower().strip()
    if enrich_input == 'y':
        from services.suno_artwork_enrichment_service import ArtworkEnrichmentService
        enrichment = ArtworkEnrichmentService()
        await enrichment.initialize()
        await batch_enrich_artwork(enrichment)

async def show_main_menu() -> None:
    print("\n" + "=" * 80)
    print("AI RADIO - BATCH MUSIC GENERATOR")
    print("=" * 80)
    print("")
    print("[1] System Check & Repair (validate catalog + resume audio pipeline + fix missing files)")
    print("[2] Cleanup Failed & Orphaned Files (remove bad metadata)")
    print("[3] Resume Incomplete Generations (finish partial tracks)")
    print("[4] Generate New Music (fresh artist-based generation)")
    print("[5] Batch Generate Opus & WebM Variants (pre-generate all bitrates for streaming)")
    print("[6] Run Pipeline Test (Specific IDs or Random)")
    print("[7] Batch Enrich Artwork (generate depth maps for 3D parallax)")
    print("[8] Catalog Polish (fix missing features/lyrics/artwork for existing DB tracks)")
    print("[9] Upscale Suno Artwork (SDXL img2img 320px -> 1024px)")
    print("[0] Exit")
    print("")
    print("=" * 80)

async def main() -> None:
    await start_log_worker()
    init_db()

    metadata_service = MusicMetadata()
    if not getattr(metadata_service, '_initialized', False):
        await metadata_service.initialize()

    orchestrator: Optional[SunoServiceOrchestrator] = None
    background_service: Optional[SunoGenerationQueueService] = None
    catalog_service: Optional[CatalogDatabaseService] = None

    while True:
        await show_main_menu()
        choice = input("\nSelect operation: ").strip()

        if choice == "0":
            break

        elif choice == "1":
            if orchestrator is None:
                orchestrator = SunoServiceOrchestrator()
                if not getattr(orchestrator, '_initialized', False):
                    await orchestrator.initialize()
            await comprehensive_system_check_and_repair(metadata_service, orchestrator)

        elif choice == "2":
            await cleanup_failed_and_orphaned(metadata_service)

        elif choice == "3":
            if orchestrator is None:
                orchestrator = SunoServiceOrchestrator()
                if not getattr(orchestrator, '_initialized', False):
                    await orchestrator.initialize()
            if background_service is None:
                result = await initialize_generation_stack(metadata_service, orchestrator)
                if result is not None:
                    background_service, catalog_service = result
            if background_service is not None:
                await resume_incomplete_generations(metadata_service, background_service)

        elif choice == "4":
            log_service.batch_music("GENERATE NEW MUSIC")

            if orchestrator is None:
                orchestrator = SunoServiceOrchestrator()
                if not getattr(orchestrator, '_initialized', False):
                    await orchestrator.initialize()
            if background_service is None:
                result = await initialize_generation_stack(metadata_service, orchestrator)
                if result is not None:
                    background_service, catalog_service = result

            if background_service is None or catalog_service is None:
                log_service.error("Failed to initialize generation stack")
                continue

            batch_size = int(input("Jobs per artist (3): ") or 3)
            artist_count = int(input("Artist count (100): ") or 100)
            background_service.reset_generation_stats()

            existing_artists = catalog_service.get_repeated_artists()
            exclusion_prompt = ""
            if existing_artists:
                exclusion_list = list(existing_artists)[:100]
                exclusion_list_str = ", ".join(f'"{req}"' for req in exclusion_list)
                exclusion_prompt = f"\n\nEXCLUDE these {len(exclusion_list)} artists: [{exclusion_list_str}]"

            prompt = f"""Please generate a list of {artist_count} diverse artists and bands.
Guidelines:
- Focus on music from the last 30-40 years
- Include variety across all genres (rock, pop, electronic, hip-hop, jazz, indie, metal, folk, country)
- HEAVILY favor alternative, underground, cult, and counter-culture artists over mainstream acts
- Prioritize artists with raw, authentic, experimental, or unconventional sounds
- Include obscure bands, indie darlings, and artists that push boundaries
- Think: college radio, underground scenes, music critics' favorites, "your favorite band's favorite band"
- Lean toward artists with critical acclaim but limited commercial success
- Mix well-known alternative acts with deeper cuts and hidden gems
- Include solo artists and bands
- CRITICAL: Do NOT include any artists from the exclusion list below
Return ONLY JSON in this exact format:
{{"artists": ["Artist 1", "Artist 2", "Artist 3", ...]}}
No additional commentary.{exclusion_prompt}
"""

            log_service.batch_music("Requesting artists from Gemini...")
            try:
                if background_service.prompt_service is None:
                    log_service.error("Prompt service is not available")
                    continue
                result = await background_service.prompt_service.ai_service.call_gemini_structured(
                    prompt, ArtistList, temperature=0.8
                )

                if not result:
                    log_service.error("Gemini returned empty result.")
                    continue

                artist_list = result["artists"]
                log_service.batch_music(f"Received {len(artist_list)} artists.")
                truncate_list(artist_list)

                await asyncio.sleep(0.5)
                user_input = input("Proceed? (y/n): ").lower().strip()
                if user_input != 'y':
                    continue

                log_service.batch_music("\n🔍 VALIDATION: Generating first artist to show GPT prompt...")
                first_artist = artist_list[0]
                phrase_cycle_counter = 0

                repeated_titles = catalog_service.get_repeated_titles(min_occurrences=2)
                repeated_artists_for_prompt = catalog_service.get_repeated_artists()
                overused_phrases = catalog_service.get_cycled_phrases(
                    cycle_index=phrase_cycle_counter,
                    phrases_per_cycle=5,
                    top_n=20
                )

                log_service.batch_music("\n" + "=" * 40)
                log_service.batch_music("🚫 EXCLUSION LISTS (Active for this batch)")
                log_service.batch_music("=" * 40)

                if repeated_titles:
                    log_service.batch_music(f"1. Banned Titles ({len(repeated_titles)}):")
                    truncate_list(repeated_titles)
                else:
                    log_service.batch_music("1. Banned Titles: None (Catalog clean)")

                if repeated_artists_for_prompt:
                    log_service.batch_music(f"\n2. Banned Artists ({len(repeated_artists_for_prompt)}):")
                    truncate_list(list(repeated_artists_for_prompt))
                else:
                    log_service.batch_music("\n2. Banned Artists: None")

                if overused_phrases:
                    log_service.batch_music(f"\n3. Banned Phrases (Cycle {phrase_cycle_counter}):")
                    truncate_list(overused_phrases)
                else:
                    log_service.batch_music("\n3. Banned Phrases: None")
                log_service.batch_music("=" * 40 + "\n")

                first_music_params = await background_service.prompt_service.generate_music_params(
                    first_artist,
                    catalog_service=catalog_service,
                    repeated_titles=repeated_titles,
                    overused_phrases=overused_phrases,
                    log_prompt=True
                )

                if first_music_params:
                    log_service.batch_music("\n" + "=" * 40)
                    log_service.batch_music(f"🎹 GENERATED PROMPT FOR: {first_artist}")
                    log_service.batch_music("=" * 40)
                    log_service.batch_music(json.dumps(first_music_params, indent=2))
                    log_service.batch_music("=" * 40 + "\n")
                else:
                    log_service.error("❌ ERROR: No prompt parameters were generated!")

                log_service.batch_music("=" * 80)
                log_service.batch_music("✓ Validation complete - Banned phrases shown above")
                log_service.batch_music("=" * 80 + "\n")
                phrase_cycle_counter = (phrase_cycle_counter + 1) % 4

                await asyncio.sleep(0.5)
                user_input = input("Proceed with all? (y/n): ").lower().strip()
                if user_input != 'y':
                    continue

                all_tasks = []

                async def process_artist(name, idx, use_first_params=False):
                    nonlocal phrase_cycle_counter
                    if background_service is None or catalog_service is None:
                        return
                    if background_service.prompt_service is None:
                        return
                    if background_service.suno_credits_exhausted:
                        return

                    try:
                        if use_first_params and first_music_params:
                            music_params = first_music_params
                        else:
                            loop_repeated_titles = catalog_service.get_repeated_titles(min_occurrences=2)
                            loop_overused_phrases = catalog_service.get_cycled_phrases(
                                cycle_index=phrase_cycle_counter,
                                phrases_per_cycle=5,
                                top_n=20
                            )
                            phrase_cycle_counter = (phrase_cycle_counter + 1) % 4

                            music_params = await background_service.prompt_service.generate_music_params(
                                name,
                                catalog_service=catalog_service,
                                repeated_titles=loop_repeated_titles,
                                overused_phrases=loop_overused_phrases,
                                log_prompt=False
                            )

                        if not music_params:
                            return

                        uid = metadata_service.generate_unique_id()
                        new_track_meta = await metadata_service.create_metadata(name, music_params, None, uid,
                                                                                "params_generated")
                        await metadata_service.save_metadata(new_track_meta, uid)

                        _, artist_tasks = await background_service.start_generation_job(
                            "batch", {}, batch_size, None, None, name, music_params
                        )
                        all_tasks.extend(artist_tasks)
                        log_service.batch_music(f"[{idx}] Queued: {name}")
                    except Exception as gen_err:
                        log_service.error(f"Failed {name}: {gen_err}")

                tasks = [process_artist(a, i + 1, (i == 0)) for i, a in enumerate(artist_list)]
                await asyncio.gather(*tasks, return_exceptions=True)

                log_service.batch_music(f"Processing {len(all_tasks)} Suno tasks...")
                await asyncio.gather(*all_tasks, return_exceptions=True)
                await background_service.print_generation_summary()

            except Exception as e:
                log_service.error(f"Gen Error: {e}")

        elif choice == "5":
            await batch_generate_webm_variants()

        elif choice == "6":
            if orchestrator is None:
                orchestrator = SunoServiceOrchestrator()
                if not getattr(orchestrator, '_initialized', False):
                    await orchestrator.initialize()

            track_input = input("Enter track IDs (comma-separated) or leave blank for random: ").strip()
            test_jobs = []

            if track_input:
                raw_ids = [tid.strip() for tid in track_input.split(",") if tid.strip()]
                track_ids = [tid[:-4] if tid.lower().endswith(".mp3") else tid for tid in raw_ids]

                valid_tracks = []
                invalid_ids = []

                for tid in track_ids:
                    mp3_path = settings.AUDIO_DIR / f"{tid}.mp3"
                    if mp3_path.exists():
                        valid_tracks.append((tid, mp3_path))
                    else:
                        invalid_ids.append(tid)

                if invalid_ids:
                    log_service.warning(f"⚠ Invalid/missing IDs: {', '.join(invalid_ids)}")

                if not valid_tracks:
                    log_service.error("No valid tracks found.")
                    continue

                log_service.batch_music(f"Processing {len(valid_tracks)} specific track(s)...")

                for tid, mp3_path in valid_tracks:
                    meta = await metadata_service.load_metadata(tid)
                    if meta:
                        if orchestrator is None:
                            log_service.error("Orchestrator is not initialized")
                            continue
                        job = await orchestrator.submit_track(tid, mp3_path, meta)
                        test_jobs.append(job)
                        log_service.batch_music(f"  Submitted: {tid}")
                    else:
                        log_service.warning(f"  ⚠ No metadata for: {tid}")
            else:
                count = int(input("Random count (3): ") or 3)
                mp3s = list(settings.AUDIO_DIR.glob("*.mp3"))

                if mp3s:
                    for mp3 in random.sample(mp3s, min(count, len(mp3s))):
                        meta = await metadata_service.load_metadata(mp3.stem)
                        if meta:
                            if orchestrator is None:
                                log_service.error("Orchestrator is not initialized")
                                continue
                            job = await orchestrator.submit_track(mp3.stem, mp3, meta)
                            test_jobs.append(job)

            if test_jobs:
                log_service.batch_music(f"Monitoring {len(test_jobs)} test jobs...")
                while any(not j.is_complete() for j in test_jobs):
                    await asyncio.sleep(2)
                log_service.batch_music("✓ Test jobs complete.")
            elif not track_input:
                log_service.batch_music("No MP3s found in catalog.")

        elif choice == "7":
            if orchestrator is None:
                orchestrator = SunoServiceOrchestrator()
            if not getattr(orchestrator, '_initialized', False):
                await orchestrator.initialize()
            # Fix: artwork_enrichment might be None if singleton was created before this attribute was added
            if orchestrator.artwork_enrichment is None:
                from services.suno_artwork_enrichment_service import ArtworkEnrichmentService
                orchestrator.artwork_enrichment = ArtworkEnrichmentService()
                await orchestrator.artwork_enrichment.initialize()
            if orchestrator.artwork_enrichment is not None:
                await batch_enrich_artwork(orchestrator.artwork_enrichment)
            else:
                log_service.error("Artwork enrichment is not available")

        elif choice == "8":
            if orchestrator is None:
                orchestrator = SunoServiceOrchestrator()
                if not getattr(orchestrator, '_initialized', False):
                    await orchestrator.initialize()
            if catalog_service is None:
                catalog_service = CatalogDatabaseService()
                if not getattr(catalog_service, '_initialized', False):
                    await catalog_service.initialize()
            if orchestrator is not None and catalog_service is not None:
                await catalog_polish(metadata_service, orchestrator, catalog_service)
            else:
                log_service.error("Required services are not initialized")

        elif choice == "9":
            await batch_upscale_suno_artwork()

    await stop_worker()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")