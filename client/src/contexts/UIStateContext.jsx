/**
 * UIStateContext - Single Source of Truth (SSOT) for Application State
 *
 * ARCHITECTURE PATTERN: Publisher/Subscriber with SSOT + Fast/Slow Lane
 *
 * This context is the "brain" of the application. It manages all UI state and provides
 * a clean pub/sub interface. Components should NEVER manage shared UI state locally.
 *
 * GOLDEN RULE: If any UI state could benefit the whole app, it MUST live here.
 *
 * ═══════════════════════════════════════════════════════════════════════════════
 * FAST LANE vs SLOW LANE - Performance Optimization
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 * ⚡ FAST LANE (Refs - NO React re-renders):
 * - For HIGH FREQUENCY data that updates many times per second (20-60fps)
 * - Stored in useRef() - updates don't trigger React re-renders
 * - Read directly in RAF loops (requestAnimationFrame) for smooth 60fps performance
 * - Examples:
 *   • engineRef.progress_ms - Updates constantly during playback
 *   • djFftDataRef, micFftDataRef, shoutoutFftDataRef - FFT data ~20fps
 *   • speakerColorRef - Dynamic color updates
 *   • interfaceRef - Scroll position/velocity for parallax effects
 *
 * 🐌 SLOW LANE (State - Triggers React re-renders):
 * - For LOW FREQUENCY data that changes rarely (few times per song or less)
 * - Stored in useState() - updates trigger React component re-renders
 * - Components subscribe and automatically update when data changes
 * - Examples:
 *   • engineState.is_playing - Boolean (start/stop/pause, ~2-4 times per song)
 *   • engineState.currentTrack - Changes once per song
 *   • engineState.isMusicPlaying, isDJSpeaking - Discrete state flags
 *
 * WHY? Avoid unnecessary re-renders. If RAF loops read from React state, every
 * update would cascade through the component tree causing expensive re-renders.
 * Refs let RAF loops read at 60fps without triggering React's reconciliation.
 *
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 * PUBLISHERS (How to update state):
 * - reportEngineStatus() - Engine state (music, DJ, mic, AI processing)
 *   → Splits updates into fast lane (refs) and slow lane (state)
 * - publishAudioState() - Audio/caching state
 * - publishQueueState() - Generation queue state
 * - publishAuthState() - Authentication state
 * - publishRadioState() - Radio/seed mode state
 * - publishContentUpdate() - Content update counters (triggers data refresh)
 * - reportInterfaceState() - Interface state (fullscreen, scrolling, mobile visibility)
 * - publishToast() - Toast notifications (success, error, info, warning)
 *
 * SUBSCRIBERS (How to read state):
 * - useUISelector(state => slice) - Subscribe to ONE slice; re-renders only when that slice changes
 *   (shallow compare). Prefer this in anything that renders often or renders a big subtree.
 * - useUIState() - Access full context (both fast and slow lane); re-renders on ANY UIState change
 * - useRadioUI() - Convenience hook for radio-specific state
 * - useArtwork() - Artwork URL management
 *
 * ENGINES publish data → UIState derives visual state → VIEWS subscribe and render
 *
 * NO MIDDLEMEN. NO PROP DRILLING. Just clean pub/sub.
 *
 * See: docs/ARCHITECTURE_SSOT_PATTERN.md for detailed documentation
 */

import { createContext, startTransition, useContext, useState, useCallback, useMemo, useRef, useEffect, useLayoutEffect, useSyncExternalStore } from 'react'
import { artworkCache, artworkThumbCache, enrichedArtworkCache } from '../lib/mediaCache'
import { artworkPrefetcher } from '../lib/artworkPrefetcher'
import { AudioInteractionManager } from '../lib/audioInteractionManager'
import { logger } from '../lib/logger'
import { safeStorage } from '../lib/safeStorage'
import { UI_FULLSCREEN, FALLBACK_GRADIENT_HEX, getOnAirSegment } from '../lib/themeManager'
import { api } from '../lib/api'
import { MODAL_OPEN_PAUSE_MS, pauseSceneRendering } from '../lib/renderPause'

const TILT_STORAGE_KEY = 'tiltEffects'
const RADIO_INPUT_KEY = 'radioInputMode'
const initialRadioInput = () => (safeStorage.get(RADIO_INPUT_KEY) === 'text' ? 'text' : 'voice')
const TILT_NEEDS_PERMISSION = typeof DeviceOrientationEvent !== 'undefined' &&
  typeof DeviceOrientationEvent.requestPermission === 'function'

/**
 * ═══════════════════════════════════════════════════════════════════════════════
 * UNIFIED GLASS EFFECT CONFIGURATION
 * ═══════════════════════════════════════════════════════════════════════════════
 *
 * Single source of truth for glass panel visual treatment.
 * Uses REFERENCE_DIMENSION_PX as the base for all calculations to ensure
 * consistent curve appearance regardless of screen orientation.
 *
 * The key insight: we use ONE reference dimension (the larger of width/height)
 * and apply it evenly to both axes. This gives truly consistent curved edges.
 */
export const GLASS_EFFECT_CONFIG = {
  // Base reference dimension - used for ALL calculations
  // GLSL uses: max(canvasWidth, canvasHeight)
  // CSS uses: 100% of container (reference dimension = container size)
  referenceDimensionPx: 1000,  // Normalized reference (GLSL divides by this)

  // Corner radius as percentage of reference dimension
  // 1.2% = ~12px on a 1000px reference
  cornerRadiusPct: 0.012,

  // Feather amount as percentage of reference dimension
  // 0.8% = ~8px on a 1000px reference
  featherPct: 0.008,

  // Inset shadow intensity
  edgeGlowIntensity: 0.25,

  // Opacity levels for different contexts
  opacity: {
    panelHeader: 0.9,
    catalogHeader: 0.95,
    searchBar: 0.9
  },

  // Helper to get actual pixel values (for documentation/reference)
  get cornerRadiusPx() { return Math.round(this.referenceDimensionPx * this.cornerRadiusPct) },
  get featherPx() { return Math.round(this.referenceDimensionPx * this.featherPct) }
}

const DJ_DUCK_RELEASE_HOLD_MS = 350

const UIStateContext = createContext(null)
const ArtworkStoreContext = createContext(null)
const RadioButtonContext = createContext(null)
const UIActionsContext = createContext(null)
const RadioStateContext = createContext(null)
const UIStoreContext = createContext(null)
const EMPTY_CLIPS = []
const noopUnsubscribe = () => {}
const NO_SELECTION = Symbol('no-selection')

function createUIStore() {
  const listeners = new Set()
  const store = {
    value: null,
    get: () => store.value,
    subscribe: (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    notify: () => listeners.forEach(listener => listener()),
  }
  return store
}

export function shallowEqual(a, b) {
  if (Object.is(a, b)) return true
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object') return false
  const keys = Object.keys(a)
  if (keys.length !== Object.keys(b).length) return false
  for (const key of keys) {
    if (!Object.prototype.hasOwnProperty.call(b, key) || !Object.is(a[key], b[key])) return false
  }
  return true
}

const GRADIENT_COLORS = FALLBACK_GRADIENT_HEX

function gradientSvgDataUrl(color1, color2) {
  return `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:${color1}"/><stop offset="100%" style="stop-color:${color2}"/></linearGradient></defs><rect width="100" height="100" fill="url(#g)"/><text x="50" y="65" font-size="40" text-anchor="middle" fill="white" opacity="0.8">🎵</text></svg>`)}`
}

function generatePlaceholderDataURL(id) {
  try {
    const index = id ? parseInt(id.slice(0, 2), 16) % GRADIENT_COLORS.length : 0
    return gradientSvgDataUrl(...GRADIENT_COLORS[index])
  } catch {
    return gradientSvgDataUrl(...GRADIENT_COLORS[0])
  }
}

export const uiState = {
  audioState: {
    isOnline: navigator.onLine,
    isServerAvailable: navigator.onLine,
    connectionMode: navigator.onLine ? 'full' : 'offline',
    offlineMode: !navigator.onLine,
    isCached: false,
    buffering: false,
    bitrate: null,
    effectiveBitrate: null,
    networkQuality: null,
  },
  downloadState: {
    isDownloading: false,
    currentTrackId: null,
    currentTrackTitle: null,
    downloadedCount: 0,
    totalQueued: 0,
    dailyDownloadedBytes: 0,
    dailyLimit: 500 * 1024 * 1024,
    isEnabled: (() => { try { return localStorage.getItem('backgroundDownloads') !== 'false' } catch { return true } })(),
  },
  authState: {
    isAuthenticated: (() => { try { return !!localStorage.getItem('cached_user') } catch { return false } })(),
    user: null,
  }
}

let updateDownloadStateCallback = null

export function setDownloadStateUpdater(callback) {
  updateDownloadStateCallback = callback
}

export function updateDownloadState(updates) {
  if (updateDownloadStateCallback) {
    updateDownloadStateCallback(updates)
  }
}

let authStateChangeCallbacks = []

export function onAuthStateChange(callback) {
  authStateChangeCallbacks.push(callback)
  return () => {
    authStateChangeCallbacks = authStateChangeCallbacks.filter(cb => cb !== callback)
  }
}

function notifyAuthStateChange(authState) {
  authStateChangeCallbacks.forEach(cb => cb(authState))
}

const STATE_COLORS = {
  0: { r: 128, g: 128, b: 128 },
  1: { r: 239, g: 68, b: 68 },
  2: { r: 16, g: 185, b: 129 },
  4: { r: 59, g: 130, b: 246 },
  5: { r: 250, g: 204, b: 21 }
}

const ON_AIR_TINTED_STATES = new Set([0, 2, 5])

function hexToRgb(hex) {
  const value = parseInt(hex.replace('#', ''), 16)
  return { r: (value >> 16) & 255, g: (value >> 8) & 255, b: value & 255 }
}

export function UIStateProvider({ children }) {
  const artworkUrlsRef = useRef(new Map())
  const enrichedArtworkUrlsRef = useRef(new Map())
  const artworkListenersRef = useRef(new Map())
  const pinnedArtworkIdsRef = useRef(new Set())
  const loadingTracksRef = useRef(new Set())
  const loadingEnrichedRef = useRef(new Set())
  const [audioFeatures, setAudioFeatures] = useState(null)
  const [lyricTimestamps, setLyricTimestamps] = useState(null)

  const videoClipsMapRef = useRef(new Map())
  const [videoClipsByTrack, setVideoClipsByTrack] = useState({})
  const loadingVideoClipsRef = useRef(new Set())

  const djFftDataRef = useRef(new Array(32).fill(0))
  const micFftDataRef = useRef(new Array(32).fill(0))
  const shoutoutFftDataRef = useRef(new Array(32).fill(0))
  const speakerColorRef = useRef({ r: 147, g: 51, b: 234 })

  const gyroscopeRef = useRef({
    parallaxX: 0,
    parallaxY: 0,
  })

  const mouseRef = useRef({
    parallaxX: 0,
    parallaxY: 0,
  })

  const physicsState = useRef({
    x: 0,
    y: 0,
    vx: 0,
    vy: 0,
    rawTargetX: 0,
    rawTargetY: 0,
    smoothTargetX: 0,
    smoothTargetY: 0
  })

  const sensorBaseline = useRef({
    beta: 0,
    gamma: 0,
    isCalibrated: false,
    startTime: Date.now()
  })

  const mixerRefInternal = useRef(null)

  const [engineState, setEngineState] = useState({
    isMicRecording: false,
    isVideoPreviewPlaying: false,
    isDJSpeaking: false,
    isShoutoutPlaying: false,
    isMusicPlaying: false,
    isMusicPaused: false,
    isAIProcessing: false,
    isActiveDevice: false,
    activeDeviceId: null,
    activeDeviceOnline: false,
    isCrossfading: false,
    is_playing: false,
    currentTrack: null,
    queue: [],
    currentIndex: 0,
    talkBreak: null,
    audioNeedsTap: false,
    djActivity: [],
  })

  const engineRef = useRef({
    progress_ms: 0
  })

  const lastProgressUpdateTimeRef = useRef(Date.now())

  const [shoutoutModalState, setShoutoutModalState] = useState({
    isOpen: false,
    shoutout: null
  })

  const [uploadModalOpen, setUploadModalOpen] = useState(false)
  const [usageModalOpen, setUsageModalOpen] = useState(false)

  const [isOfflineRendering, setIsOfflineRendering] = useState(false)

  const [isScreenVisible, setIsScreenVisible] = useState(!document.hidden)

  useEffect(() => {
    const handleVisibilityChange = () => {
      setIsScreenVisible(!document.hidden)
    }
    const handlePageHide = () => setIsScreenVisible(false)
    const handlePageShow = () => setIsScreenVisible(true)
    document.addEventListener('visibilitychange', handleVisibilityChange)
    window.addEventListener('pagehide', handlePageHide)
    window.addEventListener('pageshow', handlePageShow)
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      window.removeEventListener('pagehide', handlePageHide)
      window.removeEventListener('pageshow', handlePageShow)
    }
  }, [])

  const duckingStateRef = useRef('idle')

  const setMixerRef = useCallback((ref) => {
    mixerRefInternal.current = ref
  }, [])

  const physicsKickRef = useRef(null)

  const [tiltEnabled, setTiltEnabled] = useState(() => !TILT_NEEDS_PERMISSION || safeStorage.get(TILT_STORAGE_KEY) === 'true')
  const tiltControlsRef = useRef(null)

  const enableTiltEffects = useCallback(async (enabled) => {
    safeStorage.set(TILT_STORAGE_KEY, String(enabled))
    if (!enabled) {
      tiltControlsRef.current?.detach()
      setTiltEnabled(false)
      return false
    }
    const granted = await (tiltControlsRef.current?.request() ?? Promise.resolve(false))
    setTiltEnabled(granted)
    if (!granted) safeStorage.set(TILT_STORAGE_KEY, 'false')
    return granted
  }, [])

  useEffect(() => {
    let disposed = false
    let cancelGesture = null

    const handleOrientation = (event) => {
      const { beta, gamma } = event
      if (beta === null || gamma === null) return

      const now = Date.now()
      if (!sensorBaseline.current.isCalibrated) {
        if (now - sensorBaseline.current.startTime > 1000) {
          sensorBaseline.current.beta = beta
          sensorBaseline.current.gamma = gamma
          sensorBaseline.current.isCalibrated = true
        }
        return
      }

      const deltaBeta = beta - sensorBaseline.current.beta
      const deltaGamma = gamma - sensorBaseline.current.gamma

      const clampedBeta = Math.max(-30, Math.min(30, deltaBeta))
      const clampedGamma = Math.max(-30, Math.min(30, deltaGamma))

      physicsState.current.rawTargetX = -(clampedGamma / 30)
      physicsState.current.rawTargetY = -(clampedBeta / 30)
      physicsKickRef.current?.()
    }

    const handleMouseMove = (e) => {
      mouseRef.current.parallaxX = (e.clientX / window.innerWidth - 0.5) * 2
      mouseRef.current.parallaxY = (e.clientY / window.innerHeight - 0.5) * 2
    }

    if (TILT_NEEDS_PERMISSION) {
      const request = () => DeviceOrientationEvent.requestPermission()
        .then((permission) => {
          const granted = permission === 'granted'
          if (granted && !disposed) {
            window.addEventListener('deviceorientation', handleOrientation, { passive: true })
          }
          return granted
        })
        .catch((error) => {
          logger.warn('Motion permission not granted', error)
          return false
        })
      tiltControlsRef.current = {
        request,
        detach: () => window.removeEventListener('deviceorientation', handleOrientation),
      }
      if (safeStorage.get(TILT_STORAGE_KEY) === 'true') {
        cancelGesture = AudioInteractionManager.onUserGesture(() => { void request() })
      }
    } else {
      window.addEventListener('deviceorientation', handleOrientation, { passive: true })
    }

    window.addEventListener('mousemove', handleMouseMove, { passive: true })

    return () => {
      disposed = true
      cancelGesture?.()
      window.removeEventListener('deviceorientation', handleOrientation)
      window.removeEventListener('mousemove', handleMouseMove)
    }
  }, [])

  useEffect(() => {
    window.registerRAFSource?.('UIState-physics')
  }, [])

  useEffect(() => {
    if (!isScreenVisible) return

    let animationFrameId = null
    let lastInputTime = Date.now()
    const IDLE_TIMEOUT = 2000

    const TENSION = 0.12
    const FRICTION = 0.80
    const INPUT_SMOOTHING = 0.10

    const handleInput = () => {
      lastInputTime = Date.now()
      if (!animationFrameId) {
        animationFrameId = requestAnimationFrame(loop)
      }
    }

    const loop = () => {
      const p = physicsState.current

      p.smoothTargetX += (p.rawTargetX - p.smoothTargetX) * INPUT_SMOOTHING
      p.smoothTargetY += (p.rawTargetY - p.smoothTargetY) * INPUT_SMOOTHING

      const forceX = (p.smoothTargetX - p.x) * TENSION
      const forceY = (p.smoothTargetY - p.y) * TENSION

      p.vx = (p.vx + forceX) * FRICTION
      p.vy = (p.vy + forceY) * FRICTION

      p.x += p.vx
      p.y += p.vy

      gyroscopeRef.current.parallaxX = p.x
      gyroscopeRef.current.parallaxY = p.y

      window.__rafDebug?.sources && (window.__rafDebug.sources['UIState-physics'] = (window.__rafDebug.sources['UIState-physics'] || 0) + 1)

      const isMoving = Math.abs(p.vx) > 0.001 || Math.abs(p.vy) > 0.001
      const isRecentInput = Date.now() - lastInputTime < IDLE_TIMEOUT
      if (isMoving || isRecentInput) {
        animationFrameId = requestAnimationFrame(loop)
      } else {
        animationFrameId = null
      }
    }

    physicsKickRef.current = handleInput

    return () => {
      physicsKickRef.current = null
      if (animationFrameId) cancelAnimationFrame(animationFrameId)
    }
  }, [isScreenVisible])

  const duckRestoreTimerRef = useRef(null)

  useEffect(() => () => clearTimeout(duckRestoreTimerRef.current), [])

  useEffect(() => {
    const mixer = mixerRefInternal.current?.current
    if (!mixer) return

    const { isMicRecording, isDJSpeaking, isShoutoutPlaying, isVideoPreviewPlaying } = engineState
    const targetState = isMicRecording ? 'user'
      : (isShoutoutPlaying ? 'shoutout'
      : (isVideoPreviewPlaying ? 'videoPreview'
      : (isDJSpeaking ? 'dj' : 'idle')))

    if (duckingStateRef.current === targetState) return

    const previousState = duckingStateRef.current
    duckingStateRef.current = targetState
    clearTimeout(duckRestoreTimerRef.current)
    duckRestoreTimerRef.current = null

    if (targetState === 'idle' && previousState === 'dj') {
      duckRestoreTimerRef.current = setTimeout(() => {
        duckRestoreTimerRef.current = null
        if (duckingStateRef.current === 'idle') mixerRefInternal.current?.current?.restoreMusic(400)
      }, DJ_DUCK_RELEASE_HOLD_MS)
    } else if (targetState === 'idle') {
      mixer.restoreMusic(400)
    } else if (targetState === 'user') {
      mixer.duckMusic(0.1, 200)
    } else if (targetState === 'shoutout') {
      mixer.duckMusic(0.1, 200)
    } else if (targetState === 'videoPreview') {
      mixer.duckMusic(0.15, 200)
    } else if (targetState === 'dj') {
      mixer.duckMusic(0.25, 400)
    }
  }, [engineState.isMicRecording, engineState.isDJSpeaking, engineState.isShoutoutPlaying, engineState.isVideoPreviewPlaying])

  const [audioState, setAudioState] = useState({
    isOnline: navigator.onLine,
    isServerAvailable: navigator.onLine,
    connectionMode: navigator.onLine ? 'full' : 'offline',
    offlineMode: !navigator.onLine,
    isCached: false,
    buffering: false,
    bitrate: null,
    effectiveBitrate: null,
    networkQuality: null,
  })

  const publishAudioState = useCallback((updates) => {
    setAudioState(prev => {
      const keys = Object.keys(updates)
      if (keys.every(key => prev[key] === updates[key])) return prev
      const newState = { ...prev, ...updates }
      Object.assign(uiState.audioState, newState)
      return newState
    })
  }, [])

  const [queueState, setQueueState] = useState({
    hasActiveJobs: false,
  })

  const publishQueueState = useCallback((updates) => {
    setQueueState(prev => ({ ...prev, ...updates }))
  }, [])

  const [authState, setAuthState] = useState({
    isAuthenticated: false,
    user: null,
  })

  const publishAuthState = useCallback((updates) => {
    setAuthState(prev => {
      const newState = { ...prev, ...updates }
      Object.assign(uiState.authState, newState)
      notifyAuthStateChange(newState)
      return newState
    })
  }, [])

  const [radioState, setRadioState] = useState({
    activeSeedMode: safeStorage.get('lastSeedMode') || null,
  })

  const publishRadioState = useCallback((updates) => {
    setRadioState(prev => {
      if (Object.keys(updates).every(key => prev[key] === updates[key])) return prev
      const newState = { ...prev, ...updates }
      if (updates.activeSeedMode !== undefined) {
        safeStorage.set('lastSeedMode', updates.activeSeedMode || '')
      }
      return newState
    })
  }, [])

  const [downloadState, setDownloadState] = useState({
    isDownloading: false,
    currentTrackId: null,
    currentTrackTitle: null,
    downloadedCount: 0,
    totalQueued: 0,
    dailyDownloadedBytes: 0,
    dailyLimit: 500 * 1024 * 1024,
    isEnabled: safeStorage.get('backgroundDownloads') !== 'false',
  })

  const [settingsState, setSettingsState] = useState({
    ttsMuted: false,
    notificationsMuted: false,
    audioQuality: 'auto',
    dataSaverMode: safeStorage.get('dataSaverMode') === 'true',
    costTickerEnabled: safeStorage.get('costTicker') === 'true',
    autoClaimOnOpen: safeStorage.get('autoClaimOnOpen') !== 'false',
    fpsEnabled: false,
    videoClipsEnabled: false,
    visualQuality: 'high',
  })

  const publishSettings = useCallback((updates) => {
    setSettingsState(prev => {
      const newState = { ...prev, ...updates }
      if (updates.dataSaverMode !== undefined) {
        safeStorage.set('dataSaverMode', String(updates.dataSaverMode))
      }
      if (updates.costTickerEnabled !== undefined) {
        safeStorage.set('costTicker', String(updates.costTickerEnabled))
      }
      if (updates.autoClaimOnOpen !== undefined) {
        safeStorage.set('autoClaimOnOpen', String(updates.autoClaimOnOpen))
      }
      return newState
    })
  }, [])

  const publishDownloadState = useCallback((updates) => {
    setDownloadState(prev => {
      const newState = { ...prev, ...updates }
      Object.assign(uiState.downloadState, newState)
      if (updates.isEnabled !== undefined) {
        safeStorage.set('backgroundDownloads', String(updates.isEnabled))
      }
      return newState
    })
  }, [])

  useEffect(() => {
    setDownloadStateUpdater(publishDownloadState)
    return () => setDownloadStateUpdater(null)
  }, [publishDownloadState])

  const [contentUpdates, setContentUpdates] = useState({
    tracks: 0,
    shoutouts: 0
  })

  const publishContentUpdate = useCallback((contentType) => {
    setContentUpdates(prev => ({
      ...prev,
      [contentType]: prev[contentType] + 1
    }))
  }, [])

  const [toasts, setToasts] = useState([])
  const timeoutRefsToast = useRef({})

  const removeToast = useCallback((id) => {
    if (timeoutRefsToast.current[id]) {
      clearTimeout(timeoutRefsToast.current[id])
      delete timeoutRefsToast.current[id]
    }
    setToasts(prev => prev.filter(toast => toast.id !== id))
  }, [])

  const publishToast = useCallback((message, type = 'info', duration = 5000, position = 'top', replaceKey = null) => {
    const id = Date.now() + Math.random()
    const toast = { id, message, type, duration, position }

    setToasts(prev => {
      if (replaceKey) {
        const existing = prev.find(t => t.replaceKey === replaceKey)
        if (existing) {
          if (timeoutRefsToast.current[existing.id]) {
            clearTimeout(timeoutRefsToast.current[existing.id])
          }
          return prev.map(t => t.replaceKey === replaceKey
            ? { ...toast, replaceKey }
            : t
          )
        }
      }
      return [...prev, { ...toast, replaceKey }]
    })

    if (duration > 0) {
      timeoutRefsToast.current[id] = setTimeout(() => {
        removeToast(id)
      }, duration)
    }

    return id
  }, [removeToast])

  const toastSuccess = useCallback((message, duration, position = 'top', replaceKey = null) => {
    return publishToast(message, 'success', duration, position, replaceKey)
  }, [publishToast])

  const toastError = useCallback((message, duration, position = 'top', replaceKey = null) => {
    return publishToast(message, 'error', duration, position, replaceKey)
  }, [publishToast])

  const toastInfo = useCallback((message, duration, position = 'top', replaceKey = null) => {
    return publishToast(message, 'info', duration, position, replaceKey)
  }, [publishToast])

  const toastWarning = useCallback((message, duration, position = 'top', replaceKey = null) => {
    return publishToast(message, 'warning', duration, position, replaceKey)
  }, [publishToast])

  const [radioButtonInteraction, setRadioButtonInteraction] = useState({
    isHovered: false,
    isPressed: false,
    scale: 1
  })

  const initialButtonOpacity = initialRadioInput() === 'text' ? 0 : 1
  const [radioButtonOpacity, setRadioButtonOpacityState] = useState(initialButtonOpacity)
  const [radioButtonForegroundOpacity, setRadioButtonForegroundOpacityState] = useState(initialButtonOpacity)

  const radioButtonRef = useRef({
    opacity: initialButtonOpacity,
    foregroundOpacity: initialButtonOpacity,
    isHovered: false,
    isPressed: false,
    scale: 1
  })

  const setRadioButtonOpacity = useCallback((opacity) => {
    radioButtonRef.current.opacity = opacity
    setRadioButtonOpacityState(opacity)
  }, [])

  const setRadioButtonForegroundOpacity = useCallback((opacity) => {
    radioButtonRef.current.foregroundOpacity = opacity
    setRadioButtonForegroundOpacityState(opacity)
  }, [])

  const [interfaceState, setInterfaceState] = useState({
    isScrolling: false,
    scrollVelocity: 0,
    scrollPosition: 0,
    isFullscreenVisuals: false,
    showUIControls: false,
    currentMobilePanel: 2,
    catalogView: 'tracks',
    radioInput: initialRadioInput(),
    playerHeight: 80
  })

  const interfaceRef = useRef({
    isScrolling: false,
    scrollVelocity: 0,
    scrollPosition: 0,
    isFullscreenVisuals: false,
    showUIControls: false,
    currentMobilePanel: 2,
    catalogView: 'tracks',
    radioInput: initialRadioInput(),
    playerHeight: 80
  })

  const reportInterfaceState = useCallback((updates) => {
    Object.assign(interfaceRef.current, updates)

    if (window.__refCalls) {
      Object.keys(updates).forEach(key => window.__refCalls.keysThisSecond.add(key))
      window.__refCalls.count++
    }

    const merged = interfaceRef.current
    const isHidden = merged.isFullscreenVisuals || merged.isScrolling || merged.radioInput === 'text'
    const isRadioPanel = merged.currentMobilePanel === 2
    setRadioButtonOpacity(isHidden ? 0 : (isRadioPanel ? 1 : UI_FULLSCREEN.radioGlassOpacity))
    setRadioButtonForegroundOpacity(isHidden ? 0 : (isRadioPanel ? 1 : 0))

    setInterfaceState(prev => {
      const changed = Object.keys(updates).some(key => prev[key] !== updates[key])
      return changed ? { ...prev, ...updates } : prev
    })
  }, [setRadioButtonOpacity, setRadioButtonForegroundOpacity])

  const toggleCatalogView = useCallback(() => {
    reportInterfaceState({ catalogView: interfaceRef.current.catalogView === 'tracks' ? 'shoutouts' : 'tracks' })
  }, [reportInterfaceState])

  const toggleRadioInput = useCallback(() => {
    const radioInput = interfaceRef.current.radioInput === 'text' ? 'voice' : 'text'
    safeStorage.set(RADIO_INPUT_KEY, radioInput)
    reportInterfaceState({ radioInput })
  }, [reportInterfaceState])

  const setMobilePanel = useCallback((index) => {
    reportInterfaceState({ currentMobilePanel: index })
  }, [reportInterfaceState])

  const shaderPanelRegionsRef = useRef([])
  const shaderPanelOpacitiesRef = useRef([])
  const shaderRadioButtonPosRef = useRef({ x: 0.5, y: 0.5, radiusX: 0, radiusY: 0 })

  const visualState = useMemo(() => {
    if (engineState.isMicRecording) return 1
    if (engineState.isAIProcessing) return 4
    if (engineState.isDJSpeaking) return 3
    if (engineState.isMusicPlaying) return 2
    if (engineState.isMusicPaused) return 5
    return 0
  }, [engineState])

  const onAirKind = engineState.talkBreak ? (engineState.talkBreak.kind || 'radio') : null

  const onAirColor = useMemo(() => (
    onAirKind ? hexToRgb(getOnAirSegment({ kind: onAirKind }).color) : null
  ), [onAirKind])

  const visualColorData = useMemo(() => {
    let resolvedColor = STATE_COLORS[0]
    if (visualState === 3) {
      resolvedColor = speakerColorRef.current
    } else if (onAirColor && ON_AIR_TINTED_STATES.has(visualState)) {
      resolvedColor = onAirColor
    } else if (STATE_COLORS[visualState]) {
      resolvedColor = STATE_COLORS[visualState]
    }

    return {
      stateInt: visualState,
      currentVisualColor: resolvedColor,
      onAirColor
    }
  }, [visualState, onAirColor])

  const radioProgressData = useMemo(() => {
    return {
      stateInt: visualState,
      currentVisualColor: visualColorData.currentVisualColor,
      onAirColor
    }
  }, [visualState, visualColorData, onAirColor])

  const reportEngineStatus = useCallback((updates) => {
    const now = Date.now()

    if (updates.djFftData !== undefined) djFftDataRef.current = updates.djFftData
    if (updates.micFftData !== undefined) micFftDataRef.current = updates.micFftData
    if (updates.shoutoutFftData !== undefined) shoutoutFftDataRef.current = updates.shoutoutFftData
    if (updates.speakerColor !== undefined) speakerColorRef.current = updates.speakerColor
    if (updates.progress_ms !== undefined) {
      engineRef.current.progress_ms = updates.progress_ms
      lastProgressUpdateTimeRef.current = now
    }

    const stateUpdates = {}
    if (updates.isMicRecording !== undefined) stateUpdates.isMicRecording = updates.isMicRecording
    if (updates.isDJSpeaking !== undefined) stateUpdates.isDJSpeaking = updates.isDJSpeaking
    if (updates.isShoutoutPlaying !== undefined) stateUpdates.isShoutoutPlaying = updates.isShoutoutPlaying
    if (updates.isMusicPlaying !== undefined) stateUpdates.isMusicPlaying = updates.isMusicPlaying
    if (updates.isMusicPaused !== undefined) stateUpdates.isMusicPaused = updates.isMusicPaused
    if (updates.isAIProcessing !== undefined) stateUpdates.isAIProcessing = updates.isAIProcessing
    if (updates.isActiveDevice !== undefined) stateUpdates.isActiveDevice = updates.isActiveDevice
    if (updates.activeDeviceId !== undefined) stateUpdates.activeDeviceId = updates.activeDeviceId
    if (updates.activeDeviceOnline !== undefined) stateUpdates.activeDeviceOnline = updates.activeDeviceOnline
    if (updates.isCrossfading !== undefined) stateUpdates.isCrossfading = updates.isCrossfading
    if (updates.is_playing !== undefined) stateUpdates.is_playing = updates.is_playing
    if (updates.currentTrack !== undefined) stateUpdates.currentTrack = updates.currentTrack
    if (updates.queue !== undefined) stateUpdates.queue = updates.queue
    if (updates.currentIndex !== undefined) stateUpdates.currentIndex = updates.currentIndex
    if (updates.talkBreak !== undefined) stateUpdates.talkBreak = updates.talkBreak
    if (updates.audioNeedsTap !== undefined) stateUpdates.audioNeedsTap = updates.audioNeedsTap
    if (updates.djActivity !== undefined) stateUpdates.djActivity = updates.djActivity

    const keys = Object.keys(stateUpdates)
    if (keys.length > 0) {
      setEngineState(prev => {
        for (let i = 0; i < keys.length; i++) {
          if (prev[keys[i]] !== stateUpdates[keys[i]]) return { ...prev, ...stateUpdates }
        }
        return prev
      })
    }
  }, [])

  const updateRadioButtonInteraction = useCallback((interaction) => {
    Object.assign(radioButtonRef.current, interaction)
    setRadioButtonInteraction(prev => ({ ...prev, ...interaction }))
  }, [])

  const updateRadioButtonOpacity = setRadioButtonOpacity

  const updateRadioButtonForegroundOpacity = setRadioButtonForegroundOpacity

  const notifyArtwork = useCallback((kind, trackId) => {
    const listeners = artworkListenersRef.current.get(`${kind}:${trackId}`)
    if (listeners) listeners.forEach(listener => listener())
  }, [])

  const subscribeArtwork = useCallback((kind, trackId, listener) => {
    const key = `${kind}:${trackId}`
    const registry = artworkListenersRef.current
    let listeners = registry.get(key)
    if (!listeners) {
      listeners = new Set()
      registry.set(key, listeners)
    }
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
      if (listeners.size === 0 && registry.get(key) === listeners) {
        registry.delete(key)
      }
    }
  }, [])

  const getArtworkUrl = useCallback((trackId, hasArtwork = true) => {
    if (!trackId || hasArtwork === false) return null
    if (artworkUrlsRef.current.has(trackId)) return artworkUrlsRef.current.get(trackId)
    const memoryCached = artworkCache.getMemory(trackId)
    if (memoryCached) {
      artworkUrlsRef.current.set(trackId, memoryCached)
      return memoryCached
    }
    return generatePlaceholderDataURL(trackId)
  }, [])

  const getEnrichedArtworkUrl = useCallback((trackId, hasArtwork = true) => {
    if (!trackId || hasArtwork === false) return null
    if (enrichedArtworkUrlsRef.current.has(trackId)) return enrichedArtworkUrlsRef.current.get(trackId)
    const memoryCached = enrichedArtworkCache.getMemory(trackId)
    if (memoryCached) {
      enrichedArtworkUrlsRef.current.set(trackId, memoryCached)
      return memoryCached
    }
    return generatePlaceholderDataURL(trackId)
  }, [])


  const preloadArtwork = useCallback(async (trackId, hasArtwork = true) => {
    if (!trackId || hasArtwork === false) return
    if (artworkUrlsRef.current.has(trackId)) return
    if (loadingTracksRef.current.has(trackId)) return
    loadingTracksRef.current.add(trackId)
    try {
      const url = await artworkCache.getMedia(trackId)
      if (url) {
        artworkUrlsRef.current.set(trackId, url)
        notifyArtwork('artwork', trackId)
      }
    } catch (error) {
      logger.error(`[UIState] Failed to load artwork:`, error)
    } finally {
      loadingTracksRef.current.delete(trackId)
    }
  }, [notifyArtwork])

  const getThumbArtworkUrl = useCallback((trackId, hasArtwork = true) => {
    if (!trackId || hasArtwork === false) return null
    if (artworkPrefetcher.isReady(trackId)) return artworkThumbCache.peekMemory(trackId)
    return artworkUrlsRef.current.get(trackId) || artworkCache.peekMemory(trackId) || generatePlaceholderDataURL(trackId)
  }, [])

  const preloadThumbArtwork = useCallback((trackId, hasArtwork = true) => {
    if (!trackId || hasArtwork === false) return
    artworkPrefetcher.request(trackId)
  }, [])

  const preloadArtworkBatch = useCallback(async (trackIds) => {
    const promises = trackIds.filter(id => id && !artworkUrlsRef.current.has(id)).map(id => preloadArtwork(id, true))
    await Promise.all(promises)
  }, [preloadArtwork])

  const clearArtwork = useCallback((trackId) => {
    artworkCache.releaseMemory(trackId)
    artworkUrlsRef.current.delete(trackId)
    notifyArtwork('artwork', trackId)
  }, [notifyArtwork])

  const preloadEnrichedArtwork = useCallback(async (trackId, hasArtwork = true) => {
    if (!trackId || hasArtwork === false) return
    if (enrichedArtworkUrlsRef.current.has(trackId)) return
    if (loadingEnrichedRef.current.has(trackId)) return
    loadingEnrichedRef.current.add(trackId)
    try {
      const url = await enrichedArtworkCache.getMedia(trackId)
      if (url) {
        enrichedArtworkUrlsRef.current.set(trackId, url)
        notifyArtwork('enriched', trackId)
      }
    } catch (error) {
      logger.error(`[UIState] Failed to load enriched artwork:`, error)
    } finally {
      loadingEnrichedRef.current.delete(trackId)
    }
  }, [notifyArtwork])

  const clearEnrichedArtwork = useCallback((trackId) => {
    enrichedArtworkCache.releaseMemory(trackId)
    enrichedArtworkUrlsRef.current.delete(trackId)
    notifyArtwork('enriched', trackId)
  }, [notifyArtwork])

  useEffect(() => {
    const listeners = artworkListenersRef.current
    const pinned = pinnedArtworkIdsRef.current
    const bind = (kind, cache, urlsRef, preload) => {
      cache.setPinnedChecker(id => pinned.has(id) || listeners.has(`${kind}:${id}`))
      const unsubscribe = cache.subscribe((id) => {
        const url = cache.peekMemory(id)
        if (url) {
          urlsRef.current.set(id, url)
        } else {
          urlsRef.current.delete(id)
          if (listeners.has(`${kind}:${id}`)) void preload(id, true)
        }
        notifyArtwork(kind, id)
      })
      return () => {
        unsubscribe()
        cache.setPinnedChecker(null)
      }
    }
    const unbindArtwork = bind('artwork', artworkCache, artworkUrlsRef, preloadArtwork)
    const unbindEnriched = bind('enriched', enrichedArtworkCache, enrichedArtworkUrlsRef, preloadEnrichedArtwork)
    artworkThumbCache.setPinnedChecker(id => listeners.has(`thumb:${id}`) || artworkPrefetcher.isDemanded(id))
    artworkPrefetcher.setHeldChecker(id => listeners.has(`thumb:${id}`))
    const notifyThumb = (id) => notifyArtwork('thumb', id)
    const unsubscribeThumbCache = artworkThumbCache.subscribe(notifyThumb)
    const unsubscribeThumbReady = artworkPrefetcher.subscribe(notifyThumb)
    return () => {
      unbindArtwork()
      unbindEnriched()
      unsubscribeThumbCache()
      unsubscribeThumbReady()
      artworkThumbCache.setPinnedChecker(null)
      artworkPrefetcher.setHeldChecker(null)
    }
  }, [notifyArtwork, preloadArtwork, preloadEnrichedArtwork])

  const fetchVideoClips = useCallback(async (trackId) => {
    if (!trackId) return
    if (videoClipsMapRef.current.has(trackId)) {
      logger.info(`[UIState] 🎬 Already have clips for ${trackId.slice(0, 8)}, skipping fetch`)
      return
    }
    if (loadingVideoClipsRef.current.has(trackId)) {
      logger.info(`[UIState] 🎬 Already loading ${trackId.slice(0, 8)}, skipping`)
      return
    }
    loadingVideoClipsRef.current.add(trackId)
    logger.info(`[UIState] 🎬 Fetching video clips for ${trackId.slice(0, 8)}...`)
    try {
      const data = await api.getVideoClips(trackId)
      logger.info(`[UIState] 🎬 API data:`, data.reason || `${data.clips?.length || 0} clips`, data.keywords?.slice(0, 2) || 'no keywords')
      if (data.clips && data.clips.length > 0) {
        const clips = data.clips.map(clip => ({
          ...clip,
          url: `${window.location.origin}${clip.url}`
        }))
        videoClipsMapRef.current.set(trackId, clips)
        setVideoClipsByTrack(prev => ({ ...prev, [trackId]: clips }))
        logger.info(`[UIState] ✅ Loaded ${clips.length} video clips for track ${trackId.slice(0, 8)}`)
      } else {
        videoClipsMapRef.current.set(trackId, EMPTY_CLIPS)
        setVideoClipsByTrack(prev => ({ ...prev, [trackId]: EMPTY_CLIPS }))
        logger.info(`[UIState] ⚠️ No video clips for ${trackId.slice(0, 8)}: ${data.reason || 'empty'}`)
      }
    } catch (error) {
      logger.error(`[UIState] ❌ Failed to fetch video clips:`, error)
    } finally {
      loadingVideoClipsRef.current.delete(trackId)
    }
  }, [])

  useEffect(() => {
    const { currentTrack, queue, currentIndex } = engineState
    if (!currentTrack) return

    // Preload current track artwork (standard + enriched)
    preloadArtwork(currentTrack.id, currentTrack.has_artwork)
    preloadEnrichedArtwork(currentTrack.id, currentTrack.has_artwork)

    // Preload NEXT track artwork for seamless transitions
    const nextTrack = queue?.[currentIndex + 1]
    const pinned = pinnedArtworkIdsRef.current
    pinned.clear()
    pinned.add(currentTrack.id)
    if (nextTrack) pinned.add(nextTrack.id)
    if (nextTrack) {
      preloadArtwork(nextTrack.id, nextTrack.has_artwork)
      preloadEnrichedArtwork(nextTrack.id, nextTrack.has_artwork)
    }

    if (settingsState.videoClipsEnabled) {
      fetchVideoClips(currentTrack.id)
    }
  }, [engineState.currentTrack?.id, engineState.queue, engineState.currentIndex, preloadArtwork, preloadEnrichedArtwork, fetchVideoClips, settingsState.videoClipsEnabled])

  useEffect(() => {
    logger.info(`[UIState] 🎬 Video clips setting: ${settingsState.videoClipsEnabled ? 'ENABLED' : 'DISABLED'}`)
  }, [settingsState.videoClipsEnabled])

  const setTrackData = useCallback((features, lyrics) => {
    setAudioFeatures(features)
    setLyricTimestamps(lyrics)
  }, [])

  const updateShaderRegions = useCallback((regions, opacities) => {
    shaderPanelRegionsRef.current = regions
    shaderPanelOpacitiesRef.current = opacities
  }, [])

  const updateShaderRadioButtonPos = useCallback((pos) => {
    shaderRadioButtonPosRef.current = pos
  }, [])

  const openShoutoutModal = useCallback((shoutout) => {
    pauseSceneRendering(MODAL_OPEN_PAUSE_MS)
    startTransition(() => setShoutoutModalState({ isOpen: true, shoutout }))
  }, [])

  const closeShoutoutModal = useCallback(() => {
    setShoutoutModalState({ isOpen: false, shoutout: null })
  }, [])

  const openUploadModal = useCallback(() => {
    pauseSceneRendering(MODAL_OPEN_PAUSE_MS)
    startTransition(() => setUploadModalOpen(true))
  }, [])

  const closeUploadModal = useCallback(() => {
    setUploadModalOpen(false)
  }, [])

  const openUsageModal = useCallback(() => {
    pauseSceneRendering(MODAL_OPEN_PAUSE_MS)
    startTransition(() => setUsageModalOpen(true))
  }, [])

  const closeUsageModal = useCallback(() => {
    setUsageModalOpen(false)
  }, [])

  const setVideoPreviewPlaying = useCallback((isPlaying) => {
    setEngineState(prev => ({ ...prev, isVideoPreviewPlaying: isPlaying }))
  }, [])

  const artworkStore = useMemo(() => ({
    subscribeArtwork,
    getArtworkUrl,
    getEnrichedArtworkUrl,
    getThumbArtworkUrl,
    preloadArtwork,
    preloadEnrichedArtwork,
    preloadThumbArtwork,
  }), [subscribeArtwork, getArtworkUrl, getEnrichedArtworkUrl, getThumbArtworkUrl, preloadArtwork, preloadEnrichedArtwork, preloadThumbArtwork])

  const value = useMemo(() => ({
    tiltEnabled,
    tiltNeedsPermission: TILT_NEEDS_PERMISSION,
    enableTiltEffects,
    reportEngineStatus,
    visualState,
    radioProgressData,
    visualColorData,
    engineState,
    engineRef,
    lastProgressUpdateTimeRef,

    djFftDataRef,
    micFftDataRef,
    shoutoutFftDataRef,
    speakerColorRef,

    setMixerRef,

    audioState,
    publishAudioState,

    queueState,
    publishQueueState,

    authState,
    publishAuthState,

    radioState,
    publishRadioState,

    downloadState,
    publishDownloadState,

    settingsState,
    publishSettings,

    contentUpdates,
    publishContentUpdate,

    toasts,
    publishToast,
    removeToast,
    toastSuccess,
    toastError,
    toastInfo,
    toastWarning,

    gyroscopeRef,
    mouseRef,

    radioButtonRef,
    updateRadioButtonInteraction,
    updateRadioButtonOpacity,
    updateRadioButtonForegroundOpacity,
    reportInterfaceState,
    interfaceState,
    interfaceRef,
    toggleCatalogView,
    toggleRadioInput,
    setMobilePanel,

    shaderPanelRegions: shaderPanelRegionsRef,
    shaderPanelOpacities: shaderPanelOpacitiesRef,
    shaderRadioButtonPos: shaderRadioButtonPosRef,
    updateShaderRegions,
    updateShaderRadioButtonPos,

    subscribeArtwork,
    getArtworkUrl,
    preloadArtwork,
    preloadArtworkBatch,
    clearArtwork,
    getEnrichedArtworkUrl,
    preloadEnrichedArtwork,
    clearEnrichedArtwork,
    videoClipsMapRef,
    videoClipsByTrack,
    fetchVideoClips,
    audioFeatures,
    lyricTimestamps,
    setTrackData,

    shoutoutModalState,
    openShoutoutModal,
    closeShoutoutModal,

    uploadModalOpen,
    openUploadModal,
    closeUploadModal,

    usageModalOpen,
    openUsageModal,
    closeUsageModal,

    isOfflineRendering,
    setIsOfflineRendering,

    isScreenVisible,

    setVideoPreviewPlaying,
  }), [
    tiltEnabled, enableTiltEffects,
    reportEngineStatus, visualState, radioProgressData, visualColorData, engineState,
    setMixerRef, audioState, publishAudioState, queueState, publishQueueState,
    authState, publishAuthState, radioState, publishRadioState, downloadState, publishDownloadState,
    settingsState, publishSettings, contentUpdates, publishContentUpdate,
    toasts, publishToast, removeToast, toastSuccess, toastError, toastInfo, toastWarning,
    updateRadioButtonInteraction, updateRadioButtonOpacity, updateRadioButtonForegroundOpacity,
    reportInterfaceState, interfaceState, toggleCatalogView, toggleRadioInput, setMobilePanel, updateShaderRegions, updateShaderRadioButtonPos,
    subscribeArtwork, getArtworkUrl, preloadArtwork, preloadArtworkBatch, clearArtwork,
    getEnrichedArtworkUrl, preloadEnrichedArtwork, clearEnrichedArtwork,
    videoClipsByTrack, fetchVideoClips, audioFeatures, lyricTimestamps, setTrackData,
    shoutoutModalState, openShoutoutModal, closeShoutoutModal,
    uploadModalOpen, openUploadModal, closeUploadModal,
    usageModalOpen, openUsageModal, closeUsageModal,
    isOfflineRendering, isScreenVisible, setVideoPreviewPlaying,
  ])

  const storeRef = useRef(null)
  if (!storeRef.current) storeRef.current = createUIStore()
  const store = storeRef.current
  store.value = value

  useLayoutEffect(() => {
    store.notify()
  }, [store, value])

  const radioButtonValue = useMemo(() => ({
    radioButtonInteraction,
    radioButtonOpacity,
    radioButtonForegroundOpacity,
  }), [radioButtonInteraction, radioButtonOpacity, radioButtonForegroundOpacity])

  const actionsValue = useMemo(() => ({
    publishToast,
    removeToast,
    toastSuccess,
    toastError,
    toastInfo,
    toastWarning,
  }), [publishToast, removeToast, toastSuccess, toastError, toastInfo, toastWarning])

  return (
    <UIStoreContext.Provider value={store}>
    <UIStateContext.Provider value={value}>
      <UIActionsContext.Provider value={actionsValue}>
        <RadioStateContext.Provider value={radioState}>
          <ArtworkStoreContext.Provider value={artworkStore}>
            <RadioButtonContext.Provider value={radioButtonValue}>
              {children}
            </RadioButtonContext.Provider>
          </ArtworkStoreContext.Provider>
        </RadioStateContext.Provider>
      </UIActionsContext.Provider>
    </UIStateContext.Provider>
    </UIStoreContext.Provider>
  )
}

export function useUIState() {
  const context = useContext(UIStateContext)
  if (!context) throw new Error('useUIState must be used within UIStateProvider')
  return context
}

export function useUISelector(selector, isEqual = shallowEqual) {
  const store = useContext(UIStoreContext)
  if (!store) throw new Error('useUISelector must be used within UIStateProvider')
  const selectionRef = useRef(NO_SELECTION)

  const getSnapshot = () => {
    const next = selector(store.get())
    const previous = selectionRef.current
    if (previous !== NO_SELECTION && isEqual(previous, next)) return previous
    selectionRef.current = next
    return next
  }

  return useSyncExternalStore(store.subscribe, getSnapshot)
}

export function useUIStateGetter() {
  const store = useContext(UIStoreContext)
  if (!store) throw new Error('useUIStateGetter must be used within UIStateProvider')
  return store.get
}

export function useUIActions() {
  const context = useContext(UIActionsContext)
  if (!context) throw new Error('useUIActions must be used within UIStateProvider')
  return context
}

export function useRadioState() {
  const context = useContext(RadioStateContext)
  if (!context) throw new Error('useRadioState must be used within UIStateProvider')
  return context
}

function useArtworkStore() {
  const context = useContext(ArtworkStoreContext)
  if (!context) throw new Error('useArtwork must be used within UIStateProvider')
  return context
}

function useArtworkSubscription(kind, trackId, hasArtwork, getUrl, preload) {
  const { subscribeArtwork } = useArtworkStore()
  const enabled = !!trackId && hasArtwork !== false

  const subscribe = useCallback(
    (onChange) => (enabled ? subscribeArtwork(kind, trackId, onChange) : noopUnsubscribe),
    [enabled, kind, trackId, subscribeArtwork]
  )
  const getSnapshot = useCallback(
    () => (enabled ? getUrl(trackId, hasArtwork) : null),
    [enabled, trackId, hasArtwork, getUrl]
  )

  const url = useSyncExternalStore(subscribe, getSnapshot)

  useEffect(() => {
    if (enabled) void preload(trackId, hasArtwork)
  }, [enabled, trackId, hasArtwork, preload])

  return url
}

export function useArtwork(trackId, hasArtwork = true) {
  const { getArtworkUrl, preloadArtwork } = useArtworkStore()
  return useArtworkSubscription('artwork', trackId, hasArtwork, getArtworkUrl, preloadArtwork)
}

export function useArtworkThumb(trackId, hasArtwork = true) {
  const { getThumbArtworkUrl, preloadThumbArtwork } = useArtworkStore()
  return useArtworkSubscription('thumb', trackId, hasArtwork, getThumbArtworkUrl, preloadThumbArtwork)
}

export function useEnrichedArtwork(trackId, hasArtwork = true) {
  const { getEnrichedArtworkUrl, preloadEnrichedArtwork } = useArtworkStore()
  return useArtworkSubscription('enriched', trackId, hasArtwork, getEnrichedArtworkUrl, preloadEnrichedArtwork)
}

export function useVideoClips(trackId) {
  const {
    videoClipsByTrack,
    fetchVideoClips,
    settingsState,
  } = useUISelector(state => ({
    videoClipsByTrack: state.videoClipsByTrack,
    fetchVideoClips: state.fetchVideoClips,
    settingsState: state.settingsState,
  }))
  const enabled = settingsState.videoClipsEnabled
  const clips = enabled && trackId ? videoClipsByTrack[trackId] : EMPTY_CLIPS

  useEffect(() => {
    if (enabled && trackId && clips === undefined) void fetchVideoClips(trackId)
  }, [enabled, trackId, clips, fetchVideoClips])

  return clips || EMPTY_CLIPS
}

export function useRadioUI() {
  const radioButton = useContext(RadioButtonContext)
  if (!radioButton) throw new Error('useRadioUI must be used within UIStateProvider')
  const { radioButtonOpacity, radioButtonForegroundOpacity, radioButtonInteraction } = radioButton
  const {
    reportEngineStatus,
    visualState,
    radioProgressData,
    visualColorData,
    updateRadioButtonOpacity,
    updateRadioButtonForegroundOpacity,
    updateRadioButtonInteraction,
    reportInterfaceState,
    engineState,
    engineRef,
    lastProgressUpdateTimeRef,
    djFftDataRef,
    micFftDataRef,
    shoutoutFftDataRef,
    speakerColorRef,
    interfaceRef,
  } = useUISelector(state => ({
    reportEngineStatus: state.reportEngineStatus,
    visualState: state.visualState,
    radioProgressData: state.radioProgressData,
    visualColorData: state.visualColorData,
    updateRadioButtonOpacity: state.updateRadioButtonOpacity,
    updateRadioButtonForegroundOpacity: state.updateRadioButtonForegroundOpacity,
    updateRadioButtonInteraction: state.updateRadioButtonInteraction,
    reportInterfaceState: state.reportInterfaceState,
    engineState: state.engineState,
    engineRef: state.engineRef,
    lastProgressUpdateTimeRef: state.lastProgressUpdateTimeRef,
    djFftDataRef: state.djFftDataRef,
    micFftDataRef: state.micFftDataRef,
    shoutoutFftDataRef: state.shoutoutFftDataRef,
    speakerColorRef: state.speakerColorRef,
    interfaceRef: state.interfaceRef,
  }))

  return {
    reportEngineStatus,
    visualState,
    buttonOpacity: radioButtonOpacity,
    buttonForegroundOpacity: radioButtonForegroundOpacity,
    buttonInteraction: radioButtonInteraction,
    progressData: radioProgressData,
    visualColorData,
    updateButtonOpacity: updateRadioButtonOpacity,
    updateButtonForegroundOpacity: updateRadioButtonForegroundOpacity,
    updateButtonInteraction: updateRadioButtonInteraction,
    reportInterfaceState,
    engineState,
    engineRef,
    lastProgressUpdateTimeRef,
    djFftDataRef,
    micFftDataRef,
    shoutoutFftDataRef,
    speakerColorRef,
    interfaceRef
  }
}