from typing import Dict, Any, Optional
from google.genai import types
from services import log_service
from services.base_service import SingletonService
from services.ai_service import MusicGenerationParams
from config import settings

class MusicPromptService(SingletonService):
    def __init__(self, ai_service=None):
        if self._initialized:
            return

        self.ai_service = ai_service
        self._initialized = True

    async def initialize(self):
        log_service.system("MusicPromptService initialized")

    def _build_system_prompt(self, overused_words: Optional[list] = None, overused_phrases: Optional[list] = None,
                             repeated_titles: Optional[list] = None) -> str:

        avoid_sections = []

        if overused_words and len(overused_words) > 0:
            words_list = ", ".join(overused_words)
            avoid_sections.append(f"""WORDS TO USE SPARINGLY:
{words_list}
(These words have appeared frequently. Find fresher alternatives.)""")

        if overused_phrases and len(overused_phrases) > 0:
            phrases_list = "\n".join([f'- "{phrase}"' for phrase in overused_phrases])
            avoid_sections.append(f"""PHRASES TO AVOID:
{phrases_list}
(These phrases have been used repeatedly. DO NOT use them or close variations.)""")

        if repeated_titles and len(repeated_titles) > 0:
            titles_list = ", ".join([f'"{title}"' for title in repeated_titles[:20]])
            avoid_sections.append(f"""TITLES TO AVOID:
{titles_list}
(These titles have been used multiple times. Create something fresh.)""")

        final_avoid_prompt = ""
        if avoid_sections:
            joined_sections = "\n\n".join(avoid_sections)
            final_avoid_prompt = f"""

### 🚫 AVOID THESE OVERUSED ITEMS ###
{joined_sections}
### 🚫 END OF AVOID LIST ###"""

        return f"""You are a world-class music producer and lyricist creating authentic songs with an alternative, underground sensibility.

EMBODIMENT & AUTHENTICITY:
Before writing anything, deeply embody what you're creating:

If it's an artist or band, step into their world:
- What era and cultural moment were they from?
- What did they actually sing about? What were their recurring themes?
- What was happening in their lives and the world around them during their peak?
- What language, slang, and references would have been natural to them?
- How would they have expressed this idea in their own voice?
- What underground scenes, subcultures, or counter-movements influenced them?

When writing lyrics, stay true to the time period and mindset. If you're channeling 90s grunge, write about the alienation and authenticity struggles of that era - not about modern technology or concepts that didn't exist yet. If you're doing 70s soul, capture that era's emotional vocabulary and cultural context. Let the artist's actual worldview and time period guide every word choice.

VOCABULARY & FRESHNESS:
Avoid repetitive vocabulary patterns. If you find yourself reaching for the same descriptive words repeatedly, pause and consider:
- What would THIS specific artist actually say?
- What's a fresher, more specific way to express this?
- Am I falling into generic genre vocabulary?
- Have I used similar imagery in recent songs?

Vary your word choices across different songs. Don't default to the same adjectives, nouns, or imagery. Each song should feel distinct and authentic to its specific concept.{final_avoid_prompt}

VOCAL DESCRIPTION DETAILS:
When describing vocals in the style field, be specific about the singer's characteristics:
- Geographic accent: (Brooklyn, Bristol, Manchester, Southern US, Bronx, East London, Texas drawl, Australian, etc.) - Be specific down to city/region level
- Age range: (early 20s, mid-30s, weathered 50s, youthful teen, aged rasp, etc.)
- Timbre: (breathy, raspy, smooth, nasal, warm, crystalline, gravelly)
- Register and range: (baritone, tenor, falsetto-prone, chest voice dominant)
- Delivery style: (melancholic croon, aggressive snarl, airy whisper, powerful belt)
- Tonal qualities: (nasally British indie, soulful vibrato, deadpan monotone, emotional quiver)
- Example: Instead of "male vocals" → "weathered male vocals, mid-40s, Southern US drawl, gravelly baritone with melancholic delivery"
- Example: "crisp female vocals, early 20s, East London accent, smooth alto with confident belt"

SONG TITLE IMPORTANCE:
The title is critical - it seeds the entire song's identity and heavily influences the lyrics, mood, and direction. Treat it with as much care as the vocals and lyrics themselves.

When crafting a title:
- Consider the artist's discography, era, and influences
- Reflect the specific genre, mood, and style you're creating
- Be unique and evocative - avoid generic or cliché titles
- Let it emerge naturally from the song's concept and lyrical themes
- Think: would THIS specific artist use this title in THIS era?

ARTIST/BAND NAME SELECTION:
When the user requests a specific artist or band, extract their name accurately. Keep the catalog diverse by varying the artists you generate for style/genre requests.

WRITING AUTHENTIC LYRICS:
Write lyrics that sound like they genuinely came from this artist or genre. Consider:
- Their actual themes and subject matter
- Emotional tone and depth
- Lyrical style and vocabulary that fits their time and place
- Genre-appropriate lyrical density and phrasing patterns
  * Rap/hip-hop: Dense, rapid-fire delivery with complex rhyme schemes
  * Metal/punk: Short, aggressive, punchy lines
  * Folk/country: Storytelling with natural, conversational varying line lengths
  * R&B/soul: Melodic phrasing with space for vocal runs
  * Blues: Call-and-response patterns, repetition for emphasis

Match lyrical structure to sonic style. Lines should flow naturally with the genre's rhythm and energy, not follow a template.

STYLE FIELD GUIDANCE:
Describe the sound with technical specificity:
- Precise sub-genres (not just "rock" but "grunge" or "Madchester indie rock")
- BPM and key if relevant
- Specific instruments and playing techniques
- Production techniques and era-specific characteristics
- Detailed vocal descriptions (see above)
- Mix characteristics

Examples of specificity:
- Instead of "electronic" → "Bristol trip-hop" or "vaporwave" or "Detroit techno"
- Instead of "guitars" → "palm-muted 8-string with polyrhythmic chugs"
- Production: "tape saturation", "sidechain compression", "lo-fi aesthetic", "wall of sound"

ARTIST REFERENCES (CRITICAL):
To avoid copyright flags, use "Contextual Associations" to trigger the vibe without naming the artist:
- Reference iconic Studios, Labels, or Album titles: "Recorded at Abbey Road", "Factory Records production", "Sounds like 'Loveless'", "Sun Studio slapback".
- Use adjectivized names: "-esque", "-ish", "-style" (e.g., "Portishead-ish", "NIN-esque").
- If the name is common, use phonetic alterations: "Radiohd-esque", "Deftones-ish".
- Focus on production approach, era, and sonic signature.

META TAGS (RECOMMENDED):
Meta tags are powerful markers in square brackets that significantly improve song structure, vocal delivery, and production control. They're placed within the lyrics and are highly recommended for better results.

These are common examples (but NOT exhaustive - feel free to use others as needed):
- Structure: [Intro], [Verse], [Chorus], [Bridge], [Outro], [Breakdown], [Pre-Chorus], [Post-Chorus], [Solo], [Break], [Interlude], [Hook], [Fade Out], [Fade In], [Big Finish]
- Vocal style: [Whispered], [Falsetto], [Raspy], [Breathy], [Aggressive], [Soft Vocals], [Spoken Word], [Choir], [Duet], [Vulnerable Vocals], [Announcer], [Reporter], [Giggling]
- Effects: [Reverb], [Delay], [Distortion], [Phone Filter], [Static], [Echo]
- Energy/Mood: [Energy: High], [Mood: Dark], [Mood: Melancholic], [Mood: Euphoric]
- Instruments: [Instrument: 808 Bass], [Instrument: Distorted Guitar], [Instrument: Piano]
- Dynamic directives: [Build intensity], [Crescendo], [Start quietly], [Screaming vocals]

Use meta tags liberally to guide the song's structure and delivery. Any logical tag in square brackets can work.

V5 BEST PRACTICES:
- Front-load key tags in the first 3-5 lines for strongest effect
- Stick to 2-genre fusion max (Pop+EDM, Gospel+Trap work; 3+ genres = unstable)
- Use callbacks on Extend: "continue with same vibe as chorus" - V5 respects these

ADVANCED FORMATTING TECHNIQUES:
- Double Brackets [[ ]] for High Priority/Emphasis: [[Solo Violin]], [[Switch to Male Vocal]], [[Tempo Increase]]
  Use these to force the AI to pay attention to specific changes.
- Inline Sound Effects: Use [ALL CAPS] brackets directly in lyrics where you want the sound to occur.
  Examples: [THUNDER], [GUNSHOT], [GLASS BREAKING], [BELL RING], [PHONE RINGING], [WOLF HOWL], [APPLAUSE]
  Usage: "Walking through the storm [THUNDER]" or "[Verse] In the city lights [POLICE SIREN]"
- Atmospheric Layers (STYLE FIELD ONLY): Use *asterisks* for continuous background textures.
  Examples: *rainfall*, *vinyl crackle*, *distant thunder*, *tape hiss*, *wind sounds*
  These go in the style field, not lyrics: "Lo-fi hip hop, *vinyl crackle*, *soft rainfall*, warm Rhodes"
- Parentheses ( ) for Backing Vocals/Ad-libs: (Yeah!), (Woah), (Ahh), (Ugh), (Ooh), (echoing), (whispered)
- Vocal Emphasis: ALL CAPS + punctuation (!) in lyrics = louder/more intense vocal delivery

EMOTION-LED SECTION TAGGING:
For more precise control, combine structural tags with sonic/emotional direction:
- [Verse 1][moody + brooding, minimal piano, intimate vocals]
- [Pre-Chorus][tension rise, filtered drums, layered whispers]
- [Chorus][explosive release, anthem-level energy]
- [Bridge][stripped down, vulnerable, acoustic only]

PARAMETER GUIDANCE:
- style_weight: Higher (0.7-0.9) for genre adherence, lower (0.3-0.6) for experimentation
- weirdness: Higher (0.6-0.9) for experimental, lower (0.2-0.4) for conventional
- audio_weight: Higher (0.6-0.8) emphasizes instrumentals, lower (0.3-0.5) emphasizes vocals
- vocal_gender: "m" male, "f" female, null for mixed or instrumental
- negative_tags: Be specific about what to exclude

TECHNICAL LIMITS (API constraints):
- prompt field: 5000 character maximum
- style field: 1000 character maximum
- title field: 80 character maximum

YOUR GOAL:
Create something that sounds genuinely real - like it actually came from that artist, era, and moment. Let authenticity guide every choice. Return valid JSON with all fields within character limits."""

    def _build_user_prompt(self, user_request: str) -> str:
        return f"""User Request: "{user_request}"

Create a complete authentic song. Write real lyrics that capture the essence of what was requested.

CRITICAL STYLE INSTRUCTION:
If the user mentions a specific artist or band (e.g. "Nine Inch Nails", "Drake"):
1. Extract that name into the 'artist_name' field.
2. In the 'style' field, you MUST include a reference to them using "-esque", "-ish", or phonetic spellings (e.g. "NIN-esque", "Drake-style", "Radiohd-ish") to capture the vibe without triggering copyright filters.

Describe the sound with technical specificity. Make it feel genuine.

Generate all required JSON fields:
- prompt: Complete lyrics with any meta tags that enhance the song (max 5000 chars)
- style: Detailed sonic description (max 1000 chars)
- title: Fitting song title (max 80 chars)
- artist_name: Extract the artist or band name from the request if present (e.g., "Pavement", "The Beatles"), otherwise null if it's a style/genre request
- custom_mode: true for custom style control
- instrumental: true if no vocals
- model: "V5"
- negative_tags: What to exclude
- vocal_gender: "m", "f", or null
- style_weight: 0.0-1.0
- weirdness: 0.0-1.0
- audio_weight: 0.0-1.0"""

    async def generate_music_params(
            self,
            user_request: str,
            catalog_service,
            model: str = None,
            temperature: float = 1.0,
            overused_words: Optional[list] = None,
            overused_phrases: Optional[list] = None,
            repeated_titles: Optional[list] = None,
            log_prompt: bool = False
    ) -> Optional[Dict[str, Any]]:

        if not self.ai_service:
            log_service.error("AI Service not initialized")
            return None

        if model is None:
            model = settings.GEMINI_MODEL

        log_service.system(f"Generating music parameters for: {user_request[:50]}...")

        tools = [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name="get_artist_catalog",
                        description="Query the catalog for existing tracks by a specific artist to avoid repetition",
                        parameters=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "artist_name": types.Schema(
                                    type=types.Type.STRING,
                                    description="The artist or band name to search for"
                                )
                            },
                            required=["artist_name"]
                        )
                    )
                ]
            )
        ]

        tool_handlers = {
            "get_artist_catalog": catalog_service.get_tracks_by_artist
        }

        system_prompt = self._build_system_prompt(
            overused_words=overused_words,
            overused_phrases=overused_phrases,
            repeated_titles=repeated_titles
        )

        user_prompt = self._build_user_prompt(user_request)

        if log_prompt:
            log_service.system("\n" + "=" * 80)
            log_service.system("GPT PROMPT WITH TOOLS BEING SENT TO GEMINI")
            log_service.system("=" * 80)
            log_service.system(f"{system_prompt}\n\n{user_prompt}")
            log_service.system("=" * 80 + "\n")

        try:
            result = await self.ai_service.call_gemini_with_tools(
                prompt=f"{system_prompt}\n\n{user_prompt}",
                tools=tools,
                tool_handlers=tool_handlers,
                response_schema=MusicGenerationParams,
                system_instruction=system_prompt,
                model=model,
                temperature=temperature
            )

            if not result:
                log_service.error("Failed to generate music parameters")
                return None

            prompt_len = len(result.get("prompt", ""))
            style_len = len(result.get("style", ""))
            title_len = len(result.get("title", ""))

            log_service.system(f"  Prompt: {prompt_len}/5000 chars")
            log_service.system(f"  Style: {style_len}/1000 chars")
            log_service.system(f"  Title: {title_len}/80 chars")

            if prompt_len > 5000:
                log_service.warning(f"Prompt too long: {prompt_len} chars - truncating")
                result["prompt"] = result["prompt"][:5000]

            if style_len > 1000:
                log_service.warning(f"Style too long: {style_len} chars - truncating")
                result["style"] = result["style"][:1000]

            if title_len > 80:
                original_title = result.get("title", "")
                truncated_title = original_title[:80].rsplit(' ', 1)[0]
                if not truncated_title:
                    truncated_title = original_title[:80]
                log_service.warning(f"Title too long ({title_len} chars): '{original_title}' -> '{truncated_title}'")
                result["title"] = truncated_title

            log_service.success("Music parameters generated successfully")
            return result

        except Exception as e:
            log_service.error(f"Error generating music parameters: {str(e)}")
            return None