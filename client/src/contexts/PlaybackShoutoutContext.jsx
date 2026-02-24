import { logger } from '../lib/logger'
import { createContext, useContext, useState, useCallback, useMemo, useRef, useEffect } from 'react'
import { useAudio } from '../hooks/useAudio'
import { useFFTProcessor } from '../hooks/useFFTProcessor'
import { useAuth } from './AuthContext'
import { useUIState } from './UIStateContext'
import { api } from '../lib/api'

const PlaybackShoutoutContext = createContext(null)

export function PlaybackShoutoutProvider({ children }) {
  const [playingShoutout, setPlayingShoutout] = useState(null)
  const [progress, setProgress] = useState(0)
  const { playSfx, stopSfx, engineRef } = useAudio()
  const { user } = useAuth()
  const { reportEngineStatus, openShoutoutModal } = useUIState()

  const playStartTimeRef = useRef(null)
  const currentShoutoutRef = useRef(null)
  const progressIntervalRef = useRef(null)

  const [analyser, setAnalyser] = useState(null)
  const isAnalyzerSetupRef = useRef(false)

  useFFTProcessor(
    playingShoutout,
    analyser,
    'shoutoutFftData',
    { processingMode: 'logarithmic' }
  )

  const logShoutoutStart = useCallback(async (shoutoutId) => {
    try {
      await api.trackShoutoutPlay({
        shoutout_id: shoutoutId,
        user_id: user?.id || null,
        event_type: 'play'
      })
      logger.info(`[PlaybackShoutout] Logged play start for shoutout ${shoutoutId}`)
    } catch (error) {
      logger.error('[PlaybackShoutout] Failed to log play start:', error)
    }
  }, [user?.id])

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

    try {
      await api.trackShoutoutPlay({
        shoutout_id: shoutoutId,
        user_id: user?.id || null,
        event_type: completed ? 'complete' : 'skip',
        duration_ms: durationMs,
        completion_pct: completionPct
      })
      logger.info(`[PlaybackShoutout] Logged ${completed ? 'complete' : 'skip'} for shoutout ${shoutoutId}`)
    } catch (error) {
      logger.error('[PlaybackShoutout] Failed to log play end:', error)
    }

    playStartTimeRef.current = null
    currentShoutoutRef.current = null
  }, [user?.id])

  useEffect(() => {
    if (!playingShoutout || !engineRef.current?.sfxElement) {
      queueMicrotask(() => setProgress(0))
      return
    }

    const updateProgress = () => {
      const sfxElement = engineRef.current?.sfxElement
      if (sfxElement && !sfxElement.paused) {
        queueMicrotask(() => setProgress(sfxElement.currentTime))
      }
    }

    progressIntervalRef.current = setInterval(updateProgress, 50)

    return () => {
      if (progressIntervalRef.current) {
        clearInterval(progressIntervalRef.current)
      }
    }
  }, [playingShoutout, engineRef])

  const setupAnalyzer = useCallback(() => {
    if (isAnalyzerSetupRef.current) return true

    const engine = engineRef.current
    if (!engine?.context || !engine?.sfxSource) {
      return false
    }

    try {
      const analyserNode = engine.context.createAnalyser()
      analyserNode.fftSize = 512
      analyserNode.smoothingTimeConstant = 0.75

      engine.sfxSource.disconnect()
      engine.sfxSource.connect(analyserNode)
      analyserNode.connect(engine.uiSoundsGain)

      setAnalyser(analyserNode)
      isAnalyzerSetupRef.current = true
      logger.info('[PlaybackShoutout] ✅ FFT analyzer connected')
      return true
    } catch (error) {
      logger.error('[PlaybackShoutout] ❌ FFT analyzer setup failed:', error)
      return false
    }
  }, [engineRef])

  useEffect(() => {
    if (!playingShoutout || isAnalyzerSetupRef.current) return

    if (setupAnalyzer()) return

    const timeout = setTimeout(setupAnalyzer, 200)
    return () => clearTimeout(timeout)
  }, [playingShoutout, setupAnalyzer])

  const stopShoutoutRef = useRef(null)

  const stopShoutout = useCallback(async () => {
    if (!playingShoutout) return

    logger.info(`[PlaybackShoutout] Stopping shoutout: ${playingShoutout.id}`)

    await logShoutoutEnd(playingShoutout.id, false)
    stopSfx()
    setPlayingShoutout(null)
    setProgress(0)
    reportEngineStatus({ isShoutoutPlaying: false })
  }, [playingShoutout, stopSfx, logShoutoutEnd, reportEngineStatus])

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

    if (playingShoutout) {
      await logShoutoutEnd(playingShoutout.id, false)
      stopSfx()
    }

    currentShoutoutRef.current = shoutout
    playStartTimeRef.current = Date.now()
    setProgress(0)

    if (showModal && openShoutoutModal) {
      openShoutoutModal(shoutout)
    }

    await logShoutoutStart(shoutout.id)

    await playSfx(shoutout.audio_url, async () => {
      logger.info(`[PlaybackShoutout] Shoutout ${shoutout.id} finished`)
      await logShoutoutEnd(shoutout.id, true)
      setPlayingShoutout(null)
      setProgress(0)
      reportEngineStatus({ isShoutoutPlaying: false })
    })

    setPlayingShoutout(shoutout)
    reportEngineStatus({ isShoutoutPlaying: true })

    logger.info(`[PlaybackShoutout] Playing shoutout: ${shoutout.id}`)
  }, [playingShoutout, playSfx, stopSfx, logShoutoutStart, logShoutoutEnd, reportEngineStatus, openShoutoutModal, setupAnalyzer])

  const value = useMemo(() => ({
    playingShoutout,
    playShoutout,
    stopShoutout,
    progress
  }), [playingShoutout, playShoutout, stopShoutout, progress])

  return (
    <PlaybackShoutoutContext.Provider value={value}>
      {children}
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