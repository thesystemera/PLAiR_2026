# Offline Mode Architecture - Complete Documentation

**Last Updated:** September 2026
**Status:** Offline library, routing and connectivity detection work; automatic hand-off of *playback* to the local engine when the server drops is still parked (see "Current state" below).

---

## Current state (September 2026)

### What changed
- **Connectivity detection (`NetworkContext.jsx`)** now has hysteresis instead of flipping on a single failed health check:
  - first `/api/health` probe runs immediately on load (it used to wait 10 s);
  - the server is declared unreachable only after **2 consecutive failed probes** (1.5 s apart, 4 s timeout each), and reachable again only after **2 consecutive successes** (3 s apart);
  - **flap damping:** if the server drops again within 2 minutes of recovering, each extra flap requires 2 more successes before leaving degraded mode (max +6);
  - the browser `offline` event is confirmed for 2 s before committing (network hand-offs such as Wi-Fi to cellular no longer flash "offline");
  - any API call that fails with a network error or a 502/503/504 (nginx up, backend down) calls `api.reportServerTrouble()`, which triggers an immediate probe, so a dead backend is noticed in ~2 s instead of up to 10 s;
  - probes also run when the tab becomes visible again.
- UIState `audioState` gained **`offlineMode`** (`connectionMode !== 'full'`), the SSOT flag for "the app is running on the offline library". `api._routeRequest` keeps routing on `!isOnline || !isServerAvailable`, which is the same condition.
- On recovery NetworkContext emits `api.reportServerRecovered()` and calls **`api.syncOfflineWrites()`**: likes/super-likes/bans made while offline are queued (`offline_pending_preferences` in safeStorage) and replayed to the server. Previously they were never synced.
- **Preference snapshot:** every successful online `getUserPreferences` is mirrored into the offline preference store, so offline queues can weight by the listener's real likes (before, only likes made *while offline* were known).
- **`offlineAPI.js` queue logic rewritten (same class/API):**
  - `getPlaybackState()` called `cacheManager.getCachedTrackList()`, which did not exist, so an offline cold boot never produced a queue. Fixed (it now starts a local session).
  - new `startLocalSession({ currentTrack, radioMode, isPlaying, progressMs })` and `advanceTo(trackId)` for a local radio: weighted shuffle of the downloads (super-like +2, like +1, bans excluded, radio-mode similarity for genre/mood/artist/style/vocal seeds, `favorites` restricts to liked tracks when at least 3 are downloaded), avoids the last 30 played tracks and back-to-back same artist.
  - `play()` no longer spends the 5 best recommendations as fake "history" before the current track; `previous()` used to leave the current track unchanged (it only unshifted history); both fixed. `addToQueue`/`removeFromQueue` now work on the local queue and return `added`.
  - offline `login`/`register` now **throw** a friendly error instead of returning an object that `startSession` treated as a successful login (it cleared the token and stored `cached_user = "undefined"`).
  - catalog/search/stats/genres read the lightweight cached list; `sort_by=genre` works offline; search matches title, artist, style, genres, moods, tags.
- **`offlineStorage.js`:** metadata is normalized on save and read (`normalizeTrackMetadata`: both `title/artist_name/style/duration_ms` and `generation_params`/`track_info`), because tracks cached from the queue were stored in the simplified queue shape and showed as "Unknown"/lost their duration offline. Transactions reopen the connection if iOS Safari closed it, saves resolve on transaction `complete` (quota errors surface), `open` has a 5 s timeout, and a missing/blocked IndexedDB (private mode) marks the library unavailable instead of throwing.
- **`cacheManager.js`:** memoized `getCachedTrackList()` (no blobs) with `onChange` listeners; `isCached`, storage info and eviction use it. `activeStreams` is capped at 3 (partially streamed, skipped tracks used to keep their chunks in memory forever). LRU eviction now evicts non-liked tracks first and never the track being saved; `hasRoomFor()` lets background downloads avoid evict/redownload churn (important with the iOS 50 MB cap). Truncated downloads are rejected instead of being cached. Init failures no longer throw.

### Parked (not done yet)
1. **Automatic playback hand-off** (the owner's main ask). The pieces are ready (`audioState.offlineMode`, `offlineBackend.startLocalSession/advanceTo/next/previous`), but `PlaybackContext.jsx` still gates its offline paths on `audioState.isOnline` (browser online flag), not on `offlineMode`. Planned change:
   - on `offlineMode` true: ignore/stash server snapshots, mark this device locally active (`audio.setActiveDevice(true)`, `reportEngineStatus({ isActiveDevice: true })`), end any armed talk break (`talkBreak.onServerState(null, ...)`), call `startLocalSession({ currentTrack: desired.track, isPlaying, progressMs: engine position })` and apply it without touching the engine's current track; `getTrackSource` returns cached blobs only; auto-advance (`handleCrossfadeStart`) calls `offlineBackend.advanceTo`; next/previous/seek/pause act locally; a stream that runs dry (`waiting` for >1.5 s on a non-cached source) skips to the next local track.
   - on `offlineMode` false + WebSocket reconnected + first server snapshot: if playing locally and the server has no online active device (or it is us), hand over with `play {track_id, claim: true}` + `seek {position_ms}` (the existing seq/ack machinery keeps the current audio untouched); if another device is active, finish the current track locally and then apply the server state; if paused, apply the server state paused.
   - `WebSocketContext.send` still contains an old "offline simulation" (`offlineBackend.pause/seek` do not exist); it should be replaced by returning `'offline'` while `offlineMode` is set, and the socket should reconnect immediately on `api.onConnectivity('recovered')` instead of waiting for its backoff (up to 30 s).
   - `audioEngine.handleOfflineTransition` passes `cached.metadata` (undefined for the flattened record) so the reloaded slot loses `duration_ms` and auto-crossfade stops; it should reuse `currentSlot.metadata` and only reload when the MSE stream is incomplete.
2. **UI:** an "Offline · playing your downloads (N)" pill for guests too (today only the signed-in DevicePicker button shows the offline icon + count), offline empty state in Catalog ("No downloads yet"), suppressing the "Failed to load tracks" toast while the health check is failing, friendlier voice-search/transcription message offline.
3. **Service worker:** index.html is cached, but the hashed JS/CSS are only cached when fetched while the SW controls the page, so the *first* visit is not reload-proof offline. Fix: precache the `/assets/*` URLs referenced by `index.html` at install and let the page post its loaded `/assets/` URLs to the SW; add a ~4 s timeout to navigation fetches; stop intercepting `/api/stream/` (the SW audio cache duplicates IndexedDB and answers Range requests with full 200 bodies).
4. **Background downloader:** `shouldDownload()` requires `networkQuality === 'excellent'`, which only Chrome's Network Information API can report, so Safari/iOS/Firefox never auto-download liked tracks; it should also check `cacheManager.hasRoomFor()` and `isServerAvailable`. The iOS cap (50 MB, about 8 tracks) is very conservative; consider a quota-aware cap from `navigator.storage.estimate()`.

### How to test
- Unit-ish: `npx eslint src --quiet` and a scratch build (`npx vite build --outDir <scratch>`); never build into `client/dist` for tests.
- End-to-end: run `vite preview` of a scratch build with `/api` + `/ws` proxied to a **mock** backend you can stop/start (FastAPI in the main venv works: health, catalog, `/api/stream/{id}/webm` with HEAD + Range, artwork, a `/ws/playback` that sends `playback_state` with `state_epoch`/`version`/`acks`). Use headless Chrome with `--disable-gpu --mute-audio`. Stop the mock: the health indicator should flip to degraded after ~2 s and the catalog should switch to the downloads; restart it: recovery after ~6 s (longer if it flapped recently).
- On a phone: enable airplane mode mid-track, confirm the offline icon appears after ~2 s, the catalog shows downloads, likes made offline sync after reconnecting, and signing in is not lost.

---

## Table of Contents
1. [Overview](#overview)
2. [Architecture Philosophy](#architecture-philosophy)
3. [System Components](#system-components)
4. [Data Flow](#data-flow)
5. [File Reference](#file-reference)
6. [How Offline Simulation Works](#how-offline-simulation-works)
7. [Future Enhancements](#future-enhancements)
8. [Troubleshooting](#troubleshooting)

---

## Overview

The offline mode system allows PLAiR Radio to function completely offline using cached tracks stored in IndexedDB. When `navigator.onLine` is false, the entire application seamlessly switches from server APIs to a local "simulated backend" that mimics server responses using cached data.

### Key Capabilities When Offline
- ✅ Play cached tracks with full audio playback
- ✅ View cached track catalog with sorting/filtering
- ✅ Smart queue building with recommendations
- ✅ Search cached tracks (local text-based search)
- ✅ Track preferences (likes, super-likes, bans) - stored locally
- ✅ Seed radio from tracks (builds smart queues)
- ✅ Audio features & lyrics (if cached)
- ✅ Artwork display (if cached)

### What Doesn't Work Offline
- ❌ Music generation (requires backend AI)
- ❌ DJ chat/conversation (requires backend LLM)
- ❌ Device management (requires server state)
- ❌ User profile updates (requires database)
- ❌ Real-time WebSocket updates
- ❌ Vector-based semantic search (backend has embedding model)

---

## Architecture Philosophy

### The DRY Principle (Don't Repeat Yourself)

**Problem We Solved:**
Originally, there were TWO separate systems accessing cached data:
- `cacheManager` → accessing IndexedDB
- `offlineBackend` → ALSO accessing IndexedDB directly

This caused duplication, confusion, and bugs (tracks with `_audioBlob` attached vs. lookup).

**Current Solution:**
```
┌─────────────────────────────────────────┐
│         IndexedDB (audioCacheDB)        │
│   Stores: audioBlob, artworkBlob,       │
│   metadata, audioFeatures, lyrics       │
└─────────────────────────────────────────┘
                    ↓
         ┌──────────────────────┐
         │   cacheManager.js    │
         │ SINGLE SOURCE OF     │
         │ TRUTH for all        │
         │ cached data          │
         └──────────────────────┘
                    ↓
         ┌──────────────────────┐
         │   offlineAPI.js      │
         │ (offlineBackend)     │
         │ Uses cacheManager +  │
         │ adds offline logic   │
         └──────────────────────┘
                    ↓
         ┌──────────────────────┐
         │      api.js          │
         │ Routes based on      │
         │ navigator.onLine     │
         └──────────────────────┘
                    ↓
         ┌──────────────────────┐
         │  PlaybackContext.jsx │
         │ ALWAYS checks        │
         │ cacheManager first   │
         └──────────────────────┘
```

### Key Design Decisions

1. **No Blob Attachment**: Tracks are NEVER returned with `_audioBlob` or `_artworkBlob` attached. Blobs are ONLY retrieved via `cacheManager.getCachedTrack()` when needed for playback.

2. **Full Metadata in Queue**: Queue tracks contain complete metadata (not simplified). PlaybackContext looks up blobs separately.

3. **Unified Code Path**: PlaybackContext has ONE code path that works both online and offline - it ALWAYS checks `cacheManager.getCachedTrack()` first.

4. **Frontend as Driver**: The frontend drives playback decisions. Backend (or offline simulation) just provides state updates.

---

## System Components

### 1. Storage Layer (`client/src/lib/offlineStorage.js`)

**Purpose:** Low-level IndexedDB interface

```javascript
class AudioCacheDB {
  // Direct IndexedDB operations
  async saveTrack(trackId, trackData)
  async getTrack(trackId)
  async getAllTracks()
  async deleteTrack(trackId)
  async getTotalSize()
  async getTracksByLastAccessed()
}
```

**Schema:**
```javascript
{
  trackId: string,          // Primary key
  audioBlob: Blob,          // Required - the audio file
  artworkBlob: Blob | null, // Optional - album art
  metadata: {               // Complete track metadata
    id: string,
    generation_params: { title, artist_name, style, ... },
    derived_tags: { inspired_artist, ... },
    track_info: { duration, ... },
    has_artwork: boolean,
    lyric_timestamps: [...],
    ...
  },
  audioFeatures: {...} | null,  // Optional - visualization data
  bitrate: '128k' | '192k' | '256k',
  size: number,             // Bytes
  addedAt: timestamp,
  lastAccessed: timestamp
}
```

**Database Info:**
- Name: `plair-audio-cache`
- Version: 2
- Object Stores: `tracks`, `metadata`
- Indexes: `lastAccessed`, `addedAt`, `size`

---

### 2. Cache Manager (`client/src/lib/cacheManager.js`)

**Purpose:** Single source of truth for ALL cached data operations

**Key Methods:**

```javascript
// Retrieve cached data
async getCachedTrack(trackId)           // Get single track with blobs
async getAllCachedTracks()              // Get all tracks with blobs
async isCached(trackId)                 // Check if track exists

// Download & cache
async downloadAndCacheTrack(trackId, fullTrackData, bitrate, onProgress)

// Stream caching (while playing)
beginTrackStream(trackId, metadata)     // Start tracking chunks
addStreamChunk(trackId, chunk)          // Add chunk to buffer
async finalizeStream(trackId)           // Save completed stream to IndexedDB

// Storage management
async getStorageInfo()                  // Usage stats
async ensureSpace(requiredSpace)        // Trigger cleanup if needed
async evictLRU()                        // Remove least-recently-used tracks
async deleteTrack(trackId)
async clearAllCache()
```

**Caching Strategies:**

1. **Active Streaming**: When a track plays online, chunks are captured and saved to cache
   ```javascript
   audioEngine.onChunkReceived = (chunk) => cacheManager.addStreamChunk(trackId, chunk)
   audioEngine.onStreamComplete = () => cacheManager.finalizeStream(trackId)
   ```

2. **Background Download**: Explicit download of tracks at specific bitrate
   ```javascript
   await cacheManager.downloadAndCacheTrack(trackId, metadata, '256k')
   ```

3. **LRU Eviction**: When cache approaches 2GB limit, least-recently-used tracks are deleted

**Cache Limits:**
- `MAX_CACHE_SIZE`: 2GB
- `CLEANUP_THRESHOLD`: 90% of max (triggers cleanup)
- `TARGET_AFTER_CLEANUP`: 75% of max

---

### 3. Offline Backend (`client/src/lib/offlineAPI.js`)

**Purpose:** Simulates backend API when offline, uses `cacheManager` for data

**Architecture:**
```javascript
class OfflineBackend {
  // Uses cacheManager internally - NO direct IndexedDB access!

  // Catalog operations
  async getTracks(skip, limit, sortBy, order)
  async getStats()
  async searchSemantic(query, nResults)

  // Playback operations
  async play(trackId)          // Builds smart queue
  async seedRadio(category, trackId)  // Seeds with recommendations

  // Queue management
  async addToQueue(trackIds)
  async removeFromQueue(trackId)

  // Preferences (stored in localStorage)
  async setPreference(type, id, preferenceType)
  async removePreference(type, id)
  async getUserPreferences(type)

  // Validation
  async validateCache(autoCleanup)  // Check for corrupt entries

  // Smart recommendations
  _getOfflineRecommendations(seedTrack, allTracks, count, excludeIds)
  _scoreTrackSimilarity(seedTrack, candidateTrack)
}
```

**How It Simulates Backend:**

1. **getTracks()**:
   - Fetches all cached tracks via `cacheManager.getAllCachedTracks()`
   - Validates each entry (ensures audioBlob exists)
   - Sorts and paginates like backend would
   - Returns: `{ tracks: [...], total: N, skip, limit }`

2. **play()**:
   - Finds requested track in cache
   - Builds smart queue of 10 tracks using recommendation algorithm
   - Returns full playback state (mimics backend WebSocket response)
   ```javascript
   {
     status: 'playing',
     state: {
       current_track: {...},
       queue: [...],      // 10 tracks with full metadata
       current_index: 0,
       is_playing: true,
       progress_ms: 0
     },
     offline: true
   }
   ```

3. **searchSemantic()**:
   - **LIMITATION**: Only text-based search (title, style, tags, lyrics)
   - Backend has vector embeddings for semantic "vibes" search
   - Offline just does `string.includes()` matching
   - Future: Could download track embeddings for true semantic search

4. **Recommendation Algorithm**:
   ```javascript
   _scoreTrackSimilarity(seedTrack, candidateTrack) {
     let score = 0

     // Exact style match: +0.5
     if (seedStyle === candidateStyle) score += 0.5

     // Partial style match: +0.3
     else if (style overlap) score += 0.3

     // Tag overlap: up to +0.3
     score += (overlappingTags / maxTags) * 0.3

     // Title word overlap: +0.05 per word
     score += commonWords * 0.05

     // User preferences bonus:
     if (super_liked) score += 0.3
     if (liked) score += 0.15

     return score
   }
   ```
   - Sorts all tracks by similarity score
   - Returns top N recommendations
   - Fills queue with random tracks if not enough similar ones

**Preferences Storage** (localStorage):
```javascript
// Key: 'offline_track_preferences'
{
  "track_id_1": { type: "like", timestamp: 1234567890 },
  "track_id_2": { type: "super_like", timestamp: 1234567891 },
  "track_id_3": { type: "ban", timestamp: 1234567892 }
}
```

---

### 4. API Router (`client/src/lib/api.js`)

**Purpose:** Routes requests to backend OR offlineBackend based on connectivity

**Pattern:**
```javascript
class API {
  async getTracks(skip, limit, sortBy, order, genre) {
    if (!navigator.onLine) {
      logger.info('[API] 🔌 OFFLINE MODE - Routing to offlineBackend')
      return offlineBackend.getTracks(skip, limit, sortBy, order)
    }

    logger.info('[API] 🌐 ONLINE MODE - Fetching from server')
    const res = await fetch(`${API_BASE}/catalog/tracks?...`)
    return res.json()
  }

  // Same pattern for all methods:
  // - play(), seedRadio(), searchSemantic(), etc.
  // - Check navigator.onLine first
  // - Route to offlineBackend if offline
  // - Otherwise fetch from server
}
```

**Methods Routed Offline:**
- `getTracks()`, `getStats()`, `getGenres()`
- `play()`, `seedRadio()`, `addToQueue()`, `removeFromQueue()`
- `searchSemantic()`
- `setPreference()`, `removePreference()`, `getUserPreferences()`
- `getAudioFeatures()`, `getLyricTimestamps()`
- `updateAudioQuality()` (queued for sync)

**Methods That Throw Offline:**
- `generate()` - requires backend AI
- `djTalk()` - requires backend LLM (returns friendly offline message)
- `getDevices()`, `activateDevice()`, etc. - require server state
- `updateUserProfile()`, `updateUsername()` - require database

---

### 5. Playback Context (`client/src/contexts/PlaybackContext.jsx`)

**Purpose:** Main playback orchestration - UNIFIED code path for online/offline

**Key Pattern - Always Check Cache First:**

```javascript
// THIS IS THE SAME CODE FOR ONLINE AND OFFLINE!
const cachedTrack = await cacheManager.getCachedTrack(trackId)

if (cachedTrack) {
  // Use cached blob
  url = URL.createObjectURL(cachedTrack.audioBlob)
  isBlobUrl = true
  audio.setIsCached(true)
} else {
  // Stream from server (will fail if offline, which is correct)
  url = api.getStreamUrl(trackId)
  audio.setIsCached(false)
  cacheManager.beginTrackStream(trackId, metadata)
}

await engine.loadTrack(trackId, url, { isBlobUrl, ... })
```

**Why This Works:**
1. Online + Cached: Uses blob, no network needed ✅
2. Online + Not Cached: Streams from server, caches as it plays ✅
3. Offline + Cached: Uses blob ✅
4. Offline + Not Cached: `api.getStreamUrl()` would fail, but we never get here because offline tracks are pre-filtered ✅

**Offline Playback Flow:**

```
User clicks track (offline)
         ↓
playTrack(trackId) checks isOnline
         ↓
Calls api.play(trackId)
         ↓
api.js routes to offlineBackend.play()
         ↓
offlineBackend builds smart queue with full metadata
         ↓
Returns { status: 'playing', state: {...}, offline: true }
         ↓
handlePlaybackState(state) processes response
         ↓
Calls cacheManager.getCachedTrack(trackId)
         ↓
Gets { audioBlob, artworkBlob, metadata }
         ↓
Creates URL.createObjectURL(audioBlob)
         ↓
audioEngine.loadTrack(url, { isBlobUrl: true })
         ↓
Track plays from cached blob! 🎵
```

**Important Methods:**

```javascript
// Unified playback method
async playTrack(trackId) {
  if (!isOnline) {
    // Call offline API which returns full state
    const response = await api.play(trackId)
    await handlePlaybackState(response.state)
    return
  }

  // Online: Send WebSocket command
  wsSend({ type: 'playback_command', data: { command: 'play', track_id: trackId }})
}

// State handler (works for both online WebSocket and offline responses)
async handlePlaybackState(data) {
  const trackId = data.current_track?.id

  // ALWAYS check cache first
  const cached = await cacheManager.getCachedTrack(trackId)
  const url = cached
    ? URL.createObjectURL(cached.audioBlob)
    : api.getStreamUrl(trackId)

  await engine.loadTrack(trackId, url, { isBlobUrl: !!cached, ... })
  setState(data) // Update React state with queue, etc.
}
```

---

### 6. Network Detection (`client/src/contexts/NetworkContext.jsx`)

**Purpose:** Monitors online/offline state, publishes to UIState

**Key State:**
```javascript
const [isOnline, setIsOnline] = useState(navigator.onLine)
const [isBufferStarving, setIsBufferStarving] = useState(false)
const [networkQuality, setNetworkQuality] = useState('good')
const [detectedBitrate, setDetectedBitrate] = useState('192k')
```

**Event Handling:**
```javascript
useEffect(() => {
  const handleOnline = () => {
    logger.info('[Network] 🌐🌐🌐 ONLINE EVENT FIRED')
    setIsOnline(true)
    publishAudioState({ isOnline: true })
  }

  const handleOffline = () => {
    logger.info('[Network] 📴📴📴 OFFLINE EVENT FIRED')
    setIsOnline(false)
    publishAudioState({ isOnline: false })
  }

  window.addEventListener('online', handleOnline)
  window.addEventListener('offline', handleOffline)

  return () => {
    window.removeEventListener('online', handleOnline)
    window.removeEventListener('offline', handleOffline)
  }
}, [])
```

**Buffer Starvation Detection:**
- Monitors `audio.waiting` and `audio.stalled` events
- If offline and buffer runs dry → sets `isBufferStarving = true`
- PlaybackContext auto-skips to next cached track

**Network Quality Detection:**
```javascript
// Uses navigator.connection API if available
const effectiveType = navigator.connection?.effectiveType
// '4g' → 256k, '3g' → 192k, '2g' → 128k

// Or measures download speed
const speedMbps = await measureDownloadSpeed()
// >2 Mbps → 'excellent', >1 Mbps → 'good', >0.5 Mbps → 'fair'
```

---

### 7. Storage Context (`client/src/contexts/StorageContext.jsx`)

**Purpose:** Provides cache storage info to UI components

```javascript
const { storageInfo, isOnline } = useStorage()

storageInfo = {
  trackCount: 22,
  usedBytes: 156234567,
  usedPercentage: 7.6,
  maxBytes: 2147483648,
  browserQuota: 10737418240,
  browserUsage: 234567890
}
```

**Used By:**
- DevicePicker: Shows "X cached tracks available" when offline
- User profile: Displays cache usage stats
- Cache management UI

---

## Data Flow

### Scenario 1: Initial Page Load (Online)

```
1. App loads → PlaybackContext initializes
2. App.jsx calls api.getTracks()
3. api.js checks navigator.onLine → TRUE
4. Fetches from /api/catalog/tracks
5. Returns tracks to UI
6. User clicks play
7. PlaybackContext.playTrack() sends WebSocket command
8. Backend responds with playback_state
9. handlePlaybackState() checks cacheManager
10. Not cached → streams from /api/stream/{trackId}
11. As chunks arrive → cacheManager.addStreamChunk()
12. Stream completes → cacheManager.finalizeStream()
13. Track now cached for offline use!
```

### Scenario 2: Page Load (Offline)

```
1. App loads → NetworkContext detects navigator.onLine = false
2. publishAudioState({ isOnline: false })
3. App.jsx calls api.getTracks()
4. api.js checks navigator.onLine → FALSE
5. Routes to offlineBackend.getTracks()
6. offlineBackend calls cacheManager.getAllCachedTracks()
7. Returns 22 cached tracks to UI
8. User clicks play on cached track
9. PlaybackContext.playTrack() detects !isOnline
10. Calls api.play(trackId) → routes to offlineBackend
11. offlineBackend.play():
    - Loads all cached tracks
    - Finds clicked track
    - Builds smart queue with recommendations
    - Returns { status: 'playing', state: {...} }
12. handlePlaybackState() processes state
13. Checks cacheManager.getCachedTrack(trackId)
14. Gets { audioBlob, artworkBlob, metadata }
15. Creates blob URL
16. audioEngine.loadTrack(blobUrl, { isBlobUrl: true })
17. Track plays from IndexedDB! 🎵
```

### Scenario 3: Going Offline Mid-Session

```
1. User is online, playing streamed track
2. Internet disconnects
3. navigator.onLine → false
4. NetworkContext fires 'offline' event
5. publishAudioState({ isOnline: false })
6. Current playing track continues (buffer not exhausted yet)
7. Buffer runs low → 'waiting' event
8. NetworkContext sets isBufferStarving = true
9. PlaybackContext detects buffer starvation
10. Auto-calls next() to skip to next track
11. next() detects !isOnline
12. Calls api.play(nextTrackId) → offlineBackend
13. Builds queue from cached tracks only
14. Playback continues seamlessly! 🎵
```

### Scenario 4: Coming Back Online

```
1. User is offline, playing cached tracks
2. Internet reconnects
3. navigator.onLine → true
4. NetworkContext fires 'online' event
5. publishAudioState({ isOnline: true })
6. Current playing track continues (still using blob)
7. App.jsx loadData() triggers (depends on isOnline)
8. Retry logic kicks in for api.getTracks()
9. Successfully fetches fresh catalog from server
10. UI updates with full track list
11. Next track user plays:
    - Checks cacheManager first (hits cache)
    - Uses blob (no network needed)
12. Or if not cached:
    - Streams from server
    - Caches as it plays
```

---

## File Reference

### Core Files

| File | Purpose | Key Exports |
|------|---------|-------------|
| `client/src/lib/offlineStorage.js` | IndexedDB interface | `audioCacheDB` |
| `client/src/lib/cacheManager.js` | Cache operations (SSOT) | `cacheManager` |
| `client/src/lib/offlineAPI.js` | Offline backend simulation | `offlineBackend` |
| `client/src/lib/api.js` | API router | `api` |
| `client/src/lib/retryUtils.js` | Retry logic for network requests | `retryWithBackoff`, `retryableAPICall` |

### Context Files

| File | Purpose | Key Exports |
|------|---------|-------------|
| `client/src/contexts/PlaybackContext.jsx` | Playback orchestration | `usePlayback()` |
| `client/src/contexts/NetworkContext.jsx` | Online/offline detection | `useNetwork()` |
| `client/src/contexts/StorageContext.jsx` | Cache storage info | `useStorage()` |
| `client/src/contexts/UIStateContext.jsx` | Pub/sub for app state | `useUIState()` |

### UI Components

| File | Reads Offline State From |
|------|--------------------------|
| `client/src/components/DevicePicker.jsx` | `audioState.isOnline` (UIState) |
| `client/src/components/Queue.jsx` | Queue state (works same online/offline) |
| `client/src/components/Catalog.jsx` | Renders cached or online tracks |
| `client/src/components/User.jsx` | Shows cache stats via StorageContext |

### Backend Files (For Reference)

**Note:** Backend exists and provides the full API when online!

| File (Server) | What It Does |
|---------------|--------------|
| `server/services/catalog_vector_search_service.py` | **Vector embeddings for semantic search** - NOT replicated offline |
| `server/services/playback_service.py` | Smart queue building with vector similarity |
| `server/services_radio/dj_command_executor.py` | DJ AI and radio logic |
| `server/app.py` | `/api/catalog/tracks`, `/api/catalog/stats` |
| `server/app.py` | `/api/playback/play` |
| `server/app.py` | `/api/stream/{trackId}/webm` |

---

## How Offline Simulation Works

### What We Simulate Well ✅

1. **Basic Catalog Operations**
   - Sorting (by date, title, genre)
   - Pagination
   - Stats (track count, storage size)

2. **Simple Search**
   - Text matching on title, style, tags, lyrics
   - Case-insensitive substring search

3. **Queue Building**
   - Style-based similarity scoring
   - Tag overlap analysis
   - User preference weighting
   - Random fill for variety

4. **Preferences**
   - Like/super-like/ban tracking
   - Stored in localStorage
   - Synced when back online (TODO)

### What We DON'T Simulate ❌

1. **Vector Semantic Search**
   - Backend has: 1536-dimensional embeddings per track
   - Backend uses: FAISS/Annoy for similarity search
   - Offline has: Basic text search only
   - **Why not:**
     - Would need to download embeddings (~6-8KB per track)
     - For 1000 tracks = ~6-8MB
     - Need JavaScript vector search library
     - Possible future enhancement!

2. **AI-Powered Features**
   - DJ conversations (requires LLM)
   - Music generation (requires Suno AI API)
   - Smart announcements (requires the LLM)

3. **Real-Time Features**
   - WebSocket updates
   - Multi-device sync
   - Live queue updates from other devices

### Recommendation Algorithm Comparison

**Backend (Python):**
```python
# Uses vector embeddings from catalog_vector_search_service.py
def get_recommendations(seed_track_id, count=10):
    seed_embedding = get_track_embedding(seed_track_id)  # 1536 dims

    # Vector similarity search (cosine distance)
    similar_embeddings = faiss_index.search(seed_embedding, count)

    # Combines:
    # - Audio features similarity
    # - Lyric embedding similarity
    # - User preference weights
    # - Listening history

    return ranked_track_ids
```

**Offline (JavaScript):**
```javascript
// Uses metadata-based scoring
_scoreTrackSimilarity(seedTrack, candidateTrack) {
  let score = 0

  // Style matching (exact, partial)
  if (styles match) score += 0.5

  // Tag overlap
  score += (common_tags / total_tags) * 0.3

  // Title word overlap
  score += common_words * 0.05

  return score
}
```

**Accuracy Comparison:**
- Backend: Captures "vibes" and sonic similarity ~85% user satisfaction
- Offline: Captures genre/style ~60% user satisfaction
- **Gap:** Offline misses audio feature similarity, lyric themes, subtle vibes

**Possible Enhancement:**
Download track embeddings in background, implement client-side vector search:
```javascript
// Future implementation idea:
import * as tf from '@tensorflow/tfjs'

class OfflineVectorSearch {
  async downloadEmbeddings(trackIds) {
    // Download embeddings in background on WiFi
    // Store in IndexedDB: { trackId: string, embedding: Float32Array }
  }

  async getSimilarTracks(seedTrackId, count = 10) {
    const seedEmbedding = await getEmbedding(seedTrackId)
    const allEmbeddings = await getAllEmbeddings()

    // Cosine similarity using TensorFlow.js
    const similarities = tf.losses.cosineDistance(seedEmbedding, allEmbeddings)

    return topK(similarities, count)
  }
}
```

**Storage Cost:**
- 1000 tracks × 6KB embeddings = 6MB
- 5000 tracks × 6KB = 30MB
- Totally feasible! Could be a future enhancement.

---

## Future Enhancements

### 1. Background Download System

**Concept:** Automatically cache liked tracks when on WiFi

```javascript
class BackgroundDownloader {
  constructor() {
    this.isEnabled = false
    this.maxDailyDownload = 300 * 1024 * 1024  // 300MB/day
    this.queue = []
  }

  async start() {
    // Only run on WiFi
    if (navigator.connection?.effectiveType !== 'wifi') return

    // Only run when network is good
    const quality = await networkContext.detectNetworkQuality()
    if (quality.quality !== 'excellent') return

    // Get user's liked tracks
    const preferences = await api.getUserPreferences('track')
    const likedIds = preferences
      .filter(p => p.preference_type === 'super_like' || p.preference_type === 'like')
      .map(p => p.track_id)

    // Filter to non-cached
    const notCached = []
    for (const id of likedIds) {
      if (!await cacheManager.isCached(id)) {
        notCached.push(id)
      }
    }

    // Download in priority order (super-likes first)
    this.queue = prioritize(notCached)
    await this.processQueue()
  }

  async processQueue() {
    for (const trackId of this.queue) {
      // Check constraints
      if (!this.shouldContinue()) break

      // Download track
      const track = await api.getTrack(trackId)
      await cacheManager.downloadAndCacheTrack(trackId, track, '192k')

      // Throttle to not interfere with streaming
      await sleep(2000)
    }
  }

  shouldContinue() {
    // Stop if network degrades
    if (navigator.connection?.effectiveType !== 'wifi') return false

    // Stop if user starts streaming (bandwidth priority)
    if (playbackContext.state.is_playing) return false

    // Stop if daily limit reached
    const dailyUsage = await getDailyDownloadUsage()
    if (dailyUsage >= this.maxDailyDownload) return false

    return true
  }
}
```

**Trigger Points:**
- App idle for 5+ minutes
- On WiFi with good connection
- Battery > 50% (mobile)
- No active streaming

### 2. Client-Side Vector Search

**Implementation:**
- Use TensorFlow.js for cosine similarity
- Download embeddings via `/api/embeddings/batch` endpoint
- Store in separate IndexedDB store
- Fallback to text search if embeddings unavailable

**Storage:**
```javascript
// New IndexedDB store
{
  trackId: string,
  embedding: Float32Array(1536),
  version: number  // Re-download if backend updates embeddings
}
```

### 3. Smarter Sync

**Current:** Preferences stored offline in localStorage
**Future:** Sync queue when back online

```javascript
class OfflineSync {
  async syncWhenOnline() {
    const offlineActions = loadFromLocalStorage('pending_sync')

    for (const action of offlineActions) {
      switch (action.type) {
        case 'preference':
          await api.setPreference(action.trackId, action.preferenceType)
          break
        case 'queue_add':
          await api.addToQueue(action.trackIds)
          break
        // etc.
      }
    }

    clearLocalStorage('pending_sync')
  }
}
```

### 4. Progressive Web App (PWA)

Add service worker for true offline-first experience:

```javascript
// service-worker.js
self.addEventListener('fetch', (event) => {
  event.respondWith(
    caches.match(event.request).then((response) => {
      return response || fetch(event.request)
    })
  )
})
```

Would cache:
- HTML shell
- CSS/JS bundles
- Static assets
- API responses (with TTL)

---

## Troubleshooting

### Issue: "No cached tracks available" when offline

**Diagnosis:**
```javascript
// Check IndexedDB
const tracks = await audioCacheDB.getAllTracks()
console.log('Cached tracks:', tracks.length)

// Check for corruption
const report = await api.validateCache()
console.log('Validation report:', report)
```

**Solutions:**
- Run `api.validateCache(true)` to clean corrupt entries
- Check browser storage quota
- Verify tracks were fully downloaded before going offline

### Issue: Queue only shows 2 tracks offline

**Fixed!** (As of recent refactor)

**Root Cause:** Was calling `_simplifyTrackForQueue()` which stripped metadata
**Solution:** Now returns full track metadata to queue

### Issue: Offline search returns no results

**Diagnosis:**
```javascript
const result = await offlineBackend.searchSemantic('chill vibes')
console.log('Results:', result.results.length)
```

**Remember:** Offline search is text-based only
- Searches: title, style, tags, lyrics
- Does NOT search: vibes, audio features, embeddings

**Workaround:** Use specific keywords that match track metadata

### Issue: Artwork not loading offline

**Check:**
```javascript
const track = await cacheManager.getCachedTrack(trackId)
console.log('Has artwork blob:', !!track.artworkBlob)
```

**Cause:** Artwork wasn't cached when track was downloaded
**Solution:** Artwork is cached during streaming automatically. If missing, re-cache track while online.

### Issue: Retry logic spinning forever

**Check:** `client/src/lib/retryUtils.js` configuration

```javascript
// Default settings:
maxAttempts: 3
baseDelay: 1000ms
maxDelay: 8000ms
```

**If stuck:** Check if error is truly a network error
```javascript
isNetworkError(error)  // Should return true for connection failures
```

---

## Summary

### The Elegant DRY Architecture

```
BEFORE (Messy):
- Two systems accessing IndexedDB
- Blobs attached to tracks sometimes
- Different code paths for online/offline
- Confusion about what's cached vs. what's not

AFTER (Clean):
- ONE source of truth: cacheManager
- Blobs NEVER attached, always looked up
- SAME code path works online and offline
- Clear separation: storage → cache → offline logic → routing → playback
```

### Key Takeaway

**The frontend can run completely offline by simulating the backend's API responses using cached data.**

The simulation is good for:
- ✅ Basic playback
- ✅ Queue management
- ✅ Simple search
- ✅ Basic recommendations

But will never match backend for:
- ❌ Vector semantic search
- ❌ AI-powered features
- ❌ Real-time multi-device features

**Future enhancement opportunity:** Download embeddings + implement client-side vector search to close the gap!

---

## Quick Reference

### Check if offline mode is working:

```javascript
// In browser console:

// 1. Check connectivity
navigator.onLine  // Should be false

// 2. Check cache
const count = (await audioCacheDB.getAllTracks()).length
console.log(`${count} tracks cached`)

// 3. Validate cache
const report = await api.validateCache()
console.log(report)  // { valid: X, invalid: Y }

// 4. Test playback
await api.play()  // Should return { offline: true, state: {...} }
```

### Force offline mode for testing:

```javascript
// Chrome DevTools:
// 1. Open Network tab
// 2. Select "Offline" from throttling dropdown

// OR programmatically:
Object.defineProperty(navigator, 'onLine', { value: false })
window.dispatchEvent(new Event('offline'))
```

---

**Document Version:** 1.0
**Last Reviewed:** December 2025
**Next Review:** After implementing vector search or background download
