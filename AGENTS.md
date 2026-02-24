# PLAiR.fm - AI Radio Platform

This document provides essential information for AI coding agents working on the PLAiR.fm codebase.

---

## Project Overview

**PLAiR.fm** is a full-stack AI-powered music streaming platform with real-time voice interaction. It combines traditional radio functionality with advanced AI capabilities including an AI DJ that controls playback, engages in natural conversations, and curates personalized music experiences using semantic vector search.

**Key Features:**
- Real-time AI DJ with personality and voice synthesis (ElevenLabs)
- Semantic music discovery using 10-dimensional vector embeddings
- Multi-device orchestration with exclusive playback control
- Dual-buffer audio engine for gapless crossfading
- WebGL/GLSL audio-reactive visualizations
- User-generated content (shoutouts) with speech enhancement
- AI music generation integration (Suno)

---

## Technology Stack

### Frontend
- **Framework:** React 18 with Vite
- **Language:** JavaScript/JSX, GLSL (shaders)
- **State Management:** Context API with Publisher/Subscriber pattern
- **Styling:** Tailwind CSS + CSS-in-JS
- **Graphics:** WebGL, Three.js (@react-three/fiber)
- **Animation:** Framer Motion
- **Build Tool:** Vite (port 3000)

### Backend
- **Framework:** FastAPI (async Python)
- **Language:** Python 3.11+
- **Database:** SQLite with SQLAlchemy (async)
- **WebSocket:** Native asyncio WebSocket (not socket.io)
- **Vector Search:** Annoy (Approximate Nearest Neighbors)
- **Audio Processing:** FFmpeg, Demucs, Whisper, ClearVoice
- **AI/LLM:** Anthropic Claude API
- **Server:** Uvicorn (port 8000)

### Infrastructure
- **Platform:** Windows Server with ProactorEventLoop
- **Web Server:** Nginx (reverse proxy, SSL)
- **Process Management:** Custom service orchestration
- **Caching:** Multi-layer (Memory Map, IndexedDB, Cache API)

---

## Project Structure

```
AI_RADIO/
├── client/                     # React frontend
│   ├── src/
│   │   ├── contexts/          # 12 React contexts (state management)
│   │   ├── components/        # 40+ React components
│   │   ├── hooks/             # Custom React hooks
│   │   ├── lib/               # Core libraries (audioEngine, api, caching)
│   │   ├── App.jsx            # Root app component
│   │   └── main.jsx           # React entry point
│   ├── package.json           # NPM dependencies
│   └── vite.config.js         # Vite configuration
│
├── server/                     # FastAPI backend
│   ├── app.py                 # Main FastAPI application
│   ├── start.py               # Server startup script (Windows-specific)
│   ├── requirements.txt       # Python dependencies
│   ├── services/              # 60+ backend services
│   │   ├── playback_state.py  # CRITICAL: Playback state machine
│   │   ├── catalog_*.py       # Catalog & vector search services
│   │   ├── audio_*.py         # Audio processing services
│   │   └── ...
│   ├── services_radio/        # DJ system services (20+)
│   │   ├── dj_command_executor.py
│   │   ├── tts_*.py           # TTS pipeline services
│   │   └── ...
│   └── database/
│       ├── models.py          # SQLAlchemy models
│       └── connection.py      # Database connection
│
├── docs/                       # Architecture documentation
│   ├── ARCHITECTURE_SSOT.md   # Single Source of Truth pattern
│   ├── PLAYBACK_ARCHITECTURE.md  # Playback & audio engine
│   ├── KNOWN_ISSUES.md        # Known bugs and gaps
│   └── ...
│
├── external_components/        # External tools
├── checkpoints/               # Model checkpoints
├── data/                      # Data storage
└── models/                    # ML models
```

---

## Development Commands

### Frontend (Client)
```bash
cd client
npm run dev       # Development server (port 3000)
npm run build     # Production build
npm run preview   # Preview production build
```

### Backend (Server)
```bash
cd server
# Using virtual environment
E:/AI_RADIO/.venv/Scripts/python.exe start.py

# Database migration
E:/AI_RADIO/.venv/Scripts/python.exe migrate_database.py
```

**Note:** Backend uses Windows-specific paths. The project uses a Python virtual environment at `E:/AI_RADIO/.venv/`.

---

## Critical Architecture Patterns

### 1. Single Source of Truth (SSOT) - UIStateContext

**File:** `client/src/contexts/UIStateContext.jsx`

This is the **most critical architectural pattern** in the frontend.

**Core Principle:**
- **Engines** (PlaybackContext, useDJAudioStream, VoiceRecordingContext) produce raw data
- **UIStateContext** derives visual state from engine data (Publisher/Subscriber pattern)
- **Views** (components) consume state, do NOT manage or transform it

**Visual State Priority Order:**
```javascript
1. Recording (Red) - isMicRecording
2. AI Processing (Blue) - isAIProcessing
3. DJ Speaking (Dynamic Color) - isDJSpeaking
4. Music Playing (Green) - isMusicPlaying
5. Music Paused (Yellow) - isMusicPaused
0. Idle (Grey) - default
```

**DO:**
- Engines call `reportEngineStatus()` to publish their state to UIState
- Components read from UIState via `useRadioUI()` or `useUIState()` hooks
- Keep components "dumb" - they only render based on state

**DO NOT:**
- Pass engine state through component props
- Make components coordinate state between engines
- Add business logic to view components

### 2. Backend Playback State Machine

**File:** `server/services/playback_state.py`

This is the **most critical file** on the backend. All playback state lives here.

**Key Properties:**
- `self.radio_mode` - Current mode: "favorites", "discovery", "mood", "genre", etc.
- `self.queue` - Array of tracks to play
- `self.current_track_id` - Currently playing track
- `self.is_playing` - Playback state
- `active_device_id` - When set, frontend controls playback (backend should not auto-advance)

### 3. Multi-Device Playback Management

The app supports multiple devices per user session with **one active playback device** at a time.

**Three-Layer Device Enforcement:**
1. **Bandwidth Layer (PlaybackContext):** Inactive devices don't load audio
2. **Hardware Layer (AudioEngine):** Blocks play/pause/seek on inactive devices
3. **Visual Layer (UIState → Components):** Shows "Playing on another device" banner

**Critical Pattern - Default to INACTIVE:**
All three layers default `isActiveDevice = false`. Backend explicitly activates via WebSocket.

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

### 5. Audio Engine - Dual Buffer Crossfading

**File:** `client/src/lib/audioEngine.js`

Uses A/B slot architecture for seamless crossfading:
- Slot A and Slot B alternate as current/next
- During crossfade, both play simultaneously (A fades out, B fades in)
- `timeUpdateElement` tracks which element has the progress listener
- Track state must update during crossfade

### 6. Artwork Preloading Architecture

**Files:** `client/src/contexts/UIStateContext.jsx`, `client/src/lib/mediaCache.js`

UIStateContext automatically preloads artwork for smooth crossfades:
- Current track: Regular artwork + enriched artwork (for parallax)
- Next track: Regular artwork + enriched artwork

**Component Usage:**
```javascript
const artworkUrl = useArtwork(trackId, hasArtwork)
const enrichedUrl = useEnrichedArtwork(trackId, hasArtwork)
```

### 7. Modal System

**File:** `client/src/components/modals/Modal.jsx`

Modal open/close state lives in **UIStateContext**, NOT in local component state:
```javascript
// UIStateContext provides:
uploadModalOpen, openUploadModal, closeUploadModal

// Components just call the open function:
const { openUploadModal } = useUIState()
<button onClick={openUploadModal}>Upload</button>
```

---

## Key File Locations

### Frontend Contexts (State Management)
- `UIStateContext.jsx` - SSOT for all UI state
- `PlaybackContext.jsx` - Music playback engine
- `VoiceRecordingContext.jsx` - Microphone recording
- `WebSocketContext.jsx` - WebSocket client
- `ViewportContext.jsx` - Responsive breakpoints
- `DynamicThemeContext.jsx` - Theme engine

### Frontend Core Libraries
- `client/src/lib/audioEngine.js` - Audio playback engine (dual-buffer)
- `client/src/lib/api.js` - API client (ALL backend calls)
- `client/src/lib/mediaCache.js` - Media caching
- `client/src/lib/session.js` - Device ID management

### Backend Critical Services
- `server/services/playback_state.py` - Playback state machine
- `server/services/playback_service.py` - Session-level management
- `server/services/device_management_service.py` - Device management
- `server/services/catalog_vector_search_service.py` - Semantic search
- `server/services/websocket_service.py` - WebSocket management

### Backend DJ System
- `server/services_radio/dj_command_executor.py` - Command parser
- `server/services_radio/tts_generation_service.py` - TTS generation
- `server/services_radio/tts_broadcast_service.py` - Audio streaming

---

## Code Style Guidelines

### JavaScript/React
- Use functional components with hooks
- Destructure props and context values
- Use `useCallback` for event handlers passed to children
- Context values should be memoized
- No prop drilling - subscribe to contexts directly

### Python
- Use async/await throughout
- Type hints where possible
- Service classes inherit from `BaseService`
- Database access via `AsyncSession = Depends(get_db)`
- Log using `log_service` for structured logging

### Naming Conventions
- **JavaScript:** camelCase for variables/functions, PascalCase for components
- **Python:** snake_case for variables/functions, PascalCase for classes
- **Constants:** UPPER_SNAKE_CASE

---

## Testing Strategy

**Current State:** The project does not have an automated test suite.

**Manual Testing Checklist:**
- Play/pause/seek functionality
- Crossfade between tracks
- Multi-device playback handoff
- Voice recording and playback
- DJ TTS streaming
- Queue management
- Modal interactions

**Testing Documentation:**
- `docs/PLAYBACK_ARCHITECTURE.md` - Section: "Testing Checklist"

---

## Common Pitfalls

### ❌ DON'T
- Use direct `fetch()` for backend calls - ALWAYS use `api.js` methods
- Add code comments/notes (no `// ####`, `// TODO`, etc.) - keep code clean
- Store `activeSeedMode` in PlaybackContext (use UIState only)
- Store `isActiveDevice` in local component state (use UIState only)
- Use `socket.on()` for WebSocket events (use `useWebSocketSubscribe()`)
- Let backend auto-advance tracks when `active_device_id` is set
- Pass viewport state through props (components subscribe directly)
- Pass artwork URLs through component props
- Manually call `preloadArtwork()` in components
- Duplicate FFT processing logic
- Duplicate shader effect logic

### ✅ DO
- Use `api.js` for ALL backend calls (`api.methodName()`)
- Use UIState as SSOT for all UI state
- Use ViewportContext as SSOT for all viewport/responsive state
- Use `useArtwork()` and `useEnrichedArtwork()` hooks
- Trust UIState to automatically preload artwork
- Engines report directly to UIState via `reportEngineStatus()`
- Components subscribe to contexts directly - NO prop drilling
- Use optimistic updates for user actions
- Broadcast playback state changes via WebSocket

---

## Performance Optimizations

### Frontend
- FFT processing uses early-exit pattern (20fps instead of 60fps)
- Zero-allocation audio processing using for loops
- Refs used for RAF loops, setState only when React needs to re-render
- PlaybackContext guards against unnecessary re-renders
- Artwork proactively preloaded for current + next track
- Multi-layer caching: Memory Map → IndexedDB → Cache API

### Backend
- Service instances initialized once at startup
- Vector search uses Annoy index (O(log n) complexity)
- Preferences cached in-memory
- Connection pooling for database and external APIs

---

## Security Considerations

- JWT-based authentication
- Rate limiting per user (`rate_limit_service.py`)
- File upload validation and sanitization
- CORS configured in FastAPI
- SQL injection protection via SQLAlchemy ORM

---

## Environment Setup

### Required Environment Variables
```bash
# Backend (.env file in server/)
ANTHROPIC_API_KEY=...
ELEVENLABS_API_KEY=...
SUNO_API_KEY=...
STRIPE_SECRET_KEY=...
JWT_SECRET_KEY=...
NEWS_API_KEY=...
```

### Python Virtual Environment
```bash
# Located at E:/AI_RADIO/.venv/
# Python 3.11+ required
# Install dependencies: pip install -r server/requirements.txt
```

### Node.js
```bash
# Node 18+ recommended
cd client
npm install
```

---

## Deployment

### Production Build
```bash
cd client
npm run build
# Output: client/dist/
```

### Server Deployment
- Windows Server with Nginx reverse proxy
- SSL via Let's Encrypt (Certbot)
- Service managed via custom scripts in `external_components/`

---

## Known Issues

See `docs/KNOWN_ISSUES.md` for detailed information.

**Critical Issue: Session State Resyncing**
- When server restarts or client reconnects, session state can become desynchronized
- Workaround: Refresh the page (F5)
- Status: Not yet implemented

---

## Additional Documentation

- `CLAUDE.md` - Comprehensive project guide for Claude Code
- `PROJECT_OVERVIEW.md` - High-level project overview and CV points
- `docs/ARCHITECTURE_SSOT.md` - Single Source of Truth pattern
- `docs/PLAYBACK_ARCHITECTURE.md` - Playback system architecture
- `docs/HUMAN_MUSIC_UPLOAD.md` - Human music upload system
- `docs/OFFLINE_MODE.md` - Offline support documentation

---

## Contact & Support

For questions about the architecture or patterns, refer to the comprehensive documentation in `CLAUDE.md` and the `docs/` directory.

**Project Timeline:** Built in < 3 months
**Total Lines of Code:** ~35,500 (15K frontend, 20K backend)
**External APIs:** Claude, ElevenLabs, Suno, Stripe, News, Events, Geolocation
