# UIState Cleanup TODO

## Problem Summary

UIState has accumulated business logic that violates the pub/sub pattern. UIState is meant to be a **pure pub/sub central communicator** - services own their domain logic and publish to UIState, components subscribe. But over time, business logic has leaked into UIState itself.

**The Correct Pattern:**
```
Services (own logic, persistence) → publish to UIState → Components subscribe
```

**What We Have:**
```
Components directly call publishSettings() → UIState does localStorage + state
UIState contains fetch logic, RAF loops, API calls
```

---

## Audit Results

### Likely Fine (Pure UI State / Pub-Sub)

These are appropriate for UIState:

- `engineState` - receives published state from engines (PlaybackContext, DJ, Mic, etc.)
- `visualState` / `visualColorData` - derived from engineState for rendering
- `toasts` - pure UI feedback system
- `modalState` (shoutoutModalState, uploadModalOpen) - pure UI open/close state
- `interfaceState` - scroll position, fullscreen, panel visibility
- `radioButtonInteraction` - UI hover/press state
- `shaderPanelRegions` / `shaderRadioButtonPos` - visual rendering coordinates
- `artworkUrls` / `enrichedArtworkUrls` - cached blob URLs (the Maps themselves are fine)

### Questionable (State Without Owning Service)

These have state in UIState but no dedicated service managing them:

#### `settingsState`
- Contains: `audioQuality`, `ttsMuted`, `notificationsMuted`, `fpsEnabled`, `videoClipsEnabled`, `visualQuality`, `dataSaverMode`
- **Problem:** No SettingsContext service owns this. Components directly call `publishSettings()`.
- **Problem:** localStorage persistence logic baked into `publishSettings()` callback
- **Fix:** Create `SettingsContext` that owns settings, handles persistence, publishes to UIState

#### `radioState.activeSeedMode`
- **Problem:** localStorage persistence logic inside `publishRadioState()`
- **Fix:** Should be owned by PlaybackContext (it's playback-related)

#### `downloadState`
- Contains: `isDownloading`, `currentTrackId`, `totalQueued`, `dailyDownloadedBytes`, `isEnabled`
- **Problem:** localStorage persistence in `publishDownloadState()`
- **Problem:** `setDownloadStateUpdater` pattern is a workaround for not having proper service
- **Fix:** BackgroundDownloader should own this state and publish to UIState

### Definitely Pollution (Business Logic in UIState)

These are active business logic that should not be in UIState:

#### Artwork Preloading (lines 919-937)
```javascript
useEffect(() => {
  const { currentTrack, queue, currentIndex } = engineState
  // ... fetch and preload logic
  preloadArtwork(currentTrack.id, currentTrack.has_artwork)
  preloadEnrichedArtwork(currentTrack.id, currentTrack.has_artwork)
  // ...
}, [...])
```
- **Problem:** UIState is doing actual preload orchestration
- **Fix:** Move to dedicated `ArtworkPreloader` hook or service

#### Video Clips Fetching (lines 875-917)
```javascript
const fetchVideoClips = useCallback(async (trackId) => {
  // ... API calls, caching logic
  const data = await api.getVideoClips(trackId)
  // ...
}, [])
```
- **Problem:** API calls and caching logic inside UIState
- **Fix:** Move to dedicated service, publish results to UIState

#### Gyroscope/Physics Loop (lines 289-416)
```javascript
useEffect(() => {
  // ... RAF loop for parallax physics
  const loop = () => {
    // physics calculations
    requestAnimationFrame(loop)
  }
  loop()
}, [isScreenVisible])
```
- **Problem:** Active RAF processing loop in UIState
- **Fix:** Move to dedicated `useParallaxPhysics` hook

#### Music Ducking Logic (lines 418-443)
```javascript
useEffect(() => {
  const mixer = mixerRefInternal.current?.current
  // ... ducking state machine logic
  if (targetState === 'idle') {
    mixer.restoreMusic(400)
  } else if (targetState === 'user') {
    mixer.duckMusic(0.1, 200)
  }
  // ...
}, [engineState.isMicRecording, engineState.isDJSpeaking, ...])
```
- **Problem:** Audio mixing business logic in UIState
- **Fix:** Move to AudioMixer service or PlaybackContext

---

## Suggested Refactoring Plan

### Phase 1: Create SettingsContext
1. Create `client/src/contexts/SettingsContext.jsx`
2. Move all settings state (`audioQuality`, `ttsMuted`, `fpsEnabled`, `visualQuality`, `dataSaverMode`, etc.)
3. Handle localStorage persistence in SettingsContext
4. Publish to UIState for component consumption
5. Update User.jsx, BitratePicker.jsx to use SettingsContext

### Phase 2: Fix DataSaverMode Location
1. Move `dataSaverMode` back to StorageContext (it's storage-related)
2. StorageContext publishes to UIState
3. PlaybackContext reads from UIState (or StorageContext directly)
4. BitratePicker reads from UIState

### Phase 3: Extract Processing Hooks
1. Create `useParallaxPhysics` hook - move gyroscope/physics RAF loop
2. Create `useArtworkPreloader` hook - move preload orchestration
3. Create `useVideoClipsLoader` hook - move video clips fetching
4. UIState just stores the results, doesn't orchestrate

### Phase 4: Clean Up Download State
1. BackgroundDownloader service owns download state
2. Publishes to UIState via `reportDownloadStatus()` pattern
3. Remove `setDownloadStateUpdater` workaround

### Phase 5: Move Ducking Logic
1. Move music ducking logic to AudioMixer or PlaybackContext
2. UIState just provides the flags (isDJSpeaking, isMicRecording, etc.)
3. Mixer subscribes and handles ducking internally

---

## Files Affected

- `client/src/contexts/UIStateContext.jsx` - main cleanup target
- `client/src/contexts/SettingsContext.jsx` - new file
- `client/src/contexts/StorageContext.jsx` - receives dataSaverMode back
- `client/src/contexts/PlaybackContext.jsx` - may receive ducking logic
- `client/src/components/User.jsx` - update to use SettingsContext
- `client/src/components/BitratePicker.jsx` - update to use SettingsContext
- `client/src/hooks/useParallaxPhysics.js` - new file
- `client/src/hooks/useArtworkPreloader.js` - new file
- `client/src/lib/backgroundDownloader.js` - owns download state

---

## Guiding Principle

**UIState is a mailbox, not a post office.**

- Services put mail in (publish)
- Components take mail out (subscribe)
- UIState doesn't read, process, or act on the mail

If UIState is doing anything more than storing and providing state, it's pollution.

---

## Current State (Feb 2026)

The `dataSaverMode` change we just made adds to this pollution. For now it works, but it's in the wrong place architecturally. This document serves as a reminder to fix the broader issue.

**Priority:** Medium - System works, but architecture is degrading. Should fix before adding more features that touch UIState.
