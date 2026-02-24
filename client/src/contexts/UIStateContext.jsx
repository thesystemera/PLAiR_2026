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
 * - useUIState() - Access full context (both fast and slow lane)
 * - useRadioUI() - Convenience hook for radio-specific state
 * - useArtwork() - Artwork URL management
 *
 * ENGINES publish data → UIState derives visual state → VIEWS subscribe and render
 *
 * NO MIDDLEMEN. NO PROP DRILLING. Just clean pub/sub.
 *
 * See: docs/ARCHITECTURE_SSOT_PATTERN.md for detailed documentation
 */

import { createContext, useContext, useState, useCallback, useMemo, useRef, useEffect } from 'react'
import { artworkCache, enrichedArtworkCache } from '../lib/mediaCache'
import { logger } from '../lib/logger'
import { UI_FULLSCREEN } from '../lib/themeManager'
import { api } from '../lib/api'

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

const UIStateContext = createContext(null)

const GRADIENT_COLORS = [
  ['#9333ea', '#2563eb'],
  ['#db2777', '#9333ea'],
  ['#2563eb', '#06b6d4'],
  ['#16a34a', '#14b8a6'],
  ['#ea580c', '#dc2626'],
]

function generatePlaceholderDataURL(id) {
  if (!id) {
    const [color1, color2] = GRADIENT_COLORS[0]
    return `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:${color1}"/><stop offset="100%" style="stop-color:${color2}"/></linearGradient></defs><rect width="100" height="100" fill="url(#g)"/><text x="50" y="65" font-size="40" text-anchor="middle" fill="white" opacity="0.8">🎵</text></svg>`)}`
  }

  try {
    const index = parseInt(id.slice(0, 2), 16) % GRADIENT_COLORS.length
    const [color1, color2] = GRADIENT_COLORS[index]
    return `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:${color1}"/><stop offset="100%" style="stop-color:${color2}"/></linearGradient></defs><rect width="100" height="100" fill="url(#g)"/><text x="50" y="65" font-size="40" text-anchor="middle" fill="white" opacity="0.8">🎵</text></svg>`)}`
  } catch (e) {
    const [color1, color2] = GRADIENT_COLORS[0]
    return `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:${color1}"/><stop offset="100%" style="stop-color:${color2}"/></linearGradient></defs><rect width="100" height="100" fill="url(#g)"/><text x="50" y="65" font-size="40" text-anchor="middle" fill="white" opacity="0.8">🎵</text></svg>`)}`
  }
}

export const uiState = {
  audioState: {
    isOnline: navigator.onLine,
    isServerAvailable: navigator.onLine,
    connectionMode: navigator.onLine ? 'full' : 'offline',
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
    isEnabled: localStorage.getItem('backgroundDownloads') !== 'false',
  },
  authState: {
    isAuthenticated: !!localStorage.getItem('cached_user'),
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

export function UIStateProvider({ children }) {
  const [artworkUrls, setArtworkUrls] = useState(new Map())
  const [enrichedArtworkUrls, setEnrichedArtworkUrls] = useState(new Map())
  const loadingTracksRef = useRef(new Set())
  const loadingEnrichedRef = useRef(new Set())
  const [audioFeatures, setAudioFeatures] = useState(null)
  const [lyricTimestamps, setLyricTimestamps] = useState(null)

  const [videoClipsMap, setVideoClipsMap] = useState(new Map())
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
    isCrossfading: false,
    is_playing: false,
    currentTrack: null,
    queue: [],
    currentIndex: 0,
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

  const [isOfflineRendering, setIsOfflineRendering] = useState(false)

  const [isScreenVisible, setIsScreenVisible] = useState(!document.hidden)

  useEffect(() => {
    const handleVisibilityChange = () => {
      setIsScreenVisible(!document.hidden)
    }
    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange)
  }, [])

  const duckingStateRef = useRef('idle')

  const setMixerRef = useCallback((ref) => {
    mixerRefInternal.current = ref
  }, [])

  useEffect(() => {
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
    }

    const handleMouseMove = (e) => {
      mouseRef.current.parallaxX = (e.clientX / window.innerWidth - 0.5) * 2
      mouseRef.current.parallaxY = (e.clientY / window.innerHeight - 0.5) * 2
    }

    const requestPermission = async () => {
      if (typeof DeviceOrientationEvent !== 'undefined' && typeof DeviceOrientationEvent.requestPermission === 'function') {
        try {
          const permission = await DeviceOrientationEvent.requestPermission()
          if (permission === 'granted') {
            window.addEventListener('deviceorientation', handleOrientation)
          }
        } catch (error) {
          logger.error('Gyroscope permission denied', error)
        }
      } else {
        window.addEventListener('deviceorientation', handleOrientation)
      }
    }

    void requestPermission()
    window.addEventListener('mousemove', handleMouseMove)

    return () => {
      window.removeEventListener('deviceorientation', handleOrientation)
      window.removeEventListener('mousemove', handleMouseMove)
    }
  }, [])

  // Register RAF source for debugging
  useEffect(() => {
    window.registerRAFSource?.('UIState-physics')
  }, [])

  useEffect(() => {
    if (!isScreenVisible) return  // Don't run when screen is hidden

    let animationFrameId
    let lastInputTime = Date.now()
    const IDLE_TIMEOUT = 2000

    const TENSION = 0.12
    const FRICTION = 0.80
    const INPUT_SMOOTHING = 0.10

    const isTouchDevice = window.matchMedia('(pointer: coarse)').matches

    const handleInput = () => {
      lastInputTime = Date.now()
      // Restart loop if it stopped
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

      // Touch devices: always run (gyroscope is continuous input)
      // Desktop: pause when mouse idle AND physics settled
      if (isTouchDevice) {
        animationFrameId = requestAnimationFrame(loop)
      } else {
        const isMoving = Math.abs(p.vx) > 0.001 || Math.abs(p.vy) > 0.001
        const isRecentInput = Date.now() - lastInputTime < IDLE_TIMEOUT
        if (isMoving || isRecentInput) {
          animationFrameId = requestAnimationFrame(loop)
        } else {
          animationFrameId = null
        }
      }
    }

    if (!isTouchDevice) {
      window.addEventListener('mousemove', handleInput, { passive: true })
    }

    loop()

    return () => {
      if (animationFrameId) cancelAnimationFrame(animationFrameId)
      if (!isTouchDevice) {
        window.removeEventListener('mousemove', handleInput)
      }
    }
  }, [isScreenVisible])

  useEffect(() => {
    const mixer = mixerRefInternal.current?.current
    if (!mixer) return

    const { isMicRecording, isDJSpeaking, isShoutoutPlaying, isVideoPreviewPlaying } = engineState
    const targetState = isMicRecording ? 'user'
      : (isShoutoutPlaying ? 'shoutout'
      : (isVideoPreviewPlaying ? 'videoPreview'
      : (isDJSpeaking ? 'dj' : 'idle')))

    if (duckingStateRef.current === targetState) return

    duckingStateRef.current = targetState

    if (targetState === 'idle') {
      mixer.restoreMusic(400)
    } else if (targetState === 'user') {
      mixer.duckMusic(0.1, 200)
    } else if (targetState === 'shoutout') {
      mixer.duckMusic(0.1, 200)
    } else if (targetState === 'videoPreview') {
      mixer.duckMusic(0.15, 200)  // Duck music for video preview
    } else if (targetState === 'dj') {
      mixer.duckMusic(0.25, 400)
    }
  }, [engineState.isMicRecording, engineState.isDJSpeaking, engineState.isShoutoutPlaying, engineState.isVideoPreviewPlaying])

  const [audioState, setAudioState] = useState({
    isOnline: navigator.onLine,
    isServerAvailable: navigator.onLine,
    connectionMode: navigator.onLine ? 'full' : 'offline',
    isCached: false,
    buffering: false,
    bitrate: null,
    effectiveBitrate: null,
    networkQuality: null,
  })

  const publishAudioState = useCallback((updates) => {
    setAudioState(prev => {
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
    activeSeedMode: localStorage.getItem('lastSeedMode') || null,
  })

  const publishRadioState = useCallback((updates) => {
    setRadioState(prev => {
      const newState = { ...prev, ...updates }
      if (updates.activeSeedMode !== undefined) {
        localStorage.setItem('lastSeedMode', updates.activeSeedMode || '')
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
    isEnabled: localStorage.getItem('backgroundDownloads') !== 'false',
  })

  const [settingsState, setSettingsState] = useState({
    ttsMuted: false,
    notificationsMuted: false,
    audioQuality: 'auto',
    dataSaverMode: localStorage.getItem('dataSaverMode') === 'true',
    fpsEnabled: false,
    videoClipsEnabled: false,
    visualQuality: 'high',
  })

  const publishSettings = useCallback((updates) => {
    setSettingsState(prev => {
      const newState = { ...prev, ...updates }
      if (updates.dataSaverMode !== undefined) {
        localStorage.setItem('dataSaverMode', String(updates.dataSaverMode))
      }
      return newState
    })
  }, [])

  const publishDownloadState = useCallback((updates) => {
    setDownloadState(prev => {
      const newState = { ...prev, ...updates }
      Object.assign(uiState.downloadState, newState)
      if (updates.isEnabled !== undefined) {
        localStorage.setItem('backgroundDownloads', String(updates.isEnabled))
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

  const [radioButtonOpacity, setRadioButtonOpacity] = useState(1)
  const [radioButtonForegroundOpacity, setRadioButtonForegroundOpacity] = useState(1)

  const [interfaceState, setInterfaceState] = useState({
    isScrolling: false,
    scrollVelocity: 0,
    scrollPosition: 0,
    isFullscreenVisuals: false,
    showUIControls: false,
    currentMobilePanel: 2,
    catalogView: 'tracks'
  })

  const interfaceRef = useRef({
    isScrolling: false,
    scrollVelocity: 0,
    scrollPosition: 0,
    isFullscreenVisuals: false,
    showUIControls: false,
    currentMobilePanel: 2,
    catalogView: 'tracks'
  })

  const reportInterfaceState = useCallback((updates) => {
    Object.assign(interfaceRef.current, updates)

    if (window.__refCalls) {
      Object.keys(updates).forEach(key => window.__refCalls.keysThisSecond.add(key))
      window.__refCalls.count++
    }

    setInterfaceState(prev => {
      const newState = { ...prev, ...updates }

      // GLSL opacity (background frosted glass) - 0.35 on other panels
      let glslOpacity
      if (newState.isFullscreenVisuals || newState.isScrolling) {
        glslOpacity = 0
      } else if (newState.currentMobilePanel === 2) {
        glslOpacity = 1
      } else {
        glslOpacity = UI_FULLSCREEN.radioGlassOpacity
      }
      setRadioButtonOpacity(glslOpacity)
      
      // React foreground opacity - 0 on other panels (foreground graphic, not glass)
      let fgOpacity
      if (newState.isFullscreenVisuals || newState.isScrolling) {
        fgOpacity = 0
      } else if (newState.currentMobilePanel === 2) {
        fgOpacity = 1
      } else {
        fgOpacity = 0  // Foreground button hidden on other panels
      }
      setRadioButtonForegroundOpacity(fgOpacity)

      return newState
    })
  }, [])

  const [shaderPanelRegions, setShaderPanelRegions] = useState([])
  const [shaderPanelOpacities, setShaderPanelOpacities] = useState([])
  const [shaderRadioButtonPos, setShaderRadioButtonPos] = useState({ x: 0.5, y: 0.5, radiusX: 0, radiusY: 0 })

  const visualState = useMemo(() => {
    if (engineState.isMicRecording) return 1
    if (engineState.isAIProcessing) return 4
    if (engineState.isDJSpeaking) return 3
    if (engineState.isMusicPlaying) return 2
    if (engineState.isMusicPaused) return 5
    return 0
  }, [engineState])

  const visualColorData = useMemo(() => {
    let resolvedColor = STATE_COLORS[0]
    if (visualState === 3) {
      resolvedColor = speakerColorRef.current
    } else if (STATE_COLORS[visualState]) {
      resolvedColor = STATE_COLORS[visualState]
    }

    return {
      stateInt: visualState,
      currentVisualColor: resolvedColor
    }
  }, [visualState])

  const radioProgressData = useMemo(() => {
    return {
      stateInt: visualState,
      currentVisualColor: visualColorData.currentVisualColor
    }
  }, [visualState, visualColorData])

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

    const refKeys = Object.keys(updates).filter(k =>
      ['djFftData', 'micFftData', 'shoutoutFftData', 'speakerColor', 'progress_ms'].includes(k)
    )
    if (refKeys.length > 0) {
      if (!window.__refCalls) {
        window.__refCalls = { count: 0, last: now, perSecond: [], keysThisSecond: new Set() }
      }
      refKeys.forEach(key => window.__refCalls.keysThisSecond.add(key))
      window.__refCalls.count++

      if (now - window.__refCalls.last > 1000) {
        const fps = window.__refCalls.count
        window.__refCalls.perSecond.push(fps)
        if (window.__refCalls.perSecond.length > 5) window.__refCalls.perSecond.shift()
        window.__refCalls.count = 0
        window.__refCalls.last = now
        window.__refCalls.keysThisSecond.clear()
      }
    }

    const stateUpdates = {}
    if (updates.isMicRecording !== undefined) stateUpdates.isMicRecording = updates.isMicRecording
    if (updates.isDJSpeaking !== undefined) stateUpdates.isDJSpeaking = updates.isDJSpeaking
    if (updates.isShoutoutPlaying !== undefined) stateUpdates.isShoutoutPlaying = updates.isShoutoutPlaying
    if (updates.isMusicPlaying !== undefined) stateUpdates.isMusicPlaying = updates.isMusicPlaying
    if (updates.isMusicPaused !== undefined) stateUpdates.isMusicPaused = updates.isMusicPaused
    if (updates.isAIProcessing !== undefined) stateUpdates.isAIProcessing = updates.isAIProcessing
    if (updates.isActiveDevice !== undefined) stateUpdates.isActiveDevice = updates.isActiveDevice
    if (updates.isCrossfading !== undefined) stateUpdates.isCrossfading = updates.isCrossfading
    if (updates.is_playing !== undefined) stateUpdates.is_playing = updates.is_playing
    if (updates.currentTrack !== undefined) stateUpdates.currentTrack = updates.currentTrack
    if (updates.queue !== undefined) stateUpdates.queue = updates.queue
    if (updates.currentIndex !== undefined) stateUpdates.currentIndex = updates.currentIndex

    if (Object.keys(stateUpdates).length > 0) {
      if (!window.__stateCalls) {
        window.__stateCalls = { count: 0, last: now, perSecond: [], keysThisSecond: new Set() }
      }

      Object.keys(stateUpdates).forEach(key => window.__stateCalls.keysThisSecond.add(key))
      window.__stateCalls.count++

      if (now - window.__stateCalls.last > 1000) {
        const fps = window.__stateCalls.count
        window.__stateCalls.perSecond.push(fps)
        if (window.__stateCalls.perSecond.length > 5) window.__stateCalls.perSecond.shift()
        window.__stateCalls.count = 0
        window.__stateCalls.last = now
        window.__stateCalls.keysThisSecond.clear()
      }
      setEngineState(prev => ({ ...prev, ...stateUpdates }))
    }
  }, [])

  const updateRadioButtonInteraction = useCallback((interaction) => {
    setRadioButtonInteraction(prev => ({ ...prev, ...interaction }))
  }, [])

  const updateRadioButtonOpacity = useCallback((opacity) => {
    setRadioButtonOpacity(opacity)
  }, [])

  const updateRadioButtonForegroundOpacity = useCallback((opacity) => {
    setRadioButtonForegroundOpacity(opacity)
  }, [])

  const getArtworkUrl = useCallback((trackId, hasArtwork = true) => {
    if (!trackId || hasArtwork === false) return null
    if (artworkUrls.has(trackId)) return artworkUrls.get(trackId)
    const memoryCached = artworkCache.getMemory(trackId)
    if (memoryCached) {
      setArtworkUrls(prev => new Map(prev).set(trackId, memoryCached))
      return memoryCached
    }
    return generatePlaceholderDataURL(trackId)
  }, [artworkUrls])

  const getEnrichedArtworkUrl = useCallback((trackId, hasArtwork = true) => {
    if (!trackId || hasArtwork === false) return null
    if (enrichedArtworkUrls.has(trackId)) return enrichedArtworkUrls.get(trackId)
    const memoryCached = enrichedArtworkCache.getMemory(trackId)
    if (memoryCached) {
      setEnrichedArtworkUrls(prev => new Map(prev).set(trackId, memoryCached))
      return memoryCached
    }
    return generatePlaceholderDataURL(trackId)
  }, [enrichedArtworkUrls])


  const preloadArtwork = useCallback(async (trackId, hasArtwork = true) => {
    if (!trackId || hasArtwork === false) return
    if (artworkUrls.has(trackId)) return
    if (loadingTracksRef.current.has(trackId)) return
    loadingTracksRef.current.add(trackId)
    try {
      const url = await artworkCache.getMedia(trackId)
      if (url) {
        setArtworkUrls(prev => new Map(prev).set(trackId, url))
      }
    } catch (error) {
      logger.error(`[UIState] Failed to load artwork:`, error)
    } finally {
      loadingTracksRef.current.delete(trackId)
    }
  }, [artworkUrls])

  const preloadArtworkBatch = useCallback(async (trackIds) => {
    const promises = trackIds.filter(id => id && !artworkUrls.has(id)).map(id => preloadArtwork(id, true))
    await Promise.all(promises)
  }, [artworkUrls, preloadArtwork])

  const clearArtwork = useCallback((trackId) => {
    setArtworkUrls(prev => {
      const next = new Map(prev)
      const blobUrl = prev.get(trackId)
      if (blobUrl && blobUrl.startsWith('blob:')) {
        URL.revokeObjectURL(blobUrl)
      }
      next.delete(trackId)
      return next
    })
  }, [])

  const preloadEnrichedArtwork = useCallback(async (trackId, hasArtwork = true) => {
    if (!trackId || hasArtwork === false) return
    if (enrichedArtworkUrls.has(trackId)) return
    if (loadingEnrichedRef.current.has(trackId)) return
    loadingEnrichedRef.current.add(trackId)
    try {
      const url = await enrichedArtworkCache.getMedia(trackId)
      if (url) {
        setEnrichedArtworkUrls(prev => new Map(prev).set(trackId, url))
      }
    } catch (error) {
      logger.error(`[UIState] Failed to load enriched artwork:`, error)
    } finally {
      loadingEnrichedRef.current.delete(trackId)
    }
  }, [enrichedArtworkUrls])

  const clearEnrichedArtwork = useCallback((trackId) => {
    setEnrichedArtworkUrls(prev => {
      const next = new Map(prev)
      const blobUrl = prev.get(trackId)
      if (blobUrl && blobUrl.startsWith('blob:')) {
        URL.revokeObjectURL(blobUrl)
      }
      next.delete(trackId)
      return next
    })
  }, [])

  const videoClipsMapRef = useRef(videoClipsMap)
  useEffect(() => { videoClipsMapRef.current = videoClipsMap }, [videoClipsMap])

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
      // Uses api._routeRequest() which handles offline routing automatically
      const data = await api.getVideoClips(trackId)
      logger.info(`[UIState] 🎬 API data:`, data.reason || `${data.clips?.length || 0} clips`, data.keywords?.slice(0, 2) || 'no keywords')
      if (data.clips && data.clips.length > 0) {
        const clips = data.clips.map(clip => ({
          ...clip,
          url: `${window.location.origin}${clip.url}`
        }))
        setVideoClipsMap(prev => {
          const next = new Map(prev)
          next.set(trackId, clips)
          return next
        })
        videoClipsMapRef.current = new Map(videoClipsMapRef.current).set(trackId, clips)
        logger.info(`[UIState] ✅ Loaded ${clips.length} video clips for track ${trackId.slice(0, 8)}`)
      } else {
        setVideoClipsMap(prev => {
          const next = new Map(prev)
          next.set(trackId, [])
          return next
        })
        videoClipsMapRef.current = new Map(videoClipsMapRef.current).set(trackId, [])
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
    setShaderPanelRegions(regions)
    setShaderPanelOpacities(opacities)
  }, [])

  const updateShaderRadioButtonPos = useCallback((pos) => {
    setShaderRadioButtonPos(pos)
  }, [])

  const openShoutoutModal = useCallback((shoutout) => {
    setShoutoutModalState({ isOpen: true, shoutout })
  }, [])

  const closeShoutoutModal = useCallback(() => {
    setShoutoutModalState({ isOpen: false, shoutout: null })
  }, [])

  const openUploadModal = useCallback(() => {
    setUploadModalOpen(true)
  }, [])

  const closeUploadModal = useCallback(() => {
    setUploadModalOpen(false)
  }, [])

  const setVideoPreviewPlaying = useCallback((isPlaying) => {
    setEngineState(prev => ({ ...prev, isVideoPreviewPlaying: isPlaying }))
  }, [])

  const value = {
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

    radioButtonInteraction,
    radioButtonOpacity,
    radioButtonForegroundOpacity,
    updateRadioButtonInteraction,
    updateRadioButtonOpacity,
    updateRadioButtonForegroundOpacity,
    reportInterfaceState,
    interfaceState,
    interfaceRef,

    shaderPanelRegions,
    shaderPanelOpacities,
    shaderRadioButtonPos,
    updateShaderRegions,
    updateShaderRadioButtonPos,

    artworkUrls,
    getArtworkUrl,
    preloadArtwork,
    preloadArtworkBatch,
    clearArtwork,
    enrichedArtworkUrls,
    getEnrichedArtworkUrl,
    preloadEnrichedArtwork,
    clearEnrichedArtwork,
    videoClipsMap,
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

    isOfflineRendering,
    setIsOfflineRendering,

    isScreenVisible,

    setVideoPreviewPlaying,
  }

  return (
    <UIStateContext.Provider value={value}>
      {children}
    </UIStateContext.Provider>
  )
}

export function useUIState() {
  const context = useContext(UIStateContext)
  if (!context) throw new Error('useUIState must be used within UIStateProvider')
  return context
}

export function useArtwork(trackId, hasArtwork = true) {
  const { getArtworkUrl, preloadArtwork } = useUIState()
  const [artworkUrl, setArtworkUrl] = useState(() => getArtworkUrl(trackId, hasArtwork))
  useEffect(() => {
    if (!trackId || hasArtwork === false) { setArtworkUrl(null); return }
    const currentUrl = getArtworkUrl(trackId, hasArtwork)
    setArtworkUrl(currentUrl)
    preloadArtwork(trackId, hasArtwork)
  }, [trackId, hasArtwork, getArtworkUrl, preloadArtwork])
  return artworkUrl
}

export function useEnrichedArtwork(trackId, hasArtwork = true) {
  const { getEnrichedArtworkUrl, preloadEnrichedArtwork } = useUIState()
  const [enrichedArtworkUrl, setEnrichedArtworkUrl] = useState(() => getEnrichedArtworkUrl(trackId, hasArtwork))
  useEffect(() => {
    if (!trackId || hasArtwork === false) { setEnrichedArtworkUrl(null); return }
    const currentUrl = getEnrichedArtworkUrl(trackId, hasArtwork)
    setEnrichedArtworkUrl(currentUrl)
    preloadEnrichedArtwork(trackId, hasArtwork)
  }, [trackId, hasArtwork, getEnrichedArtworkUrl, preloadEnrichedArtwork])
  return enrichedArtworkUrl
}

export function useVideoClips(trackId) {
  const { videoClipsMap, fetchVideoClips } = useUIState()
  const [clips, setClips] = useState(() => videoClipsMap.get(trackId) || [])
  useEffect(() => {
    if (!trackId) { setClips([]); return }
    const currentClips = videoClipsMap.get(trackId)
    if (currentClips !== undefined) {
      setClips(currentClips)
    } else {
      fetchVideoClips(trackId)
    }
  }, [trackId, videoClipsMap, fetchVideoClips])

  return clips
}

export function useRadioUI() {
  const {
    reportEngineStatus,
    visualState,
    radioButtonOpacity,
    radioButtonForegroundOpacity,
    radioButtonInteraction,
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
    interfaceRef
  } = useUIState()

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