import { logger } from '../lib/logger'
import { createContext, useContext, useState, useCallback, useMemo, useRef, useEffect } from 'react'
import { useFFTProcessor } from '../hooks/useFFTProcessor'
import { useAuth } from './AuthContext'
import { useUIState } from './UIStateContext'
import { usePlayback } from './PlaybackContext'
import { api } from '../lib/api'
import { AudioInteractionManager } from '../lib/audioInteractionManager'

function createShoutoutElement() {
  const element = new Audio()
  element.crossOrigin = 'anonymous'
  element.preload = 'auto'
  element.setAttribute('playsinline', '')
  return element
}

const PlaybackShoutoutContext = createContext(null)
const ShoutoutProgressContext = createContext(0)

const PROGRESS_INTERVAL_MS = 50

export function PlaybackShoutoutProvider({ children }) {
  const [playingShoutout, setPlayingShoutout] = useState(null)
  const [progress, setProgress] = useState(0)
  const { audio } = usePlayback()
  const { user } = useAuth()
  const userId = user?.id || null
  const { reportEngineStatus, openShoutoutModal } = useUIState()

  const playStartTimeRef = useRef(null)
  const currentShoutoutRef = useRef(null)
  const playerRef = useRef(null)
  const playTokenRef = useRef(0)

  const [analyser, setAnalyser] = useState(null)
  const [initialElement] = useState(() => (typeof Audio !== 'undefined' ? createShoutoutElement() : null))
  const initialElementBoundRef = useRef(false)

  useEffect(() => AudioInteractionManager.registerMediaElement(initialElement), [initialElement])

  useFFTProcessor(
    !!playingShoutout,
    analyser,
    'shoutoutFftData',
    { processingMode: 'logarithmic' }
  )

  const logShoutoutStart = useCallback(async (shoutoutId) => {
    try {
      await api.trackShoutoutPlay({
        shoutout_id: shoutoutId,
        user_id: userId,
        event_type: 'play'
      })
      logger.info(`[PlaybackShoutout] Logged play start for shoutout ${shoutoutId}`)
    } catch (error) {
      logger.error('[PlaybackShoutout] Failed to log play start:', error)
    }
  }, [userId])

  const logShoutoutEnd = useCallback(async (shoutoutId, completed = true) => {
    if (!playStartTimeRef.current) return

    const durationMs = Date.now() - playStartTimeRef.current
    const shoutout = currentShoutoutRef.current

    let completionPct = completed ? 100 : 0
    let totalDuration = 0

    if (shoutout?.word_level_transcription?.length > 0) {
      const words = shoutout.word_level_transcription
      const firstWord = words[0]
      const lastWord = words[words.length - 1]
      totalDuration = ((lastWord.end || 0) - (firstWord.start || 0)) * 1000
    } else if (shoutout?.transcription_metadata?.duration) {
      totalDuration = shoutout.transcription_metadata.duration * 1000
    } else if (shoutout?.metadata?.duration) {
      totalDuration = shoutout.metadata.duration * 1000
    }

    if (totalDuration > 0) {
      completionPct = Math.min(100, (durationMs / totalDuration) * 100)
    }

    playStartTimeRef.current = null
    currentShoutoutRef.current = null

    try {
      await api.trackShoutoutPlay({
        shoutout_id: shoutoutId,
        user_id: userId,
        event_type: completed ? 'complete' : 'skip',
        duration_ms: durationMs,
        completion_pct: completionPct
      })
      logger.info(`[PlaybackShoutout] Logged ${completed ? 'complete' : 'skip'} for shoutout ${shoutoutId}`)
    } catch (error) {
      logger.error('[PlaybackShoutout] Failed to log play end:', error)
    }
  }, [userId])

  const getPlayer = useCallback(async () => {
    const existing = playerRef.current
    const engine = audio.getEngine()
    if (existing && engine && existing.context === engine.context && engine.context.state !== 'closed') {
      return existing
    }

    const ready = await audio.initializeAudio()
    const currentEngine = audio.getEngine()
    if (!ready || !currentEngine?.context || !currentEngine.uiSoundsGain) return null
    if (playerRef.current && playerRef.current.context === currentEngine.context) return playerRef.current

    const element = initialElement && !initialElementBoundRef.current ? initialElement : createShoutoutElement()
    initialElementBoundRef.current = true
    const context = currentEngine.context
    const source = context.createMediaElementSource(element)
    const analyserNode = context.createAnalyser()
    analyserNode.fftSize = 512
    analyserNode.smoothingTimeConstant = 0.75
    source.connect(analyserNode)
    analyserNode.connect(currentEngine.uiSoundsGain)

    playerRef.current = { element, source, analyser: analyserNode, context, engine: currentEngine }
    setAnalyser(analyserNode)
    logger.info('[PlaybackShoutout] ✅ Shoutout output connected to the shared audio engine')
    return playerRef.current
  }, [audio, initialElement])

  const haltElement = useCallback(() => {
    const player = playerRef.current
    if (!player) return
    const { element } = player
    element.onended = null
    element.onerror = null
    element.pause()
    if (element.getAttribute('src') !== null) {
      element.removeAttribute('src')
      element.load()
    }
  }, [])

  const finishPlayback = useCallback(() => {
    setPlayingShoutout(null)
    setProgress(0)
    reportEngineStatus({ isShoutoutPlaying: false })
  }, [reportEngineStatus])

  useEffect(() => {
    if (!playingShoutout) return
    const player = playerRef.current
    if (!player) return

    const interval = setInterval(() => {
      if (!player.element.paused) setProgress(player.element.currentTime)
    }, PROGRESS_INTERVAL_MS)

    return () => clearInterval(interval)
  }, [playingShoutout])

  useEffect(() => {
    const tokenRef = playTokenRef
    return () => {
      tokenRef.current++
      const player = playerRef.current
      if (!player) return
      player.element.onended = null
      player.element.onerror = null
      player.element.pause()
      player.element.removeAttribute('src')
      player.element.load()
      try { player.source.disconnect() } catch { /* already disconnected */ }
      try { player.analyser.disconnect() } catch { /* already disconnected */ }
      playerRef.current = null
    }
  }, [])

  const stopShoutoutRef = useRef(null)

  const stopShoutout = useCallback(async () => {
    if (!playingShoutout) return

    logger.info(`[PlaybackShoutout] Stopping shoutout: ${playingShoutout.id}`)

    playTokenRef.current++
    haltElement()
    finishPlayback()
    await logShoutoutEnd(playingShoutout.id, false)
  }, [playingShoutout, haltElement, finishPlayback, logShoutoutEnd])

  useEffect(() => {
    stopShoutoutRef.current = stopShoutout
  }, [stopShoutout])

  const playShoutout = useCallback(async (shoutout, options = {}) => {
    const { showModal = true } = options

    if (!shoutout?.id || !shoutout?.audio_url) {
      logger.error('[PlaybackShoutout] Invalid shoutout:', shoutout)
      return
    }

    if (playingShoutout?.id === shoutout.id) {
      void stopShoutoutRef.current?.()
      return
    }

    const token = ++playTokenRef.current

    if (playingShoutout) {
      haltElement()
      void logShoutoutEnd(playingShoutout.id, false)
    }

    currentShoutoutRef.current = shoutout
    playStartTimeRef.current = Date.now()
    setProgress(0)

    if (showModal && openShoutoutModal) {
      openShoutoutModal(shoutout)
    }

    void logShoutoutStart(shoutout.id)

    const player = await getPlayer()
    if (token !== playTokenRef.current) return
    if (!player) {
      logger.error('[PlaybackShoutout] Audio engine unavailable - cannot play shoutout')
      finishPlayback()
      return
    }

    await player.engine.ensureContext()
    if (token !== playTokenRef.current) return

    const { element } = player
    const handleDone = (completed) => {
      if (token !== playTokenRef.current) return
      playTokenRef.current++
      element.onended = null
      element.onerror = null
      logger.info(`[PlaybackShoutout] Shoutout ${shoutout.id} ${completed ? 'finished' : 'failed'}`)
      finishPlayback()
      void logShoutoutEnd(shoutout.id, completed)
    }

    element.onended = () => handleDone(true)
    element.onerror = () => handleDone(false)
    element.src = shoutout.audio_url

    setPlayingShoutout(shoutout)
    reportEngineStatus({ isShoutoutPlaying: true })

    try {
      await element.play()
      logger.info(`[PlaybackShoutout] Playing shoutout: ${shoutout.id}`)
    } catch (error) {
      if (error?.name === 'AbortError') return
      logger.error('[PlaybackShoutout] Shoutout playback failed:', error)
      handleDone(false)
    }
  }, [playingShoutout, getPlayer, haltElement, finishPlayback, logShoutoutStart, logShoutoutEnd, reportEngineStatus, openShoutoutModal])

  const value = useMemo(() => ({
    playingShoutout,
    playShoutout,
    stopShoutout
  }), [playingShoutout, playShoutout, stopShoutout])

  return (
    <PlaybackShoutoutContext.Provider value={value}>
      <ShoutoutProgressContext.Provider value={progress}>
        {children}
      </ShoutoutProgressContext.Provider>
    </PlaybackShoutoutContext.Provider>
  )
}

export function usePlaybackShoutout() {
  const context = useContext(PlaybackShoutoutContext)
  if (!context) {
    throw new Error('usePlaybackShoutout must be used within PlaybackShoutoutProvider')
  }
  return context
}

export function useShoutoutProgress() {
  return useContext(ShoutoutProgressContext)
}
