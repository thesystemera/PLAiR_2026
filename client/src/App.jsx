import {useCallback, useEffect, useMemo, useRef, useState} from 'react'
import {AnimatePresence, motion} from 'framer-motion'
import {api} from './lib/api'
import {cacheManager} from './lib/cacheManager'
import {logger} from './lib/logger'
import {useArtwork, useUIState} from './contexts/UIStateContext'
import {useProfilePicture} from './hooks/useProfilePicture'
import {usePlayback} from './contexts/PlaybackContext'
import {useAuth} from './contexts/AuthContext'
import {TRANSITIONS, UI_FULLSCREEN, useDynamicTheme} from './contexts/DynamicThemeContext'
import {useWebSocketSubscribe} from './contexts/WebSocketContext'
import {VoiceRecordingProvider} from './contexts/VoiceRecordingContext'
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
    PANEL_IDS,
    PanelHeader
} from './components/Panel'
import Login from './components/Auth/Login'
import Register from './components/Auth/Register'
import ToastContainer from './components/Toast'
import {AudioReactiveCanvas} from './components/AudioReactiveCanvas'
import {SeedRadioModal} from './components/modals/SeedRadioModal'
import {TrackAnalyticsModal} from './components/modals/TrackAnalyticsModal'
import {ShoutoutModal} from './components/modals/ShoutoutModal'
import {GenerationModal} from './components/modals/GenerationModal'
import {CompatibilityWarningModal} from './components/modals/CompatibilityWarningModal'
import {DemoModeModal} from './components/modals/DemoModeModal'
import {ShareModal} from './components/modals/ShareModal'
import {UploadMusicModal} from './components/modals/UploadMusicModal'
import {FPSCounter} from './components/FPSCounter'
import {KeyboardControls} from './components/KeyboardControls'

// DEBUG: Global RAF counter - components register and report
if (typeof window !== 'undefined' && !window.__rafDebug) {
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
  const [playerHeight, setPlayerHeight] = useState(80)
  const [showLogin, setShowLogin] = useState(false)
  const [showRegister, setShowRegister] = useState(false)
  const [showSeedModal, setShowSeedModal] = useState(false)
  const [seedModalTrack, setSeedModalTrack] = useState(null)
  const [showAnalyticsModal, setShowAnalyticsModal] = useState(false)
  const [showGenerationModal, setShowGenerationModal] = useState(false)
  const [generationModalTrack, setGenerationModalTrack] = useState(null)
  const [showCompatibilityWarning, setShowCompatibilityWarning] = useState(false)
  const [showDemoModal, setShowDemoModal] = useState(false)
  const [showShareModal, setShowShareModal] = useState(false)
  const [shareModalTrack, setShareModalTrack] = useState(null)
  const [mobilePanel, setMobilePanel] = useState(2)
  const [isPanelAnimating, setIsPanelAnimating] = useState(false)
  const [panelStates, setPanelStates] = useState({
    queue: true,
    catalog: true,
    radio: true,
    nowPlaying: true,
    user: true
  })

  const playback = usePlayback()
  const { user, isAuthenticated, logout } = useAuth()
  const { getAccentColor } = useDynamicTheme()
  const { setTrackData, updateShaderRegions, updateShaderRadioButtonPos, engineState, publishSettings, settingsState, toastSuccess, toastInfo, toastError, interfaceState, reportInterfaceState, shoutoutModalState, closeShoutoutModal, queueState, uploadModalOpen, closeUploadModal } = useUIState()
  const { addJob, setIsOpen: setQueueOpen } = useGenerationQueue()

  const catalogView = interfaceState.catalogView
  const toggleCatalogView = useCallback(() => {
    reportInterfaceState({ catalogView: catalogView === 'tracks' ? 'shoutouts' : 'tracks' })
  }, [catalogView, reportInterfaceState])

  const success = toastSuccess
  const info = toastInfo
  const errorToast = toastError

  const isFullscreenVisuals = interfaceState.isFullscreenVisuals
  const showUIControls = interfaceState.showUIControls
  const [wasConnected, setWasConnected] = useState(false)

  const currentTrack = engineState.currentTrack
  const currentTrackArtwork = useArtwork(currentTrack?.id, currentTrack?.has_artwork)

  useGeolocation(isAuthenticated)

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

  // Sync FPS debug flag
  useEffect(() => {
    if (window.__rafDebug) {
      window.__rafDebug.enabled = settingsState.fpsEnabled
    }
  }, [settingsState.fpsEnabled])

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
          await playback.playTrack(trackId)
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
  }, [playback, toastError])

  const { isMobile, isCompatible } = useViewport()
  const userProfilePicture = useProfilePicture(user?.id, !!user?.profile_picture)

  useEffect(() => {
    const STORAGE_KEY = 'plair_compatibility_warning_dismissed'
    const hasSeenWarning = localStorage.getItem(STORAGE_KEY)

    if (!isCompatible && !hasSeenWarning) {
      setShowCompatibilityWarning(true)
    }
  }, [isCompatible])

  useEffect(() => {
    const STORAGE_KEY = 'plair_demo_mode_modal_seen'
    const hasSeenDemo = localStorage.getItem(STORAGE_KEY)
    const isGuest = !user

    if (isGuest && !hasSeenDemo) {
      setShowDemoModal(true)
    }
  }, [user])

  useEffect(() => {
    if (!playback.connected && wasConnected) {
      errorToast('Disconnected from server. Attempting to reconnect...', 8000)
      setWasConnected(false)
    } else if (playback.connected && !wasConnected) {
      success('Connected to server', 4000)
      setWasConnected(true)
    }
  }, [playback.connected, wasConnected, errorToast, success])

  useEffect(() => {
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

          setTrackData(features, lyrics)

        } catch (err) {
          logger.warn(`[App] Cache lookup failed for ${trackId}:`, err)
          setTrackData(null, null)
        }
      }
      void loadFeatures()
    } else {
      setTrackData(null, null)
    }
  }, [engineState.currentTrack?.id, setTrackData])

  const calculatePanelRegion = useCallback((panelId, windowWidth, windowHeight) => {
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

    const centerX = (isMobile && panelId === 'radio')
      ? 0.5
      : (rect.left + rect.width / 2) / windowWidth

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

            const centerX = (rect.left + rect.width / 2) / windowWidth
            const centerY = 1.0 - ((rect.top + rect.height / 2) / windowHeight)

            const borderWidth = 4
            const innerWidth = baseWidth - borderWidth
            const innerHeight = baseHeight - borderWidth

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
  }, [panelStates, playerHeight, isFullscreenVisuals, showUIControls, isMobile, calculatePanelRegion, updateShaderRegions, updateShaderRadioButtonPos])

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
    if (interfaceState.isFullscreenVisuals) {
      reportInterfaceState({ showUIControls: true })

      if (uiHideTimeoutRef.current) {
        clearTimeout(uiHideTimeoutRef.current)
      }

      uiHideTimeoutRef.current = setTimeout(() => {
        reportInterfaceState({ showUIControls: false })
      }, UI_FULLSCREEN.autoHideDelay)
    }
  }, [interfaceState, reportInterfaceState])

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
    await playback.playTrack(trackId)
  }, [playback])

  const handleSeek = useCallback(async (positionMs) => {
    await playback.seek(positionMs)
  }, [playback])

  const { radioState } = useUIState()
  const { getCategoryMetadata } = useDynamicTheme()

  const handleSeedFromTrack = useCallback(async (trackId, seedMode) => {
    const metadata = getCategoryMetadata(seedMode)
    const modeLabel = metadata?.label || 'All Categories'

    await playback.seedRadio(seedMode, trackId)
    success(`Seeded ${modeLabel} playlist from track`)
  }, [playback, success, radioState, getCategoryMetadata])

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

      navigator.mediaSession.setActionHandler('play', () => void playback.togglePlay())
      navigator.mediaSession.setActionHandler('pause', () => void playback.togglePlay())
      navigator.mediaSession.setActionHandler('previoustrack', () => void playback.previous())
      navigator.mediaSession.setActionHandler('nexttrack', () => void playback.next())
    }
  }, [currentTrack, engineState.is_playing, playback.togglePlay, playback.previous, playback.next, currentTrackArtwork])

  const handleGenerationBatchCompleted = useCallback(async (data) => {
    const trackCount = (data?.tracks) ? data.tracks.length : 0

    if (data?.tracks && data.tracks.length > 0) {
      const trackIds = data.tracks.map(t => t.id || t.track_id).filter(Boolean)
      if (trackIds.length > 0) {
        await playback.addToQueue(trackIds)
      }
    }

    success(`${trackCount} new track${trackCount > 1 ? 's' : ''} added to queue!`, 4000, 'top')
  }, [success, playback])

  const handleGenerationRetrying = useCallback((data) => {
    const maxAttempts = data?.max_attempts ?? '??'
    info(`Generation retry ${data.attempt}/${maxAttempts}...`, 3000, 'top')
  }, [info])

  const handleGenerationBatchFailed = useCallback((data) => {
    errorToast(`Generation batch failed: ${data.error}`, 5000, 'top')
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
    localStorage.setItem('plair_compatibility_warning_dismissed', 'true')
    setShowCompatibilityWarning(false)
  }, [])

  const handleCloseDemoModal = useCallback(() => {
    localStorage.setItem('plair_demo_mode_modal_seen', 'true')
    setShowDemoModal(false)
  }, [])

  const handleOpenSeedModal = useCallback(() => {
    const currentTrack = engineState.queue.find(t => t.id === engineState.currentTrack?.id)
    setSeedModalTrack(currentTrack)
    setShowSeedModal(true)
  }, [engineState.queue, engineState.currentTrack])

  const handleOpenAnalyticsModal = useCallback(() => {
    setShowAnalyticsModal(true)
  }, [])

  const handleOpenGenerationModal = useCallback((track) => {
    setGenerationModalTrack(track)
    setShowGenerationModal(true)
  }, [])

  const handleOpenShareModal = useCallback((track) => {
    setShareModalTrack(track)
    setShowShareModal(true)
  }, [])

  const handleSeedRadioSelect = useCallback(async (category) => {
    await playback.seedRadio(category)
    handleCloseSeedModal()
  }, [playback, handleCloseSeedModal])

  const handleAnalyticsSelect = useCallback(async (category) => {
    await playback.seedRadio(category)
    handleCloseAnalyticsModal()
  }, [playback, handleCloseAnalyticsModal])

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
      contentUpdateCounter={contentUpdates.tracks}
      onToggleView={toggleCatalogView}
      currentView={catalogView}
    />
  ), [handlePlayNow, handleSeedFromTrack, contentUpdates.tracks, toggleCatalogView, catalogView])

  const NowPlayingPanel = useMemo(() => engineState.currentTrack ? (
    <NowPlaying
      track={engineState.currentTrack}
      onToggleFullscreen={handleToggleFullscreenVisuals}
      onOpenGenerationModal={handleOpenGenerationModal}
      onOpenShareModal={handleOpenShareModal}
    />
  ) : (
    <div className="flex flex-col h-full">
      <PanelHeader title="Now Playing" />
      <div className="flex-1 flex items-center justify-center text-gray-400">
        No track playing
      </div>
    </div>
  ), [engineState.currentTrack, handleToggleFullscreenVisuals, handleOpenGenerationModal, handleOpenShareModal])

  const UserPanel = useMemo(() => (
    <User
      onLogin={() => setShowLogin(true)}
      onRegister={() => setShowRegister(true)}
      onLogout={handleLogout}
      onPlayTrack={handlePlayNow}
      onReloadTrackQuality={playback.reloadCurrentTrackQuality}
    />
  ), [handleLogout, handlePlayNow, playback.reloadCurrentTrackQuality])

  const handlePlayerHeightChange = useCallback((height) => {
    setPlayerHeight(height)
  }, [])

  const RadioPanel = useMemo(() => (
    <Radio
      mobilePanel={mobilePanel}
      playerHeight={playerHeight}
    />
  ), [mobilePanel, playerHeight])

  const ShoutoutsPanel = useMemo(() => (
    <Shoutouts
      onToggleView={toggleCatalogView}
      currentView={catalogView}
    />
  ), [toggleCatalogView, catalogView])

  const CatalogPanel = useMemo(() => (
    catalogView === 'tracks' ? LibraryPanel : ShoutoutsPanel
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
      <VoiceRecordingProvider mixerRef={playback.audio?.mixerRef}>
        <KeyboardControls
        showLogin={showLogin}
        showRegister={showRegister}
        onCloseLogin={() => setShowLogin(false)}
        onCloseRegister={() => setShowRegister(false)}
      />
      <div
        className="h-screen flex flex-col overflow-hidden bg-dark-bg relative"
        style={{
          maxWidth: isMobile && !isFullscreenVisuals ? '100vh' : 'none',
          margin: isMobile && !isFullscreenVisuals ? '0 auto' : '0'
        }}
        onMouseMove={handleMouseMove}
        onClick={handleFullscreenClick}
      >
        <AudioReactiveCanvas />

        <AnimatePresence>
          {!isFullscreenVisuals && <ToastContainer />}
        </AnimatePresence>

        <AnimatePresence>
          {isFullscreenVisuals && showUIControls && (
            <motion.button
              initial={{ opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.8 }}
              transition={TRANSITIONS.fade}
              onClick={handleToggleFullscreenVisuals}
              className="fixed top-6 right-6 z-50 bg-black/80 hover:bg-black/90 text-white p-4 rounded-full shadow-2xl transition-all hover:scale-110"
              title="Exit fullscreen visuals"
            >
              <svg className="h-8 w-8" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
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
              className="fixed inset-x-0 top-0 flex flex-col z-10"
              style={{
                bottom: `${playerHeight}px`,
                ...getPanelPointerEvents(!isFullscreenVisuals)
              }}
            >
              <div className="flex-1 overflow-hidden relative">
                <motion.div
                  key="mobile-panel-slider"
                  className="flex h-full"
                  animate={{ x: `-${mobilePanel * 100}vw` }}
                  transition={{ type: 'spring', stiffness: 300, damping: 30 }}
                  onAnimationStart={() => setIsPanelAnimating(true)}
                  onAnimationComplete={() => setIsPanelAnimating(false)}
                >
                  {panelStructure.map(({ id, content, stateKey, mobileClass }) => (
                    <div
                      key={id}
                      data-shader-panel={stateKey}
                      className={`w-screen h-full flex-shrink-0 border-x border-white/10 ${mobileClass || 'overflow-hidden'}`}
                    >
                      <div className="h-full overflow-y-auto">
                        {content}
                      </div>
                    </div>
                  ))}
                </motion.div>
              </div>

              <div
                className="flex-shrink-0 bg-black/25 border-t border-gray-800/50 flex justify-around items-center h-16 z-20"
                style={{
                  maskImage: 'linear-gradient(to bottom, transparent 0%, black 4px, black calc(100% - 4px), transparent 100%)',
                  WebkitMaskImage: 'linear-gradient(to bottom, transparent 0%, black 4px, black calc(100% - 4px), transparent 100%)'
                }}
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
                      className={`flex flex-col items-center justify-center flex-1 h-full transition ${
                        isPanelAnimating ? 'opacity-50 cursor-not-allowed' : ''
                      }`}
                      style={{
                        color: isActive ? 'white' : getAccentColor(0.85),
                        textShadow: isActive ? 'none' : `0 0 8px ${getAccentColor(0.6)}`,
                        filter: isActive ? 'none' : 'brightness(1.3)'
                      }}
                    >
                      {id === PANEL_IDS.USER && isAuthenticated ? (
                        <>
                          {userProfilePicture ? (
                            <img
                              src={userProfilePicture}
                              alt={user?.username || 'User'}
                              className="w-6 h-6 mb-1 rounded-full object-cover"
                            />
                          ) : (
                            <div className="w-6 h-6 mb-1 rounded-full bg-purple-600 flex items-center justify-center text-white text-xs font-bold">
                              {user?.username?.charAt(0).toUpperCase() || 'U'}
                            </div>
                          )}
                          <span className="text-xs truncate max-w-[60px]">{user?.username || 'User'}</span>
                        </>
                      ) : (
                        <>
                          {id === PANEL_IDS.CATALOG && isMobile && isActive && catalogView === 'tracks'
                            ? PANEL_CONFIG[PANEL_IDS.SHOUTOUTS].mobileIcon()
                            : config.mobileIcon()
                          }
                          <span className="text-xs">
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
              </div>
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
            onHeightChange={handlePlayerHeightChange}
            onArtworkClick={handleToggleFullscreenVisuals}
          />
        </motion.div>

        {showLogin && (
          <Login
            onClose={handleCloseLogin}
            onSwitchToRegister={() => {
              handleCloseLogin()
              setShowRegister(true)
            }}
          />
        )}

        {showRegister && (
          <Register
            onClose={handleCloseRegister}
            onSwitchToLogin={() => {
              handleCloseRegister()
              setShowLogin(true)
            }}
          />
        )}

        <SeedRadioModal
          isOpen={showSeedModal}
          onClose={handleCloseSeedModal}
          onSelect={handleSeedRadioSelect}
          track={seedModalTrack}
        />

        <TrackAnalyticsModal
          isOpen={showAnalyticsModal}
          onClose={handleCloseAnalyticsModal}
          onSelect={handleAnalyticsSelect}
        />

        <ShoutoutModal
          isOpen={shoutoutModalState.isOpen}
          onClose={closeShoutoutModal}
          shoutout={shoutoutModalState.shoutout}
        />

        <GenerationModal
          isOpen={showGenerationModal}
          onClose={handleCloseGenerationModal}
          track={generationModalTrack}
          onGenerate={handleGenerateJobs}
        />

        <CompatibilityWarningModal
          isOpen={showCompatibilityWarning}
          onClose={handleCloseCompatibilityWarning}
        />

        <DemoModeModal
          isOpen={showDemoModal}
          onClose={handleCloseDemoModal}
        />

        <ShareModal
          isOpen={showShareModal}
          onClose={() => setShowShareModal(false)}
          track={shareModalTrack}
        />

        <UploadMusicModal
          isOpen={uploadModalOpen}
          onClose={closeUploadModal}
        />

        {settingsState.fpsEnabled && <FPSCounter />}
      </div>
      </VoiceRecordingProvider>
    </DialogProvider>
  )
}

export default App