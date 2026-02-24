import json
import aiofiles
import aiofiles.os
from typing import Dict, Any, Optional, List
from pathlib import Path
from datetime import datetime, timezone
from pydantic import BaseModel, Field

from services import log_service
from services.base_service import SingletonService
from services.youtube_clip_service import VIDEO_SEARCH_TERMS_PROMPT
from config import settings

class DerivedTags(BaseModel):

    primary_genre: str = Field(
        description="Primary genre tag (e.g., 'Synth-Pop', 'Hip-Hop', 'Jazz')"
    )

    secondary_genres: List[str] = Field(
        default_factory=list,
        description="2-4 secondary genre tags for discoverability"
    )

    inspired_artist: Optional[str] = Field(
        default=None,
        description="Artist explicitly referenced in style description"
    )

    mood_keywords: List[str] = Field(
        default_factory=list,
        description="3-6 mood descriptors from style/prompt"
    )

    lyrical_interpretation: Optional[str] = Field(
        default=None,
        description="1-2 sentence thematic summary of lyrics"
    )

    vocal_style_keywords: List[str] = Field(
        default_factory=list,
        description="Technical vocal production terms"
    )

    similar_artists: List[str] = Field(
        default_factory=list,
        description="4-6 stylistically similar artists for discovery"
    )

    video_search_terms: List[str] = Field(
        default_factory=list,
        description="8-12 YouTube search queries for background video art that matches the song's mood, theme, and energy. More terms = more visual variety."
    )

class EnrichedMetadataService(SingletonService):

    def __init__(self, ai_service=None):
        super().__init__()
        if self._initialized:
            return

        self.metadata_dir = settings.METADATA_DIR
        self.enriched_dir = settings.CATALOG_DIR / "enriched_metadata"
        self.ai_service = ai_service
        self._service_initialized = False
        self._initialized = True

    async def initialize(self):
        if self._service_initialized:
            log_service.system("EnrichedMetadataService already initialized")
            return

        self._service_initialized = True
        log_service.system("EnrichedMetadataService initialized")

    async def _generate_canonical_style(
        self,
        obfuscated_style: str,
        user_request: str
    ) -> str:

        if not obfuscated_style:
            return ""

        rewrite_prompt = f"""Rewrite this music style description to use proper artist/band names and correct spelling.

**Original User Request**: {user_request}

**Obfuscated Style Description**: {obfuscated_style}

**Task**:
- Fix any intentionally misspelled artist/band names (e.g., "Blud Orang" → "Blood Orange")
- Keep all technical details, BPM, instruments, production notes EXACTLY the same
- Only fix artist name spelling/formatting - don't change anything else
- Return ONLY the corrected style description, nothing else

Example:
Input: "80s-inspired indie R&B in the style of Blud Orang..."
Output: "80s-inspired indie R&B in the style of Blood Orange..."
"""

        try:
            result = await self.ai_service.call_gemini(
                prompt=rewrite_prompt,
                model="gemini-2.5-flash-lite",
                temperature=0.1,
                system_instruction="You are a text correction assistant. Fix only artist name spelling while preserving all other details."
            )

            return result.strip() if result else obfuscated_style

        except Exception as e:
            log_service.warning(f"Failed to generate canonical style: {str(e)}")
            return obfuscated_style

    def _build_enrichment_prompt(
        self,
        metadata: Dict[str, Any],
        canonical_style: str = None
    ) -> str:
        gen_params = metadata.get("generation_params", {})
        track_info = metadata.get("track_info", {})
        user_request = metadata.get("user_request", {})

        style = canonical_style or gen_params.get("style_canonical") or gen_params.get("style", "")
        prompt = gen_params.get("prompt", "")
        title = gen_params.get("title", track_info.get("title", ""))
        instrumental = gen_params.get("instrumental", False)
        original_request = user_request.get("original_text", "")
        target_artist = gen_params.get("artist_name") or user_request.get("artist_name") or ""

        prompt_text = f"""Analyze this music track and derive catalog metadata tags.

**Original User Request**: {original_request}
**Target Artist**: {target_artist}
**Track Title**: {title}
**Style Description**: {style}
**Creative Prompt**: {prompt}
**Instrumental**: {"Yes" if instrumental else "No"}

---

**Task**: Extract structured metadata following these rules:

1. **primary_genre**: Single most accurate genre (e.g., "Synth-Pop", "Hip-Hop", "Jazz")

2. **secondary_genres**: 2-4 additional genre tags for discoverability
   - Include sub-genres that capture mood/style nuances
   - Examples: For dark electronic → ["Electronic", "Darkwave", "Electropop"]

3. **inspired_artist**: Artist name if EXPLICITLY mentioned in style description
   - Only include if style says "in the style of X" or "like X"
   - Return null if no specific artist reference

4. **mood_keywords**: 3-6 descriptive mood tags from style/prompt
   - Pull from actual text: "dark", "energetic", "melancholic", "hypnotic"
   - Focus on emotional/atmospheric qualities

5. **lyrical_interpretation**: 1-2 sentence thematic summary
   - Analyze themes, narrative, emotional arc from the creative prompt
   - Return null if instrumental or no lyrical content described

6. **vocal_style_keywords**: Technical production terms
   - Extract from prompt: "Vocoder", "Pitch-Shifted", "Layered", "Auto-Tuned"
   - Look for bracketed technical notes like [Low Pitch Shifted Vocals]
   - Empty array if instrumental

7. **similar_artists**: 4-6 artists with similar style/genre
   - Base on primary/secondary genres and mood
   - Ensure stylistic consistency with the track
   - Examples: For "Dark Swedish Synth-Pop" → ["Fever Ray", "Austra", "Ladytron"]

{VIDEO_SEARCH_TERMS_PROMPT}

**Important**:
- Be concise but accurate
- If Target Artist is present, inspired_artist should NOT be null
- Use existing style/prompt text as primary source
- Don't invent information not supported by the input
- For minimal metadata, do your best with available information"""

        return prompt_text

    async def enrich_metadata(
        self,
        metadata: Dict[str, Any],
        temperature: float = 0.3
    ) -> Optional[Dict[str, Any]]:

        try:
            enriched = metadata.copy()
            gen_params = enriched.get("generation_params", {})
            user_request = enriched.get("user_request", {})

            canonical_style = gen_params.get("style_canonical")
            if not canonical_style:
                obfuscated_style = gen_params.get("style", "")
                original_request = user_request.get("original_text", "")

                if obfuscated_style:
                    log_service.info(f"Generating canonical style for: {metadata.get('id', 'unknown')}")
                    canonical_style = await self._generate_canonical_style(
                        obfuscated_style,
                        original_request
                    )
                    if "generation_params" not in enriched:
                        enriched["generation_params"] = {}
                    enriched["generation_params"]["style_canonical"] = canonical_style

            prompt = self._build_enrichment_prompt(enriched, canonical_style)

            log_service.info(f"Enriching metadata for: {metadata.get('id', 'unknown')}")

            result = await self.ai_service.call_gemini_structured(
                prompt=prompt,
                response_schema=DerivedTags,
                model="gemini-2.5-flash-lite",
                temperature=temperature,
                system_instruction="You are a music cataloging expert. Extract precise, consistent metadata tags."
            )

            if not result:
                log_service.error(f"Failed to enrich metadata: {metadata.get('id')}")
                return None

            if "track_info" in enriched:
                if "tags" in enriched["track_info"]:
                    del enriched["track_info"]["tags"]
                if "prompt" in enriched["track_info"]:
                    del enriched["track_info"]["prompt"]

            enriched["derived_tags"] = result
            enriched["derived_tags"]["enriched_at"] = datetime.now(timezone.utc).isoformat()

            log_service.success(f"Enriched: {metadata.get('id')} → {result['primary_genre']}")

            return enriched

        except Exception as e:
            log_service.error(f"Error enriching metadata: {str(e)}")
            return None

    async def save_enriched_metadata(
        self,
        enriched_metadata: Dict[str, Any],
        overwrite_original: bool = False
    ) -> Optional[Path]:

        temp_filepath: Optional[Path] = None
        try:
            unique_id = enriched_metadata.get("id")
            if not unique_id:
                log_service.error("Cannot save enriched metadata: missing id")
                return None

            target_dir = self.metadata_dir if overwrite_original else self.enriched_dir
            filepath = target_dir / f"{unique_id}.json"
            temp_filepath = target_dir / f"{unique_id}.json.tmp"

            json_content = json.dumps(enriched_metadata, indent=2, ensure_ascii=False)

            async with aiofiles.open(temp_filepath, 'w', encoding='utf-8') as f:
                await f.write(json_content)
                await f.flush()

            try:
                await aiofiles.os.replace(temp_filepath, filepath)
            except Exception as e:
                log_service.warning(f"Async replace failed, using sync: {str(e)}")
                import os
                os.replace(str(temp_filepath), str(filepath))

            mode = "OVERWRITE" if overwrite_original else "TEST MODE"
            log_service.success(f"[{mode}] Saved enriched metadata: {filepath.name}")

            return filepath

        except Exception as e:
            log_service.error(f"Error saving enriched metadata: {str(e)}")
            if temp_filepath and temp_filepath.exists():
                try:
                    await aiofiles.os.remove(temp_filepath)
                except Exception as cleanup_error:
                    log_service.warning(f"Failed to cleanup temp file: {str(cleanup_error)}")
            return None

    async def load_metadata(self, unique_id: str) -> Optional[Dict[str, Any]]:

        filepath = self.metadata_dir / f"{unique_id}.json"

        try:
            async with aiofiles.open(filepath, 'r', encoding='utf-8') as f:
                content = await f.read()
                if not content or content.strip() == "":
                    log_service.warning(f"Empty metadata file: {filepath.name}")
                    return None
                return json.loads(content)

        except FileNotFoundError:
            return None
        except json.JSONDecodeError:
            log_service.warning(f"Invalid JSON in metadata: {filepath.name}")
            return None
        except Exception as e:
            log_service.error(f"Error loading metadata {unique_id}: {str(e)}")
            return None

    def is_already_enriched(self, metadata: Dict[str, Any]) -> bool:
        return "derived_tags" in metadata and metadata["derived_tags"]

    async def get_unenriched_files(self) -> List[Path]:

        all_files = list(self.metadata_dir.glob("*.json"))
        unenriched = []

        for json_path in all_files:
            try:
                async with aiofiles.open(json_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    metadata = json.loads(content)

                    if not self.is_already_enriched(metadata):
                        unenriched.append(json_path)

            except Exception as e:
                log_service.warning(f"Error checking enrichment status for {json_path.name}: {str(e)}")
                continue

        return unenriched

    def needs_video_search_terms(self, metadata: Dict[str, Any]) -> bool:
        gen_params = metadata.get("generation_params", {})
        if gen_params.get("video_search_terms"):
            return False

        derived_tags = metadata.get("derived_tags", {})
        if derived_tags.get("video_search_terms"):
            return False

        return True

    async def get_files_needing_video_terms(self) -> List[Path]:
        all_files = list(self.metadata_dir.glob("*.json"))
        needs_video = []

        for json_path in all_files:
            try:
                async with aiofiles.open(json_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    metadata = json.loads(content)

                    if self.is_already_enriched(metadata) and self.needs_video_search_terms(metadata):
                        needs_video.append(json_path)

            except Exception as e:
                log_service.warning(f"Error checking video terms for {json_path.name}: {str(e)}")
                continue

        return needs_video
