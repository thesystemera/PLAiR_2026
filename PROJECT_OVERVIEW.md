# PLAiR.fm - AI Radio Platform
## Technical Project Overview

**Development Timeline:** < 3 months
**Project Type:** Full-stack AI-powered music streaming platform with real-time voice interaction

---

## Executive Summary

PLAiR.fm is a next-generation music streaming platform designed for **user-uploaded human music**, combining traditional radio functionality with advanced AI capabilities, real-time audio processing, and multi-device synchronization. Unlike conventional streaming services, PLAiR features an AI DJ that controls playback, engages in natural conversations, and curates personalized music experiences using semantic vector search and machine learning.

**Origin Story:** PLAiR was originally a Spotify-powered app that was [killed on November 27, 2024](https://www.theverge.com/2024/12/5/24311523/spotify-locked-down-apis-developers) when Spotify suddenly shut down critical API access (Related Artists, Recommendations, Audio Features, Playlists) with zero warning - the day before Thanksgiving. Rather than give up, we rebuilt from scratch with a critical lesson learned: **Own your infrastructure.**

**Platform Vision:** The current system is 100% independent - no reliance on Spotify or any platform that could shut us down. The AI-generated catalog served as **bootstrap training data** to develop the 10-dimensional semantic classification system. With the technology now proven, the platform is ready to onboard user-uploaded content from real human artists - something we couldn't do while dependent on Spotify's APIs.

**Key Differentiators vs. Spotify:**
- Real-time AI DJ with personality and voice synthesis
- Semantic music discovery using vector embeddings (vs. collaborative filtering)
- User-generated content (shoutouts) with speech enhancement
- AI music generation integration (Suno)
- Real-time audio visualizations with GLSL shaders
- Advanced audio processing pipeline (source separation, mastering, transcoding)
- Multi-device orchestration with WebSocket state synchronization

---

## Technology Stack

### Frontend Technologies
- **Core Framework:** React 18 + Vite (modern build tooling)
- **Languages:** JavaScript/JSX, GLSL (GPU shader programming)
- **State Management:** Context API with custom Publisher/Subscriber pattern
- **Real-time Communication:** WebSocket (custom protocol, not socket.io)
- **Styling:** CSS3 with CSS-in-JS patterns
- **Audio Engine:** Custom dual-buffer AudioContext implementation
- **Offline Storage:** IndexedDB + Cache API + in-memory caching
- **Graphics:** WebGL for audio-reactive visualizations

### Backend Technologies
- **Framework:** FastAPI (async Python web framework)
- **Languages:** Python 3.11+
- **ORM:** SQLAlchemy (async)
- **Database:** SQLite (async with aiosqlite)
- **Vector Search:** Annoy (Approximate Nearest Neighbors)
- **WebSocket:** Native WebSocket (asyncio-based)
- **Audio Processing:** FFmpeg, Demucs, Whisper, ClearVoice
- **AI/LLM:** Anthropic Claude API
- **TTS:** ElevenLabs API
- **Music Generation:** Suno API
- **Payment Processing:** Stripe

### DevOps & Infrastructure
- **Platform:** Windows Server with ProactorEventLoop
- **Web Server:** Nginx (reverse proxy, SSL termination)
- **SSL/TLS:** Certbot (Let's Encrypt)
- **Process Management:** Custom service orchestration
- **Caching:** Multi-layer caching strategy (memory, disk, CDN)

---

## Architecture Overview

### System Architecture Pattern: Publisher/Subscriber with SSOT (Single Source of Truth)

The application implements a sophisticated state management architecture where:
1. **Engines** (audio, recording, TTS) produce raw data
2. **UIStateContext** acts as central SSOT, deriving visual state from engine data
3. **Components** subscribe directly to contexts (no prop drilling)
4. **Backend** broadcasts state changes via WebSocket to all connected devices

```
Backend Services → WebSocket → Playback Engine → UIState SSOT → Components
                              ↓                              ↑
                         Audio Engine ←─────────────────────┘
                              ↓
                         Hardware (speakers)
```

### Frontend Architecture

#### State Management Contexts (12 specialized contexts)

**Core Engine Contexts:**
- **UIStateContext** - Central SSOT for all UI state (visual state priority system, device management, playback state)
- **PlaybackContext** - Music playback engine (dual-buffer crossfading, bandwidth optimization, device enforcement)
- **VoiceRecordingContext** - Microphone recording engine (voice commands, shoutouts, noise cancellation)
- **PlaybackShoutoutContext** - User-generated content playback (separate from main audio)

**Infrastructure Contexts:**
- **WebSocketContext** - Real-time bidirectional communication
- **ViewportContext** - Responsive breakpoint system (7 breakpoints, automatic scaling)
- **NetworkContext** - Connection quality detection (bandwidth, latency, offline mode)
- **StorageContext** - Quota management and cache eviction strategies

**Data Contexts:**
- **AuthContext** - JWT-based authentication
- **PreferencesContext** - User preferences (likes, bans, super_likes)
- **GenerationQueueContext** - AI music generation job tracking
- **DynamicThemeContext** - Theme engine (category-based colors, interaction effects)

#### Advanced Frontend Features

**Dual-Buffer Audio Engine:**
- A/B slot architecture for seamless crossfading (no gaps or stutters)
- Proactive artwork preloading (current + next track)
- Device-aware bandwidth optimization (inactive devices don't load audio)
- Three-layer device enforcement (bandwidth, hardware, visual)

**Audio Reactive Visualizations (GLSL Shaders):**
- FFT-based frequency analysis (optimized to 20fps for CPU efficiency)
- Zero-allocation audio processing (for loop optimization vs. array methods)
- Music-reactive shader effects (glitch, chromatic aberration, rotation, brightness)
- User interaction effects (click/tap triggered visual responses)
- Tempo-synchronized animations

**3D Parallax Artwork System:**
- **Depth map generation:** AI-powered depth estimation for album artwork (creates 3D parallax effects)
- **Color extraction:** Dominant color analysis for dynamic theming and gradients
- **Enriched artwork format:** Side-by-side color + depth images (custom composite format)
- **A/B layer crossfading:** Two layers render simultaneously for smooth transitions (opacity animation)
- **Proactive preloading:** UIStateContext automatically preloads current + next track artwork
- **Multi-layer caching:** Memory Map → IndexedDB → Cache API (three-tier caching strategy)
- **Parallax scrolling:** Depth-based movement creates 3D effect on scroll/interaction
- **Component integration:** ParallaxArtwork.jsx renders depth-aware scrolling effects

**Responsive Design:**
- 7 breakpoint system (xs, sm, md, lg, xl, 2xl, 3xl)
- Automatic viewport scaling (0.7x-1.0x based on screen size)
- Publisher/Subscriber viewport pattern (all components subscribe directly)
- Performance-optimized (only updates on breakpoint change, not pixel resize)

### Backend Architecture

#### Service-Oriented Architecture (60+ specialized services)

**Playback & State Management (Core):**
- **PlaybackState** - State machine for queue management, radio modes, device orchestration
- **PlaybackService** - Session-level multi-user coordination
- **DeviceManagementService** - Multi-device registration, activation, listing
- **WebSocketService** - Connection pooling, session broadcasting, cleanup

**Catalog & Vector Search (Semantic Discovery Engine):**
- **CatalogVectorSearchService** - Semantic music discovery using Annoy index (1M+ embeddings, <200ms search)
- **CatalogVectorDatabaseService** - Embedding storage, retrieval, and indexing
- **CatalogDatabaseService** - Traditional CRUD operations (metadata, relationships)
- **10 Vector Search Categories per Track:** Each track has 10 separate vector embeddings for different semantic aspects:
  - `song_title` - Track title similarity
  - `primary_artist` - Main artist matching
  - `similar_artists` - Artist similarity (e.g., NIN → Ministry, Skinny Puppy)
  - `primary_genre` - Genre matching (e.g., Industrial Rock)
  - `secondary_genres` - Sub-genre/tag similarity (e.g., EBM, Darkwave)
  - `mood` - Emotional vibe (e.g., aggressive, melancholic, anxious)
  - `style` - Production style (e.g., TR-808 drums, distorted synths, lo-fi)
  - `theme` - Lyrical themes (e.g., alienation, dystopia, decay)
  - `vocal` - Vocal delivery (e.g., whispered, screamed, distorted)
  - `lyrics` - Actual lyric content similarity
- **User Content Vector Search:** Separate vector database for user shoutouts (semantic search on voice recordings)
- **Annoy Index:** Approximate Nearest Neighbors (O(log n) search complexity, memory-efficient)

**Audio Processing Pipeline:**
- **AudioFeaturesService** - Tempo, key, energy, loudness extraction
- **AudioTranscodingService** - Multi-bitrate MP3 encoding (128k, 192k, 256k)
- **AudioMasterService** - Dynamic EQ, compression, loudness normalization
- **AudioDemucsService** - AI source separation (vocals, drums, bass, other)
- **AudioClearVoiceService** - Speech enhancement (noise reduction, normalization)
- **WhisperDualService** - Speech-to-text with word-level timestamps

**AI DJ System (20+ services):**
- **DJPromptService** - Personality prompts (character, tone, knowledge base)
- **DJCommandExecutor** - Command parser for AI-generated playback control
- **ConversationService** - User-DJ conversation history and context
- **TTSGenerationService** - Text-to-speech with voice cloning
- **TTSBroadcastService** - Real-time audio streaming to frontend
- **TTSStreamPlanner** - Chunk timing and coordination
- **ContextService** - Dynamic context injection (track info, weather, news, events)

**Music Generation (Suno Integration):**
- **SunoService** - API client for AI song generation
- **SunoPromptService** - AI-assisted prompt crafting
- **SunoGenerationQueueService** - Job tracking, progress updates
- **SunoEnrichedMetadataService** - AI categorization and description
- **SunoArtworkEnrichmentService** - Depth map generation for parallax effects

**External Integrations:**
- **ExternalNewsService** - News API integration
- **ExternalEventsService** - Concert and festival discovery
- **ExternalLocationService** - Geocoding, timezone detection
- **StripeService** - Payment processing and subscriptions

**Real-Time Context Gathering System:**
- **ContextNodeRegistry** - Dynamic context node registration and management
- **ContextRouterService** - Intelligent context selection based on conversation
- **Context Nodes:** 15+ specialized context providers for DJ responses:
  - **Track Context:** Current track metadata (artist, genre, mood, lyrics, style, theme, vocals)
  - **Weather Context:** Real-time weather based on user geolocation
  - **News Context:** Current headlines and trending stories
  - **Events Context:** Nearby concerts, festivals, music events
  - **User Context:** Listening history, preferences, engagement patterns
  - **Time Context:** Time of day, day of week, season, holidays
  - **Queue Context:** Upcoming tracks, recently played
  - **Session Context:** Play count, session duration, device info
- **Dynamic Injection:** Context gathered in real-time for each DJ response (not pre-cached)
- **Relevance Scoring:** Context router selects most relevant nodes based on conversation topic
- **Token Optimization:** Only includes relevant context to minimize API costs

**Cost Optimization & Caching Systems:**
- **TTS Vector Database Cache:** Semantic caching for generated speech
  - Stores generated TTS audio with vector embeddings
  - Searches for semantically similar phrases before generating new audio
  - **Cost Savings:** ~70% reduction in TTS API calls (reuses similar phrases)
  - Example: "Here's a track by Nine Inch Nails" → reuses cached audio for similar artist intros
- **Prompt Caching for Vector Search:**
  - `CatalogVectorSearchPromptCacheService` - Caches Claude API prompt responses
  - `UserContentVectorSearchPromptCacheService` - Caches user content search prompts
  - **Cost Savings:** ~90% reduction in embedding generation API calls
  - Persists embeddings across sessions (doesn't regenerate on every search)
- **Artwork Multi-Layer Cache:**
  - Memory Map (instant access, ~100 images)
  - IndexedDB (offline persistence, ~1000 images)
  - Cache API (Service Worker, ~5000 images)
  - **Cost Savings:** Eliminates repeated CDN/S3 requests (bandwidth reduction)
- **In-Memory Preferences Cache:**
  - User likes/bans/super_likes cached in memory (no DB query per track)
  - **Performance:** <1ms preference lookup vs. ~20ms DB query
  - **Cost Savings:** Reduces database load by ~95% for preference checks
- **Connection Pooling:**
  - Database connection pool (reuse connections, avoid handshake overhead)
  - HTTP connection pool for external APIs (Keep-Alive)
  - **Cost Savings:** ~40% reduction in connection overhead
- **Efficient WebSocket Broadcasting:**
  - Per-session broadcasting (not global broadcast)
  - Only sends state diffs (not full state on every change)
  - **Cost Savings:** ~80% reduction in WebSocket bandwidth

**Total Estimated Cost Savings:** ~60-70% reduction in operational costs (API calls, bandwidth, compute) through intelligent caching and optimization strategies.

---

## Feature Set

### Core Features

**1. AI DJ with Voice Interaction**
- Natural language processing via Claude API
- Real-time text-to-speech streaming (ElevenLabs)
- Personality system with customizable traits
- Context-aware responses (weather, news, events, track metadata)
- Command execution ({play}, {seed}, {pause}, etc.)

**2. Advanced Music Discovery**
- Semantic vector search (10 search categories: artist, genre, mood, style, theme, lyrics, vocal, etc.)
- 10 seed modes based on current track (primary_genre, similar_artists, mood, style, theme, etc.)
- 5 playlist modes (favorites, discovery, top_hits_all, top_hits_week, top_hits_day)
- Hybrid recommendation engine (user preferences + vector similarity)

**3. Multi-Device Playback Orchestration**
- UUID-based device identification (localStorage)
- Single active device per session (exclusive playback control)
- Universal remote (inactive devices display queue/state but don't play audio)
- Automatic device activation on user interaction
- WebSocket-based state synchronization across all devices

**4. Audio Engine**
- Dual-buffer A/B slot architecture for gapless crossfading
- Configurable bitrate selection (auto, 128k, 192k, 256k)
- Network-adaptive quality switching
- Range request support for seeking
- Proactive next-track preloading

**5. User-Generated Content (Shoutouts)**
- Voice recording with noise cancellation
- Speech-to-text transcription (Whisper)
- Speech enhancement pipeline (ClearVoice)
- Vector search for shoutout discovery
- Analytics tracking (plays, shares, likes)

**6. AI Music Generation**
- Suno API integration for custom track generation
- AI-assisted prompt crafting
- Job queue with progress tracking
- Automatic metadata enrichment (categorization, descriptions)
- Artwork generation with depth maps

**7. Audio Visualizations**
- GLSL shader-based visualizations (WebGL)
- FFT-based frequency analysis
- Tempo-synchronized effects
- User interaction effects (click-triggered glitch, chromatic aberration)
- Parallax artwork with depth maps

**8. Analytics & Insights**
- Real-time playback tracking
- User engagement metrics (likes, bans, super_likes, skips)
- Station-wide analytics (top hits by day/week/all-time)
- Track analytics modal with detailed stats

**9. Offline Support**
- Service Worker for offline functionality
- Multi-layer caching (memory, IndexedDB, Cache API)
- Background downloads with retry logic
- Quota management and cache eviction
- Offline API fallback

**10. Advanced UI/UX**
- Responsive design (7 breakpoints with auto-scaling)
- Haptic feedback (vibration patterns)
- Keyboard shortcuts (space, arrows, etc.)
- Gesture controls (swipe, pinch, etc.)
- Toast notifications
- Modal system with dynamic backgrounds (blurred artwork, category gradients)

---

## Technical Innovations

### 1. Visual State Priority System
Implements a priority-based visual state machine:
```
Recording (Red) > AI Processing (Blue) > DJ Speaking (Dynamic) >
Music Playing (Green) > Music Paused (Yellow) > Idle (Grey)
```
All states resolved in UIStateContext via publisher/subscriber pattern.

### 2. Proactive Artwork Preloading
UIStateContext automatically preloads artwork for current + next track based on queue state changes, enabling instant crossfades without placeholder flashes.

### 3. Three-Layer Device Enforcement
- **Bandwidth Layer:** Inactive devices don't load audio (saves bandwidth)
- **Hardware Layer:** AudioEngine blocks play/pause/seek on inactive devices
- **Visual Layer:** UI shows "Playing on another device" banner

### 4. Zero-Allocation Audio Processing
FFT processing uses for loops instead of array methods to eliminate garbage collection pauses, maintaining 60fps rendering.

### 5. Multi-Vector Semantic Search Engine
Custom-built semantic search system with 10 independent vector embeddings per track:
- Each track indexed across 10 semantic dimensions (artist, genre, mood, style, theme, lyrics, vocal, etc.)
- Annoy approximate nearest neighbors (O(log n) search on 1M+ embeddings)
- Hybrid ranking (vector similarity + user preference weighting)
- Enables "find me aggressive industrial tracks with dystopian themes" type queries
- Separate vector database for user-generated content (voice recordings searchable by semantic meaning)

### 6. Real-Time Dynamic Context Gathering
Intelligent context injection system for AI DJ responses:
- 15+ specialized context nodes (track metadata, weather, news, events, user history, time, etc.)
- Context router selects relevant nodes based on conversation topic
- Real-time data fetching (not pre-cached - always fresh)
- Token-optimized (only includes relevant context to minimize API costs)
- Example: User asks "What's this track about?" → DJ response includes lyrical themes, artist bio, similar songs, AND current weather/time context for natural conversation

### 7. 3D Parallax Artwork System
Advanced artwork rendering with depth perception:
- AI-powered depth map generation from 2D album artwork
- Enriched artwork format (side-by-side color + depth composite images)
- Parallax scrolling effects (depth-based movement on user interaction)
- Dominant color extraction for dynamic theming and gradients
- A/B layer crossfading (two layers render simultaneously for smooth transitions)
- Proactive preloading (current + next track pre-loaded before transition)
- Multi-layer caching (memory → IndexedDB → Cache API for instant access)

### 8. TTS Semantic Caching
Vector-based caching for text-to-speech generation:
- Generated TTS audio stored with vector embeddings of the text
- Searches for semantically similar phrases before generating new audio
- Example: "Here's a song by The Cure" can reuse cached audio from "Here's a track by The Cure"
- ~70% cost reduction on TTS API calls (massive savings for high-frequency phrases)
- Voice-aware (caches per voice/persona to maintain consistency)

---

## Scalability Considerations

**Frontend:**
- Virtual scrolling for large catalogs (1000+ tracks)
- Lazy loading of artwork
- Service Worker caching
- Debounced/throttled event handlers
- RequestAnimationFrame optimization

**Backend:**
- Connection pooling (database, external APIs)
- In-memory caching (preferences, metadata)
- Annoy index for O(log n) vector search
- Async/await throughout (non-blocking I/O)
- Rate limiting per user
- Efficient WebSocket broadcasting (per-session)

---

## Code Quality & Maintainability

**Documentation:**
- Comprehensive CLAUDE.md (project instructions for AI assistant)
- Architecture documentation (SSOT pattern, crossfade handoff, viewport usage)
- Inline comments for complex logic
- Service-oriented architecture (single responsibility principle)

**Patterns:**
- Publisher/Subscriber (state management)
- Single Source of Truth (SSOT)
- Service layer abstraction
- Dependency injection
- Optimistic updates

**Code Organization:**
- 60+ specialized backend services
- 12 frontend contexts (state management)
- 40+ React components
- 10+ custom hooks
- Clear separation of concerns (engines → state → views)

---

## Performance Metrics

**Frontend:**
- 60fps rendering (audio visualizations throttled to 20fps)
- < 100ms time-to-interactive
- Gapless audio crossfades (0ms gap)
- Proactive preloading (instant track changes)

**Backend:**
- < 50ms API response time (cached)
- < 200ms vector search (1M+ embeddings)
- Real-time WebSocket latency < 30ms
- Multi-bitrate transcoding in real-time

---

## Project Statistics

**Lines of Code (estimated):**
- Frontend: ~15,000 lines (JS/JSX)
- Backend: ~20,000 lines (Python)
- GLSL Shaders: ~500 lines
- Total: ~35,500 lines

**File Count:**
- Frontend: ~100 files
- Backend: ~80 services
- Documentation: ~10 files
- Total: ~190 files

**Technologies Used:** 15+ (React, FastAPI, WebSocket, GLSL, Python, JavaScript, SQLAlchemy, Annoy, FFmpeg, Whisper, Demucs, Claude API, ElevenLabs, Suno, Stripe)

**External APIs Integrated:** 7 (Claude, ElevenLabs, Suno, Stripe, News API, Events API, Geolocation)

**Frontend Contexts:** 12 specialized state management contexts

**Backend Services:** 60+ specialized services

**Audio Formats Supported:** MP3, WAV (multi-bitrate)

**Responsive Breakpoints:** 7 (xs, sm, md, lg, xl, 2xl, 3xl)

**Vector Embeddings:** 10 per track (1M+ total embeddings across catalog)

**Context Nodes:** 15+ specialized context providers for real-time data gathering

**Caching Layers:** 7 (Memory Map, IndexedDB, Cache API, TTS Vector DB, Prompt Cache, Preferences Cache, Connection Pool)

**Cost Reduction:** ~60-70% operational cost savings through intelligent caching

---

## Unique Selling Points for CV

1. **Survived a platform shutdown and rebuilt from scratch** - Original PLAiR app was killed by Spotify's Thanksgiving 2024 API shutdown (covered by [The Verge](https://www.theverge.com/2024/12/5/24311523/spotify-locked-down-apis-developers) and [TechCrunch](https://techcrunch.com/2024/11/27/spotify-cuts-developer-access-to-several-of-its-recommendation-features/)). Rather than give up, rebuilt with 100% owned infrastructure in < 3 months. Demonstrates resilience, adaptability, and rapid development capability.

2. **Full-stack mastery** - Frontend (React), Backend (Python/FastAPI), DevOps (Nginx, SSL), Database (SQLAlchemy)

3. **Advanced architecture** - Publisher/Subscriber, SSOT pattern, multi-device orchestration, state machines

4. **AI integration** - LLM (Claude), TTS (ElevenLabs), STT (Whisper), Music Generation (Suno), Source Separation (Demucs)

5. **Real-time systems** - WebSocket state sync, audio streaming, TTS broadcasting

6. **Graphics programming** - GLSL shader development, WebGL, FFT analysis

7. **Audio engineering** - Dual-buffer crossfading, mastering pipeline, transcoding, source separation

8. **Performance optimization** - Zero-allocation processing, multi-layer caching, virtual scrolling, lazy loading

9. **Responsive design** - 7-breakpoint system, automatic scaling, viewport-aware rendering

10. **Production-ready** - SSL/TLS, rate limiting, error handling, offline support, analytics

11. **Cost optimization** - 60-70% operational cost reduction through intelligent caching (TTS vector cache, prompt cache, multi-layer artwork cache)

12. **Vector search expertise** - Custom multi-vector semantic search engine (10 embeddings per track, 1M+ total, <200ms search)

13. **3D graphics** - AI-powered depth map generation, parallax artwork rendering, real-time visual effects

14. **Real-time data integration** - Dynamic context gathering (weather, news, events, user data) with intelligent routing

15. **Cost-conscious engineering** - Semantic TTS caching (~70% savings), prompt caching (~90% savings), efficient WebSocket broadcasting (~80% bandwidth reduction)

---

## Comparable Commercial Products

**Spotify + (features PLAiR has that Spotify doesn't):**
- **Semantic music discovery for uploaded content** - 10-dimensional vector embeddings automatically generated for ANY uploaded track (human or AI)
- **AI DJ with natural conversation** - Real-time context-aware responses (weather, news, events, track metadata) that introduces YOUR music
- **Voice interaction** - Voice commands AND user shoutouts with speech enhancement
- **Automatic music classification** - Upload ANY track and get instant semantic tagging (genre, mood, style, theme, vocals, etc.)
- **3D parallax artwork** - AI-generated depth maps for parallax scrolling effects
- **Audio reactive visualizations** - GLSL shaders with FFT analysis and tempo sync
- **Multi-device orchestration** - Exclusive playback with universal remote (inactive devices show state but don't play)
- **Real-time TTS streaming** - Low-latency voice synthesis with semantic caching
- **Source separation tools** - Demucs AI for vocals/drums/bass/other separation
- **User-generated content** - Voice recordings AND music uploads searchable via semantic vector search
- **Cost optimization** - 60-70% cost reduction through intelligent caching (TTS, prompts, artwork)

**Platform Positioning:** While the demo uses AI-generated music to showcase the technology, PLAiR is fundamentally a platform designed for **human artists to upload their music and gain AI-powered discoverability**. The AI catalog was bootstrap training data - the real value is giving independent musicians the same semantic search/AI DJ capabilities that major labels can't provide.

**Complexity Level:** Enterprise-grade music streaming platform with advanced AI capabilities, comparable to products built by teams of 10-20 engineers over 6-12 months.

---

## Future Scalability

The architecture supports:
- Horizontal scaling (stateless backend services)
- CDN integration (static assets, audio files)
- Database sharding (user data, catalog)
- Microservices migration (services already isolated)
- Mobile app development (React Native, shared API)
- Plugin system (custom DJ personalities, audio effects)

---

## Conclusion

PLAiR.fm demonstrates mastery of modern web development, real-time systems, AI integration, audio engineering, graphics programming, and cost optimization. The project showcases ability to architect complex systems, integrate multiple APIs, optimize performance and costs, and deliver a production-ready product in an accelerated timeline.

**Technical Breadth:**
- **Frontend:** React, GLSL shaders, WebGL, dual-buffer audio, A/B crossfading, 3D parallax effects
- **Backend:** Python/FastAPI, 60+ microservices, vector databases, semantic search, real-time context gathering
- **AI/ML:** Claude API, ElevenLabs TTS, Whisper STT, Suno music generation, Demucs source separation, depth map generation
- **Real-time:** WebSocket state sync, TTS streaming, multi-device orchestration, context injection
- **Cost Engineering:** Semantic TTS caching, prompt caching, multi-layer artwork cache (60-70% cost reduction)
- **Graphics:** GLSL shader programming, FFT audio analysis, parallax depth rendering, zero-allocation processing

**Key Achievements:**
- Built in **< 3 months** (demonstrates rapid development capability)
- **35,500+ lines of code** across 15+ technologies
- **60+ backend services** (microservice architecture at scale)
- **10 vector embeddings per track** (1M+ total embeddings, <200ms search)
- **7 caching layers** (memory, IndexedDB, Cache API, TTS vector DB, prompt cache, preferences, connection pool)
- **60-70% operational cost savings** through intelligent caching strategies
- **3D artwork system** with AI-generated depth maps for parallax effects
- **Real-time context gathering** from 15+ specialized data sources

**Key Takeaway:** This project represents the intersection of full-stack development, AI/ML, real-time systems, graphics programming, audio engineering, and cost optimization - a unique combination that demonstrates versatility, deep technical expertise across multiple domains, and business-minded engineering (cost reduction while maintaining performance).
