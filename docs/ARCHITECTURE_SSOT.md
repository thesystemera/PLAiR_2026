# AI Radio - Single Source of Truth (SSOT) Architecture

## Critical Context: DO NOT REPEAT PAST MISTAKES

This document explains the **Publisher/Subscriber** architecture using **UIStateContext as the Single Source of Truth (SSOT)**. If you're an AI working on this codebase and the user says things like:

- "Radio is just a fucking panel"
- "The engine reports directly to UIState"
- "Why are we fighting this?"

**STOP IMMEDIATELY** and read this document. The architecture is intentionally simple and should not be complicated.

---

## The Problem We Solved

**Before:** Components were passing data through intermediaries (like Radio.jsx acting as a "bridge"), causing:
- Audio crackle and frame drops during DJ speech
- Main thread starvation from 60fps React re-renders
- Unclear data flow and tight coupling
- Performance bottlenecks from unnecessary re-renders

**After:** Clean Publisher/Subscriber pattern where:
- Engines publish data directly to UIState
- UIState derives visual state (the SSOT "Brain")
- Views subscribe to UIState and render based on state
- No middleman components

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                         ENGINES                              │
│                    (Data Producers)                          │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────┐│
│  │ PlaybackContext  │  │ useDJAudioStream │  │VoiceRecording││
│  │   (Music)        │  │   (DJ Audio)     │  │  Context   ││
│  └────────┬─────────┘  └────────┬─────────┘  └─────┬──────┘│
│           │                     │                    │        │
│           │ reportEngineStatus()│                    │        │
│           └─────────────────────┼────────────────────┘        │
└───────────────────────────────┼─────────────────────────────┘
                                 ▼
                    ┌────────────────────────┐
                    │    UIStateContext      │
                    │       (SSOT Brain)     │
                    │                        │
                    │ - Stores engineState   │
                    │ - Derives visualState  │
                    │ - Computes colors      │
                    └───────────┬────────────┘
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
        ┌───────────────────┐   ┌──────────────────┐
        │  VIEWS (Dumb)     │   │   VIEWS (Dumb)   │
        │ - Radio.jsx       │   │ - Interactive    │
        │ - AudioCanvas     │   │   Button         │
        │ - Shader Panel    │   │ - Others         │
        └───────────────────┘   └──────────────────┘
```

---

## The Three Layers

### 1. **ENGINES** (Data Producers)

Engines know NOTHING about visual state. They only know about their raw data and report it to UIState.

#### **PlaybackContext.jsx** - Music Playback Engine
```javascript
useEffect(() => {
  reportEngineStatus({
    isMusicPlaying: state.is_playing,
    isMusicPaused: !state.is_playing && state.current_track !== null,
    progressPercent: (state.progress_ms / state.current_track?.duration_ms) * 100
  })
}, [state.is_playing, state.current_track, state.progress_ms])
```

Reports:
- `isMusicPlaying` (boolean)
- `isMusicPaused` (boolean)
- `progressPercent` (0-100)

#### **useDJAudioStream.js** - DJ Audio Engine
```javascript
useEffect(() => {
  reportEngineStatus({
    isDJSpeaking,
    djFftData: fftData, // Array[32]
    speakerColor: getSpeakerColor() // { r, g, b }
  })
}, [isDJSpeaking, fftData])

// Polls CSS for dynamic color every 100ms when DJ is speaking
useEffect(() => {
  if (!isDJSpeaking) return
  const colorInterval = setInterval(() => {
    reportEngineStatus({ speakerColor: getSpeakerColor() })
  }, 100)
  return () => clearInterval(colorInterval)
}, [isDJSpeaking])
```

Reports:
- `isDJSpeaking` (boolean)
- `djFftData` (Array[32] of frequency data)
- `speakerColor` ({ r, g, b } from CSS variables)

Performance optimizations:
- Throttled FFT updates (20fps instead of 60fps)
- Zero-allocation processing with for loops
- Uses refs for internal RAF loop, setState only every 3 frames

#### **VoiceRecordingContext.jsx** - Microphone Engine
```javascript
useEffect(() => {
  reportEngineStatus({
    isMicRecording: voiceRecorder.isRecording,
    micFftData: fftData // Array[32]
  })
}, [voiceRecorder.isRecording, fftData])
```

Reports:
- `isMicRecording` (boolean)
- `micFftData` (Array[32] of frequency data)

Performance optimizations:
- Same throttled FFT updates and zero-allocation as DJ engine

---

### 2. **UIStateContext** (SSOT Brain)

**File:** `client/src/contexts/UIStateContext.jsx`

This is the ONLY place where visual state logic lives. It receives raw data from engines and derives the application's visual state.

#### Engine State Storage
```javascript
const [engineState, setEngineState] = useState({
  // Music engine
  isMusicPlaying: false,
  isMusicPaused: false,
  progressPercent: 0,

  // DJ audio engine
  isDJSpeaking: false,
  djFftData: new Array(32).fill(0),
  speakerColor: { r: 147, g: 51, b: 234 },

  // Microphone engine
  isMicRecording: false,
  micFftData: new Array(32).fill(0),

  // AI processing state
  isAIProcessing: false
})
```

#### Visual State Derivation (Priority Order)
```javascript
const visualState = useMemo(() => {
  if (engineState.isMicRecording) return 1    // RECORDING (Red)
  if (engineState.isAIProcessing) return 4    // PROCESSING (Blue)
  if (engineState.isDJSpeaking) return 3      // DJ SPEAKING (Dynamic)
  if (engineState.isMusicPlaying) return 2    // PLAYING (Green)
  if (engineState.isMusicPaused) return 5     // PAUSED (Yellow)
  return 0                                     // IDLE (Grey)
}, [engineState])
```

**CRITICAL:** This priority order determines what state "wins" when multiple engines are active. For example, if music is playing AND DJ is speaking, DJ wins (state 3).

#### Color Resolution
```javascript
const radioProgressData = useMemo(() => {
  let resolvedColor = STATE_COLORS[0] // Default grey

  if (visualState === 3) {
    resolvedColor = engineState.speakerColor // Dynamic DJ color
  } else if (STATE_COLORS[visualState]) {
    resolvedColor = STATE_COLORS[visualState] // Static palette
  }

  return {
    stateInt: visualState,
    progressPercent: engineState.progressPercent,
    currentVisualColor: resolvedColor
  }
}, [visualState, engineState.progressPercent, engineState.speakerColor])
```

#### Public API
```javascript
// reportEngineStatus() - Engines call this to update state
const reportEngineStatus = useCallback((updates) => {
  setEngineState(prev => ({ ...prev, ...updates }))
}, [])

// Exposed through useRadioUI() for views
export function useRadioUI() {
  return {
    visualState,        // 0-5 integer
    progressData,       // { stateInt, progressPercent, currentVisualColor }
    engineState,        // Full engine state for detailed reads
    // ... button interaction helpers
  }
}
```

---

### 3. **VIEWS** (Dumb Components)

Views are "pixel renderers" - they read from UIState and render based on the data. They do NOT contain business logic.

#### **Radio.jsx** - Just a Panel Container
```javascript
export function Radio({ playback, isFullscreen, isMobileView, mobilePanel }) {
  const { visualState, engineState, updateButtonOpacity } = useRadioUI()

  // ONLY manages audio element for DJ stream
  useDJAudioStream(audioElementRef)

  // NO engine state reporting
  // NO data transformation
  // Just layout and interaction handlers

  return (
    <div>
      <audio ref={audioElementRef} />
      <Conversation />
      <InteractiveEngagementButton
        onRecordingComplete={sendToDJ}
        audioElementRef={audioElementRef}
        onSwipeLeft={...}
        onSwipeRight={...}
      />
    </div>
  )
}
```

**Key Point:** Radio.jsx does NOT call `useVoiceRecording()` or manage recording state. That's the button's job.

#### **InteractiveEngagementButton.jsx** - Self-Contained Recording
```javascript
export function InteractiveEngagementButton({
  onRecordingComplete,
  audioElementRef,
  onSwipeLeft,
  onSwipeRight,
  // ... other handlers
}) {
  const { visualState, engineState } = useRadioUI()
  const { isRecording, startRecording, stopRecording } = useVoiceRecording()

  // Reads FFT from UIState based on current visual state
  const activeFftData = visualState === 1
    ? engineState.micFftData
    : (visualState === 3 ? engineState.djFftData : new Array(32).fill(0))

  // Manages its own recording lifecycle
  const handleMouseDown = () => {
    void startRecording('radio')
  }

  const handleMouseUp = async () => {
    const audioBlob = await stopRecording()
    if (audioBlob && onRecordingComplete) {
      await onRecordingComplete(audioBlob)
    }
  }

  // Canvas blob reacts to activeFftData from UIState
  // Color comes from progressData.currentVisualColor
}
```

**Key Point:** Button manages its own recording and reads FFT data from UIState, not props.

#### **Other Views** (AudioReactiveCanvas, Shader Panels, etc.)
All follow the same pattern:
```javascript
const { visualState, progressData, engineState } = useRadioUI()
// Read what you need, render accordingly
```

---

## Data Flow Examples

### Example 1: User Presses Record Button

1. **Button** calls `startRecording('radio')`
2. **VoiceRecordingContext** starts recording, begins FFT processing
3. **VoiceRecordingContext** calls `reportEngineStatus({ isMicRecording: true, micFftData: [...] })`
4. **UIStateContext** updates `engineState.isMicRecording = true`
5. **UIStateContext** recomputes `visualState` → becomes 1 (RECORDING)
6. **UIStateContext** recomputes `radioProgressData.currentVisualColor` → RED
7. **Button** re-renders, reads `visualState === 1` and `engineState.micFftData`
8. **Canvas** blob turns red and reacts to mic FFT data
9. **Shader panels** update based on new color

### Example 2: DJ Starts Speaking While Music Plays

1. **WebSocket** receives `tts_stream_start` event
2. **useDJAudioStream** plays audio, sets `isDJSpeaking = true`
3. **useDJAudioStream** calls `reportEngineStatus({ isDJSpeaking: true, djFftData: [...] })`
4. **UIStateContext** updates `engineState.isDJSpeaking = true`
5. **UIStateContext** recomputes `visualState` → becomes 3 (DJ SPEAKING, overrides music state 2)
6. **UIStateContext** recomputes color → Dynamic DJ color from CSS
7. **useDJAudioStream** starts 100ms color polling interval
8. **All views** re-render with DJ visual state and dynamic color
9. **Button blob** reacts to DJ FFT data instead of music

### Example 3: Music Track Progress Updates

1. **WebSocket** sends `playback_state` update with new `progress_ms`
2. **PlaybackContext** receives update, updates internal state
3. **PlaybackContext** calls `reportEngineStatus({ progressPercent: 45.2 })`
4. **UIStateContext** updates `engineState.progressPercent`
5. **UIStateContext** recomputes `radioProgressData.progressPercent`
6. **Progress bar** re-renders with new percentage
7. **Shader** updates progress visual

**Performance Note:** PlaybackContext has optimizations to prevent re-renders on every tick. It only updates when structural changes occur or progress jumps >2000ms.

---

## Critical Rules for AI Developers

### ❌ DO NOT:

1. **Make Radio.jsx a "bridge"** - It's just a panel container
2. **Pass engine data through props** - Views read from UIState directly
3. **Report engine status from views** - Engines report their own status
4. **Add business logic to views** - Logic lives in UIStateContext only
5. **Call engine hooks from multiple places** - Each engine is called once
6. **Use setState for high-frequency audio data** - Use refs + throttled updates
7. **Poll for state in views** - Subscribe to UIState through hooks

### ✅ DO:

1. **Engines report directly to UIState** via `reportEngineStatus()`
2. **UIState derives visual state** from engine flags (priority order matters)
3. **Views read from UIState** via `useRadioUI()` or `useUIState()`
4. **Keep components dumb** - Just render based on state
5. **Use refs for RAF loops** - Only setState when needed for React updates
6. **Throttle high-frequency updates** - FFT at 20fps, not 60fps
7. **Trust the SSOT** - UIState decides visual state, not views

---

## Performance Optimizations

### FFT Processing (60fps → 20fps)
```javascript
// In RAF loop
frameCounterRef.current++
if (frameCounterRef.current >= 3) {
  frameCounterRef.current = 0
  setFftData([...fftDataRef.current]) // React update every 3 frames
}
```

### Zero-Allocation Processing
```javascript
// ❌ BAD: Creates new arrays every frame
const bandAmplitudes = frequencyBands.map(band => {
  const slice = allData.slice(startIndex, endIndex)
  return slice.reduce((sum, val) => sum + val, 0) / slice.length
})

// ✅ GOOD: Reuses array, no intermediate allocations
const bandAmplitudes = new Array(frequencyBands.length)
for (let b = 0; b < frequencyBands.length; b++) {
  let sum = 0, count = 0
  for (let i = startIndex; i < endIndex; i++) {
    sum += allData[i]
    count++
  }
  bandAmplitudes[b] = count > 0 ? sum / count : 0
}
```

### Playback State Guards
```javascript
// Only trigger React re-render if structural changes occurred
if (!trackChanged && !playingChanged && !indexChanged && !queueChanged && !isSeek) {
  return prevState // Same reference = no re-render
}
```

---

## Debugging Tips

### "FFT data not updating"
- Check engine is calling `reportEngineStatus()` with FFT array
- Check UIStateContext has `djFftData`/`micFftData` in engineState
- Check view is reading from `engineState` not stale props
- Verify throttling isn't too aggressive (should update every 3 frames)

### "Wrong visual state"
- Check priority order in `visualState` useMemo
- Verify engine is setting its flag (isMusicPlaying, isDJSpeaking, etc.)
- Check if multiple engines are active (priority determines winner)

### "Colors not updating"
- Check DJ color polling interval is running (only when isDJSpeaking)
- Verify CSS variable `--dj-speaking-glow` is being set by DJColorManager
- Check `speakerColor` is being reported to UIState

### "Performance issues"
- Check for setState in RAF loops (should use refs)
- Verify FFT throttling is active (frameCounter logic)
- Check PlaybackContext isn't re-rendering on every tick
- Use React DevTools Profiler to find unnecessary re-renders

---

## Common Mistakes & Solutions

### Mistake: "I need to pass FFT data to the button"
**Solution:** Button reads FFT from UIState, not props.

### Mistake: "Radio needs to know if recording is active"
**Solution:** Radio doesn't care. UIState knows via `engineState.isMicRecording`.

### Mistake: "I need to report engine status from Radio.jsx"
**Solution:** Engines report their own status. Radio does nothing.

### Mistake: "Multiple components need playback data, so Radio should coordinate"
**Solution:** Multiple components read from UIState. No coordinator needed.

### Mistake: "Visual state isn't updating fast enough"
**Solution:** Check if engine is triggering re-render (setState, not just ref update).

---

## File Reference

### Core Architecture Files
- `client/src/contexts/UIStateContext.jsx` - SSOT Brain
- `client/src/contexts/PlaybackContext.jsx` - Music engine
- `client/src/hooks/useDJAudioStream.js` - DJ audio engine
- `client/src/contexts/VoiceRecordingContext.jsx` - Microphone engine

### View Files
- `client/src/components/Radio.jsx` - Panel container
- `client/src/components/InteractiveEngagementButton.jsx` - Recording button
- `client/src/components/AudioReactiveCanvas.jsx` - Visual canvas
- `client/src/components/ShaderPanel.jsx` - Shader effects

---

---

## Device Management - Multi-Device SSOT

### The Problem

Multiple devices (desktop, mobile, tablet) can connect to the same user session. **Only ONE device should play audio at a time.** All devices should show the same UI (universal remote), but only the active device plays sound.

### The Solution: Three-Layer SSOT

**Backend SSOT:** `playback_state.active_device_id` (per-session, in-memory)
**Frontend SSOT:** `UIStateContext.engineState.isActiveDevice` (derived from backend)
**Components:** Read from UIState, never check locally

### Architecture Flow

```
┌─────────────────────────────────────────────────────────────┐
│                      BACKEND                                 │
├─────────────────────────────────────────────────────────────┤
│  playback_state.active_device_id = "device-123"             │
│  ↓ WebSocket broadcast to ALL devices in session            │
└────────────────────┬────────────────────────────────────────┘
                     ↓
         ┌───────────────────────────────┐
         │   PlaybackContext (Receiver)  │
         ├───────────────────────────────┤
         │  weAreActive = (data.active_device_id === deviceId)  │
         │                               │
         │  ✅ audio.setActiveDevice(weAreActive)              │
         │     [Hardware enforcement]    │
         │                               │
         │  ✅ reportEngineStatus({ isActiveDevice })          │
         │     [Publish to UIState]      │
         └───────────────┬───────────────┘
                         ↓
              ┌──────────────────────┐
              │   UIStateContext     │
              │      (SSOT)          │
              ├──────────────────────┤
              │ engineState.isActiveDevice = true/false      │
              └──────────┬───────────┘
                         │
         ┌───────────────┴───────────────┐
         ↓                               ↓
┌────────────────────┐      ┌────────────────────┐
│   DevicePicker     │      │ useDJAudioStream   │
│ showInactive =     │      │ if (!isActiveDevice)│
│ !isActiveDevice    │      │   return (block)   │
└────────────────────┘      └────────────────────┘
```

### Three Enforcement Layers

#### 1. Bandwidth Layer (PlaybackContext)
```javascript
// Don't load audio files on inactive devices
if (!isActiveDeviceRef.current) {
  logger.info('[PlaybackContext] Inactive device - skipping audio load')
  return
}
```

**What it blocks:**
- Loading track audio files
- Preloading next track
- Sending heartbeat to backend

**Why:** Inactive devices shouldn't waste bandwidth downloading audio they won't play.

#### 2. Hardware Layer (AudioEngine)
```javascript
_enforceActiveDevice(operation = 'operation') {
  if (!this.isActiveDevice) {
    logger.warn(`[AudioEngine] ⛔ Blocked ${operation}: Not active device`)
    return false
  }
  return true
}

// Used in all playback operations
play() {
  if (!this._enforceActiveDevice('play')) return
  // ... actual play logic
}
```

**What it blocks:**
- play(), pause(), seek()
- loadTrack(), preloadTrack()
- crossfade()

**Why:** Prevents sound from actually coming out of inactive devices (defensive programming).

#### 3. Visual Layer (UIState → Components)
```javascript
// DevicePicker
const { engineState } = useUIState()
showInactive = !engineState.isActiveDevice

// useDJAudioStream
const { engineState } = useUIState()
const isActiveDevice = engineState.isActiveDevice
if (!isActiveDevice || !data?.unique_id) return  // Block DJ stream
```

**What it shows:**
- Inactive devices show "Playing on another device" banner
- All devices display queue, current track, progress (universal remote)
- Active device shows normal playback UI

**Why:** Inactive devices still show UI for remote control functionality.

### Critical Pattern: Default to FALSE

**The Bug We Fixed:**
```javascript
// ❌ OLD - Caused race condition
AudioEngine.isActiveDevice = true  // Defaults to active!
PlaybackContext.isActiveDeviceRef = useRef(true)
UIStateContext.engineState.isActiveDevice = true

// Result: ALL devices try to play before backend says who's active
```

**The Fix:**
```javascript
// ✅ NEW - Wait for backend activation
AudioEngine.isActiveDevice = false
PlaybackContext.isActiveDeviceRef = useRef(false)
UIStateContext.engineState.isActiveDevice = false

// Result: Devices wait for explicit activation
```

### Activation Flow Example

**User opens Device B while Device A is playing:**

1. Device B loads → `isActiveDevice = false` (default)
2. Device B connects to WebSocket
3. **Backend** receives connection, sets `active_device_id = Device B`
4. **Backend** broadcasts `playback_state` with `active_device_id = Device B` to ALL devices
5. **Device A** receives state:
   ```javascript
   weAreActive = (data.active_device_id === deviceId)  // false
   audio.setActiveDevice(false)  // Stops audio immediately
   reportEngineStatus({ isActiveDevice: false })
   ```
7. **Device B** receives state:
   ```javascript
   weAreActive = (data.active_device_id === deviceId)  // true
   audio.setActiveDevice(true)  // Enables audio
   reportEngineStatus({ isActiveDevice: true })
   ```
8. **UIState** updates → Components re-render
9. Device A shows "Playing on another device" banner
10. Device B plays audio normally

### Critical Logic: Exact Match Only

**The Bug:**
```javascript
// ❌ WRONG - Makes ALL devices active when null
const weAreActive = !data.active_device_id || data.active_device_id === deviceId

// When active_device_id is null:
// !null = true → ALL devices think they're active!
```

**The Fix:**
```javascript
// ✅ CORRECT - Only active if backend explicitly says so
const weAreActive = data.active_device_id === deviceId

// When active_device_id is null:
// null === deviceId → false → No devices active (correct!)
```

### DO NOT Patterns

❌ **Don't check device state locally in components:**
```javascript
// ❌ BAD - Local state
const [isActiveDevice, setIsActiveDevice] = useState(true)

// ✅ GOOD - Read from UIState
const { engineState } = useUIState()
const isActiveDevice = engineState.isActiveDevice
```

❌ **Don't manage playback state in multiple places:**
```javascript
// ❌ BAD - Multiple components managing device state
// PlaybackContext: useWebSocketSubscribe('playback_state', ...)
// DevicePicker: useWebSocketSubscribe('playback_state', ...) // Manages device list separately
// Component: const [isActive, setIsActive] = useState() // Local duplicate state

// ✅ GOOD - PlaybackContext listens, publishes to UIState
// PlaybackContext handles playback_state, reports to UIState
// DevicePicker listens to playback_state for device list updates only
// Other components read from UIState
```

### Key Takeaway

**Device state follows the same SSOT pattern as everything else:**

1. **Backend** sets `active_device_id` (source of truth)
2. **PlaybackContext** receives it, calculates `weAreActive`, reports to UIState
3. **UIStateContext** stores `engineState.isActiveDevice` (SSOT)
4. **Components** read from UIState (never check locally)

**No scattered checks. One source of truth.**

---

## Summary

**This architecture is intentionally simple:**

1. Engines produce data → report to UIState
2. UIState derives visual state → exposes through hooks
3. Views consume state → render pixels

**No bridges. No middlemen. No coordinators. No scattered state.**

Just clean Publisher/Subscriber with a Single Source of Truth.

If you find yourself adding complexity, you're doing it wrong. Stop and re-read this document.
