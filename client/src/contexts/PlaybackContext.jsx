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
import { AudioInteractionManager } from '../lib/audioInteractionManager'
import { TalkBreakController } from '../lib/talkBreak'
import { MusicBed } from '../lib/musicBed'
import {
  NO_PENDING,
  PENDING_ACK_TIMEOUT_MS,
  advanceCursor,
  classifySnapshot,
  createSyncCursor,
  expectedProgressMs,
  isOutdatedSnapshot,
  markCommandSent,
  reconcileQueue,
  reconcileTrack,
  reconcileValue
} from '../lib/playbackSync'

const PlaybackContext = createContext(null)

const INITIAL_STATE = {
  current_track: null,
  is_playing: false,
  queue: [],
  history: [],
  current_index: 0,
  crossfade_hint: null,
  announcer_hint: null,
}

const INITIAL_DESIRED = {
  track: null,
  isPlaying: false,
  progressMs: 0,
  receivedAt: 0,
  seekVersion: null,
  queue: [],
  history: [],
  crossfadeHint: null,
}

const SEEK_TOLERANCE_MS = 1000
const OPEN_CLAIM_MIN_HIDDEN_MS = 3000
const OPEN_CLAIM_SETTLE_MS = 400
const OPEN_CLAIM_WINDOW_MS = 20000
const PAGE_LOAD = typeof document === 'undefined'
  ? { visible: true, at: 0 }
  : { visible: document.visibilityState === 'visible', at: Date.now() }
const CACHE_LOOKUP_TIMEOUT_MS = 1500
const RESTART_THRESHOLD_S = 3

function stepDisplayTrack(prev, direction) {
  const index = prev.queue.findIndex(t => t.id === prev.current_track?.id)
  const target = index >= 0 ? prev.queue[index + direction] : null
  if (!target) return prev
  return {
    ...prev,
    current_track: {
      ...target,
      generation_params: { title: target.title, artist_name: target.artist_name, style: target.style }
    },
    current_index: index + direction,
    is_playing: true,
  }
}

function mergeDisplayState(prev, data, { includePlayState, keepTrack = false }) {
  const queue = data.queue !== undefined ? reconcileQueue(prev.queue, data.queue ?? []) : prev.queue
  const history = data.history !== undefined ? reconcileQueue(prev.history, data.history ?? []) : prev.history
  const currentTrack = keepTrack ? prev.current_track
    : (data.current_track !== undefined ? reconcileTrack(prev.current_track, data.current_track) : prev.current_track)
  const keptIndex = keepTrack ? queue.findIndex(t => t.id === prev.current_track?.id) : -1
  const currentIndex = keepTrack ? (keptIndex >= 0 ? keptIndex : prev.current_index) : (data.current_index ?? prev.current_index)
  const isPlaying = includePlayState ? (data.is_playing ?? prev.is_playing) : prev.is_playing
  const crossfadeHint = includePlayState ? reconcileValue(prev.crossfade_hint, data.crossfade_hint ?? null) : prev.crossfade_hint
  const announcerHint = includePlayState ? reconcileValue(prev.announcer_hint, data.announcer_hint ?? null) : prev.announcer_hint

  if (queue === prev.queue && history === prev.history && currentTrack === prev.current_track &&
      currentIndex === prev.current_index && isPlaying === prev.is_playing &&
      crossfadeHint === prev.crossfade_hint && announcerHint === prev.announcer_hint) {
    return prev
  }

  return {
    ...prev,
    current_track: currentTrack,
    queue,
    history,
    current_index: currentIndex,
    is_playing: isPlaying,
    crossfade_hint: crossfadeHint,
    announcer_hint: announcerHint,
  }
}

export function PlaybackProvider({ children }) {
  const [state, setState] = useState(INITIAL_STATE)
  const progressMsRef = useRef(0)
  const { user } = useAuth()
  const { reportEngineStatus, publishAudioState, publishRadioState, audioState, settingsState, engineState } = useUIState()
  const settingsStateRef = useRef(settingsState)
  const { getEffectiveBitrate } = useNetwork()
  const { send: wsSend, connected: wsConnected } = useContext(WebSocketContext) || {}
  const deviceId = useMemo(() => getDeviceId(), [])

  const wsSendRef = useRef(wsSend)
  useEffect(() => { wsSendRef.current = wsSend }, [wsSend])

  const getEffectiveBitrateRef = useRef(getEffectiveBitrate)
  useEffect(() => { getEffectiveBitrateRef.current = getEffectiveBitrate }, [getEffectiveBitrate])

  const publishAudioStateRef = useRef(publishAudioState)
  useEffect(() => { publishAudioStateRef.current = publishAudioState }, [publishAudioState])

  const audio = useAudio()
  const prevIsOnlineRef = useRef(audioState.isOnline)

  const serverTalkBreakRef = useRef(null)
  const talkBreakDisplayRef = useRef(null)
  const publishTalkBreakRef = useRef(null)
  const [talkBreak] = useState(() => new TalkBreakController({ log: logger }))

  useEffect(() => {
    const getEngine = () => audio.getEngine() || null
    const bed = new MusicBed(getEngine)
    talkBreak.configure({
      getEngine,
      bed,
      send: (type, data) => { void wsSendRef.current?.({ type, data }) },
      report: () => publishTalkBreakRef.current?.()
    })
    return () => bed.destroy()
  }, [audio, talkBreak])

  const failedTracksRef = useRef(new Map())

  const stateRef = useRef(state)
  const userRef = useRef(user)
  const isActiveDeviceRef = useRef(false)
  const activeDeviceIdRef = useRef(null)
  const activeDeviceOnlineRef = useRef(false)
  const wsConnectedRef = useRef(false)
  const stateSinceConnectRef = useRef(false)
  const openIntentRef = useRef(PAGE_LOAD.visible ? { at: PAGE_LOAD.at, reason: 'load' } : null)
  const hiddenSinceRef = useRef(PAGE_LOAD.visible ? null : { at: PAGE_LOAD.at, fromLoad: true })
  const claimTimerRef = useRef(null)
  const tryOpenClaimRef = useRef(null)

  const desiredRef = useRef(INITIAL_DESIRED)
  const cursorRef = useRef(createSyncCursor())
  const seqRef = useRef(0)
  const pendingRef = useRef(NO_PENDING)
  const optimisticTrackRef = useRef(false)
  const deferredRef = useRef(null)
  const deferTimerRef = useRef(null)
  const reconcileGenRef = useRef(0)
  const reconcileScheduledRef = useRef(false)
  const appliedSeekVersionRef = useRef(null)
  const forceSeekRef = useRef(false)
  const manualSwitchRef = useRef(false)
  const boundEngineRef = useRef(null)
  const gestureRetryRef = useRef(null)

  const lastServerProgressRef = useRef(0)
  const lastServerUpdateTimeRef = useRef(0)

  const reconcileAudioRef = useRef(null)
  const triggerPreloadRef = useRef(null)
  const playTrackRef = useRef(null)
  const handlePlaybackStateRef = useRef(null)

  useEffect(() => { stateRef.current = state }, [state])
  useEffect(() => { userRef.current = user }, [user])
  useEffect(() => { settingsStateRef.current = settingsState }, [settingsState])

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

  const publishTalkBreak = useCallback(() => {
    let view = null
    if (isActiveDeviceRef.current) {
      view = talkBreak.display()
    } else {
      const server = serverTalkBreakRef.current
      if (server?.status === 'on_air') view = { id: server.id, kind: server.kind, label: server.label, title: server.title, paused: false }
    }
    const key = view ? `${view.id}:${view.label}:${view.paused}` : null
    if (key === talkBreakDisplayRef.current) return
    talkBreakDisplayRef.current = key
    reportEngineStatus({ talkBreak: view })
  }, [reportEngineStatus, talkBreak])

  useEffect(() => { publishTalkBreakRef.current = publishTalkBreak }, [publishTalkBreak])

  useEffect(() => () => talkBreak.destroy(), [talkBreak])

  const setProgressMs = useCallback((progressMs) => {
    progressMsRef.current = progressMs
    lastServerProgressRef.current = progressMs
    lastServerUpdateTimeRef.current = Date.now()
    reportEngineStatus({ progress_ms: progressMs })
  }, [reportEngineStatus])

  const bitrateToNumber = useCallback((bitrate) => {
    if (bitrate === 'auto' || bitrate === '256k') return 256
    if (bitrate === '192k') return 192
    return 128
  }, [])

  const getTrackSource = useCallback(async (trackId, requestedBitrate, trackMetadata = null) => {
    const isOnline = uiState.audioState.isOnline
    const lookup = cacheManager.getCachedTrack(trackId).catch(() => null)
    const cached = isOnline
      ? await Promise.race([lookup, new Promise(resolve => setTimeout(() => resolve(null), CACHE_LOOKUP_TIMEOUT_MS))])
      : await lookup
    const dataSaverMode = settingsStateRef.current.dataSaverMode

    if (cached) {
      if (dataSaverMode) {
        return { url: URL.createObjectURL(cached.audioBlob), isBlobUrl: true, bitrate: cached.bitrate, fromCache: true }
      }

      const dominated = isOnline &&
                        requestedBitrate !== 'auto' &&
                        bitrateToNumber(requestedBitrate) > bitrateToNumber(cached.bitrate)

      if (!dominated) {
        return { url: URL.createObjectURL(cached.audioBlob), isBlobUrl: true, bitrate: cached.bitrate, fromCache: true }
      }
    }

    if (!isOnline) return null

    const bitrate = dataSaverMode ? '128k' :
      (requestedBitrate === 'auto' ? getEffectiveBitrateRef.current('auto', !!userRef.current) : requestedBitrate)
    if (trackMetadata) cacheManager.beginTrackStream(trackId, { ...trackMetadata, bitrate })

    return { url: api.getStreamUrl(trackId, bitrate), isBlobUrl: false, bitrate, fromCache: false }
  }, [bitrateToNumber])

  const scheduleReconcile = useCallback(() => {
    if (reconcileScheduledRef.current) return
    reconcileScheduledRef.current = true
    queueMicrotask(() => {
      reconcileScheduledRef.current = false
      void reconcileAudioRef.current?.()
    })
  }, [])

  const clearDeferTimer = useCallback(() => {
    if (deferTimerRef.current) {
      clearTimeout(deferTimerRef.current)
      deferTimerRef.current = null
    }
  }, [])

  const armDeferTimerRef = useRef(null)
  const armDeferTimer = useCallback(() => {
    if (deferTimerRef.current) return
    deferTimerRef.current = setTimeout(() => {
      deferTimerRef.current = null
      const deferred = deferredRef.current
      if (!pendingRef.current.seq) return
      if (!wsConnectedRef.current || Date.now() - pendingRef.current.since <= PENDING_ACK_TIMEOUT_MS) {
        armDeferTimerRef.current?.()
        return
      }
      logger.warn(`[PlaybackContext] ⏱️ Command seq ${pendingRef.current.seq} not acknowledged, accepting latest server state`)
      pendingRef.current = NO_PENDING
      optimisticTrackRef.current = false
      deferredRef.current = null
      if (deferred) handlePlaybackStateRef.current?.(deferred, { replay: true })
    }, PENDING_ACK_TIMEOUT_MS + 100)
  }, [])

  useEffect(() => { armDeferTimerRef.current = armDeferTimer }, [armDeferTimer])

  const sendTransport = useCallback((type, data) => {
    const seq = ++seqRef.current
    pendingRef.current = markCommandSent(pendingRef.current, seq, Date.now())
    armDeferTimer()
    const send = wsSendRef.current
    if (!send) {
      pendingRef.current = NO_PENDING
      optimisticTrackRef.current = false
      return seq
    }
    void send({ type, data: { ...data, seq } })
    return seq
  }, [armDeferTimer])

  const sendCommand = useCallback((command, extra = {}) => {
    return sendTransport('playback_command', { command, ...extra })
  }, [sendTransport])

  const applySnapshot = useCallback((data) => {
    const now = Date.now()
    const prevDesired = desiredRef.current
    const desired = {
      track: data.current_track !== undefined ? data.current_track : prevDesired.track,
      isPlaying: data.is_playing ?? prevDesired.isPlaying,
      progressMs: data.progress_ms ?? prevDesired.progressMs,
      receivedAt: now,
      seekVersion: data.seek_version ?? prevDesired.seekVersion,
      queue: data.queue ?? prevDesired.queue,
      history: data.history ?? prevDesired.history,
      crossfadeHint: data.crossfade_hint ?? null,
    }
    desiredRef.current = desired

    setState(prev => mergeDisplayState(prev, data, { includePlayState: true }))

    const engine = audio.getEngine()
    if (engine && desired.crossfadeHint && desired.track?.id) {
      engine.setCrossfadeHint(desired.track.id, desired.crossfadeHint)
    }

    const engineOnTrack = isActiveDeviceRef.current && engine?.getCurrentTrackId() === desired.track?.id && engine?.hasCurrentSource()
    if (!engineOnTrack && data.progress_ms !== undefined) {
      setProgressMs(data.progress_ms)
    }

    if (isActiveDeviceRef.current) scheduleReconcile()
  }, [audio, scheduleReconcile, setProgressMs])

  const handlePlaybackState = useCallback((data, { replay = false } = {}) => {
    if (!data) return

    if (!replay) {
      if (isOutdatedSnapshot(data, cursorRef.current)) {
        logger.info(`[PlaybackContext] ⏪ Dropping outdated playback_state v${data.version} (have v${cursorRef.current.version})`)
        return
      }
      cursorRef.current = advanceCursor(data, cursorRef.current)
    }

    const weAreActive = !!data.active_device_id && data.active_device_id === deviceId
    activeDeviceIdRef.current = data.active_device_id ?? null
    if (!replay && typeof data.state_epoch === 'string' && !stateSinceConnectRef.current) {
      stateSinceConnectRef.current = true
      queueMicrotask(() => tryOpenClaimRef.current?.())
    }
    activeDeviceOnlineRef.current = data.active_device_online !== undefined ? !!data.active_device_online : !!data.active_device_id

    if (weAreActive !== isActiveDeviceRef.current) {
      logger.info(`[PlaybackContext] 🔄 Device status changed: ${isActiveDeviceRef.current ? 'active' : 'inactive'} → ${weAreActive ? 'active' : 'inactive'}`)
      isActiveDeviceRef.current = weAreActive
      reconcileGenRef.current++
      audio.setActiveDevice(weAreActive)
      if (weAreActive) {
        forceSeekRef.current = true
      }
    }

    reportEngineStatus({
      isActiveDevice: weAreActive,
      activeDeviceId: activeDeviceIdRef.current,
      activeDeviceOnline: activeDeviceOnlineRef.current
    })

    serverTalkBreakRef.current = data.talk_break ?? null
    talkBreak.setActive(weAreActive)
    talkBreak.onServerState(data.talk_break ?? null, {
      currentTrackId: data.current_track?.id ?? null,
      isPlaying: data.is_playing !== false
    })
    publishTalkBreak()

    if (data.activeSeedMode !== undefined && publishRadioState) {
      publishRadioState({ activeSeedMode: data.activeSeedMode })
    }

    const verdict = classifySnapshot(data, deviceId, pendingRef.current, Date.now())
    if (verdict !== 'apply') {
      deferredRef.current = data
      if (verdict === 'display') {
        const keepTrack = optimisticTrackRef.current
        setState(prev => mergeDisplayState(prev, data, { includePlayState: false, keepTrack }))
      }
      armDeferTimer()
      if (weAreActive) scheduleReconcile()
      return
    }

    pendingRef.current = NO_PENDING
    optimisticTrackRef.current = false
    deferredRef.current = null
    clearDeferTimer()
    applySnapshot(data)
  }, [audio, deviceId, reportEngineStatus, publishRadioState, armDeferTimer, clearDeferTimer, applySnapshot, scheduleReconcile, publishTalkBreak, talkBreak])

  useEffect(() => { handlePlaybackStateRef.current = handlePlaybackState }, [handlePlaybackState])

  const handleCrossfadeStart = useCallback((fromTrackId, toTrackId, fadeTimeMs, info) => {
    logger.info(`[Audio] 🎵 CROSSFADE: ${fromTrackId?.slice(0, 8)} → ${toTrackId?.slice(0, 8)} (${fadeTimeMs}ms)`)

    const desired = desiredRef.current
    const engineMetadata = audio.getEngine()?.currentSlot?.metadata
    const nextTrack = desired.queue.find(t => t.id === toTrackId) ||
      desired.history.find(t => t.id === toTrackId) ||
      (engineMetadata?.id === toTrackId ? engineMetadata : null)

    if (!nextTrack) {
      logger.error(`[PlaybackContext] ❌ Engine crossfaded to ${toTrackId?.slice(0, 8)} but track not found in state!`)
      return
    }

    desiredRef.current = {
      ...desired,
      track: nextTrack,
      isPlaying: true,
      progressMs: 0,
      receivedAt: Date.now(),
    }

    const newIndex = desired.queue.findIndex(t => t.id === toTrackId)
    setState(prev => ({
      ...prev,
      current_track: reconcileTrack(prev.current_track, nextTrack),
      current_index: newIndex !== -1 ? newIndex : prev.current_index + 1
    }))
    setProgressMs(0)

    if (uiState.audioState.isOnline) {
      sendTransport('track_transition', {
        from_track_id: fromTrackId,
        to_track_id: nextTrack.id,
        transition_type: 'crossfade',
        fade_duration_ms: fadeTimeMs,
        crossfade_info: info || null
      })
      logger.info('[PlaybackContext] 📡 Sent track_transition to backend')
    } else {
      logger.info('[PlaybackContext] 🔌 Offline auto-advance to next track')
      void playTrackRef.current?.(nextTrack.id)
    }
  }, [audio, sendTransport, setProgressMs])

  const handleCrossfadeStartRef = useRef(handleCrossfadeStart)
  useEffect(() => { handleCrossfadeStartRef.current = handleCrossfadeStart }, [handleCrossfadeStart])

  const bindEngine = useCallback((engine) => {
    if (!engine) return
    engine.setActiveDevice(isActiveDeviceRef.current)
    if (boundEngineRef.current === engine) return
    boundEngineRef.current = engine

    engine.onBufferStatusChange = (isBuffering) => {
      publishAudioStateRef.current({ buffering: isBuffering })
    }

    engine.onCrossfadeStateChange = (isCrossfading) => {
      reportEngineStatus({ isCrossfading })
      if (!isCrossfading) {
        const isFromCache = engine.getCurrentElement()?.getAttribute('data-blob-url') === 'true'
        const bitrate = engine.currentSlot?.metadata?.bitrate || settingsStateRef.current.audioQuality
        publishAudioStateRef.current({ isCached: isFromCache, bitrate })
        triggerPreloadRef.current?.()
      }
    }

    engine.onCrossfadeStart = (...args) => handleCrossfadeStartRef.current?.(...args)
    engine.onHoldReached = (trackId) => talkBreak.onHoldReached(trackId)
  }, [reportEngineStatus, talkBreak])

  const ensureEngine = useCallback(async () => {
    const ok = await audio.initializeAudio()
    if (!ok) return null
    const engine = audio.getEngine()
    bindEngine(engine)
    return engine
  }, [audio, bindEngine])

  const triggerPreload = useCallback(() => {
    if (!isActiveDeviceRef.current) return
    const engine = audio.getEngine()
    if (!engine || engine.isTransportBusy()) return

    const desired = desiredRef.current
    const currentTrackId = desired.track?.id
    if (!currentTrackId || engine.getCurrentTrackId() !== currentTrackId) return

    const currentIndex = desired.queue.findIndex(t => t.id === currentTrackId)
    const nextTrack = currentIndex >= 0 ? desired.queue[currentIndex + 1] : null
    if (!nextTrack || engine.getNextTrackId() === nextTrack.id) return

    engine.preloadTrack(nextTrack.id, {
      resolveSource: () => getTrackSource(nextTrack.id, settingsStateRef.current.audioQuality, nextTrack),
      metadata: nextTrack
    }).catch(error => logger.error('[PlaybackContext] Preload failed:', error))
  }, [audio, getTrackSource])

  useEffect(() => { triggerPreloadRef.current = triggerPreload }, [triggerPreload])

  const retryOnGesture = useCallback(() => {
    if (gestureRetryRef.current) return
    gestureRetryRef.current = AudioInteractionManager.onUserGesture(() => {
      gestureRetryRef.current = null
      scheduleReconcile()
    })
  }, [scheduleReconcile])

  const recordFailure = useCallback((trackId) => {
    const info = failedTracksRef.current.get(trackId) || { attempts: 0 }
    const attempts = info.attempts + 1
    failedTracksRef.current.set(trackId, { attempts, lastAttempt: Date.now() })
    if (attempts < 3) {
      setTimeout(() => {
        if (desiredRef.current.track?.id === trackId) scheduleReconcile()
      }, 1500 * attempts)
    }
  }, [scheduleReconcile])

  const reconcileAudio = useCallback(async () => {
    const gen = ++reconcileGenRef.current
    if (!isActiveDeviceRef.current) return

    const startDesired = desiredRef.current
    const track = startDesired.track
    if (!track?.id) return

    const engine = await ensureEngine()
    if (!engine || gen !== reconcileGenRef.current || !isActiveDeviceRef.current) return

    let switchResult = null
    if (engine.getCurrentTrackId() !== track.id || !engine.hasCurrentSource()) {
      const failure = failedTracksRef.current.get(track.id)
      if (failure && failure.attempts >= 3 && Date.now() - failure.lastAttempt < 10000) return

      const manual = manualSwitchRef.current
      manualSwitchRef.current = false
      const startsNearZero = expectedProgressMs(startDesired) < SEEK_TOLERANCE_MS

      try {
        switchResult = await engine.switchTo(track.id, {
          resolveSource: () => getTrackSource(track.id, settingsStateRef.current.audioQuality, track),
          metadata: { ...track, crossfade_hint: startDesired.crossfadeHint || track.crossfade_hint || null },
          fadeTimeMs: manual ? 50 : 3000,
          preloadedFadeMs: 50,
          shouldPlay: startDesired.isPlaying && startsNearZero && !forceSeekRef.current,
        })
      } catch (error) {
        logger.error('[PlaybackContext] Track load failed:', error)
        recordFailure(track.id)
        return
      }

      if (gen !== reconcileGenRef.current) return
      const status = switchResult?.status
      if (status === 'unavailable') {
        recordFailure(track.id)
        return
      }
      if (status !== 'current' && status !== 'crossfaded' && status !== 'loaded') return

      failedTracksRef.current.delete(track.id)
      const metadata = engine.currentSlot?.metadata
      publishAudioStateRef.current({ isCached: !!metadata?.fromCache, bitrate: metadata?.bitrate || settingsStateRef.current.audioQuality })
    }

    if (engine.getCurrentTrackId() !== track.id) return

    const desired = desiredRef.current
    if (desired.track?.id !== track.id) return

    if (engine.isHoldReached(track.id)) {
      if (gen === reconcileGenRef.current) triggerPreload()
      return
    }

    const freshLoad = switchResult?.status === 'loaded'
    const seekVersionChanged = desired.seekVersion !== appliedSeekVersionRef.current
    if (forceSeekRef.current || freshLoad || seekVersionChanged) {
      forceSeekRef.current = false
      appliedSeekVersionRef.current = desired.seekVersion
      const target = expectedProgressMs(desired)
      const element = engine.getCurrentElement()
      const current = element ? element.currentTime * 1000 : 0
      if (Math.abs(target - current) > SEEK_TOLERANCE_MS) {
        logger.info(`[PlaybackContext] 🎯 Syncing position to ${Math.round(target)}ms (audio at ${Math.round(current)}ms)`)
        await engine.seek(target / 1000)
        if (gen !== reconcileGenRef.current) return
      }
    }

    const wantPlaying = desiredRef.current.isPlaying
    if (wantPlaying && !engine.isPlaying()) {
      await engine.play()
      if (gen !== reconcileGenRef.current) return
      if (engine.getCurrentElement()?.paused) {
        logger.warn('[PlaybackContext] Playback blocked until next user gesture')
        retryOnGesture()
      }
    } else if (!wantPlaying && engine.isPlaying()) {
      engine.pause()
    }

    if (gen === reconcileGenRef.current) triggerPreload()
  }, [ensureEngine, getTrackSource, recordFailure, retryOnGesture, triggerPreload])

  useEffect(() => { reconcileAudioRef.current = reconcileAudio }, [reconcileAudio])

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
    wsConnectedRef.current = !!wsConnected
    if (!wsConnected) stateSinceConnectRef.current = false
    if (wsConnected && pendingRef.current.seq) {
      pendingRef.current = { ...pendingRef.current, since: Date.now() }
    }
  }, [wsConnected])

  const tryOpenClaim = useCallback(() => {
    if (claimTimerRef.current) {
      clearTimeout(claimTimerRef.current)
      claimTimerRef.current = null
    }
    const intent = openIntentRef.current
    if (!intent) return
    const age = Date.now() - intent.at
    if (age > OPEN_CLAIM_WINDOW_MS || document.visibilityState !== 'visible' ||
        settingsStateRef.current.autoClaimOnOpen === false) {
      openIntentRef.current = null
      return
    }
    if (age < OPEN_CLAIM_SETTLE_MS) {
      claimTimerRef.current = setTimeout(() => tryOpenClaimRef.current?.(), OPEN_CLAIM_SETTLE_MS - age)
      return
    }
    if (!stateSinceConnectRef.current || !uiState.audioState.isOnline) return
    if (typeof document.hasFocus === 'function' && !document.hasFocus()) return
    openIntentRef.current = null
    if (isActiveDeviceRef.current || !activeDeviceIdRef.current) return
    logger.info(`[PlaybackContext] 📲 Opened on this device (${intent.reason}) - taking over playback`)
    void ensureEngine()
    sendCommand('claim', { reason: intent.reason })
  }, [ensureEngine, sendCommand])

  useEffect(() => { tryOpenClaimRef.current = tryOpenClaim }, [tryOpenClaim])

  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState !== 'visible') {
        if (!hiddenSinceRef.current) hiddenSinceRef.current = { at: Date.now(), fromLoad: false }
        openIntentRef.current = null
        return
      }
      const hidden = hiddenSinceRef.current
      hiddenSinceRef.current = null
      if (!hidden) return
      if (hidden.fromLoad || Date.now() - hidden.at >= OPEN_CLAIM_MIN_HIDDEN_MS) {
        openIntentRef.current = { at: Date.now(), reason: hidden.fromLoad ? 'first_show' : 'foreground' }
        tryOpenClaimRef.current?.()
      }
    }
    const handleFocus = () => tryOpenClaimRef.current?.()
    document.addEventListener('visibilitychange', handleVisibility)
    window.addEventListener('focus', handleFocus)
    tryOpenClaimRef.current?.()
    return () => {
      document.removeEventListener('visibilitychange', handleVisibility)
      window.removeEventListener('focus', handleFocus)
      if (claimTimerRef.current) {
        clearTimeout(claimTimerRef.current)
        claimTimerRef.current = null
      }
    }
  }, [])

  useEffect(() => {
    if (wsConnected) tryOpenClaimRef.current?.()
  }, [wsConnected])

  useEffect(() => {
    const reconcileGen = reconcileGenRef
    return () => {
      clearDeferTimer()
      reconcileGen.current++
      if (gestureRetryRef.current) {
        gestureRetryRef.current()
        gestureRetryRef.current = null
      }
    }
  }, [clearDeferTimer])

  const initialOfflineRef = useRef({ isOnline: audioState.isOnline, hasTrack: !!state.current_track })
  useEffect(() => {
    const initOfflineState = async () => {
      if (initialOfflineRef.current.isOnline || initialOfflineRef.current.hasTrack) {
        return
      }

      logger.info('[PlaybackContext] 🔌 Offline cold boot - initializing from cached state')

      try {
        const offlineState = await offlineBackend.getPlaybackState()
        if (offlineState) {
          logger.info('[PlaybackContext] 🔌 Loaded offline state:', offlineState.current_track?.title)
          handlePlaybackStateRef.current?.(offlineState)
        } else {
          logger.info('[PlaybackContext] 🔌 No offline state available')
        }
      } catch (err) {
        logger.error('[PlaybackContext] 🔌 Failed to load offline state:', err)
      }
    }

    void initOfflineState()
  }, [])

  const isActiveDevice = engineState.isActiveDevice
  const currentTrackId = state.current_track?.id
  const currentDurationMs = state.current_track?.duration_ms

  useEffect(() => {
    if (isActiveDevice || !state.is_playing || !currentTrackId) return

    const progressSimulation = setInterval(() => {
      if (isActiveDeviceRef.current || !stateRef.current.is_playing) return
      const elapsed = Date.now() - lastServerUpdateTimeRef.current
      const estimatedProgress = lastServerProgressRef.current + elapsed
      const maxProgress = currentDurationMs || Infinity
      const clampedProgress = Math.min(estimatedProgress, maxProgress)
      progressMsRef.current = clampedProgress
      reportEngineStatus({ progress_ms: clampedProgress })
    }, 250)

    return () => clearInterval(progressSimulation)
  }, [isActiveDevice, state.is_playing, currentTrackId, currentDurationMs, reportEngineStatus])

  useEffect(() => {
    if (isActiveDevice) scheduleReconcile()
  }, [isActiveDevice, scheduleReconcile])

  useEffect(() => { triggerPreload() }, [state.queue, triggerPreload])

  useEffect(() => {
    if (!wsSend) return

    const shouldSkipTick = () => {
      if (!isActiveDeviceRef.current || !desiredRef.current.track) return true
      if (pendingRef.current.seq) return true
      const engine = audio.getEngine()
      if (!engine || engine.isTransportBusy()) return true
      return engine.getCurrentTrackId() !== desiredRef.current.track.id
    }

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
      if (shouldSkipTick() || !wsConnectedRef.current) return

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
        bufferedAhead = 0
      }

      void wsSend({
        type: 'playback_heartbeat',
        data: {
          track_id: desiredRef.current.track.id,
          actual_position_ms: actualPos,
          is_playing: !!audio.getEngine()?.isPlaying() || (talkBreak.isOnAir() && !talkBreak.isPaused()),
          buffered_ahead_ms: bufferedAhead,
          timestamp: Date.now()
        }
      })
    }, 5000)

    return () => {
      clearInterval(heartbeatInterval)
      clearInterval(statusInterval)
    }
  }, [audio, wsSend, talkBreak])

  useEffect(() => {
    if (!isActiveDevice) return

    const progressInterval = setInterval(() => {
      const engine = audio.getEngine()
      const trackId = desiredRef.current.track?.id
      if (!engine || !trackId || engine.getCurrentTrackId() !== trackId) return

      const currentElement = engine.getCurrentElement()
      if (!currentElement || currentElement.paused) return

      const currentTime = Math.floor(currentElement.currentTime * 1000)

      if (Math.abs(currentTime - progressMsRef.current) > 100) {
        setProgressMs(currentTime)
      }
    }, 250)

    return () => clearInterval(progressInterval)
  }, [isActiveDevice, audio, setProgressMs])

  const playTrack = useCallback(async (trackId) => {
    if (!uiState.audioState.isOnline) {
      try {
        logger.info('[Playback] 🔌 Offline mode: Loading cached track', { trackId })
        manualSwitchRef.current = true
        const response = await api.play(trackId)

        if (response.offline && response.state) {
          handlePlaybackStateRef.current?.(response.state)
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

    talkBreak.onUserTransport('play')
    manualSwitchRef.current = true
    sendCommand('play', { track_id: trackId })
  }, [sendCommand, talkBreak])

  useEffect(() => { playTrackRef.current = playTrack }, [playTrack])

  const togglePlay = useCallback(async () => {
    const wasPlaying = stateRef.current.is_playing
    const isActive = isActiveDeviceRef.current

    if (!isActive && (!activeDeviceIdRef.current || !activeDeviceOnlineRef.current)) {
      logger.info('[PlaybackContext] ▶️ Nothing is playing elsewhere - claiming playback for this device')
      void ensureEngine()
      setState(prev => (prev.is_playing ? prev : { ...prev, is_playing: true }))
      sendCommand('play', { claim: true })
      return
    }

    const nowPlaying = !wasPlaying
    setState(prev => ({ ...prev, is_playing: nowPlaying }))

    if (isActive && talkBreak.isOnAir()) {
      desiredRef.current = { ...desiredRef.current, isPlaying: nowPlaying, receivedAt: Date.now() }
      if (nowPlaying) talkBreak.resume()
      else talkBreak.pause()
      publishTalkBreak()
      sendCommand(nowPlaying ? 'play' : 'pause')
      return
    }

    if (isActive) {
      const element = audio.getEngine()?.getCurrentElement()
      desiredRef.current = {
        ...desiredRef.current,
        isPlaying: nowPlaying,
        progressMs: element ? Math.floor(element.currentTime * 1000) : progressMsRef.current,
        receivedAt: Date.now()
      }
      if (nowPlaying) {
        audio.play().catch(err => logger.error('[PlaybackContext] Play failed:', err))
        scheduleReconcile()
      } else {
        audio.pause()
      }
    }

    sendCommand(nowPlaying ? 'play' : 'pause')
  }, [audio, ensureEngine, scheduleReconcile, sendCommand, publishTalkBreak, talkBreak])

  const next = useCallback(async () => {
    if (!uiState.audioState.isOnline) {
      const desired = desiredRef.current
      const currentTrackId = desired.track?.id
      if (currentTrackId && desired.queue.length > 0) {
        const currentIndex = desired.queue.findIndex(t => t.id === currentTrackId)
        if (currentIndex >= 0 && currentIndex < desired.queue.length - 1) {
          const nextTrack = desired.queue[currentIndex + 1]
          logger.info('[Playback] 🔌 Offline next: Playing', nextTrack.id)
          await playTrack(nextTrack.id)
          return
        }
        logger.warn('[Playback] 🔌 Offline: No next track in queue')
        return
      }
    }

    talkBreak.onUserTransport('next')
    manualSwitchRef.current = true
    sendCommand('next', { skip_reason: 'manual_skip' })
    optimisticTrackRef.current = true
    queueMicrotask(() => setState(prev => stepDisplayTrack(prev, 1)))
  }, [playTrack, sendCommand, talkBreak])

  const seek = useCallback(async (positionMs) => {
    talkBreak.onUserTransport('seek')
    setProgressMs(positionMs)

    if (isActiveDeviceRef.current) {
      desiredRef.current = { ...desiredRef.current, progressMs: positionMs, receivedAt: Date.now() }
      void audio.seek(positionMs / 1000)
    }

    sendCommand('seek', { position_ms: positionMs })
  }, [audio, sendCommand, setProgressMs, talkBreak])

  const previous = useCallback(async () => {
    const engine = audio.getEngine()
    const desired = desiredRef.current
    const settled = !pendingRef.current.seq && (!isActiveDeviceRef.current ||
      (engine && !engine.isTransportBusy() && engine.getCurrentTrackId() === desired.track?.id))

    if (settled) {
      const element = engine?.getCurrentElement()
      const currentProgress = isActiveDeviceRef.current
        ? (element?.currentTime || 0)
        : (progressMsRef.current / 1000)

      if (currentProgress > RESTART_THRESHOLD_S) {
        await seek(0)
        return
      }
    }

    if (!uiState.audioState.isOnline) {
      const currentTrackId = desired.track?.id
      const currentIndex = desired.queue.findIndex(t => t.id === currentTrackId)
      if (currentIndex > 0 && desired.queue[currentIndex - 1]) {
        const previousTrack = desired.queue[currentIndex - 1]
        logger.info('[Playback] 🔌 Offline previous: Playing', previousTrack.id)
        await playTrack(previousTrack.id)
        return
      }
      logger.warn('[Playback] 🔌 Offline: No previous track in queue')
      return
    }

    talkBreak.onUserTransport('previous')
    manualSwitchRef.current = true
    sendCommand('previous')
    optimisticTrackRef.current = true
    queueMicrotask(() => setState(prev => stepDisplayTrack(prev, -1)))
  }, [audio, seek, playTrack, sendCommand, talkBreak])

  const transferPlayback = useCallback(async (targetDeviceId = null) => {
    const target = targetDeviceId || deviceId
    if (target === deviceId) {
      await ensureEngine()
    }
    return api.activateDevice(targetDeviceId)
  }, [deviceId, ensureEngine])

  const addToQueue = useCallback(async (trackIds) => {
    const response = await api.addToQueue(trackIds)

    if (response.offline && !uiState.audioState.isOnline) {
      try {
        const addedTracks = []
        for (const trackId of (response.added || [])) {
          const cached = await cacheManager.getCachedTrack(trackId)
          if (cached) addedTracks.push(cached)
        }

        setState(prevState => ({ ...prevState, queue: [...prevState.queue, ...addedTracks] }))
        desiredRef.current = { ...desiredRef.current, queue: [...desiredRef.current.queue, ...addedTracks] }
      } catch (err) {
        logger.error('[PlaybackContext] Failed to update offline queue:', err)
      }
    }

    return response
  }, [])

  const removeFromQueue = useCallback(async (trackId) => {
    const response = await api.removeFromQueue(trackId)

    if (response.offline && !uiState.audioState.isOnline) {
      setState(prevState => ({
        ...prevState,
        queue: prevState.queue.filter(track => track.id !== trackId),
      }))
      desiredRef.current = { ...desiredRef.current, queue: desiredRef.current.queue.filter(track => track.id !== trackId) }
    }
  }, [])

  const seedRadio = useCallback(async (category = 'all', trackId = null) => {
    const currentId = stateRef.current.current_track?.id
    const seedTrackId = trackId || currentId

    if (seedTrackId && seedTrackId !== currentId) {
      manualSwitchRef.current = true
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

      if (response.offline && !uiState.audioState.isOnline && response.state) {
        logger.info('[Playback] 🔌 Offline seedRadio: Loading queue from cache')
        handlePlaybackStateRef.current?.(response.state)
      }
    } catch (error) {
      console.error('[PlaybackContext.seedRadio] Failed to seed radio:', error)
      if (publishRadioState) {
        publishRadioState({ activeSeedMode: null })
      }
    }
  }, [publishRadioState])

  useEffect(() => {
    const justWentOffline = prevIsOnlineRef.current && !audioState.isOnline
    prevIsOnlineRef.current = audioState.isOnline

    const engine = audio.getEngine()
    const trackId = desiredRef.current.track?.id
    if (justWentOffline && engine && trackId && isActiveDeviceRef.current) {
      engine.handleOfflineTransition(
        () => desiredRef.current.track?.id,
        async (id) => {
          const cached = await cacheManager.getCachedTrack(id)
          if (cached) {
            logger.info(`[PlaybackContext] 🔌 Offline: Found cached ${cached.bitrate} version for ${id}`)
          }
          return cached
        }
      ).then(result => {
        if (result.switched) {
          publishAudioStateRef.current({ isCached: true, bitrate: result.bitrate })
          logger.info(`[PlaybackContext] ✅ Switched to offline cached playback at ${result.bitrate}`)
        } else {
          logger.warn(`[PlaybackContext] ❌ No cached version available for offline playback`)
        }
      }).catch(err => logger.error('[PlaybackContext] Offline switch failed:', err))
    }
  }, [audioState.isOnline, audio])

  useEffect(() => {
    if (audioState.buffering && !audioState.isOnline && !audioState.isCached) {
        logger.warn('[Playback] 🔌 Buffer starved offline, not cached. Policy: Auto-skip')
        next().catch(err => logger.error('[Playback] Auto-skip failed:', err))
    }
  }, [audioState.buffering, audioState.isOnline, audioState.isCached, next])

  const reloadCurrentTrackQuality = useCallback(async () => {
    const currentTrack = desiredRef.current.track
    const userQuality = settingsStateRef.current.audioQuality

    if (!currentTrack?.id || !isActiveDeviceRef.current) return

    const engine = audio.getEngine()
    if (!engine || engine.getCurrentTrackId() !== currentTrack.id) return

    const currentElement = engine.getCurrentElement()
    const currentPosition = currentElement?.currentTime || 0

    try {
      const source = await getTrackSource(currentTrack.id, userQuality || 'auto', currentTrack)
      if (!source) return

      const result = await engine.reloadCurrent(currentTrack.id, source, {
        metadata: { ...currentTrack, bitrate: source.bitrate, fromCache: source.fromCache },
        positionSeconds: currentPosition,
        shouldPlay: desiredRef.current.isPlaying
      })

      if (result?.status === 'loaded') {
        publishAudioStateRef.current({ isCached: source.fromCache, bitrate: source.bitrate })
      }
    } catch (error) {
      logger.error('[PlaybackContext] Failed to reload track quality:', error)
    }
  }, [audio, getTrackSource])

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
    transferPlayback,
    audio,
    talkBreak,
  }), [
    state, connected, playTrack, togglePlay, next, previous,
    seek, addToQueue, removeFromQueue, seedRadio,
    reloadCurrentTrackQuality, transferPlayback, audio, talkBreak
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
