# Offline Mode Architecture - Complete Documentation

**Last Updated:** 28 September 2026
**Status:** Complete. When PLAiR can't be reached (no internet, or the server is down), the device keeps playing from its downloads, and it hands back to the server without a cut when the server returns. Tested in headless Chrome against a mock backend. Not yet tested on a real phone.

---

## Current state (28 September 2026)

### What the listener sees
- **Server drops mid-song:** the song keeps playing. About 2-4 s later a pill appears at the top, `Offline · playing your downloads (N)` (guests too). If the song is downloaded, playback quietly moves to the downloaded copy (120 ms crossfade of identical audio at the same position). If it isn't, it plays to the end of what was already buffered, and when the buffer runs dry (1.5 s of stall) it skips to a download.
- **While offline:** next, previous, seek, pause, tapping a track in the catalog, adding to the queue and Seed Radio all work on the downloads. The catalog shows only downloads, or `No downloads yet` when there are none. Voice search, the DJ, Radio Mode and shoutouts show a short friendly message instead of an error, and Radio Mode talk breaks pause. The "Failed to load tracks" toast and the red "Disconnected from server" toast no longer fire during an outage (the pill is the status).
- **Server comes back:** the socket reconnects immediately (it no longer waits out its backoff, which could be 30 s). The device hands its current song and position to the server; the audio is not reloaded or re-seeked. The pill disappears, and likes made offline are sent to the server.
  - If **another device on the account is playing** when the server returns, this device finishes its current song and then steps aside (becomes a remote), so the two devices don't fight.
  - If this device was only a **remote** before the outage and nobody pressed play on it, it simply follows the server again.
- **Reloading while offline** (even with nginx down) works: the service worker serves the whole app from its cache, and the downloads play.
- **Signed-in users stay signed in** through outages and offline reloads (only a real 401/403 or WebSocket close 4401 signs out).

### How it works (files)
- `NetworkContext.jsx`: server-down detection with hysteresis (2 failed `/api/health` probes 1.5 s apart; back after 2 successes 3 s apart; more after flapping; browser `offline` confirmed for 2 s). It publishes `audioState.offlineMode` (the SSOT) and emits `api.reportServerLost()` / `api.reportServerRecovered()`. A 502/503/504, a network error, or a WebSocket that closes abnormally triggers an immediate probe (`api.reportServerTrouble`).
- `PlaybackContext.jsx`, **local mode** (`localRef`):
  - `enterLocalMode()` runs when `offlineMode` turns on. It resets pending command state, ends any armed or on-air talk break (resuming music from a hold), marks this device locally active (`audio.setActiveDevice(true)` + `reportEngineStatus({ isActiveDevice: true })`), and starts `offlineBackend.startLocalSession()`.
    - It keeps the current track if the engine is already playing it or it is downloaded. Otherwise it starts a download: playing if this device was playing, paused if it was a remote.
    - If the current track is downloaded but still streaming, `engine.handleOfflineTransition()` swaps to the downloaded copy.
  - `applyLocalState()` applies a local queue/state without touching the engine's current track. `getTrackSource()` returns downloads only in local mode.
  - Auto-advance calls `offlineBackend.advanceTo()`. `next`/`previous`/`seek`/`togglePlay`/`playTrack`/`addToQueue`/`removeFromQueue`/`seedRadio` act locally.
  - A stream that stalls for 1.5 s (`STARVED_SKIP_MS`) while offline skips to the next download. A download that fails to load is skipped (max 5 in a row, then pause).
  - Server snapshots that arrive in local mode are stashed, not applied. Heartbeats and claim-on-open are suspended.
  - `handBack()` runs when `offlineMode` turns off and a snapshot from the reconnected socket is available:
    - **not used locally** → apply the server state;
    - **another online device is active and we're playing** → `yielding`: drop the preloaded next track, finish the song, then `stepAside()` (apply the server state, so this device becomes a remote);
    - **otherwise** → `claim` (only if we aren't already the server's active device) + `play {track_id}` + `seek {position_ms}` (+ `pause` if paused). `handoverRef` holds back snapshots until the first of those commands is acknowledged, so a stale "other device is active" snapshot can't silence this device mid-hand-back. Any transport (next/prev/play/pause/seek) during `yielding` takes over instead.
- `WebSocketContext.jsx`: the old fake-offline simulation (it called `offlineBackend.pause/seek`, which don't exist) is gone. `send()` returns `'offline'` while `offlineMode` is on. Queued playback and talk-break messages are dropped when the server is lost (so stale commands aren't replayed), and the socket reconnects immediately on recovery.
- `audioEngine.js`:
  - `handleOfflineTransition()` now keeps the current slot's metadata. It used to drop `duration_ms`, which stopped the next crossfade.
  - It only swaps when the current source is an incomplete stream, and it does so seamlessly.
  - New helpers: `isCurrentSourceComplete()`, `isNextSourceComplete()`, `dropNext()`.
  - A stream chunk that fails is retried every 2 s (up to 3 minutes) while the buffer keeps playing, so a short server blip no longer kills the song.
- `offlineAPI.js`:
  - friendly offline answers for voice search, the DJ, Radio Mode (`getRadioMode`/`updateRadioMode`) and shoutouts (`getShoutout`, stats, replies, reply upload, delete);
  - calmer offline DJ lines.
- UI:
  - `OfflinePill.jsx` (rendered in `App.jsx`, visible to guests; count = playable downloads from `cacheManager.getStorageInfo().offlineTrackCount`);
  - `Catalog.jsx` empty states and toast grace;
  - `Shoutouts.jsx` offline empty state;
  - `MediaSearch.jsx` voice search message;
  - `Radio.jsx` DJ message;
  - `RadioModeSettings.jsx` "talk breaks are paused" note;
  - `PreferencesContext.jsx` Radio Mode error text;
  - `Toast.jsx` moves top toasts below the pill;
  - `App.jsx` 4 s grace before the disconnect toast.
- `StorageContext.jsx` refreshes on `cacheManager.onChange`, so the pill count updates as downloads land.
- **Service worker** (`public/sw.js`, caches `plair-static-v5` / `plair-dynamic-v4`):
  - A Vite plugin in `vite.config.js` writes `asset-manifest.json` at build time (every `/assets/*` file, icons, interface sounds). The SW precaches it on install, and again when the page posts `PRECACHE` after load (`main.jsx`); that pass also prunes old `/assets/` entries, so updates keep working.
  - Lookups use `ignoreVary` (the page's module-script requests otherwise miss precached entries).
  - Navigations are network-first with a 4 s fallback to the cached shell, and a 5xx serves the cached shell.
  - `/api/*` (including `/api/stream/`) is no longer intercepted: no duplicate audio cache. The old `plair-audio-v2` cache is deleted on activate.
  - Cached media answers `Range` requests with proper 206 responses (Safari needs this).
- **Background downloads** (`backgroundDownloader.js`):
  - They no longer require Chrome's Network Information API (`networkQuality === 'excellent'`), so Safari, iOS and Firefox download liked tracks too.
  - They skip when offline, IndexedDB is unavailable, Data Saver is on, or Chrome reports `saveData`, cellular, or 2g/3g.
  - `cacheManager.canMakeRoomFor()` pauses downloads (30 min) when the cache is full of liked tracks, instead of evicting one liked track to download another.
  - Liking a track, or the server coming back, wakes the downloader at once (it used to sleep up to 60 s).

### Storage and downloads (decided by the owner, 28 Sep)
- **Same experience on iPhone as on Android:** the download space is sized from the browser's own quota on every platform: `navigator.storage.estimate()`, half the quota, at most 2 GB, at least 200 MB where the quota allows (`cacheManager._sizeFromQuota`). Without an estimate it falls back to 500 MB on iOS and 2 GB elsewhere. The old fixed 50 MB iOS cap is gone.
- Once there are downloads, the app asks the browser to keep them (`navigator.storage.persist()`, `cacheManager.requestPersistence`). It skips this on Firefox, which would show a prompt. iOS may still clear a site's storage after about 7 days without use unless it was added to the Home Screen; that is a platform rule.
- **Wi-Fi vs mobile data:** Safari and Firefox don't expose the connection type, and the owner accepts that as a platform limitation. Background downloads measure their own speed and pause for 15 minutes after a download slower than 1 Mbps. They still skip Data Saver, `saveData`, cellular (Chrome) and 2g/3g.

### How to test
- `npx eslint src --quiet` and a scratch build: `npx vite build --outDir <scratch>`. Never build into `client/dist` for tests.
- The end-to-end harness used on 28 Sep (it was in the session scratchpad, so copy it if you want to keep it) had four parts:
  - a FastAPI mock backend with health, catalog, auth, preferences, `/api/stream/{id}/webm` with HEAD + Range, and `/ws/playback` with `state_epoch`/`version`/`acks`/`claim`;
  - `vite preview` of a scratch build with `/api` + `/ws` proxied to the mock;
  - a raw-CDP driver;
  - headless Chrome with `--headless=new --disable-gpu --disable-software-rasterizer --mute-audio --autoplay-policy=no-user-gesture-required`.
- All **51 checks** passed. They covered:
  - server down mid-song (pill in ~3 s, no gap);
  - a stream running dry, then a skip to a download;
  - local next/previous;
  - a downloads-only catalog;
  - a smooth hand-back (play + seek, no jump);
  - an offline reload with web and server both down;
  - a flapping server (no gaps, only 2 mode changes);
  - signed-in, with a liked track auto-downloaded, a seamless swap to it, offline likes synced and still signed in;
  - two devices (one takes over, the other finishes its song and steps aside);
  - no IndexedDB (iOS private mode): "no downloads yet", a clean pause, no exceptions.
- **On a phone:** see the airplane-mode checklist in `docs/HANDOVER_2026-09-28.md`.

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

**Purpose:** plays from the server when it is reachable, and runs a **local mode** on the downloads when `audioState.offlineMode` is on. See "Current state" above for the full behaviour.

- `getTrackSource()` prefers a downloaded copy (unless the listener asked for a higher bitrate than the download and the server is reachable), otherwise streams and records the stream so it can be saved when complete. In local mode it returns downloads only.
- Local mode (`localRef`) is entered by `enterLocalMode()` and left by `handBack()` (hand the current song + position to the server) or `stepAside()` (follow the server, used when another device is playing or this device never played locally).
- Transport in local mode goes to `offlineBackend` (`next`, `previous`, `advanceTo`, `play`, `seedRadio`, `addToQueue`, `removeFromQueue`) and the result is applied with `applyLocalState()`, which never reloads the track that is already playing.

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

### Scenario 3: Server or Internet Lost Mid-Session

```
1. Streaming track 1 (not downloaded); the server stops answering
2. API errors / the WebSocket closing trigger an immediate /api/health probe
3. 2 failed probes -> connectionMode 'degraded' (or 'offline') -> audioState.offlineMode = true
4. PlaybackContext.enterLocalMode(): keep track 1 playing, build a local queue from the downloads
5. The pill shows "Offline · playing your downloads (N)"
6. Track 1's buffer runs dry -> 1.5 s stall -> skip to the next download
7. Auto-advance / next / previous / seek run locally
```

### Scenario 4: Server Returns

```
1. Health probes succeed twice -> offlineMode = false -> api.reportServerRecovered()
2. WebSocket reconnects immediately; offline likes are synced (api.syncOfflineWrites)
3. First playback_state on the new socket -> handBack()
   - nobody else playing: claim (if needed) + play {track_id} + seek {position} -> no reload, no jump
   - another device playing: finish this song, then follow the server as a remote
4. Pill disappears; streaming resumes for the following tracks
```

### Scenario 5: Reload While Offline

```
1. The service worker serves the cached app shell (precached from asset-manifest.json)
2. Health probes fail -> offlineMode -> local session starts paused on a download
3. The catalog shows the downloads; pressing play starts them
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

**Implemented** in `client/src/lib/backgroundDownloader.js` (signed-in users, liked and super-liked tracks, 500 MB/day). See "Current state" for the rules; the iOS storage cap is an open decision.

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

**Implemented:** `client/public/sw.js` precaches the app shell from the build's `asset-manifest.json`, so the app reloads with no connection. API responses are not cached by the service worker (the offline backend answers them from IndexedDB).

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

The most realistic test is to stop the (mock) backend: the app should switch to the downloads in ~2-4 s. Or:

```javascript
// Chrome DevTools:
// 1. Open Network tab
// 2. Select "Offline" from throttling dropdown

// OR programmatically:
Object.defineProperty(navigator, 'onLine', { value: false })
window.dispatchEvent(new Event('offline'))
```

---

**Document Version:** 2.0
**Last Reviewed:** 28 September 2026
**Next Review:** After testing on a real phone and deciding the iOS storage cap
