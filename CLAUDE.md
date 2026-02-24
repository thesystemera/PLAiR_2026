# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Radio (PLAiR.fm) is a full-stack music streaming application with an AI DJ that controls playback, manages playlists, and interacts with users via voice/text. The system features real-time audio processing, WebSocket-based state synchronization, and advanced audio engine capabilities including dual-buffer crossfading.

**Architecture:** React + Vite frontend, FastAPI backend, SQLAlchemy database, WebSocket for real-time updates.

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
# Start backend server (port 8000)
cd server
E:/AI_RADIO/.venv/Scripts/python.exe start.py

# Database migration
E:/AI_RADIO/.venv/Scripts/python.exe migrate_database.py
```

**Note:** Backend uses Windows-specific paths. The project uses a Python virtual environment at `E:/AI_RADIO/.venv/`.

## Critical Architecture Patterns

### 1. Single Source of Truth (SSOT) - UIStateContext

**File:** `client/src/contexts/UIStateContext.jsx`

This is the **most critical architectural pattern** in the frontend. Read `docs/ARCHITECTURE_SSOT_PATTERN.md` before making any UI state changes.

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
- Components read from UIState via `useRadioUI()` or `useUIState()` hooks
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
- `progressPercent` - Playback progress

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
When `active_device_id` is set, the frontend controls playback timing. Backend should NOT auto-advance tracks when progress reaches end. See `docs/CROSSFADE_FIX_HANDOFF.md` for details.

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
1. **Connection:** Device connects → defaults to INACTIVE
2. **Backend Activation:** Backend sets `active_device_id` for one device
3. **WebSocket Broadcast:** All devices receive `playback_state` with `active_device_id`
4. **PlaybackContext:** Calculates `weAreActive`, updates AudioEngine + UIState
5. **Components:** Read `engineState.isActiveDevice` from UIState

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

**Backend:** `server/app.py` - `broadcast_playback_state_to_session()`
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

### 5. DJ Command System

**File:** `server/services_radio/dj_command_executor.py`

Parses and executes commands from AI-generated text. Commands map 1:1 to vector search categories.

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
xs<640px (phone, 1.0), sm 640-768px (1.0), md 768-1024px (tablet, 1.0), lg 1024-1440px (laptop, 0.8), xl 1440-1920px (desktop, 0.75), 2xl 1920-2560px (1080p, 0.7), 3xl 2560-3840px (QHD, 0.85), 4K 3840px+ (baseline, 1.0). Scaling applies to desktop (≥1024px) only.

See `docs/VIEWPORT_USAGE.md` for usage patterns.

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
const { openUploadModal } = useUIState()
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
- `client/src/contexts/NetworkContext.jsx` - Network status (online/offline, connection quality, bitrate detection)
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
- `client/src/components/Toast.jsx` - Toast notifications (success, error, info messages)
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
- `client/src/hooks/useDJAudioStream.js` - DJ TTS audio stream (real-time voice playback, device-aware)
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
- `client/src/lib/audioEngine.js` - **CRITICAL** Audio playback engine (dual-buffer A/B crossfading, device enforcement)
- `client/src/lib/session.js` - Device ID management (localStorage, UUID generation)
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
- `client/src/lib/djBroadcastChain.js` - DJ broadcast chain (audio pipeline)
- `client/src/lib/audioInteractionManager.js` - Audio interaction manager (click-to-play, autoplay policy)

**Video/Rendering:**
- `client/src/lib/offlineVideoRenderer.js` - Offline video export (frame-by-frame WebGL rendering, MP4 encoding via WebCodecs, uses shared shaders from AudioReactiveCanvas)

**UI Libraries:**
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
- `server/app.py` - FastAPI routes and WebSocket handlers

**Authentication & User:**
- `server/services/auth_service.py` - Authentication (JWT tokens, password hashing)
- `server/services/user_profile_service.py` - User profile management (username, settings, location)
- `server/services/preferences_service.py` - User preferences (audio quality, theme, TTS settings)
- `server/services/preferences_cache_service.py` - In-memory cache for user likes/bans
- `server/services/profile_picture_service.py` - Profile picture uploads and serving

**Media Serving:**
- `server/services/media_streaming_service.py` - Media file streaming (range requests, bitrate selection)
- `server/services/rate_limit_service.py` - Rate limiting (per-user request throttling)

**Analytics:**
- `server/services/analytics_service.py` - Analytics tracking (play events, engagement)
- `server/services/analytics_file_service.py` - File-based analytics storage (JSONL, exports)

**AI Services:**
- `server/services/ai_service.py` - AI/LLM service wrapper (Claude API, prompt management)

### Backend Data Services

**Catalog Management:**
- `server/services/catalog_database_service.py` - Track catalog (CRUD operations, metadata queries)
- `server/services/catalog_vector_database_service.py` - Vector database (Annoy index for embeddings)
- `server/services/catalog_vector_search_service.py` - Track similarity search (semantic search, recommendations)
- `server/services/catalog_vector_search_prompt_cache_service.py` - Prompt caching for vector search (performance optimization)

**User Content Management:**
- `server/services/user_content_database_service.py` - User content database (shoutouts, recordings)
- `server/services/user_content_vector_database_service.py` - User content vector DB (semantic search for user audio)
- `server/services/user_content_vector_search_service.py` - User content vector search
- `server/services/user_content_vector_search_prompt_cache_service.py` - User content prompt cache
- `server/services/user_content_speech_enhancement_service.py` - User speech enhancement (noise reduction, normalization)

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

- `server/services/human_metadata_extraction_service.py` - Gemini Pro audio analysis (genre, mood, artists, lyrics)
- `server/services/human_music_upload_service.py` - Upload pipeline (validation, transcoding, catalog integration)

**Key Principle:** Human tracks are stored in the same `tracks` table with `is_ai_generated=0`. They use identical metadata schemas, playback systems, and discovery pipelines as AI tracks.

### Backend DJ System (services_radio)

**DJ Conversation & Prompting:**
- `server/services_radio/dj_prompt_service.py` - DJ personality prompts (character, tone, knowledge base)
- `server/services_radio/dj_prompt_system_service.py` - DJ system prompts (instructions, formatting)
- `server/services_radio/dj_prompt_helper_service.py` - DJ prompt helpers (context injection, template rendering)
- `server/services_radio/dj_command_executor.py` - Command parser/executor ({play}, {cue}, {seed}, {pause}, etc.)
- `server/services_radio/conversation_service.py` - User-DJ conversations (message history, context management)
- `server/services_radio/announcer_service.py` - Station announcements (scheduled broadcasts, event notifications)
- `server/services_radio/persona_service.py` - DJ persona management (personality traits, voice selection)

**Context Management:**
- `server/services_radio/context_service.py` - Context for AI responses (track info, user preferences, session state)
- `server/services_radio/context_node_registry.py` - Context node registry (dynamic context system)
- `server/services_radio/context_router_service.py` - Context routing (selecting relevant context nodes)
- `server/services_radio/context_nodes.py` - Context node definitions (track, user, weather, news, etc.)

**TTS Pipeline:**
- `server/services_radio/tts_generation_service.py` - Text-to-speech generation (ElevenLabs, voice cloning)
- `server/services_radio/tts_processing_service.py` - Audio processing (normalization, compression, effects)
- `server/services_radio/tts_stream_planner.py` - TTS streaming (chunk planning, timing coordination)
- `server/services_radio/tts_queue_manager.py` - TTS queue management (priority, cancellation)
- `server/services_radio/tts_broadcast_service.py` - Audio broadcasting (WebSocket streaming, chunking)
- `server/services_radio/tts_vector_db_service.py` - Vector DB for TTS (voice similarity, caching)

**External Data Services:**
- `server/services_radio/external_web_service.py` - Web scraping (artist info, lyrics, news)
- `server/services_radio/external_news_service.py` - News API (headlines, articles)
- `server/services_radio/external_location_service.py` - Location services (geocoding, timezone)
- `server/services_radio/external_events_service.py` - Events API (concerts, festivals)

**Background Tasks:**
- `server/services_radio/background_tasks_service.py` - Background tasks (scheduled jobs, cleanup, maintenance)
- `server/services_radio/stripe_service.py` - Payment processing (subscriptions, billing)

## Database

**ORM:** SQLAlchemy (async)
**Connection:** `server/database/connection.py`
**Models:** `server/database/models.py`

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
- Preferences cached in-memory (`preferences_cache_service.py`)
- Rate limiting per user (`rate_limit_service.py`)

## Windows-Specific Notes

This project runs on Windows with specific configurations: Backend uses `WindowsProactorEventLoopPolicy`, disables Quick Edit Mode (`start.py`), Python venv at `E:/AI_RADIO/.venv/`, restart services with `external_components/restart_all.bat`.

**CRITICAL:** When editing files with Claude Code, **ALWAYS use relative paths** (e.g., `client/src/App.jsx`) instead of absolute paths (e.g., `E:/AI_RADIO/client/src/App.jsx`). Absolute paths with drive letters cause "file has been unexpectedly modified" errors. Working directory is `E:/AI_RADIO`.

## Debugging Tips

**Frontend:** Check console for `[PlaybackContext]`, `[AudioEngine]`, `[UIState]` logs. Verify `engineState.queue`/`currentTrack` in UIState. Artwork URLs should be blob URLs when cached.

**Backend:** Check for `[PLAYBACK]`, `[DJ]`, `[TTS]` logs. Verify `broadcast_playback_state_to_session` calls and `PlaybackState.radio_mode`.
