import { useEffect, useRef, useState, useMemo, useCallback } from 'react'
import { AudioEngine } from '../lib/audioEngine'
import { AudioMixer } from '../lib/audioMixer'
import { cacheManager } from '../lib/cacheManager'
import { useStorage } from '../contexts/StorageContext'
import { useUIState, uiState } from '../contexts/UIStateContext'
import { logger } from '../lib/logger'

export function useAudio() {
  const { refreshStorageInfo, refreshDataUsage } = useStorage()
  const { publishAudioState } = useUIState()
  const engineRef = useRef(null)
  const mixerRef = useRef(null)
  const [playing, setPlaying] = useState(false)
  const [buffering, setBuffering] = useState(false)
  const [isCached, setIsCached] = useState(false)
  const audioRef = useRef({ current: null })
  const isInitializedRef = useRef(false)
  const stallTimeouts = useRef(new Map())

  const handleError = useCallback((e) => {
    const element = e.target
    const error = element.error

    if (!error) return

    if (error && error.code === 4) {
      return
    }

    if (!element.src) {
      return
    }

    logger.error('Audio error:', error)
    setBuffering(false)
    publishAudioState({ buffering: false })

    if (element && (element.networkState === HTMLMediaElement.NETWORK_NO_SOURCE ||
      element.networkState === HTMLMediaElement.NETWORK_IDLE)) {
      logger.info('Network error detected, retrying in 2 seconds')
      setTimeout(() => {
        if (element && element.src) {
          logger.info('Retrying audio load')
          element.load()
        }
      }, 2000)
    }
  }, [publishAudioState])

  const handleStalled = useCallback((e) => {
    setBuffering(true)
    publishAudioState({ buffering: true })
    logger.warn('[useAudio] Playback stalled - buffer may be starving')
    const element = e.target
    if (stallTimeouts.current.has(element)) {
      clearTimeout(stallTimeouts.current.get(element))
    }

    const timeout = setTimeout(() => {
      logger.warn('[useAudio] Stalled for 10s, attempting recovery')
      if (element && element.src && !element.paused) {
        const currentTime = element.currentTime
        element.load()
        element.currentTime = currentTime
        element.play().catch(err => logger.error('Recovery play failed:', err))
      }
    }, 10000)

    stallTimeouts.current.set(element, timeout)
  }, [publishAudioState])

  const handleProgress = useCallback((e) => {
    const element = e.target
    if (stallTimeouts.current.has(element)) {
      clearTimeout(stallTimeouts.current.get(element))
      stallTimeouts.current.delete(element)
    }
  }, [])

  const attachListeners = useCallback((element) => {
    element.addEventListener('ended', () => setPlaying(false))
    element.addEventListener('play', () => setPlaying(true))
    element.addEventListener('pause', () => setPlaying(false))
    element.addEventListener('waiting', () => {
      setBuffering(true)
      publishAudioState({ buffering: true })
    })
    element.addEventListener('canplay', () => {
      setBuffering(false)
      publishAudioState({ buffering: false })
    })
    element.addEventListener('canplaythrough', () => {
      setBuffering(false)
      publishAudioState({ buffering: false })
    })
    element.addEventListener('stalled', handleStalled)
    element.addEventListener('progress', handleProgress)
    element.addEventListener('error', handleError)
  }, [handleError, handleStalled, handleProgress, publishAudioState])

  const initializeAudio = useCallback(async () => {
    if (isInitializedRef.current) {
      return true
    }
    if (!engineRef.current) {
      engineRef.current = new AudioEngine()
    }

    try {
      await engineRef.current.initialize()

      if (!mixerRef.current) {
        mixerRef.current = new AudioMixer(engineRef.current)
      }

      attachListeners(engineRef.current.slots.A.element)
      attachListeners(engineRef.current.slots.B.element)

      engineRef.current.onChunkReceived = (trackId, chunk) => {
        cacheManager.addStreamChunk(trackId, chunk)
      }

      engineRef.current.onStreamComplete = async (trackId) => {
        logger.info(`[useAudio] Stream complete for ${trackId}, caching`)
        try {
          await cacheManager.finalizeStream(trackId, uiState.audioState.isOnline)
          setIsCached(true)
          publishAudioState({ isCached: true })
          logger.info(`[useAudio] Cached: ${trackId}`)
          await refreshStorageInfo()
          await refreshDataUsage()
        } catch (err) {
          logger.error(`[useAudio] Cache failed for ${trackId}:`, err)
        }
      }

      window.audioEngine = engineRef.current
      logger.info('[useAudio] AudioEngine initialized')

      isInitializedRef.current = true
      return true
    } catch (error) {
      logger.error('Failed to initialize audio engine:', error)
      isInitializedRef.current = false
      return false
    }
  }, [attachListeners, refreshStorageInfo, refreshDataUsage, publishAudioState])

  useEffect(() => {
    return () => {
      if (mixerRef.current) {
        mixerRef.current.destroy()
      }
      if (engineRef.current) {
        engineRef.current.destroy()
      }
      if (window.audioEngine === engineRef.current) {
        window.audioEngine = null
      }
    }
  }, [])

  useEffect(() => {
    if (engineRef.current && isInitializedRef.current) {
      audioRef.current.current = engineRef.current.getCurrentElement()
    }
  })

  const play = useCallback(async () => {
    const initialized = await initializeAudio()
    if (initialized && engineRef.current) {
      await engineRef.current.play()
    }
  }, [initializeAudio])

  const pause = useCallback(() => {
    if (engineRef.current && isInitializedRef.current) {
      engineRef.current.pause()
    }
  }, [])

  const seek = useCallback((timeSeconds) => {
    if (engineRef.current && isInitializedRef.current && !isNaN(timeSeconds)) {
      return engineRef.current.seek(timeSeconds)
    } else {
      logger.warn(`[useAudio] Seek blocked: engine=${!!engineRef.current}, initialized=${isInitializedRef.current}, validTime=${!isNaN(timeSeconds)}`)
      return Promise.resolve()
    }
  }, [])

  const setVolume = useCallback((value) => {
    if (engineRef.current && isInitializedRef.current) {
      engineRef.current.setVolume(value)
    }
  }, [])

  const setMuted = useCallback((muted) => {
    if (engineRef.current && isInitializedRef.current) {
      engineRef.current.setMuted(muted)
    }
  }, [])

  const stopImmediately = useCallback(() => {
    if (engineRef.current && isInitializedRef.current) {
      engineRef.current.stopImmediately()
    }
  }, [])

  const setActiveDevice = useCallback((isActive) => {
    if (engineRef.current) {
      engineRef.current.setActiveDevice(isActive)
    }
  }, [])

  const getCurrentElement = useCallback(() => {
    if (engineRef.current && isInitializedRef.current) {
      return engineRef.current.getCurrentElement()
    }
    return null
  }, [])

  const playSfx = useCallback(async (url, onEnded) => {
    const initialized = await initializeAudio()
    if (initialized && engineRef.current) {
      return engineRef.current.playSfx(url, onEnded)
    }
  }, [initializeAudio])

  const stopSfx = useCallback(() => {
    if (engineRef.current && isInitializedRef.current) {
      engineRef.current.stopSfx()
    }
  }, [])

  return useMemo(() => ({
    initializeAudio,
    play,
    pause,
    stopImmediately,
    setActiveDevice,
    seek,
    setVolume,
    getVolume: () => engineRef.current?.getVolume() || 1,
    setMuted,
    playSfx,
    stopSfx,
    playing,
    buffering,
    audioRef,
    engineRef,
    mixerRef,
    getCurrentElement,
    isCached,
    setIsCached,
  }), [initializeAudio, play, pause, stopImmediately, setActiveDevice, seek, setVolume, setMuted, playSfx, stopSfx, playing, buffering, getCurrentElement, isCached])
}