# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What PLAiR is

**PLAiR = Personalized Localized Adaptive Interactive Radio.** A geo-social radio station: every listener gets their own station with two AI hosts, and it knows where they are and what's around them. Each letter is a design rule:

- **Personalized:** one station per listener, shaped by their likes, bans, skips and what they tell the hosts.
- **Localized:** it knows where you are and what's on near you: gigs, places, news, weather and other listeners' shoutouts (City Pulse, section 15). Geo-social means people in a walkable city finding where to hang out and finding their friends nearby, many of them without a car.
- **Adaptive:** the music follows what you're doing and the time of day. The goal is DJs with the authority to change the music themselves from your activity (`docs/DJ_AUTONOMY.md`).
- **Interactive:** listeners talk to the hosts by voice or text and the hosts act through tools (section 5); listeners talk to each other through shoutouts, replies and reviews that go on air (section 17).
- **Radio:** it runs like a real station: hosts, between-track talk, stings, time checks, talk breaks.

**Location is the point.** After one opt-in the station uses the listener's exact position and movement. Other listeners only ever see an area: the suburb or neighbourhood ("Eden Terrace") or a distance (about 500 m, close enough to meet on foot), never a home or exact address. The code rules that keep coordinates out of public items (`public_shoutout()`, no listener coordinates in a `Where`) hold that line; they don't mean the station should know less.

**Independent by design:** the first PLAiR ran on Spotify's APIs and died when Spotify closed them in Nov 2024, so everything is in-house now: catalog (AI and human uploads), semantic search, audio pipeline, voices. The story is in `PROJECT_OVERVIEW.md`.

## Project Overview

Technically, PLAiR is a full-stack music streaming application with an AI DJ that controls playback, manages playlists, and interacts with users via voice/text. The system features real-time audio processing, WebSocket-based state synchronization, and advanced audio engine capabilities including dual-buffer crossfading.

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
- **Vector stores:** every vector engine follows one pattern (section 19). Only the TTS clip tables keep Annoy files (`data/embeddings/<table>_1.ann` / `_2.ann`, item id = `id - 1`); they are checked against the database at startup and rebuilt if stale, so deleting them is always safe.
- **Shared machine:** the same nginx also serves other sites (lifespan.ink, deepmirror.live, moneyprinter.live, realityvirtual.co), and the owner runs other GPU/AI projects on this box. Never kill python/node/nginx/java by image name: other projects use them. Don't leave PLAiR running for no reason; start it for testing, stop it after. nginx runs elevated: `nginx -s reload` needs an **admin** shell.
- **Starting PLAiR:** `external_components/plair_start.bat` is the one start script (the owner's "PLAiR Start" desktop shortcut points here): it stops any older PLAiR backend first (`plair_stop.ps1`, by command line, window included) and refuses to start a second one, rebuilds the frontend, starts/reloads nginx gracefully, launches the backend window and runs the smoke test. The old `restart_all.bat` was removed on 2026-09-30.
- **Running production:** the backend runs in a console window titled `Plair Backend (Port 8000)` (`cmd /k ... start.py`) bound to `127.0.0.1:8000` (`HOST` in `.env`); nginx proxies `plair.live` → it and serves `client/dist` directly. Stop only PLAiR with `powershell -File external_components/plair_stop.ps1` (finds the backend by its command line, so it also catches windows titled "Administrator: ..." and closes the window, not just Python), then relaunch it with PLAiR Start. **Deploying is the agent's job:** when a batch of work is done, committed and pushed, launch the owner's own shortcut so the window appears on their desktop exactly as if they had pressed it: `explorer.exe "C:\Users\info\Desktop\PLAiR Start.lnk"` (it stops the old backend, rebuilds the frontend, reloads nginx, starts the backend window and runs the smoke test), then check `data/logs/radio.log` and the smoke test result. Never start the backend any other way (`Start-Process`, `cmd /c start`, a background shell): those windows are invisible on the owner's desktop and lead to duplicates. Deploy the frontend with `npm run build` (no nginx reload needed). For private test boots next to a running production backend: copy `data/embeddings` somewhere and run `EMBEDDINGS_DIR=<copy> TTS_SERVER_EXTERNAL=true HOST=127.0.0.1 PORT=8011 python start.py` (two backends must not share the Annoy files; production has them open). Launch it with a background shell of its own, not `&` inside another command.
- **Offline quality gate:** `powershell -File scripts/check-quality.ps1 [-SkipBuild]` (skill `offline-audit`): Python syntax, Ruff F (undefined/unused names), Vulture, ESLint (zero warnings; hook deps and `console.*` are errors, use `logger`), `check:ui-state` (selectors that read UIState fields that don't exist, engine keys `reportEngineStatus` drops), `check:motion` (no CSS transition classes on framer-animated elements), Knip (dead files/exports/deps), build. Run it after a batch of edits; it needs no server. Dev tools: `server/requirements-dev.txt`, Knip in `client`.
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
- Components read from UIState via **slice subscriptions**: `useUISelector(state => ({ a: state.a, isPlaying: state.engineState.is_playing }))` (shallow-compared, re-renders only when the selected slice changes). Select the narrowest values you need (e.g. `state.engineState.currentTrack?.id`, not all of `engineState`). There is no whole-state hook (`useUIState` was removed); `npm run check:ui-state` fails on a selector that reads a field UIState doesn't have, and on one that selects a whole state object (`engineState`, `audioState`, `interfaceState`, `settingsState`, ...) instead of the fields it reads: a whole object re-renders the component on every change to any field in it. `useUIStateGetter()` reads the latest state on demand inside handlers without subscribing.
- Playback: components that only call actions use `usePlaybackActions()` (stable: playTrack, next, previous, seek, audio, talkBreak...); `usePlaybackConnected()` for the socket flag. Playback state reaches components through UIState (`engineState`), not a whole-context hook.
- Theme: `useDynamicTheme()` changes once per track (colours). `getCategoryMetadata` is also a plain export; the scene artwork is `useThemeArtwork()` (the current track's pack, section 8); the accent colour is also published as CSS vars `--theme-accent-85` / `--theme-accent-60`.
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

**File:** `server/services/playback_state.py` (core: play, commands, transitions, state), with its mixins `playback_state_devices.py` (devices, claims, transfers), `playback_state_queue.py` (queue rules and fills) and `playback_state_stations.py` (switching lists and stations)

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

**Stations survive restarts:** each session's station (mode, seed song, queue, current track, position, playing/paused, history up to `QUEUE_HISTORY_SONGS`, auto-filled ids) is saved to `playback_snapshots` (ai_radio DB, `services/playback_snapshots.py`) every `PLAYBACK_SNAPSHOT_INTERVAL_S` (15 s) when it changed, and on shutdown. A new session (after a restart or the 2 h idle eviction) restores it first (`PlaybackService._restore_session`, logged "station restored"); only a listener with no saved station starts on the all-time top hits (`top_hits_all`). Rows unused for `PLAYBACK_SNAPSHOT_KEEP_DAYS` (30) are pruned at boot; account deletion removes the row.

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
- Setting: "Auto-switch playback to the device I open" (`settingsState.autoClaimOnOpen`, a device-kind setting, default ON; see `docs/SETTINGS.md`) in User → Audio & Devices.
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

**Files:** `server/services_radio/dj_command_executor.py` (core: track ids, labels, segment scheduling) with its parts `dj_command_executor_search.py` (search, name lookup, loved tracks, queueing), `dj_command_executor_playback.py` (transport, seed radio and blends, playlists, devices, Radio Mode settings), `dj_command_executor_community.py` (ratings, saving posts) and `dj_command_executor_segments.py` (news, weather, events, places, bios, lyrics, shoutouts); the tools are `dj_tools_registry.py` (the registry and everything built from it), `dj_tools_args.py` (argument checks), `dj_tools_display.py` (command strings, activity chips) and `dj_tools.py` (the turn runtime and `authorize_tool_call`)

Executes the actions behind the DJ tools (`execute_*` methods: searches, playback, seed, playlists, ratings, segments, saves). Search categories map 1:1 to vector search categories; the brace syntax below is how executed actions are displayed and stored in history.

**One DJ turn:** `conversation_service._process_tool_turn` → `dj_prompt_service.gpt_dj_interactive_tools` → `ai_service.run_gemini_tool_turn`.
- **One brain: tools only.** HAL11000 (the second "command extraction" prompt) was retired on 2026-09-29; every action the DJ takes is a tool call. Nothing happens unless a tool is called.
- **Routing lives in the dynamic prompt system.** The Producer AI (`context_router_service.determine_route`) picks the nodes and returns `needs_tools` + a `tool_plan` (numbered function-call steps; free-text args are always normalised to `<placeholders>` and `when`/`near_me` args dropped by `clean_plan`). Cache: exact text, then mpnet similarity (`GEMINI_NODE_PRODUCER_SIMILARITY_THRESHOLD`); a similar hit re-derives the pulse `when`/`near_me` from the listener's own words (`stated_pulse`), since "rain tonight" and "rain tomorrow" embed at 0.96. Cached rows carry a hash of the Producer prompt and are dropped when the prompt changes. The plan is a hint, not a gate: it decides which tools are declared (`dj_tools_registry.declarations_for`: `CORE_TOOLS` pulse_search/pulse_detail/search_and_play/find_tracks/playback_control/rate_track always, plus the planned tools and their companions) and renders as the `tool_guidance` PRODUCER NOTE. `city_pulse` adds what the station knows that fits the message (and records `route.pulse_found`).
- **Tool registry (LifeSpan pattern):** `dj_tools_registry.TOOL_REGISTRY` is the one table: per tool a name, cost (memory / live / segment), requires, a one-line summary, a full description (what it does, when it's the right one, typical pattern) and described parameters. The DJ's declarations (`DJ_FUNCTION_DECLARATIONS`, with Cost/Requires appended), the Producer's catalog (`tool_catalog()`) and the `request_tools` menu are all built from it; per-tool usage never goes in prompt prose.
- **Model:** DJ turns run on their own chain `LLM_DJ` (gemini-2.5-flash, then 3.5-flash-lite) with thinking on for the round that decides the tools (`DJ_TOOL_THINKING_BUDGET=-1`, dynamic) and off once a tool result is back (`DJ_TOOL_FOLLOWUP_THINKING_BUDGET=0`). Measured 2026-09-30: with thinking in the later rounds Gemini re-planned the turn it had just taken and re-emitted its earlier line and tool call (a song request restarted the track four times); thinking for the first round only, plus the `[STUDIO]` marker appended to each tool result ("your line has aired and these calls have run"), took repeats from 7/10 to 0/12, with follow-up calls and double skips still working. After a line plus a scheduled segment the studio message is the hand-off one (`ai_service.HANDED_OFF_NOTE`, `handoff_tools=SEGMENT_TOOLS`): nothing more goes on air, notes and `[TASK]` only (the plain "reply on air now" message made the hosts speak a second hand-off line in 11 of 12 replays; 0 of 12 with it). gemini-3.5-flash-lite refuses a thinking budget of 0 (400 INVALID_ARGUMENT), so `llm_router.fit_thinking` raises it to that model's floor (`GEMINI_THINKING_BUDGET_FLOOR`, 1 = no thinking tokens); without it the DJ's fallback model failed every follow-up round. The loop also drops a call identical to one already made in the turn (`playback_control` excepted unless the echoed line comes with it) as a logged safety net ("the model echoed its earlier turn"). Measured 2026-09-29: without thinking the model narrated tool calls instead of making them ("<<CALL:...>>", "I will call play_shoutouts"); 3.5-flash(-lite) with thinking returned MALFORMED_RESPONSE ~1 in 4 turns; 2.5-flash with thinking was 0/12 failures at the same speed and price. The Producer stays on `LLM_LIVE` (flash-lite, falls back to 3.5-flash on an empty reply).
- **Prompt layout and caching:** every context node carries a role (`node_registry.register(..., role="system"|"live")`, default live). The DJ's system prompt is only the `system` nodes of its config, in config order, plus all 23 tool declarations, so it is identical for every listener and turn; every `live` node (track, queue, pulse, weather, profile, conversation, tool hint, `format_performance_tag_examples` with fresh tag picks) goes in the user message before `[LISTENER TXT]`. That fixed prefix is held in an explicit Gemini cache (`services/gemini_cache.py` `system_caches`, one per agent label + model + prompt/tools hash, `GEMINI_CACHE_TTL_S` 30 min renewed while in use, `GEMINI_CACHE_RETRY_S` back-off; `gemini_generate_chain(cache_label=...)`, inline fallback if Gemini drops it). Requests that set `tool_config` can't use a cache and run inline. Measured 2026-09-29: DJ calls ~80% cached, $0.0015/call (was 16% / $0.0025). Other agents (announcer, For You, segments) can pass their own `cache_label`.
- **Tool turn:** 23 tools in `server/services_radio/dj_tools_registry.py`, run by `dj_tools.DJToolRuntime`. Read tools return data to the model: `pulse_search`, `pulse_detail`, `listener_context`, `city_trends`, `what_aired` (City Pulse, `docs/CITY_PULSE.md` sections 15-16; store first, live fetch only on a miss, at most `DJ_TOOL_MAX_LIVE_FETCHES` per turn, results saved for everyone). Action tools (search_and_play, playback_control, seed_radio, play_playlist, rate_track, move_playback, radio_settings, save_*) dispatch to the structured `execute_*` methods on `dj_command_executor`; segment tools (get_news, get_weather, get_events, find_places, get_artist_biography, explain_lyrics, play_shoutouts) schedule the produced interpretation segment, released after the reply. Empty replies get a finish-reason-aware corrective message, at most `LLM_RECOVERY_MAX_STRIKES` (2). The loop runs on the `LLM_LIVE` chain with the circuit breaker (`llm_router.gemini_generate_chain`). Talking while tools run: a line the model writes alongside a call airs at once; a round with no line only tells the interlude monitor what the hosts are doing (`dj_tools_display.tool_activity`, see "Impulse and interlude"). After a line that already covered a plain action, an empty final reply is accepted.
- **Transparency (front end):** every DJ turn streams ordered `dj_activity` events over the WebSocket: `turn` (the listener's words), the Producer's plan (`source: producer`), `say` for each on-air line as it airs, each tool call (`start` with label + brace-style `command`, then `result` with outcome found/done/empty/failed/blocked and a short summary such as "3 gigs · 1 place"), then `done`. `Conversation.jsx` renders them in order between the DJ bubbles (`ActivityCard`: expanded while running, tap to show the command) and skips the final `conversation_update` for a streamed `turn_id`; `DJActivity.jsx` also pops compact chips at the top of the screen that fade. History rows keep actions under `[STUDIO TOOLS]` (older rows may say `[HAL11000]`).
- **Talk or Type:** `settingsState.radioInput` ('voice' | 'text', a device-kind setting, `docs/SETTINGS.md`). Tapping the active Radio tab toggles it like Catalog/Shoutouts (both tabs always show the current mode); in text mode the tab shows `TextRadioIcon` labelled "Text" (desktop: the same icon as a toggle in the Radio header). Text mode hides the radio bubble (opacity 0, shader included) and shows `DJTextComposer` above the tab bar, which posts `api.djTalk({text})` (the server's `handle_text_interaction`).
- **Length is the hosts' call (`services_radio/talk_clock.py`):** no per-prompt length numbers. Every interactive turn carries a `studio_clock` live node (what's on air, time left, when vocals come in, next track's intro, any talk break lined up); how to size the reply to it is stated once, in `guidelines_general` (system prompt). Every segment tool takes `depth` (brief / standard / detailed, injected for all `SEGMENT_TOOLS`); it rides a context variable into the scheduled segment, where the `segment_length` node renders `SEGMENT_DEPTHS` seconds as a word ceiling. One speaking pace for the whole station: `TALK_WORDS_PER_SECOND` (measured 2.0 on air), read only through `talk_clock.pace()` / `talk_clock.words_for(seconds)` by these segments, Radio Mode talk breaks and the announcer's time constraint; never hard-code a words-per-second figure, and never give the hosts a time without its word count: every prompt that names seconds (announcer window, Radio Mode and DJ segments, a song's intro in the studio clock and in play-tool results) pairs it with `words_for(seconds, kind)`. The pace measures itself (`talk_clock.meter`): each finished chat, announcer or segment stream adds one sample (script words / aired seconds) in `TTSQueueManager`, smoothed per kind (`chat`, `announcer`, `segment`), saved in `talk_pace`; the setting is the starting value and fallback. Tools that start a song (`PLAY_TOOLS`) return `vocals_start_s` from the lyric timing. The between-track announcer is sized by its music window alone (its TIME CONSTRAINT); there is no fixed word cap anywhere. `get_weather` takes a list of periods.
- **Parity with the app (`docs/HANDOVER_DJ_TOOL_PARITY.md`):** a tool calls the same service the app's route calls, never its own copy. `playback_control` also restarts, seeks and removes an upcoming track; `seed_radio` takes a `target` track; `rate_track` rates tracks and other listeners' posts (target `shoutout`, default the one that just aired) through `preferences_service`; `pulse_search` takes `mine` (the listener's own posts) and `about_track` (reviews of one track), and `pulse_detail` lists a shoutout's replies; `move_playback` moves the music to another online device by name; `radio_settings` reads or patches Radio Mode and music source (`preferences_service.apply_radio_settings`, the same call as `PUT /api/radio-mode`; guests get a `radio_mode_updated` patch their client stores). Guests are refused favorites / discovery as in the app. Deleting posts, clearing the conversation, sound and quality settings and song generation stay in the app.
- **Listener timeline (`docs/LISTENER_TIMELINE.md`):** what aired for a listener is read on demand, never put in the prompt. `services/listener_timeline.timeline()` reads `play_events` (tracks and the shoutouts, replies and reviews that aired, for users and guests) plus the unwritten analytics buffer; the hosts reach it with `what_aired` when the listener points back at something, then pass the id on (`track_id` on `rate_track`, `seed_radio`, `save_review`, `explain_lyrics`; `shoutout_id` / `parent_id` for posts). With no id and more than one candidate post a tool says so; it never guesses. Everything the hosts voice (segments, chat replies, between-track talk, Radio Mode breaks when they go on air) is written as plain text to `aired_talk` by `listener_timeline.record_talk` and shows as `segment` / `talk` entries; `what_aired(id=...)` returns the full text of one. Time is always a window the hosts choose: `around_minutes_ago` (one rough moment, the studio looks either side) or `from_minutes_ago` / `to_minutes_ago`; lists are capped at 25 short entries, never full transcripts. Add new voiced stream types to `SEGMENT_STREAMS` / `TALK_STREAMS` there or they are not recorded. The listener sees the same read in the app: the Timeline toggle in the Radio header (`ListenerTimeline.jsx`, `GET /api/timeline`, `GET /api/timeline/entry?id=`) swaps the conversation for the last 24 hours of what aired.
- **Self-directed, cost-aware:** every tool description ends with its cost (`dj_tools_registry.TOOL_COSTS`: memory / may go online / full segment) and every result says where it came from (`came_from`); a result that comes up short lists `could_try_next` tools (auto-granted for the next step). The hosts judge quick answer vs full segment themselves (no keyword rules), review each result against the ask, and sign off with a `[TASK]` section after `[INTERNAL DIALOGUE]` (a valid tag in `clean_gpt_output`, stripped for other roles, never spoken, shown as a Review card, logged on the `DJ turn |` line). `[TASK]` is the hosts' own review: did they do what they told the listener they'd do. Partial means they carry on: one more step (`DJPromptService._review_step`, `run_gemini_tool_turn(review=...)`); a missing sign-off gets the same review. Context stays lean LifeSpan-style: every tool takes an optional `_done_with` {tool: what I took from it}, and `run_gemini_tool_turn` replaces those earlier results with that one-liner before the next round.
- **The DJs decide their own tool calls.** There are no word-list gates on what the listener said (they were removed on 2026-09-30: a plural "songs" once blocked a review the DJs chose to save). `dj_tools.authorize_tool_call` only enforces plain limits: actions only in reply to the listener's own message, a per-turn call cap, saves and ratings need a signed-in account, one save per turn, and each segment once per turn. Whether something is worth keeping is for the DJs and the review/shoutout pipeline to judge, never keywords.
- **Track search = the app's search, for the DJs:** Who sings is a hard filter, not a weight. Every track carries `derived_tags.vocals` (instrumental / male / female / duet / unknown; read it with `services/catalog_vocals.vocals_of`, never from `instrumental`/`vocal_gender` directly): set by the AI enrichment and the upload analysis (the Suno/upload settings win when they settle it), kept right on lyric edits, flagged and repaired by the Asset Doctor and `utils/catalog_audit.py`; backfilled 2 Oct by `utils/backfill_vocals.py`. The search brain (`QueryIntentAnalysis.vocals`, cached in `query_intent_cache.filters_json`, older rows re-asked once) sets the filter from the words ('no lyrics', 'a female singer', 'a boy-girl duet') for the app's search box and the DJs' AI searches alike, and `find_tracks` / `search_and_play` / `POST /api/search` take `vocals` for searches pinned to one category; all feed `CatalogVectorSearchService.search(vocals=)`. The DJ's track context shows it (`track_vocal_info`). `find_tracks` (read) returns candidates (title, artist, genre, who sings, a line on the sound, track id) without playing; `starts_with` filters the whole catalog by how the artist name (or, with `song_title`, the title) starts. Its description tells the hosts to work out likely real names from their own knowledge and look those up next to the clues, then play their pick with `search_and_play(track_id=...)`, never asking the listener to confirm. Tested 1 Oct with `tests/dj_find_test.py` (whole turns, real search, playback faked) and `tests/track_search_probe.py` (search ranking only). `search_and_play` has the same two modes as the front-end search box. The search box shows results while you type and fills the queue only on Enter (the AI search) or a voice search (`POST /api/search/semantic` with `queue`: the results are queued to play next and the first plays); otherwise the listener taps a result to play it. Default (no `category`): the AI search, `vector_search_service.search(use_ai_analysis=True)`, which works out the category weights from the listener's words and skips the track already playing. With a `category`: the old-school single-field search; `primary_artist` / `song_title` look the name up by spelling, and when nothing is spelled exactly like it the result says so (`not_spelled_exactly`) with the closest names and their spelling scores, and the hosts judge whether the listener meant one of them. Both take `within`: `catalog` (default), `favorites` (likes + super likes) or `super_likes` (`listener_filters.scope_ids` -> `only_ids`; signed-in only); with `within` set and no query, `search_and_play` plays a weighted shuffle of those tracks and `find_tracks` lists them, most loved first. `pulse_search` takes `within` too and uses the AI weighting for track-only lookups. Don't build a separate search for the DJs.
- **One statement per rule:** the DJ prompt states each rule once, in one node (length in `guidelines_general`, tool use in `instruction_dj_tools`, tags in `format_performance_tags_guide`), and the Producer only chooses live nodes (system nodes come from the config). The `[ON AIR JUST NOW]` note frames impulse and interlude lines as the hosts thinking out loud: nothing to correct or apologise for. When adding a rule, change the node that owns it instead of restating it elsewhere.
- **One vocabulary (2 Oct, `docs/HANDOVER_2026-10-02_DJ_TOOLS.md`):** the DJ tools use the app's and the database's names: `favorites` (likes + super likes), ratings `like | super_like | clear | ban` (`clear` = the app's DELETE), `category` for the aspect of a track (search and `seed_radio` alike, as in `POST /api/queue/seed`), `vocal` = how the singing sounds, `vocals` = who sings. A schema's required fields must match what `normalize_tool_args` demands; a call it rejects returns `status: invalid_call` with `accepts` (the tool's arguments), never a result that reads like "nothing found".
- **Listening history weights favorites (`services/listener_plays.py`):** per listener and track, from `play_events` plus the unwritten buffer: plays (starts), listens (ended at ≥ 50 %), skips (cut short) and last played. `love()` = rating (super_like 3, like 1) × √(1 + listens) × (1 + listens) / (1 + listens + skips); `weighted_pick` samples by it. Used by the favorites/discovery playlist fill, `search_and_play` / `find_tracks` within favorites or super likes, the `yours` field on every `find_tracks` candidate, and `listener_context` (`most_loved`, `most_played`, like counts). Raw play starts are not a signal: most are quick manual skips.
- **Queue (4 Oct, `playback_state.py`):** `QUEUE_PLAYED_SONGS` (5) kept behind the current song, `QUEUE_AHEAD_SONGS` (15) ahead, `QUEUE_HISTORY_SONGS` (50) for Previous, `QUEUE_NO_REPEAT_SONGS` (20) the fill skips. The songs ahead never pass `QUEUE_AHEAD_SONGS`: trimming drops station fill (`_auto_filled_track_ids`) from the end first, then the picks (anything the listener or the DJs asked for) furthest away; a restored station is trimmed the same way (6 Oct: picks used to be never cut, and a search that queued 50 songs per keystroke took the owner's queue to 503). `add_to_queue` puts a batch after the earlier picks; `play_next=True` (the DJs' and the search box's "play") puts it right after the current song, earlier picks follow, and a song already queued moves up instead of being skipped to. A station switch keeps the songs just played and the upcoming picks: seed keeps the current track then the picks, a playlist starts its first track now then the picks, and the new station fills behind them. The DJs see the queue through `queue_playlist` (required in every interactive turn: `DJ_PLAYLIST_VIEW_SONGS` played, now, and ahead) and the short `history_last_track` / `queue_next_track` nodes. Test: `tests/queue_rules_test.py` (no server, no LLM).
- **Three ways into the catalog (owner, 4 Oct), one module each:** (1) **lists**, the listener's own and the charts: favorites, discovery, top hits (`services/playback_lists.py`); (2) **name lookup**: artists, bands and song titles by spelling (`services/catalog_names.py`, character n-gram vectors, so typos, spacing, accents and a leading 'the' still find the name and different names never match by meaning; there is no cleanup of names anywhere); (3) **stations built from aspects** (`services/catalog_aspects.py`). `CatalogVectorSearchService` is the front: free-text search (AI or keyword weights) plus `station()` and `closest_names()`.
- **Stations from aspects:** a seed mode is one aspect at full weight (pure: 'alternative rock' plays alternative rock); the DJs' `seed_radio` also takes a `blend` of aspects, each with a weight and optional `words` matched instead of the track ("this track's style 0.7 + mood 0.5 'rainy, slow'"), the same idea as the search box's AI weights. Every refill matches the seed (the song, `PlaybackState.seed_track_id`, or the words; the blend is `seed_blend`; both saved with the station) plus the last `SEED_CONTEXT_SONGS` songs queued, counted equally, so it moves forward gently but can't wander off. Tags are compared one by one (`CatalogVectorDatabaseService.category_tags`: each subgenre, mood word, vocal word, similar artist and lyric section has its own embedding; the sound description is the style aspect, the full lyrical interpretation the theme; nothing is cut short): song to song both ways, words one way (the words must be found in the song). Artists are compared by spelling: artist radio plays the artist, then the seed's similar artists, then the same scene (overlapping similar-artist lists); similar-artists radio the same without the artist; a misspelled artist still makes a station. Stations never look at likes; discovery is the mode that drifts. Single-category searches use the same machinery (`Genre: x` ranks tags, `Artist:` / `Song:` look names up). Free-text search weighs each aspect's average tag vector (`category_vectors`). The catalog is a `CategoryStore` (section 19) whose slots are `CatalogSlot`s (per-aspect matrices, tag matrices, spelling vectors); new and edited tracks (uploads, edits, the catalog reload) are added one by one and are searchable at once, removed ones are hidden at once, and the vector store maintainer folds them into the spare slot. Startup builds slot 1 (about 3 s). Probes (no LLM): `tests/seed_probe.py`, `tests/catalog_ways_probe.py`. Artist/song honesty checks and track labels read every artist field (`log_service.track_artists`: generation_params.artist_name, track_info.artist, derived_tags.inspired_artist).
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

**Crossfade plan (per pair of songs):** the transition analysis (`announcer_service._analyze_transition`, run for every listener on each new current/next pair, not only when the announcer talks) calls `services_radio/crossfade_plan.plan` with both songs' audio features and lyric timing, and the result reaches the client as `crossfade_hint` (a new plan is pushed to the session at once, `_publish_crossfade`; before 1 Oct it only rode along with the next state change, so most fades used the generic one). The overlap follows the outgoing song's own fade-out (`tail`) and the incoming intro's build (`rise`), within `CROSSFADE_MIN_MS`/`CROSSFADE_MAX_MS` (1.5-5 s; no beat-matching, so longer would clash); it shrinks (to `CROSSFADE_FLOOR_MS` at least) so it never runs over the outgoing song's full-level vocals or past the incoming vocals. Leading and trailing silence are skipped. A song with a hard ending plays on and fades only at its end; a building intro needs only a short fade-in. The engine runs the fade-out and fade-in as separate equal-power ramps with their own delays (`_rampGain(..., { delaySec, equalPower })`); without a hint it falls back to a 3 s equal-power fade from the end. Pausing mid-fade takes the outgoing song out too. While a fade of 1 s or more runs, `engineState.crossfadeMs` drives the ON AIR edge glow in the shader, tinted with the incoming song's accent colour (no new UI). Probe: `tests/crossfade_probe.py --pairs N` (plans for random catalog pairs, no server or LLM). Live since 1 Oct 2026; the owner listened on air on 2 Oct and it works well.

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


### 8. Covers: One Pack, One Renderer (owner's rule, 5 Oct)

**Files:** `client/src/components/DepthArt.jsx`, `client/src/lib/depthArtRenderer.js`, `client/src/lib/packImage.js` (+ `packDecodeWorker.js`), `client/src/lib/mediaCache.js`, `client/src/lib/artworkPrefetcher.js`, `server/services/artwork_thumbnail_service.py`

- Every cover and avatar is one packed JPEG: colour on the left; normal x (red), depth (green), normal y (blue) on the right. Sizes 256/512/768 for tiles (`ART_PACK_SIZE`, picked per device) and 1024 (`FULL_PACK_SIZE`, the full artwork) for Now Playing. There is no plain-JPEG path anywhere in the app: no `<img>` covers, no `useArtwork`, no enriched or normal-map downloads. The plain `/api/artwork/{id}` route exists only for link-preview cards (`opengraph_service`).
- Moving covers render coarse (a Keep It Smooth lever, section 14): parallax and lighting run at a quarter of the pixels into half-float targets (landing offset, lighting terms, edge fill) and a full-resolution pass reads the cover colour at the blended landing position (`depthArtRenderer.drawCoarse`, `COARSE_FRAGMENT` / `COMPOSE_FRAGMENT`); 40 -> 50 fps on Now Playing while moving, 31 -> 41 on the Catalog (7 Oct).
- Covers draw through ONE renderer (`depthArtRenderer`) via `TrackArt` / `ProfileArt`; anything that changes track uses `TrackArtCrossfade` (the new cover draws behind, the old one is frozen (`held`, no parallax pass) and fades out once the new one has drawn; 1.5 s limit). 3D Lit Artwork off draws the pack's colour half flat in the same renderer.
- Packs are decoded only in `packDecodeWorker` (`decodePack`): decoding, cutting and resizing an ImageBitmap on the main thread cost ~90 ms per 1024 pack on the owner's phone. Inside the scene worker `decodePack` runs inline.
- Everything else that needs the cover reads the current track's full pack: `useThemeArtwork()` = `{ key, blob, imageUrl, trackId }` (DynamicThemeContext). The background scene gets `key` + `blob`; colours come from a 100 px decode; `imageUrl` (a 512 JPEG made from the pack in idle time) is for the lock-screen art (Media Session), modal blurs and the no-WebGL fallback.
- Preloading: UIState preloads current + next track's packs (tile size and full) and pins them; the catalog scroller prefetches packs ahead (`artworkPrefetcher`) and warms the nearest on the GPU. Packs are rendered ahead on the server (`backfill_track_packs` at startup, all sizes); an unrendered pack takes ~0.9 s on demand, a rendered one ~4 ms.
- Debug: `__plairArt.stats()`, `.views()`, `.drawDelays()`, `.blank()` (each blank cover on screen: no pack yet / decoding / uploading / ready but not drawn).

**DO NOT:** add an `<img>` or plain-JPEG cover, a second renderer or WebGL context for covers, or decode packs on the main thread.

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

**Files:** `lib/sceneShaders.js` (all scene shaders), `lib/sceneEffects.js` (beat/energy effect functions), `lib/sceneRenderer.js` (the live scene), `lib/sceneWorker.js`, `components/AudioReactiveCanvas.jsx` (page side), `offlineVideoRenderer.js` (consumer)

The live background and the offline share-video renderer share the shaders (`sceneShaders.js`) and `createInitialEffects()`, `buildVisualCueMap()`, `calculateFrameEffects()`, `processLyricTimestamps()`, `getLyricAt()`, `renderLyricToCanvas()` (`sceneEffects.js`).

**The scene can render in a Web Worker** (OffscreenCanvas, plain three.js, no React), but runs on the main thread by default (owner, 5 Oct): Chrome on Android holds main-thread frames to 60 Hz unless the screen is touched while a worker canvas runs at the screen's rate (60 vs 120 measured), but on the Adreno 610 the worker scene at ~113 fps took the GPU from the UI (page 60 -> ~52) and felt worse. `localStorage plair_scene_thread=worker` turns the worker on for tests. `SceneRenderer` speaks one message protocol either way (`size`, `state`, `features`, `artwork`, `lyric`, `clips`, `clipFrame`, `click`, `visible`, `bench`, `eval` in; `tick`, `ready`, `renderer`, `context`, `clipIndex`, `clipRate` out); `AudioReactiveCanvas` posts a state snapshot every page frame (panels, radio bubble, playback position, gyro, voice level, quality). Animations that stepped a fixed amount per frame are time-based (`perFrame(rate)` = the 60 fps step), so they look the same at any frame rate. Bench hooks: `__plairScene.set({ skip, force, extra, uniforms, timing })`, `__plairScene.eval(code)` (runs with `scene` = the renderer, in the worker), `__plairScene.thread()`.

**Styled lyrics (6 Oct):** a song can carry a lyric style. One DeepSeek pass (`server/services/lyric_style_service.py`, CLI `server/utils/build_lyric_styles.py --track ID`, about 1 cent a song) groups its timed words into cards that build up word by word as they are sung, each with a layout (stack / flow / cascade / solo), alignment and tilt, and per-word font, size, weight and case from the shared font list `client/src/lib/lyricFonts.json` (25 OFL fonts in `client/public/fonts/lyrics/`, loaded only for a styled song, only the subsets its words need). It is saved in `LYRIC_STYLES_DIR` with a hash of the words, and `/api/lyric-timestamps` attaches it as `style` while the words still match. Every new lyric timing schedules one in the background (`lyric_style_service.schedule_refresh`, called when the timing service saves: new songs, uploads, remasters, asset doctor re-alignments; `LYRIC_STYLE_AUTO`, `LYRIC_STYLE_CONCURRENCY` 2); a re-timing that keeps the same words keeps its style, and `process_backlog.py` waits for pending styles before it exits. Songs timed before 6 Oct are styled on demand, liked songs first: `server/utils/build_lyric_styles.py --missing [--limit N]` (count and cost; `--apply` to spend; 2,147 songs, about $21) or option 10 in `batch_music_generate_and_repair.py`. `lib/lyricStyle.js` lays each card out in the screen's aspect and draws it into the same lyric canvas, so the texture, shader and share video are unchanged; a song without a style keeps the single word. The lyric clock is the playing audio element read every frame (`audioEngine.heardPositionMs()`, less the output latency), not the `timeupdate` progress (~250 ms steps). Online, lyric timing always comes from the server (a downloaded copy is the offline fallback), so re-timings and new styles reach downloaded songs.

**DO:** Modify effect logic in the scene modules only. Use seeded random for offline, `Math.random` for live. Keep shaders identical between live and offline.
**DO NOT:** Duplicate shaders/effects in offlineVideoRenderer, or read React state from the renderer (everything it needs arrives in the snapshot).

### 12. Local TTS Engine (Chatterbox-Turbo)

ElevenLabs was retired in 2026-09 (cost). DJ speech is generated by Chatterbox-Turbo (Resemble AI, MIT) in `tts_chatterbox/`, with our own batched CUDA-graph decoder and batched vocoder for the P6000 (`tts_chatterbox/README.md`). It replaced Orpheus-3B on 2026-09-30 after a bake-off (`docs/TTS_ENGINE_RESEARCH.md`): better realism, first audio ~0.4 s, ~6x the capacity. Orpheus (`tts_server/`) stays in the repo but is not loaded.

- **Voices:** `[JESS]` (she/her) is Chatterbox's built-in voice; `[LEO]` (he/him) and the station voice are cloned from 10 s reference clips in `tts_chatterbox/voices/leo.wav` and `station.wav` (voices designed with Qwen3-TTS VoiceDesign, not real people). `settings.VOICE_PREFERENCES` maps host -> engine voice + temperature. Changing a host's voice means clearing that host's cached clips (`data/tts_dj_engine_data/*/<host>/`), otherwise old and new voices mix.
- **Paralanguage (never call it "meta"):** paralanguage segments are standalone clips in their own vector cache (`paralanguage_audio`, table `paralanguage_embeddings`), never mixed into spoken sentences. `generate_paralanguage_gpt_response` turns each stage direction (`~laughs heartily~`) into a phonetic/onomatopoeic spelling (`bwa-ha-ha-ha-haaaa`, `aarrrgg....`, `mm-hmm`) with the owner's original ElevenLabs-era prompt, a fresh LLM script for every new clip (no script cache) so reactions keep their variety. With `PARALANGUAGE_ENGINE_TAGS` on (default, owner's call 2026-10-01) the same prompt also gets Chatterbox's native sound tags (`ENGINE_SOUND_TAGS`: `[laugh]`, `[chuckle]`, `[sigh]`, ...) and decides per sound: tag alone, tag + phonetic, or phonetic alone. Flipping the setting purges the paralanguage cache at the next boot (`purge_paralanguage_on_mode_change`, marker `.paralanguage_mode`) so the two kinds of take never mix. The engine-tag paragraph also tells the writer it only ever voices the host: an object noise (`~taps desk~`) is played by the studio, so the writer gives the host's own little sound (`hnn-hm`), never an imitation of the object. Any bracket that isn't an engine tag is snapped to the closest one by its words (`[h chuckle]` -> `[chuckle]`) or dropped before rendering (`keep_engine_tags`, logged), because Chatterbox reads unknown brackets aloud.
- **Voice upscaling (owner's pick 2026-10-03):** every fresh host take (sentences, paralanguage, breaths; not SFX or the station voice) goes through ClearVoice speech super-resolution (`MossFormer2_SR_48K`, `services_radio/voice_upscale.py`) from 24 kHz to 48 kHz before the room chain, and the cache stores the upscaled take, so cached lines cost nothing. Measured on the P6000: 0.28 s for a 1 s take, 0.56 s for 3 s, 0.94 s for 6 s (the first fresh line of a reply waits for it; later lines upscale far faster than they play). Loaded once at startup (~3.4 s, ~1.6 GB); `TTS_UPSCALE=false` turns it off, and `output_rate` tells the mixer the real rate. Upscale an existing cache in place with `server/utils/upscale_voice_cache.py` (skips takes already at 48 kHz, keeps tags).
- **Two tracks, one per host (owner's model):** each host's sentences, paralanguage and breaths stack on her own track; only the two hosts cross over. In `IncrementalBlend` every element starts at the conversation end minus its `@N@` overlap, never before its host's own track end (a host never overlaps herself; owner rule, don't remove it). The planner keeps `@N@` whenever the window reaches the other host and drops it when it only reaches her own track. A breath is glued to the sentence it precedes. `%sfx%` is a bed layer: it overlays where it falls and never takes up time or pushes a host later. Mic proximity (`&N&`) carries per host until the next tag (an untagged line used to fall back to 0, which also switched Perlin variation off).
- **Paralanguage emojis (from LifeSpan):** every paralanguage title in the voice cache gets an emoji (`paralanguage_embeddings.emoji`, one small LLM call per title, `DJPromptSystemService.generate_paralanguage_emoji`; the phonetic prompt is untouched). `services_radio/paralanguage_emoji.py` renders `~tag~` as the emoji of the closest cached title in outgoing chat text only, and its `render` is the one place chat text is made ready to show: it also strips sounds, time-shifts and mic proximity (`dj_prompt_helper_service.CHAT_MARKUP` / `chat_text`). `websocket_service.broadcast_to_session` runs it on `conversation_update` / `dj_activity`, and `GET /api/conversation` on read. The client has no tag filter of its own (one list, no drift); a new performance tag is added to `CHAT_MARKUP`. Stored conversation text keeps the raw tags. Titles without an emoji get one in the background the first time they're needed.
- **Movement (mic proximity) is a real-time room simulation (owner's design; ask before changing any part of it):** `&N&` is a distance from the mic (`AUDIO_EFFECT_CONFIG['proximity']`: 0 = 1 cm on the mic, 0.2 = 3 cm, 0.5 = 14 cm, 1 = 2 m, the furthest a host gets (owner, 2026-10-02: 4 m sounded like a bathroom)). The `&N&` values are points on each host's path through time: every take eases from the midpoint with her previous own element to the midpoint with her next one (`movement_curve`; joins skip the co-host's lines, so the path never jumps). Perlin wobble sits on top: one continuous noise per host per reply (`MotionTrack` / `MotionSlot` in `tts_processing_service`, each take continues where her last one ended), and her pan is a slow continuous drift around her seat (Jess left, Leo right). Every 200 ms chunk (100 ms crossfade) applies that moment's value in one pass: dry level by inverse distance (capped +4 dB on the mic), room reverb held constant, proximity bass under 30 cm, high roll-off with distance (`mic_distance_response`). Never take an effect out of the chunks or make it static. Room ambiance (`%x%` with `&N&` > 0: pen, desk, chair) is a separate object per sound: its own random spot (±0.4 pan), its distance, a little drift and wobble, no curve joins. True SFX (`&0&`, soundboard / direct input) are dry and centred. The prompt asks for `&N&` on every element; an untagged element keeps its host's last value. Hosts are matched in level by a fixed per-host gain (`VOICE_PREFERENCES[...]['gain_db']`, Leo +2.2 dB measured 2026-10-01), never per-take leveling.
- **Written as spoken (4 Oct):** the engine reads text literally and nothing turns figures into words ("19:23" came out as "nineteen twenty-three", "9:29" as "nine thousand twenty-nine", "2.57" as "two fifty-seven"). `format_tone` (in every voiced prompt) tells the hosts to write times, numbers, symbols and short forms as words, on the 12-hour clock; data reaches them on the 12-hour clock too (`talking_clock.clock_time`: weather, events, Pulse, bulletins, local time) and wind as a compass direction.
- **Channels: only `[BROADCAST]` is spoken.** `[TXT]` is the hosts' private text message to the listener and stays in the chat (`tts_stream_planner.spoken_text`, used by every voice path). Untagged text is spoken. The old player worked this way; the 2026-09-28 revival started voicing `[TXT]` and it was restored on 2026-09-30.
- **Paralanguage tags use tildes (`~laughs~`), since 2026-09-30 (LifeSpan's lesson):** a tilde never appears in normal DJ talk, so emphasis or a song title can't turn into a tag. `clean_gpt_output` strips every `*` and escaped quote, keeps well-formed tags (`~x~ %x% @N@ &N&`) and only removes broken fragments between them (`handle_mismatched_tags`), so a stray symbol can't eat a speaker tag. The planner never sends a sentence without words. Example tags for the DJ are random clean titles from the paralanguage library, topped up from `STARTER_PARALANGUAGE_TAGS` (20) until the library has 20 clean titles; a tag with digits or markup is never rendered or cached.
- **Speed:** up to 8 sentences decode together (`TTS_ENGINE_SLOTS`), any mix of voices; the backend's priority lanes decide what goes first. Measured on the P6000: one sentence 0.41 s to first audio, 8 at once ~1 s and ~6.4x real time in total. The token model is not the limit (727 tok/s at 8 slots, 25 is real time); S3Gen, which re-reads each voice's 10 s reference on every call, is.
- **Sacred design:** the performance planner (two hosts talking over each other via `@N@` overlaps/blends, `&N&` mix + 3D reverb, `~paralanguage~`, `%sfx%` beds, breaths) must not change. Only pure delivery/latency/efficiency changes are acceptable — the listener must hear the same audio in the same order. Streaming the LLM reply and re-planning it was tried and rejected.
- **Delivery (progressive, identical audio):** `tts_queue_manager` starts rendering every segment at once, assembles the timeline in playback order (`IncrementalBlend` emits a blend only once later segments can't change it) and feeds `TimelineMixer` (`tts_broadcast_service.py`) → one persistent ffmpeg WebM/Opus encoder per stream (`tts_live_stream.LiveStreamEncoder`) → `tts_stream_audio_chunk` events. Engine requests go through a priority scheduler in playback order (`tts_generation_service`), one generation until first audio then two (`TTS_TURN_GENERATION_PARALLEL_START` / `_PARALLEL`). Fresh PCM goes straight into processing; the FLAC cache file is written in the background.
- **Impulse and interlude are cached mini-scripts (owner's design 2026-10-01, from LifeSpan; `services_radio/filler_scripts.py`):** little bits of radio theatre caught on a hot mic (candid, the hosts drifting between the mic and leaning away, so `&N&` varies within a script) in the normal DJ format (both hosts, `~paralanguage~`, `%ambiance%`, `@N@`, `&N&`), stored in the `filler_scripts` table (embeddings DB) under the conversation they fit (`conversation_context`: NOW listener first, then the last two exchanges). One impulse per listener message, the moment it lands (voice: fast transcript; typed: on send). An interlude only when the station has been quiet for `INTERLUDE_SILENCE_S` while the DJs work on the reply (tool activity included), up to `INTERLUDE_MAX_PER_TURN`, as escalating sequences of `INTERLUDE_SEQUENCE` beats that a turn plays in order. Rules: always the closest stored script, never a wait (an empty library plays nothing); the anti-repeat is the one universal shotgun (`VectorDBService.note_used` / `is_fresh`, `VECTOR_DB_SHOTGUN_COOLDOWN`, per listener): recently heard scripts give way to the next best, and if every fitting script was heard recently the best one still plays, exactly like the lines; a turn's interlude follows its sequence in order; a best match under `FILLER_LEARN_BELOW` makes DeepSeek write a better one in the background (writers `write_impulse_script` / `write_interlude_scripts` get the show's own nodes: identity, roles, tone, performance-tag guide and examples, dialogue examples). They play through the normal engine in cache-only mode (`tts_type` impulse / interlude): every line takes its closest cached take and its exact line is queued on the low-priority lane for next time (`schedule_refresh`); nothing renders live. What aired is told to the DJ (`[ON AIR JUST NOW]`), shown in the chat and saved in the conversation. Their `say` activity carries `kind` (impulse / interlude), and the chat shows those lines as off-mic (ear icon, dashed, dimmed). The library takes a few days of listening to fill up; that is expected. The old `[IMPULSE]` clip type, `impulse_audio` and the tool fillers are gone.
- **Paralanguage is emergent:** each uses a cached clip at/above `PARALANGUAGE_SIMILARITY_THRESHOLD` (and the per-listener anti-repeat cooldown), otherwise it renders live and joins the cache. No hourly caps, library ceilings, script caches or background refreshes: the threshold and the cooldown are the only limits, so the library grows until it stops missing.
- **Breaths are an offline library (2026-10-02):** Chatterbox can't render a breath on its own (scripted breaths came out as hums), but it breathes naturally between two sentences of one take. `server/utils/build_breath_library.py` clusters real DJ lines by meaning (~100 clusters), renders each centre line plus a follower as one take, finds the gap with Whisper word timings, drops voiced edges and silent gaps, and writes the breath as FLAC tagged with the sentence it followed. The library is copied into `breath_audio/<host>/` with `breath_embeddings` empty so it indexes at boot. Breaths go between a host's consecutive sentences, also when the co-host cuts in between; each is matched on the sentence she just said (`segment['context']`), always the closest take (`BREATH_SIMILARITY_THRESHOLD` -1, cooldown applies), never rendered live (`GENERATION_PERMISSIONS['breath']` empty). Grow it by re-running the builder. Breaths are meant to be subtle: never turn them up.
- **Anti-repeat (shotgun) cooldown is per listener:** `VECTOR_DB_SHOTGUN_COOLDOWN` is keyed by (listener session, clip filename), passed through `lookup_clip(..., listener=owner)`. One listener hearing a clip never blocks it for anyone else.
- **Interrupt:** a new listener text/voice turn calls `TTSQueueManager.cancel_session` — drops queued TTS, cancels the in-flight render, aborts that session's engine jobs via `POST /abort/<job_id>`, and emits `tts_stream_end` + `tts_stream_cancel` (the client clears its queue).
- **Cache:** new clips are cached per host in `data/tts_dj_engine_data/{tts,paralanguage,breath}_audio/<host>/` as lossless FLAC (16-bit 24 kHz, the engine's own PCM; Vorbis `title` = the line, or for breaths the sentence before it, and `description` = the script that was voiced), never MP3: they are kept for offline upscaling later; missing files self-heal (stale rows deleted). Thresholds (measured 2026-10-01 on the live cache): TTS 0.975 (0.95 reused "it's 6 pm" for "11 pm"), paralanguage 0.85 (0.75 matched "gasps softly" to "groans softly"), SFX 0.75 (0.5 played a horn for "cowbell"). A line below the threshold renders live; there is no background exact-line re-render any more (removed 2026-10-01). The near-best random pick only chooses among takes at/above the threshold. SFX are leveled before the Perlin chain (`level_sound_effect`, `TTS_SFX_TARGET_DBFS` -46 / `TTS_SFX_MAX_PEAK_DBFS` -24) so `&N&` distance still changes their level. Perlin noise starts at a random offset on every render (it used to be identical for every clip). All cache embeddings (lookups, live saves, startup migration) use the shared mpnet encoder (`SEMANTIC_ENCODER`, 768-dim, ~15 ms per line on the P6000); rows made with another encoder are cleared at startup and re-embedded from the clips' `title` tags (`drop_stale_encoder_rows`, `tts_database_migration_service.read_clip_tags`). flan-T5 was retired on 2026-09-29: its last-position (padding) state scored opposite lines ~1.0 and identical ones ~0. Cached clips decode in-process via `tts_processing_service.decode_mp3` (soundfile, FLAC or MP3). Sound effects (`audio_effect_audio/`) are cache-only bought MP3s with ID3 tags and stay MP3.
- **Stings & talking clock (no LLM):** a third voice, `station` (`tts_chatterbox/voices/station.wav`), with a "station computer" treatment (`tts_processing_service.station_treatment`: radio-band EQ, light ring-mod/comb/bitcrush, slap + short room, then the normal chain via `process_station`). Its clips live in their own exact-key cache (`station_voice.py`, `data/tts_dj_engine_data/station_audio/<voice>/index.json`, FLAC), never the vector cache. The talking clock (`talking_clock.py`: intro + "forty-six minutes past" / "quarter past" / "half past" + hour, or hour + "o'clock", + optional daypart; 85 parts. Minutes carry "minutes past", which Orpheus needed to avoid looping on a bare number) only ever uses exact parts; a missing part means no time check (never a near match), and hours/minutes are checked with the fast Whisper model after rendering. Station IDs (`station_ids.py`, ~36 lines incl. "{city}" lines) play the best cached take and queue the exact line (LifeSpan pattern). Musical stings are cut from 2 Suno idents into `STINGS_DIR` (`manifest.json`, never the catalog); sweeper beds can also come from `audio_effect_audio`. Types are a registry (`sting_types.py`, each with `min_window_s` and `voice`), the choice is pure (`sting_schedule.choose`: a time check if due, else a voiced kind that fits the space, else a musical sting), `sting_service.py` pre-renders at low priority; they stream as `tts_type="sting"` through `TTSQueueManager.add_clip_request` → `LiveStreamEncoder` (same client path and ducking). Radio Mode talk breaks can open with an ID (`lead_in`). Settings: `STINGS_*` / `STATION_*`. Station name is spelled "Playar" for the engine (`STATION_NAME_SPOKEN`; it said "PLAiR" as "P. L. A. R."); `tts_stream_planner.say_station_name` swaps PLAiR / PLAiR.fm in every host line for it ("Playar F M") on the way to the engine, so the chat keeps "PLAiR".
- **Radio drops (in/out sounds, 3 Oct):** every hosts' voice stream (not impulse/interlude) and every computer-voice sting opens and closes with a short drop from `BLIPS_DIR/<hosts|station>/<in|out>/` (`services_radio/station_blips.py`, mixed in `LiveStreamEncoder`, which holds back the last `BLIPS_HOLD_MS` to place the out-drop): the voice comes in as the in-drop decays (`BLIPS_RELEASE_DB`) and the out-drop peaks as the last word ends, so drops overlap the talk instead of adding gaps. Hardwired, never chosen by an LLM. The stream's end message carries `talk_end_s` (where the talking stops); `djStreamPlayer` turns `isDJSpeaking` off once playback passes it, so the music comes back up (and the speaking glow ends) while the out-drop's tail rings out. Drops are made with Stable Audio Open on the P6000 and picked by the owner: skill `radio-drops`, tool `server/utils/generate_radio_drops.py` (batches in `BLIPS_DIR/candidates`, earlier Keep/Skip verdicts steer the next prompts).
- **The station uses each song's spaces like a DJ (4 Oct, `docs/HANDOVER_2026-10-04_STATION_ACTIVITY.md`):** when a song starts, the announcer maps its spaces: the change into the next song (the transition window, or the adaptive crossfade when there is none) and the quiet, vocal-free stretches at 10-85 % (quieter than 40 % of the song by loudness AND clear of every sung line +/- 0.4 s, at least `STINGS_MIDTRACK_MIN_WINDOW_S` 3.5 s; no mid-song spaces when the lyric timing can't be trusted (alignment score < 0.3 or squashed into the start, ~5 % of songs) unless the track is instrumental). Each space is decided when it arrives, from the live state (`_run_space` -> `sting_service.offer_space`), first come first served: at the song change a lined-up Radio Mode break, else the hosts (who keep their LLM lead; their failure or `[N/A]` hands the gap to the computer), else the computer; in a mid-song stretch a listener review if one fits, else the computer, but only when the station has been quiet for `STATION_QUIET_TARGET_S` (180, `TTSQueueManager.quiet_for`). The listener's own switches (stings, reviews, Radio Mode) decide what is allowed; there are no other timers, rotations or probability rolls (time checks keep their own spacing). Skips cancel the song's spaces. Every space logs one `playback` line: `<space> (<len>) -> <what aired>` or `-> nothing (<why>)`.
- **Re-render, don't band-aid:** rendering is local and free (we are not paying ElevenLabs). When an engine, prompt or wording change is an overall improvement, purge the affected regenerable caches (`tts_audio`, `paralanguage_audio`, `breath_audio`, `station_audio` + their embedding rows/`.ann`) and let everything re-render fresh. Don't preserve old takes with targeted scans, quarantines or extra validation layers. Never purge `audio_effect_audio` (sound effects are cache-only and cannot be regenerated).
- **Stopping:** a take ends on Chatterbox's stop token; runaway takes are cut by a length cap from the text, `max(5 s, 3 s + 1 s/word)` (`tts_chatterbox/engine.py`), logged as `end=max_tokens`.
- **Build gotchas:** PyTorch must stay on a CUDA 12.x wheel (cu124); cu128+ and CUDA 13 dropped Pascal (sm_61). No fp16/bf16 on this card: the engine runs fp32 with CUDA graphs. See `tts_chatterbox/README.md`.

### 13. Backend Structure & Security

- **Routers:** `server/app.py` only holds the lifespan (service startup/shutdown), middleware and router includes. Routes live in `server/routers/*.py` by domain; shared dependencies (`get_session_info`, `get_current_user`, `RateLimit(...)`, `require_admin`, `read_upload_limited`) are in `server/routers/deps.py`, request models in `server/routers/schemas.py`. Services are created in the lifespan and published on `server/service_registry.py` (`services.x`) — routers read `services.x` at request time; never import a service instance at module import time.
- **Request guard:** `server/security_middleware.RequestGuardMiddleware` (HTTP + WebSocket) rejects paths containing `\`, `..`, NUL or `:` and any guest id not matching `guest_<uuid>`. Every file-serving route builds paths from validated ids — never join raw request strings onto directories (Windows `Path(dir) / "E:\\x"` escapes the directory).
- **Sessions:** user session id = user id; guest session id = the client's `guest_<uuid>`. Invalid tokens fall back to guest; invalid guest ids are rejected. The WebSocket requires a user token or a valid guest id; its first message is `session_info {authenticated, user_id, token_rejected}` (closes with 4401 when a rejected token comes without a guest id). The client (`AuthContext.handleSessionInfo`) re-validates on `token_rejected` and on any 401 to a token-bearing request, then signs out cleanly (toast + login modal) instead of staying half signed-in. Tokens nearing expiry are renewed silently via `POST /api/auth/refresh`. The WebSocket reconnects only when the identity (`sessionKey`) changes, not on token refresh.
- **Sign-in without passwords (owner's call, 2026-10-02: least resistance, no email):** passkeys (WebAuthn: Windows Hello, Face ID, fingerprint) are the default for sign-up and sign-in (`services/passkey_service.py`, table `passkeys`, `routers/account.py`, client `lib/passkeys.js` via `@simplewebauthn/browser`; RP ID from `PASSKEY_RP_HOSTS`, so `www.plair.live` shares `plair.live`'s passkeys). A new device can sign in by QR: it shows a code (`services/device_link_service.py`, in memory, `DEVICE_LINK_TTL_S`) as a QR of `/?link=CODE`; a signed-in phone scans it with its camera (`DeviceLinkBridge`) or types the code in User → Account & Privacy, and approves. Passwords are optional (`users.password_hash` nullable, set or changed while signed in). Accounts without a passkey get one silently after a password sign-in (`upgradeToPasskey`, conditional create, no user presence). Test: `tests/account_test.py --base URL` (software passkey, about 2 LLM calls for one review).
- **Delete account:** `DELETE /api/auth/account` → `services/account_deletion_service.delete_account` closes Stripe billing first (nothing is deleted if that fails), closes the live session, deletes uploads, posts, share videos, every row keyed by the user or their session, and `USERS_DIR/<uid>`; AI usage rows are kept as `deleted:<uid>` for cost totals. A new per-user table, file or in-memory store must be added there.
- **Secrets:** `JWT_SECRET_KEY` must be a random ≥32-char value in `.env` (startup refuses weak/placeholder values); tokens last `JWT_ACCESS_TOKEN_EXPIRE_DAYS` (7). Never print `.env` contents, even masked (comment lines can hold secrets).
- **Abuse limits:** paid AI/GPU endpoints use `RateLimit` token buckets (guests get a small quota on `/api/dj/talk` and `/api/transcribe`; AI analysis in searches is user-only; login/register limited per IP and username); lyric generation is admin-only (`ADMIN_USER_IDS`); uploads are read in chunks with hard caps; API docs are off unless `ENABLE_API_DOCS=true`; shoutout responses never include coordinates (`public_shoutout()`).
- **Media responses:** `MediaAwareGZipMiddleware` (security_middleware.py) never gzips `/api/stream`, `/api/artwork`, beds or stings. `media_streaming_service.parse_range` handles suffix ranges and answers 416.
- **Mobile readiness:** status, open items and the device test plan are in `docs/MOBILE_LAUNCH_READINESS.md`; the brand masters and the script that rebuilds the app icons are in `brand/2026-refresh/`.
- **DB connection budget** (Postgres `max_connections` 100, 3 reserved): async engine 20 + 10 overflow (30 s wait), psycopg2 pools 16 per database × 3 (catalog, user content, embeddings) that block up to 30 s when full instead of opening extra connections, sync engine unpooled (startup `create_all` only) — at most ~80 in total. Async connections carry `idle_in_transaction_session_timeout` 120 s. Never hold a DB session for the life of a WebSocket or a streamed response: auth reads users from `user_data_cache` (`merge(load=False)`, or session-less `get_cached_current_user` for media streams) and the WebSocket uses short `AsyncSessionLocal()` blocks.
- **nginx (plair.live block of `C:\nginx\conf\nginx.conf`, shared with other sites; edit only that block):** `www.plair.live` and plain http 301 to `https://plair.live` (one origin, one login; 5 Oct), forwards `X-Real-IP`/`X-Forwarded-For` (per-IP limits are live), gzip for static JS/CSS/JSON, HSTS + nosniff + referrer-policy headers (repeated in `location /` because its own `add_header` stops inheritance), `/ws` has `access_log off` and a 75 s read timeout. Test with `nginx -t`, apply with `cd /c/nginx && ./nginx.exe -s reload` (graceful).

### 14. Motion System - One Design Language

**Files:** `client/src/lib/motion.js` (tokens + presets), `client/src/lib/microMotion.js` (WAAPI micro-interactions), `client/src/components/Motion.jsx` (Expandable, ExpandSection, FadeSwap), `client/src/hooks/useEntranceWindow.js`, `client/src/hooks/useArtPop.js`, `client/src/contexts/QualityContext.jsx` (adaptive quality tiers).

- All durations, easings and springs come from `motion.js` tokens (`DURATION`, `EASE`, `SPRING`, `TWEEN`, `PRESETS`, `VARIANTS`, `MICRO`, `CSS_TRANSITION`); CSS uses the matching `--dur-*` / `--ease-*` variables and Tailwind `duration-*` names. Never hard-code a duration or spring.
- Startup: the `#splash` in `index.html` (inline CSS, shows from the first paint) stays until `lib/splash.js` has both `scene` (first shader frame, `AudioReactiveCanvas`) and `playback` (first `playback_state`), 4 s at most, then fades out over the finished app. Layers that arrive late (modal blurs) use `ui-layer-in` (fades from 0 to the element's own opacity).
- **One notice channel (SSOT):** every message that pops up at the top is a notice in UIState: `showNotice({ key, tone, text, icon, content, duration, sticky, dismissible, priority, borderClass/borderColor })` / `hideNotice(key)`, rendered only by `NoticeStack` with `NoticeChip` (`components/Notice.jsx`: dark glass pill, tone-coloured edge + default tone icon; tones success/error/warning/info/neutral). Passing notices time out per tone (`NOTICE_DURATION_MS` 2 / 2.5 / 3.5 / 4 s; a longer request is shortened; 0 or `sticky` stays), share one key per tone+text (repeats replace), at most 3 passing at once, tap to dismiss. Toasts (`toastSuccess/Error/Info/Warning`, `publishToast`, `removeToast`) are thin wrappers; the old `position` argument is ignored. Sources that stay up are render-nothing bridges that show/hide a keyed sticky notice: `OnAirNotice` (priority 0, while `engineState.talkBreak`), `DeviceNotice` (Player, 'Playing on another device' + Play here), `OfflineNotice` (AppBridges), `DJActivityBridge` (tool chips, priority 2, hidden offline). Never add another floating message surface or a second store; add a bridge that calls `showNotice`.
- **Every list that can grow is virtualised (SSOT, owner 6 Oct):** it renders through `VirtualScroller` (fixed or measured rows), never a plain `.map()` into the DOM; rows animate in only when they newly arrive (not when scrolled back into view) and keys must be unique (the Timeline once keyed by track id and broke). Measured on the owner's phone: a 503-song queue alone was 7,237 of the page's 9,673 elements.
- **One empty / offline state (SSOT):** any panel or list that has nothing to show (empty, loading, failed, offline) renders `MediaEmptyState` from `components/MediaShared.jsx` (icon, title, subtitle; centred, padded, balanced wrapping; `compact` for in-panel lists), and "needs a connection" states use `MediaOfflineState` (fixed wording). Never hand-roll an empty or offline message with its own `<p>` or `<div>`. A short status line inside a panel, row or form (paused, failed, offline, validation) is an `InlineNote` from `components/Notice.jsx` (`tone` success / error / warning / info / neutral, the same tone icons and colours as `NoticeChip`). Pop-up messages stay on the notice channel above.
- **Nothing pops (owner, 6 Oct):** anything that appears or disappears while it is on screen enters and leaves through the shared blocks in `components/Motion.jsx`: `Pop` for small things (controls, icons, badges, chips, spinners; `PRESETS.pop`, the elastic spring), `Fade` for text and blocks (`PRESETS.fade`), `FadeSwap` for two things swapping in one spot. An element framer-motion animates never carries `ui-press` / `ui-tap` / `ui-hover` or Tailwind `transition*` classes: their CSS opacity/transform transitions fight framer, so fades stutter and exits pop. Put those classes on the element inside. `npm run check:motion` (part of the quality gate) enforces it.
- Tactile feedback: add `ui-tap` (icon buttons) or `ui-press` (wide buttons) and one document-level listener runs the press/release pop. Positive actions use `MICRO.burst`, bans `MICRO.nope`, artwork `useArtPop`.
- Animate transform/opacity only (no width/height/box-shadow/filter), no per-frame React state, nothing off-screen. `data-motion` (full / lite / off) follows the QualityContext level and `prefers-reduced-motion`.
- Visual state priority (Recording > AI Processing > DJ Speaking > Music Playing > Paused > Idle) has an ON AIR modifier during Radio Mode talk breaks (`engineState.talkBreak`: the sticky `OnAirNotice` pop-up, `OnAirFrame`, shader tint; no ON AIR badge in a panel or the player). While a break is on air the player's waveform becomes the break's progress bar: `usePlaybackActions().talkBreakProgress()` (the DJ stream's clock on the playing device, `on_air_at_ms` + `server_time_ms` on the others); breaks air only once fully rendered, so the length is exact. The background shader also glows with the speaking DJ's voice colour (`VOICE_*` constants in `lib/sceneRenderer.js`), in both the panel scene and fullscreen visuals (`AMBIENT_GLOW_FUNCTION` is shared by the scene and backdrop shaders); shared shader exports for `offlineVideoRenderer` must stay unchanged.
- Frame-rate rule: always aim for the device's own refresh rate (120 Hz phones get 120 fps). Visual quality is one setting, High / Medium / Low / Auto (`settingsState.visualQuality`, `QualityContext`; owner, 5 Oct 2026): the levels trade resolution (DPR), glass blur taps and parallax detail, never the frame rate, and nothing caps frames (the old 30 fps cap on weak GPUs held the owner's Adreno 610 phone at 60). Only Auto adapts: it moves between the three levels against the screen's own refresh (`lib/screenRefresh.js`; windows averaging over 1.5 refresh intervals drop a level, under 1.11 raise one). Components read the effective level from `useQuality().level`, never `settingsState.visualQuality`. Keep It Smooth (`settingsState.keepSmooth`, default on) is a separate control on top: small levers (`QualityContext` `SMOOTH_LEVERS`, first: coarse covers) go down one by one while frames run slower than the screen's refresh, before Auto changes the level, and come back in reverse; a step that fails twice stays locked. `__plairQuality()` shows the levers in use.
- Shader efficiency rules (keep quality and full frame rate, 120 fps included, never cap it): values that are the same for every pixel (glow centre, aspect, colour × level, panel edge tint) are computed once per frame into `u_glow_*` / `u_panel_glow` uniforms, and the glow only runs while `u_glow_active` (DJ speaking or ON AIR). The low-res background capture pass only re-renders when background inputs change (the signature prefix up to `captureLength`, textures, video, resize). The lyric texture is skipped while `u_text_empty` (defaults to 0, so the video export is unaffected). Three.js `VideoTexture` uploads its own new frames; don't set `needsUpdate` on it per frame.

### 15. City Pulse, Location, Radio Mode & Cost Systems (Sept 2026)

Design doc: `docs/CITY_PULSE.md`. All open work, bugs and owner decisions are in one list, `docs/KNOWN_ISSUES.md`; add to it instead of writing a new handover.

- **Listener location (SSOT):** `context_service.listener_location(user, session_id)` (`services_radio/listener_location.py`). Logged-in users come from the profile; guests send `listener_location` over the WebSocket and are held in memory only (6 h TTL, never stored). Fallback: device location, then the timezone city, then `NEWS_DEFAULT_COUNTRY`. Every location consumer (news, weather, events, places, geocode, air/pollen, local time, Radio Mode, DJ context) goes through it.
- **Regional knowledge (city pool):** `services_radio/regional_knowledge.py` defines the `Collector` interface, `KnowledgeItem` and `RegionalKnowledgeService.query(...)`. Collectors: Ticketmaster events, Google Places IDs, Google News. Shared per region, targeted per listener by taste (no LLM).
- **Places (semantic source):** every Google Places result we fetch is kept in `place_cache` for good (refreshed when fetched again) and embedded on the fly into the `place_nuggets` source (`local_knowledge.PlaceVectorDatabaseService`: name, type/tags, about, area, hours, rating/price, plus a `where`). We ask Google for everything of value (`external_location_service.FIELD_MASK`): the place's summary, a summary of its reviews, all its types, features (live music, good for kids, dog friendly, outdoor seating, vegetarian, cocktails, wheelchair access, parking, cash only, ...), the suburb, whether it has closed for good (left out) and its Maps link. These are kept in `place_cache.details` and searched as the `place_about` category; the hosts see the features and summary. The city sweep also covers libraries, community centres, farmers markets, galleries, event venues and theatres, whose websites seed the event harvester. The city sweep (`GooglePlacesCollector`, `REGIONAL_PLACES_*`) fetches full records per category into the same cache. `PlacesNode` searches it by meaning within `PULSE_CITY_RADIUS_KM`; Google is asked live only when nobody has asked a similar question near that spot (`place_memory.searched_near`, `PLACE_MEMORY_*`).
- **Area signals:** `area_signals.py` with `area_geocode.py`, `area_air_quality.py` and `area_pollen.py` (Google APIs on the PLAiR project key `GOOGLE_PLACES_API_KEY`), shared per grid cell in `area_cache`.
- **News store:** `services_radio/news_store.py` persists Google News pulls and items in Postgres, reuses them by meaning plus matching stories, stores the ranking once, and keeps an aired ledger per listener.
- **Radio Mode:** opt-in scheduled talk breaks (`radio_mode_service.py`, `radio_segments.py`, `radio_schedule.py`, `music_beds.py`; client `lib/talkBreak.js`, `lib/musicBed.js`, `RadioModeSettings.jsx`, `OnAirBadge.jsx`). Music beds are in `CATALOG_DIR/music_beds`, stings in `CATALOG_DIR/stings`, and neither is ever in the main catalog.
- **Stings & talking clock:** `sting_service.py`, `sting_types.py`, `sting_schedule.py`, `talking_clock.py`, `station_ids.py`, `station_voice.py`. Station voice is `tts_chatterbox/voices/station.wav` with `station_treatment`; the spoken name is `STATION_NAME_SPOKEN` ("Playar"). Between tracks and in mid-song quiet stretches: see "The station uses each song's spaces like a DJ" in section 12.
- **LLM routing and cost:** `services/llm_router.py` has role chains (`LLM_LIVE` Gemini 3.5 Flash-Lite; `LLM_ANNOUNCE/INTERPRET/BACKGROUND` DeepSeek flash, then Gemini fallback)). Rule (owner, 2026-10-01): Gemini only for what is urgent and interactive (the live DJ turn and its Producer); everything else runs on DeepSeek, split like the live DJ vs the between-track announcer. Voice-take and filler scripts (paralanguage, breaths, impulse and interlude scripts) use `LLM_ANNOUNCE` (`dj_prompt_system_service.VOICE_SCRIPT_ROLE`); `_execute_gpt_stream` has no default role so nothing lands on Gemini by accident. `services/llm_telemetry.py` is the one price table. `services/usage_tracking.py` + `usage_middleware.py` attribute every paid call (and cache savings) to a user, guest or system scope; the admin view is `UsageStatsModal.jsx` / `routers/usage.py` (`ADMIN_USER_IDS`).
- **Cover packs:** `GET /api/artwork/{id}/pack/{size}` (`services/artwork_thumbnail_service.py`, section 8); the client prefetches through `lib/artworkPrefetcher.js`.
- **Semantic sources (one pattern for everything searchable):** tracks, shoutouts, local knowledge (events), news and listener requests are all `CategoryStore` sources (`services/category_store.py`, on the shared `VectorStore`, section 19): a source table of `(rowid, id, metadata_json)`, named semantic categories with weights, a per-category embedding table (text -> vector, each text embedded once), per-category item vectors held as matrices in the live slot, and search that re-weights categories per query (regex presets or the query-intent prompt cache) and ranks every item in a few matrix operations (`services/semantic_source.py` `SemanticSearch`, used by music and shoutout search too). Every source uses one encoder, `SEMANTIC_ENCODER` (all-mpnet-base-v2, 768-dim; the TTS clip cache uses it too). Embedding tables carry the encoder slug (`<category>_mpnet_embeddings`), so changing the encoder rebuilds cleanly; query-intent caches and the Producer cache (both `SemanticCache`) re-embed stale rows in place. New sources subclass `SemanticVectorDatabaseService` with a `category_specs` list: adding a category is one line.
- **International or nothing (owner's rule):** every City Pulse source must work in any city in the world (general web search, schema.org / iCal pages, global APIs). Never an adapter for a one-country platform (Eventfinda, Skiddle, ...), never code or settings for one city. Auckland is only the test city.
- **Grassroots events (`services_radio/event_harvest.py`, collector `web_events`):** no code per site. Event searches run through the news store plus venue websites from place memory. Pages are read through `web_fetch`: schema.org Event JSON-LD first, then iCal, then the WordPress events feed, and DeepSeek only for pages with none of these. Every page also offers its links to a focused crawler: links are scored by meaning with the shared encoder against event phrases (no word lists) and the best become `lead` sources, a few hops deep, capped per site; it is our own search, no search API (owner's call). Pages that keep yielding events are remembered in `event_sources` and re-read every 2 days. Results land in `regional_items` as source `web`. Settings `EVENT_HARVEST_*` / `EVENT_SOURCE_*`; probe `tests/event_harvest_probe.py`; details in `docs/CITY_PULSE.md` section 15.
- **City Pulse sources:** local knowledge (`services_radio/local_knowledge.py`) is the `local_nuggets` view over `regional_items` (events and local news filled by the collectors; categories title, tags, people, place, details, kind, when). Listener requests (`services/listener_request_service.py`, table `listener_requests` in the user-content DB) store every ask with what the station answered (categories text, topic, answers, intent, area, daypart, daily-salted asker hash; no user ids or coordinates). New asks, places and shoutouts are added one by one as they come in; the collectors' bulk writes (events, news) mark the store dirty and the vector store maintainer rebuilds it. Shoutouts are searched through the existing shoutout search service.
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
- **Audio chain (`SHOUTOUT_ENHANCEMENT_VERSION` 3):** native 48 kHz decode (browsers record Opus at 48 kHz; the client records mono Opus at 32 kbps / AAC 64 kbps) → MossFormer2_SE_48K (4 s windows under `no_grad`, ~0.6 GB) → 75 Hz low cut → LLM filter on `LLM_BACKGROUND` (process talk, stumbles) cut on Whisper word timestamps → Silero VAD shortens pauses over 0.45 s (never where a word starts) → -16 LUFS (the station level, `MASTER_TARGET_LUFS`) → MP3. No compression server-side: the client DJ broadcast chain compresses on air. Measured with Audiobox Aesthetics on real uploads: the old 16 kHz DeepFilterNet + super-resolution chain scored below the raw recording. Bumping the version makes the asset doctor re-render every item from its source webm (backups kept).
- **Reviews → stings:** the review prompt returns `sting_quote` (3-12 exact words that stand alone over the song, null for negative/unsafe); `_sting.mp3` is cut from the final audio. A mid-song quiet stretch that fits one plays the best-ranked fresh review of that track (`announcer_service._offer_review`, `build_review`/`play_review`) before the computer gets the space. Applies to all listeners, gated by the `reviews` radio pref (default on, "Listener reviews over songs") and the usual sting gate minus the stings pref.
- **On air:** `community_on_air.pick` is the one way to choose shoutouts for the DJ (`play_shoutouts`), Community Corner and Pulse: shoutouts only, minus the listener's bans, items buried by bans (≥3 bans and more bans than likes) and anything aired to this listener in the last 30 min; each carries its `top_reply` (ranked by likes/plays) and the DJ is told to play it right after. Plays inside the DJ mix are logged (`record_on_air_play`, deduped 6 h). Pulse has a `review` kind (`ReviewsNode`); community `detail()` includes the top reply.
- **Tests:** `tests/community_test.py --base URL [--recording file.webm] [--keep]` (typed review, spoken review + sting, typed reply, ranking, no coordinates; cleans up after itself).

### 18. Music Mastering Chain (master chain version 4, 5 Oct 2026)

Details, measurements and everything tried and rejected: `docs/HANDOVER_2026-10-02_MUSIC_UPSCALING.md`.

- **Suno chain (orchestrator lanes 1, 1b, 2, 3, 4, 6):** decode -> **Apollo** on the decoded MP3 (what it was trained on), keeping Suno's own signal below its MP3 cutoff (`audio_headroom.keep_source_below_cutoff`: Apollo only fills above it; on the full mix it had pulled 16.5-17.5 kHz down 4-5 dB) -> RoFormer split of the restored mix -> Lew's vocal Apollo on the vocal (no crossover) -> remix -> notches (`correct_audio`: 25 Hz rumble cut, stationary resonances only, 20 kHz safety roll-off) -> SonicMaster (33% wet, 20 steps, `SONIC_MASTER_PRECISION=auto`: fp32 on the P6000 (Pascal runs fp16 at 1/64 speed), fp16 on the RTX 6000 (1.7x faster); prompt "give the mix more shine and sparkle, with depth and separation between left and right, and let the audio breathe more and improve the dynamics", blend compensation on) -> **reference EQ + final leveler** (lane 6). Intermediates: `DECODED_WAV_DIR` -> `WAV_DIR` (Apollo) -> stems (with `vocal_mix.wav`) -> `PREMASTER_WAV_DIR` (SonicMaster's input) -> `SONIC_WAV_DIR` -> master.
- **Reference EQ (owner's rule, 5 Oct):** the target is the modern-master average (Pestana et al. 2013, US/UK No.1 singles 2000-2010, per-Hz density; `MODERN_MASTER_HZ/DB` in `audio_master_service.py`), aligned at 500-2000 Hz. Only what falls outside ±3 dB (`REFERENCE_TOLERANCE_DB`) is moved, by the excess (up to 10 dB, 40 Hz-16 kHz, linear phase; the curve is iterated until the measured result lands on the band edge), so each mix keeps its own fingerprint; never a flat match. The old Elowsson 2017 curve was retired: its folk-heavy CD-era corpus is 6-7 dB darker at 8-12 kHz than modern masters, and matching it made masters sound muddy. Never use the owner's own mixes as a reference. Uploads get the same EQ at their mastering blend.
- **Tape hiss (owner, 5 Oct):** Suno masters get steady tape-style hiss at `MASTER_TAPE_HISS_DBFS` (-80 dBFS since 6 Oct, owner: -70 was a little loud; decorrelated L/R, lighter lows, flat highs) added after the reference EQ, just before the limiter (`audio_headroom.tape_hiss`). It masks the codec holes in the highs (the deepest ones halve) and is heard only in true silence. Not on human uploads.
- **Station level -16 LUFS** (Apple Music standard, `MASTER_TARGET_LUFS`): masters, uploads and shoutouts; stings -18, station voice -20, beds -22; DJ hosts get `DJ_VOICE_LEVEL_DB` (-2 dB). Never master louder: at -14 the true-peak ceiling capped the peak-to-loudness ratio at 12.5 dB and the owner heard it as squashed.
- **Versioning:** every master stamps `master_chain_version` (`settings.MASTER_CHAIN_VERSION`) and `master_rendered_at` into its metadata. Bump the version whenever the sound changes; `server/utils/process_backlog.py --rerender` then re-renders only older masters.
- **GPU memory (5 Oct):** the render shares a card with whatever else runs there (the live station holds ~13.6 GB of the P6000). On Windows a card that runs out spills silently to system RAM and slows that step 10-25x (Carbon Copy took 17 min instead of 5). So SonicMaster parks its model in system RAM between runs (`SONIC_MASTER_PARK_ON_CPU`, ~2 s) and encodes/decodes its VAE in 10 s tiles with 2 s margins (`SONIC_MASTER_VAE_TILE_S`; bit-identical output, peak 11.4 -> 7.7 GB). Measured peaks: Apollo +8 GB on a 20 s chunk, Lew +1.8, RoFormer +0.8; the whole render process peaks ~12.6 GB with no spill, ~5 min per song on the P6000.
- **Lyric timing (6 Oct):** lane 5 times the lyrics with Whisper on the separated vocal (`track_asset_stages.demucs_vocal_stem` looks in the RoFormer folder `demucs_stems/<id>/roformer/`; before 6 Oct it never found it, so renders timed against the full mix). Whisper starts a word that follows a pause where the pause began (measured: 0.3-0.5 s early on the stem, up to 4 s on the mix), so a word whose start is silent on the stem moves to where the voice comes in (`snap_to_voice`), and words Whisper missed are spread over the sung frames between their neighbours (`voiced_frames`), not over the silence. Files carry `sync_version` (`SYNC_VERSION`, 1.1) and `voice_snapped`; lane 5 re-times any file from an older version while the stem is there, so a remaster also fixes the song's lyric timing. Stems are deleted after each song, so timing outside a render (asset doctor re-aligns) still uses the mix.
- **Background renders:** `process_backlog.py` runs the orchestrator in its own process on the P6000 (`--gpu 0`, the default; the RTX 6000 belongs to the owner's other projects such as DeepPBR, 5 Oct; own log `data/logs/backlog.log`, asset doctor off, intermediates deleted after each song), never inside the live backend. The live backend's catalog watcher (`CATALOG_WATCH_INTERVAL_S`) reloads when masters appear from any process; "Recent" sorts by `catalog_added_at`, then `created_at`.
- `server/utils/relevel_catalog.py` moves masters above the station level down by gain only and re-encodes their Opus/WebM.
- A/B work: measure every render (`scripts/music_lab/proof.py`), compare by beep cycles and difference files, loudness-matched, with raw Suno alongside.
- **Suno decoder fingerprint (measured 6 Oct):** every Suno V5 file carries fixed tones at multiples of 50 Hz and highs that move in step with the decoder's 20 ms frames (960 samples, locked to the file's first sample); plain MP3 and real music show neither. `scripts/music_lab/decoder_lock.py file ...` scores any file or chain stage for it with no training and no reference (raw Suno about 0.14, chance 0.013, after SonicMaster 33% about 0.08). Facts, rejected cancellers and open leads: `docs/HANDOVER_2026-10-06_SUNO_ARTIFACTS.md`.

### 19. Vector Stores - One Pattern (owner's February TTS design, 4 Oct)

**Files:** `server/services/vector_store.py` (`VectorStore`), `server/services/category_store.py` (`CategoryStore`), `server/services_radio/tts_vector_db_service.py` (`ClipStore`), `BackgroundTasksService.vector_store_maintainer`. Test: `tests/vector_store_test.py` (no DB, GPU or LLM).

Every vector engine (TTS clip tables, catalog, shoutouts, local knowledge, news, places, listener requests) is a `VectorStore`:
- **Live:** searches read the live slot plus the items added since it was built (the pending slot).
- **Ingest:** a new or edited item is embedded on its own and searchable at once (`add_meta`, `add_rows`, `add_entries`); a removed item is hidden at once (`remove`). Either marks the store dirty. Shoutout saves, places, listener asks, uploads, edits, the catalog reload and TTS clip saves all go this way.
- **Background:** `vector_store_maintainer` rebuilds every dirty store every `VECTOR_REBUILD_INTERVAL_S` (300): the fresh slot is built in a worker thread into the spare slot (1/2), swapped in under the lock, and the pending items it now holds are dropped. One build at a time per store; reading never waits for it. Bulk writers that don't add items one by one (event and news collectors) only mark the store dirty.
- **Boot:** `load()` restores what is valid (TTS: the Annoy file against the database ids) and builds slot 1 otherwise.
- **Never on a request:** no rebuild, no bulk embedding and no embedding-table writes while someone waits. A query's own text is encoded (`vector_store.query_vector`, memory cache only).
- A new engine subclasses `VectorStore` (or `CategoryStore`) and implements `load_rows`, `embed` and, when it needs its own structures, `build_slot` / `restore`; it never writes its own swap, staleness or rebuild loop.

### 20. Human Music Uploads

**IMPORTANT:** Human uploads use the SAME catalog system as AI tracks. See `docs/HUMAN_MUSIC_UPLOAD.md` for full details.


**AI track credits are compulsory and one name:** an AI track's artist is the artist it was inspired by, and it is shown as the artist. `generation_params.artist_name`, `track_info.artist` and `derived_tags.inspired_artist` always hold the same name (`services/catalog_credit.settle_ai_credit`: the requested artist, then the inspired-by artist, then the artist in the original request, then the first similar artist). Enrichment settles it on every new track, the Asset Doctor repairs any drift, `server/utils/backfill_artist_credit.py` fixes the catalog in bulk (run 3 Oct: 1,340 tracks). Every view reads `generation_params.artist_name`. Audit the catalog with `server/utils/catalog_audit.py`.

**Key Principle:** Human tracks are stored in the same `tracks` table with `is_ai_generated=0`. They use identical metadata schemas, playback systems, and discovery pipelines as AI tracks.

**Credits (least resistance):** uploading is one tap; everything is automatic and editable afterwards. A user's bands are artist profiles they define once (User panel → Artists & Bands); an upload is credited to the chosen profile, else their last-used, else their first, else one made from their username. The credit is `generation_params.artist_name` (= `track_info.artist`, plus `artist_profile_id`/`artist_slug`), the field every view reads; `derived_tags.inspired_artist` is only a "sounds like" comparison and never the credit. Titles: uploader's > embedded tag (mutagen, `embedded_tags`) > a real title in the filename > the sung hook (Gemini is told all of these). Renaming a profile re-credits its tracks (`retag_artist`); edits go through `update_track_metadata` (validated, re-indexed; a lyrics edit drops the lyric timing so the asset doctor re-aligns it). "Enhance audio" (Apollo) is the user's remembered opt-in (`users.upload_enhance`). Uploads, artwork and profile pictures are refused before the body is read without a valid token (`SIGNED_IN_UPLOAD_PATHS`). Fix old uploads with `server/utils/backfill_upload_credits.py`; test with `tests/upload_credit_test.py --file <audio> [--cancel-file <other audio>]` (runs the full pipeline, 2-8 min).

**Rights, sharing, jobs, duplicates:** the first upload asks once for a rights confirmation (`users.upload_rights_confirmed_at`). Tracks go live as `visibility` public; unlisted/private tracks are in `CatalogDatabaseService.hidden_ids` and are left out of `get_all_tracks`, vector search, queue fill (`_get_user_preferences` unions them into bans), top hits, charts, on-air stats and new-session seeding; they still play by id. `explicit` is set by Gemini (editable). Music source: each listener's Both / Human / AI setting (radio pref `music_source`, default both) is applied with bans and hidden tracks by `services/listener_filters.excluded_ids` in queue fill, catalog, search, DJ searches and Pulse; no separate human stations, filters or badges. The upload request returns `{"status": "processing", "upload_id"}` as soon as the file is received; processing runs as a background job (`UPLOAD_JOBS` in `routers/user_music.py`, `GET/DELETE /api/user/music/uploads/{id}`), and its final `upload_progress` event is sent only after the job's result is stored. Every human upload carries a Chromaprint `fingerprint` (ffmpeg's chromaprint muxer, first 120 s, `services/audio_fingerprint.py`); after decoding, a match (similarity ≥ 0.8; the same song measured 0.94-1.0, different songs ≤ 0.55) returns the uploader's existing track or refuses another listener's song. Missing fingerprints are backfilled at startup.

## Settings (one system, `docs/SETTINGS.md`)

Every Settings-panel option is saved per **device kind** (`windows-pc`, `android-phone`, `iphone`, ...; `deviceKind()` in `lib/session.js`, sent as `X-Device-Kind`), never per account and never per device ID (IDs churn: 538 for one account on 4 Oct 2026). The registry is `client/src/lib/settingsSchema.json` (read by client and server); values live in `settingsState` + the local `plair_settings` entry and in `users.device_settings[kind]` (`GET`/`PUT /api/settings`, `SettingsSyncBridge`). Change a setting only through `publishSettings()`; the server reads one only through `services/device_settings_service.py`. Log out resets nothing. Add a setting by adding it to the schema. `user_devices` rows unused for 60 days are pruned at startup.

## State Flow Architecture

**Frontend → Backend:** Component → Context → API → Service → Database → WebSocket Broadcast → All Clients

**Backend → Frontend:** Backend Event → broadcast_playback_state_to_session() → WebSocket → PlaybackContext.handlePlaybackState() → reportEngineStatus() to UIState → UIState auto-preloads artwork → Components Re-render

**Optimistic Updates:** UI updates immediately → API call → Backend broadcasts → Frontend verifies → Revert if mismatch

## File Map

Every `.py`, `.jsx` and `.js` source file with one line on what it does, by folder. A file split into parts keeps the original name as its prefix (`dj_tools.py`, `dj_tools_registry.py`, ...), so the parts sit together; the file without a suffix is the core and says what each part holds. Add a line here for every new file and update the line when a file's job changes.

#### client

- `client/public/sw.js` - Service worker: precaches the build's asset manifest, caches static/dynamic files, never intercepts /api, serves ranges.
- `client/vite.config.js` - Vite config: React plugin, offline asset-manifest plugin, dev server on 3000 proxying /api, /ws, /track.
- `client/lyric-sheet.html` - Dev page (Vite dev server only, never built): every card of a song's lyric style at its final state; `?track=ID` (default The Archive of Cool) and `?aspect=0.46` for a phone held upright.

#### client/src

- `client/src/App.jsx` - Root app: lays out panels, player, canvas, bridges and lazy-loaded modals at root level.
- `client/src/main.jsx` - Entry point: installs error reporter and press feedback, stale-chunk reload guard, mounts providers and App.

#### client/src/components

- `client/src/components/AccountSettings.jsx` - User panel account section: passkeys, optional password, device-link code approval, sign out, delete account.
- `client/src/components/AppBridges.jsx` - Render-nothing bridges: track data loading, media session, offline/connection/upload notices, device link, settings sync.
- `client/src/components/AppErrorBoundary.jsx` - Root error boundary that reports render crashes and shows a "Reload PLAiR" screen.
- `client/src/components/AudioReactiveCanvas.jsx` - Page side of the background scene: starts the scene worker (or the main-thread fallback), posts a state snapshot every frame, sends artwork, lyric words and video frames, relays the light probe, beat, glow and frame stats.
- `client/src/components/AudioUnlockPrompt.jsx` - "Tap to start audio" prompt shown when the browser blocks playback until a tap.
- `client/src/components/BitratePicker.jsx` - Audio quality selector hook, button and panel (auto, 128k, 192k, 256k, data saver).
- `client/src/components/Catalog.jsx` - Track catalog panel: genre cards, virtualised track grid, sorting, play now and seed actions.
- `client/src/components/Conversation.jsx` - DJ conversation view: chat bubbles, internal dialogue, ordered tool activity cards, filters, autoscroll.
- `client/src/components/CostTicker.jsx` - Small fixed overlay showing today's AI usage cost, refreshed every minute.
- `client/src/components/DepthArt.jsx` - Every cover and avatar: one canvas drawn by the shared renderer from the cover's pack (colour | normal x, depth, normal y); 3D Lit Artwork off draws the pack's colour half flat. `TrackArt` / `ProfileArt`, `TrackArtCrossfade` (player and Now Playing) and the renderer config bridge.
- `client/src/components/DevicePicker.jsx` - Multi-device picker hook, button, panel and "Playing on another device" notice with Play here.
- `client/src/components/DJActivity.jsx` - DJ tool activity cards (plan, calls, results) and the bridge that pops tool chips as notices.
- `client/src/components/DJTextComposer.jsx` - Text input box for typing messages to the DJs in text mode.
- `client/src/components/FPSCounter.jsx` - Developer overlay: page and scene frames per second, the screen's refresh rate, frames dropped against it, the worst frame, ms per page frame (main thread, artwork JS and GPU) and ms per scene frame (JS and GPU, worker or main thread).
- `client/src/components/GenerationQueuePanel.jsx` - Panel listing Suno generation jobs with progress, cancel and remove.
- `client/src/components/GestureGuide.jsx` - One-time onboarding overlay for guests showing swipe gestures for play, pause, previous, next.
- `client/src/components/InteractiveEngagementButton.jsx` - Animated canvas-blob engagement button with haptics and visual feedback.
- `client/src/components/KeyboardControls.jsx` - Global keyboard shortcuts for playback, seeking and push-to-talk recording.
- `client/src/components/ListenerTimeline.jsx` - Listener timeline of what aired in the last 24 hours, with kind filters.
- `client/src/components/MediaActions.jsx` - Like, super like, ban (and delete) buttons for tracks and shoutouts.
- `client/src/components/MediaSearch.jsx` - Search box for catalog and shoutouts with AI search toggle and results.
- `client/src/components/MediaSearchMatchBadge.jsx` - Badge showing why a search result matched (category and score).
- `client/src/components/MediaShared.jsx` - Shared media UI: grid columns, spinners, empty/offline states, badges, card animation, search hook.
- `client/src/components/MediaStatsOverlay.jsx` - Catalog header overlay with stats and sort modes that hides while scrolling.
- `client/src/components/Motion.jsx` - Motion building blocks: Expandable, ExpandChevron, ExpandSection, FadeSwap, and Fade / Pop (anything that appears or disappears on screen).
- `client/src/components/Notice.jsx` - NoticeChip pop-up pill and InlineNote status line with tone colours and icons.
- `client/src/components/NoticeStack.jsx` - Renders the UIState notice channel as a stack of NoticeChips at the top.
- `client/src/components/NowPlaying.jsx` - Now playing panel: full-size crossfading cover, synced lyrics, audio features, artist info, reviews, share.
- `client/src/components/OnAirBadge.jsx` - ON AIR notice and frame shown during Radio Mode talk breaks.
- `client/src/components/Panel.jsx` - Generic panel wrapper, header, curved backdrop, panel ids/config and text radio icon.
- `client/src/components/Player.jsx` - Main player bar: artwork, transport controls, waveform progress, talk break progress, device status.
- `client/src/components/Queue.jsx` - Playback queue list, virtualised through `VirtualScroller` like the Catalog (rows of one measured height, the now-playing highlight and the follow-the-current-song scroll computed from the row index, rows fade out only when removed), preference badges and seed/list mode buttons.
- `client/src/components/Radio.jsx` - Radio panel container holding the radio talk button, conversation and timeline.
- `client/src/components/RadioModeSettings.jsx` - Settings for Radio Mode talk breaks and their segment types.
- `client/src/components/RotationVeil.jsx` - Black veil set opaque inside the first resize/orientation event of a real screen rotation (so the first frame at the new size is black) and faded back once resizing has stopped and frames flow again (4 under 50 ms), at most 1.5 s.
- `client/src/components/Scroller.jsx` - Custom scroll container with edge fades, scroll label and haptic feedback.
- `client/src/components/SettingRow.jsx` - Small settings layout pieces: SettingRow and ToggleChip.
- `client/src/components/Shoutouts.jsx` - Shoutouts panel: category cards and shoutout cards with playback and delete.
- `client/src/components/User.jsx` - User panel: profile, settings, devices, preferences lists, uploads, artist profiles, own posts.
- `client/src/components/VirtualScroller.jsx` - The one virtual list: renders only the visible rows of a long list and prefetches their covers. Fixed rows or grids by `itemHeight` (Catalog, Queue), or rows of any height measured as they render (`itemKey` + `estimatedItemHeight`, grids too: Radio feed with `stickToEnd`, Timeline, User panel lists, Shoutouts grid); rows above the view that change height keep the content still. `itemClassName` styles each measured row (spacing goes inside the row), `wrapItems` wraps the rendered rows (the Queue's presence animations).
- `client/src/components/VisualErrorBoundary.jsx` - Error boundary for visual parts that logs and renders a fallback.

#### client/src/components/Auth

- `client/src/components/Auth/DeviceLinkQR.jsx` - Shows a sign-in QR code and code for linking this device from a signed-in phone.
- `client/src/components/Auth/Login.jsx` - Sign-in form: passkey first, optional password, QR device link.
- `client/src/components/Auth/Register.jsx` - Sign-up form creating an account with a passkey or optional password.

#### client/src/components/modals

- `client/src/components/modals/CompatibilityWarningModal.jsx` - Warning modal about limited support on iOS or Safari devices.
- `client/src/components/modals/ConfirmationModal.jsx` - Confirm, alert and prompt dialog rendered for DialogContext.
- `client/src/components/modals/DemoModeModal.jsx` - Shows the demo mode info markdown rendered as styled JSX.
- `client/src/components/modals/GenerationModal.jsx` - Choose remix or similar-artist Suno generation jobs for a track.
- `client/src/components/modals/ListModal.jsx` - Pick a playlist station: your music (favorites, discovery) or charts (top hits).
- `client/src/components/modals/Modal.jsx` - Base modal with artwork blur, category gradient, glow border, plus shared modal parts.
- `client/src/components/modals/ReviewModal.jsx` - Write or record a review of a track, with editor verdict feedback.
- `client/src/components/modals/SeedRadioModal.jsx` - Pick a seed mode (genre, mood, artist...) to start radio from a track.
- `client/src/components/modals/ShareModal.jsx` - Share a track by link or exported music video, with download and copy.
- `client/src/components/modals/ShoutoutModal.jsx` - Shoutout playback modal with transcript, analytics, replies and reply recording.
- `client/src/components/modals/UploadMusicModal.jsx` - Human music upload and edit: drag/drop, rights, credits, analysis progress, metadata preview.
- `client/src/components/modals/UploadMusicModalFields.jsx` - The upload modal's editable fields: metadata and tag editors, visibility, description, artist chooser, quality badge, artwork, audio features.
- `client/src/components/modals/UsageStatsModal.jsx` - Admin view of AI usage and costs by scope and period.

#### client/src/contexts

- `client/src/contexts/AuthContext.jsx` - Authentication state: user, token, login/logout, refresh, session info handling.
- `client/src/contexts/DialogContext.jsx` - Promise-based confirm, alert and prompt dialogs via showConfirm/showAlert/showPrompt.
- `client/src/contexts/DynamicThemeContext.jsx` - Track-driven theme colours, category metadata, panel icons, interaction effects, theme artwork.
- `client/src/contexts/GenerationQueueContext.jsx` - Suno generation job queue state, status updates and actions.
- `client/src/contexts/NetworkContext.jsx` - Network status: server health probes, connection mode, speed test, bitrate selection. Without `navigator.connection` (Safari/iOS/Firefox) the speed test times a 516 KB `Range: bytes=0-` download of `/images/plair_icon.png`, which the service worker never caches.
- `client/src/contexts/PlaybackContext.jsx` - Music playback engine: server state sync, device enforcement, offline local mode, playback actions.
- `client/src/contexts/PlaybackShoutoutContext.jsx` - Shoutout audio playback engine with progress, reporting to UIState.
- `client/src/contexts/PreferencesContext.jsx` - Track and shoutout likes, super likes and bans with optimistic updates.
- `client/src/contexts/QualityContext.jsx` - The visual quality level (Low / Medium / High, or Auto adapting between them against the screen's refresh rate): scene DPR, glass taps, parallax detail; never frame rate. Also the reduced-motion policy.
- `client/src/contexts/StorageContext.jsx` - Offline storage usage, quota and data usage info with refresh trigger.
- `client/src/contexts/UIStateContext.jsx` - SSOT for UI state: engine status, visual state, modals, notices, settings, current/next cover pack preloading.
- `client/src/contexts/ViewportContext.jsx` - Responsive breakpoints, scale, device detection and root font size.
- `client/src/contexts/VoiceRecordingContext.jsx` - Microphone recording engine provider for voice turns and shoutouts.
- `client/src/contexts/WebSocketContext.jsx` - WebSocket client: auth handshake, ping/pong health, reconnect, outbox, subscribe and emit hooks.

#### client/src/hooks

- `client/src/hooks/useArtPop.js` - Plays a pop animation on artwork when its id changes.
- `client/src/hooks/useAudio.js` - Creates the AudioEngine and mixer and publishes audio state to UIState.
- `client/src/hooks/useDeletePost.js` - Confirm-and-delete flow for the listener's own shoutouts, replies and reviews.
- `client/src/hooks/useDepthMap.js` - Loads a depth or normal map URL from a media cache, with retry backoff when missing.
- `client/src/hooks/useDeviceLinkApproval.js` - Approves a device-link code so another device signs in as this user.
- `client/src/hooks/useDeviceSelector.js` - Lists microphones and speakers and stores the selected ones.
- `client/src/hooks/useDJAudioStream.js` - DJVoiceEngine: plays DJ voice streams, speaker colours and FFT, device-aware. Mounted once at the App root as `<DJVoiceEngine />`, never inside a panel, so collapsing panels cannot cut the DJ.
- `client/src/hooks/useEntranceWindow.js` - Returns true for a short window after a key changes, to run entrance animations.
- `client/src/hooks/useFFTProcessor.js` - Shared FFT analysis loop for DJ, voice and shoutout audio, reported to UIState.
- `client/src/hooks/useGeolocation.js` - Listener location: permission, position updates, sending location to the server.
- `client/src/hooks/usePointerInteraction.js` - Tells taps from drags by tracking pointer movement against a threshold.
- `client/src/hooks/useProfilePicture.js` - Loads a user's profile picture URL through the media cache.
- `client/src/hooks/useUISound.js` - Plays interface sounds (record press, broadcast, errors) respecting notification mute.
- `client/src/hooks/useVirtualWindow.js` - Paged data window for virtual lists: fetches pages on demand with retries and limits.
- `client/src/hooks/useVoiceRecorder.js` - Microphone capture with MediaRecorder, audio session switching and level analysis.

#### client/src/lib

- `client/src/lib/api.js` - API client core: requests, auth token, offline routing via `_routeRequest`, connectivity events, offline write replay; assigns the method groups of its parts onto the `API` class. Every backend call goes through it, never a direct `fetch()`.
- `client/src/lib/apiAccount.js` - API part: sign-up and sign-in (passwords, passkeys, device links), account, device settings, likes and bans, profile picture, billing, usage.
- `client/src/lib/apiCommunity.js` - API part: shoutouts, replies, reviews and their stats.
- `client/src/lib/apiMusic.js` - API part: catalog, queue, search, stream and artwork URLs, track data, song generation, uploads, artist profiles, share videos.
- `client/src/lib/apiStation.js` - API part: devices, DJ conversation and timeline, transcription, Radio Mode, music beds.
- `client/src/lib/artworkPrefetcher.js` - Loads cover packs ahead of the virtual scroller in both directions and has the renderer prepare the nearest ones on the GPU, so covers never visibly load.
- `client/src/lib/audioEngine.js` - Dual-slot audio engine: A/B crossfades, gain ramps, streaming, device enforcement, iOS unlock. All gain automation goes through `_rampGain()`.
- `client/src/lib/audioInteractionManager.js` - Unlocks audio on user gestures: resumes contexts, runs hooks, primes media elements.
- `client/src/lib/audioMixer.js` - Ducks and restores the music level under the DJ via the engine's gain ramps.
- `client/src/lib/backgroundDownloader.js` - Background download queue for offline tracks with daily limits and slow-connection backoff.
- `client/src/lib/cacheManager.js` - Offline library manager: download tracks with artwork and data, quota, cleanup, track list.
- `client/src/lib/cacheValidator.js` - Validates track data and blobs before they are stored offline.
- `client/src/lib/depthArtRenderer.js` - Shared WebGL renderer drawing every cover pack into its canvas: textures sized to the tile, prepared (warm) covers, flat mode when lit is off, own visibility check, per-tile parallax cache for light-only frames, one atlas snapshot per frame; `__plairArt.stats()` for texture accounting.
- `client/src/lib/depthArtShader.js` - Depth artwork shaders: full pass (parallax march + lighting), parallax-to-cache and re-light-from-cache passes, the max-depth pyramid that lets the march skip steps that cannot hit, light uniforms and the cached viewport size.
- `client/src/lib/djBroadcastChain.js` - Web Audio processing chain for DJ voice playback (compression, gain ramps).
- `client/src/lib/djStreamPlayer.js` - DJ voice stream player: MediaSource per stream, sequential queue, cancel, stall watchdog.
- `client/src/lib/frameStats.js` - Per-frame work totals for the FPS counter (main thread, scene JS) and GPU timer queries on the scene and artwork contexts, only while the counter is on.
- `client/src/lib/errorReporter.js` - Collects log breadcrumbs and sends client errors and events to the server.
- `client/src/lib/haptics.js` - Triggers device vibration patterns for haptic feedback.
- `client/src/lib/lightProbe.js` - Turns the background's 16x16 light grid into tracked lights for depth artwork; says when a probe is worth reading (lit art on screen, light up, no panel resizing).
- `client/src/lib/backgroundProbe.js` - Computes the 16x16 light probe on the CPU from small copies of the artwork and lyric word plus the background's own transform and colour uniforms (no GPU readback; Chrome's readback stalled the main thread behind every queued frame).
- `client/src/lib/logger.js` - Logger with levels and a sink hook used by the error reporter.
- `client/src/lib/lyricFonts.js` - Registers a styled song's lyric fonts as FontFaces (only the subsets its words need) and builds canvas font strings.
- `client/src/lib/lyricFonts.json` - The lyric font list (id, family, weights, italic, a line on its character for the design prompt, woff2 files), read by client and server.
- `client/src/lib/lyricStyle.js` - Styled lyrics: builds cards from a song's lyric style, finds the card and sung words at a moment, lays cards out (stack, flow, cascade, solo) in the screen's aspect and draws them.
- `client/src/lib/mediaCache.js` - Multi-layer media cache (memory, IndexedDB, Cache API) for cover packs (tile and full size) and profile pictures; deletes retired caches.
- `client/src/lib/packImage.js` - Decodes cover packs (colour and map halves) in `packDecodeWorker`, and makes the pack's colour half into a JPEG URL for the lock screen and modal blurs.
- `client/src/lib/packDecodeWorker.js` - Worker that decodes, cuts and resizes cover packs off the main thread.
- `client/src/lib/mediaSupport.js` - Detects MSE and WebM/Opus support and picks streaming and download formats.
- `client/src/lib/microMotion.js` - Web Animations micro-interactions: press pop, nope, burst, art pop, arrival glow.
- `client/src/lib/motion.js` - Motion tokens: durations, easings, springs, variants, presets and CSS transitions.
- `client/src/lib/musicBed.js` - Plays and fades music beds under Radio Mode talk breaks.
- `client/src/lib/offlineAPI.js` - Offline backend core: the local radio session and queue on the downloads; assigns its parts' method groups onto `OfflineBackend`.
- `client/src/lib/offlineAPILibrary.js` - Offline part: the downloads library (tracks, stats, genres, search, cache validation, features, lyric timing).
- `client/src/lib/offlineAPIUnavailable.js` - Offline part: every call that needs a connection, answered with an empty result or a friendly error.
- `client/src/lib/offlineAPIWrites.js` - Offline part: likes, bans and profile changes queued for replay when back online, cached user.
- `client/src/lib/offlineStorage.js` - IndexedDB store for downloaded tracks and metadata normalisation.
- `client/src/lib/offlineVideoRenderer.js` - Renders share videos frame by frame with the shared shaders and encodes MP4.
- `client/src/lib/sceneEffects.js` - Beat and energy effect functions and lyric helpers shared by the live scene and the share-video renderer.
- `client/src/lib/sceneRenderer.js` - The background scene: three.js setup, per-frame effects, glass panels, capture pass, light probe, render-on-change signature; runs in the worker or on the main thread.
- `client/src/lib/sceneShaders.js` - The background, glass scene and fullscreen backdrop shaders.
- `client/src/lib/sceneWorker.js` - Web Worker entry that hosts `SceneRenderer` on an OffscreenCanvas.
- `client/src/lib/panelReveal.js` - Panel slide reveal: fixes open panels' contents at their final width during a slide (no per-frame reflow), switched on automatically after janky slides (`plair_panel_reveal` = on/off overrides).
- `client/src/lib/passkeys.js` - WebAuthn passkey helpers: login, sign-up, add and silent upgrade.
- `client/src/lib/playbackSync.js` - Pure helpers ordering server playback snapshots against pending commands and acks.
- `client/src/lib/renderPause.js` - Briefly pauses scene rendering while modals open or close.
- `client/src/lib/retryUtils.js` - Retry with exponential backoff for API calls.
- `client/src/lib/safeStorage.js` - Safe localStorage wrapper with in-memory fallback. ALL localStorage access goes through `safeStorage.get/set/remove` (iOS private browsing throws on raw localStorage).
- `client/src/lib/screenRefresh.js` - Measures the screen's refresh interval from rAF timestamps (fastest confirmed interval over the last 5 s), shared by the quality governor and the FPS counter.
- `client/src/lib/session.js` - Device id generation, device name/kind detection and session ids.
- `client/src/lib/settings.js` - Local settings schema: validate, load, save and notify, migrating legacy keys.
- `client/src/lib/soundModes.js` - The four DJ sound modes over TTS and notification mute settings.
- `client/src/lib/splash.js` - Hides the startup splash once scene and playback are ready, 4 s at most.
- `client/src/lib/talkBreak.js` - TalkBreakController: runs Radio Mode talk breaks: hold the music, play bed and stream, progress, resume.
- `client/src/lib/textRenderer.js` - Canvas text renderer turning lyric words into textures for the shader.
- `client/src/lib/themeManager.js` - Extracts artwork colours and holds panel, transition, fade and category colour constants.
- `client/src/lib/uploadJobs.js` - Helpers for polling background upload jobs until they finish.
- `client/src/lib/usageFormat.js` - Formats usage costs, counts and durations for the usage views.
- `client/src/lib/utils.js` - Small helpers: duration and date formatting, blob to base64, WebGL2 check.

#### server

- `server/app.py` - FastAPI app: lifespan that starts and stops every service, middleware and router includes.
- `server/models_global.py` - Shared torch device, cached sentence encoders, GPU executor, GPU lease lock and CUDA out-of-memory helpers.
- `server/run_migration.py` - Adds columns missing from PostgreSQL tables after models.py changes (users, device settings).
- `server/security_middleware.py` - Request guard (traversal, guest ids, body limits), media-aware gzip, secret redaction and quiet access logs.
- `server/service_registry.py` - The `services` registry the lifespan fills and routers read at request time.
- `server/start.py` - Backend entry point: loads .env, disables Windows Quick Edit, sets event loop, runs uvicorn.
- `server/usage_middleware.py` - Attributes paid AI calls in each request to a user, guest or system scope.

#### server/config

- `server/config/__init__.py` - Re-exports `settings` and `Settings` from config.settings.
- `server/config/settings.py` - All configuration from .env: databases, paths, LLM chains, TTS, City Pulse, stings and limits.

#### server/database

- `server/database/__init__.py` - Re-exports the async engine, session factory, get_db, init_db and core models.
- `server/database/connection.py` - Async SQLAlchemy engine and session for ai_radio, `get_db` dependency and table creation.
- `server/database/models.py` - SQLAlchemy models: users, passkeys, devices, preferences, conversations, play events, usage, regional and place caches.
- `server/database/pg_pool.py` - Bounded psycopg2 connection pools per database that block when full instead of overflowing.

#### server/routers

- `server/routers/__init__.py` - Empty package marker.
- `server/routers/account.py` - Passkey sign-up and sign-in, passkey management, password setting, QR device linking and account deletion.
- `server/routers/analytics.py` - Top hits, per-track and per-shoutout analytics, and logging shoutout plays.
- `server/routers/artists.py` - Artist/band profiles CRUD and the upload setup info for a signed-in user.
- `server/routers/auth.py` - Password register and login, token refresh, current user, username change and user data management.
- `server/routers/catalog.py` - Catalog track listing, stats, genres, single track JSON and the public track page.
- `server/routers/client_log.py` - Receives scrubbed, rate-limited client error reports and writes them to client.jsonl.
- `server/routers/conversation.py` - Listener timeline (last 24 h aired) and DJ conversation history read and save.
- `server/routers/deps.py` - Shared dependencies: session info, current user, client IP, rate limits, limited upload reads, admin check.
- `server/routers/devices.py` - List, activate, rename and remove a user's playback devices.
- `server/routers/dj.py` - Speech transcription and typed DJ talk endpoints.
- `server/routers/generation.py` - Start, list, check and cancel Suno music generation jobs.
- `server/routers/media.py` - Streams tracks (MP3/Opus/WebM), cover packs, the plain artwork (link previews only), audio features, video clips, lyric timing.
- `server/routers/playback.py` - REST playback controls: play, pause, stop, seek, queue add/remove and seed radio.
- `server/routers/preferences.py` - Set, clear and list a listener's track likes, super likes and bans.
- `server/routers/radio.py` - Read and update Radio Mode settings and serve music beds.
- `server/routers/schemas.py` - Pydantic request models shared by the routers.
- `server/routers/search.py` - Semantic track search endpoint for the app's search box.
- `server/routers/settings.py` - Get and save a signed-in user's settings per device kind.
- `server/routers/share.py` - Upload shared music videos and serve their MP4 and public share page.
- `server/routers/shoutouts.py` - Shoutouts, replies and reviews: fetch, search, audio, typed or recorded saves, preferences, delete.
- `server/routers/system.py` - Health check and admin Asset Doctor report and scan trigger.
- `server/routers/usage.py` - Admin AI usage and cost summaries per user, plus the caller's own usage.
- `server/routers/user.py` - User profile read and update, and profile picture upload, serving and delete.
- `server/routers/user_music.py` - Human music uploads as background jobs, uploader track edits and deletes, track artwork.
- `server/routers/ws.py` - Playback WebSocket: handshake auth, playback commands, guest location, announcer worker.

#### server/utils

- `server/utils/backfill_artist_credit.py` - Gives every AI track one artist name across all credit fields; dry run unless --apply.
- `server/utils/backfill_upload_credits.py` - Fixes artist credits and titles on one uploader's human tracks; dry run unless --apply.
- `server/utils/backfill_video_search_terms.py` - Fills missing video_search_terms on tracks in batched DeepSeek calls.
- `server/utils/backfill_vocals.py` - Sets derived_tags.vocals (who sings) on tracks via batched LLM calls on text.
- `server/utils/bake_normal_maps.py` - Bakes missing artwork normal maps for every catalog cover on the GPU.
- `server/utils/batch_music_generate_and_repair.py` - Interactive menu: catalog check and repair, Suno generation, variants, artwork enrichment and upscaling.
- `server/utils/build_breath_library.py` - Builds the offline breath library from clustered DJ lines, cutting breaths between rendered sentences.
- `server/utils/build_lyric_styles.py` - Designs lyric styles with one DeepSeek call per song: chosen tracks (`--track`, prints the cards) or every song without one (`--missing`, liked first, dry run unless `--apply`).
- `server/utils/catalog_audit.py` - Audits catalog metadata for missing titles, artists, credits and malformed fields.
- `server/utils/find_dead_functions.py` - Finds Python functions that are never called, aware of routes and node registrations.
- `server/utils/generate_radio_drops.py` - Makes radio drop candidates: DeepSeek prompts, Stable Audio renders, pick page, install and relevel.
- `server/utils/process_backlog.py` - Renders Suno tracks without masters (or stale chain versions, `--rerender`; one song with `--track ID`) on a chosen GPU, own process.
- `server/utils/relevel_catalog.py` - Turns masters louder than the station level down by gain and re-encodes Opus/WebM.
- `server/utils/reprocess_catalog_audio.py` - Resumable re-process of catalog audio through the mastering chain, with A/B, verify, swap and restore.
- `server/utils/upscale_voice_cache.py` - Upscales cached voice takes (sentences, paralanguage, breaths) to 48 kHz in place, keeping tags.

#### tests

- `tests/account_test.py` - Passkey sign-up/in, device link, password, typed post, account deletion on a running backend; no LLM per docstring.
- `tests/catalog_ways_probe.py` - Probes catalog name lookup by spelling and blended-aspect stations; no backend, no LLM.
- `tests/community_test.py` - Shoutouts, replies, reviews end to end (typed, spoken, sting, no coordinates); calls LLMs via the editor/analysis.
- `tests/crossfade_probe.py` - Prints crossfade plans for random catalog pairs; no server, no LLM.
- `tests/device_settings_test.py` - Checks per-device-kind settings stay independent and survive a fresh login; no LLM.
- `tests/dj_find_test.py` - Runs whole DJ music-request turns with faked playback, checks the artist played; Gemini, 2-4 calls/turn.
- `tests/dj_followup_replay.py` - Replays recorded DJ follow-up rounds with different studio messages, classifies how replies open; calls Gemini.
- `tests/dj_pulse_test.py` - Live DJ turns over WebSocket as an Auckland guest (or signed in), checks radio.log; calls Gemini.
- `tests/dj_tool_loop_test.py` - Checks the DJ tool loop drops echoed lines and calls with a stubbed Gemini; no real LLM.
- `tests/event_harvest_probe.py` - Crawls for events in one city without saving; DeepSeek reads a few pages without structured data.
- `tests/for_you_probe.py` - Builds one Radio Mode For You feature for a located guest or user; calls the DeepSeek agent.
- `tests/perf_depth_art.mjs` - Frame-time benchmark of depth/light artwork on a simulated low-end phone in headless Chrome; no LLM.
- `tests/gpu_bench/perf/` - Phone performance scripts over USB (interleaved A/B under simulated tilt, slope method, React re-render roots, blank-cover classes, soak for context loss); `docs/NEXT_SESSION_PERFORMANCE.md` explains them.
- `tests/gpu_bench/` - Headless Chrome pinned to the P6000: per-pass GPU timing, traces, panel-toggle frames and pixel-identity checks against an older shader (`README.md`); no LLM.
- `tests/pulse_links_test.py` - Checks City Pulse links between gigs, places, news and shoutouts on fake sources; no LLM.
- `tests/queue_rules_test.py` - Checks queue rules (picks, play next, seeding, playlist switch) on a fake catalog; no LLM.
- `tests/seed_probe.py` - Probes whether each seed radio mode holds its thread song after song; no backend, no LLM.
- `tests/semantic_probe.py` - Semantic City Pulse search probe with no DJ LLM; Gemini only with --ai on cache miss.
- `tests/smoke_test.py` - End-to-end smoke test: health, catalog, streaming, search, TTS, guest DJ turn; Gemini unless --skip-dj.
- `tests/track_search_probe.py` - Reports where the intended artist ranks for clue-style track searches; small Gemini calls unless --no-ai.
- `tests/tts_engine_bakeoff.py` - Bake-off of TTS engines over HTTP, scored with Whisper and loudness plus concurrency; no LLM.
- `tests/tts_voice_bakeoff.py` - Renders test lines in candidate TTS voices and scores them with Whisper; no LLM.
- `tests/upload_credit_test.py` - Music upload end to end: artist credit, edits, profiles, cancel; full pipeline incl. Gemini analysis.
- `tests/upload_llm_ab.py` - A/B of Gemini upload analysis models on real human uploads, with cost and latency; calls Gemini.
- `tests/vector_store_test.py` - Checks the shared VectorStore build, add, remove and slot-swap rebuild on a fake store; no LLM.

#### scripts

- `scripts/check-quality.ps1` - Offline quality gate: Python syntax, Ruff, Vulture, ESLint, UI-state check, motion check, Knip and build.
- `client/scripts/check-motion.mjs` - Fails when a framer-animated element carries `ui-press`/`ui-tap`/`ui-hover` or Tailwind `transition*` classes (`npm run check:motion`).
- `scripts/voice_restoration/README.md` - Notes on parked offline voice restoration models (Resemble Enhance, Sidon) versus the live CVSR.
- `scripts/voice_restoration/resemble_enhance_dir.py` - Runs Resemble Enhance over a folder of voice takes offline (parked experiment).
- `scripts/voice_restoration/sidon_dir.py` - Runs the Sidon speech restoration model over a folder of voice takes offline (parked experiment).

#### external_components

- `external_components/plair_start.bat` - PLAiR Start: stops old backend, builds frontend, reloads nginx, launches backend window, runs smoke test.
- `external_components/plair_stop.ps1` - Stops only PLAiR's backend and TTS engine by command line and ports, windows included.

#### server/services

- `server/services/__init__.py` - Package marker for the services module; holds no code.
- `server/services/account_deletion_service.py` - Deletes an account: closes billing and live session, removes uploads, posts, share videos, rows and user files.
- `server/services/ai_service.py` - Gemini wrapper: plain, structured and tool calls, plus the DJ's Gemini and DeepSeek tool-turn loops.
- `server/services/analytics_file_service.py` - Writes per-track analytics JSON and appends daily event logs under the analytics directory.
- `server/services/analytics_service.py` - Buffers play events into play_events, scores popularity, serves top hits and track/shoutout stats.
- `server/services/api_utils.py` - One helper that reduces a track record to the short summary fields API responses use.
- `server/services/artist_profile_service.py` - Listeners' artist/band profiles: create, edit, delete, list, slugs, and picking the profile an upload is credited to.
- `server/services/artwork_generation_service.py` - Generates and upscales track artwork with a lazily loaded Stable Diffusion XL pipeline, unloaded when idle.
- `server/services/artwork_thumbnail_service.py` - Renders cover packs (colour | normal x, depth, normal y; flat maps until depth and normals exist) on demand, and backfills every missing or stale pack at startup.
- `server/services/asset_integrity_service.py` - The asset doctor: schedules scans of tracks and shoutouts, keeps state and reports, runs repairs.
- `server/services/asset_integrity_service_checks.py` - Asset doctor's check definitions, finding and subject types, and file/duration probes they use.
- `server/services/asset_integrity_service_detect.py` - Asset doctor detection: probes each track's and shoutout's files, metadata, DB flags and vector index entry.
- `server/services/asset_integrity_service_repair.py` - Asset doctor repairs: per-check fixes, repair gating (GPU, busy, back-off) and quarantine.
- `server/services/audio_apollo_service.py` - Apollo bandwidth restoration on a full mix, chunked on GPU, keeping the source below its own cutoff; base for the vocal Apollo.
- `server/services/audio_clearvoice_service.py` - Re-exports ClearVoice, patched to use the current CUDA device instead of picking a free GPU.
- `server/services/audio_demucs_service.py` - Demucs stem separation (vocals, drums, bass, other) for the processing pipeline.
- `server/services/audio_features_service.py` - Analyses a track: tempo, beats, key, loudness, sections, energy-style features, crossfade points, announcer safe zones.
- `server/services/audio_fingerprint.py` - Chromaprint fingerprints of uploads via ffmpeg, encoding and similarity scoring for duplicate detection.
- `server/services/audio_flashsr_service.py` - Optional FlashSR bandwidth stage: super-resolves audio above its detected cutoff and merges bands back.
- `server/services/audio_headroom.py` - Shared DSP helpers: true-peak measurement and limiting, dithered PCM16 writing, spectrally balanced stem remixing, source-cutoff detection and the restore crossover.
- `server/services/audio_lyrical_timestamp_service.py` - Aligns lyrics to the vocal with Whisper word timings, moves word starts that fall in silence on the vocal stem to where the voice comes in, places missed words on the singing, and writes per-line/word lyric timestamps.
- `server/services/audio_master_service.py` - Corrective EQ (rumble cut, resonance notches), the ±3 dB reference EQ to the modern-master average, and the final loudness leveler.
- `server/services/audio_quality_score_service.py` - Audiobox Aesthetics scorer: rates audio quality on four axes and compares renders, loaded on demand.
- `server/services/audio_roformer_service.py` - Mel-Band RoFormer vocal/music separation on GPU, windowed, unloaded when idle.
- `server/services/audio_sonic_master_service.py` - SonicMaster generative mastering applied as a partial wet mix with RMS matching.
- `server/services/audio_stage_registry.py` - Picks and builds the configured bandwidth, separation and quality-scorer stages; stem directory paths.
- `server/services/audio_transcoding_service.py` - ffmpeg transcoding: Opus/WebM bitrate variants, MP3 conversion, WAV extraction and media probing.
- `server/services/audio_vocal_enhance_service.py` - Lew's vocal Apollo: the Apollo service with the vocal checkpoint, run on separated vocals.
- `server/services/auth_service.py` - Password hashing and checks, JWT creation and decoding, user registration and login lookups.
- `server/services/base_prompt_cache_service.py` - Base for search query-intent caches: LLM works out category weights and filters, reused by SemanticCache.
- `server/services/base_service.py` - SingletonService base class that gives each service one shared instance.
- `server/services/catalog_aspects.py` - Builds stations from song aspects: scores every song per aspect (meaning, tags, artist spelling) and blends.
- `server/services/catalog_credit.py` - Settles an AI track's single artist credit across all artist fields and lists credited artists.
- `server/services/catalog_database_service.py` - Track catalog: scans metadata into memory and Postgres, flags, hidden tracks, and watches for new masters.
- `server/services/catalog_names.py` - Artist and title lookup by spelling using character n-gram vectors, tolerant of typos and accents.
- `server/services/catalog_vector_database_service.py` - Catalog's category store: per-aspect texts, tags and weights per track, and building search slots.
- `server/services/catalog_vector_search_prompt_cache_service.py` - Catalog search intent prompts: category weights and filters (vocals etc.) the LLM derives from a query.
- `server/services/catalog_vector_search_service.py` - Catalog search entry point: free-text weighted search, name lookup and aspect stations, with exclusions.
- `server/services/catalog_vocals.py` - Reads who sings a track (instrumental, male, female, duet, unknown) from tags or generation settings.
- `server/services/category_store.py` - Generic store of items described by named categories, embedded per category and ranked by weighted similarity.
- `server/services/community_engagement.py` - Shoutout engagement: popularity scores, bans and burying, airable ranking, top replies and what aired per listener.
- `server/services/device_link_service.py` - In-memory QR/code device linking: a new device shows a code, a signed-in device approves it.
- `server/services/device_management_service.py` - Registers, renames, removes, deduplicates and prunes a user's playback devices in the database.
- `server/services/device_settings_service.py` - Per-device-kind app settings validated against the shared client settings schema, with defaults.
- `server/services/embedded_artwork_service.py` - Extracts cover art embedded in uploaded MP3, FLAC, M4A, OGG or WAV files and saves it.
- `server/services/gemini_cache.py` - Manages explicit Gemini context caches for fixed system prompts: create, find, renew and invalidate.
- `server/services/http_client.py` - Shared httpx client with retries and a per-host circuit breaker for outbound requests.
- `server/services/human_metadata_extraction_service.py` - Gemini audio analysis of human uploads into catalog metadata (genre, mood, lyrics, mix decisions, artwork prompt).
- `server/services/human_music_upload_service.py` - Human upload pipeline: validation, duplicate fingerprint check, stage planning, processing and rollback on failure.
- `server/services/human_music_upload_service_common.py` - Upload shared bits: limits, value cleaning, embedded tag reading, upload exceptions and progress events.
- `server/services/human_music_upload_service_lanes.py` - Upload processing lanes: Gemini metadata, loudness, audio lane (split, enhance, master) and artwork lane.
- `server/services/human_music_upload_service_tracks.py` - A listener's uploaded tracks: list, delete, edit metadata and re-credit to an artist profile.
- `server/services/listener_filters.py` - Track ids a listener must not get (bans, hidden, music source) and favorites/super-like search scopes.
- `server/services/listener_plays.py` - Per-listener play, listen and skip counts, the love score from ratings, and weighted favorite picks.
- `server/services/listener_request_service.py` - Stores listeners' City Pulse asks with their answers and indexes them for semantic search.
- `server/services/listener_timeline.py` - What aired for a listener (tracks, posts, hosts' talk) over a time window; records voiced talk.
- `server/services/llm_result_cache.py` - Keyed, TTL-limited cache of LLM results, optionally persisted to a JSON file per namespace.
- `server/services/llm_router.py` - LLM role chains: Gemini and DeepSeek calls with fallback, circuit breaker, thinking budgets, structured JSON parsing.
- `server/services/llm_telemetry.py` - LLM price table, token usage and cost estimates, cache savings, errors and fallbacks, periodic aggregate logs.
- `server/services/log_service.py` - Central logging: categories, file rotation, verbose details, throttled warnings, listener and track labels.
- `server/services/lyric_style_service.py` - Lyric styles: the DeepSeek design prompt, checking and saving its cards, attaching a style to the lyric timing response while the words match, the background refresh after every new timing and the catalog backfill.
- `server/services/media_streaming_service.py` - Streams media files with HTTP range parsing and picks the bitrate variant to serve.
- `server/services/normal_map_service.py` - Bakes normal maps from artwork plus depth for track and profile images used by lit depth art.
- `server/services/opengraph_service.py` - Renders track share pages with Open Graph and Twitter meta tags injected into the app's HTML.
- `server/services/passkey_service.py` - WebAuthn passkeys: sign-up, add, sign-in options and verification, listing and removal, username checks.
- `server/services/playback_lists.py` - Fills list stations: favorites weighted by listening, discovery (half favorites, half new nearby songs) and top hits.
- `server/services/playback_population_service.py` - Fills a station's queue from lists or aspect-built stations matched to the seed and recent songs.
- `server/services/playback_service.py` - Holds per-session playback states: transport commands, broadcasts, idle eviction and periodic snapshot saving.
- `server/services/playback_snapshots.py` - Saves, loads, restores and prunes each session's station snapshot in the playback_snapshots table.
- `server/services/playback_state.py` - Per-session playback state machine: current track, play/pause/seek/next, acknowledgements, play events, transitions.
- `server/services/playback_state_devices.py` - Playback state device handling: connects, disconnects, active device, transfers, claims and claim-on-open.
- `server/services/playback_state_queue.py` - Playback state queue: history, upcoming picks, station auto-fill, queue size limits, adding and removing tracks.
- `server/services/playback_state_stations.py` - Playback state station switching: seeding a list or aspect station from a song or words.
- `server/services/preferences_service.py` - Track and shoutout likes, super likes and bans, plus reading and applying Radio Mode settings.
- `server/services/profile_picture_service.py` - Uploads, serves and deletes profile pictures, with depth and normal map renders.
- `server/services/rate_limit_service.py` - Token-bucket rate limits and per-user Suno generation quotas with reserve and refund.
- `server/services/semantic_cache.py` - Reuses earlier answers by exact text, then closest meaning above a threshold; backs search-intent and Producer caches.
- `server/services/semantic_source.py` - Shared semantic source pattern: category specs, base vector database class, and per-query re-weighted search.
- `server/services/source_quality_analysis_service.py` - Grades an upload's source quality (format, bit depth, spectrum, dynamics) and decides whether enhancement applies.
- `server/services/suno_artwork_enrichment_service.py` - Makes depth maps for artwork and writes side-by-side colour plus depth images for parallax.
- `server/services/suno_enriched_metadata_service.py` - LLM enrichment of track metadata: derived tags and canonical style, saved into the metadata.
- `server/services/suno_generation_queue_service.py` - Suno generation jobs: submit batches, track credits, assign ids, refunds, cancellation and job status.
- `server/services/suno_generation_queue_service_inflight.py` - Records Suno generations in flight on disk and resumes or refunds them after a restart.
- `server/services/suno_generation_queue_service_jobs.py` - Suno job model: statuses, limits, progress calculation, status output and friendly error messages.
- `server/services/suno_metadata_service.py` - Creates, saves and loads Suno track metadata JSON, file paths and generation status.
- `server/services/suno_prompt_service.py` - LLM turns a listener's music request into Suno generation parameters.
- `server/services/suno_service.py` - Suno API client: submit tasks, poll status, check credits, download audio and images.
- `server/services/suno_service_orchestrator.py` - Multi-lane processing pipeline from Suno MP3 plus metadata to a mastered catalog entry.
- `server/services/task_utils.py` - Spawns tracked asyncio background tasks and logs their failures.
- `server/services/track_artwork_service.py` - Uploads, deletes, checks and generates a track's artwork image.
- `server/services/track_asset_stages.py` - Per-track asset paths and steps: MP3, Opus variants, features, lyric timing, artwork, master version stamp.
- `server/services/usage_report_service.py` - Builds the admin usage report: costs per period, feature and user, projections, electricity, revenue.
- `server/services/usage_tracking.py` - Attributes paid LLM, API, Suno and GPU usage to user, guest or system and records rollups.
- `server/services/user_content_database_service.py` - Shoutouts, replies and reviews store: files, Postgres rows, creation, enrichment, deletion and public views without coordinates.
- `server/services/user_content_speech_enhancement_service.py` - Turns listener recordings into clean radio audio: enhancement, LLM transcript filter, pause trim, leveling, review stings.
- `server/services/user_content_vector_database_service.py` - Shoutout category store: categories, default weights and texts embedded for semantic search.
- `server/services/user_content_vector_search_prompt_cache_service.py` - Shoutout search intent prompts: category weights the LLM derives from a query.
- `server/services/user_content_vector_search_service.py` - Semantic search over shoutouts with query intent weights, distance filtering and audio URLs.
- `server/services/user_data_cache_service.py` - In-memory cache of users and their likes and bans, with invalidation tied to DB commits. Auth reads users from it: any write to a `User` row must call `invalidate_user` (as `UserProfileService.update_profile` does), or `/api/auth/me` serves stale settings.
- `server/services/user_profile_service.py` - Updates username and profile, reads profiles, deletes DJ conversations and resets the listener persona.
- `server/services/vector_store.py` - Generic vector store pattern: live plus pending slots, instant add/remove, background rebuilds, shared encoder.
- `server/services/web_fetch.py` - Polite web fetching: per-host pacing, rest periods, rate-limit handling, robots.txt checks and rotating identities.
- `server/services/websocket_service.py` - WebSocket connections per session and device: registration, broadcasting, online devices and stale cleanup.
- `server/services/whisper_dual_service.py` - Two Whisper models for transcription: a fast one and a higher-quality one.
- `server/services/youtube_clip_service.py` - Finds and downloads YouTube background video clips per keyword or track via yt-dlp, with cache limits.

#### server/services_radio

- `server/services_radio/announcer_service.py` - Between-track announcer: watches sessions, analyses transitions, publishes crossfade plans, maps song spaces for talk and stings.
- `server/services_radio/area_air_quality.py` - Area signal for Google Air Quality: parses the API response into an air-quality category per area.
- `server/services_radio/area_geocode.py` - Reverse-geocode area signal: turns coordinates into a cached suburb/area name via Google Geocoding.
- `server/services_radio/area_pollen.py` - Area signal for Google Pollen: parses daily pollen forecasts for the listener's area.
- `server/services_radio/area_signals.py` - Shared framework for area signals: grid cells, area_cache store, Google request helpers and talking points.
- `server/services_radio/background_tasks_service.py` - Background loops: weather updates, regional knowledge refresh, listener request and vector store maintenance, clip pre-downloads.
- `server/services_radio/community_judge.py` - Editor AI verdict deciding whether a listener's shoutout, reply or review is worth saving, with feedback.
- `server/services_radio/community_on_air.py` - Picks which shoutouts air (minus bans, buried and recently aired) with their top replies.
- `server/services_radio/context_node_registry.py` - Decorator registry of async context nodes the Producer selects; fetches chosen nodes in parallel.
- `server/services_radio/context_nodes.py` - Imports every context node family so all nodes register with the node registry.
- `server/services_radio/context_nodes_format.py` - Context nodes for host identity, format, tone, channels, performance tags, dialogue examples and guidelines.
- `server/services_radio/context_nodes_listener.py` - Context nodes for the listener: profile, local time, persona, favourites, bans, conversation and weather.
- `server/services_radio/context_nodes_segments.py` - Context nodes for segment instructions and data (news, weather, places, events, lyrics), studio clock, segment length.
- `server/services_radio/context_nodes_station.py` - Context nodes for station schedule, recent airings, talking points, Radio Mode segments, tool guidance and City Pulse.
- `server/services_radio/context_nodes_track.py` - Context nodes for the track on air, its audio features, the queue and history.
- `server/services_radio/context_router_service.py` - The Producer: picks context nodes, tool plan and pulse facets per listener message, with Postgres caching.
- `server/services_radio/context_service.py` - Gathers raw context data: listener location/time, track info, favourites, weather, news, events, biographies, shoutouts.
- `server/services_radio/conversation_service.py` - Handles listener text/voice turns: impulses, the DJ tool turn, speaking replies and saving conversation history.
- `server/services_radio/crossfade_plan.py` - Plans each song-to-song crossfade from loudness shape and vocal timing of both tracks.
- `server/services_radio/dj_bank_sources.py` - Talking-point sources: weather change cues, sun/moon sky facts, listener and station listening stats, taste.
- `server/services_radio/dj_command_executor.py` - CommandExecutorService combining search, playback, community and segment executors behind the DJ tools.
- `server/services_radio/dj_command_executor_community.py` - DJ actions for rating tracks and shoutouts and saving listener shoutouts, replies and reviews.
- `server/services_radio/dj_command_executor_playback.py` - DJ actions for transport, seed radio, playlists, moving playback between devices and Radio Mode settings.
- `server/services_radio/dj_command_executor_search.py` - DJ actions for finding and queueing music via the app's search and the listener's loved tracks.
- `server/services_radio/dj_command_executor_segments.py` - DJ actions scheduling produced segments: news, weather, events, places, biographies, lyrics and shoutouts.
- `server/services_radio/dj_content_bank.py` - Talking-point bank: per-session airings, timezones, weather cues, trivia and window-sized talking point menus.
- `server/services_radio/dj_prompt_helper_service.py` - Prompt helpers: assembling prompts, wrapping untrusted data, cleaning LLM output and validating DJ scripts.
- `server/services_radio/dj_prompt_service.py` - Runs the interactive DJ tool turn and Radio Mode segment prompts, including the review step.
- `server/services_radio/dj_prompt_service_configs.py` - Which context nodes each prompt type uses, segment kinds with stand-in lines, and the GPT error wrapper.
- `server/services_radio/dj_prompt_service_debug.py` - Writes each prompt and response to the prompt debug folder for inspection.
- `server/services_radio/dj_prompt_service_segments.py` - Prompts and broadcasts for produced segments, the interpretation cache, and allowed performance tag lists.
- `server/services_radio/dj_prompt_system_service.py` - Voice-script LLM calls: impulse/interlude scripts, paralanguage phonetics and emojis, breaths, engine tag snapping.
- `server/services_radio/dj_tools.py` - DJ tool runtime: per-turn context, call authorization and dispatching each tool to its handler.
- `server/services_radio/dj_tools_args.py` - Normalises DJ tool call arguments and enforces the plain limits on what may run.
- `server/services_radio/dj_tools_display.py` - How DJ tool calls display: brace-style command strings, activity chips and short result summaries.
- `server/services_radio/dj_tools_registry.py` - The one DJ tool registry building the DJ's declarations, the Producer's catalog and request_tools.
- `server/services_radio/event_harvest.py` - Harvests grassroots events from web pages via JSON-LD, iCal, WordPress feeds and a meaning-scored link crawler.
- `server/services_radio/external_events_service.py` - Ticketmaster events client with caching and formatting of events for the hosts.
- `server/services_radio/external_location_service.py` - Google Places search and place details for location segments and the place cache.
- `server/services_radio/external_news_service.py` - News service: Google News pulls, country/city resolution, story depth plans and news reports.
- `server/services_radio/external_web_service.py` - Artist biographies (MusicBrainz/web) and weather forecasts with caching and formatting.
- `server/services_radio/filler_scripts.py` - Impulse and interlude mini-scripts: picks the closest stored script by conversation, learns better ones, plays them.
- `server/services_radio/geo.py` - The Where spatial standard: distances, nearness, overlap, relations and Google-geocoded phrase resolution.
- `server/services_radio/listener_location.py` - Resolves a listener's location from profile, guest position, device or timezone; holds guest positions in memory.
- `server/services_radio/local_knowledge.py` - Vector sources for local events, news and places (local_nuggets, news, place_nuggets) and their dirty flags.
- `server/services_radio/music_beds.py` - Music bed library loaded from a manifest: loop lengths, gains and picking beds for talk breaks.
- `server/services_radio/news_analysis.py` - LLM story cards for news: tags, people, place, category, tone and on-air worth.
- `server/services_radio/news_links.py` - News URL helpers: strip tracking params, article identity and decoding Google News links to real URLs.
- `server/services_radio/news_reader.py` - Fetches news articles and extracts a short sentence summary from the body text.
- `server/services_radio/news_store.py` - Postgres store for news pulls and items: reuse by meaning and matching stories, rankings, aired ledger.
- `server/services_radio/paralanguage_emoji.py` - Renders ~paralanguage~ tags as emojis in chat text and generates emojis for new titles.
- `server/services_radio/persona_service.py` - Generates and updates a listener's persona and profile from their recent conversations via LLM.
- `server/services_radio/place_memory.py` - Place cache memory: looks up remembered places and whether a similar search ran nearby before.
- `server/services_radio/pulse.py` - City Pulse router: queries sources per kind, ranks, links related items, records requests, gives details.
- `server/services_radio/pulse_agent.py` - For You research agent: explores pulse and listener tools to return a cited story-beat narrative.
- `server/services_radio/pulse_demand.py` - City Pulse demand: ledger of listener requests clustered into trends, and the region charts.
- `server/services_radio/pulse_items.py` - City Pulse building blocks: PulseItem, PulseListener, PulseQuery and the KnowledgeNode source base class.
- `server/services_radio/pulse_sources.py` - City Pulse sources: one KnowledgeNode each for events, places, news, music, weather, area, artists, community, reviews, charts, trends.
- `server/services_radio/radio_mode_service.py` - Radio Mode: plans and airs scheduled talk breaks per session, with beds and segment content.
- `server/services_radio/radio_schedule.py` - Radio Mode preferences and scheduling: clock slots, feature intervals and choosing which break is due.
- `server/services_radio/radio_segments.py` - Radio Mode segment registry: news, city, local, community, trivia and For You segments and their prompts.
- `server/services_radio/regional_knowledge.py` - Regional knowledge pool: regions, collectors (Ticketmaster, Google News, Places), the store and taste-targeted queries.
- `server/services_radio/station_blips.py` - Radio drops library: picks in/out blips and mixes them around host and station voice streams.
- `server/services_radio/station_ids.py` - Station ID lines (generic and city) and picking the best cached take for one.
- `server/services_radio/station_voice.py` - Station computer voice: exact-key clip cache, take validation and the background renderer.
- `server/services_radio/sting_library.py` - Musical sting library loaded from the stings manifest, with audio reading.
- `server/services_radio/sting_schedule.py` - Pure sting choice: time check if due, else a voiced kind that fits, else a musical sting.
- `server/services_radio/sting_service.py` - Sting service: per-session sting state, offering song spaces, pre-rendering and streaming stings.
- `server/services_radio/sting_types.py` - Registry of sting types (station ID, sweeper, musical, logo ID, time check) and how each renders.
- `server/services_radio/stripe_service.py` - Stripe billing: checkout, customers, subscription sync and webhook handling for PLAiR subscriptions.
- `server/services_radio/talk_clock.py` - Speaking pace and talk length: measured words per second, word counts for seconds, studio clock notes.
- `server/services_radio/talking_clock.py` - Talking clock: builds spoken time readings from exact cached parts and checks rendered numbers.
- `server/services_radio/tts_broadcast_service.py` - TimelineMixer and audio broadcast: mixes timeline chunks and their intensities for streaming.
- `server/services_radio/tts_database_migration_service.py` - Re-indexes voice clip files from disk into the embeddings DB at startup using their tags.
- `server/services_radio/tts_engine_bootstrap.py` - Launches, health-checks and stops the local Chatterbox TTS engine with the backend.
- `server/services_radio/tts_generation_service.py` - Voice take generation: cache lookup else engine render, priority scheduling, saving FLAC clips and embeddings.
- `server/services_radio/tts_live_stream.py` - LiveStreamEncoder: persistent ffmpeg WebM/Opus encoder per voice stream, with resampling and drops.
- `server/services_radio/tts_processing_service.py` - Voice audio processing: decoding, SFX leveling, station treatment, mic-distance movement curves and Perlin motion.
- `server/services_radio/tts_queue_manager.py` - TTS queue: renders turn segments, blends hosts' tracks incrementally, streams, cancels and meters pace.
- `server/services_radio/tts_stream_planner.py` - Splits DJ scripts into sentences and builds the stream plan; only [BROADCAST] text is spoken.
- `server/services_radio/tts_vector_db_service.py` - Vector clip cache for lines, paralanguage, SFX and breaths: Annoy slots, lookups, anti-repeat cooldown.
- `server/services_radio/tts_voice_threads.py` - Shared thread pool for running blocking voice work off the event loop.
- `server/services_radio/voice_upscale.py` - ClearVoice speech super-resolution upscaling host takes from 24 kHz to 48 kHz.

#### tts_chatterbox (DJ voice engine, own venv)

- `tts_chatterbox/server.py` - The local Chatterbox-Turbo TTS engine: separate process, Flask on 127.0.0.1:8090, streams PCM s16le 24 kHz from `POST /tts`, `POST /abort/<job_id>`.
- `tts_chatterbox/engine.py` - Engine core: voices, slots, batched decoding, the stop token and the length cap per take.
- `tts_chatterbox/batch_t3.py` - Batched CUDA-graph token decoder for up to `TTS_ENGINE_SLOTS` sentences at once.
- `tts_chatterbox/batch_vocoder.py` - Batched S3Gen vocoder pass for the decoded sentences.
- `tts_chatterbox/s3_patches.py` - Patches to Chatterbox's S3Gen for fp32 on the Pascal P6000.
- `tts_server/` - The retired Orpheus-3B engine; kept in the repo, not loaded.

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
- `all` - Every aspect, counted equally

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
- Use `TrackArt` / `TrackArtCrossfade` / `ProfileArt` for every cover (section 8)
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
- Vector stores serve from in-memory slots (section 19); only the TTS clip tables use Annoy
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
- **Media Session / outside pauses:** separate `play`/`pause`/`seekto` handlers (`resumePlayback`/`pausePlayback`), `playbackState`, `setPositionState` and the cover as a local image made from its pack. Pauses the engine didn't cause (calls, other apps, headphones) and a context `interrupted` state go through `engine.onExternalPause` → `pauseFromOutside`, which updates UI + server. `navigator.audioSession.type = 'playback'` is set where supported; the voice recorder switches it to `play-and-record` only while capturing and back to `playback` as soon as the mic tracks stop (released on stop, not after `MediaRecorder.onstop`), so Bluetooth headsets return from hands-free to A2DP.
- **3D Lit Artwork** (`settingsState.litArtwork`, default ON) turns parallax and lighting on covers and profile pictures on or off. On iOS the motion permission is requested on the first tap while it is on; a refused permission never turns the setting off.
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
