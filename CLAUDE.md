# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Radio (PLAiR.fm) is a full-stack music streaming application with an AI DJ that controls playback, manages playlists, and interacts with users via voice/text. The system features real-time audio processing, WebSocket-based state synchronization, and advanced audio engine capabilities including dual-buffer crossfading.

**Architecture:** React + Vite frontend, FastAPI backend, PostgreSQL (SQLAlchemy async + psycopg2), WebSocket for real-time updates, Google Gemini (`google-genai`) as the LLM, local Chatterbox-Turbo TTS engine (`tts_chatterbox/`) for DJ voices.

**Live site:** `https://plair.live` (nginx at `C:\nginx` proxies to backend :8000 and frontend :3000). "PLAiR.fm" is only the on-air brand name.

## Working Rule: Root Causes, Not Band-Aids

When something misbehaves, find out why it happened before changing code: read the logs and the raw model input/output for that turn, reproduce it, and fix the cause. Don't add guards, filters or retries that hide the symptom. If the cause can't be found yet, say so and add the logging needed to find it.

## Working Rule: Go Easy on Paid LLM Calls

Gemini calls cost the owner money and share one quota with the live station. Don't bulk-test against it: keep replays and live test runs to the few calls needed to answer the question (tens, not hundreds), run them one or two at a time, say what you are about to run and roughly how many calls before you start, and never while the owner is testing the app. On 2026-10-01 a 960-call replay caused a 429 on a live DJ turn.

## Environment & Infrastructure

All configuration lives in the repo-root `.env` (template: `.env.example`), loaded by `server/config/settings.py`.

- **PostgreSQL 18** on `localhost:5433` with 4 databases: `ai_radio` (users, devices, preferences, conversations, play_events, analytics), `ai_radio_catalog` (tracks), `ai_radio_user_content` (shoutouts), `ai_radio_embeddings` (vector caches). Tables are created at startup; the catalog and shoutouts **self-sync from their JSON metadata files on every boot**, and the TTS clip cache re-indexes from the ID3 tags of files in `data/tts_dj_engine_data/`. A fresh database therefore only needs the 4 empty databases to exist, plus user data restored separately.
- **Catalog media** (~620 GB) lives outside the repo at `CATALOG_DIR` (currently `D:/catalog`). Only tracks with a mastered WAV in `master_wav/` are loaded into the catalog.
- **GPU placement:** `CUDA_DEVICE_ORDER=PCI_BUS_ID` + `CUDA_VISIBLE_DEVICES=0` pin everything to the **Quadro P6000** (Pascal, sm_61). The Quadro RTX 6000 (index 1) is reserved for the owner's other projects. Pascal has no efficient float16, so Whisper runs with `WHISPER_COMPUTE_TYPE=int8`. Never hard-code `cuda:N` indexes.
- **Vector indexes** (Annoy `.ann` files in `data/embeddings/`) use `rowid - 1` / `id - 1` as item ids. Each vector service checks the index size against the database at startup and rebuilds if stale, so deleting `.ann` files is always safe.
- **Shared machine:** the same nginx also serves other sites (lifespan.ink, deepmirror.live, moneyprinter.live, realityvirtual.co), and the owner runs other GPU/AI projects on this box. Never kill python/node/nginx/java by image name: other projects use them. Don't leave PLAiR running for no reason; start it for testing, stop it after. nginx runs elevated: `nginx -s reload` needs an **admin** shell.
- **Starting PLAiR:** `external_components/plair_start.bat` is the one start script (the owner's "PLAiR Start" desktop shortcut points here): it stops any older PLAiR backend first (`plair_stop.ps1`, by command line, window included) and refuses to start a second one, rebuilds the frontend, starts/reloads nginx gracefully, launches the backend window and runs the smoke test. The old `restart_all.bat` was removed on 2026-09-30.
- **Running production:** the backend runs in a console window titled `Plair Backend (Port 8000)` (`cmd /k ... start.py`) bound to `127.0.0.1:8000` (`HOST` in `.env`); nginx proxies `plair.live` → it and serves `client/dist` directly. Stop only PLAiR with `powershell -File external_components/plair_stop.ps1` (finds the backend by its command line, so it also catches windows titled "Administrator: ..." and closes the window, not just Python), then relaunch it with PLAiR Start. **Deploying is the agent's job:** when a batch of work is done, committed and pushed, launch the owner's own shortcut so the window appears on their desktop exactly as if they had pressed it: `explorer.exe "C:\Users\info\Desktop\PLAiR Start.lnk"` (it stops the old backend, rebuilds the frontend, reloads nginx, starts the backend window and runs the smoke test), then check `data/logs/radio.log` and the smoke test result. Never start the backend any other way (`Start-Process`, `cmd /c start`, a background shell): those windows are invisible on the owner's desktop and lead to duplicates. Deploy the frontend with `npm run build` (no nginx reload needed). For private test boots next to a running production backend: copy `data/embeddings` somewhere and run `EMBEDDINGS_DIR=<copy> TTS_SERVER_EXTERNAL=true HOST=127.0.0.1 PORT=8011 python start.py` (two backends must not share the Annoy files; production has them open). Launch it with a background shell of its own, not `&` inside another command.
- **Offline quality gate:** `powershell -File scripts/check-quality.ps1 [-SkipBuild]` (skill `offline-audit`): Python syntax, Ruff F (undefined/unused names), Vulture, ESLint (zero warnings; hook deps and `console.*` are errors, use `logger`), `check:ui-state` (selectors that read UIState fields that don't exist, engine keys `reportEngineStatus` drops), Knip (dead files/exports/deps), build. Run it after a batch of edits; it needs no server. Dev tools: `server/requirements-dev.txt`, Knip in `client`.
- **Smoke test:** `E:/AI_RADIO/.venv/Scripts/python.exe tests/smoke_test.py [--base URL] [--skip-dj]` — health, catalog, MP3/Opus streaming, semantic search, TTS engine, and a guest WebSocket DJ conversation that must produce speech. Run it after every backend restart or deploy.

## Development Commands

### Frontend (Client)
```bash
# Development server (port 3000)
cd client
npm run dev

# Production build
npm run build

# Preview production build
npm run preview
```

### Backend (Server)
```bash
# Start backend server (port 8000; override with HOST/PORT env vars). Also launches the TTS engine.
cd server
E:/AI_RADIO/.venv/Scripts/python.exe start.py

# Add missing columns after changing models.py
E:/AI_RADIO/.venv/Scripts/python.exe run_migration.py
```

**Note:** Backend uses Windows-specific paths. The project uses a Python virtual environment at `E:/AI_RADIO/.venv/` (Python 3.11, dependencies pinned in `server/requirements.txt`).

### TTS Engine (tts_chatterbox/)
Separate process with its own venv at `tts_chatterbox/.venv`; the backend starts and stops it automatically (`TTS_SERVER_EXTERNAL=true` to manage it yourself). Logs go to `data/logs/tts_server.log`. Setup: `tts_chatterbox/README.md`.

## Critical Architecture Patterns

### 1. Single Source of Truth (SSOT) - UIStateContext

**File:** `client/src/contexts/UIStateContext.jsx`

This is the **most critical architectural pattern** in the frontend. Read `docs/ARCHITECTURE_SSOT.md` before making any UI state changes.

**Core Principle:**
- **Engines** (PlaybackContext, useDJAudioStream, VoiceRecordingContext) produce raw data
- **UIStateContext** derives visual state from engine data (Publisher/Subscriber pattern)
- **Views** (components) consume state, do NOT manage or transform it

**DO NOT:**
- Pass engine state through component props
- Make components coordinate state between engines
- Add business logic to view components
- Report engine status from views

**DO:**
- Engines call `reportEngineStatus()` to publish their state to UIState
- Components read from UIState via **slice subscriptions**: `useUISelector(state => ({ a: state.a, isPlaying: state.engineState.is_playing }))` (shallow-compared, re-renders only when the selected slice changes). Select the narrowest values you need (e.g. `state.engineState.currentTrack?.id`, not all of `engineState`). There is no whole-state hook (`useUIState` was removed); `npm run check:ui-state` fails on a selector that reads a field UIState doesn't have. `useUIStateGetter()` reads the latest state on demand inside handlers without subscribing.
- Playback: components that only call actions use `usePlaybackActions()` (stable: playTrack, next, previous, seek, audio, talkBreak...); `usePlaybackConnected()` for the socket flag. Playback state reaches components through UIState (`engineState`), not a whole-context hook.
- Theme: `useDynamicTheme()` changes once per track (colours). `getCategoryMetadata` is also a plain export; the canvas artwork is `useThemeArtwork()`; the accent colour is also published as CSS vars `--theme-accent-85` / `--theme-accent-60`.
- Per-track side effects that aren't UI (media session, lyrics/features loading, connection toast) live in render-nothing bridges in `components/AppBridges.jsx`, mounted next to `<DJVoiceEngine />`, so the root `App` never re-renders on a skip.
- Keep components "dumb" - they only render based on state

**Visual State Priority Order:**
```javascript
1. Recording (Red) - isMicRecording
2. AI Processing (Blue) - isAIProcessing
3. DJ Speaking (Dynamic Color) - isDJSpeaking
4. Music Playing (Green) - isMusicPlaying
5. Music Paused (Yellow) - isMusicPaused
0. Idle (Grey) - default
```

**Playback State Publishing:**
PlaybackContext publishes playback state to UIState via `reportEngineStatus()`:
- `currentTrack` - Currently playing track object
- `queue` - Full playback queue array
- `currentIndex` - Index of current track in queue
- `isMusicPlaying`, `isMusicPaused` - Playback state
- `isActiveDevice` - Whether THIS device controls playback

UIStateContext uses this to automatically manage artwork preloading (see section 8).

### 2. Backend Playback State Machine

**File:** `server/services/playback_state.py`

This is the **most critical file** on the backend. All playback state lives here.

**Key Properties:**
- `self.radio_mode` - Current mode: "favorites", "discovery", "mood", "genre", etc.
- `self.queue` - Array of tracks to play
- `self.current_track_id` - Currently playing track
- `self.is_playing` - Playback state
- `active_device_id` - When set, frontend controls playback (backend should not auto-advance)

**Important Methods:**
- `seed_radio(category, track_id, user_id)` - Sets mode and fills queue
- `play(track_id, user_id)` - Starts playback
- `_auto_fill_queue()` - Fills queue based on `self.radio_mode`
- `get_state()` - Returns current state (includes `activeSeedMode`)

**Critical Rule for `_playback_loop`:**
When `active_device_id` is set, the frontend controls playback timing. Backend should NOT auto-advance tracks when progress reaches end. See `docs/archive/CROSSFADE_RACE_CONDITION_FIX.md` for details.

### 3. Multi-Device Playback Management - SSOT Pattern

**Backend:** `server/services/device_management_service.py`, `server/services/playback_state.py`
**Frontend:** `client/src/contexts/PlaybackContext.jsx` → `client/src/contexts/UIStateContext.jsx` → Components

The app supports multiple devices per user session with **one active playback device** at a time.

**Device Identity:**
- Every browser instance gets a unique `device_id` stored in localStorage (`client/src/lib/session.js`)
- Format: UUID generated on first visit, persists across sessions
- Sent in all API requests via `X-Device-ID` header
- Used in WebSocket connection to identify which device is sending messages

**SSOT Flow (Backend → PlaybackContext → UIState → Components):**
```
Backend: active_device_id = "device-123"
         ↓ (WebSocket broadcast)
PlaybackContext: receives playback_state
         ↓ weAreActive = (data.active_device_id === deviceId)
         ↓ audio.setActiveDevice(weAreActive)  [Hardware enforcement]
         ↓ reportEngineStatus({ isActiveDevice: weAreActive })
UIStateContext: engineState.isActiveDevice = true/false  [SSOT]
         ↓
Components: Read engineState.isActiveDevice
         ↓
DevicePicker: showInactive = !engineState.isActiveDevice
useDJAudioStream: blocks streams if !engineState.isActiveDevice
Player: shows device status
```

**Three-Layer Device Enforcement:**
1. **Bandwidth Layer (PlaybackContext):** Blocks loading/preloading audio files on inactive devices
2. **Hardware Layer (AudioEngine):** Blocks play/pause/seek, stops audio on deactivation
3. **Visual Layer (UIState → Components):** Inactive devices show UI (remote control) but "Playing on another device" banner

**Critical Pattern - Default to INACTIVE:**
All three layers default `isActiveDevice = false`. Backend explicitly activates via WebSocket. Use `data.active_device_id === deviceId` (exact match) - NOT `!data.active_device_id || ...` which activates all devices when null.

**Device Management Flow:**
1. **Connection:** Device connects → defaults to INACTIVE. A (re)connect never moves playback; the backend only activates a device when the session has no active device yet (`PlaybackState.device_connected`)
2. **Backend Activation:** Backend sets `active_device_id` for one device (first device, explicit transfer, "Play here instead", or claim-on-open below)
3. **WebSocket Broadcast:** All devices receive `playback_state` with `active_device_id`
4. **PlaybackContext:** Calculates `weAreActive`, updates AudioEngine + UIState
5. **Components:** Read `engineState.isActiveDevice` from UIState

**Claim on open (auto-switch to the device the user opens):**
- PlaybackContext sends `playback_command {command: 'claim'}` only for a genuine user open: a page load while the page is visible, the first time a page that loaded hidden becomes visible, or the page becoming visible after being hidden for ≥ 3 s (`OPEN_CLAIM_MIN_HIDDEN_MS`). It waits ~400 ms to settle, needs a server `playback_state` on the current socket, `document.hasFocus()`, and is dropped after 20 s or if the page is hidden again.
- Never claims on WebSocket reconnects/blips, background/hidden tabs reconnecting or loading, quick glances away (< 3 s), or when this device is already active.
- Setting: "Auto-switch playback to the device I open" (`settingsState.autoClaimOnOpen`, per device in `safeStorage` key `autoClaimOnOpen`, default ON) in User → Audio & Devices.
- Backend: `ws.py` `claim` → `PlaybackState.claim_on_open()` reuses `_apply_transfer` (hands over the simulated position, bumps `seek_version`, keeps `is_playing`); no-op for the active device; ignored within `OPEN_CLAIM_DEBOUNCE_S` (2 s) of another transfer to prevent ping-pong. Radio Mode `on_transfer` runs first, so talk breaks and DJ streams follow the transfer like any other transfer.

**Key Properties:**
- `playback_state.active_device_id` (backend) - SSOT for which device is active
- `engineState.isActiveDevice` (UIState) - SSOT for frontend components
- `session.deviceId` (localStorage) - THIS device's unique ID

**DO:**
- ✅ Default `isActiveDevice` to FALSE everywhere
- ✅ Use `data.active_device_id === deviceId` (exact match only)
- ✅ Report device status to UIState via `reportEngineStatus()`
- ✅ Read device status from UIState in ALL components
- ✅ Allow inactive devices to display queue/UI (remote control)

**DO NOT:**
- ❌ Default `isActiveDevice` to TRUE (causes race condition)
- ❌ Use `!data.active_device_id || ...` fallback (activates all devices when null)
- ❌ Check device state locally in components (use UIState)
- ❌ Use legacy `device_inactive`/`device_activated` WebSocket events (removed - use `playback_state` only)

### 4. WebSocket State Synchronization

**Backend:** `server/services/websocket_service.py` + `server/routers/ws.py` - playback state broadcast to all devices in a session
**Frontend:** `client/src/contexts/WebSocketContext.jsx`

WebSocket messages use custom format (NOT socket.io):
```javascript
{ type: "playback_state", data: {...} }
```

Subscribe to events using:
```javascript
useWebSocketSubscribe('playback_state', handlePlaybackState)
```

**DO NOT** use `socket.on()` - use `useWebSocketSubscribe()` hook.

**Connection health and auth:**
- The client pings (`{type:'ping'}`) every 20 s while visible, and at once on resume, `pageshow` and `online`. The server answers `pong` (`ws.py`). Once the server has answered a ping at least once, no reply within 5 s closes the socket and reconnects immediately. Mobile networks and iOS leave "zombie" sockets that look open but are dead.
- Reconnect backoff has jitter. `checkConnection()` is exposed on the context.
- **Login token is sent in the handshake, not the URL.** The client offers the subprotocols `plair.v1` plus `auth.<jwt>`, and the server echoes `plair.v1` (`_subprotocol_auth`). This keeps JWTs out of nginx access logs.
  - `?token=` is still accepted for old clients.
  - If the handshake is refused twice while `/api/health` is OK, the client falls back to the query token for that page session.
  - Deploy client and server together (PLAiR Start does).
- Outbox TTL for `playback_command` is 8 s, so stale taps aren't replayed long after a reconnect.

### 5. DJ Command System

**File:** `server/services_radio/dj_command_executor.py`

Executes the actions behind the DJ tools (`execute_*` methods: searches, playback, seed, playlists, ratings, segments, saves). Search categories map 1:1 to vector search categories; the brace syntax below is how executed actions are displayed and stored in history.

**One DJ turn:** `conversation_service._process_tool_turn` → `dj_prompt_service.gpt_dj_interactive_tools` → `ai_service.run_gemini_tool_turn`.
- **One brain: tools only.** HAL11000 (the second "command extraction" prompt) was retired on 2026-09-29; every action the DJ takes is a tool call. Nothing happens unless a tool is called.
- **Routing lives in the dynamic prompt system.** The Producer AI (`context_router_service.determine_route`) picks the nodes and returns `needs_tools` + a `tool_plan` (numbered function-call steps; free-text args are always normalised to `<placeholders>` and `when`/`near_me` args dropped by `clean_plan`). Cache: exact text, then mpnet similarity (`GEMINI_NODE_PRODUCER_SIMILARITY_THRESHOLD`); a similar hit re-derives the pulse `when`/`near_me` from the listener's own words (`stated_pulse`), since "rain tonight" and "rain tomorrow" embed at 0.96. Cached rows carry a hash of the Producer prompt and are dropped when the prompt changes. The plan is a hint, not a gate: it decides which tools are declared (`dj_tools.declarations_for`: `CORE_TOOLS` pulse_search/pulse_detail/search_and_play/find_tracks/playback_control/rate_track always, plus the planned tools and their companions) and renders as the `tool_guidance` PRODUCER NOTE. `city_pulse` adds what the station knows that fits the message (and records `route.pulse_found`).
- **Tool registry (LifeSpan pattern):** `dj_tools.TOOL_REGISTRY` is the one table: per tool a name, cost (memory / live / segment), requires, a one-line summary, a full description (what it does, when it's the right one, typical pattern) and described parameters. The DJ's declarations (`DJ_FUNCTION_DECLARATIONS`, with Cost/Requires appended), the Producer's catalog (`tool_catalog()`) and the `request_tools` menu are all built from it; per-tool usage never goes in prompt prose.
- **Model:** DJ turns run on their own chain `LLM_DJ` (gemini-2.5-flash, then 3.5-flash-lite) with thinking on for the round that decides the tools (`DJ_TOOL_THINKING_BUDGET=-1`, dynamic) and off once a tool result is back (`DJ_TOOL_FOLLOWUP_THINKING_BUDGET=0`). Measured 2026-09-30: with thinking in the later rounds Gemini re-planned the turn it had just taken and re-emitted its earlier line and tool call (a song request restarted the track four times); thinking for the first round only, plus the `[STUDIO]` marker appended to each tool result ("your line has aired and these calls have run"), took repeats from 7/10 to 0/12, with follow-up calls and double skips still working. After a line plus a scheduled segment the studio message is the hand-off one (`ai_service.HANDED_OFF_NOTE`, `handoff_tools=SEGMENT_TOOLS`): nothing more goes on air, notes and `[TASK]` only (the plain "reply on air now" message made the hosts speak a second hand-off line in 11 of 12 replays; 0 of 12 with it). gemini-3.5-flash-lite refuses a thinking budget of 0 (400 INVALID_ARGUMENT), so `llm_router.fit_thinking` raises it to that model's floor (`GEMINI_THINKING_BUDGET_FLOOR`, 1 = no thinking tokens); without it the DJ's fallback model failed every follow-up round. The loop also drops a call identical to one already made in the turn (`playback_control` excepted unless the echoed line comes with it) as a logged safety net ("the model echoed its earlier turn"). Measured 2026-09-29: without thinking the model narrated tool calls instead of making them ("<<CALL:...>>", "I will call play_shoutouts"); 3.5-flash(-lite) with thinking returned MALFORMED_RESPONSE ~1 in 4 turns; 2.5-flash with thinking was 0/12 failures at the same speed and price. The Producer stays on `LLM_LIVE` (flash-lite, falls back to 3.5-flash on an empty reply).
- **Prompt layout and caching:** every context node carries a role (`node_registry.register(..., role="system"|"live")`, default live). The DJ's system prompt is only the `system` nodes of its config, in config order, plus all 23 tool declarations, so it is identical for every listener and turn; every `live` node (track, queue, pulse, weather, profile, conversation, tool hint, `format_performance_tag_examples` with fresh tag picks) goes in the user message before `[LISTENER TXT]`. That fixed prefix is held in an explicit Gemini cache (`services/gemini_cache.py` `system_caches`, one per agent label + model + prompt/tools hash, `GEMINI_CACHE_TTL_S` 30 min renewed while in use, `GEMINI_CACHE_RETRY_S` back-off; `gemini_generate_chain(cache_label=...)`, inline fallback if Gemini drops it). Requests that set `tool_config` can't use a cache and run inline. Measured 2026-09-29: DJ calls ~80% cached, $0.0015/call (was 16% / $0.0025). Other agents (announcer, For You, segments) can pass their own `cache_label`.
- **Tool turn:** 22 tools in `server/services_radio/dj_tools.py`. Read tools return data to the model: `pulse_search`, `pulse_detail`, `listener_context`, `city_trends`, `what_aired` (City Pulse, `docs/CITY_PULSE.md` sections 15-16; store first, live fetch only on a miss, at most `DJ_TOOL_MAX_LIVE_FETCHES` per turn, results saved for everyone). Action tools (search_and_play, playback_control, seed_radio, play_playlist, rate_track, move_playback, radio_settings, save_*) dispatch to the structured `execute_*` methods on `dj_command_executor`; segment tools (get_news, get_weather, get_events, find_places, get_artist_biography, explain_lyrics, play_shoutouts) schedule the produced interpretation segment, released after the reply. Empty replies get a finish-reason-aware corrective message, at most `LLM_RECOVERY_MAX_STRIKES` (2). The loop runs on the `LLM_LIVE` chain with the circuit breaker (`llm_router.gemini_generate_chain`). Talking while tools run: a line the model writes alongside a call airs at once; a round with no line only tells the interlude monitor what the hosts are doing (`dj_tools.tool_activity`, see "Impulse and interlude"). After a line that already covered a plain action, an empty final reply is accepted.
- **Transparency (front end):** every DJ turn streams ordered `dj_activity` events over the WebSocket: `turn` (the listener's words), the Producer's plan (`source: producer`), `say` for each on-air line as it airs, each tool call (`start` with label + brace-style `command`, then `result` with outcome found/done/empty/failed/blocked and a short summary such as "3 gigs · 1 place"), then `done`. `Conversation.jsx` renders them in order between the DJ bubbles (`ActivityCard`: expanded while running, tap to show the command) and skips the final `conversation_update` for a streamed `turn_id`; `DJActivity.jsx` also pops compact chips at the top of the screen that fade. History rows keep actions under `[STUDIO TOOLS]` (older rows may say `[HAL11000]`).
- **Talk or Type:** `interfaceState.radioInput` ('voice' | 'text', per device in `safeStorage` `radioInputMode`). Tapping the active Radio tab toggles it like Catalog/Shoutouts (both tabs always show the current mode); in text mode the tab shows `TextRadioIcon` labelled "Text" (desktop: the same icon as a toggle in the Radio header). Text mode hides the radio bubble (opacity 0, shader included) and shows `DJTextComposer` above the tab bar, which posts `api.djTalk({text})` (the server's `handle_text_interaction`).
- **Length is the hosts' call (`services_radio/talk_clock.py`):** no per-prompt length numbers. Every interactive turn carries a `studio_clock` live node (what's on air, time left, when vocals come in, next track's intro, any talk break lined up) followed by how to size the reply to the listener's words. Every segment tool takes `depth` (brief / standard / detailed, injected for all `SEGMENT_TOOLS`); it rides a context variable into the scheduled segment, where the `segment_length` node renders `SEGMENT_DEPTHS` seconds as a word ceiling. One speaking pace for the whole station: `TALK_WORDS_PER_SECOND` (measured 2.0 on air), read only through `talk_clock.pace()` / `talk_clock.words_for(seconds)` by these segments, Radio Mode talk breaks and the announcer's time constraint; never hard-code a words-per-second figure, and never give the hosts a time without its word count: every prompt that names seconds (announcer window, Radio Mode and DJ segments, a song's intro in the studio clock and in play-tool results) pairs it with `words_for(seconds, kind)`. The pace measures itself (`talk_clock.meter`): each finished chat, announcer or segment stream adds one sample (script words / aired seconds) in `TTSQueueManager`, smoothed per kind (`chat`, `announcer`, `segment`), saved in `talk_pace`; the setting is the starting value and fallback. Tools that start a song (`PLAY_TOOLS`) return `vocals_start_s` from the lyric timing. The between-track announcer is sized by its music window alone (its TIME CONSTRAINT); there is no fixed word cap anywhere. `get_weather` takes a list of periods.
- **Parity with the app (`docs/HANDOVER_DJ_TOOL_PARITY.md`):** a tool calls the same service the app's route calls, never its own copy. `playback_control` also restarts, seeks and removes an upcoming track; `seed_radio` takes a `target` track; `rate_track` rates tracks and other listeners' posts (target `shoutout`, default the one that just aired) through `preferences_service`; `pulse_search` takes `mine` (the listener's own posts) and `about_track` (reviews of one track), and `pulse_detail` lists a shoutout's replies; `move_playback` moves the music to another online device by name; `radio_settings` reads or patches Radio Mode and music source (`preferences_service.apply_radio_settings`, the same call as `PUT /api/radio-mode`; guests get a `radio_mode_updated` patch their client stores). Guests are refused favorites / discovery as in the app. Deleting posts, clearing the conversation, sound and quality settings and song generation stay in the app.
- **Listener timeline (`docs/LISTENER_TIMELINE.md`):** what aired for a listener is read on demand, never put in the prompt. `services/listener_timeline.timeline()` reads `play_events` (tracks and the shoutouts, replies and reviews that aired, for users and guests) plus the unwritten analytics buffer; the hosts reach it with `what_aired` when the listener points back at something, then pass the id on (`track_id` on `rate_track`, `seed_radio`, `save_review`, `explain_lyrics`; `shoutout_id` / `parent_id` for posts). With no id and more than one candidate post a tool says so; it never guesses. Everything the hosts voice (segments, chat replies, between-track talk, Radio Mode breaks when they go on air) is written as plain text to `aired_talk` by `listener_timeline.record_talk` and shows as `segment` / `talk` entries; `what_aired(id=...)` returns the full text of one. Time is always a window the hosts choose: `around_minutes_ago` (one rough moment, the studio looks either side) or `from_minutes_ago` / `to_minutes_ago`; lists are capped at 25 short entries, never full transcripts. Add new voiced stream types to `SEGMENT_STREAMS` / `TALK_STREAMS` there or they are not recorded. The listener sees the same read in the app: the Timeline toggle in the Radio header (`ListenerTimeline.jsx`, `GET /api/timeline`, `GET /api/timeline/entry?id=`) swaps the conversation for the last 24 hours of what aired.
- **Self-directed, cost-aware:** every tool description ends with its cost (`dj_tools.TOOL_COSTS`: memory / may go online / full segment) and every result says where it came from (`came_from`); a result that comes up short lists `could_try_next` tools (auto-granted for the next step). The hosts judge quick answer vs full segment themselves (no keyword rules), review each result against the ask, and sign off with a `[TASK]` section after `[INTERNAL DIALOGUE]` (a valid tag in `clean_gpt_output`, stripped for other roles, never spoken, shown as a Review card, logged on the `DJ turn |` line). `[TASK]` is the hosts' own review: did they do what they told the listener they'd do. Partial means they carry on: one more step (`DJPromptService._review_step`, `run_gemini_tool_turn(review=...)`); a missing sign-off gets the same review. Context stays lean LifeSpan-style: every tool takes an optional `_done_with` {tool: what I took from it}, and `run_gemini_tool_turn` replaces those earlier results with that one-liner before the next round.
- **The DJs decide their own tool calls.** There are no word-list gates on what the listener said (they were removed on 2026-09-30: a plural "songs" once blocked a review the DJs chose to save). `dj_tools.authorize_tool_call` only enforces plain limits: actions only in reply to the listener's own message, a per-turn call cap, saves and ratings need a signed-in account, one save per turn, and each segment once per turn. Whether something is worth keeping is for the DJs and the review/shoutout pipeline to judge, never keywords.
- **Track search = the app's search, for the DJs:** `find_tracks` (read) returns candidates (title, artist, genre, who sings, a line on the sound, track id) without playing; `starts_with` filters the whole catalog by how the artist name (or, with `song_title`, the title) starts. Its description tells the hosts to work out likely real names from their own knowledge and look those up next to the clues, then play their pick with `search_and_play(track_id=...)`, never asking the listener to confirm. Tested 1 Oct with `tests/dj_find_test.py` (whole turns, real search, playback faked) and `tests/track_search_probe.py` (search ranking only). `search_and_play` has the same two modes as the front-end search box. Default (no `category`): the AI search, `vector_search_service.search(use_ai_analysis=True)`, which works out the category weights from the listener's words (shown as `{description}`). With a `category`: the old-school single-field search; `primary_artist` / `song_title` also report honestly when the catalog doesn't have the name. Both take `within`: `catalog` (default), `favourites` (likes + super-likes) or `super_likes` (`listener_filters.scope_ids` -> `only_ids`; signed-in only). `pulse_search` takes `within` too and uses the AI weighting for track-only lookups. Don't build a separate search for the DJs.
- **Queue:** `add_to_queue` inserts a batch in order after the current track, so "play" starts the first match and the rest follow. A station switch (seed or playlist) keeps upcoming picks (tracks not in `_auto_filled_track_ids`): seed keeps the current track then the picks, a playlist starts its first track now then the picks, and the new station fills behind them. Artist/song honesty checks and track labels read every artist field (`log_service.track_artists`: generation_params.artist_name, track_info.artist, derived_tags.inspired_artist).
- **Testing honestly:** boot with `PRODUCER_CACHE_ENABLED=false` (no route cache reads or writes) and use a fresh conversation: `tests/dj_pulse_test.py` uses a new guest each run, and `--signed-in` clears the account's DJ conversation first (test account in git-ignored `tests/.dj_test_account.json`, or `DJ_TEST_USERNAME`/`DJ_TEST_PASSWORD` from the environment). The script records the real queue after each turn (`now_playing`, `up_next`). Production keeps the cache on.
- **Logs:** one `DJ turn | Plan: ... | tools: <command> -> <summary>; ... | N round(s)` line per turn in the `commands` category.
- **Notes marker:** `clean_gpt_output` normalises any `[INTERNAL …]` tag or `INTERNAL …:` line to `[INTERNAL DIALOGUE]` before the split, so planning notes are never spoken.
- Keep the model's full `Content` objects in history (Gemini 3.x `thought_signature` parts must round-trip). Safety guards (server-side, independent of the model): tools act only on the current session; guests can't rate; conversation history, shoutouts, pulse items and web data are wrapped as untrusted data. `gpt_*` names are legacy; the provider is Gemini. Watch logs for `[DJ TOOLS] Blocked`, `DJ tool call`, `[PULSE]` and `covered on hand`. Live test: `tests/dj_pulse_test.py --base http://127.0.0.1:8011` (guest in Auckland); knowledge probe without an LLM: `tests/pulse_probe.py`.

**SEARCH Commands (Full Catalog):**
`{song_title}`, `{primary_artist}`, `{similar_artists}`, `{primary_genre}`, `{secondary_genres}`, `{mood}`, `{style}`, `{theme}`, `{vocal}`, `{lyrics}`

**SEED Commands (Current Track):**
`{seed}` with modes: `primary_genre`, `secondary_genres`, `mood`, `primary_artist`, `similar_artists`, `style`, `theme`, `lyrics`, `vocal`

**Playlist Commands:**
`{playlist}` with: `favorites`, `discovery`, `top_hits_all`, `top_hits_week`, `top_hits_day`

**Playback Controls:**
`{next}`, `{previous}`, `{activate}`, `{pause}`

After executing commands, **ALWAYS** broadcast state via `broadcast_playback_state_callback`.

### 6. Audio Engine - Dual Buffer Crossfading

**File:** `client/src/lib/audioEngine.js`

Uses A/B slot architecture for seamless crossfading:
- Slot A and Slot B alternate as current/next
- `timeUpdateElement` tracks which element has the progress listener
- During crossfade, listener must be removed from old element and added to new one
- Track state (`currentTrackId`, `nextTrackId`) must update during crossfade

**Crossfade plan (per pair of songs):** the transition analysis (`announcer_service._analyze_transition`, run for every listener on each new current/next pair, not only when the announcer talks) calls `services_radio/crossfade_plan.plan` with both songs' audio features and lyric timing, and the result reaches the client as `crossfade_hint` (a new plan is pushed to the session at once, `_publish_crossfade`; before 1 Oct it only rode along with the next state change, so most fades used the generic one). The overlap follows the outgoing song's own fade-out (`tail`) and the incoming intro's build (`rise`), within `CROSSFADE_MIN_MS`/`CROSSFADE_MAX_MS` (1.5-5 s; no beat-matching, so longer would clash); it shrinks (to `CROSSFADE_FLOOR_MS` at least) so it never runs over the outgoing song's full-level vocals or past the incoming vocals. Leading and trailing silence are skipped. A song with a hard ending plays on and fades only at its end; a building intro needs only a short fade-in. The engine runs the fade-out and fade-in as separate equal-power ramps with their own delays (`_rampGain(..., { delaySec, equalPower })`); without a hint it falls back to a 3 s equal-power fade from the end. Pausing mid-fade takes the outgoing song out too. While a fade of 1 s or more runs, `engineState.crossfadeMs` drives the ON AIR edge glow in the shader, tinted with the incoming song's accent colour (no new UI). Probe: `tests/crossfade_probe.py --pairs N` (plans for random catalog pairs, no server or LLM).

**DO NOT:**
- Add multiple timeupdate listeners
- Forget to swap `timeUpdateElement` during crossfade
- Let backend auto-advance when frontend controls playback

### 7. Viewport Responsiveness - Publisher/Subscriber Pattern

**File:** `client/src/contexts/ViewportContext.jsx`

ViewportContext is a **full Publisher/Subscriber pattern** - ALL components subscribe directly, NEVER pass viewport through props.

**Performance Optimization:** ViewportContext only updates state when the **breakpoint changes**, not on every pixel resize. This prevents unnecessary re-renders and maintains 60fps.

```javascript
// ✅ CORRECT - Direct subscription
const { breakpoint, isWidescreen, scale, isMobile, isTablet, isDesktop } = useViewport()

// ❌ WRONG - Prop drilling
function Parent() {
  const { isMobile } = useViewport()
  return <Child isMobile={isMobile} />  // NO!
}
```

**All Available Breakpoint Flags:**
- Exact: `isXS`, `isSM`, `isMD`, `isLG`, `isXL`, `is2XL`, `is3XL`
- Min-width: `minSM`, `minMD`, `minLG`, `minXL`, `min2XL`, `min3XL`
- Max-width: `maxSM`, `maxMD`, `maxLG`, `maxXL`, `max2XL`
- Convenience: `isMobile` (xs||sm), `isTablet` (md), `isDesktop` (lg+), `isWidescreen` (xl+), `is4K` (3xl)
- Other: `scale`, `isPortrait`, `isLandscape`, `isRetina`, `width`, `height`, `dpr`

**Breakpoint Ranges & Scaling:**
xs<640px (phone, 1.0), sm 640-768px (1.0), md 768-1024px (tablet, 1.0), lg 1024-1440px (laptop, 0.8), xl 1440-1920px (desktop, 0.75), 2xl 1920-2560px (1080p, 0.7), 3xl 2560-3840px (QHD, 0.85), 4K 3840px+ (baseline, 1.0). Scaling applies to desktop (≥1024px) only. The root font-size is that scale × `UI_TEXT_SCALE` (ViewportContext, 0.94): the one knob for overall text/UI size on every device, since Tailwind sizes are rem-based.


### 8. Artwork Preloading Architecture - Proactive SSOT Pattern

**Files:** `client/src/contexts/UIStateContext.jsx`, `client/src/lib/mediaCache.js`

UIStateContext automatically manages artwork preloading for smooth crossfades. This is a **proactive** system - artwork is loaded BEFORE track changes.

**How It Works:**
1. PlaybackContext publishes `currentTrack` + `queue` + `currentIndex` to UIState via `reportEngineStatus()`
2. UIStateContext watches for changes and automatically preloads:
   - **Current track:** Regular artwork + enriched artwork (for parallax effects)
   - **Next track:** Regular artwork + enriched artwork (for instant crossfades)
3. Components consume preloaded URLs via hooks - NO manual preloading needed

**Component Usage:**
```javascript
// Regular artwork (for thumbnails, small images)
const artworkUrl = useArtwork(trackId, hasArtwork)

// Enriched artwork (for parallax effects - color + depth side-by-side)
const enrichedUrl = useEnrichedArtwork(trackId, hasArtwork)
```

**A/B Crossfade Pattern:**
Both `Player.jsx` and `NowPlaying.jsx` use A/B layer crossfading:
- Two layers (A and B) render the same component with different artwork
- `frontLayer` state controls which layer is visible (opacity transition)
- When track changes, new artwork loads into the back layer, then crossfades to front
- Because UIState preloads next track, crossfade is instant (no placeholder flash)

**DO NOT:**
- Manually call `preloadArtwork()` or `preloadEnrichedArtwork()` in components
- Pass artwork URLs through component props
- Create separate preloading logic in components

**DO:**
- Use `useArtwork()` and `useEnrichedArtwork()` hooks to consume URLs
- Trust that UIState has already preloaded current + next track
- Use A/B layer pattern for smooth crossfades (see Player.jsx:88-134, NowPlaying.jsx:185-354)

### 9. Modal System - Dynamic Backgrounds

**File:** `client/src/components/modals/Modal.jsx`

All modals use a standardized base component with dynamic visual effects.

**Modal State Pattern (IMPORTANT):**
Modal open/close state lives in **UIStateContext**, NOT in local component state:
```javascript
// UIStateContext provides:
uploadModalOpen, openUploadModal, closeUploadModal  // Upload modal
shoutoutModalState, openShoutoutModal, closeShoutoutModal  // Shoutout modal

// Components just call the open function:
const openUploadModal = useUISelector(state => state.openUploadModal)
<button onClick={openUploadModal}>Upload</button>  // No local state!

// App.jsx renders ALL modals at root level:
<UploadMusicModal isOpen={uploadModalOpen} onClose={closeUploadModal} />
<ShoutoutModal isOpen={shoutoutModalState.isOpen} ... />
```
**Why:** Modals MUST render at App.jsx level for proper mobile positioning. CSS `position: fixed` inside transformed containers positions relative to the transform, not viewport. Components just trigger open/close via UIState functions.

**Visual Effects:** Blurred artwork background, category-based gradient, animated border glow, smooth animations, noise texture.

**Props:** `maxWidth`, `showCloseButton`, `closeOnBackdrop`, `categoryOverride`, `gradientOpacity`
**Helpers:** `ModalSection`, `ModalButton`, `ModalOptionButton`, `ModalFooter`, `ModalCard`

**DO NOT:**
- Store modal state in local component state (use UIStateContext)
- Render modals inside components (render at App.jsx level)
- Create modal wrappers manually (use Modal.jsx)
- Duplicate backdrop/animation logic

**DO:**
- Add modal state to UIStateContext following existing patterns
- Use Modal.jsx for all modals
- Let Modal handle artwork + gradient automatically
- Use ModalSection, ModalOptionButton, ModalFooter for consistency

### 10. Interaction Effects System - Visual Reactivity

**Files:** `client/src/contexts/DynamicThemeContext.jsx`, `client/src/components/AudioReactiveCanvas.jsx`

User interactions trigger visual effects by adding to existing music-reactive shader effects (glitch, chromatic, rotation, brightness).

**API:**
```javascript
const { triggerEffect } = useDynamicTheme()
triggerEffect('click', { x: 0.5, y: 0.5, intensity: 1.0 })
```

**Effect Intensities:** Queue reselection: 1.5, Track clicks: 1.0, Engagement buttons: 0.5

**Integration Points:** `Queue.jsx`, `Catalog.jsx`, `MediaActions.jsx`

**DO:** Use strategic intensities (0.5 subtle, 1.0 normal, 1.5 special), let effects decay naturally
**DO NOT:** Add to every button, use intensity > 2.0

### 11. Shader Effects Architecture - DRY Pattern

**Files:** `AudioReactiveCanvas.jsx` (SSOT), `offlineVideoRenderer.js` (consumer)

Both live canvas and offline renderer share shader code from AudioReactiveCanvas. Exported functions: `createInitialEffects()`, `buildVisualCueMap()`, `calculateFrameEffects()`, `processLyricTimestamps()`, `getLyricAt()`.

**DO:** Modify effect logic in AudioReactiveCanvas only. Use seeded random for offline, `Math.random` for live.
**DO NOT:** Duplicate shaders/effects in offlineVideoRenderer.

### 12. Local TTS Engine (Chatterbox-Turbo)

ElevenLabs was retired in 2026-09 (cost). DJ speech is generated by Chatterbox-Turbo (Resemble AI, MIT) in `tts_chatterbox/`, with our own batched CUDA-graph decoder and batched vocoder for the P6000 (`tts_chatterbox/README.md`). It replaced Orpheus-3B on 2026-09-30 after a bake-off (`docs/TTS_ENGINE_RESEARCH.md`): better realism, first audio ~0.4 s, ~6x the capacity. Orpheus (`tts_server/`) stays in the repo but is not loaded.

- **Voices:** `[JESS]` (she/her) is Chatterbox's built-in voice; `[LEO]` (he/him) and the station voice are cloned from 10 s reference clips in `tts_chatterbox/voices/leo.wav` and `station.wav` (voices designed with Qwen3-TTS VoiceDesign, not real people). `settings.VOICE_PREFERENCES` maps host -> engine voice + temperature. Changing a host's voice means clearing that host's cached clips (`data/tts_dj_engine_data/*/<host>/`), otherwise old and new voices mix.
- **Paralanguage (never call it "meta"):** paralanguage segments are standalone clips in their own vector cache (`paralanguage_audio`, table `paralanguage_embeddings`), never mixed into spoken sentences. `generate_paralanguage_gpt_response` turns each stage direction (`~laughs heartily~`) into a phonetic/onomatopoeic spelling (`bwa-ha-ha-ha-haaaa`, `aarrrgg....`, `mm-hmm`) with the owner's original ElevenLabs-era prompt, a fresh LLM script for every new clip (no script cache) so reactions keep their variety. With `PARALANGUAGE_ENGINE_TAGS` on (default, owner's call 2026-10-01) the same prompt also gets Chatterbox's native sound tags (`ENGINE_SOUND_TAGS`: `[laugh]`, `[chuckle]`, `[sigh]`, ...) and decides per sound: tag alone, tag + phonetic, or phonetic alone. Flipping the setting purges the paralanguage cache at the next boot (`purge_paralanguage_on_mode_change`, marker `.paralanguage_mode`) so the two kinds of take never mix. The engine-tag paragraph also tells the writer it only ever voices the host: an object noise (`~taps desk~`) is played by the studio, so the writer gives the host's own little sound (`hnn-hm`), never an imitation of the object. Any bracket that isn't an engine tag is snapped to the closest one by its words (`[h chuckle]` -> `[chuckle]`) or dropped before rendering (`keep_engine_tags`, logged), because Chatterbox reads unknown brackets aloud.
- **Two tracks, one per host (owner's model):** each host's sentences, paralanguage and breaths stack on her own track; only the two hosts cross over. In `IncrementalBlend` every element starts at the conversation end minus its `@N@` overlap, never before its host's own track end (a host never overlaps herself; owner rule, don't remove it). The planner keeps `@N@` whenever the window reaches the other host and drops it when it only reaches her own track. A breath is glued to the sentence it precedes. `%sfx%` is a bed layer: it overlays where it falls and never takes up time or pushes a host later. Mic proximity (`&N&`) carries per host until the next tag (an untagged line used to fall back to 0, which also switched Perlin variation off).
- **Paralanguage emojis (from LifeSpan):** every paralanguage title in the voice cache gets an emoji (`paralanguage_embeddings.emoji`, one small LLM call per title, `DJPromptSystemService.generate_paralanguage_emoji`; the phonetic prompt is untouched). `services_radio/paralanguage_emoji.py` renders `~tag~` as the emoji of the closest cached title in outgoing chat text only: `websocket_service.broadcast_to_session` runs it on `conversation_update` / `dj_activity`, and `GET /api/conversation` on read. Stored conversation text keeps the raw tags. Titles without an emoji get one in the background the first time they're needed.
- **Movement (mic proximity) is a real-time room simulation (owner's design; ask before changing any part of it):** `&N&` is a distance from the mic (`AUDIO_EFFECT_CONFIG['proximity']`: 0 = 5 cm on the mic, 0.2 = 12 cm normal talking distance, 0.5 = arm's length, 1 = 4 m back of the studio). The `&N&` values are points on each host's path through time: every take eases from the midpoint with her previous own element to the midpoint with her next one (`movement_curve`; joins skip the co-host's lines, so the path never jumps). Perlin wobble sits on top: one continuous noise per host per reply (`MotionTrack` / `MotionSlot` in `tts_processing_service`, each take continues where her last one ended), and her pan is a slow continuous drift around her seat (Jess left, Leo right). Every 200 ms chunk (100 ms crossfade) applies that moment's value in one pass: dry level by inverse distance (capped +4 dB on the mic), room reverb held constant, proximity bass under 30 cm, high roll-off with distance (`mic_distance_response`). Never take an effect out of the chunks or make it static. Room ambiance (`%x%` with `&N&` > 0: pen, desk, chair) is a separate object per sound: its own random spot (±0.4 pan), its distance, a little drift and wobble, no curve joins. True SFX (`&0&`, soundboard / direct input) are dry and centred. The prompt asks for `&N&` on every element; an untagged element keeps its host's last value. Hosts are matched in level by a fixed per-host gain (`VOICE_PREFERENCES[...]['gain_db']`, Leo +2.2 dB measured 2026-10-01), never per-take leveling.
- **Channels: only `[BROADCAST]` is spoken.** `[TXT]` is the hosts' private text message to the listener and stays in the chat (`tts_stream_planner.spoken_text`, used by every voice path). Untagged text is spoken. The old player worked this way; the 2026-09-28 revival started voicing `[TXT]` and it was restored on 2026-09-30.
- **Paralanguage tags use tildes (`~laughs~`), since 2026-09-30 (LifeSpan's lesson):** a tilde never appears in normal DJ talk, so emphasis or a song title can't turn into a tag. `clean_gpt_output` strips every `*` and escaped quote, keeps well-formed tags (`~x~ %x% @N@ &N&`) and only removes broken fragments between them (`handle_mismatched_tags`), so a stray symbol can't eat a speaker tag. The planner never sends a sentence without words. Example tags for the DJ are random clean titles from the paralanguage library, topped up from `STARTER_PARALANGUAGE_TAGS` (20) until the library has 20 clean titles; a tag with digits or markup is never rendered or cached.
- **Speed:** up to 8 sentences decode together (`TTS_ENGINE_SLOTS`), any mix of voices; the backend's priority lanes decide what goes first. Measured on the P6000: one sentence 0.41 s to first audio, 8 at once ~1 s and ~6.4x real time in total. The token model is not the limit (727 tok/s at 8 slots, 25 is real time); S3Gen, which re-reads each voice's 10 s reference on every call, is.
- **Sacred design:** the performance planner (two hosts talking over each other via `@N@` overlaps/blends, `&N&` mix + 3D reverb, `~paralanguage~`, `%sfx%` beds, breaths) must not change. Only pure delivery/latency/efficiency changes are acceptable — the listener must hear the same audio in the same order. Streaming the LLM reply and re-planning it was tried and rejected.
- **Delivery (progressive, identical audio):** `tts_queue_manager` starts rendering every segment at once, assembles the timeline in playback order (`IncrementalBlend` emits a blend only once later segments can't change it) and feeds `TimelineMixer` (`tts_broadcast_service.py`) → one persistent ffmpeg WebM/Opus encoder per stream (`tts_live_stream.LiveStreamEncoder`) → `tts_stream_audio_chunk` events. Engine requests go through a priority scheduler in playback order (`tts_generation_service`), one generation until first audio then two (`TTS_TURN_GENERATION_PARALLEL_START` / `_PARALLEL`). Fresh PCM goes straight into processing; the FLAC cache file is written in the background.
- **Impulse and interlude are cached mini-scripts (owner's design 2026-10-01, from LifeSpan; `services_radio/filler_scripts.py`):** little bits of radio theatre in the normal DJ format (both hosts, `~paralanguage~`, `%ambiance%`, `@N@`, `&N&`), stored in the `filler_scripts` table (embeddings DB) under the conversation they fit (`conversation_context`: NOW listener first, then the last two exchanges). One impulse per listener message, the moment it lands (voice: fast transcript; typed: on send). An interlude only when the station has been quiet for `INTERLUDE_SILENCE_S` while the DJs work on the reply (tool activity included), up to `INTERLUDE_MAX_PER_TURN`, as escalating sequences of `INTERLUDE_SEQUENCE` beats that a turn plays in order. Rules: always the closest stored script, never a wait (an empty library plays nothing); the anti-repeat is the one universal shotgun (`VectorDBService.note_used` / `is_fresh`, `VECTOR_DB_SHOTGUN_COOLDOWN`, per listener): recently heard scripts give way to the next best, and if every fitting script was heard recently the best one still plays, exactly like the lines; a turn's interlude follows its sequence in order; a best match under `FILLER_LEARN_BELOW` makes DeepSeek write a better one in the background (writers `write_impulse_script` / `write_interlude_scripts` get the show's own nodes: identity, roles, tone, performance-tag guide and examples, dialogue examples). They play through the normal engine in cache-only mode (`tts_type` impulse / interlude): every line takes its closest cached take and its exact line is queued on the low-priority lane for next time (`schedule_refresh`); nothing renders live. What aired is told to the DJ (`[ON AIR JUST NOW]`), shown in the chat and saved in the conversation. The library takes a few days of listening to fill up; that is expected. The old `[IMPULSE]` clip type, `impulse_audio` and the tool fillers are gone.
- **Paralanguage is emergent:** each uses a cached clip at/above `PARALANGUAGE_SIMILARITY_THRESHOLD` (and the per-listener anti-repeat cooldown), otherwise it renders live and joins the cache. No hourly caps, library ceilings, script caches or background refreshes: the threshold and the cooldown are the only limits, so the library grows until it stops missing.
- **Breaths are an offline library (2026-10-02):** Chatterbox can't render a breath on its own (scripted breaths came out as hums), but it breathes naturally between two sentences of one take. `server/utils/build_breath_library.py` clusters real DJ lines by meaning (~100 clusters), renders each centre line plus a follower as one take, finds the gap with Whisper word timings, drops voiced edges and silent gaps, and writes the breath as FLAC tagged with the sentence it followed. The library is copied into `breath_audio/<host>/` with `breath_embeddings` empty so it indexes at boot. Breaths go between a host's consecutive sentences, also when the co-host cuts in between; each is matched on the sentence she just said (`segment['context']`), always the closest take (`BREATH_SIMILARITY_THRESHOLD` -1, cooldown applies), never rendered live (`GENERATION_PERMISSIONS['breath']` empty). Grow it by re-running the builder. Breaths are meant to be subtle: never turn them up.
- **Anti-repeat (shotgun) cooldown is per listener:** `VECTOR_DB_SHOTGUN_COOLDOWN` is keyed by (listener session, clip filename), passed through `lookup_clip(..., listener=owner)`. One listener hearing a clip never blocks it for anyone else.
- **Interrupt:** a new listener text/voice turn calls `TTSQueueManager.cancel_session` — drops queued TTS, cancels the in-flight render, aborts that session's engine jobs via `POST /abort/<job_id>`, and emits `tts_stream_end` + `tts_stream_cancel` (the client clears its queue).
- **Cache:** new clips are cached per host in `data/tts_dj_engine_data/{tts,paralanguage,breath}_audio/<host>/` as lossless FLAC (16-bit 24 kHz, the engine's own PCM; Vorbis `title` = the line, or for breaths the sentence before it, and `description` = the script that was voiced), never MP3: they are kept for offline upscaling later; missing files self-heal (stale rows deleted). Thresholds (measured 2026-10-01 on the live cache): TTS 0.975 (0.95 reused "it's 6 pm" for "11 pm"), paralanguage 0.85 (0.75 matched "gasps softly" to "groans softly"), SFX 0.75 (0.5 played a horn for "cowbell"). A line below the threshold renders live; there is no background exact-line re-render any more (removed 2026-10-01). The near-best random pick only chooses among takes at/above the threshold. SFX are leveled before the Perlin chain (`level_sound_effect`, `TTS_SFX_TARGET_DBFS` -46 / `TTS_SFX_MAX_PEAK_DBFS` -24) so `&N&` distance still changes their level. Perlin noise starts at a random offset on every render (it used to be identical for every clip). All cache embeddings (lookups, live saves, startup migration) use the shared mpnet encoder (`SEMANTIC_ENCODER`, 768-dim, ~15 ms per line on the P6000); rows made with another encoder are cleared at startup and re-embedded from the clips' `title` tags (`drop_stale_encoder_rows`, `tts_database_migration_service.read_clip_tags`). flan-T5 was retired on 2026-09-29: its last-position (padding) state scored opposite lines ~1.0 and identical ones ~0. Cached clips decode in-process via `tts_processing_service.decode_mp3` (soundfile, FLAC or MP3). Sound effects (`audio_effect_audio/`) are cache-only bought MP3s with ID3 tags and stay MP3.
- **Stings & talking clock (no LLM):** a third voice, `station` (`tts_chatterbox/voices/station.wav`), with a "station computer" treatment (`tts_processing_service.station_treatment`: radio-band EQ, light ring-mod/comb/bitcrush, slap + short room, then the normal chain via `process_station`). Its clips live in their own exact-key cache (`station_voice.py`, `data/tts_dj_engine_data/station_audio/<voice>/index.json`, FLAC), never the vector cache. The talking clock (`talking_clock.py`: intro + "forty-six minutes past" / "quarter past" / "half past" + hour, or hour + "o'clock", + optional daypart; 85 parts. Minutes carry "minutes past", which Orpheus needed to avoid looping on a bare number) only ever uses exact parts; a missing part means no time check (never a near match), and hours/minutes are checked with the fast Whisper model after rendering. Station IDs (`station_ids.py`, ~36 lines incl. "{city}" lines) play the best cached take and queue the exact line (LifeSpan pattern). Musical stings are cut from 2 Suno idents into `STINGS_DIR` (`manifest.json`, never the catalog); sweeper beds can also come from `audio_effect_audio`. Types are a registry (`sting_types.py`), rules are pure (`sting_schedule.py`), `sting_service.py` pre-renders at low priority, and the announcer asks it first: short windows (`STINGS_SHORT_WINDOW_S`) or every Nth break get a sting instead of an LLM line; they stream as `tts_type="sting"` through `TTSQueueManager.add_clip_request` → `LiveStreamEncoder` (same client path and ducking). Radio Mode talk breaks can open with an ID (`lead_in`). Settings: `STINGS_*` / `STATION_*`. Station name is spelled "Play Air" for Orpheus (`STATION_NAME_SPOKEN`; "PLAiR" is read inconsistently).
- **Re-render, don't band-aid:** rendering is local and free (we are not paying ElevenLabs). When an engine, prompt or wording change is an overall improvement, purge the affected regenerable caches (`tts_audio`, `paralanguage_audio`, `breath_audio`, `station_audio` + their embedding rows/`.ann`) and let everything re-render fresh. Don't preserve old takes with targeted scans, quarantines or extra validation layers. Never purge `audio_effect_audio` (sound effects are cache-only and cannot be regenerated).
- **Stopping:** a take ends on Chatterbox's stop token; runaway takes are cut by a length cap from the text, `max(5 s, 3 s + 1 s/word)` (`tts_chatterbox/engine.py`), logged as `end=max_tokens`.
- **Build gotchas:** PyTorch must stay on a CUDA 12.x wheel (cu124); cu128+ and CUDA 13 dropped Pascal (sm_61). No fp16/bf16 on this card: the engine runs fp32 with CUDA graphs. See `tts_chatterbox/README.md`.

### 13. Backend Structure & Security

- **Routers:** `server/app.py` only holds the lifespan (service startup/shutdown), middleware and router includes. Routes live in `server/routers/*.py` by domain; shared dependencies (`get_session_info`, `get_current_user`, `RateLimit(...)`, `require_admin`, `read_upload_limited`) are in `server/routers/deps.py`, request models in `server/routers/schemas.py`. Services are created in the lifespan and published on `server/service_registry.py` (`services.x`) — routers read `services.x` at request time; never import a service instance at module import time.
- **Request guard:** `server/security_middleware.RequestGuardMiddleware` (HTTP + WebSocket) rejects paths containing `\`, `..`, NUL or `:` and any guest id not matching `guest_<uuid>`. Every file-serving route builds paths from validated ids — never join raw request strings onto directories (Windows `Path(dir) / "E:\\x"` escapes the directory).
- **Sessions:** user session id = user id; guest session id = the client's `guest_<uuid>`. Invalid tokens fall back to guest; invalid guest ids are rejected. The WebSocket requires a user token or a valid guest id; its first message is `session_info {authenticated, user_id, token_rejected}` (closes with 4401 when a rejected token comes without a guest id). The client (`AuthContext.handleSessionInfo`) re-validates on `token_rejected` and on any 401 to a token-bearing request, then signs out cleanly (toast + login modal) instead of staying half signed-in. Tokens nearing expiry are renewed silently via `POST /api/auth/refresh`. The WebSocket reconnects only when the identity (`sessionKey`) changes, not on token refresh.
- **Secrets:** `JWT_SECRET_KEY` must be a random ≥32-char value in `.env` (startup refuses weak/placeholder values); tokens last `JWT_ACCESS_TOKEN_EXPIRE_DAYS` (7). Never print `.env` contents, even masked (comment lines can hold secrets).
- **Abuse limits:** paid AI/GPU endpoints use `RateLimit` token buckets (guests get a small quota on `/api/dj/talk` and `/api/transcribe`; AI analysis in searches is user-only; login/register limited per IP and username); lyric generation is admin-only (`ADMIN_USER_IDS`); uploads are read in chunks with hard caps; API docs are off unless `ENABLE_API_DOCS=true`; shoutout responses never include coordinates (`public_shoutout()`).
- **Media responses:** `MediaAwareGZipMiddleware` (security_middleware.py) never gzips `/api/stream`, `/api/artwork`, beds or stings. `media_streaming_service.parse_range` handles suffix ranges and answers 416.
- **Mobile readiness:** status, open items and the device test plan are in `docs/MOBILE_LAUNCH_READINESS.md`; the brand masters and the script that rebuilds the app icons are in `brand/2026-refresh/`.
- **DB connection budget** (Postgres `max_connections` 100, 3 reserved): async engine 20 + 10 overflow (30 s wait), psycopg2 pools 16 per database × 3 (catalog, user content, embeddings) that block up to 30 s when full instead of opening extra connections, sync engine unpooled (startup `create_all` only) — at most ~80 in total. Async connections carry `idle_in_transaction_session_timeout` 120 s. Never hold a DB session for the life of a WebSocket or a streamed response: auth reads users from `user_data_cache` (`merge(load=False)`, or session-less `get_cached_current_user` for media streams) and the WebSocket uses short `AsyncSessionLocal()` blocks.
- **nginx (plair.live block of `C:\nginx\conf\nginx.conf`, shared with other sites; edit only that block):** forwards `X-Real-IP`/`X-Forwarded-For` (per-IP limits are live), gzip for static JS/CSS/JSON, HSTS + nosniff + referrer-policy headers (repeated in `location /` because its own `add_header` stops inheritance), `/ws` has `access_log off` and a 75 s read timeout. Test with `nginx -t`, apply with `cd /c/nginx && ./nginx.exe -s reload` (graceful).

### 14. Motion System - One Design Language

**Files:** `client/src/lib/motion.js` (tokens + presets), `client/src/lib/microMotion.js` (WAAPI micro-interactions), `client/src/components/Motion.jsx` (Expandable, ExpandSection, FadeSwap), `client/src/hooks/useEntranceWindow.js`, `client/src/hooks/useArtPop.js`, `client/src/contexts/QualityContext.jsx` (adaptive quality tiers).

- All durations, easings and springs come from `motion.js` tokens (`DURATION`, `EASE`, `SPRING`, `TWEEN`, `PRESETS`, `VARIANTS`, `MICRO`, `CSS_TRANSITION`); CSS uses the matching `--dur-*` / `--ease-*` variables and Tailwind `duration-*` names. Never hard-code a duration or spring.
- Startup: the `#splash` in `index.html` (inline CSS, shows from the first paint) stays until `lib/splash.js` has both `scene` (first shader frame, `AudioReactiveCanvas`) and `playback` (first `playback_state`), 4 s at most, then fades out over the finished app. Layers that arrive late (modal blurs) use `ui-layer-in` (fades from 0 to the element's own opacity).
- **One notice channel (SSOT):** every message that pops up at the top is a notice in UIState: `showNotice({ key, tone, text, icon, content, duration, sticky, dismissible, priority, borderClass/borderColor })` / `hideNotice(key)`, rendered only by `NoticeStack` with `NoticeChip` (`components/Notice.jsx`: dark glass pill, tone-coloured edge + default tone icon; tones success/error/warning/info/neutral). Passing notices time out per tone (`NOTICE_DURATION_MS` 2 / 2.5 / 3.5 / 4 s; a longer request is shortened; 0 or `sticky` stays), share one key per tone+text (repeats replace), at most 3 passing at once, tap to dismiss. Toasts (`toastSuccess/Error/Info/Warning`, `publishToast`, `removeToast`) are thin wrappers; the old `position` argument is ignored. Sources that stay up are render-nothing bridges that show/hide a keyed sticky notice: `OnAirNotice` (priority 0, while `engineState.talkBreak`), `DeviceNotice` (Player, 'Playing on another device' + Play here), `OfflineNotice` (AppBridges), `DJActivityBridge` (tool chips, priority 2, hidden offline). Never add another floating message surface or a second store; add a bridge that calls `showNotice`.
- Tactile feedback: add `ui-tap` (icon buttons) or `ui-press` (wide buttons) and one document-level listener runs the press/release pop. Positive actions use `MICRO.burst`, bans `MICRO.nope`, artwork `useArtPop`.
- Animate transform/opacity only (no width/height/box-shadow/filter), no per-frame React state, nothing off-screen. `data-motion` (full / lite / off) follows QualityContext tiers and `prefers-reduced-motion`.
- Visual state priority (Recording > AI Processing > DJ Speaking > Music Playing > Paused > Idle) has an ON AIR modifier during Radio Mode talk breaks (`engineState.talkBreak`: the sticky `OnAirNotice` pop-up, `OnAirFrame`, shader tint; no ON AIR badge in a panel or the player). While a break is on air the player's waveform becomes the break's progress bar: `usePlaybackActions().talkBreakProgress()` (the DJ stream's clock on the playing device, `on_air_at_ms` + `server_time_ms` on the others); breaks air only once fully rendered, so the length is exact. The background shader also glows with the speaking DJ's voice colour (`VOICE_*` constants in `AudioReactiveCanvas.jsx`), in both the panel scene and fullscreen visuals (`AMBIENT_GLOW_FUNCTION` is shared by the scene and backdrop shaders); shared shader exports for `offlineVideoRenderer` must stay unchanged.
- Frame-rate rule: always aim for the device's own refresh rate (120 Hz phones get 120 fps). Quality tiers trade resolution (DPR), glass blur taps and parallax detail to hold that target, never the frame rate: tiers 2-4 are uncapped (`fpsCap: 0`); only tiers 0-1 (weak or software GPUs) and scenes hidden behind a modal (`OVERLAY_FPS_CAP`) run at 30.
- Shader efficiency rules (keep quality and full frame rate, 120 fps included, never cap it): values that are the same for every pixel (glow centre, aspect, colour × level, panel edge tint) are computed once per frame into `u_glow_*` / `u_panel_glow` uniforms, and the glow only runs while `u_glow_active` (DJ speaking or ON AIR). The low-res background capture pass only re-renders when background inputs change (the signature prefix up to `captureLength`, textures, video, resize). The lyric texture is skipped while `u_text_empty` (defaults to 0, so the video export is unaffected). Three.js `VideoTexture` uploads its own new frames; don't set `needsUpdate` on it per frame.

### 15. City Pulse, Location, Radio Mode & Cost Systems (Sept 2026)

Design doc: `docs/CITY_PULSE.md`. All open work, bugs and owner decisions are in one list, `docs/KNOWN_ISSUES.md`; add to it instead of writing a new handover.

- **Listener location (SSOT):** `context_service.listener_location(user, session_id)` (`services_radio/listener_location.py`). Logged-in users come from the profile; guests send `listener_location` over the WebSocket and are held in memory only (6 h TTL, never stored). Fallback: device location, then the timezone city, then `NEWS_DEFAULT_COUNTRY`. Every location consumer (news, weather, events, places, geocode, air/pollen, local time, Radio Mode, DJ context) goes through it.
- **Regional knowledge (city pool):** `services_radio/regional_knowledge.py` defines the `Collector` interface, `KnowledgeItem` and `RegionalKnowledgeService.query(...)`. Collectors: Ticketmaster events, Google Places IDs, Google News. Shared per region, targeted per listener by taste (no LLM).
- **Places (semantic source):** every Google Places result we fetch is kept in `place_cache` for good (refreshed when fetched again) and embedded on the fly into the `place_nuggets` source (`local_knowledge.PlaceVectorDatabaseService`: name, type/tags, area, hours, rating/price, plus a `where`). The city sweep (`GooglePlacesCollector`, `REGIONAL_PLACES_*`) fetches full records per category into the same cache. `PlacesNode` searches it by meaning within `PULSE_CITY_RADIUS_KM`; Google is asked live only when nobody has asked a similar question near that spot (`place_memory.searched_near`, `PLACE_MEMORY_*`).
- **Area signals:** `area_signals.py` with `area_geocode.py`, `area_air_quality.py` and `area_pollen.py` (Google APIs on the PLAiR project key `GOOGLE_PLACES_API_KEY`), shared per grid cell in `area_cache`.
- **News store:** `services_radio/news_store.py` persists Google News pulls and items in Postgres, reuses them by meaning plus matching stories, stores the ranking once, and keeps an aired ledger per listener.
- **Radio Mode:** opt-in scheduled talk breaks (`radio_mode_service.py`, `radio_segments.py`, `radio_schedule.py`, `music_beds.py`; client `lib/talkBreak.js`, `lib/musicBed.js`, `RadioModeSettings.jsx`, `OnAirBadge.jsx`). Music beds are in `CATALOG_DIR/music_beds`, stings in `CATALOG_DIR/stings`, and neither is ever in the main catalog.
- **Stings & talking clock:** `sting_service.py`, `sting_types.py`, `sting_schedule.py`, `talking_clock.py`, `station_ids.py`, `station_voice.py`. Station voice is `tts_chatterbox/voices/station.wav` with `station_treatment`; the spoken name is `STATION_NAME_SPOKEN` ("Play Air"). Between tracks: musical and voice stings, randomly every 10-15 min. Mid-song: voice-only IDs and time checks on their own timer (Radio Mode only).
- **LLM routing and cost:** `services/llm_router.py` has role chains (`LLM_LIVE` Gemini 3.5 Flash-Lite; `LLM_ANNOUNCE/INTERPRET/BACKGROUND` DeepSeek flash, then Gemini fallback)). Rule (owner, 2026-10-01): Gemini only for what is urgent and interactive (the live DJ turn and its Producer); everything else runs on DeepSeek, split like the live DJ vs the between-track announcer. Voice-take and filler scripts (paralanguage, breaths, impulse and interlude scripts) use `LLM_ANNOUNCE` (`dj_prompt_system_service.VOICE_SCRIPT_ROLE`); `_execute_gpt_stream` has no default role so nothing lands on Gemini by accident. `services/llm_telemetry.py` is the one price table. `services/usage_tracking.py` + `usage_middleware.py` attribute every paid call (and cache savings) to a user, guest or system scope; the admin view is `UsageStatsModal.jsx` / `routers/usage.py` (`ADMIN_USER_IDS`).
- **Artwork thumbnails:** `GET /api/artwork/{id}/thumb/{size}` (`services/artwork_thumbnail_service.py`); the client prefetches through `lib/artworkPrefetcher.js` and `useArtworkThumb`.
- **Semantic sources (one pattern for everything searchable):** tracks, shoutouts, local knowledge (events), news and listener requests are all `BaseVectorDatabaseService` sources: a source table of `(rowid, id, metadata_json)`, named semantic categories with weights, a per-category embedding cache table, one weighted vector per item, A/B Annoy indexes (still used for seed radio), and search that re-weights categories per query (regex presets or the query-intent prompt cache) and ranks every item (`services/semantic_source.py` `SemanticSearch`, used by music and shoutout search too). Every source uses one encoder, `SEMANTIC_ENCODER` (all-mpnet-base-v2, 768-dim; the TTS clip cache uses it too). Embedding tables and index files carry the encoder slug (`<category>_mpnet_embeddings`, `<prefix>_mpnet_1.ann`), so changing the encoder rebuilds cleanly; query-intent caches and the Producer cache re-embed stale rows in place. New sources subclass `SemanticVectorDatabaseService` with a `category_specs` list: adding a category is one line.
- **City Pulse sources:** local knowledge (`services_radio/local_knowledge.py`) is the `local_nuggets` view over `regional_items` (events and local news filled by the collectors; categories title, tags, people, place, details, kind, when). Listener requests (`services/listener_request_service.py`, table `listener_requests` in the user-content DB) store every ask with what the station answered (categories text, topic, answers, intent, area, daypart, daily-salted asker hash; no user ids or coordinates). Both rebuild when dirty (`listener_request_maintainer`, `PULSE_REQUEST_REBUILD_S`). Shoutouts are searched through the existing shoutout search service.
- **City Pulse router:** `services_radio/pulse.py`. Search only retrieves candidates: each source returns its closest matches (`per_kind`) and there are no relevance thresholds. The LLMs judge relevance. The Producer (`context_router_service`, cached per input) outputs `pulse_topic`, `pulse_kinds`, `pulse_near_me` and `pulse_when` next to the tool plan; the `city_pulse` node searches only those kinds (none for banter). The DJ, and the For You agent, read results grouped by source and use only what fits. Sources: events (local knowledge), places (place memory), news (news source by meaning; `near_me` = the listener's city), tracks and artist bios (catalog search), shoutouts (shoutout search, region-filtered), weather, air/pollen, charts, trends. `near_me` filters by distance only for things that have a location. `related()` links by exact name mentions and gig-to-place distance. Every route records the ask once with its answers (`pulse.note_request`); trends group asks with the same intent and topic or a shared answer, and hot topics steer the news and Ticketmaster sweeps. Tests: `tests/pulse_links_test.py`, `tests/semantic_probe.py`, `tests/dj_pulse_test.py`.
- **Where (one spatial standard):** anything placeable carries an optional `geo.Where` (label, centre, radius, scope spot/street/neighbourhood/city/region/country; `services_radio/geo.py`). Radius and scope come from Google Geocoding, cached per phrase in `geo_places`. Events and places use their coordinates; news is placed by a background LLM pass (`NewsService.locate_pending`, `news_items.geo_*`); shoutouts by `about_place` from their analysis pass (else their recording area); the listener is a `Where` too (`PulseListener.where`). `relation()` (what the DJ reads as `near`), `near()` ("near me"), `gap_m` (nearest) and `overlap()` (same-street links across gigs, places, news and shoutouts) are the only spatial rules. Never store a listener's own coordinates in a `Where`; public shoutouts show only its label and scope.
- **AI autonomy by length:** the between-track announcer gets a TALKING POINTS MENU from the pulse sized to the window (`menu_for_window`: 2 / 4 / 7 options, pick up to 1 / 2 / 3; `DJ_ANNOUNCER_MENU_ENABLED`). Radio Mode's **For You** feature (`ForYouSegment`, under the Features toggle, at most once per `RADIO_FOR_YOU_INTERVAL_S` per listener, first in line when due) runs `services_radio/pulse_agent.py`: a read-only agent given only the job ("a two-minute narrative for this listener") and the tools (`listener_context`, `recent_conversation`, `on_air_now`, `pulse_search`, `pulse_detail`, `city_trends`), which picks its own angle and returns cited story beats for the normal segment script. It runs on DeepSeek (`ai_service.run_tool_turn`: DeepSeek function calling on the role's chain, Gemini tool loop as fallback); only the live DJ turn uses `run_gemini_tool_turn` directly. Probe: `tests/for_you_probe.py [--user N]`.
- **Offline mode:** see section 16 and `docs/OFFLINE_MODE.md`.

### 16. Offline Mode

**Files:** `NetworkContext.jsx` (health checks), `PlaybackContext.jsx` (local mode + hand-back), `lib/api.js` (`_routeRequest`, connectivity events `trouble`/`lost`/`recovered`), `lib/offlineAPI.js` (local backend + local radio queue), `lib/cacheManager.js` / `lib/offlineStorage.js` (IndexedDB library), `lib/backgroundDownloader.js`, `OfflineNotice` in `components/AppBridges.jsx`, `public/sw.js` + the `asset-manifest.json` plugin in `vite.config.js`. Full notes: `docs/OFFLINE_MODE.md`.

- `audioState.offlineMode` (UIState) is the SSOT for "running on the downloads"; it is `connectionMode !== 'full'`. Gate offline behaviour on it, never on `audioState.isOnline` (browser flag only). The server counts as down only after 2 failed `/api/health` probes and back up after 2 successes (more if it flapped); never flip on a single failure.
- API calls that hit a network error or 502/503/504, and a WebSocket that closes abnormally, call `api.reportServerTrouble()` (immediate probe). Unreachable is not rejected: only a 401/403 or WS close 4401 may sign the user out.
- **Local mode** (`PlaybackContext` `localRef`): entered when `offlineMode` turns on. This device becomes locally active, keeps the current song if it is playing or downloaded, and plays the rest from the downloads via `offlineBackend` (`startLocalSession`, `advanceTo`, `next`, `previous`). `applyLocalState()` never reloads the playing track. Server snapshots are stashed, not applied, while local. A stream stalled 1.5 s while offline skips to a download.
- **Hand-back** on recovery (first snapshot on the new socket): `claim` (if needed) + `play {track_id}` + `seek {position_ms}` (+ `pause`), with `handoverRef` holding back stale snapshots until acknowledged. No reload, no jump. If another online device is active and this one is playing, finish the song, then `stepAside()`. A device that never played locally just follows the server. Don't send transport commands while local.
- `WebSocketContext.send` returns `'offline'` while `offlineMode` is on; queued playback/talk-break messages are dropped when the server is lost, and the socket reconnects immediately on `recovered`.
- `audioEngine.handleOfflineTransition()` swaps a still-streaming song to its downloaded copy seamlessly and keeps the slot metadata (`duration_ms` drives crossfades). Stream chunk failures retry every 2 s while the buffer plays.
- Cached track metadata is normalized (`normalizeTrackMetadata`); read the library via `cacheManager.getCachedTrackList()` (no blobs, memoized), not `getAllCachedTracks()`.
- Offline preference changes queue in `offline_pending_preferences` and replay via `api.syncOfflineWrites()` on recovery. Offline settings changes queue in `offline_pending_profile`: a later successful online save drops the queued value for the same keys (`clearPendingProfileKeys`), and anything still queued is synced as soon as the app is online (App settings effect), so a missed recovery can't pin a setting.
- Service worker: precaches the build's `asset-manifest.json`, matches with `ignoreVary`, never intercepts `/api/*` (incl. streams), answers `Range` from cache with 206. Bump the cache names in `sw.js` when changing its caching rules.
- Download space = half the browser quota (max 2 GB) on every platform, iOS included; `navigator.storage.persist()` is requested once downloads exist (not on Firefox). Background downloads back off 15 min after a < 1 Mbps download; Wi-Fi vs cellular is unknowable on Safari/Firefox (accepted).

### 17. Community: Shoutouts, Replies & Reviews

**Files:** `services/user_content_database_service.py` (store), `services/user_content_speech_enhancement_service.py` (audio + LLM filter), `services/community_engagement.py` (likes/bans/plays), `services_radio/community_on_air.py` (what airs), `routers/shoutouts.py`, `sting_service.py` / `announcer_service.py` (review stings); client `ShoutoutModal.jsx`, `ReviewModal.jsx`, `Shoutouts.jsx`.

- **One store:** every item is a row in `shoutouts` plus `USERS_DIR/<uid>/shoutouts/<ts>.json` (+ `.mp3`), with `content_type` = `shoutout` | `reply` (`parent_id`, one level deep, only to a shoutout) | `review` (`track` {id,title,artist,genre}). Use `kind_of()`, never assume. Items are voice (mp3) or typed (`text_only`, no mp3, `audio_url` null). The JSON on disk is the source of truth (self-syncs at boot); reply counts come from the in-memory `children` index.
- **Saving:** `create_voice_item` (from the turn's own recording: the WebSocket voice turn puts its webm in `session_dict['recording']`; routes save uploads with `_ingest_recording`; never "latest upload") and `create_text_item`. Both index the vector DB at once and broadcast `public_shoutout()` (no coordinates). DJ tools `save_shoutout`, `save_shoutout_reply` (no `parent_id` = the shoutout that just aired for this listener, `community_engagement.last_aired`), `save_review`; voice or typed, signed-in only.
- **Editor verdict before saving (everywhere):** the DJ tools `save_shoutout`, `save_shoutout_reply` and `save_review`, and the app's own review/reply routes (`routers/shoutouts._save_item`, typed or recorded) first ask an editor AI (`services_radio/community_judge.py`, one structured call on `LLM_LIVE`, ~1.0-1.5 s) whether the listener's words are worth keeping (friendly bar; replies can be short). Scrapped: nothing is saved and the tool returns `status: scrapped` with the editor's feedback, which the DJs pass on in their own words. Kept: the normal pipeline below runs and the feedback comes back too. In the app the verdict shows in the shared notices (`Review not posted: …` keeps the text for editing; `Reply posted! …`). If the editor can't answer, the save goes ahead.
- **Audio chain (`SHOUTOUT_ENHANCEMENT_VERSION` 3):** native 48 kHz decode (browsers record Opus at 48 kHz; the client records mono Opus at 32 kbps / AAC 64 kbps) → MossFormer2_SE_48K (4 s windows under `no_grad`, ~0.6 GB) → 75 Hz low cut → LLM filter on `LLM_BACKGROUND` (process talk, stumbles) cut on Whisper word timestamps → Silero VAD shortens pauses over 0.45 s (never where a word starts) → -14 LUFS → MP3. No compression server-side: the client DJ broadcast chain compresses on air. Measured with Audiobox Aesthetics on real uploads: the old 16 kHz DeepFilterNet + super-resolution chain scored below the raw recording. Bumping the version makes the asset doctor re-render every item from its source webm (backups kept).
- **Reviews → stings:** the review prompt returns `sting_quote` (3-12 exact words that stand alone over the song, null for negative/unsafe); `_sting.mp3` is cut from the final audio. When a track starts, `_schedule_review_sting` finds a quiet or lyric-free window (loudness or lyric gaps, 5-85 %) and plays the best-ranked fresh review of that track (`build_review`/`play_review`, `REVIEW_STINGS_*`); it replaces that track's mid-track sting. Applies to all listeners, gated by the `reviews` radio pref (default on, "Listener reviews over songs") and the usual sting gate minus the stings pref.
- **On air:** `community_on_air.pick` is the one way to choose shoutouts for the DJ (`play_shoutouts`), Community Corner and Pulse: shoutouts only, minus the listener's bans, items buried by bans (≥3 bans and more bans than likes) and anything aired to this listener in the last 30 min; each carries its `top_reply` (ranked by likes/plays) and the DJ is told to play it right after. Plays inside the DJ mix are logged (`record_on_air_play`, deduped 6 h). Pulse has a `review` kind (`ReviewsNode`); community `detail()` includes the top reply.
- **Tests:** `tests/community_test.py --base URL [--recording file.webm] [--keep]` (typed review, spoken review + sting, typed reply, ranking, no coordinates; cleans up after itself).

## State Flow Architecture

**Frontend → Backend:** Component → Context → API → Service → Database → WebSocket Broadcast → All Clients

**Backend → Frontend:** Backend Event → broadcast_playback_state_to_session() → WebSocket → PlaybackContext.handlePlaybackState() → reportEngineStatus() to UIState → UIState auto-preloads artwork → Components Re-render

**Optimistic Updates:** UI updates immediately → API call → Backend broadcasts → Frontend verifies → Revert if mismatch

## Key File Locations

### Frontend Contexts (State Management)

**Core Engine Contexts (Report to UIState):**
- `client/src/contexts/UIStateContext.jsx` - **CRITICAL** SSOT for all UI state (visual state, playback state, device state)
- `client/src/contexts/PlaybackContext.jsx` - Music playback engine (audio loading, crossfading, device enforcement)
- `client/src/contexts/VoiceRecordingContext.jsx` - Microphone recording engine (voice commands, shoutouts)
- `client/src/contexts/PlaybackShoutoutContext.jsx` - Shoutout playback engine (user-generated audio playback)

**Infrastructure Contexts:**
- `client/src/contexts/WebSocketContext.jsx` - WebSocket client (message subscription, connection management)
- `client/src/contexts/ViewportContext.jsx` - Responsive breakpoints (window size, device detection, scaling)
- `client/src/contexts/NetworkContext.jsx` - Network status (online/offline, connection quality, bitrate detection; without `navigator.connection` (Safari/iOS/Firefox) the speed test times a 516 KB `Range: bytes=0-` download of `/images/plair_icon.png`, which the service worker never caches)
- `client/src/contexts/StorageContext.jsx` - Storage management (cache size, quota, cleanup)

**Data Contexts:**
- `client/src/contexts/AuthContext.jsx` - Authentication state (user, token, login/logout)
- `client/src/contexts/PreferencesContext.jsx` - User preferences (track/shoutout likes, bans, super_likes)
- `client/src/contexts/GenerationQueueContext.jsx` - Suno music generation queue (job status, progress tracking)
- `client/src/contexts/DynamicThemeContext.jsx` - Theme/category metadata (colors, icons, interaction effects)

### Frontend Components

**Core UI Components:**
- `client/src/components/Player.jsx` - Main playback controls (play/pause/skip, volume, progress bar with segment colors)
- `client/src/components/NowPlaying.jsx` - Now playing display (artwork, track info, A/B crossfade, parallax effects)
- `client/src/components/Queue.jsx` - Playback queue display (draggable items, interaction effects)
- `client/src/components/Catalog.jsx` - Track catalog grid (virtual scrolling, artwork preloading)
- `client/src/components/Radio.jsx` - Radio panel container (seed modes, playlists)
- `client/src/components/User.jsx` - User profile panel (settings, preferences)
- `client/src/components/Conversation.jsx` - DJ conversation interface (text/voice input, message history)

**Media Components:**
- `client/src/components/MediaActions.jsx` - Like/ban/superlike buttons (engagement tracking, interaction effects)
- `client/src/components/MediaSearch.jsx` - Search interface (track/artist search)
- `client/src/components/Shoutouts.jsx` - User shoutouts panel (playback, analytics)
- `client/src/components/ParallaxArtwork.jsx` - Parallax artwork component (depth-based scrolling)
- `client/src/components/AudioReactiveCanvas.jsx` - **SSOT** Shader-based audio visualizer (exports shared shaders + effect functions for offlineVideoRenderer)
- `client/src/components/MediaStatsOverlay.jsx` - Media statistics overlay (playback stats)
- `client/src/components/MediaShared.jsx` - Shared media components

**Modals (client/src/components/modals/):**
- `Modal.jsx` - **Base modal component** (blurred artwork background, category gradients, animated borders)
- `SeedRadioModal.jsx` - Radio seeding modal (category selection)
- `GenerationModal.jsx` - Music generation modal (Suno generation UI)
- `ShoutoutModal.jsx` - Shoutout playback modal (transcription, analytics)
- `TrackAnalyticsModal.jsx` - Station analytics modal (top hits selection)
- `UploadMusicModal.jsx` - Human music upload modal (drag/drop, Gemini analysis, metadata preview)
- `ShareModal.jsx` - Content sharing modal (video export with music video background)

**Device/Settings Components:**
- `client/src/components/DevicePicker.jsx` - Device selection/management (multi-device playback, device naming)
- `client/src/components/BitratePicker.jsx` - Audio quality selector (auto, 128k, 192k, 256k)
- `client/src/components/GenerationQueuePanel.jsx` - Generation queue panel (job progress, track list)

**UI Utilities:**
- `client/src/components/Panel.jsx` - Generic panel wrapper (consistent styling, animations)
- `client/src/components/Scroller.jsx` - Custom scroller (haptic feedback, smooth scrolling)
- `client/src/components/Notice.jsx` / `NoticeStack.jsx` - the one notice channel (`NoticeChip`; see section 14)
- `client/src/components/VirtualScroller.jsx` - Virtual scrolling (large lists, lazy loading)
- `client/src/components/KeyboardControls.jsx` - Keyboard shortcuts (space = play/pause, arrows = seek)
- `client/src/components/InteractiveEngagementButton.jsx` - Interactive buttons (haptics, visual feedback)
- `client/src/components/MediaSearchMatchBadge.jsx` - Search result badges (match highlighting)
- `client/src/components/GestureGuide.jsx` - Gesture tutorial (onboarding)
- `client/src/components/FPSCounter.jsx` - Performance monitor (dev tool)

**Auth Components:**
- `client/src/components/Auth/Login.jsx` - Login form
- `client/src/components/Auth/Register.jsx` - Registration form

**Main Files:**
- `client/src/App.jsx` - Root app component
- `client/src/main.jsx` - React entry point

### Frontend Hooks

**Audio Hooks:**
- `client/src/hooks/useAudio.js` - Audio engine hook (play, pause, seek, volume, crossfade)
- `client/src/hooks/useDJAudioStream.js` - DJ TTS audio stream (real-time voice playback, device-aware). Mounted once at the App root as `<DJVoiceEngine />` (never inside a panel, so collapsing/remounting panels cannot cut the DJ); owns its own audio element, pauses while the mic records
- `client/src/hooks/useFFTProcessor.js` - **DRY** Unified FFT processing (DJ/Voice/Shoutout frequency analysis, two modes: frequency_bands & logarithmic)
- `client/src/hooks/useUISound.js` - UI sound effects (click sounds, haptic feedback)
- `client/src/hooks/useVoiceRecorder.js` - Voice recording (microphone access, audio processing)

**UI Hooks:**
- `client/src/hooks/usePointerInteraction.js` - Pointer interaction handling (touch, mouse, hover)
- `client/src/hooks/useDeviceSelector.js` - Device selection logic (active device detection)
- `client/src/hooks/useVirtualWindow.js` - Virtual windowing (scroll optimization for large lists)
- `client/src/hooks/useProfilePicture.js` - Profile picture handling (upload, cache)
- `client/src/hooks/useGeolocation.js` - Geolocation services (user location, weather)

### Frontend Libraries

**Core Libraries:**
- `client/src/lib/audioEngine.js` - **CRITICAL** Audio playback engine (dual-buffer A/B crossfading, device enforcement, `_rampGain()` for all gain automation)
- `client/src/lib/safeStorage.js` - **CRITICAL** Safe localStorage wrapper (try-catch with in-memory fallback for iOS private browsing). ALL localStorage access MUST go through `safeStorage.get/set/remove`
- `client/src/lib/session.js` - Device ID management (safeStorage, UUID generation)
- `client/src/lib/api.js` - **CRITICAL** API client (ALL backend calls route through `_routeRequest()` for offline/online switching)
- `client/src/lib/logger.js` - Logging utilities (console formatting, log levels)
- `client/src/lib/utils.js` - Utility functions (date formatting, string manipulation)

**Caching/Offline:**
- `client/src/lib/mediaCache.js` - Media caching (artwork, audio, multi-layer: memory + IndexedDB + Cache API)
- `client/src/lib/cacheManager.js` - Cache management (quota, cleanup, eviction)
- `client/src/lib/backgroundDownloader.js` - Background downloads (queue, retry, progress)
- `client/src/lib/offlineAPI.js` - Offline API fallback (cached responses)
- `client/src/lib/offlineStorage.js` - Offline storage (local data persistence)
- `client/src/lib/cacheValidator.js` - Cache validation (staleness checks)

**Audio Processing:**
- `client/src/lib/audioMixer.js` - Audio mixing (volume control, crossfading)
- `client/src/lib/djStreamPlayer.js` - DJ stream player (one MediaSource per stream, strictly sequential queue, cancel with fade, stall/reconnect watchdog, mic hold); pure logic with injectable env, node-testable
- `client/src/lib/djBroadcastChain.js` - DJ broadcast chain (audio pipeline; only processes while the DJ is audible, all gain changes via `_rampGain`)
- `client/src/lib/audioInteractionManager.js` - Audio interaction manager (click-to-play, autoplay policy)

**Video/Rendering:**
- `client/src/lib/offlineVideoRenderer.js` - Offline video export (frame-by-frame WebGL rendering, MP4 encoding via WebCodecs, uses shared shaders from AudioReactiveCanvas)

**UI Libraries:**
- `client/src/lib/soundModes.js` - the four DJ sound modes (DJ + PING, DJ only, PING only, OFF over `settingsState.ttsMuted` / `notificationsMuted`), shared by the player button and User → Sounds; `useUISound` skips notification sounds while `notificationsMuted` (mic press/release feedback still plays)
- `client/src/lib/haptics.js` - Haptic feedback (vibration patterns)
- `client/src/lib/textRenderer.js` - Text rendering (canvas-based text)
- `client/src/lib/themeManager.js` - Theme management (color schemes)
- `client/src/lib/retryUtils.js` - Retry utilities (exponential backoff)

### Backend Core Services

**Playback & State Management:**
- `server/services/playback_state.py` - **CRITICAL** Playback state machine (queue, radio_mode, active_device_id)
- `server/services/playback_service.py` - Session-level playback management (multi-user coordination)
- `server/services/device_management_service.py` - Device management (registration, activation, listing)
- `server/services/websocket_service.py` - WebSocket connection management (session broadcasting, cleanup)
- `server/app.py` - FastAPI app: lifespan (service startup/shutdown), middleware, router includes
- `server/routers/` - API routes by domain (auth, playback, catalog, share, media, shoutouts, dj, devices, ws, ...); `deps.py` shared dependencies, `schemas.py` request models
- `server/service_registry.py` - `services` registry populated by the lifespan and read by routers
- `server/security_middleware.py` - request guard (path traversal, guest id format)

**Authentication & User:**
- `server/services/auth_service.py` - Authentication (JWT tokens, password hashing)
- `server/services/user_profile_service.py` - User profile management (username, settings, location)
- `server/services/preferences_service.py` - User preferences (audio quality, theme, TTS settings)
- `server/services/user_data_cache_service.py` - In-memory cache for users and their likes/bans (auth reads users from it; any write to a `User` row must call `invalidate_user`, as `UserProfileService.update_profile` does, or `/api/auth/me` serves stale settings)
- `server/services/profile_picture_service.py` - Profile picture uploads and serving

**Media Serving:**
- `server/services/media_streaming_service.py` - Media file streaming (range requests, bitrate selection)
- `server/services/rate_limit_service.py` - Rate limiting (per-user request throttling)

**Analytics:**
- `server/services/analytics_service.py` - Analytics tracking (play events, engagement)
- `server/services/analytics_file_service.py` - File-based analytics storage (JSONL, exports)

**AI Services:**
- `server/services/ai_service.py` - Gemini LLM wrapper (`google-genai`: `call_gemini`, `call_gemini_with_tools`, structured output)

### Backend Data Services

**Catalog Management:**
- `server/services/catalog_database_service.py` - Track catalog (CRUD operations, metadata queries)
- `server/services/base_vector_database_service.py` - Shared base for the catalog and user-content vector DBs (embedding caches, weighted embeddings, A/B Annoy indexes with stale-index rebuild)
- `server/services/catalog_vector_database_service.py` - Catalog vector DB (hooks on the base class)
- `server/services/catalog_vector_search_service.py` - Track similarity search (semantic search, recommendations)
- `server/services/base_prompt_cache_service.py` - Shared base for the catalog and user-content query-intent prompt caches
- `server/services/catalog_vector_search_prompt_cache_service.py` - Prompt caching for vector search (hooks on the base class)

**User Content Management:**
- `server/services/user_content_database_service.py` - User content database (shoutouts, recordings)
- `server/services/user_content_vector_database_service.py` - User content vector DB (semantic search for user audio)
- `server/services/user_content_vector_search_service.py` - User content vector search
- `server/services/user_content_vector_search_prompt_cache_service.py` - User content prompt cache
- `server/services/user_content_speech_enhancement_service.py` - Shoutout/reply/review audio (MossFormer2_SE_48K, VAD pause trim, LLM filter, review stings) - see section 17

**Database Core:**
- `server/database/connection.py` - Database connection (async SQLAlchemy)
- `server/database/models.py` - SQLAlchemy models (User, Track, Conversation, etc.)

### Backend Audio Processing Services

**Audio Analysis & Transcoding:**
- `server/services/audio_features_service.py` - Audio feature extraction (tempo, key, energy, loudness)
- `server/services/audio_transcoding_service.py` - Format conversion (MP3 encoding at multiple bitrates)
- `server/services/audio_master_service.py` - Audio mastering (dynamic EQ, compression)
- `server/services/audio_sonic_master_service.py` - Sonic mastering (alternative mastering engine)

**Audio Source Separation & Enhancement:**
- `server/services/audio_demucs_service.py` - Audio source separation (Demucs - vocals, drums, bass, other)
- `server/services/audio_clearvoice_service.py` - Voice enhancement (ClearVoice - noise reduction for speech)
- `server/services/audio_apollo_service.py` - Apollo audio processing (advanced separation model)

**Lyrical Processing:**
- `server/services/audio_lyrical_timestamp_service.py` - Lyrical timestamping (word-level alignment)
- `server/services/whisper_dual_service.py` - Speech-to-text (Whisper - transcription, language detection)

### Backend Music Generation Services (Suno)

**Core Suno Services:**
- `server/services/suno_service.py` - Suno API client (song generation, clip fetching)
- `server/services/suno_metadata_service.py` - Suno metadata extraction (tags, style analysis)
- `server/services/suno_prompt_service.py` - Suno prompt generation (AI-assisted prompt crafting)
- `server/services/suno_service_orchestrator.py` - Suno service coordination (workflow management)
- `server/services/suno_generation_queue_service.py` - Generation queue management (job tracking, status updates)

**Metadata Enrichment:**
- `server/services/suno_enriched_metadata_service.py` - Enriched metadata (AI-generated descriptions, categorization)
- `server/services/suno_artwork_enrichment_service.py` - Artwork enrichment (depth maps, color analysis for parallax)

### Backend Human Music Upload Services

**IMPORTANT:** Human uploads use the SAME catalog system as AI tracks. See `docs/HUMAN_MUSIC_UPLOAD.md` for full details.

- `server/services/human_metadata_extraction_service.py` - Gemini (`GEMINI_UPLOAD_ANALYSIS_MODEL`, 3.5 Flash) audio analysis (genre, mood, lyrics, mix decisions, artwork prompt)
- `server/services/human_music_upload_service.py` - Upload pipeline (validation, transcoding, mastering, catalog integration, edits)
- `server/services/artist_profile_service.py` + `routers/artists.py` - artist/band profiles (`artist_profiles` table)

**AI track credits are compulsory:** every AI track has `derived_tags.inspired_artist` (`services/catalog_credit.py`: the credit, then the artist in the original request, then the first similar artist; the enrichment requires it, the Asset Doctor repairs it). The search's artist category reads the credit and the inspired-by artist. Audit the catalog with `server/utils/catalog_audit.py`.

**Key Principle:** Human tracks are stored in the same `tracks` table with `is_ai_generated=0`. They use identical metadata schemas, playback systems, and discovery pipelines as AI tracks.

**Credits (least resistance):** uploading is one tap; everything is automatic and editable afterwards. A user's bands are artist profiles they define once (User panel → Artists & Bands); an upload is credited to the chosen profile, else their last-used, else their first, else one made from their username. The credit is `generation_params.artist_name` (= `track_info.artist`, plus `artist_profile_id`/`artist_slug`), the field every view reads; `derived_tags.inspired_artist` is only a "sounds like" comparison and never the credit. Titles: uploader's > embedded tag (mutagen, `embedded_tags`) > a real title in the filename > the sung hook (Gemini is told all of these). Renaming a profile re-credits its tracks (`retag_artist`); edits go through `update_track_metadata` (validated, re-indexed; a lyrics edit drops the lyric timing so the asset doctor re-aligns it). "Enhance audio" (Apollo) is the user's remembered opt-in (`users.upload_enhance`). Uploads, artwork and profile pictures are refused before the body is read without a valid token (`SIGNED_IN_UPLOAD_PATHS`). Fix old uploads with `server/utils/backfill_upload_credits.py`; test with `tests/upload_credit_test.py --file <audio> [--cancel-file <other audio>]` (runs the full pipeline, 2-8 min).

**Rights, sharing, jobs, duplicates:** the first upload asks once for a rights confirmation (`users.upload_rights_confirmed_at`). Tracks go live as `visibility` public; unlisted/private tracks are in `CatalogDatabaseService.hidden_ids` and are left out of `get_all_tracks`, vector search, queue fill (`_get_user_preferences` unions them into bans), top hits, charts, on-air stats and new-session seeding; they still play by id. `explicit` is set by Gemini (editable). Music source: each listener's Both / Human / AI setting (radio pref `music_source`, default both) is applied with bans and hidden tracks by `services/listener_filters.excluded_ids` in queue fill, catalog, search, DJ searches and Pulse; no separate human stations, filters or badges. The upload request returns `{"status": "processing", "upload_id"}` as soon as the file is received; processing runs as a background job (`UPLOAD_JOBS` in `routers/user_music.py`, `GET/DELETE /api/user/music/uploads/{id}`), and its final `upload_progress` event is sent only after the job's result is stored. Every human upload carries a Chromaprint `fingerprint` (ffmpeg's chromaprint muxer, first 120 s, `services/audio_fingerprint.py`); after decoding, a match (similarity ≥ 0.8; the same song measured 0.94-1.0, different songs ≤ 0.55) returns the uploader's existing track or refuses another listener's song. Missing fingerprints are backfilled at startup.

### Backend DJ System (services_radio)

**DJ Conversation & Prompting:**
- `server/services_radio/dj_prompt_service.py` - DJ personality prompts (character, tone, knowledge base)
- `server/services_radio/dj_prompt_system_service.py` - DJ system prompts (instructions, formatting)
- `server/services_radio/dj_prompt_helper_service.py` - DJ prompt helpers (context injection, template rendering)
- `server/services_radio/dj_command_executor.py` - Executes DJ tool actions (search, playback, seed, playlist, ratings, segments, saves)
- `server/services_radio/conversation_service.py` - User-DJ conversations (message history, context management)
- `server/services_radio/announcer_service.py` - Station announcements (scheduled broadcasts, event notifications)
- `server/services_radio/persona_service.py` - DJ persona management (personality traits, voice selection)

**Context Management:**
- `server/services_radio/context_service.py` - Context for AI responses (track info, user preferences, session state)
- `server/services_radio/context_node_registry.py` - Context node registry (dynamic context system)
- `server/services_radio/context_router_service.py` - Context routing (selecting relevant context nodes)
- `server/services_radio/context_nodes.py` - Context node definitions (track, user, weather, news, etc.)

**TTS Pipeline:**
- `tts_chatterbox/server.py` - **Local Chatterbox-Turbo TTS engine** (separate process, Flask on 127.0.0.1:8090, own venv; streams PCM s16le 24 kHz from `POST /tts`; `tts_server/` is the retired Orpheus engine)
- `server/services_radio/tts_engine_bootstrap.py` - Launches/health-checks/stops the TTS engine with the backend lifespan
- `server/services_radio/tts_generation_service.py` - Semantic clip cache lookup, else `generate_pcm()` from the engine; saves new clips as FLAC (`write_clip`) + embeddings
- `server/services_radio/tts_processing_service.py` - Audio processing (normalization, compression, effects)
- `server/services_radio/tts_stream_planner.py` - TTS streaming (chunk planning, timing coordination)
- `server/services_radio/tts_queue_manager.py` - TTS queue management (priority, cancellation)
- `server/services_radio/tts_broadcast_service.py` - Audio broadcasting (WebSocket streaming, chunking)
- `server/services_radio/tts_vector_db_service.py` - Vector DB for TTS (voice similarity, caching)
- `server/services_radio/tts_database_migration_service.py` - Re-indexes clip files from disk into the embeddings DB at startup
- `server/services_radio/sting_service.py` - Stings & talking clock (station voice cache + renderer in `station_voice.py`, `talking_clock.py`, `station_ids.py`, `sting_library.py`, `sting_types.py` registry, `sting_schedule.py` rules)

**External Data Services:**
- `server/services_radio/external_web_service.py` - Web scraping (artist info, lyrics, news)
- `server/services_radio/external_news_service.py` - News API (headlines, articles)
- `server/services_radio/external_location_service.py` - Location services (geocoding, timezone)
- `server/services_radio/external_events_service.py` - Events API (concerts, festivals)

**Background Tasks:**
- `server/services_radio/background_tasks_service.py` - Background tasks (scheduled jobs, cleanup, maintenance)
- `server/services_radio/stripe_service.py` - Payment processing (subscriptions, billing)

## Database

**Engine:** PostgreSQL 18 (see Environment & Infrastructure for the 4 databases)
**ORM:** SQLAlchemy (async, `asyncpg`) for the `ai_radio` models; catalog, user content and embeddings services use `psycopg2` directly
**Connection:** `server/database/connection.py`
**Models:** `server/database/models.py`
**Schema changes:** add the column to `models.py`, then register it in `server/run_migration.py`

Get database session:
```python
from database import get_db

async def my_endpoint(db: AsyncSession = Depends(get_db)):
    # Use db session
```

## Playback Modes

### Seed Modes (Based on Current Track)
- `primary_genre` - Same primary genre
- `secondary_genres` - Similar sub-genres
- `mood` - Similar mood/vibe
- `primary_artist` - More from same artist
- `similar_artists` - Similar artists
- `style` - Production style
- `theme` - Lyrical themes
- `lyrics` - Similar lyrics
- `vocal` - Vocal style
- `all` - Balanced mix (All Categories)

### Playlist Modes (Station/User-Based, No Seed Track Required)
- `favorites` - User's liked tracks on shuffle
- `discovery` - 50/50 favorites + new similar tracks
- `top_hits_all` - All-time most popular tracks from station analytics
- `top_hits_week` - Past 7 days' most popular tracks
- `top_hits_day` - Past 24 hours' most popular tracks

**IMPORTANT:** Use normalized names "favorites" and "discovery" (NOT "my_favorites" or "smart_discovery") for consistency between backend `PlaybackState.radio_mode` and frontend `UIStateContext.radioState.activeSeedMode`.

**Adding New Playlist Modes:**
1. Add to `_auto_fill_queue()` in `playback_state.py` - logic for filling queue
2. Add to `seed_radio()` method's playlist check (line 626) - ensures it doesn't require a seed track
3. Add to `DynamicThemeContext` CATEGORY_IDENTITY - icon, color, label
4. Add to `getCategoryMetadata()` - returns metadata for the mode

## Common Pitfalls

### ❌ DON'T
- Use raw `localStorage` directly - ALWAYS use `safeStorage` from `lib/safeStorage.js` (iOS private browsing crashes without it)
- Hardcode `sampleRate` in AudioContext constructor (let browser choose — iOS Safari may reject non-native rates)
- Use `100vh` for full-height layouts (use `100dvh` with `100%` fallback — iOS address bar causes layout shift)
- Duplicate gain scheduling logic (use `audioEngine._rampGain()` for all gain automation)
- Use direct `fetch()` for backend calls - ALWAYS use `api.js` methods (enables offline/online routing)
- Add code comments/notes (no `// ####`, `// TODO`, `// Note:` etc.) - keep code clean
- Store `activeSeedMode` in PlaybackContext (use UIState only)
- Store `isActiveDevice` in local component state (use UIState only)
- Auto-activate playback just because state was received (check `active_device_id` first)
- Prop-drill device status (use UIState's `engineState.isActiveDevice`)
- Use `socket.on()` for WebSocket events (use `useWebSocketSubscribe()`)
- Mix up "my_favorites"/"favorites" naming (always use "favorites")
- Let backend auto-advance tracks when `active_device_id` is set
- Make Radio.jsx a "bridge" component (it's just a panel container)
- Pass viewport state through props (components subscribe directly)
- Pass artwork URLs through component props (components subscribe directly)
- Manually call `preloadArtwork()` or `preloadEnrichedArtwork()` in components
- Use `useIsMobile()` helper (removed - use `useViewport()` directly)
- Hard-code GPU indexes (`cuda:1`, `set_device(n)`) or float16 for Whisper/CTranslate2 — the app runs on a Pascal P6000 selected via `CUDA_VISIBLE_DEVICES`
- Reintroduce cloud TTS (ElevenLabs was retired), or replace the phonetic paralanguage converter with engine emotion tags
- Compute new row ids with `SELECT MAX(rowid)+1` — let Postgres identity columns assign them (the catalog loads with 50 concurrent upserts)
- Add multiple timeupdate listeners to audio elements
- Forget to broadcast state after changing `radio_mode`
- Forget to set `active_device_id` when user activates a device
- Create custom modal wrappers (use Modal.jsx)
- Duplicate FFT processing logic (use `useFFTProcessor` hook for all audio engines)
- Duplicate shader effect logic (use shared functions from AudioReactiveCanvas)

### ✅ DO
- Use `api.js` for ALL backend calls (`api.methodName()`) - routes through `_routeRequest()` for offline fallback
- Use UIState as SSOT for all UI state (including `isActiveDevice`)
- Use ViewportContext as SSOT for all viewport/responsive state
- Check `active_device_id` matches `deviceId` before auto-activating playback
- Report device status to UIStateContext via `reportEngineStatus({ isActiveDevice })`
- Allow inactive devices to display queue/state (UI sync) but not play audio
- Trust UIState to automatically preload current + next track artwork
- Engines report directly to UIState via `reportEngineStatus()` (including queue + currentTrack + isActiveDevice)
- Components subscribe to contexts directly - NO prop drilling
- Use `useArtwork()` and `useEnrichedArtwork()` hooks to consume preloaded URLs
- Use optimistic updates for user actions
- Broadcast playback state changes via WebSocket (including `active_device_id`)
- Normalize mode names between backend and frontend
- Check `active_device_id` before backend auto-advance
- Remove old timeupdate listener when swapping audio slots
- Use granular breakpoint flags (isXL, minLG, etc.) instead of binary isMobile
- Use Modal.jsx for all modals (automatic artwork backgrounds + gradients)
- Import shader effect functions from AudioReactiveCanvas for any new video/rendering features

## Performance Optimizations

### Frontend
- FFT processing uses early-exit pattern (RAF only runs when audio is active: DJ speaking, mic recording, shoutout playing)
- Unified `useFFTProcessor` hook consolidates FFT logic (DRY - single source of truth for all three engines)
- Zero-allocation audio processing using for loops instead of array methods
- Refs used for RAF loops (FAST LANE), setState only when React needs to re-render
- PlaybackContext guards against unnecessary re-renders on every tick
- Artwork proactively preloaded for current + next track to prevent crossfade stutter
- mediaCache uses in-memory Map + IndexedDB + Cache API for multi-layer artwork caching

### Backend
- Service instances initialized once at startup (`@asynccontextmanager`)
- Vector search uses Annoy index for fast similarity lookups
- Preferences cached in-memory (`user_data_cache_service.py`)
- Rate limiting per user (`rate_limit_service.py`)

## Windows-Specific Notes

This project runs on Windows with specific configurations: Backend uses `WindowsProactorEventLoopPolicy`, disables Quick Edit Mode (`start.py`), Python venv at `E:/AI_RADIO/.venv/`. Start PLAiR with `external_components/plair_start.bat` (see Environment & Infrastructure).

**CRITICAL:** When editing files with Claude Code, **ALWAYS use relative paths** (e.g., `client/src/App.jsx`) instead of absolute paths (e.g., `E:/AI_RADIO/client/src/App.jsx`). Absolute paths with drive letters cause "file has been unexpectedly modified" errors. Working directory is `E:/AI_RADIO`.

## iOS Safari Compatibility

The app supports iOS Safari with graceful degradation. Key patterns:

- **Storage:** All localStorage goes through `safeStorage.js` (in-memory fallback for private browsing). IndexedDB (`offlineStorage.js`) has `unavailable` flag — returns safe defaults when IndexedDB is blocked. Cache API (`mediaCache.js`) falls back to network-only when `caches.open()` fails. Download space is half the browser quota (max 2 GB, 500 MB fallback on iOS) in `cacheManager.js`.
- **Audio unlock (iOS lets each media element start only from a tap):**
  - `AudioEngine` creates its `<audio>` elements in the constructor; `useAudio` creates the engine eagerly. Every element (engine slots A/B, sfx, DJ voice, shoutout) is registered with `AudioInteractionManager.registerMediaElement`.
  - On every trusted `pointerup`/`touchend`/`click`/`keydown` (capture phase, synchronously inside the gesture), the manager does three things:
    - resumes AudioContexts;
    - runs gesture hooks: PlaybackContext calls `engine.unlockFromGesture({ resumeCurrent })`, which creates or resumes the context, starts the current track if it should be playing, and play/pauses loaded slots silently;
    - plays a tiny silent WAV on idle elements.
  - A rejected `play()` (`NotAllowedError`), or a context that isn't running, sets `engineState.audioNeedsTap` and shows `AudioUnlockPrompt` ("Tap to start audio"). `togglePlay` never pauses while blocked. `ensureContext()` waits at most 300 ms for `resume()`, since WebKit leaves it pending until a tap. DJ voice lines blocked by autoplay wait for the next tap instead of being dropped.
- **Claim-on-open** only claims after real user activation (`navigator.userActivation.hasBeenActive`); otherwise the claim waits for the first tap (within 2 min).
- **Streaming on iPhone:** `lib/mediaSupport.js` detects MSE + WebM/Opus. Without it (iPhone), the engine plays `/api/stream/{id}` (MP3) as a progressive `<audio src>`, and offline downloads are saved as MP3 (`downloadFormat()`). Downloads the device can't play are ignored (`canPlayCachedBlob`). DJ voice still needs MSE/ManagedMediaSource with WebM/Opus; if unsupported, a one-time notice explains it.
- **Media Session / outside pauses:** separate `play`/`pause`/`seekto` handlers (`resumePlayback`/`pausePlayback`), `playbackState`, `setPositionState` and https artwork. Pauses the engine didn't cause (calls, other apps, headphones) and a context `interrupted` state go through `engine.onExternalPause` → `pauseFromOutside`, which updates UI + server. `navigator.audioSession.type = 'playback'` is set where supported; the voice recorder switches it to `play-and-record` only while capturing and back to `playback` as soon as the mic tracks stop (released on stop, not after `MediaRecorder.onstop`), so Bluetooth headsets return from hands-free to A2DP.
- **Motion permission** is only requested from Settings → Tilt Effects (never on the first tap).
- `pagehide`/`pageshow` listeners supplement `visibilitychange` for reliable tab lifecycle.
- **Layout:** Body uses `height: 100dvh` (dynamic viewport height) to handle iOS address bar. `position: fixed` elements work correctly because no parent transforms interfere.
- **Compatibility:** `ViewportContext.isCompatible` is always `true` — iOS is a supported platform. Device detection (`isIOS`, `isSafari`) is available for feature-specific behavior.

## Client Error Reporting & Logs

- `lib/errorReporter.js` (installed first in `main.jsx`) keeps the last 200 logger lines as breadcrumbs. It reports these to `POST /api/client-log`:
  - `logger.error`, uncaught errors and unhandled rejections;
  - `audio_blocked`, `ws_close` and `server_unreachable`;
  - React render crashes (`AppErrorBoundary`).
- Each report carries the build id, a per-page session id, the first 8 characters of the device id, the user agent, the standalone flag and the audio capabilities.
- Limits: 20 reports per session, deduplicated, batched, and sent with `sendBeacon` on hide. Tokens and emails are scrubbed client-side, and again server-side along with coordinates.
- Server: `routers/client_log.py` rate-limits the endpoint, caps bodies at 32 KB, validates them with a strict schema, and writes JSONL to `data/logs/client.jsonl` (20 MB × 10). Errors are also echoed to `radio.log` as `[ClientLog]` warnings.
- `radio.log` rotation is `LOG_FILE_MAX_MB` × `LOG_FILE_BACKUPS` (20 × 10). Categories can be switched on and off with `LOG_CATEGORIES_ON` / `LOG_CATEGORIES_OFF`. WebSocket connects and disconnects are logged in the `playback` category, with the auth mode and the connection duration.
- A stale lazy chunk after a deploy (`vite:preloadError`) reloads the page once (60 s guard); other render crashes show a "Reload PLAiR" screen.

## Debugging Tips

**Frontend:** Check console for `[PlaybackContext]`, `[AudioEngine]`, `[UIState]` logs. Verify `engineState.queue`/`currentTrack` in UIState. Artwork URLs should be blob URLs when cached.

**Backend:** Check for `[PLAYBACK]`, `[LISTENER]`, `[TTS_QUEUE_MANAGER]` ("DJ voice for ...") logs. Verify `broadcast_playback_state_to_session` calls and `PlaybackState.radio_mode`.

**Backend logging (activity feed):** the console and `data/logs/radio.log` show one line per meaningful event, naming the listener via `log_service.who(session_id, device_id)` ("ben (user 1), device 92e5fce6" / "guest_38eae617") and tracks via `log_service.track_label(track)`. Per-segment / per-lookup / per-step detail goes through `log_service.detail(msg, category)`, which only prints when the category is listed in `LOG_VERBOSE` (`.env`, e.g. `tts_vector_db,playback` or `all`; `access` re-enables routine uvicorn access lines). Repeating warnings use `log_service.throttled(key, msg)`. A DJ turn logs a start line and one summary line (`TTSQueueManager`); never add per-chunk or per-segment info lines.
