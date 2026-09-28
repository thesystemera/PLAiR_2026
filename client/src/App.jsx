import {lazy, startTransition, Suspense, useCallback, useEffect, useMemo, useRef, useState} from 'react'
import {AnimatePresence, motion} from 'framer-motion'
import {api} from './lib/api'
import {MODAL_OPEN_PAUSE_MS, pauseSceneRendering} from './lib/renderPause'
import {cacheManager} from './lib/cacheManager'
import {logger} from './lib/logger'
import {safeStorage} from './lib/safeStorage'
import {MOTION, PRESETS} from './lib/motion'
import {VERTICAL_EDGE_FADE_MASK} from './lib/themeManager'
import {useArtwork, useUIState} from './contexts/UIStateContext'
import {useProfilePicture} from './hooks/useProfilePicture'
import {usePlayback} from './contexts/PlaybackContext'
import {useAuth} from './contexts/AuthContext'
import {TRANSITIONS, UI_FULLSCREEN, useDynamicTheme} from './contexts/DynamicThemeContext'
import {useWebSocketSubscribe} from './contexts/WebSocketContext'
import {VoiceRecordingProvider} from './contexts/VoiceRecordingContext'
import {DJVoiceEngine} from './hooks/useDJAudioStream'
import {DialogProvider} from './contexts/DialogContext'
import {useGeolocation} from './hooks/useGeolocation'
import {useViewport} from './contexts/ViewportContext'
import {useGenerationQueue} from './contexts/GenerationQueueContext'
import {Catalog} from './components/Catalog'
import {Player} from './components/Player'
import {Queue} from './components/Queue'
import {NowPlaying} from './components/NowPlaying'
import {User} from './components/User'
import {Radio} from './components/Radio'
import {Shoutouts} from './components/Shoutouts'
import {
    getPanelPointerEvents,
    Panel,
    PANEL_CONFIG,
    PANEL_FADE_TRANSITION,
    PANEL_IDS
} from './components/Panel'
import Login from './components/Auth/Login'
import Register from './components/Auth/Register'
import ToastContainer from './components/Toast'
import {OnAirFrame} from './components/OnAirBadge'
import {FPSCounter} from './components/FPSCounter'
import {KeyboardControls} from './components/KeyboardControls'

const lazyNamed = (loader, name) => lazy(() => loader().then(module => ({ default: module[name] })))

const LAZY_MODULE_LOADERS = [
  () => import('./components/AudioReactiveCanvas'),
  () => import('./components/modals/SeedRadioModal'),
  () => import('./components/modals/TrackAnalyticsModal'),
  () => import('./components/modals/ShoutoutModal'),
  () => import('./components/modals/GenerationModal'),
  () => import('./components/modals/CompatibilityWarningModal'),
  () => import('./components/modals/DemoModeModal'),
  () => import('./components/modals/ShareModal'),
  () => import('./components/modals/UploadMusicModal'),
]

const [
  loadAudioReactiveCanvas,
  loadSeedRadioModal,
  loadTrackAnalyticsModal,
  loadShoutoutModal,
  loadGenerationModal,
  loadCompatibilityWarningModal,
  loadDemoModeModal,
  loadShareModal,
  loadUploadMusicModal,
] = LAZY_MODULE_LOADERS

const AudioReactiveCanvas = lazyNamed(loadAudioReactiveCanvas, 'AudioReactiveCanvas')
const SeedRadioModal = lazyNamed(loadSeedRadioModal, 'SeedRadioModal')
const TrackAnalyticsModal = lazyNamed(loadTrackAnalyticsModal, 'TrackAnalyticsModal')
const ShoutoutModal = lazyNamed(loadShoutoutModal, 'ShoutoutModal')
const GenerationModal = lazyNamed(loadGenerationModal, 'GenerationModal')
const CompatibilityWarningModal = lazyNamed(loadCompatibilityWarningModal, 'CompatibilityWarningModal')
const DemoModeModal = lazyNamed(loadDemoModeModal, 'DemoModeModal')
const ShareModal = lazyNamed(loadShareModal, 'ShareModal')
const UploadMusicModal = lazyNamed(loadUploadMusicModal, 'UploadMusicModal')
const UsageStatsModal = lazyNamed(() => import('./components/modals/UsageStatsModal'), 'UsageStatsModal')
const CostTicker = lazyNamed(() => import('./components/CostTicker'), 'CostTicker')

const canvasFallback = <div className="absolute inset-0 bg-black/50 pointer-events-none z-0" />

const MOBILE_NAV_MASK = VERTICAL_EDGE_FADE_MASK('4px')
const MOBILE_NAV_MASK_STYLE = { maskImage: MOBILE_NAV_MASK, WebkitMaskImage: MOBILE_NAV_MASK }
const MOBILE_RAIL_STYLE = { ...MOBILE_NAV_MASK_STYLE, width: 'calc(4.5rem + var(--safe-left))', paddingLeft: 'var(--safe-left)' }
const MOBILE_NAV_CLASS = 'flex-shrink-0 bg-black/25 border-t border-gray-800/50 flex justify-around items-center h-16 z-20'
const MOBILE_RAIL_CLASS = 'order-first flex-shrink-0 bg-black/25 border-r border-gray-800/50 flex flex-col justify-around items-stretch h-full py-1 z-20'
const FULLSCREEN_EXIT_STYLE = { top: 'max(1.5rem, calc(var(--safe-top) + 0.75rem))', right: 'max(1.5rem, calc(var(--safe-right) + 0.75rem))' }

function LazyMount({ when, children }) {
  const [hasMounted, setHasMounted] = useState(when)
  if (when && !hasMounted) setHasMounted(true)
  if (!hasMounted) return null
  return <Suspense fallback={null}>{children}</Suspense>
}

if (import.meta.env.DEV && typeof window !== 'undefined' && !window.__rafDebug) {
  window.__rafDebug = {
    count: 0,
    lastLog: Date.now(),
    sources: {},
    registered: new Set(),  // All known RAF sources (even when paused)
    enabled: false          // Only log when FPS counter is enabled
  }

  // Components call this once at mount to register their RAF source
  window.registerRAFSource = (label) => {
    window.__rafDebug.registered.add(label)
  }

  const originalRAF = window.requestAnimationFrame
  window.requestAnimationFrame = (callback) => {
    window.__rafDebug.count++
    const now = Date.now()
    if (window.__rafDebug.enabled && now - window.__rafDebug.lastLog > 1000) {
      // Build output with all registered sources (0 if inactive)
      const output = {}
      for (const label of window.__rafDebug.registered) {
        output[label] = window.__rafDebug.sources[label] || 0
      }
      // Add any unlabeled sources
      for (const [label, count] of Object.entries(window.__rafDebug.sources)) {
        if (!window.__rafDebug.registered.has(label)) {
          output[label] = count
        }
      }
      const labeledTotal = Object.values(window.__rafDebug.sources).reduce((a, b) => a + b, 0)
      const unknown = window.__rafDebug.count - labeledTotal
      console.log('[RAF DEBUG] Total:', window.__rafDebug.count, 'Sources:', output, 'Unknown:', unknown)
      window.__rafDebug.count = 0
      window.__rafDebug.sources = {}
      window.__rafDebug.lastLog = now
    }
    return originalRAF(callback)
  }
}

function App() {
  const [showLogin, setShowLogin] = useState(false)
  const [showRegister, setShowRegister] = useState(false)
  const [showSeedModal, setShowSeedModal] = useState(false)
  const [seedModalTrack, setSeedModalTrack] = useState(null)
  const [showAnalyticsModal, setShowAnalyticsModal] = useState(false)
  const [showGenerationModal, setShowGenerationModal] = useState(false)
  const [generationModalTrack, setGenerationModalTrack] = useState(null)
  const [compatibilityWarningDismissed, setCompatibilityWarningDismissed] = useState(() => !!safeStorage.get('plair_compatibility_warning_dismissed'))
  const [demoModalDismissed, setDemoModalDismissed] = useState(() => !!safeStorage.get('plair_demo_mode_modal_seen'))
  const [showShareModal, setShowShareModal] = useState(false)
  const [shareModalTrack, setShareModalTrack] = useState(null)
  const [isPanelAnimating, setIsPanelAnimating] = useState(false)
  const [panelStates, setPanelStates] = useState({
    queue: true,
    catalog: true,
    radio: true,
    nowPlaying: true,
    user: true
  })

  const { playTrack, seek, seedRadio, addToQueue, togglePlay, previous, next, reloadCurrentTrackQuality, connected, audio } = usePlayback()
  const { user, isAuthenticated, logout, refreshUser, loading: authLoading, sessionExpiredCount } = useAuth()
  const { getAccentColor } = useDynamicTheme()
  const { setTrackData, updateShaderRegions, updateShaderRadioButtonPos, engineState, publishSettings, settingsState, toastSuccess, toastInfo, toastError, interfaceState, interfaceRef, reportInterfaceState, shoutoutModalState, closeShoutoutModal, queueState, uploadModalOpen, closeUploadModal, usageModalOpen, closeUsageModal, toggleCatalogView, setMobilePanel } = useUIState()
  const { addJob, setIsOpen: setQueueOpen } = useGenerationQueue()

  const catalogView = interfaceState.catalogView
  const mobilePanel = interfaceState.currentMobilePanel
  const playerHeight = interfaceState.playerHeight

  const success = toastSuccess
  const info = toastInfo
  const errorToast = toastError

  const isFullscreenVisuals = interfaceState.isFullscreenVisuals
  const showUIControls = interfaceState.showUIControls
  const wasConnectedRef = useRef(false)
  const hasConnectedOnceRef = useRef(false)

  const currentTrack = engineState.currentTrack
  const currentTrackArtwork = useArtwork(currentTrack?.id, currentTrack?.has_artwork)

  useGeolocation(isAuthenticated, { periodicCheck: true })

  useEffect(() => {
    if (!sessionExpiredCount) return
    setShowRegister(false)
    setShowLogin(true)
  }, [sessionExpiredCount])

  useEffect(() => {
    if (user) {
      publishSettings({
        ttsMuted: user.tts_muted ?? false,
        notificationsMuted: user.notifications_muted ?? false,
        audioQuality: user.audio_quality ?? 'auto',
        fpsEnabled: user.fps_enabled ?? false,
        videoClipsEnabled: user.video_clips_enabled ?? false,
        visualQuality: user.visual_quality ?? 'high'
      })
    }
  }, [user?.tts_muted, user?.notifications_muted, user?.audio_quality, user?.fps_enabled, user?.video_clips_enabled, user?.visual_quality, publishSettings])

  useEffect(() => {
    if (window.__rafDebug) {
      window.__rafDebug.enabled = settingsState.fpsEnabled
    }
  }, [settingsState.fpsEnabled])

  useEffect(() => {
    const warmLazyModules = () => {
      LAZY_MODULE_LOADERS.forEach(loader => {
        loader().catch(err => logger.warn('[App] Failed to prefetch module:', err))
      })
    }
    if ('requestIdleCallback' in window) {
      const idleId = window.requestIdleCallback(warmLazyModules, { timeout: 10000 })
      return () => window.cancelIdleCallback(idleId)
    }
    const timeoutId = setTimeout(warmLazyModules, 5000)
    return () => clearTimeout(timeoutId)
  }, [])

  useEffect(() => {
    if (!currentTrackArtwork || currentTrackArtwork.startsWith('data:')) return
    let cancelled = false
    const warmModalArtwork = () => {
      import('./components/modals/Modal')
        .then(module => { if (!cancelled) return module.prewarmModalAssets(currentTrackArtwork) })
        .catch(err => logger.warn('[App] Failed to prewarm modal artwork:', err))
    }
    if ('requestIdleCallback' in window) {
      const idleId = window.requestIdleCallback(warmModalArtwork, { timeout: 6000 })
      return () => { cancelled = true; window.cancelIdleCallback(idleId) }
    }
    const timeoutId = setTimeout(warmModalArtwork, 3000)
    return () => { cancelled = true; clearTimeout(timeoutId) }
  }, [currentTrackArtwork])

  const sharedTrackHandled = useRef(false)
  useEffect(() => {
    if (sharedTrackHandled.current) return

    const path = window.location.pathname
    const trackMatch = path.match(/^\/track\/([a-zA-Z0-9-]+)$/)

    if (trackMatch) {
      const trackId = trackMatch[1]
      sharedTrackHandled.current = true

      logger.info(`[App] Shared track URL detected: ${trackId}`)

      const playSharedTrack = async () => {
        try {
          await playTrack(trackId)
          logger.info(`[App] Started playing shared track: ${trackId}`)

          window.history.replaceState({}, '', '/')
        } catch (err) {
          logger.error(`[App] Failed to play shared track: ${err.message}`)
          toastError('Could not play shared track')
          window.history.replaceState({}, '', '/')
        }
      }

      setTimeout(playSharedTrack, 500)
    }
  }, [playTrack, toastError])

  const billingReturnHandled = useRef(false)
  useEffect(() => {
    if (billingReturnHandled.current || authLoading) return
    const params = new URLSearchParams(window.location.search)
    const billing = params.get('billing')
    if (!billing) return
    billingReturnHandled.current = true
    const checkoutSessionId = params.get('session_id')
    window.history.replaceState({}, '', window.location.pathname)

    if (billing === 'cancelled') {
      info('Checkout cancelled. You have not been charged.', 5000)
      return
    }
    if (billing !== 'success' || !isAuthenticated) return

    const confirmPremium = async () => {
      for (let attempt = 0; attempt < 5; attempt++) {
        try {
          const status = await api.getBillingStatus(attempt === 0 ? checkoutSessionId : null)
          if (status.tier === 'premium') {
            await refreshUser()
            success('Welcome to PLAiR Premium! Your subscription is active.', 6000)
            return
          }
        } catch (err) {
          logger.warn('[App] Billing status check failed:', err)
        }
        await new Promise(resolve => setTimeout(resolve, 2000))
      }
      await refreshUser()
      info('Payment received. Premium will activate in a moment.', 6000)
    }
    void confirmPremium()
  }, [authLoading, isAuthenticated, refreshUser, success, info])

  const { isMobile, isPhoneLandscape, isCompatible } = useViewport()
  const navIconClass = isPhoneLandscape ? 'w-5 h-5 mb-0.5' : 'w-6 h-6 mb-1'
  const userProfilePicture = useProfilePicture(user?.id, !!user?.profile_picture)

  const showCompatibilityWarning = !isCompatible && !compatibilityWarningDismissed
  const showDemoModal = !authLoading && !user && !demoModalDismissed

  useEffect(() => {
    if (!connected && wasConnectedRef.current) {
      errorToast('Disconnected from server. Attempting to reconnect...', 8000)
      wasConnectedRef.current = false
    } else if (connected && !wasConnectedRef.current) {
      if (hasConnectedOnceRef.current) {
        success('Connected to server', 4000)
      }
      hasConnectedOnceRef.current = true
      wasConnectedRef.current = true
    }
  }, [connected, errorToast, success])

  useEffect(() => {
    let cancelled = false
    if (engineState.currentTrack?.id) {
      const trackId = engineState.currentTrack.id

      const loadFeatures = async () => {
        try {
          const cached = await cacheManager.getCachedTrack(trackId)
          let features = null
          let lyrics = null

          if (cached?.audioFeatures) {
            features = cached.audioFeatures
          } else {
            try {
              features = await api.getAudioFeatures(trackId)
            } catch (err) {
              logger.error(`[App] Failed to fetch audio features for ${trackId}:`, err)
            }
          }

          if (cached?.lyricTimestamps) {
            lyrics = cached.lyricTimestamps
          } else {
            try {
              lyrics = await api.getLyricTimestamps(trackId)
            } catch (err) {
              logger.warn(`[App] Failed to fetch lyric timestamps for ${trackId}:`, err)
            }
          }

          if (!cancelled) setTrackData(features, lyrics)

        } catch (err) {
          logger.warn(`[App] Cache lookup failed for ${trackId}:`, err)
          if (!cancelled) setTrackData(null, null)
        }
      }
      void loadFeatures()
    } else {
      setTrackData(null, null)
    }
    return () => { cancelled = true }
  }, [engineState.currentTrack?.id, setTrackData])

  const calculatePanelRegion = useCallback((panelId, windowWidth, windowHeight) => {
    if (isMobile && panelId !== 'player') {
      const mobileViewport = panelId === 'radio' ? document.querySelector('[data-mobile-viewport]') : null
      if (!mobileViewport || mobileViewport.offsetWidth === 0) {
        return { region: { x: 0, y: 0, z: 0, w: 0 }, opacity: 0 }
      }
      const viewportRect = mobileViewport.getBoundingClientRect()
      return {
        region: {
          x: (viewportRect.left + viewportRect.width / 2) / windowWidth,
          y: 1.0 - ((viewportRect.top + viewportRect.height / 2) / windowHeight),
          z: viewportRect.width / windowWidth,
          w: viewportRect.height / windowHeight
        },
        opacity: isFullscreenVisuals ? 0.0 : 1.0
      }
    }

    const findVisiblePanel = () => {
      const panels = document.querySelectorAll(`[data-shader-panel="${panelId}"]`)
      let visiblePanel = null

      panels.forEach(panel => {
        if (panel.offsetWidth > 0 && panel.offsetHeight > 0) {
          if (isMobile) {
            visiblePanel = panel
          } else {
            const rect = panel.getBoundingClientRect()
            const isInViewport = rect.left < windowWidth && rect.right > 0 &&
                                 rect.top < windowHeight && rect.bottom > 0
            if (isInViewport && rect.width > 0 && rect.height > 0) {
              visiblePanel = panel
            }
          }
        }
      })

      return visiblePanel
    }

    const visiblePanel = findVisiblePanel()

    if (!visiblePanel) {
      return { region: { x: 0, y: 0, z: 0, w: 0 }, opacity: 0 }
    }

    const rect = visiblePanel.getBoundingClientRect()

    const centerX = (rect.left + rect.width / 2) / windowWidth

    const centerY = 1.0 - ((rect.top + rect.height / 2) / windowHeight)
    const width = rect.width / windowWidth
    const height = rect.height / windowHeight

    let targetOpacity = 1.0
    if (isFullscreenVisuals) {
      if (panelId === 'player') {
        targetOpacity = showUIControls ? 1.0 : 0.0
      } else {
        targetOpacity = 0.0
      }
    }

    return {
      region: { x: centerX, y: centerY, z: width, w: height },
      opacity: targetOpacity
    }
  }, [isMobile, isFullscreenVisuals, showUIControls])

  useEffect(() => {
    const updateShaderPositions = () => {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          const windowWidth = window.innerWidth
          const windowHeight = window.innerHeight

          const panelOrder = isMobile
            ? ['queue', 'catalog', 'radio', 'nowPlaying', 'user', 'player']
            : ['queue', 'catalog', 'radio', 'nowPlaying', 'user', 'player']

          const regions = []
          const opacities = []

          panelOrder.forEach((panelId) => {
            const { region, opacity } = calculatePanelRegion(panelId, windowWidth, windowHeight)
            regions.push(region)
            opacities.push(opacity)
          })

          if (isMobile && regions.length === 6) {
            regions.push({ x: 0, y: 0, z: 0, w: 0 })
            opacities.push(0)
          }

          updateShaderRegions(regions, opacities)

          const radioButton = document.querySelector('[data-shader-element="radio-button"]')
          if (radioButton && radioButton.offsetWidth > 0 && radioButton.offsetHeight > 0) {
            const rect = radioButton.getBoundingClientRect()
            const baseWidth = radioButton.offsetWidth
            const baseHeight = radioButton.offsetHeight

            let slideOffset = 0
            if (isMobile) {
              const radioPanel = radioButton.closest('[data-shader-panel="radio"]')
              const mobileViewport = document.querySelector('[data-mobile-viewport]')
              if (radioPanel && mobileViewport) {
                slideOffset = radioPanel.getBoundingClientRect().left - mobileViewport.getBoundingClientRect().left
              }
            }

            const centerX = (rect.left - slideOffset + rect.width / 2) / windowWidth
            const centerY = 1.0 - ((rect.top + rect.height / 2) / windowHeight)

            const host = radioButton.parentElement
            const hostScale = host && host.offsetWidth > 0 ? host.getBoundingClientRect().width / host.offsetWidth : 1
            const borderWidth = 4
            const innerWidth = (baseWidth - borderWidth) * hostScale
            const innerHeight = (baseHeight - borderWidth) * hostScale

            const radiusX = (innerWidth / 2) / windowWidth
            const radiusY = (innerHeight / 2) / windowHeight

            updateShaderRadioButtonPos({ x: centerX, y: centerY, radiusX, radiusY })
          }
        })
      })
    }

    setTimeout(updateShaderPositions, 100)
    setTimeout(updateShaderPositions, 350)

    const resizeObserver = new ResizeObserver(() => {
      requestAnimationFrame(updateShaderPositions)
    })

    const appRoot = document.getElementById('root')
    if (appRoot) {
      resizeObserver.observe(appRoot)
    }

    window.addEventListener('resize', updateShaderPositions)

    return () => {
      resizeObserver.disconnect()
      window.removeEventListener('resize', updateShaderPositions)
    }
  }, [panelStates, playerHeight, isFullscreenVisuals, showUIControls, isMobile, isPhoneLandscape, mobilePanel, calculatePanelRegion, updateShaderRegions, updateShaderRadioButtonPos])

  const { contentUpdates, publishContentUpdate } = useUIState()

  useEffect(() => {
    if (contentUpdates.tracks > 0) {
      success('New tracks added!', 5000)
    }
  }, [contentUpdates.tracks, success])

  useEffect(() => {
    if (contentUpdates.shoutouts > 0) {
      success('New shoutout added!', 5000)
    }
  }, [contentUpdates.shoutouts, success])

  const handleLogout = useCallback(() => {
    logout()
    info('You have been logged out')
  }, [logout, info])

  const handleToggleFullscreenVisuals = useCallback(() => {
    const newValue = !interfaceState.isFullscreenVisuals
    reportInterfaceState({
      isFullscreenVisuals: newValue,
      showUIControls: newValue
    })
  }, [interfaceState, reportInterfaceState])

  const uiHideTimeoutRef = useRef(null)

  const handleMouseMove = useCallback(() => {
    if (interfaceRef.current.isFullscreenVisuals) {
      if (!interfaceRef.current.showUIControls) {
        reportInterfaceState({ showUIControls: true })
      }

      if (uiHideTimeoutRef.current) {
        clearTimeout(uiHideTimeoutRef.current)
      }

      uiHideTimeoutRef.current = setTimeout(() => {
        reportInterfaceState({ showUIControls: false })
      }, UI_FULLSCREEN.autoHideDelay)
    }
  }, [interfaceRef, reportInterfaceState])

  useEffect(() => {
    return () => {
      if (uiHideTimeoutRef.current) {
        clearTimeout(uiHideTimeoutRef.current)
      }
    }
  }, [])

  const handleFullscreenClick = useCallback(() => {
    if (interfaceState.isFullscreenVisuals) {
      if (uiHideTimeoutRef.current) {
        clearTimeout(uiHideTimeoutRef.current)
        uiHideTimeoutRef.current = null
      }

      reportInterfaceState({ showUIControls: !interfaceState.showUIControls })
    }
  }, [interfaceState, reportInterfaceState])

  const handlePlayNow = useCallback(async (trackId) => {
    await playTrack(trackId)
  }, [playTrack])

  const handleSeek = useCallback(async (positionMs) => {
    await seek(positionMs)
  }, [seek])

  const { getCategoryMetadata } = useDynamicTheme()

  const handleSeedFromTrack = useCallback(async (trackId, seedMode) => {
    const metadata = getCategoryMetadata(seedMode)
    const modeLabel = metadata?.label || 'All Categories'

    await seedRadio(seedMode, trackId)
    success(`Seeded ${modeLabel} playlist from track`)
  }, [seedRadio, success, getCategoryMetadata])

  useEffect(() => {
    if ('mediaSession' in navigator && currentTrack) {
      const params = currentTrack?.generation_params || {}
      const derivedTags = currentTrack?.derived_tags || {}

      navigator.mediaSession.metadata = new MediaMetadata({
        title: params.title || 'Unknown Track',
        artist: derivedTags?.inspired_artist || 'PLAiR Radio',
        album: 'PLAiR',
        artwork: currentTrack.has_artwork ? [
          { src: currentTrackArtwork, sizes: '512x512', type: 'image/jpeg' }
        ] : []
      })

      navigator.mediaSession.setActionHandler('play', () => void togglePlay())
      navigator.mediaSession.setActionHandler('pause', () => void togglePlay())
      navigator.mediaSession.setActionHandler('previoustrack', () => void previous())
      navigator.mediaSession.setActionHandler('nexttrack', () => void next())
    }
  }, [currentTrack, engineState.is_playing, togglePlay, previous, next, currentTrackArtwork])

  const handleGenerationBatchCompleted = useCallback(async (data) => {
    const trackCount = (data?.tracks) ? data.tracks.length : 0

    if (data?.tracks && data.tracks.length > 0) {
      const trackIds = data.tracks.map(t => t.id || t.track_id).filter(Boolean)
      if (trackIds.length > 0) {
        await addToQueue(trackIds)
      }
    }

    success(`${trackCount} new track${trackCount > 1 ? 's' : ''} added to queue!`, 4000, 'top')
  }, [success, addToQueue])

  const handleGenerationRetrying = useCallback((data) => {
    const maxAttempts = data?.max_attempts ?? '??'
    info(`Generation retry ${data.attempt}/${maxAttempts}...`, 3000, 'top')
  }, [info])

  const handleGenerationBatchFailed = useCallback((data) => {
    if (data?.cancelled || data?.status?.status !== 'processing') return
    const refund = data.refunded ? ' Your generation credit was refunded.' : ''
    errorToast(`One song in your batch failed: ${data.error}${refund}`, 6000, 'top')
  }, [errorToast])

  const handleGenerationJobCompleted = useCallback((data) => {
    const totalTracks = data?.total_tracks || 0
    success(`✓ Generated ${totalTracks} tracks!`, 5000, 'top')
  }, [success])

  const handleCloseLogin = useCallback(() => setShowLogin(false), [])
  const handleCloseRegister = useCallback(() => setShowRegister(false), [])
  const handleCloseSeedModal = useCallback(() => setShowSeedModal(false), [])
  const handleCloseAnalyticsModal = useCallback(() => setShowAnalyticsModal(false), [])
  const handleCloseGenerationModal = useCallback(() => setShowGenerationModal(false), [])
  const handleCloseCompatibilityWarning = useCallback(() => {
    safeStorage.set('plair_compatibility_warning_dismissed', 'true')
    setCompatibilityWarningDismissed(true)
  }, [])

  const handleCloseDemoModal = useCallback(() => {
    safeStorage.set('plair_demo_mode_modal_seen', 'true')
    setDemoModalDismissed(true)
  }, [])

  const handleOpenSeedModal = useCallback(() => {
    const currentTrack = engineState.queue.find(t => t.id === engineState.currentTrack?.id)
    pauseSceneRendering(MODAL_OPEN_PAUSE_MS)
    startTransition(() => {
      setSeedModalTrack(currentTrack)
      setShowSeedModal(true)
    })
  }, [engineState.queue, engineState.currentTrack])

  const handleOpenAnalyticsModal = useCallback(() => {
    pauseSceneRendering(MODAL_OPEN_PAUSE_MS)
    startTransition(() => setShowAnalyticsModal(true))
  }, [])

  const handleOpenGenerationModal = useCallback((track) => {
    pauseSceneRendering(MODAL_OPEN_PAUSE_MS)
    startTransition(() => {
      setGenerationModalTrack(track)
      setShowGenerationModal(true)
    })
  }, [])

  const handleOpenShareModal = useCallback((track) => {
    pauseSceneRendering(MODAL_OPEN_PAUSE_MS)
    startTransition(() => {
      setShareModalTrack(track)
      setShowShareModal(true)
    })
  }, [])

  const handleSeedRadioSelect = useCallback(async (category) => {
    await seedRadio(category)
    handleCloseSeedModal()
  }, [seedRadio, handleCloseSeedModal])

  const handleAnalyticsSelect = useCallback(async (category) => {
    await seedRadio(category)
    handleCloseAnalyticsModal()
  }, [seedRadio, handleCloseAnalyticsModal])

  const handleGenerateJobs = useCallback(async (jobs) => {
    if (!generationModalTrack || queueState.hasActiveJobs || jobs.length === 0) {
      handleCloseGenerationModal()
      return
    }

    try {
      const params = generationModalTrack.generation_params || {}

      for (const job of jobs) {
        let response

        if (job.type === 'remix') {
          response = await api.generate({
            type: 'similar',
            sourceTrackId: generationModalTrack.id,
            batchCount: 1
          })

          if (response.job_ids && Array.isArray(response.job_ids)) {
            response.job_ids.forEach((jobId) => {
              addJob({
                job_id: jobId,
                type: 'similar',
                query: `Remix of "${params.title || 'Untitled'}"`,
                total_tracks: 2
              })
            })
          }
        } else if (job.type === 'artist') {
          response = await api.generate({
            type: 'new',
            userRequest: job.artistName,
            batchCount: 1
          })

          if (response.job_ids && Array.isArray(response.job_ids)) {
            response.job_ids.forEach((jobId) => {
              addJob({
                job_id: jobId,
                type: 'new',
                query: job.artistName,
                total_tracks: 2
              })
            })
          }
        } else if (job.type === 'similar_artist') {
          response = await api.generate({
            type: 'new',
            userRequest: job.artistName,
            batchCount: 1
          })

          if (response.job_ids && Array.isArray(response.job_ids)) {
            response.job_ids.forEach((jobId) => {
              addJob({
                job_id: jobId,
                type: 'new',
                query: `${job.artistName} (similar to ${params.artist_name || 'current artist'})`,
                total_tracks: 2
              })
            })
          }
        }
      }

      setQueueOpen(true)
      success(`Started ${jobs.length} generation job${jobs.length > 1 ? 's' : ''}!`, 3000)
    } catch (error) {
      logger.error('[App] Error generating tracks:', error)
      errorToast(`Failed to start generation: ${error.message}`, 5000)
    }

    handleCloseGenerationModal()
  }, [generationModalTrack, queueState.hasActiveJobs, addJob, setQueueOpen, success, errorToast, handleCloseGenerationModal])

  useWebSocketSubscribe('generation_batch_completed', handleGenerationBatchCompleted)
  useWebSocketSubscribe('generation_retrying', handleGenerationRetrying)
  useWebSocketSubscribe('generation_batch_failed', handleGenerationBatchFailed)
  useWebSocketSubscribe('generation_job_completed', handleGenerationJobCompleted)
  useWebSocketSubscribe('generation_stage_update', () => {})
  useWebSocketSubscribe('content_updated', (data) => {
    const { content_type } = data
    if (content_type && (content_type === 'track' || content_type === 'shoutout')) {
      const normalizedType = content_type === 'track' ? 'tracks' : 'shoutouts'
      publishContentUpdate(normalizedType)
    }
  })

  useWebSocketSubscribe('user_settings_updated', (data) => {
    logger.info('[App] 📢 User settings updated from another device:', data)
    publishSettings(data)
  })

  const QueuePanel = useMemo(() => (
    <Queue
      onSeedRadio={handleOpenSeedModal}
      onAnalytics={handleOpenAnalyticsModal}
    />
  ), [handleOpenSeedModal, handleOpenAnalyticsModal])

  const LibraryPanel = useMemo(() => (
    <Catalog
      onPlayNow={handlePlayNow}
      onSeedFromTrack={handleSeedFromTrack}
    />
  ), [handlePlayNow, handleSeedFromTrack])

  const NowPlayingPanel = useMemo(() => (
    <NowPlaying
      onToggleFullscreen={handleToggleFullscreenVisuals}
      onOpenGenerationModal={handleOpenGenerationModal}
      onOpenShareModal={handleOpenShareModal}
    />
  ), [handleToggleFullscreenVisuals, handleOpenGenerationModal, handleOpenShareModal])

  const UserPanel = useMemo(() => (
    <User
      onLogin={() => setShowLogin(true)}
      onRegister={() => setShowRegister(true)}
      onLogout={handleLogout}
      onPlayTrack={handlePlayNow}
      onReloadTrackQuality={reloadCurrentTrackQuality}
    />
  ), [handleLogout, handlePlayNow, reloadCurrentTrackQuality])

  const RadioPanel = useMemo(() => <Radio />, [])

  const ShoutoutsPanel = useMemo(() => <Shoutouts />, [])

  const CatalogPanel = useMemo(() => (
    <AnimatePresence mode="wait" initial={false}>
      <motion.div key={catalogView} className="h-full" {...PRESETS.panelSwap}>
        {catalogView === 'tracks' ? LibraryPanel : ShoutoutsPanel}
      </motion.div>
    </AnimatePresence>
  ), [catalogView, LibraryPanel, ShoutoutsPanel])

  const panelStructure = useMemo(() => {
      return [
        {id: PANEL_IDS.QUEUE, content: QueuePanel, stateKey: 'queue'},
        {id: PANEL_IDS.CATALOG, content: CatalogPanel, stateKey: 'catalog', mobileClass: 'flex flex-col'},
        {id: PANEL_IDS.RADIO, content: RadioPanel, stateKey: 'radio'},
        {id: PANEL_IDS.NOW_PLAYING, content: NowPlayingPanel, stateKey: 'nowPlaying'},
        {id: PANEL_IDS.USER, content: UserPanel, stateKey: 'user'}
    ]
  }, [QueuePanel, CatalogPanel, RadioPanel, NowPlayingPanel, UserPanel])

  return (
    <DialogProvider>
      <VoiceRecordingProvider mixerRef={audio?.mixerRef}>
        <DJVoiceEngine />
        <KeyboardControls
        showLogin={showLogin}
        showRegister={showRegister}
        onCloseLogin={() => setShowLogin(false)}
        onCloseRegister={() => setShowRegister(false)}
      />
      <div
        className="h-full flex flex-col overflow-hidden bg-dark-bg relative"
        data-layout={isPhoneLandscape ? 'phone-landscape' : isMobile ? 'mobile' : 'desktop'}
        onMouseMove={handleMouseMove}
        onClick={handleFullscreenClick}
      >
        <Suspense fallback={canvasFallback}>
          <AudioReactiveCanvas />
        </Suspense>

        <AnimatePresence>
          {!isFullscreenVisuals && <ToastContainer />}
        </AnimatePresence>

        <OnAirFrame />

        <AnimatePresence>
          {isFullscreenVisuals && showUIControls && (
            <motion.button
              initial={{ opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.8 }}
              transition={TRANSITIONS.fade}
              whileHover={PRESETS.hoverPress.whileHover}
              whileTap={PRESETS.hoverPress.whileTap}
              onClick={handleToggleFullscreenVisuals}
              className={`fixed z-50 bg-black/80 hover:bg-black/90 text-white rounded-full shadow-2xl transition-colors ${isPhoneLandscape ? 'p-3' : 'p-4'}`}
              style={FULLSCREEN_EXIT_STYLE}
              title="Exit fullscreen visuals"
              aria-label="Exit fullscreen visuals"
            >
              <svg className={isPhoneLandscape ? 'h-6 w-6' : 'h-8 w-8'} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </motion.button>
          )}
        </AnimatePresence>

        <AnimatePresence mode="wait">
          {!isMobile && (
            <motion.div
              key="desktop-panels"
              initial={{ opacity: 0 }}
              animate={{ opacity: isFullscreenVisuals ? 0 : 1 }}
              transition={PANEL_FADE_TRANSITION}
              className="flex flex-1 gap-4 p-4 relative z-10 overflow-hidden"
              style={{
                paddingBottom: `${playerHeight + 16}px`,
                ...getPanelPointerEvents(!isFullscreenVisuals)
              }}
            >
              {panelStructure.map(({ id, content, stateKey }) => (
                <Panel
                  key={id}
                  {...PANEL_CONFIG[id]}
                  isOpen={panelStates[stateKey]}
                  onToggle={() => setPanelStates(prev => ({ ...prev, [stateKey]: !prev[stateKey] }))}
                >
                  {id === PANEL_IDS.RADIO ? <div className="h-full overflow-y-auto">{content}</div> : content}
                </Panel>
              ))}
            </motion.div>
          )}
        </AnimatePresence>

        <AnimatePresence mode="wait">
          {isMobile && (
            <motion.div
              key="mobile-panels"
              initial={{ opacity: 0 }}
              animate={{ opacity: isFullscreenVisuals ? 0 : 1 }}
              transition={PANEL_FADE_TRANSITION}
              className={`fixed inset-x-0 top-0 flex z-10 ${isPhoneLandscape ? 'flex-row' : 'flex-col'}`}
              style={{
                bottom: `${playerHeight}px`,
                paddingTop: 'var(--safe-top)',
                paddingRight: isPhoneLandscape ? 'var(--safe-right)' : undefined,
                ...getPanelPointerEvents(!isFullscreenVisuals)
              }}
            >
              <div className="flex-1 min-w-0 min-h-0 overflow-hidden relative" data-mobile-viewport>
                <motion.div
                  key="mobile-panel-slider"
                  className="flex h-full"
                  animate={{ x: `-${mobilePanel * 100}%` }}
                  transition={MOTION.spring}
                  onAnimationStart={() => setIsPanelAnimating(true)}
                  onAnimationComplete={() => setIsPanelAnimating(false)}
                >
                  {panelStructure.map(({ id, content, stateKey, mobileClass }) => (
                    <div
                      key={id}
                      data-shader-panel={stateKey}
                      className={`w-full h-full flex-shrink-0 border-x border-white/10 ${mobileClass || 'overflow-hidden'}`}
                    >
                      <div className="h-full overflow-y-auto">
                        {content}
                      </div>
                    </div>
                  ))}
                </motion.div>
              </div>

              <nav
                data-mobile-nav
                aria-label="Panels"
                className={isPhoneLandscape ? MOBILE_RAIL_CLASS : MOBILE_NAV_CLASS}
                style={isPhoneLandscape ? MOBILE_RAIL_STYLE : MOBILE_NAV_MASK_STYLE}
              >
                {panelStructure.map(({ id }, index) => {
                  const config = PANEL_CONFIG[id]
                  const isActive = mobilePanel === index

                  const handleClick = () => {
                    if (isPanelAnimating) return

                    if (id === PANEL_IDS.CATALOG && isActive) {
                      toggleCatalogView()
                    } else {
                      setMobilePanel(index)
                    }
                  }

                  return (
                    <button
                      key={id}
                      onClick={handleClick}
                      disabled={isPanelAnimating}
                      aria-current={isActive ? 'page' : undefined}
                      className={`ui-tap relative flex flex-col items-center justify-center flex-1 min-h-0 transition-colors ${isPhoneLandscape ? 'w-full' : 'h-full'}`}
                      style={{
                        color: isActive ? 'white' : getAccentColor(0.85),
                        textShadow: isActive ? 'none' : `0 0 8px ${getAccentColor(0.6)}`,
                        filter: isActive ? 'none' : 'brightness(1.3)'
                      }}
                    >
                      {isActive && (
                        <motion.span
                          layoutId="mobile-nav-indicator"
                          className={`absolute rounded-full pointer-events-none bg-current opacity-80 ${isPhoneLandscape ? 'left-1 top-1/2 -mt-3 h-6 w-0.5' : 'top-1 left-1/2 -ml-3 w-6 h-0.5'}`}
                          transition={MOTION.spring}
                        />
                      )}
                      {id === PANEL_IDS.USER && isAuthenticated ? (
                        <>
                          {userProfilePicture ? (
                            <img decoding="async"
                              src={userProfilePicture}
                              alt={user?.username || 'User'}
                              className={`${navIconClass} rounded-full object-cover`}
                            />
                          ) : (
                            <div className={`${navIconClass} rounded-full bg-purple-600 flex items-center justify-center text-white text-xs font-bold`}>
                              {user?.username?.charAt(0).toUpperCase() || 'U'}
                            </div>
                          )}
                          <span className={`truncate max-w-[60px] ${isPhoneLandscape ? 'text-[10px]' : 'text-xs'}`}>{user?.username || 'User'}</span>
                        </>
                      ) : (
                        <>
                          {id === PANEL_IDS.CATALOG && isMobile && isActive && catalogView === 'tracks'
                            ? PANEL_CONFIG[PANEL_IDS.SHOUTOUTS].mobileIcon(navIconClass)
                            : config.mobileIcon(navIconClass)
                          }
                          <span className={isPhoneLandscape ? 'text-[10px] leading-tight' : 'text-xs'}>
                            {id === PANEL_IDS.CATALOG && isMobile && isActive && catalogView === 'tracks'
                              ? PANEL_CONFIG[PANEL_IDS.SHOUTOUTS].mobileLabel
                              : config.mobileLabel
                            }
                          </span>
                        </>
                      )}
                    </button>
                  )
                })}
              </nav>
            </motion.div>
          )}
        </AnimatePresence>

        <motion.div
          data-shader-panel="player"
          animate={{
            opacity: (isFullscreenVisuals && !showUIControls) ? 0 : 1
          }}
          transition={PANEL_FADE_TRANSITION}
          className="relative z-20"
          style={getPanelPointerEvents(!(isFullscreenVisuals && !showUIControls))}
        >
          <Player
            onSeek={handleSeek}
            onArtworkClick={handleToggleFullscreenVisuals}
          />
        </motion.div>

        <AnimatePresence>
          {showLogin && (
            <Login
              key="login"
              onClose={handleCloseLogin}
              onSwitchToRegister={() => {
                handleCloseLogin()
                setShowRegister(true)
              }}
            />
          )}

          {showRegister && (
            <Register
              key="register"
              onClose={handleCloseRegister}
              onSwitchToLogin={() => {
                handleCloseRegister()
                setShowLogin(true)
              }}
            />
          )}
        </AnimatePresence>

        <LazyMount when={showSeedModal}>
          <SeedRadioModal
            isOpen={showSeedModal}
            onClose={handleCloseSeedModal}
            onSelect={handleSeedRadioSelect}
            track={seedModalTrack}
          />
        </LazyMount>

        <LazyMount when={showAnalyticsModal}>
          <TrackAnalyticsModal
            isOpen={showAnalyticsModal}
            onClose={handleCloseAnalyticsModal}
            onSelect={handleAnalyticsSelect}
          />
        </LazyMount>

        <LazyMount when={shoutoutModalState.isOpen}>
          <ShoutoutModal
            isOpen={shoutoutModalState.isOpen}
            onClose={closeShoutoutModal}
            shoutout={shoutoutModalState.shoutout}
          />
        </LazyMount>

        <LazyMount when={showGenerationModal}>
          <GenerationModal
            isOpen={showGenerationModal}
            onClose={handleCloseGenerationModal}
            track={generationModalTrack}
            onGenerate={handleGenerateJobs}
          />
        </LazyMount>

        <LazyMount when={showCompatibilityWarning}>
          <CompatibilityWarningModal
            isOpen={showCompatibilityWarning}
            onClose={handleCloseCompatibilityWarning}
          />
        </LazyMount>

        <LazyMount when={showDemoModal}>
          <DemoModeModal
            isOpen={showDemoModal}
            onClose={handleCloseDemoModal}
          />
        </LazyMount>

        <LazyMount when={showShareModal}>
          <ShareModal
            isOpen={showShareModal}
            onClose={() => setShowShareModal(false)}
            track={shareModalTrack}
          />
        </LazyMount>

        <LazyMount when={uploadModalOpen}>
          <UploadMusicModal
            isOpen={uploadModalOpen}
            onClose={closeUploadModal}
            onLogin={() => setShowLogin(true)}
          />
        </LazyMount>

        <LazyMount when={usageModalOpen && Boolean(user?.is_admin || user?.usage_stats_visible)}>
          <UsageStatsModal
            isOpen={usageModalOpen}
            onClose={closeUsageModal}
            isAdmin={Boolean(user?.is_admin)}
          />
        </LazyMount>

        {settingsState.fpsEnabled && <FPSCounter />}
        <LazyMount when={settingsState.costTickerEnabled && Boolean(user?.is_admin)}>
          {settingsState.costTickerEnabled && user?.is_admin && <CostTicker />}
        </LazyMount>
      </div>
      </VoiceRecordingProvider>
    </DialogProvider>
  )
}

export default App
