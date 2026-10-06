import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services import log_service
from services.audio_stage_registry import stems_dir_for
from config import settings
from models_global import GPUOutOfMemoryError

OPUS_BITRATES: Tuple[str, ...] = ("128k", "192k", "256k")
CATALOG_MP3_BITRATE = "192k"
CATALOG_AUDIO_EXTENSIONS: Tuple[str, ...] = ('.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.opus', '.webm')
ORIGINAL_UPLOAD_SUFFIXES: Tuple[str, ...] = (
    '.wav', '.flac', '.mp3', '.ogg', '.m4a', '.aac', '.opus', '.webm', '.mp4', '.mov', '.m4v', '.mkv', '.avi'
)


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    if isinstance(value, (int, float)):
        return value != 0
    return False


def metadata_path(track_id: str) -> Path:
    return settings.METADATA_DIR / f"{track_id}.json"


def stamp_master_version(track_id: str):
    path = metadata_path(track_id)
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["master_chain_version"] = settings.MASTER_CHAIN_VERSION
    metadata["master_rendered_at"] = datetime.now(timezone.utc).isoformat()
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def master_wav_path(track_id: str) -> Path:
    return settings.ENHANCED_WAV_DIR / f"{track_id}.wav"


def catalog_mp3_path(track_id: str) -> Path:
    return settings.AUDIO_DIR / f"{track_id}.mp3"


def artwork_path(track_id: str) -> Path:
    return settings.ARTWORK_DIR / f"{track_id}.jpeg"


def enriched_artwork_path(track_id: str) -> Path:
    return settings.ARTWORK_ENRICHED_DIR / f"{track_id}.jpeg"


def audio_features_path(track_id: str) -> Path:
    return settings.AUDIOFEATURES_DIR / f"{track_id}.json"


def lyric_timestamps_path(track_id: str) -> Path:
    return settings.LYRIC_TIMESTAMPS_DIR / f"{track_id}.json"


def opus_path(track_id: str, bitrate: str = "192k") -> Path:
    if bitrate == "256k":
        return settings.OPUS_256K_DIR / f"{track_id}.opus"
    if bitrate == "128k":
        return settings.OPUS_128K_DIR / f"{track_id}.opus"
    return settings.OPUS_192K_DIR / f"{track_id}.opus"


def webm_path(track_id: str, bitrate: str = "192k") -> Path:
    return opus_path(track_id, bitrate).parent / "webm" / f"{track_id}.webm"


def demucs_vocal_stem(track_id: str) -> Optional[Path]:
    for stems_dir in dict.fromkeys((stems_dir_for(track_id), settings.DEMUCS_STEMS_DIR / track_id)):
        for name in ("vocals_enhanced.wav", "vocals.wav"):
            candidate = stems_dir / name
            try:
                if candidate.is_file() and candidate.stat().st_size > 4096:
                    return candidate
            except OSError:
                continue
    return None


def uploaded_original_path(track_id: str, metadata: Dict[str, Any]) -> Optional[Path]:
    user_id = metadata.get("uploaded_by_user_id")
    if user_id is None:
        return None
    tracks_dir = settings.USERS_DIR / str(user_id) / "tracks"
    for suffix in ORIGINAL_UPLOAD_SUFFIXES:
        candidate = tracks_dir / f"{track_id}_original{suffix}"
        if candidate.is_file():
            return candidate
    return None


def track_is_instrumental(metadata: Dict[str, Any]) -> bool:
    return coerce_bool((metadata.get("generation_params") or {}).get("instrumental", False))


def track_lyrics_text(metadata: Dict[str, Any]) -> str:
    return ((metadata.get("generation_params") or {}).get("prompt") or "").strip()


def clean_lyrics_text(prompt: str) -> str:
    clean_text = re.sub(r'\[.*?]', '', prompt or "")
    clean_text = re.sub(r'\(.*?\)', '', clean_text)
    clean_text = re.sub(r'\n\s*\n', '\n', clean_text)
    return clean_text.strip()


def track_has_sung_lyrics(metadata: Dict[str, Any]) -> bool:
    if track_is_instrumental(metadata):
        return False
    return bool(re.search(r'\w', clean_lyrics_text(track_lyrics_text(metadata))))


def fallback_artwork_prompt(metadata: Dict[str, Any]) -> Optional[str]:
    params = metadata.get("generation_params") or {}
    derived = metadata.get("derived_tags") or {}
    genre = derived.get("primary_genre") or ""
    secondary = [g for g in (derived.get("secondary_genres") or []) if isinstance(g, str)][:2]
    moods = [m for m in (derived.get("mood_keywords") or []) if isinstance(m, str)][:3]
    style = params.get("style_canonical") or params.get("style") or ""
    theme = (derived.get("lyrical_interpretation") or "").strip()
    if not (genre or moods or style or theme):
        return None
    parts = ["Album cover artwork"]
    genres = ", ".join([g for g in [genre, *secondary] if g])
    if genres:
        parts.append(f"for a {genres} song")
    if moods:
        parts.append(f"{', '.join(moods)} atmosphere")
    if theme:
        parts.append(theme.split(".")[0][:160])
    elif style:
        parts.append(str(style)[:120])
    parts.append("striking composition, rich color, cinematic lighting, highly detailed, no text")
    return ", ".join(parts)


def artwork_request(metadata: Dict[str, Any]) -> Dict[str, Any]:
    params = metadata.get("generation_params") or {}
    derived = metadata.get("derived_tags") or {}
    info = metadata.get("track_info") or {}
    mood_keywords = derived.get("mood_keywords") or []
    style_keywords = derived.get("style_keywords") or []
    return {
        "title": params.get("title") or info.get("title") or "Untitled",
        "primary_artist": info.get("artist") or params.get("artist_name") or derived.get("inspired_artist") or "Unknown",
        "primary_genre": derived.get("primary_genre"),
        "mood": mood_keywords[0] if mood_keywords else None,
        "style": style_keywords[0] if style_keywords else None,
        "artwork_prompt": metadata.get("artwork_prompt") or fallback_artwork_prompt(metadata),
    }


async def create_catalog_mp3(transcoding_service, track_id: str, source: Path) -> bool:
    output = catalog_mp3_path(track_id)
    created = await transcoding_service.convert_to_mp3(
        input_path=source,
        output_path=output,
        bitrate=CATALOG_MP3_BITRATE
    )
    return bool(created) and await asyncio.to_thread(output.exists)


async def create_opus_variant(transcoding_service, track_id: str, bitrate: str, source: Path) -> bool:
    output = opus_path(track_id, bitrate)
    created = await transcoding_service.transcode_to_opus(
        input_path=source,
        output_path=output,
        bitrate=bitrate
    )
    return bool(created) and await asyncio.to_thread(output.exists)


async def create_opus_variants(transcoding_service, track_id: str, source: Path,
                               bitrates: Tuple[str, ...] = OPUS_BITRATES) -> Dict[str, bool]:
    results = {}
    for bitrate in bitrates:
        results[bitrate] = await create_opus_variant(transcoding_service, track_id, bitrate, source)
    return results


async def extract_audio_features(features_service, track_id: str, source: Path) -> Optional[Dict[str, Any]]:
    return await features_service.analyze_audio(audio_path=source, track_id=track_id, save=True)


async def generate_lyric_timestamps(lyric_service, track_id: str, metadata: Dict[str, Any],
                                    audio_path: Optional[Path]) -> Optional[Dict[str, Any]]:
    return await lyric_service.generate_timestamps(
        track_id=track_id,
        metadata=metadata,
        audio_path=audio_path,
        save=True
    )


async def ensure_track_artwork(
    track_id: str,
    metadata: Dict[str, Any],
    embedded_artwork_service=None,
    original_path: Optional[Path] = None,
    suno_service=None,
    artwork_generation_service=None,
    allow_generation: bool = True
) -> Tuple[bool, str]:
    target = artwork_path(track_id)
    if await asyncio.to_thread(target.exists):
        return True, "existing"

    if embedded_artwork_service and original_path is not None and await asyncio.to_thread(original_path.exists):
        try:
            found, _path = await embedded_artwork_service.extract_and_save_for_track(
                audio_path=original_path,
                track_id=track_id
            )
            if found:
                return True, "embedded"
        except Exception as e:
            log_service.warning(f"[Artwork] Embedded artwork extraction failed for {track_id}: {e}")

    image_url = (metadata.get("track_info") or {}).get("image_url")
    if suno_service and image_url:
        try:
            if await suno_service.download_image(image_url, target) and await asyncio.to_thread(target.exists):
                return True, "downloaded"
        except Exception as e:
            log_service.warning(f"[Artwork] Artwork download failed for {track_id}: {e}")

    if allow_generation and artwork_generation_service:
        request = artwork_request(metadata)
        if not request.get("artwork_prompt"):
            return False, ""
        try:
            generated = await artwork_generation_service.generate_artwork_for_track(track_id=track_id, metadata=request)
            if generated and await asyncio.to_thread(Path(generated).exists):
                return True, "generated"
        except GPUOutOfMemoryError:
            raise
        except Exception as e:
            log_service.warning(f"[Artwork] Artwork generation failed for {track_id}: {e}")

    return False, ""


async def enrich_track_artwork(enrichment_service, track_id: str) -> bool:
    enriched = await enrichment_service.enrich_artwork(track_id)
    return bool(enriched)


def catalog_output_paths(track_id: str) -> List[Path]:
    paths = [
        metadata_path(track_id),
        *(settings.AUDIO_DIR / f"{track_id}{ext}" for ext in CATALOG_AUDIO_EXTENSIONS),
        master_wav_path(track_id),
        audio_features_path(track_id),
        lyric_timestamps_path(track_id),
        artwork_path(track_id),
        settings.ARTWORK_DIR / f"{track_id}.jpg",
        enriched_artwork_path(track_id),
    ]
    for bitrate in OPUS_BITRATES:
        paths.append(opus_path(track_id, bitrate))
        paths.append(webm_path(track_id, bitrate))
    return paths
