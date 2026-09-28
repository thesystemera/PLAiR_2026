import { useEffect, useRef, useMemo, useCallback } from 'react'
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
  const audioRef = useRef({ current: null })
  const isInitializedRef = useRef(false)
  const initPromiseRef = useRef(null)
  const stallTimeouts = useRef(new Map())
  const storageRefreshRef = useRef({ refreshStorageInfo, refreshDataUsage })
  const publishAudioStateRef = useRef(publishAudioState)

  useEffect(() => {
    storageRefreshRef.current = { refreshStorageInfo, refreshDataUsage }
  }, [refreshStorageInfo, refreshDataUsage])

  useEffect(() => {
    publishAudioStateRef.current = publishAudioState
  }, [publishAudioState])

  const isCurrentElement = useCallback((element) => {
    return !!engineRef.current && engineRef.current.getCurrentElement() === element
  }, [])

  const publishBuffering = useCallback((element, buffering) => {
    if (!isCurrentElement(element)) return
    publishAudioStateRef.current({ buffering })
  }, [isCurrentElement])

  const handleError = useCallback((e) => {
    const element = e.target
    const error = element.error

    if (!error || error.code === 4 || !element.getAttribute('src')) return
    if (!isCurrentElement(element)) return

    logger.error('Audio error:', error)
    publishAudioStateRef.current({ buffering: false })

    const isBlob = element.getAttribute('data-blob-url') === 'true'
    if (isBlob && (element.networkState === HTMLMediaElement.NETWORK_NO_SOURCE ||
      element.networkState === HTMLMediaElement.NETWORK_IDLE)) {
      logger.info('Network error detected, retrying in 2 seconds')
      const src = element.getAttribute('src')
      setTimeout(() => {
        if (isCurrentElement(element) && element.getAttribute('src') === src) {
          logger.info('Retrying audio load')
          element.load()
        }
      }, 2000)
    }
  }, [isCurrentElement])

  const handleStalled = useCallback((e) => {
    const element = e.target
    if (!isCurrentElement(element)) return

    publishAudioStateRef.current({ buffering: true })
    logger.warn('[useAudio] Playback stalled - buffer may be starving')
    if (stallTimeouts.current.has(element)) {
      clearTimeout(stallTimeouts.current.get(element))
    }

    const src = element.getAttribute('src')
    const timeout = setTimeout(() => {
      stallTimeouts.current.delete(element)
      const isBlob = element.getAttribute('data-blob-url') === 'true'
      if (!isBlob || !isCurrentElement(element) || element.getAttribute('src') !== src || element.paused) return
      logger.warn('[useAudio] Stalled for 10s, attempting recovery')
      const currentTime = element.currentTime
      element.load()
      element.currentTime = currentTime
      element.play().catch(err => logger.error('Recovery play failed:', err))
    }, 10000)

    stallTimeouts.current.set(element, timeout)
  }, [isCurrentElement])

  const handleProgress = useCallback((e) => {
    const element = e.target
    if (stallTimeouts.current.has(element)) {
      clearTimeout(stallTimeouts.current.get(element))
      stallTimeouts.current.delete(element)
    }
  }, [])

  const attachListeners = useCallback((element) => {
    element.addEventListener('waiting', () => publishBuffering(element, true))
    element.addEventListener('canplay', () => publishBuffering(element, false))
    element.addEventListener('canplaythrough', () => publishBuffering(element, false))
    element.addEventListener('stalled', handleStalled)
    element.addEventListener('progress', handleProgress)
    element.addEventListener('error', handleError)
  }, [handleError, handleStalled, handleProgress, publishBuffering])

  const initializeAudio = useCallback(async () => {
    if (isInitializedRef.current) {
      return true
    }
    if (initPromiseRef.current) {
      return initPromiseRef.current
    }
    if (!engineRef.current) {
      engineRef.current = new AudioEngine()
    }

    const init = (async () => {
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
            if (engineRef.current?.getCurrentTrackId() === trackId) {
              publishAudioStateRef.current({ isCached: true })
            }
            logger.info(`[useAudio] Cached: ${trackId}`)
            await storageRefreshRef.current.refreshStorageInfo()
            await storageRefreshRef.current.refreshDataUsage()
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
      } finally {
        initPromiseRef.current = null
      }
    })()

    initPromiseRef.current = init
    return init
  }, [attachListeners])

  useEffect(() => {
    const stalls = stallTimeouts.current
    return () => {
      stalls.forEach(timeout => clearTimeout(timeout))
      stalls.clear()
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

  const getVolume = useCallback(() => engineRef.current?.getVolume() || 1, [])

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

  const getEngine = useCallback(() => engineRef.current, [])

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
    getVolume,
    setMuted,
    playSfx,
    stopSfx,
    audioRef,
    engineRef,
    mixerRef,
    getCurrentElement,
    getEngine,
  }), [initializeAudio, play, pause, stopImmediately, setActiveDevice, seek, setVolume, getVolume, setMuted, playSfx, stopSfx, getCurrentElement, getEngine])
}
