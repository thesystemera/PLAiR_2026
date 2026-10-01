# Node System Architecture

## Overview

The Node System is PLAiR.fm's dynamic context assembly for DJ AI prompts. Instead of sending ALL context to the LLM every time, we use modular "nodes" that can be selectively included based on the task.

**For Interactive mode:** A "Producer AI" analyzes user input and selects ONLY relevant nodes.
**For other modes:** Each GPT function uses a pre-defined set of required nodes.

**Result:** ~80% token reduction (10,000 → 2,000 tokens per request)

**LLM provider:** role chains in `server/config/settings.py` (`services/llm_router.py`): the DJ's tool turn runs on `LLM_DJ` (gemini-2.5-flash, then 3.5-flash-lite), the Producer on `LLM_LIVE` (3.5-flash-lite), segments and the announcer on DeepSeek with Gemini as fallback. "GPT function" and the `gpt_*` method names are legacy naming only - no OpenAI models are used.

**Checked against the code on 1 Oct 2026.** The second "HAL11000" command-extraction prompt was retired on 29 Sep 2026: the DJ acts through tool calls only (`server/services_radio/dj_tools.py`, `CLAUDE.md` section 5), and the Producer also returns a tool plan alongside the nodes it picks.

---

## Core Components

### 1. Node Definitions (`context_nodes.py`)
Individual async functions that format data into prompt strings. Each registered via decorator:

```python
@node_registry.register(
    "track_title_artist",
    "Current track title and artist",
    cost="low",
    visible=True,  # Visible to Producer AI
    role="live"    # "system" = fixed, cacheable prefix; "live" (default) = goes in the user message
)
async def get_track_title_artist(current_track: Dict = None, **_) -> str:
    return f"CURRENT TRACK: {current_track['name']} by {current_track['artists']}"
```

**Roles:** the DJ's system prompt is only the `system` nodes of its config, in config order, plus the tool declarations, so it is identical for every listener and turn and is held in a Gemini cache. Every `live` node (track, queue, pulse, weather, profile, conversation, studio clock) goes in the user message.

**75 nodes** (count of `node_registry.register` calls) across categories:
- **Formatting** (identity, channels, tone, performance tags, guidelines)
- **Instruction** (biography, lyrics, news, weather, DJ tools)
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
Uses Gemini Flash-Lite (the `LLM_LIVE` chain) to select nodes based on user input (Interactive mode only). `determine_route` also returns `needs_tools`, a `tool_plan` and the City Pulse topic/kinds for the turn:

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
    'interactive_tools': {
        'required_nodes': [/* formatting + guidelines + instruction_dj_tools, tool_guidance, studio_clock, city_pulse ... */],
        'use_ai_picker': True  # Producer AI selects content nodes dynamically
    },
    'biography': {
        'required_nodes': [/* formatting + instruction_biography + data_biography */],
        'use_ai_picker': False  # Static node list
    },
    # ... lyrics, news, weather, location_search, events, shoutouts,
    #     announcements (with time_presets), radio_segment, radio_segment_shared
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

**Visible = False:** Function-specific, hidden from Producer AI
- The interactive turn's hardcoded formatting and guideline nodes (always included)
- Instruction/Data nodes (biography, lyrics, news, weather, events, location, shoutouts)

**Visible = True:** Content nodes Producer AI can select
- Track nodes (title, style, audio features, lyrics, etc.)
- User nodes (persona, profile, favorites, banned)
- Queue/History nodes
- Conversation nodes
- System nodes (weather, time, show schedule)

**Future:** `visible` will become an array like `visible=['interactive', 'announcer']` to specify which GPTs can use which nodes.

---

## GPT Functions

### 1. Interactive (`interactive_tools`, Dynamic)
**Required nodes:** formatting and guideline nodes, `instruction_dj_tools`, `tool_guidance`, `station_recent_airings`, `studio_clock`, `city_pulse`
**AI Picker:** YES - the Producer selects content nodes based on user input and plans the tools
**Flow:** Required + Dynamic, then the tool loop (`ai_service.run_gemini_tool_turn`)

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
**AI Picker:** NO
**Flow:** A node list chosen by the length of the music window (`time_presets`), plus a talking-points menu from the City Pulse

### 10. Radio Mode segments (`radio_segment`, `radio_segment_shared`)
**Required nodes:** the segment's instruction and data plus `segment_length`
**AI Picker:** NO
**Flow:** Static list. The shared variant is written once per city and half hour.

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

### 1. Tools (built September 2026)
The DJ's actions and lookups are tool calls in one turn: 22 tools in `server/services_radio/dj_tools.py` (`TOOL_REGISTRY`), run by `ai_service.run_gemini_tool_turn`. Segment tools (news, weather, events, places, biography, lyrics, shoutouts) schedule the produced segment, which still uses the static node configs above. Streaming the reply sentence by sentence was tried and rejected (it conflicts with the performance planner). Details: `CLAUDE.md` section 5.

---

### 2. Dynamic Announcer (built)
Announcements pick a node list by the length of the music window (`time_presets`: minimal, quick, standard, full, extended, everything) and get a talking-points menu sized to it.

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
- ✅ Unified architecture across all prompt configs
- ✅ Per-GPT control (required nodes + AI picker flag)
- ✅ Dynamic selection for Interactive via Producer AI
- ✅ Static optimization for specialized functions
- ✅ Parallel node execution for performance
- ✅ Comprehensive debug logging
- ✅ System/live roles so the fixed prefix can be cached

**Files:**
- `context_nodes.py` - Node definitions (75 nodes)
- `context_node_registry.py` - Registry & execution
- `context_router_service.py` - Producer AI (node selection)
- `context_service.py` - Data fetching
- `dj_prompt_service.py` - Unified orchestration
