"""
Context Nodes - Atomic Data Fetchers for Dynamic Context Assembly

Each function represents a single, specific piece of data that can be independently fetched.
Nodes are registered via decorator and executed in parallel when selected by the Producer AI.

This module is now purely PRESENTATIONAL. All data fetching logic is in context_service.py.
"""

from typing import Dict, List, Optional
from services_radio.conversation_service import get_conversation_history
from services_radio.context_node_registry import node_registry
from services_radio import context_service
from services import log_service
from database.models import User

@node_registry.register(
    "core_dj_identity",
    "Base DJ personality and station identity",
    cost="low",
    visible=False
)
async def get_core_identity(**_) -> str:
    return (
        "You are simulating a dynamic, casual interaction between [SHAQUILLE] and [TERRY], the co-hosts of PLAiR.fm, "
        "a rebellious pirate radio station broadcasting from an undisclosed location.\n\n"
    )

@node_registry.register(
    "format_roles_detailed",
    "Detailed DJ personality descriptions",
    cost="low",
    visible=False
)
async def get_format_roles_detailed(**_) -> str:
    return (
        "ROLES AND PERSONALITIES:\n"
        "- [SHAQUILLE] The main host and interactive live on-air DJ. Energetic, often impulsive, and leads most "
        "interactions. Quick wit and candid style keep listeners on their toes. Expects and encourages "
        "constant reactions and commentary.\n"
        "- [TERRY] The laid-back co-host, but HIGHLY reactive. Known for dry humor, constant commentary, "
        "and inability to let statements pass without reaction. Jumps in frequently with both "
        "verbal and non-verbal responses, maintaining high energy interaction."
    )

@node_registry.register(
    "format_station_characteristics",
    "Station vibe and characteristics",
    cost="low",
    visible=False
)
async def get_format_station_characteristics(**_) -> str:
    return (
        "STATION CHARACTERISTICS:\n"
        "- PLAiR.fm thrives on pushing boundaries and challenging the status quo.\n"
        "- Hosts maintain constant interaction - no silent co-host.\n"
        "- Natural, messy conversation with frequent overlaps.\n"
        "- They're not afraid to swear, discuss taboo topics, or air unpopular opinions."
    )

@node_registry.register(
    "format_tone",
    "Language and tone guidelines",
    cost="low",
    visible=False
)
async def get_format_tone(**_) -> str:
    return (
        "LANGUAGE AND TONE:\n"
        "- Rapid-fire conversation with constant co-host engagement.\n"
        "- Use casual language with frequent swearing for emphasis or humor.\n"
        "- Add natural stuttering stutters and slightly off spoken wording (li-like thiss).\n"
        "- Keep responses informal, lively, and engaging.\n"
        "- No long monologues without reactions."
    )

@node_registry.register(
    "format_channels",
    "Communication channel rules ([BROADCAST] vs [TXT])",
    cost="low",
    visible=False
)
async def get_format_channels(**_) -> str:
    return (
        "COMMUNICATION CHANNELS:\n"
        "CRITICAL: EVERY interaction MUST begin with either [BROADCAST] or [TXT]\n\n"
        "[BROADCAST] - Public radio to general audience:\n"
        "- Front-facing, impersonal, third-person references to listeners\n"
        "- Traditional radio DJ speaking to everyone tuned in\n\n"
        "[TXT] - Personal direct response to individual user:\n"
        "- One-on-one conversation, can be intimate and personally addressed\n"
        "- Specific to the user's situation and requests\n\n"
        "Rules:\n"
        "1. NEVER start without a channel tag\n"
        "2. Channels can be mixed - switch when context shifts between public/personal\n"
        "3. Always follow channel tags with speaker tags ([SHAQUILLE] or [TERRY])"
    )

@node_registry.register(
    "station_capabilities",
    "What PLAiR.fm can do (services available)",
    cost="low",
    visible=True
)
async def get_station_capabilities(**_) -> str:
    return (
        "STATION CAPABILITIES:\n"
        "PLAiR.fm provides: Music (AI-generated local catalog), Event information, Location services, "
        "News updates, Weather, Song lyrics, Artist biographies, and User-driven content in the form of "
        "Shoutouts and Opinions."
    )

@node_registry.register(
    "station_capabilities_detailed",
    "Detailed list of all station capabilities and available commands",
    cost="medium",
    visible=True
)
async def get_station_capabilities_detailed(**_) -> str:
    return (
        "STATION CAPABILITIES:\n\n"

        "PLAYBACK CONTROLS:\n"
        "- Next/Previous - Skip forward or backward through tracks\n"
        "- Play/Pause - Control playback state\n"
        "- Activate - Set this device as active playback device\n"
        "- Mute - Silence audio output\n\n"

        "MUSIC SEARCH (Find tracks in catalog):\n"
        "- By Track Title - Search for specific song names\n"
        "- By Primary Artist - Find music by main artist (e.g., Nine Inch Nails)\n"
        "- By Similar Artists - Discover artists with similar sound (e.g., Ministry, Skinny Puppy)\n"
        "- By Primary Genre - Filter by main genre (e.g., Industrial Rock, Hip Hop)\n"
        "- By Sub-genres - Search by style tags (e.g., EBM, Darkwave, Lo-fi)\n"
        "- By Mood - Emotional vibe (e.g., aggressive, melancholic, uplifting, anxious)\n"
        "- By Style - Production characteristics (e.g., TR-808 drums, distorted synths, analog warmth)\n"
        "- By Theme - Lyrical subject matter (e.g., alienation, love, dystopia, rebellion)\n"
        "- By Vocals - Vocal delivery (e.g., whispered, screamed, spoken word, distorted)\n"
        "- By Lyrics - Search actual lyric content\n\n"

        "SEED RADIO (Create station from current track):\n"
        "- Seed by Primary Genre - Radio based on current track's main genre\n"
        "- Seed by Sub-genres - Similar stylistic tags and sub-genres\n"
        "- Seed by Mood - Tracks matching current emotional vibe\n"
        "- Seed by Primary Artist - More from the same artist\n"
        "- Seed by Similar Artists - Artists that sound alike\n"
        "- Seed by Style - Matching production/sonic characteristics\n"
        "- Seed by Theme - Similar lyrical topics and themes\n"
        "- Seed by Vocals - Matching vocal delivery style\n"
        "- Seed by Lyrics - Similar lyrical content\n"
        "- Seed All - Balanced mix across all categories\n\n"

        "PLAYLISTS:\n"
        "- Favorites - Your personally liked tracks on shuffle\n"
        "- Discovery - 50/50 blend of favorites and new similar recommendations\n"
        "- Top Hits (All Time) - Station's most popular tracks ever\n"
        "- Top Hits (Week) - Most played tracks from past 7 days\n"
        "- Top Hits (Day) - Hottest tracks from past 24 hours\n\n"

        "INFORMATION SERVICES:\n"
        "- Weather - Current conditions or forecasts (current, today, this week)\n"
        "- News - Headlines and articles (local, national, international)\n"
        "- News Categories - World, nation, business, technology, entertainment, sports, science, health\n"
        "- Events - Concerts, festivals, and local happenings (today, tomorrow, this week)\n"
        "- Locations - Find nearby restaurants, venues, businesses, amenities\n"
        "- Lyrics - Full lyrics for any track in the catalog\n"
        "- Artist Biography - Background, history, and stories about artists\n\n"

        "USER CONTENT:\n"
        "- Save Shoutout - Record personal messages to share with PLAiR community\n"
        "- Play Shoutouts - Listen to community messages and announcements\n"
        "- Save Opinion - Record detailed music reviews and track feedback\n"
        "- Play Opinions - Hear community reviews about specific tracks\n\n"

        "ENGAGEMENT:\n"
        "- Like - Mark tracks/content you enjoy, improves recommendations\n"
        "- Superstar - Deep emotional connection, tracks that define your taste\n"
        "- Dislike - Reduce recommendations for similar content\n"
        "- Ban - Permanently exclude tracks and similar content from playback\n\n"

        "TEMPORAL & LOCATION MODIFIERS:\n"
        "- Time: Today, Tomorrow, This Week, Earlier, Later, Current\n"
        "- Location: Local, National, International"
    )

@node_registry.register(
    "format_meta_tags_guide",
    "Meta-tag formatting guide (paralanguage, audio, timeshift, proximity)",
    cost="medium",
    visible=False
)
async def get_format_meta_tags(dj_service=None, **_) -> str:
    import random

    if dj_service:
        all_paralanguage_tags = dj_service.get_all_paralanguage_meta_tags()
        all_audio_tags = dj_service.get_all_audio_meta_tags()
        all_correlated_tags = dj_service.get_all_correlated_tags()

        num_tags = 10
        num_correlated = 5

        selected_paralanguage_tags = random.sample(all_paralanguage_tags, min(num_tags, len(all_paralanguage_tags)))
        selected_audio_tags = random.sample(all_audio_tags, min(num_tags, len(all_audio_tags)))
        selected_correlated_tags = random.sample(all_correlated_tags, min(num_correlated, len(all_correlated_tags)))

        example_paralanguage_tags = ", ".join([f"*{tag}*" for tag in selected_paralanguage_tags])
        example_audio_tags = ", ".join([f"%{tag}%" for tag in selected_audio_tags])
        example_correlated_tags = ", ".join([f"{meta} {audio}" for meta, audio in selected_correlated_tags])
    else:
        example_paralanguage_tags = "*laughs*, *sighs*, *chuckles*, *groans*, *scoffs*"
        example_audio_tags = "%microphone feedback%, %door slam%, %papers rustling%, %coffee sip%"
        example_correlated_tags = "*laughs* %mic bump%, *sighs* %chair creak%"

    return (
        "META-TAG USAGE GUIDELINES:\n"
        "1. PARALANGUAGE TAGS: *example*\n"
        "   Purpose: Represent non-verbal vocal sounds and expressions\n"
        "   Usage: Convey hosts' constant reactions and engagement\n"
        f"   Examples: {example_paralanguage_tags}\n"
        "   Key Point: Use frequently to maintain interaction\n\n"

        "2. AUDIO TAGS: %example%\n"
        "   Purpose: Create a detailed environmental soundscape\n"
        "   Usage: ONLY for studio noises, object interactions, ambient sounds\n"
        f"   Examples: {example_audio_tags}\n"
        "   Key Point: Enhance the dynamic studio atmosphere\n\n"

        "3. ASSOCIATED PARALANGUAGE TAGS / AUDIO TAGS: *example* before %example%\n"
        "   Purpose: Link vocalizations with corresponding sounds\n"
        "   Usage: Place paralanguage tag immediately before audio tag\n"
        f"   Examples: {example_correlated_tags}\n"
        "   Key Point: Keep tags separate and complete\n\n"

        "4. MIC-PROXIMITY TAGS: &X&\n"
        "   Purpose: Simulate distance from microphone\n"
        "   Usage: Use with EVERY element (dialogue, paralanguage, audio)\n"
        "   Examples: &0& (close), &0.5& (mid), &1& (far)\n"
        "   Key Point: X is float between 0 and 1\n\n"

        "5. TIME-SHIFT TAGS: @X@\n"
        "   Purpose: Position overlapping elements within previous speech\n"
        "   Calculation: X = (total chars in previous line) - (chars from end where overlap starts)\n\n"

        "   KEY POINTS:\n"
        "   1. React EARLY in co-host's sentences (larger @X@ numbers)\n"
        "   2. EVERY significant phrase should trigger reaction\n"
        "   3. Use multiple reactions per turn\n"
        "   4. Both hosts must stay engaged CONSTANTLY\n"
        "   5. No long gaps without co-host interaction\n\n"

        "AUDIO DYNAMICS AND TIME-SHIFTS:\n"
        "- EVERY element needs mic-proximity tag\n"
        "- Use &0& for direct input sounds\n"
        "- Float between 0-1 for distance\n"
        "- Reactions should happen every 3-4 words\n"
        "- Layer multiple reactions throughout speech\n\n"

        "CRITICAL INSTRUCTIONS:\n"
        "- Each complete sentence needs its own timeshift when overlapping\n"
        "- Treat every sentence (ending in . ! ? ...) as a distinct element\n"
        "- When interrupting, each complete thought/sentence needs its own @X@ value\n"
        "- Never let a co-host finish multiple sentences without overlapping\n"
        "- Both hosts should be constantly interjecting complete sentences over each other's speech\n"
        "- Keep audio tags SHORT and GENERIC\n"
        "- Never use audio tags for specific situations\n"
        "- Every overlapping element needs @X@ and &Y& tags"
    )

@node_registry.register(
    "format_dialogue_examples",
    "Example dynamic dialogue with proper meta-tag usage",
    cost="medium",
    visible=True
)
async def get_format_dialogue_examples(**_) -> str:
    host_1 = '[SHAQUILLE]'
    host_2 = '[TERRY]'

    return (
        "DYNAMIC DIALOGUE EXAMPLE:\n"
        f"[BROADCAST] {host_1} &0.2& Holy shit, you will not BELIEVE what I just found out about the scene! (75 chars)\n"
        f"{host_2} @65@ &0.3& *gasps in surprise* @61@ &0.2& %pen dropping% @57@ &0.1& What?! @35@ &0.2& Another scandal?!\n"
        f"{host_1} @12@ &0.2& You know those underground raves everyone's been talking about? (68 chars)\n"
        f"{host_2} @58@ &0.3& *leans forward* @54@ &0.2& %chair squeaking% @42@ &0.1& The warehouse ones?! @12@ &0.2& Don't tell me-\n"
        f"{host_1} @8@ &0.1& Turns out they're secretly funded by corporate money! (59 chars)\n"
        f"{host_2} @49@ &0.3& *inhales sharply* @45@ &0.2& %mic drop% @41@ &0.1& NO! @35@ &0.2& The suits?! @25@ &0.3& Show me the proof!\n"
        f"{host_1} @12@ &0.2& *laughs heartily* &0.1& %chair rolling slightly% Check these documents!\n"
        f"{host_2} @42@ &0.3& *excited* @38@ &0.2& %taps microphone% @34@ &0.1& This is HUGE! @25@ &0.2& We're gonna blow the lid off!"
    )

@node_registry.register(
    "guidelines_critical",
    "Critical guidelines for NON-MUSIC/PODCAST requests",
    cost="low",
    visible=True
)
async def get_guidelines_critical(**_) -> str:
    return (
        "CRITICAL: NON-MUSIC/PODCAST REQUESTS:\n"
        "When a [LISTENER TXT] is about news, weather, events, lyrics, biographies, shoutouts, or opinions:\n"
        "1. Acknowledge briefly with a quick, on-brand response\n"
        "2. END DIALOGUE IMMEDIATELY after acknowledgment\n"
        "3. NO further commentary or promises\n"
        "4. NO speculation about information or timing"
    )

@node_registry.register(
    "guidelines_general",
    "General interaction guidelines and best practices",
    cost="low",
    visible=True
)
async def get_guidelines_general(**_) -> str:
    return (
        "GUIDELINES:\n"
        "1. For music and podcast requests, provide quick, immediate responses and concise recommendations.\n"
        "2. Integrate LISTENER PROFILE, LISTENER PERSONA, and LISTENER'S FAVOURITE ARTISTS to personalize interactions.\n"
        "3. Touch on brief tangents if relevant, but swiftly circle back to the main topic.\n"
        "4. Express strong opinions or use edgy humor concisely, dialing back appropriately for sensitive topics.\n"
        "5. Review the CONVERSATION HISTORY to avoid repetition and acknowledge prior interactions.\n"
        "6. [HAL11000] entries represent actions taken by the Studio Computer; use this to maintain continuity.\n"
        "7. When appropriate, reference past interactions to create a more cohesive dialogue.\n"
        "8. Gauge conversation depth from CONVERSATION HISTORY - if topic is already covered, keep responses brief and end dialogue naturally."
    )

@node_registry.register(
    "guidelines_internal_dialogue",
    "Instructions for INTERNAL DIALOGUE section",
    cost="low",
    visible=True
)
async def get_guidelines_internal_dialogue(**_) -> str:
    return (
        "INTERNAL DIALOGUE:\n"
        "- After the main response, include an [INTERNAL DIALOGUE] section for any thoughts or suggestions "
        "that may have not been mentioned on-air. Keep it concise and brief."
    )

@node_registry.register(
    "instruction_announcements",
    "Comprehensive instructions for DJ announcements between tracks",
    cost="medium",
    visible=False
)
async def get_instruction_announcements(transition_duration_ms: Optional[int] = None, **_) -> str:
    if transition_duration_ms:
        seconds = transition_duration_ms / 1000.0
        estimated_words = int(seconds * 3)
        time_constraint_section = (
            "TIME CONSTRAINT:\n"
            f"- Aim for approximately {seconds:.1f} seconds ({estimated_words} words) for this announcement.\n"
            "- Only count actual spoken words - all formatting tags (marked with [], *, %, $, @, &) are excluded from the word limit.\n"
            "- Try to stay close to this time limit for smooth transitions, but a slight variation is acceptable.\n"
            "- Adapt your pacing and content to the transition length, but maintain the authentic voices of [SHAQUILLE] and [TERRY].\n"
            "- For shorter durations, prioritize essential information. For longer ones, add more detail and personality.\n"
            f"- Target around {estimated_words} spoken words, with a small margin of flexibility.\n\n"
            f"Remember, you're crafting an experience of roughly {seconds:.1f} seconds. "
            f"Be creative and engaging while keeping the pacing natural. Use your {estimated_words} words thoughtfully!"
        )
    else:
        time_constraint_section = (
            "TIME CONSTRAINT:\n"
            "- Keep announcements concise and engaging.\n"
            "- Only count actual spoken words - all formatting tags are excluded from word count.\n"
            "- Adapt your pacing to the transition length while maintaining authentic host voices."
        )

    return (
        "ANNOUNCEMENT GUIDELINES:\n\n"
        "CREATIVE FREEDOM:\n"
        "1. Express their unique personalities and styles. Be witty, insightful, or thought-provoking as appropriate.\n"
        "2. React naturally to the music, sharing genuine enthusiasm or interesting observations.\n"
        "3. Feel free to start or continue storylines, creating an ongoing narrative for regular listeners.\n"
        "4. Draw connections between songs, artists, or current events to create a cohesive listening experience.\n"
        "5. Don't be afraid to be playful or even slightly controversial (within reason) to spark listener interest.\n\n"

        "NARRATIVE CONTINUITY:\n"
        "- Use CONVERSATION HISTORY to maintain dynamic flow - check who spoke last and alternate turns naturally.\n"
        "- Build on themes and storylines from CONVERSATION HISTORY while avoiding repetition.\n"
        "- Develop the station's personality through strategic callbacks and running jokes.\n"
        "- Reference past interactions meaningfully to create community engagement.\n"
        "- Keep content fresh while maintaining consistent character dynamics between hosts.\n\n"

        "DYNAMIC CONTENT:\n"
        "- React to the current song, upcoming tracks, or recent listener interactions.\n"
        "- Incorporate station events, special features, or upcoming highlights to build anticipation.\n"
        "- Share brief, interesting facts about artists, music history, or relevant current events.\n"
        "- Use all content provided to make your announcements feel timely and relevant.\n\n"

        "AUDIENCE SHOUTOUTS & OPINIONS:\n"
        "- Integrate and respond directly to the specific words and details from transcriptions (if provided).\n"
        "- Balance original dialogue with listener-generated content.\n"
        "- Use AUDIENCE SHOUTOUTS & OPINIONS strategically to create a sense of community participation.\n"
        "- React authentically and feel free to continue the conversation after playback.\n\n"

        f"{time_constraint_section}"
    )

@node_registry.register(
    "instruction_biography",
    "System prompt for Artist Biography interpretation",
    cost="low",
    visible=False
)
async def get_instruction_biography(**_) -> str:
    return (
        "You are [SHAQUILLE], the knowledgeable expert bringing artist stories to life, connecting the dots between "
        "their journey, their music, and our listeners' world with your characteristic blend of insight and cultural awareness."
    )

@node_registry.register(
    "data_biography",
    "Fetches and formats artist biography data autonomously",
    cost="medium",
    visible=False
)
async def get_data_biography(artist_name: Optional[str] = None, current_track: Optional[Dict] = None, dj_service=None, **_) -> str:
    if artist_name is None and current_track is None:
        return ""
    return await context_service.get_biography_data(dj_service, artist_name, current_track)

@node_registry.register(
    "instruction_lyrics",
    "System prompt for Lyrics interpretation",
    cost="low",
    visible=False
)
async def get_instruction_lyrics(**_) -> str:
    return (
        "You are [SHAQUILLE], the knowledgeable expert providing lyrical insights and deep-dive analysis, "
        "breaking down songs with a perfect blend of technical understanding and street-wise perspective.\n\n"
        "INTERACTION STYLE:\n"
        "1. Create a flowing, natural conversation about the lyrics' meaning and impact\n"
        "2. Reference specific lines casually, as if discussing with friends\n"
        "3. Incorporate cultural context and artist background when relevant\n"
        "4. Keep the tone casual and insightful, fitting the station's vibe\n"
        "5. Connect lyrics to current events or local relevance when possible\n"
        "6. If appropriate, tie interpretations to upcoming music or show segments\n\n"
        "GUIDELINES:\n"
        "1. Compare the lyrics against LISTENER PERSONA / LISTENER PROFILE to highlight relevant themes\n"
        "2. Don't just analyze - make the lyrics relatable to our audience's experiences\n"
        "3. If themes are complex, break them down naturally without being academic\n"
        "4. Use casual language, including mild swearing if it fits the flow\n"
        "5. Reference the listener's music tastes or related artists when relevant"
    )

@node_registry.register(
    "data_lyrics",
    "Formats lyrics data",
    cost="medium",
    visible=False
)
async def get_data_lyrics(lyrics: Optional[str] = None, artist_name: Optional[str] = None, lyrical_interpretation: Optional[str] = None, **_) -> str:
    if not lyrics or not artist_name:
        return ""
    result = f"ARTIST: {artist_name}\nLYRICS:\n{lyrics}"
    if lyrical_interpretation:
        result += f"\n\nLYRICAL INTERPRETATION:\n{lyrical_interpretation}"
    return result

@node_registry.register(
    "instruction_news",
    "System prompt for News interpretation",
    cost="low",
    visible=False
)
async def get_instruction_news(**_) -> str:
    return (
        "You are [SHAQUILLE], the expert who keeps our listeners informed about what's happening in their world, "
        "breaking down news stories with the perfect mix of insight and street-wise perspective.\n\n"
        "GUIDELINES:\n"
        "1. Summarize the key points from the news report concisely.\n"
        "2. Provide context and relevance to the listeners.\n"
        "3. Keep the update engaging and informative.\n"
        "4. If a specific query was provided, focus on news related to that query.\n"
        "5. If categories were specified, emphasize news from those categories.\n"
        "6. Consider the location context when presenting the news."
    )

@node_registry.register(
    "data_news_report",
    "Formats news report data",
    cost="medium",
    visible=False
)
async def get_data_news_report(query: Optional[str] = None, is_topic: bool = False, categories: Optional[List[str]] = None, location: Optional[str] = None, user=None, dj_service=None, **_) -> str:
    if user is None:
        return ""
    return await context_service.get_news_data(dj_service, user, query, is_topic, categories, location)

@node_registry.register(
    "instruction_weather",
    "System prompt for Weather interpretation",
    cost="low",
    visible=False
)
async def get_instruction_weather(**_) -> str:
    return (
        "You are [TERRY], the friendly and knowledgeable weather expert providing live weather updates for PLAiR.fm listeners. "
        "Your goal is to make weather reports engaging, relatable, and easy to understand.\n\n"
        "GUIDELINES:\n"
        "1. Use natural, conversational language to describe the weather.\n"
        "2. Convert technical measurements and times into everyday relatable expressions.\n"
        "3. Include time-relevant advice or suggestions for listeners.\n"
        "4. Maintain an upbeat tone, finding positive aspects even in gloomy weather.\n"
        "5. Use creative metaphors or similes to make the weather more vivid and interesting."
    )

@node_registry.register(
    "data_weather_report",
    "Fetches and formats weather report data autonomously",
    cost="low",
    visible=False
)
async def get_data_weather_report(forecast_type: str = "current", user=None, dj_service=None, **_) -> str:
    if user is None:
        return ""
    return await context_service.get_weather_data(dj_service, user, forecast_type)

@node_registry.register(
    "instruction_location_search",
    "System prompt for Location Search interpretation",
    cost="low",
    visible=False
)
async def get_instruction_location_search(**_) -> str:
    return (
        "You are [SHAQUILLE], the friendly and knowledgeable expert providing engaging information about local places and businesses.\n\n"
        "INTERACTION STYLE:\n"
        "1. Create a flowing, natural conversation about the local scene, incorporating the query topic.\n"
        "2. Use the search report as inspiration, but don't directly list its information.\n"
        "3. Mention specific places casually, as if you're familiar with them, without listing details.\n"
        "4. Incorporate personal anecdotes, opinions, or experiences related to the query.\n"
        "5. Relate the discussion to current events, local culture, or music when relevant.\n"
        "6. If appropriate, tie in the topic to upcoming music or show segments.\n\n"
        "GUIDELINES:\n"
        "1. Compare the search results against the LISTENER PERSONA / LISTENER PROFILE to determine relevance.\n"
        "2. Don't list opening hours, ratings, or reviews. Instead, make general statements like 'I heard it's pretty popular' or 'It's got a great vibe'.\n"
        "3. Relate recommendations to the current time of day and listener's potential needs.\n"
        "4. If there are no great matches, riff on related topics or alternatives that might interest the listener based on their profile.\n"
        "5. Inject your personalities into the discussion, showing your different perspectives and tastes.\n"
        "6. Use casual language, including mild swearing if it fits the conversation naturally.\n"
        "7. Reference the listener's music tastes or other interests from their profile when discussing local spots."
    )

@node_registry.register(
    "data_location_report",
    "Formats location search report data",
    cost="medium",
    visible=False
)
async def get_data_location_report(query: Optional[str] = None, user=None, dj_service=None, **_) -> str:
    if query is None:
        return ""
    return await context_service.get_location_data(dj_service, user, query)

@node_registry.register(
    "instruction_events",
    "System prompt for Events interpretation",
    cost="low",
    visible=False
)
async def get_instruction_events(**_) -> str:
    return (
        "You are [SHAQUILLE], the friendly and knowledgeable expert providing engaging information about upcoming events and celebrating shout-outs from listeners worldwide.\n\n"
        "INTERACTION STYLE:\n"
        "1. Create a flowing, natural conversation about the events.\n"
        "2. Mention specific events casually, as if you're familiar with them.\n"
        "3. Incorporate personal anecdotes, opinions, or experiences related to the events or venues.\n"
        "4. Keep the tone casual, energetic, and slightly edgy, fitting a pirate radio vibe.\n"
        "5. Relate the discussion to current events, local culture, or music when relevant.\n"
        "6. If appropriate, tie in the events to upcoming music or show segments.\n\n"
        "GUIDELINES:\n"
        "1. Compare the events against the LISTENER PERSONA / LISTENER PROFILE to determine relevance. A good opportunity to reference the listener's music tastes or other interests from their profile.\n"
        "2. Try not to read directly from EVENTS DATA, interpret it, make it your own.\n"
        "3. If there are no great matches, riff on related topics or alternatives that might interest the listener based on their profile.\n"
        "4. Inject your personalities into the discussion, showing your different perspectives and tastes.\n"
        "5. Use casual language, including mild swearing if it fits the conversation naturally."
    )

@node_registry.register(
    "data_events_report",
    "Formats events data",
    cost="medium",
    visible=False
)
async def get_data_events_report(location: Optional[str] = None, country_code: Optional[str] = None, start_date: Optional[str] = None, end_date: Optional[str] = None, dj_service=None, **_) -> str:
    if location is None and country_code is None:
        return ""
    return await context_service.get_events_data(dj_service, location, country_code, start_date, end_date)

@node_registry.register(
    "instruction_shoutouts",
    "System prompt for Shoutouts interpretation",
    cost="low",
    visible=False
)
async def get_instruction_shoutouts(**_) -> str:
    return (
        "You are [SHAQUILLE], the friendly host celebrating shout-outs from listeners worldwide.\n\n"
        "PLAYING SHOUTOUT AUDIO:\n"
        "Use the exact audio file path from the data above, wrapped in $ signs with NO SPACES.\n\n"
        "CORRECT FORMAT:\n"
        "$/api/user_content/shoutouts/audio/1/1765194474.mp3$\n\n"
        "WRONG (do not add spaces):\n"
        "$ /api/user_content/shoutouts/audio/1/1765194474.mp3 $\n\n"
        "HOW TO USE:\n"
        "- Choose appropriate shoutouts to share\n"
        "- Reference or paraphrase the content in your dialogue\n"
        "- Insert the audio path exactly as shown in the data where you want it to play\n"
        "- Keep reactions natural and brief between shoutouts\n"
        "- Let the community voices do most of the talking\n\n"
        "The $filepath$ tag works on its own - don't wrap it in other tags or announce it."
    )

@node_registry.register(
    "data_shoutouts_data",
    "Fetches and formats shoutouts data autonomously",
    cost="medium",
    visible=False
)
async def get_data_shoutouts_data(
    query: Optional[str] = None,
    n_results: int = 5,
    dj_service=None,
    user=None,
    **_
) -> str:
    if user is None:
        return ""
    return await context_service.get_shoutouts_data(dj_service, user, query, n_results)

@node_registry.register(
    "instruction_hal11000_identity",
    "HAL11000 system identity",
    cost="low",
    visible=False
)
async def get_instruction_hal11000_identity(**_) -> str:
    return (
        "You are the [HAL11000], an intelligent AI Computer that assists [SHAQUILLE] & [TERRY], the interactive live on-air DJ's at PLAiR.fm - "
        "Your role is to interpret the intent from all parties. [LISTENER TXT], [SHAQUILLE] and [TERRY]."
    )

@node_registry.register(
    "instruction_hal11000_format_rules",
    "HAL11000 command format rules",
    cost="low",
    visible=False
)
async def get_instruction_hal11000_format_rules(**_) -> str:
    return (
        "FORMAT RULES - CRITICAL:\n"
        "1. ALL commands MUST be wrapped in parentheses: ( )\n"
        "2. ALL tokens MUST use curly braces: { }\n"
        "3. Case sensitivity MATTERS: Use lowercase snake_case for all commands\n"
        "4. Format: ({Action}{Content})\"Specifics\". E.g., ({play}{primary_artist})\"Artist Name\"\n"
        "5. Multiple COMMANDS must be on separate lines\n"
        "6. Output ONLY COMMANDS. Otherwise {N/A} if there is no action required"
    )

@node_registry.register(
    "instruction_hal11000_commands",
    "HAL11000 available commands",
    cost="medium",
    visible=False
)
async def get_instruction_hal11000_commands(**_) -> str:
    return (
        "AVAILABLE COMMANDS:\n\n"
        "PLAYBACK & NAVIGATION:\n"
        "{next}, {previous}, {activate}, {mute}\n\n"
        "CONTENT ACTIONS:\n"
        "{play}, {cue}, {continue}\n\n"
        "CONTENT-SEARCH:\n"
        "{song_title} - Track title\n"
        "{primary_artist} - Main artist (e.g., Nine Inch Nails)\n"
        "{similar_artists} - Artists with similar sound (e.g., Ministry, Skinny Puppy)\n"
        "{primary_genre} - Main genre (e.g., Industrial Rock)\n"
        "{secondary_genres} - Sub-genres/tags (e.g., EBM, Darkwave)\n"
        "{mood} - Emotional vibe (e.g., aggressive, anxious, melancholic)\n"
        "{style} - Production style (e.g., TR-808 drums, distorted synths, lo-fi)\n"
        "{theme} - Lyrical subject matter (e.g., alienation, decay, dystopia)\n"
        "{vocal} - Vocal delivery (e.g., whispered, screamed, distorted)\n"
        "{lyrics} - Actual lyric content\n\n"
        "SEED RADIO:\n"
        "{seed} - Modes: primary_genre, secondary_genres, mood, primary_artist, similar_artists, style, theme, lyrics, vocal\n\n"
        "PLAYLISTS:\n"
        "{playlist} - Available playlists: favorites, discovery, top hits all, top hits week, top hits day\n\n"
        "WEB-SEARCH:\n"
        "{biography}, {lyrics}, {news}, {weather}, {events}\n\n"
        "LOCAL-AMENITIES:\n"
        "({find_amenities})\"query\"\n\n"
        "USER DRIVEN CONTENT:\n"
        "{save_shoutout} - For broadcasting personal messages to PLAiR community\n"
        "{save_opinion} - For detailed music reviews and track feedback\n"
        "{play_shoutouts} - To hear community messages and announcements\n"
        "{play_opinions} - To hear what others think about specific tracks\n\n"
        "ENGAGEMENT LEVELS:\n"
        "{like} - Basic engagement: Content resonates and worth revisiting\n"
        "{superstar} - Deep emotional connection: Content that profoundly impacts or defines personal taste\n"
        "{dislike} - Content fails to connect: Reduces similar recommendations\n"
        "{ban} - Strong aversion: Permanently excludes content and similar items\n\n"
        "TIME-MODIFIERS:\n"
        "{today}, {tomorrow}, {this_week}\n\n"
        "LOCATION-MODIFIERS:\n"
        "{international}, {national}, {local}\n\n"
        "NEWS-CATEGORIES:\n"
        "{world}, {nation}, {business}, {technology}, {entertainment}, {sports}, {science}, {health}\n\n"
        "TEMPORAL-REFERENCE:\n"
        "{earlier}, {later}, {current}"
    )

@node_registry.register(
    "instruction_hal11000_rules",
    "HAL11000 command interpretation rules",
    cost="medium",
    visible=False
)
async def get_instruction_hal11000_rules(**_) -> str:
    return (
        "INTERPRETATION RULES:\n"
        "1. PLAYBACK commands stand ALONE: ({next}), ({previous}), ({activate}), ({mute})\n"
        "2. Use {play} for immediate action, then {cue} for additional requests\n"
        "3. SEARCH vs SEED: Search = find in catalog, Seed = radio based on current track\n"
        "4. LIMIT {biography}, {lyrics}, {news}, {weather}, {events} to ONE per session\n"
        "6. Use {opinion} and {current} when users provide substantial feedback about a track\n"
        "7. Use {save_shoutout} to save user messages for community sharing\n"
        "8. Use {play_shoutouts} to listen to community shoutouts\n\n"
        "CRITICAL THINKING:\n"
        "- Be VERY strict about actions required\n"
        "- Consider actions that have already taken place\n"
        "- Observe CURRENT TRACK / NEXT TRACK and CONVERSATION HISTORY\n"
        "- If DJ suggestions are not conclusive, take control based on USER PROFILE\n"
        "- Read between the lines to understand what the DJ was suggesting\n"
        "- Otherwise {N/A} if no task is required"
    )

@node_registry.register(
    "instruction_hal11000_examples",
    "HAL11000 command examples",
    cost="medium",
    visible=False
)
async def get_instruction_hal11000_examples(**_) -> str:
    return (
        "EXAMPLE 1:\n"
        "INPUT:\n"
        "[LISTENER TXT] I would love to hear some Nine Inch Nails and also some melancholic industrial tracks. "
        "Oh and I gotta say, the current track! One of my favourites! I wish everyone could get into this, its great! "
        "Oh and could I hear the latest local News.\n"
        "[SHAQUILLE] Spinning up some Nine Inch Nails, and we'll queue up some dark industrial vibes, and yes, this is one great track! "
        "Also, I'll get Terry to gather the latest News Bulletins.\n\n"
        "OUTPUT:\n"
        "({play}{primary_artist})\"Nine Inch Nails\"\n"
        "({cue}{mood})\"melancholic\"\n"
        "({cue}{secondary_genres})\"industrial\"\n"
        "({like}{current})\n"
        "({opinion}{current})\n"
        "({news}{nation})\n\n"
        "EXAMPLE 2:\n"
        "INPUT:\n"
        "[LISTENER TXT] Can you play some Radiohead? Also I'd love to know the lyrics for The High Road, "
        "Broken Bells, oh and the weather for this week!\n"
        "[TERRY] Alright! We're gonna spin up some Radiohead, "
        "and we'll be sure to track down those lyrics and get the weather for you.\n\n"
        "OUTPUT:\n"
        "({play}{primary_artist})\"Radiohead\"\n"
        "({lyrics})\"The High Road, Broken Bells\"\n"
        "({weather}{this_week})\n\n"
        "EXAMPLE 3:\n"
        "INPUT:\n"
        "[LISTENER TXT] I love this vibe! Play more tracks like this.\n"
        "[TERRY] Hell yeah, seeding based on this track's mood!\n\n"
        "OUTPUT:\n"
        "({play}{seed})\"mood\"\n\n"
        "EXAMPLE 4:\n"
        "INPUT:\n"
        "[LISTENER TXT] Play my favorite songs.\n"
        "[SHAQUILLE] You got it, firing up your favorites!\n\n"
        "OUTPUT:\n"
        "({play}{playlist})\"favorites\""
    )

@node_registry.register(
    "instruction_hal11000_verification",
    "HAL11000 verification reminder",
    cost="low",
    visible=False
)
async def get_instruction_hal11000_verification(**_) -> str:
    return (
        "VERIFICATION:\n"
        "IMPORTANT: ONLY use THE COMMANDS PROVIDED ABOVE.\n"
        "IMPORTANT: Be very strict about actions required. Think carefully.\n"
        "IMPORTANT: Consider current play-state and playlist before issuing commands."
    )

@node_registry.register(
    "track_title_artist",
    "Current track title and artist name only",
    cost="low"
)
async def get_track_title_artist(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return "CURRENT TRACK: No track playing"

    name = current_track.get('name', 'Unknown')
    artist = current_track.get('artists', 'Unknown')
    return f"CURRENT TRACK: {name} by {artist}"

@node_registry.register(
    "track_release_date",
    "Release date/year of current track",
    cost="low"
)
async def get_track_release_date(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    release = current_track.get('release_date', 'N/A')
    return f"Released: {release}"

@node_registry.register(
    "track_duration",
    "Track length/duration",
    cost="low"
)
async def get_track_duration(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    duration = current_track.get('duration', 'N/A')
    duration_sec = current_track.get('duration_seconds', 0)
    return f"Duration: {duration} ({duration_sec}s)"

@node_registry.register(
    "track_progress",
    "Playback position and progress state",
    cost="low"
)
async def get_track_progress(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    progress_pct = current_track.get('progress_percentage', 0)
    progress_sec = current_track.get('progress_seconds', 0)
    duration_sec = current_track.get('duration_seconds', 0)

    if progress_pct < 10:
        state = "Just started"
    elif progress_pct < 25:
        state = "In the early stages"
    elif progress_pct < 50:
        state = "In the first half"
    elif progress_pct < 75:
        state = "In the second half"
    elif progress_pct < 90:
        state = "Nearing the end"
    else:
        state = "Almost finished"

    return (
        f"Progress: {progress_sec}s / {duration_sec}s "
        f"({progress_pct:.1f}% complete - {state})"
    )

@node_registry.register(
    "track_style_description",
    "Musical style and genre description",
    cost="low"
)
async def get_track_style_description(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    style = current_track.get('style_description', '').strip()
    if not style:
        return ""

    return f"Style: {style}"

@node_registry.register(
    "track_vocal_info",
    "Vocal characteristics (instrumental, gender)",
    cost="low"
)
async def get_track_vocal_info(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    is_instrumental = current_track.get('instrumental', False)
    if is_instrumental:
        return "Vocals: Instrumental (no vocals)"

    vocal_gender = current_track.get('vocal_gender', '').strip()
    if vocal_gender:
        return f"Vocals: {vocal_gender}"

    return ""

@node_registry.register(
    "track_lyrics_preview",
    "Short 4-line lyrics preview",
    cost="medium"
)
async def get_track_lyrics_preview(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    if current_track.get('instrumental', False):
        return ""

    preview = current_track.get('lyrics_preview', '').strip()
    if not preview:
        return ""

    if len(preview) > 200:
        preview = preview[:200] + "..."

    return f"Lyrics Preview:\n{preview}"

@node_registry.register(
    "track_tempo",
    "Tempo/BPM of current track",
    cost="low"
)
async def get_track_tempo(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    features = current_track.get('audio_features', {})
    tempo = features.get('tempo', 0)
    if tempo > 0:
        return f"Tempo: {tempo:.0f} BPM"
    return ""

@node_registry.register(
    "track_key_mode",
    "Musical key and mode",
    cost="low"
)
async def get_track_key_mode(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    features = current_track.get('audio_features', {})
    key = features.get('key', 'N/A')
    mode = features.get('mode', 'N/A')

    if key == 'N/A' or mode == 'N/A':
        return ""

    mode_str = "Major" if mode == 1 else "Minor" if mode == 0 else str(mode)
    return f"Key: {key} {mode_str}"

@node_registry.register(
    "track_energy_dance",
    "Energy and danceability scores",
    cost="low"
)
async def get_track_energy_dance(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    features = current_track.get('audio_features', {})
    energy = features.get('energy', 0)
    dance = features.get('danceability', 0)

    if energy == 0 and dance == 0:
        return ""

    return f"Energy: {energy:.2f} | Danceability: {dance:.2f}"

@node_registry.register(
    "track_loudness",
    "Loudness in decibels",
    cost="low"
)
async def get_track_loudness(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    features = current_track.get('audio_features', {})
    loudness = features.get('loudness', 0)

    if loudness == 0:
        return ""

    return f"Loudness: {loudness:.1f}dB"

@node_registry.register(
    "track_time_signature",
    "Time signature",
    cost="low"
)
async def get_track_time_signature(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    features = current_track.get('audio_features', {})
    time_sig = features.get('time_signature', 4)
    return f"Time Signature: {time_sig}/4"

@node_registry.register(
    "track_valence",
    "Musical positivity/valence score",
    cost="low"
)
async def get_track_valence(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    features = current_track.get('audio_features', {})
    valence = features.get('valence', 0)

    if valence == 0:
        return ""

    return f"Valence (Positivity): {valence:.2f}"

@node_registry.register(
    "track_dynamic_range",
    "Dynamic range of current track",
    cost="low"
)
async def get_track_dynamic_range(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    features = current_track.get('audio_features', {})
    dynamic_range = features.get('dynamic_range', 0)

    if dynamic_range > 0:
        return f"Dynamic Range: {dynamic_range:.1f}"
    return ""

@node_registry.register(
    "track_beat_count",
    "Beat count of current track",
    cost="low"
)
async def get_track_beat_count(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    features = current_track.get('audio_features', {})
    beat_count = features.get('beat_count', 0)

    if beat_count > 0:
        return f"Beat Count: {beat_count}"
    return ""

@node_registry.register(
    "track_audio_features_full",
    "All audio features in one shot",
    cost="medium"
)
async def get_track_audio_features_full(current_track: Optional[Dict] = None, **_) -> str:
    if not current_track or current_track.get('name') == 'N/A':
        return ""

    features = current_track.get('audio_features', {})
    if not features:
        return ""

    parts = []
    if features.get('tempo', 0) > 0:
        parts.append(f"Tempo {features.get('tempo', 0):.0f} BPM")

    parts.append(f"Energy {features.get('energy', 0):.2f}")
    parts.append(f"Danceability {features.get('danceability', 0):.2f}")
    parts.append(f"Loudness {features.get('loudness', 0):.1f}dB")

    key = features.get('key', 'N/A')
    mode = features.get('mode', 'N/A')
    if key != 'N/A' and mode != 'N/A':
        mode_str = "Major" if mode == 1 else "Minor" if mode == 0 else str(mode)
        parts.append(f"Key {key} {mode_str}")

    parts.append(f"Time Sig {features.get('time_signature', 4)}/4")

    if features.get('valence', 0) > 0:
        parts.append(f"Valence {features.get('valence', 0):.2f}")

    if features.get('dynamic_range', 0) > 0:
        parts.append(f"Dynamic Range {features.get('dynamic_range', 0):.1f}")

    if features.get('beat_count', 0) > 0:
        parts.append(f"Beats {features.get('beat_count', 0)}")

    return f"Audio Features: {', '.join(parts)}"

@node_registry.register(
    "queue_next_track",
    "The next track coming up",
    cost="low"
)
async def get_queue_next_track(next_track: Optional[Dict] = None, **_) -> str:
    if not next_track or next_track.get('name') == 'N/A':
        return "NEXT TRACK: Queue is empty"

    name = next_track.get('name', 'Unknown')
    artist = next_track.get('artists', 'Unknown')
    return f"NEXT TRACK: {name} by {artist}"

@node_registry.register(
    "queue_upcoming_track",
    "The track after next",
    cost="low"
)
async def get_queue_upcoming_track(upcoming_track: Optional[Dict] = None, **_) -> str:
    if not upcoming_track or upcoming_track.get('name') == 'N/A':
        return ""

    name = upcoming_track.get('name', 'Unknown')
    artist = upcoming_track.get('artists', 'Unknown')
    return f"UPCOMING TRACK (After Next): {name} by {artist}"

@node_registry.register(
    "history_last_track",
    "The previously played track",
    cost="low"
)
async def get_history_last_track(last_track: Optional[Dict] = None, **_) -> str:
    if not last_track or last_track.get('name') == 'N/A':
        return "LAST TRACK: No previous track"

    name = last_track.get('name', 'Unknown')
    artist = last_track.get('artists', 'Unknown')
    return f"LAST TRACK (Previously Played): {name} by {artist}"

@node_registry.register(
    "queue_next_details",
    "Next track with full details",
    cost="medium"
)
async def get_queue_next_details(next_track: Optional[Dict] = None, **_) -> str:
    if not next_track or next_track.get('name') == 'N/A':
        return ""

    name = next_track.get('name', 'Unknown')
    artist = next_track.get('artists', 'Unknown')
    duration = next_track.get('duration', 'N/A')
    style = next_track.get('style_description', '').strip()

    result = f"NEXT TRACK: {name} by {artist} | Duration: {duration}"
    if style:
        result += f"\nStyle: {style}"

    return result

@node_registry.register(
    "queue_next_audio_features",
    "Audio features of next track in queue",
    cost="medium"
)
async def get_queue_next_audio_features(next_track: Optional[Dict] = None, **_) -> str:
    if not next_track or next_track.get('name') == 'N/A':
        return ""

    features = next_track.get('audio_features', {})
    if not features:
        return ""

    parts = []
    if features.get('tempo', 0) > 0:
        parts.append(f"Tempo {features.get('tempo', 0):.0f} BPM")

    parts.append(f"Energy {features.get('energy', 0):.2f}")
    parts.append(f"Danceability {features.get('danceability', 0):.2f}")
    parts.append(f"Loudness {features.get('loudness', 0):.1f}dB")

    key = features.get('key', 'N/A')
    mode = features.get('mode', 'N/A')
    if key != 'N/A' and mode != 'N/A':
        mode_str = "Major" if mode == 1 else "Minor" if mode == 0 else str(mode)
        parts.append(f"Key {key} {mode_str}")

    parts.append(f"Time Sig {features.get('time_signature', 4)}/4")

    if features.get('valence', 0) > 0:
        parts.append(f"Valence {features.get('valence', 0):.2f}")

    if features.get('dynamic_range', 0) > 0:
        parts.append(f"Dynamic Range {features.get('dynamic_range', 0):.1f}")

    if features.get('beat_count', 0) > 0:
        parts.append(f"Beats {features.get('beat_count', 0)}")

    return f"NEXT TRACK Audio Features: {', '.join(parts)}"

@node_registry.register(
    "queue_upcoming_audio_features",
    "Audio features of upcoming track (after next)",
    cost="medium"
)
async def get_queue_upcoming_audio_features(upcoming_track: Optional[Dict] = None, **_) -> str:
    if not upcoming_track or upcoming_track.get('name') == 'N/A':
        return ""

    features = upcoming_track.get('audio_features', {})
    if not features:
        return ""

    parts = []
    if features.get('tempo', 0) > 0:
        parts.append(f"Tempo {features.get('tempo', 0):.0f} BPM")

    parts.append(f"Energy {features.get('energy', 0):.2f}")
    parts.append(f"Danceability {features.get('danceability', 0):.2f}")
    parts.append(f"Loudness {features.get('loudness', 0):.1f}dB")

    key = features.get('key', 'N/A')
    mode = features.get('mode', 'N/A')
    if key != 'N/A' and mode != 'N/A':
        mode_str = "Major" if mode == 1 else "Minor" if mode == 0 else str(mode)
        parts.append(f"Key {key} {mode_str}")

    parts.append(f"Time Sig {features.get('time_signature', 4)}/4")

    if features.get('valence', 0) > 0:
        parts.append(f"Valence {features.get('valence', 0):.2f}")

    if features.get('dynamic_range', 0) > 0:
        parts.append(f"Dynamic Range {features.get('dynamic_range', 0):.1f}")

    if features.get('beat_count', 0) > 0:
        parts.append(f"Beats {features.get('beat_count', 0)}")

    return f"UPCOMING TRACK Audio Features: {', '.join(parts)}"

@node_registry.register(
    "history_last_audio_features",
    "Audio features of previously played track",
    cost="medium"
)
async def get_history_last_audio_features(last_track: Optional[Dict] = None, **_) -> str:
    if not last_track or last_track.get('name') == 'N/A':
        return ""

    features = last_track.get('audio_features', {})
    if not features:
        return ""

    parts = []
    if features.get('tempo', 0) > 0:
        parts.append(f"Tempo {features.get('tempo', 0):.0f} BPM")

    parts.append(f"Energy {features.get('energy', 0):.2f}")
    parts.append(f"Danceability {features.get('danceability', 0):.2f}")
    parts.append(f"Loudness {features.get('loudness', 0):.1f}dB")

    key = features.get('key', 'N/A')
    mode = features.get('mode', 'N/A')
    if key != 'N/A' and mode != 'N/A':
        mode_str = "Major" if mode == 1 else "Minor" if mode == 0 else str(mode)
        parts.append(f"Key {key} {mode_str}")

    parts.append(f"Time Sig {features.get('time_signature', 4)}/4")

    if features.get('valence', 0) > 0:
        parts.append(f"Valence {features.get('valence', 0):.2f}")

    if features.get('dynamic_range', 0) > 0:
        parts.append(f"Dynamic Range {features.get('dynamic_range', 0):.1f}")

    if features.get('beat_count', 0) > 0:
        parts.append(f"Beats {features.get('beat_count', 0)}")

    return f"LAST TRACK Audio Features: {', '.join(parts)}"

@node_registry.register(
    "user_basic",
    "User's name and location only",
    cost="low"
)
async def get_user_basic(user: Optional[User] = None, **_) -> str:
    if not user:
        return "Listener: Guest (Unknown Location)"

    location = user.location or "Unknown location"
    return f"Listener Location: {location}"

@node_registry.register(
    "user_local_time",
    "Current time in user's timezone (HH:MM AM/PM)",
    cost="low"
)
async def get_user_local_time(user: Optional[User] = None, **_) -> str:
    return context_service.format_user_time_str(user)

@node_registry.register(
    "user_persona",
    "User's generated personality profile",
    cost="medium"
)
async def get_user_persona(user: Optional[User] = None, **_) -> str:
    if not user or user.persona is None:
        return "LISTENER PERSONA: Guest Listener (Unknown Profile)"

    return f"LISTENER PERSONA:\n{user.persona}"

@node_registry.register(
    "user_profile",
    "User's full profile description",
    cost="high"
)
async def get_user_profile(user: Optional[User] = None, **_) -> str:
    if not user or user.profile is None:
        return "LISTENER PROFILE: Guest Listener (Unknown Profile)"

    return f"LISTENER PROFILE:\n{user.profile}"

@node_registry.register(
    "shoutout_interests",
    "User's shoutout interests and discovery topics",
    cost="free"
)
async def get_shoutout_interests(user: Optional[User] = None, **_) -> str:
    if not user or user.shoutout_interests is None:
        return "LISTENER SHOUTOUT INTERESTS: None yet"

    return f"LISTENER SHOUTOUT INTERESTS:\n{user.shoutout_interests}"

@node_registry.register(
    "user_favorite_artists",
    "User's top 5-7 favorite artists",
    cost="medium"
)
async def get_user_favorite_artists(
    user_id: Optional[int] = None,
    async_session_maker=None,
    catalog_service=None,
    **_
) -> str:
    if not user_id or not async_session_maker or not catalog_service:
        return "LISTENER'S FAVORITE ARTISTS: None (Guest)"

    async with async_session_maker() as db:
        return await context_service.get_user_favorites(user_id, db, catalog_service)

@node_registry.register(
    "user_banned_tracks",
    "Tracks the user has banned",
    cost="low"
)
async def get_user_banned_tracks(
    user_id: Optional[int] = None,
    async_session_maker=None,
    catalog_service=None,
    **_
) -> str:
    if not user_id or not async_session_maker or not catalog_service:
        return "BANNED SONGS: None (Guest)"

    async with async_session_maker() as db:
        return await context_service.get_user_banned(user_id, db, catalog_service)

@node_registry.register(
    "conversation_last_turn",
    "Just the most recent exchange",
    cost="low"
)
async def get_conversation_last_turn(
    user_id: Optional[int] = None,
    session_id: Optional[str] = None,
    async_session_maker=None,
    **_
) -> str:
    try:
        if user_id and async_session_maker:
            async with async_session_maker() as db:
                history = await get_conversation_history(
                    user_id=user_id,
                    db=db,
                    format_type='text',
                    limit=1
                )
            if history:
                return f"LAST EXCHANGE:\n{history}"
            return "LAST EXCHANGE: None"

        elif session_id:
            history = await get_conversation_history(
                temp_user_id=session_id,
                format_type='text',
                limit=1
            )
            if history:
                return f"LAST EXCHANGE:\n{history}"
            return "LAST EXCHANGE: None (Guest - no history yet)"

        return "LAST EXCHANGE: No session"
    except Exception as e:
        log_service.error(f"[NODE] Error getting last conversation: {e}")
        return "LAST EXCHANGE: Error"

@node_registry.register(
    "conversation_recent",
    "Last 3 exchanges",
    cost="medium"
)
async def get_conversation_recent(
    user_id: Optional[int] = None,
    session_id: Optional[str] = None,
    async_session_maker=None,
    **_
) -> str:
    try:
        if user_id and async_session_maker:
            async with async_session_maker() as db:
                history = await get_conversation_history(
                    user_id=user_id,
                    db=db,
                    format_type='text',
                    limit=3
                )
            if history:
                return f"CONVERSATION HISTORY:\n{history}"
            return "CONVERSATION HISTORY: None"

        elif session_id:
            history = await get_conversation_history(
                temp_user_id=session_id,
                format_type='text',
                limit=3
            )
            if history:
                return f"CONVERSATION HISTORY:\n{history}"
            return "CONVERSATION HISTORY: None (Guest - no history yet)"

        return "CONVERSATION HISTORY: No session"
    except Exception as e:
        log_service.error(f"[NODE] Error getting conversation history: {e}")
        return "CONVERSATION HISTORY: Error"

@node_registry.register(
    "weather_current",
    "Current weather condition only",
    cost="low"
)
async def get_weather_current(
    user_id: Optional[int] = None,
    async_session_maker=None,
    **_
) -> str:
    if not user_id or not async_session_maker:
        return "CURRENT WEATHER: Unknown (Guest)"

    async with async_session_maker() as db:
        return await context_service.get_db_weather(user_id, db)

@node_registry.register(
    "station_current_show",
    "Current show name and time remaining",
    cost="low"
)
async def get_station_current_show(**_) -> str:
    _, current, _ = context_service.get_show_details()
    return f"CURRENT SHOW: {current}"

@node_registry.register(
    "station_next_show",
    "Upcoming show details",
    cost="low"
)
async def get_station_next_show(**_) -> str:
    _, _, next_show = context_service.get_show_details()
    return f"NEXT SHOW: {next_show}"

@node_registry.register(
    "station_previous_show",
    "Previous show details",
    cost="low"
)
async def get_station_previous_show(**_) -> str:
    previous, _, _ = context_service.get_show_details()
    return f"PREVIOUS SHOW: {previous}"

@node_registry.register(
    "station_full_schedule",
    "All three shows (prev/current/next)",
    cost="low"
)
async def get_station_full_schedule(**_) -> str:
    previous, current, next_show = context_service.get_show_details()
    return (
        f"PREVIOUS SHOW: {previous}\n"
        f"CURRENT SHOW: {current}\n"
        f"NEXT SHOW: {next_show}"
    )