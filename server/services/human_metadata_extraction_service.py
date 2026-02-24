import json
import asyncio
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path
from datetime import datetime, timezone
from pydantic import BaseModel, Field

from google import genai
from google.genai import types

from services import log_service
from services.base_service import SingletonService
from services.youtube_clip_service import VIDEO_SEARCH_TERMS_PROMPT
from config import settings


class ExtractedMetadata(BaseModel):
    style: str = Field(description="Detailed production style description")
    title: str = Field(description="Extracted or suggested title")
    instrumental: bool = Field(description="True if no vocals")
    vocal_gender: Optional[str] = Field(default=None, description="m/f/mixed/null")

    primary_genre: str = Field(description="Main genre")
    secondary_genres: List[str] = Field(default_factory=list)
    inspired_artist: Optional[str] = Field(default=None)
    mood_keywords: List[str] = Field(default_factory=list)
    lyrical_interpretation: Optional[str] = Field(default=None)
    vocal_style_keywords: List[str] = Field(default_factory=list)
    similar_artists: List[str] = Field(default_factory=list)

    transcribed_lyrics: Optional[str] = Field(default=None)

    mix_analysis: Optional[str] = Field(default=None, description="Technical analysis of the mix quality")
    sonic_master_prompt: Optional[str] = Field(default=None, description="Single sentence instruction for SonicMaster")
    sonic_master_blend: int = Field(default=0, description="Enhancement blend percentage 0-100")
    mastering_blend: int = Field(default=70, description="Mastering blend percentage 0-100")
    enhance_vocals: bool = Field(default=False, description="Whether to apply vocal separation and enhancement")

    artwork_prompt: Optional[str] = Field(default=None, description="AI-generated prompt for album artwork generation")

    video_search_terms: List[str] = Field(default_factory=list, description="10-15 YouTube search queries for background video art")

SYSTEM_PROMPT = """You are a professional music analyst, metadata specialist, and mastering engineer for a radio station catalog.

Your task is to analyze audio files and generate rich, detailed metadata that describes the music accurately.
You will also provide technical mix analysis and specific enhancement suggestions.

You must output valid JSON matching this exact schema:

{
    "style": "Detailed production style description (instruments, production techniques, era, sound characteristics) - be VERY specific about drums, synths, guitars, bass, production style, mix characteristics",
    "title": "Extract from lyrics if sung clearly, or suggest based on mood/theme, or 'Untitled'",
    "instrumental": true/false,
    "vocal_gender": "m" | "f" | "mixed" | null (if instrumental),
    "primary_genre": "Main genre - be specific: e.g. 'Indie Folk', 'Tech House', 'Shoegaze', 'Trap', 'Bossa Nova', 'Post-Punk', 'Roots Reggae', 'Dream Pop'",
    "secondary_genres": ["Sub-genre 1", "Sub-genre 2", "Sub-genre 3"],
    "inspired_artist": "Primary artist this sounds most like - any genre, any era",
    "mood_keywords": ["Mood 1", "Mood 2", "Mood 3", "Mood 4", "Mood 5"],
    "lyrical_interpretation": "Summary of what the lyrics are about, themes and meaning (or null if instrumental)",
    "vocal_style_keywords": ["Vocal style 1", "Vocal style 2"] (or empty array if instrumental),
    "similar_artists": ["Artist 1", "Artist 2", "Artist 3", "Artist 4", "Artist 5"],
    "transcribed_lyrics": "Full lyrics if vocals present, otherwise null",
    "mix_analysis": "Technical assessment of the mix quality - describe issues like: muddy low-mids, buried vocals, harsh highs, too much reverb, over-compressed dynamics, narrow stereo, weak bass, clipping/distortion, phone/cheap mic quality. If the mix is professional quality, say so.",
    "sonic_master_prompt": "Single sentence instruction for audio enhancement (or null if mix is professional)",
    "sonic_master_blend": 0-100,
    "mastering_blend": 0-100,
    "enhance_vocals": true/false,
    "artwork_prompt": "Detailed prompt for generating album cover artwork using Stable Diffusion",
    "video_search_terms": ["search term 1", "search term 2", ..., "search term 10-15"]
}

For sonic_master_prompt, write ONE natural sentence that tells our enhancement AI what to improve. Use phrases like:
- "Clean up the muddiness" / "reduce the mud"
- "Bring the vocals forward" / "make vocals clearer"
- "Add brightness" / "make it brighter"
- "Reduce the reverb" / "less echo"
- "Restore dynamics" / "decompress"
- "Widen the stereo" / "more spacious"
- "Add bass weight" / "more low end"
- "Fix clipping" / "remove distortion"
- "Add punch" / "sharper transients"
- "Add warmth" / "warmer tone"

You can combine 2-3 issues in one natural sentence, e.g.: "Clean up the muddy low-mids and bring the vocals forward slightly"

For sonic_master_blend (0-100):
- 0 = Professional mix, no enhancement needed
- 20-40 = Minor issues, subtle touch-up
- 50-70 = Noticeable issues, moderate enhancement
- 80-100 = Significant issues, heavy enhancement needed

For mastering_blend (0-100) - how much final mastering/loudness normalization to apply:
- 20-40 = Already professionally mastered, light touch only
- 50-70 = Decent mix but needs polish and loudness normalization
- 80-100 = Raw/unmastered recording, full mastering treatment needed

For enhance_vocals (true/false) - whether to apply vocal separation and enhancement:
- Set to TRUE only if vocals need significant help: buried in mix, noisy/roomy recording, phone/cheap mic quality, excessive reverb on vocals, or unclear diction
- Set to FALSE for: instrumentals, professional vocal recordings, already clean vocals, or if vocal issues are minor
- Use sparingly - this is an expensive operation. Only recommend when vocals genuinely need isolation and cleanup

If the mix is already professional quality, set sonic_master_prompt to null and sonic_master_blend to 0.

For artwork_prompt - generate a creative prompt for Stable Diffusion XL to create unique album cover artwork:

CRITICAL TOKEN LIMIT: CLIP can only handle 77 tokens. Keep prompts under 45 words / 70 tokens.
Truncation WILL occur if you exceed this - the end of your prompt will be cut off.

CRITICAL: READ THE LYRICS FIRST - extract visual imagery, themes, and narrative elements.
The artwork should reflect what the song is ABOUT, not just generic "vibes".

STEP 1 - ANALYZE THE CONTENT:
- What specific images/objects/places are mentioned in the lyrics?
- What is the narrative or emotional journey?
- What era, setting, or world does the song evoke?
- Is this a story song (literal imagery) or abstract/instrumental (geometric/textural)?

STEP 2 - BALANCE LITERAL VS ABSTRACT:
- Story/narrative lyrics → scene-based imagery from the song's world
- Love songs → intimate, romantic scenes, symbolic objects (not spirals)
- Political/protest → documentary aesthetic, urban scenes, powerful imagery
- Nature references → actual landscapes matching the lyrics
- Abstract/instrumental only → then use geometric patterns, textures

STEP 3 - STRUCTURE YOUR PROMPT:
1. SUBJECT - What the image depicts (extract from lyrics/theme, NOT random shapes)
2. ARTISTIC STYLE - Match the genre's visual language (see below)
3. MOOD/ATMOSPHERE - Emotional tone of the music
4. COLOR PALETTE - Colors that reflect the song's feeling
5. LIGHTING - Dramatic, soft, neon, natural, etc.
6. TEXTURE - Surface qualities fitting the production style
7. QUALITY BOOSTERS - End with: "album cover art, highly detailed, professional artwork"

GENRE-APPROPRIATE VISUAL LANGUAGE:
- Hip-Hop/Rap: urban photography, street scenes, bold graphics, documentary style, concrete and neon
- Rock/Alternative: concert aesthetic, gritty textures, high contrast, raw photography style
- Pop: clean commercial quality, bright colors, fashion photography aesthetic
- Folk/Acoustic/Country: natural landscapes, film photography, warm analog tones, rustic textures
- Electronic/EDM: digital art, clean geometry, neon lighting, futuristic cityscapes
- Metal/Hardcore: dark fantasy, gothic architecture, dramatic skies, intense detail
- R&B/Soul: intimate scenes, sensual soft lighting, cinematic warmth
- Jazz: smoky club aesthetic, noir photography, warm shadows, vintage feel
- Punk: DIY collage aesthetic, xerox textures, bold anarchic imagery
- Classical/Orchestral: fine art painting style, dramatic romanticism, grand scale

EXAMPLES BY SONG TYPE:
- Heartbreak ballad: "empty chair by rain-streaked window, soft photography style, muted blues and grays, diffused overcast light, melancholic intimate mood, film grain texture, album cover art, highly detailed"
- Political protest song: "raised fists silhouetted against urban skyline, documentary photography style, high contrast black and white with red accent, harsh dramatic lighting, gritty photojournalism texture, album cover art, powerful"
- Summer love song: "golden hour beach scene with vintage car, nostalgic film photography, warm oranges and soft teals, lens flare sunset lighting, dreamy soft focus, album cover art, professional artwork"
- Dark electronic: "neon-lit rain-soaked cyberpunk alley, digital art style, deep purple and electric blue, neon reflections on wet pavement, sharp clean details, album cover art, masterpiece"
- Acoustic folk about nature: "misty mountain lake at dawn, landscape photography style, earthy greens and morning gold, soft natural lighting, serene expansive composition, album cover art, highly detailed"
- Aggressive metal: "ancient battlefield under stormy sky, dark fantasy painting style, blood red and iron gray, dramatic lightning, intense weathered textures, epic scale composition, album cover art, masterpiece"

RULES:
- NEVER include text, words, letters, or band names
- NEVER include recognizable human faces
- DO include imagery that reflects the actual lyrics and theme
- DO match the visual style to the musical genre
- AVOID generic abstract spirals unless the music is truly abstract
- KEEP UNDER 45 WORDS - prompts longer than 77 tokens get truncated by CLIP

Be extremely detailed in the style description - mention specific:
- Drum type (acoustic kit, TR-808, TR-909, breakbeats, live drums, programmed)
- Bass characteristics (electric bass, synth bass, sub-bass, upright)
- Melodic instruments (acoustic guitar, electric guitar, synths, piano, organ, horns, strings)
- Production era and techniques (lo-fi, hi-fi, compressed, dynamic, vintage, modern)
- Mix characteristics (warm, bright, spacious, dense, intimate)
- Atmosphere and sonic texture

Listen carefully to identify the ACTUAL genre - it could be anything from any era or culture.
Identify similar artists based on what the music ACTUALLY sounds like, not assumptions.

""" + VIDEO_SEARCH_TERMS_PROMPT

class HumanMetadataExtractionService(SingletonService):

    def __init__(self):
        if getattr(self, '_initialized', False):
            return

        self.client: Any = None
        self.model = "gemini-2.5-pro"
        self._service_initialized = False
        self._initialized = True

    async def initialize(self):
        if self._service_initialized:
            log_service.info("HumanMetadataExtractionService already initialized")
            return

        api_key = settings.load_api_key_from_file("GEMINI_API_KEY")
        if not api_key:
            log_service.error("GEMINI_API_KEY not found - metadata extraction will fail")
            return

        self.client = genai.Client(api_key=api_key)
        self._service_initialized = True
        log_service.info(f"✓ HumanMetadataExtractionService initialized (model: {self.model})")

    async def extract_metadata(
        self,
        audio_path: Path,
        user_provided_title: Optional[str] = None,
        user_provided_artist: Optional[str] = None
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:

        if not self._service_initialized or not self.client:
            log_service.error("HumanMetadataExtractionService not initialized")
            return None, "Audio analysis service not available. Please try again later."

        if not audio_path.exists():
            log_service.error(f"Audio file not found: {audio_path}")
            return None, "Audio file not found."

        audio_bytes = audio_path.read_bytes()
        file_size_mb = len(audio_bytes) / (1024 * 1024)

        if file_size_mb > 20:
            log_service.error(f"Audio file too large for AI analysis: {file_size_mb:.1f}MB (max 20MB)")
            return None, "File too large for AI analysis. Try a shorter track or compressed format (MP3)."

        suffix = audio_path.suffix.lower()
        mime_types = {
            '.mp3': 'audio/mp3',
            '.wav': 'audio/wav',
            '.ogg': 'audio/ogg',
            '.opus': 'audio/opus',
            '.flac': 'audio/flac',
            '.m4a': 'audio/mp4',
            '.aac': 'audio/aac',
            '.webm': 'audio/webm',
        }
        mime_type = mime_types.get(suffix, 'audio/mp3')

        log_service.info(f"Extracting metadata from {audio_path.name} ({file_size_mb:.1f}MB)")

        user_prompt = self._build_prompt(user_provided_title, user_provided_artist)

        max_retries = 3
        retry_delays = [5, 15, 30]

        for attempt in range(max_retries):
            try:
                audio_part = types.Part.from_bytes(
                    data=audio_bytes,
                    mime_type=mime_type
                )

                response = await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.model,
                    contents=[audio_part, user_prompt],
                    config=types.GenerateContentConfig(
                        temperature=0.3,
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json"
                    )
                )

                if not response.text:
                    log_service.error("Gemini returned empty response")
                    return None, "AI returned empty response. Please try again."

                result = json.loads(response.text)

                if user_provided_title and result.get('title') in ['Untitled', None, '']:
                    result['title'] = user_provided_title

                if user_provided_artist:
                    result['user_provided_artist'] = user_provided_artist

                log_service.info(f"✓ Metadata extracted: {result.get('primary_genre')} - {result.get('title')}")
                return result, None

            except json.JSONDecodeError as e:
                log_service.error(f"Failed to parse Gemini response as JSON: {e}")
                return None, "AI returned invalid response. Please try again."
            except Exception as e:
                error_str = str(e).lower()
                is_transient = '503' in error_str or 'overloaded' in error_str or 'unavailable' in error_str or 'rate' in error_str

                if is_transient and attempt < max_retries - 1:
                    delay = retry_delays[attempt]
                    log_service.warning(f"Gemini overloaded (attempt {attempt + 1}/{max_retries}), retrying in {delay}s...")
                    await asyncio.sleep(delay)
                    continue

                log_service.error(f"Error extracting metadata: {e}")
                import traceback
                log_service.error(traceback.format_exc())

                if is_transient:
                    return None, "AI service is temporarily overloaded. Please try again in a few minutes."
                return None, f"Failed to analyze audio: {str(e)[:100]}"

        return None, "AI service is temporarily overloaded. Please try again in a few minutes."

    def _build_prompt(
        self,
        user_title: Optional[str] = None,
        user_artist: Optional[str] = None
    ) -> str:

        prompt_parts = ["Analyze this audio file and generate detailed metadata."]

        if user_title or user_artist:
            prompt_parts.append("\nUser-provided information:")
            if user_title:
                prompt_parts.append(f"- Title: {user_title}")
            if user_artist:
                prompt_parts.append(f"- Artist: {user_artist}")
            prompt_parts.append("Use this information to help with classification, but still analyze the audio independently.")

        prompt_parts.append("""
IMPORTANT:
- Listen carefully to identify the ACTUAL genre (could be anything: rock, pop, jazz, electronic, folk, hip-hop, reggae, classical, country, R&B, metal, world music, etc.)
- Identify similar artists based on what the music ACTUALLY sounds like
- Be specific about instruments you hear (acoustic guitar vs electric, real drums vs programmed, synths vs piano, etc.)
- Transcribe the lyrics accurately if there are vocals

Output ONLY valid JSON with your analysis, no other text.""")

        return "\n".join(prompt_parts)

    def format_as_catalog_metadata(
        self,
        extracted: Dict[str, Any],
        track_id: str,
        user_id: int,
        duration_ms: int,
        original_filename: str
    ) -> Dict[str, Any]:

        now = datetime.now(timezone.utc).isoformat()

        transcribed_lyrics = extracted.get("transcribed_lyrics")
        lyrics_prompt = transcribed_lyrics if transcribed_lyrics else ""

        return {
            "id": track_id,
            "created_at": now,
            "is_ai_generated": False,
            "uploaded_by_user_id": user_id,
            "original_filename": original_filename,

            "generation_params": {
                "style": extracted.get("style", ""),
                "title": extracted.get("title", "Untitled"),
                "instrumental": extracted.get("instrumental", False),
                "vocal_gender": extracted.get("vocal_gender"),
                "prompt": lyrics_prompt,
                "custom_mode": False,
                "model": "human_upload",
                "style_canonical": extracted.get("style", ""),
            },

            "track_info": {
                "title": extracted.get("title", "Untitled"),
                "duration": duration_ms,
                "artist": extracted.get("user_provided_artist") or extracted.get("inspired_artist"),
            },

            "generation_status": "completed",
            "status_updated_at": now,

            "derived_tags": {
                "primary_genre": extracted.get("primary_genre", "Unknown"),
                "secondary_genres": extracted.get("secondary_genres", []),
                "inspired_artist": extracted.get("inspired_artist"),
                "mood_keywords": extracted.get("mood_keywords", []),
                "lyrical_interpretation": extracted.get("lyrical_interpretation"),
                "vocal_style_keywords": extracted.get("vocal_style_keywords", []),
                "similar_artists": extracted.get("similar_artists", []),
                "video_search_terms": extracted.get("video_search_terms", []),
                "enriched_at": now,
            },

            "transcribed_lyrics": transcribed_lyrics,

            "mix_analysis": extracted.get("mix_analysis"),
            "sonic_master_prompt": extracted.get("sonic_master_prompt"),
            "sonic_master_blend": extracted.get("sonic_master_blend", 0),
            "mastering_blend": extracted.get("mastering_blend", 70),
            "enhance_vocals": extracted.get("enhance_vocals", False),
            "artwork_prompt": extracted.get("artwork_prompt"),
            "video_search_terms": extracted.get("video_search_terms", []),
        }