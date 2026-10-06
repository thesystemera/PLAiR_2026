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
 * - showNotice() / hideNotice() - The one notice channel (toasts are passing notices)
 *
 * SUBSCRIBERS (How to read state):
 * - useUISelector(state => slice) - Subscribe to ONE slice; re-renders only when that slice changes
 *   (shallow compare). There is no whole-state hook.
 * - useUIStateGetter() - Read the latest state inside a handler without subscribing
 * - useRadioButton() - The radio button's opacity and interaction (its own context)
 *
 * ENGINES publish data → UIState derives visual state → VIEWS subscribe and render
 *
 * NO MIDDLEMEN. NO PROP DRILLING. Just clean pub/sub.
 *
 * See: docs/ARCHITECTURE_SSOT.md for detailed documentation
 */

import { createContext, startTransition, useContext, useState, useCallback, useMemo, useRef, useEffect, useLayoutEffect, useSyncExternalStore } from 'react'
import { FULL_PACK_SIZE, PACK_SIZES, SCENE_PACK_SIZE, packCache } from '../lib/mediaCache'

const CURRENT_TRACK_PACK_SIZES = [PACK_SIZES[0], SCENE_PACK_SIZE, FULL_PACK_SIZE]
import { AudioInteractionManager } from '../lib/audioInteractionManager'
import { logger } from '../lib/logger'
import { safeStorage } from '../lib/safeStorage'
import { UI_FULLSCREEN, getOnAirSegment } from '../lib/themeManager'
import { api } from '../lib/api'
import { MODAL_OPEN_PAUSE_MS, pauseSceneRendering } from '../lib/renderPause'
import { loadLocalSettings, notifySettingsChanged, pickValidSettings, saveLocalSettings } from '../lib/settings'

const INITIAL_SETTINGS = loadLocalSettings()

const NOTICE_DURATION_MS = { success: 2000, info: 2500, warning: 3500, error: 4000, neutral: 2500 }
const MAX_PASSING_NOTICES = 3
const HOLD_AVERAGE_MS = 5000
const FULL_TILT_DEGREES = 20
const TILT_INPUT_SMOOTHING_MS = 150
const TILT_OUTPUT_SMOOTHING_MS = 60
const HALF_DEGREE = Math.PI / 360

function orientationQuaternion(alpha, beta, gamma) {
  const ca = Math.cos(alpha * HALF_DEGREE), sa = Math.sin(alpha * HALF_DEGREE)
  const cb = Math.cos(beta * HALF_DEGREE), sb = Math.sin(beta * HALF_DEGREE)
  const cg = Math.cos(gamma * HALF_DEGREE), sg = Math.sin(gamma * HALF_DEGREE)
  return {
    w: ca * cb * cg - sa * sb * sg,
    x: ca * sb * cg - sa * cb * sg,
    y: ca * cb * sg + sa * sb * cg,
    z: sa * cb * cg + ca * sb * sg,
  }
}

function tiltFrom(reference, pose) {
  let w = reference.w * pose.w + reference.x * pose.x + reference.y * pose.y + reference.z * pose.z
  let x = reference.w * pose.x - reference.x * pose.w - reference.y * pose.z + reference.z * pose.y
  let y = reference.w * pose.y + reference.x * pose.z - reference.y * pose.w - reference.z * pose.x
  const z = reference.w * pose.z - reference.x * pose.y + reference.y * pose.x - reference.z * pose.w
  if (w < 0) {
    w = -w
    x = -x
    y = -y
  }
  const sine = Math.hypot(x, y, z)
  const scale = sine > 1e-6 ? (2 * Math.atan2(sine, w) / sine) * (180 / Math.PI) : 360 / Math.PI
  return { x: x * scale, y: y * scale }
}

function followPose(reference, pose, amount) {
  const sign = reference.w * pose.w + reference.x * pose.x + reference.y * pose.y + reference.z * pose.z < 0 ? -1 : 1
  reference.w += (sign * pose.w - reference.w) * amount
  reference.x += (sign * pose.x - reference.x) * amount
  reference.y += (sign * pose.y - reference.y) * amount
  reference.z += (sign * pose.z - reference.z) * amount
  const length = Math.hypot(reference.w, reference.x, reference.y, reference.z) || 1
  reference.w /= length
  reference.x /= length
  reference.y /= length
  reference.z /= length
}

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
const RadioButtonContext = createContext(null)
const UIActionsContext = createContext(null)
const RadioStateContext = createContext(null)
const UIStoreContext = createContext(null)
const EMPTY_CLIPS = []
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

function shallowEqual(a, b) {
  if (Object.is(a, b)) return true
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object') return false
  const keys = Object.keys(a)
  if (keys.length !== Object.keys(b).length) return false
  for (const key of keys) {
    if (!Object.prototype.hasOwnProperty.call(b, key) || !Object.is(a[key], b[key])) return false
  }
  return true
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
  },
  authState: {
    isAuthenticated: (() => { try { return !!localStorage.getItem('cached_user') } catch { return false } })(),
    user: null,
  },
  settingsState: { ...INITIAL_SETTINGS },
}

let updateDownloadStateCallback = null

function setDownloadStateUpdater(callback) {
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
  const pinnedArtworkIdsRef = useRef(new Set())
  const [audioFeatures, setAudioFeatures] = useState(null)
  const [lyricTimestamps, setLyricTimestamps] = useState(null)
  const [trackDataFor, setTrackDataFor] = useState(null)

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
    rawTargetX: 0,
    rawTargetY: 0,
    smoothTargetX: 0,
    smoothTargetY: 0
  })

  const holdPose = useRef({ reference: null, pose: null })

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
    crossfadeMs: 0,
    is_playing: false,
    currentTrack: null,
    queue: [],
    currentIndex: 0,
    talkBreak: null,
    audioNeedsTap: false,
  })

  const engineRef = useRef({
    progress_ms: 0
  })

  const lastProgressUpdateTimeRef = useRef(Date.now())

  const [shoutoutModalState, setShoutoutModalState] = useState({
    isOpen: false,
    shoutout: null
  })

  const [reviewModalState, setReviewModalState] = useState({
    isOpen: false,
    trackId: null,
    track: null
  })

  const [uploadModalOpen, setUploadModalOpen] = useState(false)
  const [uploadEditTrackId, setUploadEditTrackId] = useState(null)
  const [uploadWatchId, setUploadWatchId] = useState(null)
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

  const tiltControlsRef = useRef(null)

  const requestMotionAccess = useCallback(
    () => tiltControlsRef.current?.request() ?? Promise.resolve(!TILT_NEEDS_PERMISSION),
    []
  )

  useEffect(() => {
    let disposed = false
    let cancelGesture = null

    const handleOrientation = (event) => {
      const { beta, gamma } = event
      if (beta === null || gamma === null) return
      const hold = holdPose.current
      hold.pose = orientationQuaternion(event.alpha ?? 0, beta, gamma)
      if (!hold.reference) hold.reference = { ...hold.pose }
      physicsKickRef.current?.()
    }

    const handleMouseMove = (e) => {
      mouseRef.current.parallaxX = (e.clientX / window.innerWidth - 0.5) * 2
      mouseRef.current.parallaxY = (e.clientY / window.innerHeight - 0.5) * 2
    }

    window.addEventListener('deviceorientation', handleOrientation, { passive: true })
    if (TILT_NEEDS_PERMISSION) {
      let granted = false
      const request = () => granted ? Promise.resolve(true) : DeviceOrientationEvent.requestPermission()
        .then((permission) => {
          granted = permission === 'granted'
          if (granted && !disposed) {
            window.addEventListener('deviceorientation', handleOrientation, { passive: true })
          }
          return granted
        })
        .catch((error) => {
          logger.warn('Motion permission not granted', error)
          return false
        })
      tiltControlsRef.current = { request }
      cancelGesture = AudioInteractionManager.onUserGesture(() => {
        if (uiState.settingsState.litArtwork !== false) void request()
      })
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
    let lastFrameAt = 0
    const IDLE_TIMEOUT = 2000

    const handleInput = () => {
      lastInputTime = Date.now()
      if (!animationFrameId) {
        lastFrameAt = 0
        animationFrameId = requestAnimationFrame(loop)
      }
    }

    const loop = (timestamp) => {
      const p = physicsState.current
      const dt = lastFrameAt ? Math.min(100, timestamp - lastFrameAt) : 1000 / 60
      lastFrameAt = timestamp
      const hold = holdPose.current
      if (hold.pose) {
        followPose(hold.reference, hold.pose, 1 - Math.exp(-dt / HOLD_AVERAGE_MS))
        const tilt = tiltFrom(hold.reference, hold.pose)
        const turn = (window.screen?.orientation?.angle || 0) * Math.PI / 180
        const aboutUp = tilt.x * Math.sin(turn) + tilt.y * Math.cos(turn)
        const aboutRight = tilt.x * Math.cos(turn) - tilt.y * Math.sin(turn)
        p.rawTargetX = Math.max(-1, Math.min(1, -aboutUp / FULL_TILT_DEGREES))
        p.rawTargetY = Math.max(-1, Math.min(1, -aboutRight / FULL_TILT_DEGREES))
      }
      const input = 1 - Math.exp(-dt / TILT_INPUT_SMOOTHING_MS)
      const output = 1 - Math.exp(-dt / TILT_OUTPUT_SMOOTHING_MS)

      p.smoothTargetX += (p.rawTargetX - p.smoothTargetX) * input
      p.smoothTargetY += (p.rawTargetY - p.smoothTargetY) * input
      const stepX = (p.smoothTargetX - p.x) * output
      const stepY = (p.smoothTargetY - p.y) * output
      p.x += stepX
      p.y += stepY

      gyroscopeRef.current.parallaxX = p.x
      gyroscopeRef.current.parallaxY = p.y

      window.__rafDebug?.sources && (window.__rafDebug.sources['UIState-physics'] = (window.__rafDebug.sources['UIState-physics'] || 0) + 1)

      const isMoving = Math.abs(stepX) > 0.0005 || Math.abs(stepY) > 0.0005 || Math.abs(p.x) > 0.002 || Math.abs(p.y) > 0.002
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

  const { isMicRecording, isDJSpeaking, isShoutoutPlaying, isVideoPreviewPlaying } = engineState

  useEffect(() => {
    const mixer = mixerRefInternal.current?.current
    if (!mixer) return

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
  }, [isMicRecording, isDJSpeaking, isShoutoutPlaying, isVideoPreviewPlaying])

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
  })

  const [settingsState, setSettingsState] = useState(INITIAL_SETTINGS)

  const publishSettings = useCallback((updates, { fromServer = false } = {}) => {
    const current = uiState.settingsState
    const changes = Object.fromEntries(Object.entries(pickValidSettings(updates)).filter(([key, value]) => current[key] !== value))
    if (!Object.keys(changes).length) return
    Object.assign(current, changes)
    saveLocalSettings(current)
    setSettingsState(prev => ({ ...prev, ...changes }))
    if (!fromServer) notifySettingsChanged(changes)
  }, [])

  const publishDownloadState = useCallback((updates) => {
    setDownloadState(prev => {
      const newState = { ...prev, ...updates }
      Object.assign(uiState.downloadState, newState)
      return newState
    })
  }, [])

  useEffect(() => {
    setDownloadStateUpdater(publishDownloadState)
    return () => setDownloadStateUpdater(null)
  }, [publishDownloadState])

  const [contentUpdates, setContentUpdates] = useState({
    tracks: 0,
    shoutouts: 0,
    reviews: 0,
    uploads: 0
  })

  const publishContentUpdate = useCallback((contentType) => {
    setContentUpdates(prev => ({
      ...prev,
      [contentType]: prev[contentType] + 1
    }))
  }, [])

  const [notices, setNotices] = useState([])
  const noticeTimersRef = useRef({})
  const noticeSeqRef = useRef(0)

  const clearNoticeTimer = useCallback((key) => {
    clearTimeout(noticeTimersRef.current[key])
    delete noticeTimersRef.current[key]
  }, [])

  const hideNotice = useCallback((key) => {
    clearNoticeTimer(key)
    setNotices(prev => (prev.some(notice => notice.key === key) ? prev.filter(notice => notice.key !== key) : prev))
  }, [clearNoticeTimer])

  const showNotice = useCallback(({ key, tone = 'info', text, duration, sticky = false, dismissible = true, priority = 1, ...look }) => {
    const noticeKey = key || `${tone}:${text}`
    const limit = NOTICE_DURATION_MS[tone] || NOTICE_DURATION_MS.info
    const holds = sticky || duration === 0 || duration < 0
    const seq = ++noticeSeqRef.current
    const notice = { ...look, key: noticeKey, tone, text, sticky: holds, dismissible, priority, seq }

    setNotices(prev => {
      const index = prev.findIndex(item => item.key === noticeKey)
      if (index >= 0) {
        const next = prev.slice()
        next[index] = { ...notice, seq: prev[index].seq }
        return next
      }
      const next = [...prev, notice]
      const passing = next.filter(item => !item.sticky)
      const dropped = new Set(passing.slice(0, Math.max(0, passing.length - MAX_PASSING_NOTICES)).map(item => item.key))
      dropped.forEach(clearNoticeTimer)
      return dropped.size ? next.filter(item => !dropped.has(item.key)) : next
    })

    clearNoticeTimer(noticeKey)
    if (!holds) {
      noticeTimersRef.current[noticeKey] = setTimeout(() => hideNotice(noticeKey), Math.min(duration || limit, limit))
    }
    return noticeKey
  }, [clearNoticeTimer, hideNotice])

  const publishToast = useCallback((message, type = 'info', duration, _position, replaceKey = null) => {
    return showNotice({ key: replaceKey, tone: type, text: message, duration })
  }, [showNotice])

  const removeToast = hideNotice

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

  const initialButtonOpacity = INITIAL_SETTINGS.radioInput === 'text' ? 0 : 1
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
    playerHeight: 80
  })

  const reportInterfaceState = useCallback((updates) => {
    Object.assign(interfaceRef.current, updates)

    if (window.__refCalls) {
      Object.keys(updates).forEach(key => window.__refCalls.keysThisSecond.add(key))
      window.__refCalls.count++
    }

    const merged = interfaceRef.current
    const isHidden = merged.isFullscreenVisuals || merged.isScrolling || uiState.settingsState.radioInput === 'text'
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
    publishSettings({ radioInput: uiState.settingsState.radioInput === 'text' ? 'voice' : 'text' })
  }, [publishSettings])

  useEffect(() => {
    reportInterfaceState({})
  }, [settingsState.radioInput, reportInterfaceState])

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
    if (updates.crossfadeMs !== undefined) stateUpdates.crossfadeMs = updates.crossfadeMs
    if (updates.is_playing !== undefined) stateUpdates.is_playing = updates.is_playing
    if (updates.currentTrack !== undefined) stateUpdates.currentTrack = updates.currentTrack
    if (updates.queue !== undefined) stateUpdates.queue = updates.queue
    if (updates.currentIndex !== undefined) stateUpdates.currentIndex = updates.currentIndex
    if (updates.talkBreak !== undefined) stateUpdates.talkBreak = updates.talkBreak
    if (updates.audioNeedsTap !== undefined) stateUpdates.audioNeedsTap = updates.audioNeedsTap

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

  useEffect(() => {
    const pinned = pinnedArtworkIdsRef.current
    const unpins = CURRENT_TRACK_PACK_SIZES.map(size => packCache(size).addPinnedChecker(id => pinned.has(id)))
    return () => unpins.forEach(unpin => unpin())
  }, [])

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

  const currentTrackId = engineState.currentTrack?.id
  const currentTrackHasArtwork = engineState.currentTrack?.has_artwork
  const { queue: engineQueue, currentIndex: engineIndex } = engineState

  useEffect(() => {
    if (!currentTrackId) return

    const nextTrack = engineQueue?.[engineIndex + 1]
    const pinned = pinnedArtworkIdsRef.current
    pinned.clear()
    for (const track of [{ id: currentTrackId, has_artwork: currentTrackHasArtwork }, nextTrack]) {
      if (!track?.id) continue
      pinned.add(track.id)
      if (track.has_artwork === false) continue
      for (const size of CURRENT_TRACK_PACK_SIZES) void packCache(size).getMedia(track.id)
    }

    if (settingsState.videoClipsEnabled) {
      fetchVideoClips(currentTrackId)
    }
  }, [currentTrackId, currentTrackHasArtwork, engineQueue, engineIndex, fetchVideoClips, settingsState.videoClipsEnabled])

  useEffect(() => {
    logger.info(`[UIState] 🎬 Video clips setting: ${settingsState.videoClipsEnabled ? 'ENABLED' : 'DISABLED'}`)
  }, [settingsState.videoClipsEnabled])

  const setTrackData = useCallback((features, lyrics, trackId = null) => {
    setAudioFeatures(features)
    setLyricTimestamps(lyrics)
    setTrackDataFor(trackId)
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

  const openReviewModal = useCallback((trackId, track = null) => {
    if (!trackId) return
    pauseSceneRendering(MODAL_OPEN_PAUSE_MS)
    startTransition(() => setReviewModalState({ isOpen: true, trackId, track }))
  }, [])

  const closeReviewModal = useCallback(() => {
    setReviewModalState(prev => ({ ...prev, isOpen: false }))
  }, [])

  const openUploadModal = useCallback(() => {
    pauseSceneRendering(MODAL_OPEN_PAUSE_MS)
    startTransition(() => {
      setUploadEditTrackId(null)
      setUploadModalOpen(true)
    })
  }, [])

  const openEditTrack = useCallback((trackId) => {
    if (!trackId) return
    pauseSceneRendering(MODAL_OPEN_PAUSE_MS)
    startTransition(() => {
      setUploadEditTrackId(trackId)
      setUploadModalOpen(true)
    })
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

  const value = useMemo(() => ({
    tiltNeedsPermission: TILT_NEEDS_PERMISSION,
    requestMotionAccess,
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

    notices,
    showNotice,
    hideNotice,
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

    videoClipsMapRef,
    videoClipsByTrack,
    fetchVideoClips,
    audioFeatures,
    lyricTimestamps,
    trackDataFor,
    setTrackData,

    shoutoutModalState,
    openShoutoutModal,
    closeShoutoutModal,

    reviewModalState,
    openReviewModal,
    closeReviewModal,

    uploadModalOpen,
    uploadEditTrackId,
    uploadWatchId,
    setUploadWatchId,
    openUploadModal,
    openEditTrack,
    closeUploadModal,

    usageModalOpen,
    openUsageModal,
    closeUsageModal,

    isOfflineRendering,
    setIsOfflineRendering,

    isScreenVisible,

    setVideoPreviewPlaying,
  }), [
    requestMotionAccess,
    reportEngineStatus, visualState, radioProgressData, visualColorData, engineState,
    setMixerRef, audioState, publishAudioState, queueState, publishQueueState,
    authState, publishAuthState, radioState, publishRadioState, downloadState, publishDownloadState,
    settingsState, publishSettings, contentUpdates, publishContentUpdate,
    notices, showNotice, hideNotice, publishToast, removeToast, toastSuccess, toastError, toastInfo, toastWarning,
    updateRadioButtonInteraction, updateRadioButtonOpacity, updateRadioButtonForegroundOpacity,
    reportInterfaceState, interfaceState, toggleCatalogView, toggleRadioInput, setMobilePanel, updateShaderRegions, updateShaderRadioButtonPos,
    videoClipsByTrack, fetchVideoClips, audioFeatures, lyricTimestamps, trackDataFor, setTrackData,
    shoutoutModalState, openShoutoutModal, closeShoutoutModal,
    reviewModalState, openReviewModal, closeReviewModal,
    uploadModalOpen, uploadEditTrackId, uploadWatchId, openUploadModal, openEditTrack, closeUploadModal,
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
    showNotice,
    hideNotice,
    publishToast,
    removeToast,
    toastSuccess,
    toastError,
    toastInfo,
    toastWarning,
  }), [showNotice, hideNotice, publishToast, removeToast, toastSuccess, toastError, toastInfo, toastWarning])

  return (
    <UIStoreContext.Provider value={store}>
    <UIStateContext.Provider value={value}>
      <UIActionsContext.Provider value={actionsValue}>
        <RadioStateContext.Provider value={radioState}>
          <RadioButtonContext.Provider value={radioButtonValue}>
            {children}
          </RadioButtonContext.Provider>
        </RadioStateContext.Provider>
      </UIActionsContext.Provider>
    </UIStateContext.Provider>
    </UIStoreContext.Provider>
  )
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

export function useVideoClips(trackId) {
  const {
    videoClipsByTrack,
    fetchVideoClips,
    enabled,
  } = useUISelector(state => ({
    videoClipsByTrack: state.videoClipsByTrack,
    fetchVideoClips: state.fetchVideoClips,
    enabled: state.settingsState.videoClipsEnabled,
  }))
  const clips = enabled && trackId ? videoClipsByTrack[trackId] : EMPTY_CLIPS

  useEffect(() => {
    if (enabled && trackId && clips === undefined) void fetchVideoClips(trackId)
  }, [enabled, trackId, clips, fetchVideoClips])

  return clips || EMPTY_CLIPS
}

export function useRadioButton() {
  const radioButton = useContext(RadioButtonContext)
  if (!radioButton) throw new Error('useRadioButton must be used within UIStateProvider')
  return radioButton
}

