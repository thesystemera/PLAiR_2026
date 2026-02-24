# Node System Architecture

## Overview

The Node System is PLAiR.fm's dynamic context assembly for DJ AI prompts. Instead of sending ALL context to the LLM every time, we use modular "nodes" that can be selectively included based on the task.

**For Interactive mode:** A "Producer AI" analyzes user input and selects ONLY relevant nodes.
**For other modes:** Each GPT function uses a pre-defined set of required nodes.

**Result:** ~80% token reduction (10,000 → 2,000 tokens per request)

---

## Core Components

### 1. Node Definitions (`context_nodes.py`)
Individual async functions that format data into prompt strings. Each registered via decorator:

```python
@node_registry.register(
    "track_title_artist",
    "Current track title and artist",
    cost="low",
    visible=True  # Visible to Producer AI
)
async def get_track_title_artist(current_track: Dict = None, **_) -> str:
    return f"CURRENT TRACK: {current_track['name']} by {current_track['artists']}"
```

**67 total nodes** across categories:
- **Formatting** (identity, channels, tone, meta-tags, guidelines)
- **Instruction** (biography, lyrics, news, weather, HAL11000 commands)
- **Data** (biography text, lyrics text, news report, weather data)
- **Track** (title, style, audio features, lyrics preview)
- **User** (persona, profile, favorites, banned tracks)
- **Queue** (next track, upcoming track, audio features)
- **History** (last track, audio features)
- **Conversation** (recent exchanges, last turn)
- **System** (time, weather, show schedule)

### 2. Node Registry (`context_node_registry.py`)
Stores nodes and executes them in parallel using `asyncio.gather`:

```python
async def fetch_nodes(self, node_keys: List[str], **kwargs) -> Dict[str, str]:
    results = await asyncio.gather(*[self._nodes[key](**kwargs) for key in node_keys])
    return {key: result for key, result in zip(node_keys, results)}
```

**Performance:** Executes 10-15 nodes in ~100-300ms

### 3. Producer AI (`context_router_service.py`)
Uses Gemini Flash Lite to select nodes based on user input (Interactive mode only):

**Flow:**
1. User: "Tell me about this song"
2. Producer AI analyzes intent
3. Returns: `["track_title_artist", "track_style_description", "track_audio_features_full", ...]`

**Caching:** Exact match (MD5) + semantic similarity (0.85+ threshold) → ~80% hit rate

### 4. Data Fetching (`context_service.py`)
Bulk-fetches raw data (user, tracks, playback state) upfront to prevent redundant DB calls.

### 5. Prompt Builder (`dj_prompt_service.py`)
Orchestrates the flow via unified `_get_nodes_unified()` method.

---

## Unified Config-Based Architecture

All GPT functions share the same flow but with different configurations:

```python
self.node_configs = {
    'interactive': {
        'required_nodes': [/* 7 formatting nodes */],
        'use_ai_picker': True  # Producer AI selects content nodes dynamically
    },
    'biography': {
        'required_nodes': [/* formatting + instruction_biography + data_biography */],
        'use_ai_picker': False  # Static node list
    },
    # ... 8 more GPT configs
}
```

**Unified method:**
```python
async def _get_nodes_unified(gpt_type, user_id, session_id, user_input=None, **extra):
    config = self.node_configs[gpt_type]
    final_nodes = config['required_nodes'].copy()

    if config['use_ai_picker']:
        dynamic_nodes = await producer_ai.determine_nodes(user_input)
        final_nodes.extend(dynamic_nodes)

    return await node_registry.fetch_nodes(final_nodes, **raw_data)
```

---

## Node Visibility System

Nodes have a `visible` parameter controlling whether Producer AI can select them:

**Visible = False (27 nodes):** Function-specific, hidden from Producer AI
- 7 Interactive hardcoded formatting nodes (always included)
- 14 Instruction/Data nodes (biography, lyrics, news, weather, events, location, shoutouts)
- 6 HAL11000 command extraction nodes

**Visible = True (40 nodes):** Content nodes Producer AI can select
- Track nodes (title, style, audio features, lyrics, etc.)
- User nodes (persona, profile, favorites, banned)
- Queue/History nodes
- Conversation nodes
- System nodes (weather, time, show schedule)

**Future:** `visible` will become an array like `visible=['interactive', 'announcer']` to specify which GPTs can use which nodes.

---

## GPT Functions

### 1. Interactive (Dynamic)
**Required nodes:** 7 formatting nodes (identity, channels, tone, meta-tags, guidelines)
**AI Picker:** YES - selects 8-15 content nodes based on user input
**Flow:** Required + Dynamic → ~15 nodes total

### 2. Biography
**Required nodes:** Formatting + `instruction_biography` + `data_biography` + user/conversation
**AI Picker:** NO
**Flow:** Static list → ~11 nodes

### 3. Lyrics
**Required nodes:** Formatting + `instruction_lyrics` + `data_lyrics` + user/conversation
**AI Picker:** NO
**Flow:** Static list → ~11 nodes

### 4. News
**Required nodes:** Formatting + `instruction_news` + `data_news_report` + user/weather/conversation
**AI Picker:** NO
**Flow:** Static list → ~13 nodes

### 5. Weather
**Required nodes:** Formatting + `instruction_weather` + `data_weather_report` + time/conversation
**AI Picker:** NO
**Flow:** Static list → ~8 nodes

### 6. Location Search
**Required nodes:** Formatting + `instruction_location_search` + `data_location_report` + user/weather
**AI Picker:** NO
**Flow:** Static list → ~13 nodes

### 7. Events
**Required nodes:** Formatting + `instruction_events` + `data_events_report` + user/weather
**AI Picker:** NO
**Flow:** Static list → ~13 nodes

### 8. Shoutouts
**Required nodes:** Formatting + `instruction_shoutouts` + `data_shoutouts_data` + user/weather
**AI Picker:** NO
**Flow:** Static list → ~11 nodes

### 9. Announcements
**Required nodes:** Core identity + comprehensive track/queue/user/show nodes (no instruction nodes)
**AI Picker:** NO *(future: could be YES for time-based dynamic selection)*
**Flow:** Static comprehensive list → ~23 nodes

### 10. Command Extraction (HAL11000)
**Required nodes:** 6 HAL instruction nodes + track/user/conversation
**AI Picker:** NO
**Flow:** Static list → ~12 nodes

---

## Performance

**Interactive (cache hit):**
- Gather data: ~50ms
- Producer AI (cached): ~150ms
- Fetch 15 nodes: ~100ms
- **Total: ~300ms + LLM time**

**Interactive (cache miss):**
- Gather data: ~50ms
- Producer AI (fresh): ~2500ms
- Fetch 15 nodes: ~100ms
- **Total: ~2650ms + LLM time**

**Static GPTs (biography, lyrics, etc.):**
- Gather data: ~50ms
- Fetch 8-13 nodes: ~80ms
- **Total: ~130ms + LLM time**

---

## Future Enhancements

### 1. Streaming Tools (High Priority - Replaces Separate GPTs)
**Current:** Separate GPT functions (weather, news, biography) triggered by command executor
**Future:** Convert data nodes to LLM tools callable mid-response with streaming

**Why this is better:**
- Single streaming response instead of 2 separate GPT calls
- TTS buffer hides tool latency (DJ talks while external API loads)
- Eliminates code duplication (weather GPT, weather command executor, etc.)
- More natural conversation flow
- Cheaper (1 LLM call vs 2)

**Implementation:**
```python
# DJ streaming response with tool calls
"Let me check the extended forecast for you..."  # TTS starts playing
  ↓ [calls get_detailed_weather(days=7) tool while TTS plays buffered audio]
"...looks like Saturday will be sunny, but Sunday's bringing rain..."
```

**Tools to implement:**
- `get_detailed_weather(location, days)` - Replace weather GPT
- `search_artist_bio(artist_name)` - Replace biography GPT
- `search_news(topic, location)` - Replace news GPT
- `search_events(location, genre, dates)` - Replace events GPT
- `search_shoutouts(query, filters)` - Enhance shoutouts beyond generic
- `search_tracks(query, mood, genre)` - Dynamic music search

**Key requirement:** Gemini 2.5 Flash supports streaming + function calling ✓

**Prime candidate:** Weather - simple, external API, perfect for testing

---

### 2. Dynamic Announcer
**Current:** Announcements use a static comprehensive node list (~23 nodes)
**Future:** Enable `use_ai_picker: True` with time-based selection
- Short transition (5 sec): Select minimal nodes (track title + next track)
- Long transition (30 sec): Select comprehensive nodes (full track details + lyrics + weather)

### 3. Array-Based Visibility
**Current:** `visible=True` or `visible=False`
**Future:** `visible=['interactive', 'announcer']`
- Allows nodes to be available to multiple GPTs
- Example: `track_audio_features_full` could be `visible=['interactive', 'announcer']`
- Gives fine-grained control over which GPTs can use which nodes

### 4. Conditional Node Groups
**Future:** Define node groups that are conditionally included
- Example: "If user asks about weather, include weather_extended_forecast"
- Allows more sophisticated node selection logic

---

### 5. Hybrid Architecture: Nodes + Tools
**Vision:** Nodes for baseline context (fast), Tools for dynamic actions (flexible)

**Nodes (pre-loaded, fast):**
- Track metadata, user profile, queue state
- Simple current weather, recent shoutouts
- Always-needed context

**Tools (on-demand, flexible):**
- Detailed weather forecasts, specific artist lookups
- Targeted searches (shoutouts, news, events, tracks)
- Unpredictable requests

**Best of both worlds:** Fast for common cases, flexible for edge cases

---

## Debug & Testing

All GPT functions save debug prompts to `data/prompt_debug/`:
- `{gpt_type}_{timestamp}.json` - Full prompt data
- `{gpt_type}_{timestamp}.txt` - Human-readable format

**Includes:**
- Selected nodes list
- Individual node outputs
- Full system prompt
- Token estimates

---

## Summary

**The node system provides:**
- ✅ 80% token reduction via selective context inclusion
- ✅ Unified architecture across all 10 GPT functions
- ✅ Per-GPT control (required nodes + AI picker flag)
- ✅ Dynamic selection for Interactive via Producer AI
- ✅ Static optimization for specialized functions
- ✅ Parallel node execution for performance
- ✅ Comprehensive debug logging
- ✅ Future-ready (dynamic announcer, array visibility)

**Files:**
- `context_nodes.py` - Node definitions (67 nodes)
- `context_node_registry.py` - Registry & execution
- `context_router_service.py` - Producer AI (node selection)
- `context_service.py` - Data fetching
- `dj_prompt_service.py` - Unified orchestration
