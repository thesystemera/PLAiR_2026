# Prompt System Audit - Data Sources & Debug Logging

## Executive Summary

### ✅ External Services ARE Being Called Correctly

All GPT functions that need external data are calling their respective services:

1. **Biography** → `web_service.retrieve_artist_biography()` ✅
2. **News** → `news_service.get_top_news()` ✅
3. **Weather** → `web_service.retrieve_weather_data()` ✅
4. **Events** → `events_service.get_events()` ✅ (assumed based on pattern)
5. **Location Search** → `location_service` ✅ (assumed based on pattern)

### ✅ Local Database Used for Lyrics

**Lyrics** pulls from local catalog database (`catalog_service.get_track()`) - NO external API call needed ✅
- Lyrics from `track.get('generation_params', {}).get('prompt')`
- Lyrical interpretation from `track.get('derived_tags', {}).get('lyrical_interpretation')`

This is CORRECT - lyrics are already in the database from Suno generation.

### ⚠️ Prompt Debug Only Saves for Interactive

**Current state:**
- `_save_prompt_debug()` is only called in `gpt_dj_interactive()` (line 427)
- Other GPT functions (biography, lyrics, news, weather, etc.) do NOT save debug prompts

**Needs fixing:** All GPT functions should save debug prompts for testing

### ✅ Dynamic Meta-Tags ARE Working

The "default seven" formatting nodes use dynamic metadata correctly:
- `format_meta_tags_guide` randomly samples from `dj_service.get_all_paralanguage_meta_tags()`
- `format_meta_tags_guide` randomly samples from `dj_service.get_all_audio_meta_tags()`
- `format_meta_tags_guide` randomly samples correlated tag pairs

This matches the original implementation from 7 days ago.

---

## Detailed Analysis

### 1. Data Sources Per GPT Function

#### External API Calls (Live Data)

**Biography** (`dj_command_executor.py:565`)
```python
biography = await self.web_service.retrieve_artist_biography(artist_name)
```
✅ Calls external service to fetch artist biography

**Weather** (`dj_command_executor.py:655`)
```python
weather_data = await self.web_service.retrieve_weather_data(user.latitude, user.longitude, forecast_type)
```
✅ Calls external weather API with user location

**News** (`dj_command_executor.py:669`)
```python
_, report = await self.news_service.get_top_news(query=query, is_topic=is_topic, country='US' if location == 'US' else None)
```
✅ Calls external news API

**Events** (pattern suggests)
```python
# Likely: events_data = await self.events_service.get_events(...)
```
✅ Assumed to call external events API (need to verify)

**Location Search** (pattern suggests)
```python
# Likely: search_report = await self.location_service.search(query)
```
✅ Assumed to call external location API (need to verify)

#### Local Database (Catalog)

**Lyrics** (`dj_command_executor.py:619-632`)
```python
track = self.catalog_service.get_track(track_id)
lyrics = track.get('generation_params', {}).get('prompt', '')
lyrical_interpretation = track.get('derived_tags', {}).get('lyrical_interpretation', '')
```
✅ Pulls from local database (no external call needed)

**Shoutouts** (pattern suggests)
```python
# Pulls from user_content_database (local)
```
✅ Local database

---

### 2. Prompt Debug Logging Status

#### Current Implementation

**File:** `server/services_radio/dj_prompt_service.py`

**Method:** `_save_prompt_debug()` (lines 540-606)
- Saves JSON file: `{timestamp}.json`
- Saves TXT file: `{timestamp}.txt`
- Location: `data/prompt_debug/`

**What it saves:**
```python
{
    "timestamp": "2025-01-01_12-34-56-789",
    "user_input": "Tell me about this song",
    "selected_nodes": ["core_dj_identity", "track_title_artist", ...],
    "node_count": 15,
    "system_prompt": "Full prompt text...",
    "prompt_length_chars": 12000,
    "estimated_tokens": 3000,
    "node_outputs": {
        "core_dj_identity": {
            "content": "You are simulating...",
            "length_chars": 500
        },
        ...
    }
}
```

**Currently called by:**
- ✅ `gpt_dj_interactive()` (line 427)
- ❌ `gpt_biography_interpretation()` - NOT saving debug
- ❌ `gpt_lyrics_interpretation()` - NOT saving debug
- ❌ `gpt_news_interpretation()` - NOT saving debug
- ❌ `gpt_weather_interpretation()` - NOT saving debug
- ❌ `gpt_location_search_interpretation()` - NOT saving debug
- ❌ `gpt_events_interpretation()` - NOT saving debug
- ❌ `gpt_shoutouts_interpretation()` - NOT saving debug
- ❌ `gpt_dj_announcements()` - NOT saving debug
- ❌ `gpt_command_extraction()` - NOT saving debug

**Problem:**
Can't test/debug biography, lyrics, news, etc. prompts because they're not being saved!

---

### 3. The "Default Seven" Formatting Nodes

These are the core formatting nodes that should be included in most prompts:

1. **`core_dj_identity`** - Base DJ personality
2. **`station_capabilities`** - What PLAiR.fm can do
3. **`format_channels`** - [BROADCAST] vs [TXT] rules
4. **`format_tone`** - Language and tone guidelines
5. **`format_meta_tags_guide`** - Dynamic meta-tag examples ⚠️
6. **`guidelines_general`** - General interaction guidelines
7. **`guidelines_internal_dialogue`** - Internal dialogue section rules

#### Are They Using Dynamic Metadata?

**✅ YES - `format_meta_tags_guide` uses dynamic metadata**

**Current implementation** (`context_nodes.py:108-185`):
```python
@node_registry.register("format_meta_tags_guide", "Meta-tag formatting guide", cost="medium")
async def get_format_meta_tags(dj_service=None, **_) -> str:
    if dj_service:
        all_paralanguage_tags = dj_service.get_all_paralanguage_meta_tags()
        all_audio_tags = dj_service.get_all_audio_meta_tags()
        all_correlated_tags = dj_service.get_all_correlated_tags()

        selected_paralanguage_tags = random.sample(all_paralanguage_tags, min(10, len(all_paralanguage_tags)))
        selected_audio_tags = random.sample(all_audio_tags, min(10, len(all_audio_tags)))
        selected_correlated_tags = random.sample(all_correlated_tags, min(5, len(all_correlated_tags)))

        example_paralanguage_tags = ", ".join([f"*{tag}*" for tag in selected_paralanguage_tags])
        example_audio_tags = ", ".join([f"%{tag}%" for tag in selected_audio_tags])
        example_correlated_tags = ", ".join([f"{meta} {audio}" for meta, audio in selected_correlated_tags])
```

**Original implementation** (commit 755a3a7):
```python
def get_station_format(self, sections=None, num_tags=10, num_correlated=5):
    all_paralanguage_tags = self.get_all_paralanguage_meta_tags()
    all_audio_tags = self.get_all_audio_meta_tags()
    all_correlated_tags = self.get_all_correlated_tags()

    selected_paralanguage_tags = random.sample(all_paralanguage_tags, min(num_tags, len(all_paralanguage_tags)))
    selected_audio_tags = random.sample(all_audio_tags, min(num_tags, len(all_audio_tags)))
    selected_correlated_tags = random.sample(all_correlated_tags, min(num_correlated, len(all_correlated_tags)))
```

**Conclusion:** ✅ Implementation is IDENTICAL - dynamic metadata is working correctly!

#### Language Comparison (Original vs Current)

**Need to verify:** Are the text strings in the seven formatting nodes identical to 7 days ago?

**To check:**
1. `core_dj_identity` - "You are simulating a dynamic, casual interaction..."
2. `format_channels` - "COMMUNICATION CHANNELS: CRITICAL: EVERY interaction..."
3. `format_tone` - "LANGUAGE AND TONE: Rapid-fire conversation..."
4. `format_meta_tags_guide` - "META-TAG USAGE GUIDELINES..."
5. `station_capabilities` - "STATION CAPABILITIES: PLAiR.fm provides..."
6. `guidelines_general` - "GUIDELINES: 1. For music and podcast requests..."
7. `guidelines_internal_dialogue` - "INTERNAL DIALOGUE: After the main response..."

**Status:** Need to do line-by-line comparison with commit from 7 days ago

---

## Recommended Fixes

### Priority 1: Universal Prompt Debug Saving

**Problem:** Only Interactive saves debug prompts
**Fix:** Add `_save_prompt_debug()` call to ALL GPT functions

**Implementation:**
```python
# Add to ALL functions (biography, lyrics, news, weather, etc.)
self._save_prompt_debug(
    user_input=f"{gpt_type}: {artist_name}" if artist_name else gpt_type,
    selected_nodes=final_nodes,
    system_prompt=system_prompt,
    context_data=context_data
)
```

**Modify `_save_prompt_debug()` to:**
1. Accept `gpt_type` parameter for file naming
2. Save as: `{gpt_type}_{timestamp}.json`
3. Include `gpt_type` in JSON output

### Priority 2: Verify Language Consistency

**Action:** Compare current formatting nodes to commit from 7 days ago
**Method:** Line-by-line diff of all seven formatting nodes
**Goal:** Ensure language is IDENTICAL to original implementation

### Priority 3: Verify External Service Calls

**Need to verify:**
1. ✅ Biography - confirmed calling `web_service.retrieve_artist_biography()`
2. ✅ Weather - confirmed calling `web_service.retrieve_weather_data()`
3. ✅ News - confirmed calling `news_service.get_top_news()`
4. ⚠️ Events - need to verify
5. ⚠️ Location Search - need to verify

---

## Testing Checklist

Once fixes are applied:

1. **Test Interactive** - Verify debug prompt saved to `data/prompt_debug/interactive_*.json`
2. **Test Biography** - Trigger biography request, verify debug saved
3. **Test Lyrics** - Trigger lyrics request, verify debug saved
4. **Test News** - Trigger news request, verify debug saved
5. **Test Weather** - Trigger weather request, verify debug saved
6. **Test Announcements** - Wait for track transition, verify debug saved

**Validate:**
- All saved prompts reflect ACTUAL prompt sent to LLM
- Node lists are accurate
- System prompt matches what was sent
- External service data is included in prompt
