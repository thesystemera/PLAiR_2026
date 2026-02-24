import { logger } from '../lib/logger'
import { createContext, useContext, useState, useCallback, useEffect, useRef, useMemo } from 'react'
import { api } from '../lib/api'
import { useAudio } from '../hooks/useAudio'
import { useWebSocketSubscribe, WebSocketContext } from './WebSocketContext'
import { cacheManager } from '../lib/cacheManager'
import { useAuth } from './AuthContext'
import { useUIState, uiState } from './UIStateContext'
import { useNetwork } from './NetworkContext'
import { getDeviceId } from '../lib/session'
import { offlineBackend } from '../lib/offlineAPI'

const PlaybackContext = createContext(null)

export function PlaybackProvider({ children }) {
  const [state, setState] = useState({
    current_track: null,
    progress_ms: 0,
    is_playing: false,
    queue: [],
    history: [],
    current_index: 0,
    crossfade_hint: null,
    announcer_hint: null,
  })
  const { user } = useAuth()
  const { reportEngineStatus, publishAudioState, publishRadioState, audioState, settingsState, engineState } = useUIState()
  const settingsStateRef = useRef(settingsState)
  const isCrossfadingRef = useRef(false)
  const { getEffectiveBitrate } = useNetwork()
  const { send: wsSend } = useContext(WebSocketContext) || {}
  const deviceId = getDeviceId()

  const wsSendRef = useRef(wsSend)
  useEffect(() => { wsSendRef.current = wsSend }, [wsSend])

  const publishAudioStateRef = useRef(publishAudioState)
  useEffect(() => { publishAudioStateRef.current = publishAudioState }, [publishAudioState])

  const audio = useAudio()
  const audioRef = useRef(audio)
  useEffect(() => { audioRef.current = audio }, [audio])
  const isManualChangeRef = useRef(false)
  const loadingRef = useRef(false)
  const prevIsOnlineRef = useRef(audioState.isOnline)

  const lastMixTimeRef = useRef(0)
  const currentCrossfadeDurationRef = useRef(0)

  const failedTracksRef = useRef(new Map())

  const stateRef = useRef(state)
  const userRef = useRef(user)
  const isActiveDeviceRef = useRef(false)
  const [activeDeviceTrigger, setActiveDeviceTrigger] = useState(0)

  const lastServerProgressRef = useRef(0)
  const lastServerUpdateTimeRef = useRef(Date.now())
  const lastProgressReportRef = useRef(0)

  useEffect(() => { stateRef.current = state }, [state])
  useEffect(() => { userRef.current = user }, [user])
  useEffect(() => { settingsStateRef.current = settingsState }, [settingsState])
  useEffect(() => { isCrossfadingRef.current = engineState.isCrossfading }, [engineState.isCrossfading])

  useEffect(() => {
    const engine = audio.engineRef.current
    if (!engine) return

    engine.onBufferStatusChange = (isBuffering) => {
      publishAudioState({ buffering: isBuffering })
    }

    return () => {
      engine.onBufferStatusChange = null
    }
  }, [audio.engineRef.current, publishAudioState])

  useEffect(() => {
    const isMusicPlaying = state.is_playing
    const isMusicPaused = !state.is_playing && state.current_track !== null

    reportEngineStatus({
      isMusicPlaying,
      isMusicPaused,
      is_playing: state.is_playing,
      currentTrack: state.current_track,
      queue: state.queue,
      currentIndex: state.current_index
    })
  }, [state.is_playing, state.current_track, state.queue, state.current_index, reportEngineStatus])

  useEffect(() => {
    const now = Date.now()
    if (!lastProgressReportRef.current || now - lastProgressReportRef.current >= 100) {
      reportEngineStatus({
        progress_ms: state.progress_ms
      })
      lastProgressReportRef.current = now
    }
  }, [state.progress_ms, reportEngineStatus])

  const bitrateToNumber = useCallback((bitrate) => {
    if (bitrate === 'auto' || bitrate === '256k') return 256
    if (bitrate === '192k') return 192
    return 128
  }, [])

  const getTrackSource = useCallback(async (trackId, requestedBitrate, trackMetadata = null) => {
    const cached = await cacheManager.getCachedTrack(trackId)
    const dataSaverMode = settingsStateRef.current.dataSaverMode

    if (cached) {
      // Data saver: always prefer cache regardless of bitrate quality
      if (dataSaverMode) {
        return { url: URL.createObjectURL(cached.audioBlob), isBlobUrl: true, bitrate: cached.bitrate, fromCache: true }
      }

      const dominated = audioState.isOnline &&
                        requestedBitrate !== 'auto' &&
                        bitrateToNumber(requestedBitrate) > bitrateToNumber(cached.bitrate)

      if (!dominated) {
        return { url: URL.createObjectURL(cached.audioBlob), isBlobUrl: true, bitrate: cached.bitrate, fromCache: true }
      }
    }

    if (!audioState.isOnline) return null

    // Data saver: force 128k streaming to minimize data usage
    const bitrate = dataSaverMode ? '128k' :
      (requestedBitrate === 'auto' ? getEffectiveBitrate('auto', !!userRef.current) : requestedBitrate)
    if (trackMetadata) cacheManager.beginTrackStream(trackId, { ...trackMetadata, bitrate })

    return { url: api.getStreamUrl(trackId), isBlobUrl: false, bitrate, fromCache: false }
  }, [audioState.isOnline, bitrateToNumber, getEffectiveBitrate])

   const handlePlaybackState = useCallback(async (data) => {
    const weAreActive = data.active_device_id === deviceId
    const wasActive = isActiveDeviceRef.current

    logger.info(`[PlaybackContext] 📥 Playback state: track=${data.current_track?.id?.slice(0,8) || 'none'}, is_playing=${data.is_playing}, active_device=${data.active_device_id?.slice(0,8) || 'none'}, we_are_active=${weAreActive}`)

    if (weAreActive !== wasActive) {
      logger.info(`[PlaybackContext] 🔄 Device status changed: ${wasActive ? 'active' : 'inactive'} → ${weAreActive ? 'active' : 'inactive'}`)
      isActiveDeviceRef.current = weAreActive
      setActiveDeviceTrigger(prev => prev + 1)

      if (weAreActive) {
        await audio.initializeAudio()
      }
    }

    await audio.setActiveDevice(weAreActive)
    reportEngineStatus({ isActiveDevice: weAreActive })

    if (data.crossfade_hint) {
      logger.info(`[Audio] 📥 BACKEND HINT: Crossfade ${data.crossfade_hint.duration_ms}ms`)
      if (audio.engineRef.current?.currentSlot?.metadata) {
        audio.engineRef.current.currentSlot.metadata.crossfade_hint = data.crossfade_hint
      }
    }

    if (data.announcer_hint) {
      logger.info(`[Audio] 📥 ANNOUNCER HINT: Window ${data.announcer_hint.start_ms / 1000}s-${data.announcer_hint.end_ms / 1000}s (${data.announcer_hint.duration_ms}ms)`)
    }

    if (data.activeSeedMode !== undefined && publishRadioState) {
      publishRadioState({ activeSeedMode: data.activeSeedMode })
    }

    if (weAreActive) {
      setState(prevState => {
        let currentTrack = data.current_track ?? prevState.current_track

        const engineTrackId = audio.engineRef.current?.getCurrentTrackId()
        const timeSinceMix = Date.now() - lastMixTimeRef.current

        if (currentTrack?.id && engineTrackId && currentTrack.id !== engineTrackId) {
          if (timeSinceMix < 5000) {
            logger.warn(`[PlaybackContext] ✋ IGNORE LIBRARY: DJ is mixing. Keeping ${engineTrackId.slice(0,8)}`)
            currentTrack = prevState.current_track
          } else {
            logger.info(`[PlaybackContext] ⏭️ Accepting remote track change to ${currentTrack.id.slice(0,8)}`)
          }
        }

        if (currentTrack && data.crossfade_hint) {
          currentTrack.crossfade_hint = data.crossfade_hint
        }

        return {
          ...prevState,
          current_track: currentTrack,
          queue: data.queue ?? prevState.queue,
          history: data.history ?? prevState.history,
          current_index: data.current_index ?? prevState.current_index,
          crossfade_hint: data.crossfade_hint ?? prevState.crossfade_hint,
          announcer_hint: data.announcer_hint ?? prevState.announcer_hint,
          is_playing: data.is_playing ?? prevState.is_playing,
          progress_ms: data.progress_ms ?? prevState.progress_ms,
        }
      })

      if (data.is_playing !== undefined) {
        const element = audio.engineRef.current?.getCurrentElement()
        const audioIsPlaying = element && !element.paused

        if (data.is_playing !== audioIsPlaying) {
          logger.info(`[PlaybackContext] 🎛️ Remote command: ${data.is_playing ? 'play' : 'pause'}`)
          if (data.is_playing) {
            audio.play().catch(err => logger.error('[PlaybackContext] Remote play failed:', err))
          } else {
            audio.pause()
          }
        }
      }

      if (data.progress_ms !== undefined) {
        const element = audio.engineRef.current?.getCurrentElement()
        const audioProgress = element ? Math.floor(element.currentTime * 1000) : 0
        const engineTrackId = audio.engineRef.current?.getCurrentTrackId()

        if (data.current_track?.id === engineTrackId) {
          if (Math.abs(data.progress_ms - audioProgress) > 1000) {
            logger.info(`[PlaybackContext] 🎛️ Remote seek: ${data.progress_ms}ms (audio was at ${audioProgress}ms)`)
            audio.seek(data.progress_ms / 1000)
          }
        }
      }
    } else {
      setState({
        current_track: data.current_track ?? null,
        progress_ms: data.progress_ms ?? 0,
        is_playing: data.is_playing ?? false,
        queue: data.queue ?? [],
        history: data.history ?? [],
        current_index: data.current_index ?? 0,
        crossfade_hint: data.crossfade_hint ?? null,
        announcer_hint: data.announcer_hint ?? null,
      })
    }
  }, [audio, publishRadioState, deviceId, reportEngineStatus])

  const handlePlaybackOverride = useCallback(async (data) => {
    const { mode } = data

    if (mode === 'pause') {
      await audio.pause()
    } else if (mode === 'play') {
      await audio.play()
    } else if (mode === 'mute') {
      audio.setMuted(true)
    } else if (mode === 'release') {
      audio.setMuted(false)
    }
  }, [audio])

  const connected = useWebSocketSubscribe('playback_state', handlePlaybackState)
  useWebSocketSubscribe('playback_override', handlePlaybackOverride)

  useEffect(() => {
    const initOfflineState = async () => {
      if (audioState.isOnline || state.current_track) {
        return
      }

      logger.info('[PlaybackContext] 🔌 Offline cold boot - initializing from cached state')

      try {
        const offlineState = await offlineBackend.getPlaybackState()
        if (offlineState) {
          logger.info('[PlaybackContext] 🔌 Loaded offline state:', offlineState.current_track?.title)
          await handlePlaybackState(offlineState)
        } else {
          logger.info('[PlaybackContext] 🔌 No offline state available')
        }
      } catch (err) {
        logger.error('[PlaybackContext] 🔌 Failed to load offline state:', err)
      }
    }

    void initOfflineState()
  }, []) // Run once on mount

  useEffect(() => {
    if (isActiveDeviceRef.current) return

    if (!state.is_playing || !state.current_track) {
      return
    }

    const progressSimulation = setInterval(() => {
      if (!isActiveDeviceRef.current && stateRef.current.is_playing && stateRef.current.current_track) {
        const elapsed = Date.now() - lastServerUpdateTimeRef.current
        const estimatedProgress = lastServerProgressRef.current + elapsed

        const maxProgress = stateRef.current.current_track.duration_ms || Infinity
        const clampedProgress = Math.min(estimatedProgress, maxProgress)

        setState(prev => ({ ...prev, progress_ms: clampedProgress }))
      }
    }, 250)

    return () => clearInterval(progressSimulation)
  }, [state.is_playing, state.current_track?.id, isActiveDeviceRef.current])

  useEffect(() => {
    if (state.progress_ms !== undefined && state.progress_ms !== lastServerProgressRef.current) {
      lastServerProgressRef.current = state.progress_ms
      lastServerUpdateTimeRef.current = Date.now()
    }
  }, [state.progress_ms])

  const triggerPreload = useCallback(async () => {
    if (!state.queue?.length || loadingRef.current || !isActiveDeviceRef.current || isCrossfadingRef.current) return

    const currentTrackId = state.current_track?.id
    const currentIndex = state.queue.findIndex(t => t.id === currentTrackId)
    const nextTrack = state.queue[currentIndex + 1]
    const engine = audio.engineRef.current

    if (!currentTrackId || currentIndex === -1 || !nextTrack || !engine || engine.getNextTrackId() === nextTrack.id) return

    try {
      const requestedBitrate = settingsStateRef.current.audioQuality
      const source = await getTrackSource(nextTrack.id, requestedBitrate, nextTrack)

      if (!source) return

      await engine.preloadTrack(nextTrack.id, source.url, {
        isBlobUrl: source.isBlobUrl,
        metadata: { ...nextTrack, bitrate: source.bitrate, fromCache: source.fromCache }
      })
    } catch (error) {
      logger.error('[PlaybackContext] Preload failed:', error)
    }
  }, [state.current_track?.id, state.queue, audio.engineRef, getTrackSource])

  useEffect(() => {
    const currentTrackId = state.current_track?.id
    const engine = audio.engineRef.current
    const engineTrackId = engine?.getCurrentTrackId()
    const isManual = isManualChangeRef.current

    if (currentTrackId && currentTrackId === engineTrackId && !isManual) {
      void triggerPreload()
      return
    }

    const nextTrackId = engine?.getNextTrackId()
    if (!isManual && engineTrackId === currentTrackId) return

    if (nextTrackId && nextTrackId === currentTrackId) {
      if (loadingRef.current) return

      loadingRef.current = true
      setState(prev => ({ ...prev, progress_ms: 0 }))

      engine.crossfade(50, true, true).then(() => {
        loadingRef.current = false
        void triggerPreload()
      }).catch(() => {
        loadingRef.current = false
      })

      isManualChangeRef.current = false
      return
    }

    if (!currentTrackId || !isActiveDeviceRef.current) return

    const failureInfo = failedTracksRef.current.get(currentTrackId)
    if (failureInfo && Date.now() - failureInfo.lastAttempt < 10000 && failureInfo.attempts >= 3) return

    loadingRef.current = true

    const loadNewTrack = async () => {
      try {
        await audio.initializeAudio()
        const engine = audio.engineRef.current
        if (!engine) return

        const source = await getTrackSource(currentTrackId, settingsStateRef.current.audioQuality, state.current_track)
        if (!source) return

        const isManual = isManualChangeRef.current
        await engine.loadTrack(currentTrackId, source.url, {
          isBlobUrl: source.isBlobUrl,
          shouldPlay: false,
          fadeTimeMs: isManual ? 50 : 3000,
          useCrossfade: true,
          metadata: { ...state.current_track, bitrate: source.bitrate, fromCache: source.fromCache }
        })

        const targetProgress = state.progress_ms || 0
        const currentProgress = engine.getCurrentElement()?.currentTime || 0
        if (Math.abs(targetProgress - currentProgress * 1000) > 1000) {
          await audio.seek(targetProgress / 1000)
        }

        if (state.is_playing || isManual) await audio.play()
        failedTracksRef.current.delete(currentTrackId)

      } catch {
        const info = failedTracksRef.current.get(currentTrackId) || { attempts: 0 }
        failedTracksRef.current.set(currentTrackId, { attempts: info.attempts + 1, lastAttempt: Date.now() })
      } finally {
        loadingRef.current = false
        isManualChangeRef.current = false
        void triggerPreload()
      }
    }

    void loadNewTrack()
  }, [state.current_track?.id, activeDeviceTrigger, audio, triggerPreload, getTrackSource, state.is_playing])

  useEffect(() => {
    const checkAndSetCallback = () => {
      const engine = audio.engineRef.current
      if (!engine) return false

      if (engine.onCrossfadeStart) return true

      engine.onCrossfadeStateChange = (isCrossfading) => {
        reportEngineStatus({ isCrossfading })
        if (!isCrossfading) {
          const isFromCache = engine.getCurrentElement()?.getAttribute('data-blob-url') === 'true'
          const bitrate = engine.currentSlot?.metadata?.bitrate || settingsStateRef.current.audioQuality
          audioRef.current?.setIsCached(isFromCache)
          publishAudioStateRef.current?.({ isCached: isFromCache, bitrate })
        }
      }

      engine.onCrossfadeStart = (currentSlotId, nextSlotId, fadeTimeMs) => {
        logger.info(`[Audio] 🎵 CROSSFADE: ${currentSlotId?.slice(0,8)} → ${nextSlotId?.slice(0,8)} (${fadeTimeMs}ms)`)

        lastMixTimeRef.current = Date.now()
        currentCrossfadeDurationRef.current = fadeTimeMs

        const nextTrack = stateRef.current.queue.find(t => t.id === nextSlotId)
          || stateRef.current.history.find(t => t.id === nextSlotId)

        if (nextTrack) {
          const newIndex = stateRef.current.queue.findIndex(t => t.id === nextSlotId)

          setState(prev => ({
            ...prev,
            current_track: nextTrack,
            progress_ms: 0,
            current_index: newIndex !== -1 ? newIndex : prev.current_index + 1
          }))
          logger.info(`[PlaybackContext] ✅ Optimistic update: ${nextTrack.id.slice(0,8)}`)
        } else {
          logger.error(`[PlaybackContext] ❌ Engine crossfaded to ${nextSlotId?.slice(0,8)} but track not found in state!`)
        }

        if (nextTrack && wsSendRef.current && uiState.audioState.isOnline) {
          wsSendRef.current({
            type: 'track_transition',
            data: {
              from_track_id: currentSlotId,
              to_track_id: nextTrack.id,
              fade_duration_ms: fadeTimeMs
            }
          })
          logger.info('[PlaybackContext] 📡 Sent track_transition to backend')
        } else if (nextTrack) {
          logger.info('[PlaybackContext] 🔌 Offline auto-advance to next track')
          void playTrack(nextTrack.id)
        }
      }
      return true
    }

    if (checkAndSetCallback()) return

    const interval = setInterval(() => {
      if (checkAndSetCallback()) {
        clearInterval(interval)
      }
    }, 100)

    return () => clearInterval(interval)
  }, [reportEngineStatus])

  useEffect(() => { void triggerPreload() }, [triggerPreload])

  const prevCrossfadingRef = useRef(false)
  useEffect(() => {
    const wasXfading = prevCrossfadingRef.current
    prevCrossfadingRef.current = engineState.isCrossfading
    if (wasXfading && !engineState.isCrossfading) void triggerPreload()
  }, [engineState.isCrossfading, triggerPreload])

  useEffect(() => {
    if (!wsSend) return

    const shouldSkipTick = () => !isActiveDeviceRef.current || !stateRef.current.current_track

    const statusInterval = setInterval(() => {
      if (shouldSkipTick() || !uiState.audioState.isOnline) return

      const announcer = stateRef.current.announcer_hint

      if (announcer) {
        const currentElement = audio.getCurrentElement?.()
        const currentPos = currentElement ? currentElement.currentTime * 1000 : 0
        const triggerTime = announcer.start_ms
        const timeUntilTrigger = triggerTime - currentPos

        logger.info(
          `[Audio] 🎙️ Frontend at ${(currentPos / 1000).toFixed(1)}s | Announcer trigger ${(triggerTime / 1000).toFixed(1)}s | ${(timeUntilTrigger / 1000).toFixed(1)}s away`
        )
      }
    }, 10000)

    const heartbeatInterval = setInterval(() => {
      if (shouldSkipTick()) return

      const currentElement = audio.getCurrentElement?.()
      if (!currentElement) return

      const actualPos = Math.floor(currentElement.currentTime * 1000)

      let bufferedAhead = 0
      try {
        if (currentElement.buffered.length > 0) {
          const bufferedEnd = currentElement.buffered.end(currentElement.buffered.length - 1)
          bufferedAhead = Math.floor((bufferedEnd - currentElement.currentTime) * 1000)
        }
      } catch {
        // Buffered time calculation errors are intentionally suppressed
      }

      wsSend({
        type: 'playback_heartbeat',
        data: {
          track_id: stateRef.current.current_track.id,
          actual_position_ms: actualPos,
          is_playing: !currentElement.paused,
          buffered_ahead_ms: bufferedAhead,
          timestamp: Date.now()
        }
      })
    }, 5000)

    return () => {
      clearInterval(heartbeatInterval)
      clearInterval(statusInterval)
    }
  }, [audio, wsSend])

  useEffect(() => {
    const progressInterval = setInterval(() => {
      if (!isActiveDeviceRef.current || !stateRef.current.current_track) return

      const currentElement = audio.getCurrentElement?.()
      if (!currentElement || currentElement.paused) return

      const currentTime = Math.floor(currentElement.currentTime * 1000)

      setState(prev => {
        if (Math.abs(currentTime - prev.progress_ms) > 100) {
          return { ...prev, progress_ms: currentTime }
        }
        return prev
      })
    }, 250)

    return () => clearInterval(progressInterval)
  }, [audio])

  const playTrack = useCallback(async (trackId) => {
    isManualChangeRef.current = true

    if (!audioState.isOnline) {
      try {
        logger.info('[Playback] 🔌 Offline mode: Loading cached track', { trackId })
        const response = await api.play(trackId)

        if (response.offline && response.state) {
          await handlePlaybackState(response.state)
          logger.info('[Playback] ✅ Offline: Track loaded from cache')
          return
        } else if (response.status === 'error') {
          logger.error('[Playback] ❌ Offline play failed:', response.error)
          return
        }
      } catch (err) {
        logger.error('[Playback] ❌ Offline playback error:', err)
        return
      }
    }

    wsSend?.({
      type: 'playback_command',
      data: { command: 'play', track_id: trackId }
    })
  }, [wsSend, handlePlaybackState, audioState.isOnline])

  const togglePlay = useCallback(async () => {
    const wasPlaying = state.is_playing

    setState(prev => ({ ...prev, is_playing: !wasPlaying }))

    if (wasPlaying) {
      audio.pause()
    } else {
      audio.play().catch(err => logger.error('[PlaybackContext] Play failed:', err))
    }

    if (wasPlaying) {
      wsSend?.({ type: 'playback_command', data: { command: 'pause' } })
    } else {
      wsSend?.({ type: 'playback_command', data: { command: 'play' } })
    }
  }, [state.is_playing, wsSend, audio])

  const next = useCallback(async () => {
    isManualChangeRef.current = true

    if (!audioState.isOnline) {
      const currentTrackId = state.current_track?.id
      if (currentTrackId && state.queue && state.queue.length > 0) {
        const currentIndex = state.queue.findIndex(t => t.id === currentTrackId)
        if (currentIndex >= 0 && currentIndex < state.queue.length - 1) {
          const nextTrack = state.queue[currentIndex + 1]
          logger.info('[Playback] 🔌 Offline next: Playing', nextTrack.id)
          await playTrack(nextTrack.id)
          return
        } else {
          logger.warn('[Playback] 🔌 Offline: No next track in queue')
          return
        }
      }
    }

    wsSend?.({
      type: 'playback_command',
      data: { command: 'next', skip_reason: 'manual_skip' }
    })
  }, [wsSend, state.current_track, state.queue, playTrack, audioState.isOnline])

  const previous = useCallback(async () => {
    const engine = audio.engineRef.current
    if (engine) {
      const element = engine.getCurrentElement()

      const currentProgress = isActiveDeviceRef.current
        ? (element?.currentTime || 0)
        : (state.progress_ms / 1000)

      if (currentProgress > 3) {
        await audio.seek(0)
        if (audioState.isOnline) {
          wsSend?.({
            type: 'playback_command',
            data: { command: 'seek', position_ms: 0 }
          })
        }
        return
      }
    }

    isManualChangeRef.current = true

    const currentTrackId = state.current_track?.id
    if (currentTrackId) {
      const currentIndex = state.queue?.findIndex(t => t.id === currentTrackId) ?? -1
      if (currentIndex > 0 && state.queue[currentIndex - 1]) {
        const previousTrack = state.queue[currentIndex - 1]

        if (!audioState.isOnline) {
          logger.info('[Playback] 🔌 Offline previous: Playing', previousTrack.id)
          await playTrack(previousTrack.id)
          return
        }

        wsSend?.({
          type: 'playback_command',
          data: { command: 'play', track_id: previousTrack.id }
        })
        return
      }
    }

    if (!audioState.isOnline) {
      logger.warn('[Playback] 🔌 Offline: No previous track in queue')
      return
    }

    wsSend?.({ type: 'playback_command', data: { command: 'previous' } })
  }, [audio, wsSend, state.queue, state.current_track?.id, playTrack, audioState.isOnline])

  const seek = useCallback(async (positionMs) => {
    setState(prev => ({ ...prev, progress_ms: positionMs }))

    audio.seek(positionMs / 1000)

    wsSend?.({
      type: 'playback_command',
      data: { command: 'seek', position_ms: positionMs }
    })
  }, [audio, wsSend])

  const addToQueue = useCallback(async (trackIds) => {
    const response = await api.addToQueue(trackIds)

    if (response.offline && !audioState.isOnline) {
      try {
        const addedTracks = []
        for (const trackId of (response.added || [])) {
          const cached = await cacheManager.getCachedTrack(trackId)
          if (cached) addedTracks.push(cached)
        }

        setState(prevState => ({ ...prevState, queue: [...prevState.queue, ...addedTracks] }))
      } catch (err) {
        logger.error('[PlaybackContext] Failed to update offline queue:', err)
      }
    }

    return response
  }, [audioState.isOnline])

  const removeFromQueue = useCallback(async (trackId) => {
    const response = await api.removeFromQueue(trackId)

    if (response.offline && !audioState.isOnline) {
      setState(prevState => ({
        ...prevState,
        queue: prevState.queue.filter(track => track.id !== trackId),
      }))
    }
  }, [audioState.isOnline])

  const seedRadio = useCallback(async (category = 'all', trackId = null) => {

    const seedTrackId = trackId || state.current_track?.id

    if (seedTrackId && seedTrackId !== state.current_track?.id) {
      isManualChangeRef.current = true
    }

    if (publishRadioState) {
      publishRadioState({ activeSeedMode: category })
    }

    try {
      const response = await api.seedRadio(category, seedTrackId)

      if (response.activeSeedMode !== undefined && response.activeSeedMode !== category) {
        if (publishRadioState) {
          publishRadioState({ activeSeedMode: response.activeSeedMode })
        }
      }

      if (response.offline && !audioState.isOnline && response.state) {
        logger.info('[Playback] 🔌 Offline seedRadio: Loading queue from cache')
        await handlePlaybackState(response.state)
      }
    } catch (error) {
      console.error('[PlaybackContext.seedRadio] Failed to seed radio:', error)
      if (publishRadioState) {
        publishRadioState({ activeSeedMode: null })
      }
    }
  }, [state.current_track?.id, publishRadioState, handlePlaybackState, audioState.isOnline])

  useEffect(() => {
    const justWentOffline = prevIsOnlineRef.current && !audioState.isOnline
    prevIsOnlineRef.current = audioState.isOnline

    if (justWentOffline && audio.engineRef.current && state.current_track?.id) {
      audio.engineRef.current.handleOfflineTransition(
        () => state.current_track?.id,
        async (trackId) => {
          const cached = await cacheManager.getCachedTrack(trackId)
          if (cached) {
            logger.info(`[PlaybackContext] 🔌 Offline: Found cached ${cached.bitrate} version for ${trackId}`)
          }
          return cached
        }
      ).then(result => {
        if (result.switched) {
          audio.setIsCached(true)
          publishAudioState({ isCached: true, bitrate: result.bitrate })
          logger.info(`[PlaybackContext] ✅ Switched to offline cached playback at ${result.bitrate}`)
        } else {
          logger.warn(`[PlaybackContext] ❌ No cached version available for offline playback`)
        }
      })
    }
  }, [audioState.isOnline, state.current_track?.id, audio, publishAudioState])

  useEffect(() => {
    if (audioState.buffering && !audioState.isOnline && !audioState.isCached) {
        logger.warn('[Playback] 🔌 Buffer starved offline, not cached. Policy: Auto-skip')
        next().catch(err => logger.error('[Playback] Auto-skip failed:', err))
    }
  }, [audioState.buffering, audioState.isOnline, audioState.isCached, next])

  const reloadCurrentTrackQuality = useCallback(async () => {
    const currentTrack = stateRef.current.current_track
    const isPlaying = stateRef.current.is_playing
    const isActive = isActiveDeviceRef.current
    const userQuality = settingsStateRef.current.audioQuality

    if (!currentTrack?.id || !isActive) return

    const engine = audio.engineRef.current
    if (!engine) return

    const currentElement = engine.getCurrentElement()
    const currentPosition = currentElement?.currentTime || 0

    try {
      const currentTrackId = currentTrack.id
      const requestedBitrate = userQuality || 'auto'
      const source = await getTrackSource(currentTrackId, requestedBitrate, currentTrack)

      if (!source) return

      await engine.loadTrack(currentTrackId, source.url, {
        isBlobUrl: source.isBlobUrl,
        shouldPlay: false,
        fadeTimeMs: 0,
        useCrossfade: false,
        metadata: { ...currentTrack, bitrate: source.bitrate, fromCache: source.fromCache }
      })

      audio.setIsCached(source.fromCache)
      publishAudioState({ isCached: source.fromCache, bitrate: source.bitrate })

      await engine.seek(currentPosition)

      if (isPlaying) {
        await engine.play()
      }
    } catch (error) {
      logger.error('[PlaybackContext] Failed to reload track quality:', error)
    }
  }, [audio, getTrackSource, publishAudioState])

  const value = useMemo(() => ({
    state,
    connected,
    playTrack,
    togglePlay,
    next,
    previous,
    seek,
    addToQueue,
    removeFromQueue,
    seedRadio,
    reloadCurrentTrackQuality,
    audio,
  }), [
    state, connected, playTrack, togglePlay, next, previous,
    seek, addToQueue, removeFromQueue, seedRadio,
    reloadCurrentTrackQuality, audio
  ])

  return (
    <PlaybackContext.Provider value={value}>
      {children}
    </PlaybackContext.Provider>
  )
}

export function usePlayback() {
  const context = useContext(PlaybackContext)
  if (!context) throw new Error('usePlayback must be used within PlaybackProvider')
  return context
}