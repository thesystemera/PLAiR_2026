# PLAiR Playback & Audio Engine Architecture

## Core Principle
```
Backend = "The Producer" (manages queue, analytics, timing hints)
Frontend = "The DJ" (controls audio hardware, executes playback)
Multi-Device = One active device plays audio, others remote control
Communication = Event-driven WebSocket messages
```

---

## Table of Contents
1. [System Components](#system-components)
2. [Multi-Device Architecture](#multi-device-architecture)
3. [Progress Tracking System](#progress-tracking-system)
4. [Auto-Crossfade Chain](#auto-crossfade-chain)
5. [Announcer System](#announcer-system)
6. [Queue Carousel](#queue-carousel-architecture)
7. [State Broadcast Structure](#state-broadcast-structure)
8. [Common Failure Modes](#common-failure-modes)
9. [Testing Checklist](#testing-checklist)

---

## System Components

### Frontend (audioEngine.js + PlaybackContext.jsx)
**Owns:**
- Physical audio playback (play/pause/seek)
- `is_playing` state (source of truth)
- Progress tracking from `audio.currentTime` (4x per second)
- Crossfade execution (A/B slot architecture)
- Audio element buffering
- Active device status reporting

**Does NOT own:**
- Queue order
- What track comes next
- Analytics
- Which device is active (backend decides)

**Key Files:**
- `client/src/lib/audioEngine.js` - Dual-buffer A/B slot audio engine
- `client/src/contexts/PlaybackContext.jsx` - Playback orchestration, WebSocket integration
- `client/src/hooks/useAudio.js` - Hook wrapper around audioEngine
- `client/src/contexts/UIStateContext.jsx` - SSOT for all UI state (progress, activeDevice, isCrossfading, etc.)

### Backend (playback_state.py + announcer_service.py)
**Owns:**
- Queue management (what plays next)
- Track metadata
- Analytics logging
- Radio mode (favorites/discovery/genre/mood/etc.)
- Crossfade timing hints (from audio analysis)
- Announcer timing windows
- Device coordination (`active_device_id`)

**Does NOT own:**
- When play/pause happens (frontend controls)
- Actual `is_playing` state (frontend is source of truth)
- Physical crossfade execution (frontend handles)
- Auto-advance when `active_device_id` is set (frontend handles)

**Key Files:**
- `server/services/playback_state.py` - **CRITICAL** - Playback state machine, queue carousel
- `server/services/playback_service.py` - Session-level playback management
- `server/services/announcer_service.py` - DJ timing analysis, monitoring, TTS triggers
- `server/app.py` - WebSocket handlers (lines ~2000-2600)

---

## Multi-Device Architecture

### Device Roles

**ACTIVE DEVICE (Currently Selected)**
- Owns physical audio playback - directly controls audio element
- **Optimistic updates** - user actions affect audio IMMEDIATELY, no waiting
- **Backend confirmation** - after local action, sends command to backend for sync
- **Only device that plays audio** - all others just show UI
- **Reports progress** - sends heartbeat every 5s with actual position
- **Handles crossfades** - executes A/B slot crossfading locally

**INACTIVE DEVICES (All Other Devices)**
- Remote control only - can control the active device
- No audio playback - audio elements don't play, just show UI
- Command relay - user actions send commands to backend → active device
- UI sync - receives playback_state broadcasts to show what's happening
- Display queue/progress - shows current state but doesn't control playback

### Device Identity & Registration

**Device ID:**
- Every browser instance gets unique UUID stored in localStorage
- Generated on first visit, persists across sessions
- Sent in all API requests via `X-Device-ID` header
- Used in WebSocket connection to identify sender

**Device Registration Flow:**
```
1. First API call or WebSocket connect
   ↓
2. DeviceManagementService.register_or_update_device()
   ↓
3. Creates UserDevice record with device_id
   ↓
4. First device: is_active=True, others: is_active=False
```

**Device Activation Flow:**
```
User selects device in DevicePicker
   ↓
POST /api/devices/activate with device_id
   ↓
Backend sets playback_state.active_device_id = device_id
   ↓
Backend sends 'device_inactive' to old device
Backend sends 'device_activated' to new device
   ↓
Backend broadcasts playback_state with new active_device_id
   ↓
Old device: Sees not active → stops audio
New device: Sees is active → loads track and plays
```

### Control Flow Patterns

#### Pattern 1: Active Device Local Control

**Example: User on active device clicks pause**
```
Active Device:
1. User clicks pause button
2. Check: Am I active? YES
3. audio.pause() ← IMMEDIATE, no waiting
4. setState({ is_playing: false }) ← Optimistic update
5. Send playback_command { pause } to backend
6. Backend broadcasts to all devices
7. We receive our own broadcast, already paused ✓

Inactive Devices:
1. Receive playback_state { is_playing: false }
2. Update UI to show paused state
3. No audio action (not playing anyway)
```

**Code Pattern:**
```javascript
const togglePlay = useCallback(async () => {
  const isActive = isActiveDeviceRef.current
  const wasPlaying = state.is_playing

  setState(prev => ({ ...prev, is_playing: !wasPlaying }))  // Optimistic

  if (isActive) {
    // Active: control audio immediately
    if (wasPlaying) {
      audio.pause()
    } else {
      audio.play()
    }
  }

  // Always send to backend for multi-device sync
  wsSend?.({ type: 'playback_command', data: { command: wasPlaying ? 'pause' : 'play' } })
}, [state.is_playing, wsSend, audio])
```

#### Pattern 2: Inactive Device Remote Control

**Example: User on phone (inactive) clicks pause while desktop (active) is playing**
```
Inactive Device (Phone):
1. User clicks pause
2. Check: Am I active? NO
3. DON'T touch audio (we're not playing)
4. setState({ is_playing: false }) ← Optimistic UI update
5. Send playback_command { pause } to backend

Backend:
1. Receives playback_command { pause }
2. Updates playback_state.is_playing = false
3. Broadcasts playback_state to ALL devices

Active Device (Desktop):
1. Receives playback_state { is_playing: false }
2. handlePlaybackState() detects: data.is_playing ≠ audioIsPlaying
3. Calls audio.pause() to actually stop music
4. Now paused ✓

Inactive Device (Phone):
1. Receives same broadcast
2. UI already shows paused (optimistic)
3. Mission accomplished
```

#### Pattern 3: Seek Control (Active & Inactive)

**Active device seeking:**
```javascript
const seek = useCallback(async (positionMs) => {
  // Optimistic: update UI immediately
  setState(prev => ({ ...prev, progress_ms: positionMs }))

  // Active device: seek audio element immediately
  if (isActiveDeviceRef.current) {
    audio.seek(positionMs / 1000)
  }

  // Send to backend for multi-device sync
  wsSend?.({ type: 'playback_command', data: { command: 'seek', position_ms: positionMs } })
}, [audio, wsSend])
```

**Inactive device seeking:**
```
1. User drags seek bar on phone (inactive)
2. Optimistic UI update + send seek command to backend
3. Backend updates state.progress_ms, broadcasts to all
4. Active device receives broadcast:
   - Checks: |data.progress_ms - audio.currentTime| > 1s?
   - If yes: audio.seek(data.progress_ms / 1000)
5. Active device progress interval picks up new position
6. Progress flows back through UIState → all devices see update
```

### Sync Effects on Active Device

**Sync 1: Remote play/pause detection**
```javascript
// handlePlaybackState (active devices only)
if (data.is_playing !== undefined) {
  const audioIsPlaying = element && !element.paused

  if (data.is_playing !== audioIsPlaying) {
    // Remote command detected!
    if (data.is_playing) {
      audio.play()
    } else {
      audio.pause()
    }
  }
}
```

**Sync 2: Remote seek detection**
```javascript
// handlePlaybackState (active devices only)
if (data.progress_ms !== undefined) {
  const audioProgress = element ? Math.floor(element.currentTime * 1000) : 0

  // >1s difference = remote seek
  if (Math.abs(data.progress_ms - audioProgress) > 1000) {
    audio.seek(data.progress_ms / 1000)
  }
}
```

### The `active_device_id` Flag

**Set by:** Backend when user activates device via DevicePicker

**Used by:**
- **Backend:** Decides whether to auto-advance tracks
  - If `active_device_id` is set: Frontend controls playback, backend does NOT auto-advance
  - If `active_device_id` is null: Backend controls playback (legacy mode)
- **Frontend:** Each device checks `data.active_device_id === deviceId`
  - If match: "I'm active" → play audio, control playback, report progress
  - If no match: "I'm inactive" → show UI, send commands, DON'T play audio

**Critical Pattern:**
```javascript
const weAreActive = !data.active_device_id || data.active_device_id === deviceId

if (weAreActive !== wasActive) {
  isActiveDeviceRef.current = weAreActive

  if (!weAreActive) {
    // Just became inactive - stop audio immediately
    await audio.stopImmediately()
  }
}

// Report to UIState (SSOT)
reportEngineStatus({ isActiveDevice: weAreActive })
```

---

## Progress Tracking System

### Architecture: Active Device Tracks, UIState Publishes

**Flow:**
```
Audio Element (currentTime)
  ↓ [250ms interval reads - PlaybackContext lines 464-481]
PlaybackContext state.progress_ms
  ↓ [Effect watches state.progress_ms - lines 60-80]
Calculates progressPercent = (progress_ms / duration_ms) * 100
  ↓ [Calls reportEngineStatus()]
UIStateContext engineState.progressPercent
  ↓ [Components subscribe via useUIState()]
Player, AudioReactiveCanvas, etc. render progress
```

### Implementation Details

**Progress Interval (Active Devices Only):**
```javascript
// PlaybackContext.jsx lines 464-481
const progressInterval = setInterval(() => {
  if (!isActiveDeviceRef.current || !stateRef.current.current_track) return

  const currentElement = audio.getCurrentElement?.()
  if (!currentElement || currentElement.paused) return

  const currentTime = Math.floor(currentElement.currentTime * 1000)

  // Update state (triggers publishing effect)
  setState(prev => {
    // Only update if changed >100ms (avoids excessive re-renders)
    if (Math.abs(currentTime - prev.progress_ms) > 100) {
      return { ...prev, progress_ms: currentTime }
    }
    return prev
  })
}, 250)  // 4x per second for smooth progress bar
```

**Publishing Effect:**
```javascript
// PlaybackContext.jsx lines 60-80
useEffect(() => {
  const currentProgress = state.progress_ms || 0
  const currentTrackDuration = state.current_track?.duration_ms || 0

  let progressPercent = 0
  if (currentTrackDuration > 0) {
    progressPercent = (currentProgress / currentTrackDuration) * 100
    progressPercent = Math.min(100, Math.max(0, progressPercent))
  }

  // Publish to UIState (SSOT)
  reportEngineStatus({
    isMusicPlaying: state.is_playing,
    isMusicPaused: !state.is_playing && state.current_track !== null,
    currentTrack: state.current_track,
    progressPercent,  // ← This is what components consume
    queue: state.queue,
    currentIndex: state.current_index,
    isActiveDevice: isActiveDeviceRef.current
  })
}, [state.is_playing, state.current_track, state.progress_ms, state.queue, state.current_index])
```

**Component Consumption (SSOT Pattern):**
```javascript
// Player.jsx
const { engineState } = useUIState()
const { isActiveDevice, progressPercent } = engineState

// Calculate actual position from percentage
const progress_ms = actualDuration ? Math.floor((progressPercent / 100) * actualDuration) : 0

// Use progressPercent directly for visual rendering
<div style={{ width: `${progressPercent}%` }} />
```

### Heartbeat System (Backend Sync)

**Every 5 seconds, active device sends:**
```javascript
// PlaybackContext.jsx lines 435-462
{
  type: 'playback_heartbeat',
  data: {
    track_id: current_track.id,
    actual_position_ms: audio.currentTime * 1000,
    is_playing: !audio.paused,
    buffered_ahead_ms: bufferedAhead,
    timestamp: Date.now()
  }
}
```

**Backend updates:**
```python
# playback_state.py
def handle_playback_heartbeat(data):
    self.progress_ms = data['actual_position_ms']
    self.is_playing = data['is_playing']
    # Calculates drift, tracks latency for DJ TTS timing
```

**Why:** Backend needs accurate progress for:
- Announcer countdown timing
- Analytics (actual listening time)
- Detecting playback issues (no heartbeat = problem)
- Syncing inactive devices (they see backend's progress_ms in broadcasts)

### Inactive Device Progress

**Inactive devices receive progress via backend broadcasts:**
- Backend broadcasts `playback_state` every ~5s with `progress_ms`
- Inactive devices update `state.progress_ms` from broadcast
- Less smooth (5s updates) but acceptable for remote control UI
- When device becomes active, switches to real-time tracking

---

## Auto-Crossfade Chain

**THE MOST IMPORTANT FLOW** - If this breaks, entire system breaks.

### 1. Frontend Detects End Approaching

```javascript
// audioEngine.js ~line 221
timeUpdateHandler() {
  const currentTime = element.currentTime
  const duration = element.duration
  const crossfadeDuration = metadata?.crossfade_hint?.duration_ms / 1000 || 3

  if (currentTime + crossfadeDuration >= duration) {
    // Time to crossfade!
    this.executeCrossfade()
  }
}
```

### 2. Frontend Executes Crossfade

```javascript
// audioEngine.js
executeCrossfade() {
  // Fade out current slot (A)
  this.fadeVolume(currentSlot, 1.0, 0.0, crossfadeDuration)

  // Fade in next slot (B)
  this.fadeVolume(nextSlot, 0.0, 1.0, crossfadeDuration)

  // Swap current/next pointers
  this.currentSlot = nextSlot
  this.nextSlot = currentSlot

  // **CRITICAL**: Fire callback to notify backend
  if (this.onCrossfadeStart) {
    this.onCrossfadeStart(oldTrackId, newTrackId, fadeTimeMs)
  }
}
```

### 3. Callback Notifies Backend

```javascript
// PlaybackContext.jsx lines 378-397
engine.onCrossfadeStart = (currentSlotId, nextSlotId, fadeTimeMs) => {
  const nextTrack = stateRef.current.queue?.[stateRef.current.current_index + 1]

  if (nextTrack && wsSendRef.current) {
    wsSendRef.current({
      type: 'track_transition',
      data: {
        from_track_id: stateRef.current.current_track?.id,
        to_track_id: nextTrack.id,
        fade_duration_ms: fadeTimeMs
      }
    })
  }
}
```

### 4. Backend Processes Transition

```python
# app.py WebSocket handler
async def handle_track_transition(data):
    from_track_id = data['from_track_id']
    to_track_id = data['to_track_id']

    # Update current track
    playback_state.current_track_id = to_track_id

    # Shift queue carousel (keep current at position 5)
    await playback_state._shift_queue_to_target()

    # Auto-fill queue if needed
    await playback_state._auto_fill_queue()

    # Broadcast new state to all devices
    await broadcast_playback_state_to_session(session_id)

    # Trigger announcer analysis for next transition
    await announcer_service.on_playback_state_update(playback_state.get_state())
```

### 5. Frontend Receives Broadcast

```javascript
// PlaybackContext.jsx lines 123-200
const handlePlaybackState = useCallback(async (data) => {
  // Update queue, hints, etc.
  setState({
    current_track: data.current_track,
    queue: data.queue,
    crossfade_hint: data.crossfade_hint,
    announcer_hint: data.announcer_hint,
    // ...
  })

  // Trigger preload of NEXT track
  void triggerPreload()  // Loads queue[current_index + 1] into nextSlot
}, [])
```

### Critical Points

**✓ Callback must fire every time** - Or backend never knows track changed
**✓ Callback lifecycle** - Set via polling after engine initializes (lines 371-410)
**✓ Preload must complete** - Before crossfade needs it (wait for `canplay` event)
**✓ Backend must NOT auto-advance** - When `active_device_id` is set (frontend controls)

### Crossfade State in UIState (Pub/Sub Pattern)

The crossfade state is published to UIState for visual feedback and preload coordination:

```
AudioEngine.onCrossfadeStateChange(true/false)
  ↓
PlaybackContext.reportEngineStatus({ isCrossfading })
  ↓
UIStateContext.engineState.isCrossfading
  ↓
Components (Player.jsx shows pulse on play button, scale-down on skip buttons)
```

**Guard Pattern:** PlaybackContext uses `isCrossfadingRef.current` to defer preload attempts during crossfade - the nextSlot may still be fading out. An effect retries preload when `isCrossfading` transitions false.

### A/B Slot Architecture

**Why dual buffers?**
- Slot A plays current track
- Slot B preloads next track
- During crossfade, both play simultaneously (A fades out, B fades in)
- After crossfade, pointers swap (B becomes current, A becomes next)
- A now preloads the new next track

**State tracking:**
```javascript
{
  currentSlot: { element: <audio>, trackId: 'abc', metadata: {...} },
  nextSlot: { element: <audio>, trackId: 'def', metadata: {...} },
  timeUpdateElement: currentSlot.element  // Which element has the listener
}
```

**During crossfade:**
```javascript
// BEFORE crossfade
currentSlot = A (playing 'abc')
nextSlot = B (preloaded 'def')
timeUpdateElement = A

// Execute crossfade
fadeOut(A), fadeIn(B)

// AFTER crossfade
currentSlot = B (now playing 'def')  // Pointer swap
nextSlot = A (available for preload)  // Pointer swap
timeUpdateElement = B  // **MUST UPDATE** or progress breaks
currentTrackId = 'def'  // **MUST UPDATE** or state breaks
```

### Vinyl Turntable Simulation

The audio engine simulates analog turntable behavior by coupling `playbackRate` (pitch) with gain (volume) ramps. This creates realistic DJ-style effects.

**Durations (configurable):**
- `crossfadeDuration` (3000ms) - Default crossfade, overridden by backend hints
- `vinylRapidDuration` (150ms) - Play/pause motor spin up/down
- `vinylSeekDuration` (80ms) - Seek ramp down/up

**Methods:**
| Method | Effect | Use Case |
|--------|--------|----------|
| `_vinylRapidRampUp` | 0.1 → 1.0 pitch | Play: motor starting |
| `_vinylRapidRampDown` | 1.0 → 0.1 pitch | Pause: motor stopping |
| `_vinylSeekRampDown` | 1.0 → 0.3 pitch | Seek: needle lifting |
| `_vinylSeekRampUp` | 0.3 → 1.0 pitch | Seek: needle dropping |
| `_vinylSlowRampUp` | 0.85 → 1.0 pitch | Incoming track spin-up during crossfade |
| `_vinylSlowRampDown` | 1.0 → 0.15 + wobble | Outgoing track slowdown with wobble |
| `_startVinylWobble` | Pitch oscillation | Next track not ready, waiting |
| `_stopVinylWobble` | Reset to 1.0 | Next track ready |
| `playVinylScratch` | SFX trigger | Track change sound effect |

**Wobble System:**
When auto-crossfade detects the next track isn't ready (preload incomplete), it starts a pitch wobble effect on the current track to buy time while maintaining the illusion of a live DJ. Once the next track's `readyState >= 2`, wobble stops and crossfade proceeds.

```javascript
// Wobble creates subtle pitch variations
element.playbackRate = 1.0 - wobbleAmount + Math.sin(phase) * wobbleAmount
```

**Integration with Backend Hints:**
`crossfade_hint.duration_ms` from backend drives both gain AND vinyl pitch ramp durations, allowing per-track DJ-controlled turntable effects.

---

## Announcer System

### Overview
Backend analyzes track pairs, finds silence windows for DJ talking, monitors progress, triggers TTS when time comes.

### Timing Analysis (Backend)

```python
# announcer_service.py
async def on_playback_state_update(playback_state):
    current_track = playback_state['current_track']
    next_track = playback_state['queue'][playback_state['current_index'] + 1]

    # Analyze both tracks' audio features
    current_features = await audio_features_service.get_features(current_track['id'])
    next_features = await audio_features_service.get_features(next_track['id'])

    # Find silence windows (low loudness segments)
    silence_windows = find_silence_windows(current_features, next_features)

    # Calculate optimal crossfade timing
    crossfade_timing = calculate_crossfade_timing(current_features, next_features)

    # Store timing in cache
    announcer_timing = {
        'start_ms': crossfade_timing.start_ms - 5000,  # 5s before crossfade
        'end_ms': crossfade_timing.start_ms,
        'duration_ms': 5000
    }

    # Broadcast hint to frontend
    await broadcast_playback_state({
        ...playback_state,
        'announcer_hint': announcer_timing,
        'crossfade_hint': crossfade_timing
    })

    # Schedule announcer trigger
    await schedule_transition_announcement(current_track, next_track, announcer_timing)
```

### Monitoring & Trigger (Backend)

```python
# announcer_service.py
async def schedule_transition_announcement(current_track, next_track, timing):
    # Create asyncio task IMMEDIATELY (even if trigger is 200s away)
    # Asyncio tasks are cheap - one per user is FREE
    task = asyncio.create_task(_execute_transition_announcement(
        current_track, next_track, timing
    ))

async def _execute_transition_announcement(current_track, next_track, timing):
    trigger_time = timing['start_ms']

    while True:
        current_progress = playback_state.progress_ms

        # Log countdown every 10s
        time_until_trigger = trigger_time - current_progress
        if time_until_trigger % 10000 < 1000:
            log_service.system(f"[ANNOUNCER] ⏳ Countdown: {time_until_trigger/1000:.1f}s")

        # Check if time to trigger (5s early for TTS generation)
        if current_progress >= trigger_time - 5000:
            # Generate DJ script via LLM
            script = await generate_dj_script(current_track, next_track)

            # Send to TTS
            audio_url = await tts_service.generate(script)

            # Trigger audio duck (lower music volume)
            await broadcast_playback_override('mute')

            # Play TTS
            await broadcast_dj_audio(audio_url)

            # Release audio (restore music volume)
            await broadcast_playback_override('release')

            break

        # Poll interval (far away = 10s, close = 1s)
        poll_interval = 10 if time_until_trigger > 30000 else 1
        await asyncio.sleep(poll_interval)
```

### Frontend Countdown Display

```javascript
// PlaybackContext.jsx lines 417-433
const statusInterval = setInterval(() => {
  if (!isActiveDeviceRef.current || !stateRef.current.current_track) return

  const announcer = stateRef.current.announcer_hint

  if (announcer) {
    const currentPos = audio.getCurrentElement()?.currentTime * 1000 || 0
    const triggerTime = announcer.start_ms
    const timeUntilTrigger = triggerTime - currentPos

    logger.info(
      `[Audio] 🎙️ Frontend at ${(currentPos / 1000).toFixed(1)}s | ` +
      `Announcer trigger ${(triggerTime / 1000).toFixed(1)}s | ` +
      `${(timeUntilTrigger / 1000).toFixed(1)}s away`
    )
  }
}, 10000)
```

**Critical:** Countdown logs should match between frontend and backend. If desynced, heartbeat is broken.

---

## Queue Carousel Architecture

### Target: Current Track at Position 5 of 11

**Why?** Radio station feel - always have ~5 tracks ahead, ~5 behind for bidirectional browsing.

### Operations

**Advancing Forward:**
```python
# current_index moves from 5 → 6
await self._shift_queue_to_target()
# - Moves queue[0] → history
# - current_index back to 5

# If queue < 11:
await self._auto_fill_queue()
# - Adds tracks to end based on radio_mode
```

**Going Backward:**
```python
# current_index moves from 5 → 4
await self._shift_queue_from_history()
# - Moves history[-1] → queue[0]
# - current_index back to 5
```

**Auto-fill Logic:**
```python
# playback_state.py
async def _auto_fill_queue(self):
    while len(self.queue) < 11:
        if self.radio_mode == 'favorites':
            # Get random liked track
            track = await get_random_favorite(user_id)

        elif self.radio_mode == 'discovery':
            # 50/50 mix of favorites + similar tracks
            track = await get_discovery_track(user_id)

        elif self.radio_mode in ['mood', 'genre', 'artist', 'style', ...]:
            # Use vector search to find similar tracks
            seed_track_id = self.queue[self.current_index]['id']
            track = await catalog_vector_search_service.find_similar(
                seed_track_id,
                mode=self.radio_mode
            )

        self.queue.append(track)
```

### Radio Modes

**Playlist Modes (No seed track required):**
- `favorites` - User's liked tracks on shuffle
- `discovery` - 50/50 favorites + new similar tracks
- `top_hits_all` - All-time most popular (station analytics)
- `top_hits_week` - Past 7 days most popular
- `top_hits_day` - Past 24 hours most popular

**Seed Modes (Based on current/selected track):**
- `all` - Balanced mix (All Categories)
- `primary_genre` - Same primary genre
- `secondary_genres` - Similar sub-genres
- `mood` - Similar mood/vibe
- `primary_artist` - More from same artist
- `similar_artists` - Similar artists
- `style` - Production style
- `theme` - Lyrical themes
- `lyrics` - Similar lyrics
- `vocal` - Vocal style

---

## State Broadcast Structure

### Backend → Frontend (playback_state WebSocket message)

```javascript
{
  current_track: {
    id: 'abc123',
    title: 'Track Title',
    artist: 'Artist Name',
    duration_ms: 240000,
    has_artwork: true,
    // ... metadata
  },
  queue: [...],  // 11 tracks, current at index ~5
  history: [...],  // Last 10 tracks
  current_index: 5,  // Current track position in queue
  progress_ms: 45000,  // Backend's view (from heartbeat)
  is_playing: true,  // ⚠️ Active device IGNORES (frontend owns this)
  active_device_id: 'device-uuid-here',  // Which device is active
  crossfade_hint: {
    optimal_start_ms: 180000,
    duration_ms: 2000,
    confidence: 'high'
  },
  announcer_hint: {
    start_ms: 196100,
    end_ms: 201600,
    duration_ms: 5500
  },
  activeSeedMode: 'mood'  // Current radio mode
}
```

### Frontend Response (Active Device)

```javascript
// handlePlaybackState (lines 157-198)
setState({
  current_track: data.current_track,
  queue: data.queue,
  history: data.history,
  current_index: data.current_index,
  crossfade_hint: data.crossfade_hint,
  announcer_hint: data.announcer_hint
})

// Store crossfade hint in audio engine metadata
if (data.crossfade_hint) {
  audio.engineRef.current.currentSlot.metadata.crossfade_hint = data.crossfade_hint
}

// Sync is_playing if remote command
if (data.is_playing !== audioIsPlaying) {
  data.is_playing ? audio.play() : audio.pause()
}

// Sync progress if remote seek (>1s difference)
if (Math.abs(data.progress_ms - audioProgress) > 1000) {
  audio.seek(data.progress_ms / 1000)
}

// Preload next track
void triggerPreload()
```

### Frontend Response (Inactive Device)

```javascript
// handlePlaybackState (lines 199-210)
setState({
  current_track: data.current_track,
  progress_ms: data.progress_ms,  // Use backend's progress
  is_playing: data.is_playing,
  queue: data.queue,
  history: data.history,
  current_index: data.current_index,
  crossfade_hint: data.crossfade_hint,
  announcer_hint: data.announcer_hint
})

// No audio actions - we're not playing
// Just update UI to show current state
```

---

## Common Failure Modes

### 1. Auto-Advance Stops After 1-2 Tracks

**Symptom:** Track plays to end, then silence. Next track never loads.

**Root Cause:** `onCrossfadeStart` callback is null/missing.

**Check logs:**
```
[Audio] 🔀 AUTO-CROSSFADE: abc → def
[Audio] ❌ CALLBACK MISSING!  ← Problem!
```

**Fix:** Callback must be set after audioEngine initializes. Use polling in useEffect (PlaybackContext.jsx lines 371-410).

### 2. Announcer Never Triggers

**Symptom:** Backend logs "Pending: 203s away" then nothing.

**Root Cause:** Monitoring task wasn't created (early return in schedule function).

**Check:** Backend should log countdown every 10s. If it doesn't, task wasn't created.

**Fix:** Always create asyncio task immediately, regardless of distance. Don't optimize - one task per user is FREE.

### 3. Progress Bar Frozen

**Symptom:** Music plays but progress bar doesn't move.

**Root Cause:** Progress tracking interval not running (inactive device or bug).

**Check logs:**
```
[PlaybackContext] 📥 Playback state: we_are_active=false
```

**Fix:**
- Verify device is active (check DevicePicker)
- Verify progress interval is running (PlaybackContext.jsx lines 464-481)
- Check UIState is receiving progressPercent updates

### 4. Remote Seek Not Working

**Symptom:** Inactive device drags seek bar, active device doesn't respond.

**Root Cause:** Active device's remote seek sync not checking progress difference.

**Check:** Active device should log:
```
[PlaybackContext] 🎛️ Remote seek: 120000ms (audio was at 45000ms)
```

**Fix:** Ensure handlePlaybackState has remote seek sync (lines 189-198).

### 5. Frontend/Backend Progress Desync

**Symptom:** Frontend at 200s, backend thinks 0s. Announcer fires too early/late.

**Root Cause:** Heartbeat not sending or backend ignoring it.

**Check countdown logs:**
```
Backend:  [ANNOUNCER] ⏳ progress: 10.5s
Frontend: [Audio] 🎙️ Frontend at 200.5s  ← Desynced!
```

**Fix:**
- Ensure heartbeat interval running (PlaybackContext.jsx lines 435-462)
- Verify backend calling `handle_playback_heartbeat()`
- Check WebSocket connection is healthy

### 6. Preload Says "Complete" But Audio Doesn't Play

**Symptom:** `🔋 PRELOAD COMPLETE` but crossfade exits with "waiting for next track".

**Root Cause:** `preloadTrack()` resolved before audio data arrived.

**Fix:** Wait for `canplay` event in preload, not just MediaSource `sourceopen`:
```javascript
await new Promise((resolve) => {
  element.addEventListener('canplay', resolve, { once: true })
})
```

### 7. Multiple Devices Playing Simultaneously

**Symptom:** Switch to device B, but device A keeps playing.

**Root Cause:** Device A didn't receive `device_inactive` message or ignored it.

**Check logs:**
```
Device A: [PlaybackContext] 🔕 Received device_inactive message
Device A: [PlaybackContext] 🛑 Stopping audio - device is no longer active
```

**Fix:**
- Verify WebSocket subscription to `device_inactive` event
- Ensure `handleDeviceInactive` calls `audio.stopImmediately()`
- Check `active_device_id` broadcast reaches all devices

### 8. Backend Auto-Advances While Frontend Controls

**Symptom:** Track crossfades smoothly, then jumps back to beginning.

**Root Cause:** Backend's `_playback_loop` auto-advancing when `active_device_id` is set.

**Fix:** Backend should NOT auto-advance when frontend is active:
```python
# playback_state.py _playback_loop
if duration_ms and self.progress_ms >= duration_ms:
    if self.active_device_id:
        # Frontend controls - don't auto-advance
        self.progress_ms = duration_ms
    else:
        # No active device - backend controls
        await self.next(is_auto=True)
```

### 9. New Track Starts at Wrong Position (Mode Switch)

**Symptom:** Switch to Favorites/Discovery mode, new track starts playing from middle (e.g., 3:00 instead of 0:00).

**Root Cause:** Backend's `seed_radio()` sets new `current_track_id` but doesn't reset `progress_ms = 0`. Old track's position is broadcast with new track.

**Check logs:**
```
Backend sends: { current_track: newTrack, progress_ms: 180000 }  ← Should be 0!
```

**Fix:** Always set `self.progress_ms = 0` when changing tracks in `seed_radio()`:
```python
# playback_state.py seed_radio()
if self.queue:
    async with self._queue_lock:
        self.current_track_id = self.queue[0]["id"]
        self.progress_ms = 0  # ← CRITICAL: New track starts at 0
        self._shift_queue_to_target()
```

**Rule:** `progress_ms` is just another variable. Always include it explicitly. Default to 0 on track changes.

---

## Logging Patterns

### Prefixes

```
[Audio]           - Frontend audio operations
[PlaybackContext] - React playback context
[ANNOUNCER]       - Announcer system (both sides)
[PLAYBACK]        - Backend playback operations
[Device]          - Device management
```

### Critical Logs to Watch

**Frontend:**
```
[Audio] ✓ Setting onCrossfadeStart callback
[Audio] 🔀 AUTO-CROSSFADE: abc → def
[Audio] 📡 CALLBACK FIRED: Transition abc → def
[Audio] ⚡ PRELOAD: xyz into Slot B
[Audio] 🔋 PRELOAD COMPLETE: xyz
[Audio] 🎬 Auto-crossfade monitoring active

[PlaybackContext] 📥 Playback state: track=abc, is_playing=true, we_are_active=true
[PlaybackContext] 🎛️ Remote command: pause
[PlaybackContext] 🎛️ Remote seek: 120000ms
[PlaybackContext] 🔕 Received device_inactive message
[PlaybackContext] 🛑 Stopping audio

[Audio] 🎙️ Frontend at 156.3s | Announcer trigger 201.5s | 45.2s away
```

**Backend:**
```
[PLAYBACK] Track transition: abc → def
[PLAYBACK] Shifted queue, current back to position 5
[PLAYBACK] Auto-filling queue: added 3 tracks (mood mode)

[ANNOUNCER] 🎙️ Transition Analysis Report: abc → def
[ANNOUNCER] 📅 Pending: 203s away
[ANNOUNCER] ⏳ Countdown: 45.2s (progress: 156.3s / trigger: 201.5s)
[ANNOUNCER] 🟢 Scheduled: Trigger in 5.0s
[ANNOUNCER] 🎤 Generating DJ script
[ANNOUNCER] 📻 Broadcasting TTS audio
```

---

## Testing Checklist

### Basic Playback
- [ ] Fresh page load auto-plays (active device)
- [ ] Play/pause works immediately (optimistic)
- [ ] Seek works and updates progress bar smoothly
- [ ] Auto-advance works for 5+ tracks in a row
- [ ] Manual next/previous works
- [ ] Progress bar updates 4x per second (smooth)

### Multi-Device
- [ ] Can switch active device via DevicePicker
- [ ] Old device stops playing when deactivated
- [ ] New device starts playing when activated
- [ ] Inactive device can play/pause active device (remote control)
- [ ] Inactive device can seek on active device
- [ ] Inactive device shows queue and progress (UI sync)
- [ ] Multiple tabs don't fight (device_inactive works)

### Crossfade
- [ ] Crossfade happens smoothly at track end
- [ ] Track doesn't restart from 0:00 after crossfade
- [ ] Works consistently on A→B→A→B cycles
- [ ] Crossfade callback fires every time
- [ ] Preload completes before crossfade needs it
- [ ] Next track loads into nextSlot ahead of time

### Announcer
- [ ] Backend calculates announcer timing
- [ ] Backend broadcasts announcer_hint to frontend
- [ ] Frontend countdown matches backend countdown
- [ ] Announcer triggers at expected time
- [ ] Music volume ducks when DJ speaks
- [ ] Music volume restores after DJ finishes

### Queue
- [ ] Queue stays centered around position 5
- [ ] Auto-fill adds tracks when queue < 11
- [ ] Forward/backward navigation works
- [ ] Radio mode changes queue filling strategy
- [ ] Seed modes find similar tracks correctly

### Sync
- [ ] Heartbeat sends every 5s from active device
- [ ] Backend progress_ms matches frontend
- [ ] Inactive devices see correct progress from broadcasts
- [ ] Remote commands (play/pause/seek) work from inactive devices
- [ ] Active device responds to remote commands within 1s

---

## Architecture Rules

1. **Frontend owns is_playing** - Backend broadcasts but doesn't control
2. **Frontend owns progress** - Active device tracks real-time, reports via heartbeat
3. **Backend owns queue order** - Frontend plays what it's told
4. **Backend owns device coordination** - Sets `active_device_id`
5. **All playback commands via WebSocket** - No REST for play/pause/seek
6. **Callback must fire on crossfade** - Or system breaks
7. **Announcer task created immediately** - Don't defer based on distance
8. **Queue stays centered at position 5** - Radio feel
9. **Heartbeat every 5s** - Backend needs accurate progress
10. **UIState is SSOT** - All UI state flows through UIStateContext
11. **No prop drilling** - Components subscribe to contexts directly
12. **Backend does NOT auto-advance when active_device_id set** - Frontend controls
13. **Always include progress_ms explicitly** - Track change = 0, seek = specific value. Never rely on frontend to guess.
14. **Crossfade state via UIState** - `isCrossfading` published for visual feedback + preload coordination

---

## Recent Fixes

**2026-02-05 - Data Saver Mode Wired Up:**
- **Change:** Data Saver Mode now actually affects playback (was previously just a UI toggle)
- **Cache preference:** When enabled, always returns cached track regardless of bitrate quality
- **Streaming:** Forces 128k bitrate when streaming to minimize data usage
- **Location:** State lives in `UIState.settingsState.dataSaverMode`, consumed by `PlaybackContext.getTrackSource()` and `BitratePicker`
- **Visual:** BitratePicker button shows green 🌿 when active, panel shows Data Saver banner
- **TODO:** Backend queue integration - when Data Saver is on, `_auto_fill_queue()` should prefer tracks that are already cached on the device. Requires new WebSocket message to send cached track IDs to backend.

**2026-02-05 - Crossfade State in UIState:**
- **Change:** `isCrossfading` boolean now published to UIStateContext via pub/sub pattern
- **Flow:** AudioEngine → callback → PlaybackContext → reportEngineStatus → UIState → Components
- **Visual feedback:** Player.jsx shows pulse on play button, scale-down on skip buttons during crossfade
- **Guard pattern:** `isCrossfadingRef.current` in PlaybackContext defers preload during crossfade (nextSlot may still be fading out)
- **Retry:** Effect triggers preload when crossfade ends (`isCrossfading` → false)

**2026-02-04 - Seek Position Reset on Mode Switch:**
- **Bug:** When switching radio modes (favorites, discovery, etc.), track started at old track's position instead of 0
- **Root Cause:** `seed_radio()` in `playback_state.py` wasn't resetting `progress_ms = 0` when setting new track
- **Fix:** Added `self.progress_ms = 0` in two places in `seed_radio()` where `current_track_id` is set
- **Lesson:** `progress_ms` is just another variable - always include it explicitly, default to 0 on track changes

**2025-12-29 - Progress Tracking & Remote Seek:**
- **Added:** Real-time progress tracking (250ms interval) for active devices
- **Added:** Progress flows through UIState (SSOT pattern)
- **Added:** Remote seek sync for active devices (>1s difference detection)
- **Fixed:** Player.jsx to consume progress from UIState instead of local state
- **Fixed:** Player.jsx to get `isActiveDevice` from UIState instead of undefined playback prop
- **Result:** Progress bar updates smoothly, remote seek works perfectly

**2025-12-03 - Auto-Crossfade Notification Chain:**
- Fixed callback lifecycle: now set via polling after engine init
- Added logging to detect missing callback
- Fixed preloadTrack() to wait for actual audio data (canplay event)
- Added comprehensive logging for debugging notification chain

**2025-12-03 - Announcer System:**
- Fixed monitoring task creation (removed early return on PENDING_THRESHOLD)
- Added countdown logging every 10s (both frontend + backend)
- Added announcer_hint to frontend state
- Backend now broadcasts announcer timing to frontend
- Added frontend countdown showing progress vs trigger time

**Key Lessons:**
- Don't prematurely optimize asyncio tasks - one per user is FREE
- Always use SSOT (UIState) for shared UI state - no local duplication
- Progress must flow: Audio → PlaybackContext → UIState → Components
- Active device controls playback, inactive devices remote control via backend

---

## Critical Files Reference

### Frontend
- `client/src/lib/audioEngine.js` - A/B slot engine, crossfade execution
- `client/src/contexts/PlaybackContext.jsx` - **CRITICAL** - Orchestration, intervals, WebSocket
- `client/src/contexts/UIStateContext.jsx` - **SSOT** - All UI state (progress, activeDevice, etc.)
- `client/src/hooks/useAudio.js` - Hook wrapper around audioEngine
- `client/src/contexts/WebSocketContext.jsx` - WebSocket client
- `client/src/lib/session.js` - Device ID management
- `client/src/components/Player.jsx` - Player UI (progress bar, controls)
- `client/src/components/DevicePicker.jsx` - Device selection UI

### Backend
- `server/services/playback_state.py` - **CRITICAL** - State machine, queue carousel
- `server/services/playback_service.py` - Session management
- `server/services/device_management_service.py` - Device registration/activation
- `server/services/announcer_service.py` - DJ timing analysis, monitoring
- `server/app.py` - WebSocket handlers (lines ~2000-2600)
- `server/services/audio_features_service.py` - Audio analysis
- `server/services/catalog_vector_search_service.py` - Similarity search

---

## End of Document

This document describes the complete playback and audio engine architecture for PLAiR.fm as of February 2026. For questions or updates, see the team or check git history.
