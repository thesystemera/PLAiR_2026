import { useCallback, useEffect, useRef, useState } from 'react'
import { CloudOff, Gauge } from 'lucide-react'
import { api } from '../lib/api'
import { cacheManager } from '../lib/cacheManager'
import { logger } from '../lib/logger'
import { fetchFinishedUploadJob, uploadJobTitle } from '../lib/uploadJobs'
import { useUISelector, useUIStateGetter, uiState } from '../contexts/UIStateContext'
import { onSettingsChanged, pickValidSettings } from '../lib/settings'
import { deviceKind } from '../lib/session'
import { backgroundDownloader } from '../lib/backgroundDownloader'
import { usePlaybackActions, usePlaybackConnected } from '../contexts/PlaybackContext'
import { useStorage } from '../contexts/StorageContext'
import { useWebSocketSubscribe } from '../contexts/WebSocketContext'
import { useAuth } from '../contexts/AuthContext'
import { useThemeArtwork } from '../contexts/DynamicThemeContext'
import { useDeviceLinkApproval } from '../hooks/useDeviceLinkApproval'
import { artistCredit } from '../lib/utils'
import { useTaskNotices } from './TaskNotice'

const DISCONNECT_NOTICE_GRACE_MS = 4000
const MEDIA_POSITION_REFRESH_MS = 10000

export function TrackDataLoader() {
  const { trackId, hasArtwork, setTrackData } = useUISelector(state => ({
    trackId: state.engineState.currentTrack?.id,
    hasArtwork: state.engineState.currentTrack?.has_artwork !== false,
    setTrackData: state.setTrackData,
  }))
  const artTrackId = hasArtwork ? trackId : null

  useEffect(() => {
    if (!artTrackId) return
    let cancelled = false
    const warmModalArtwork = () => {
      import('./modals/Modal')
        .then(module => { if (!cancelled) return module.prewarmModalAssets(artTrackId) })
        .catch(err => logger.warn('[App] Failed to prewarm modal artwork:', err))
    }
    if ('requestIdleCallback' in window) {
      const idleId = window.requestIdleCallback(warmModalArtwork, { timeout: 6000 })
      return () => { cancelled = true; window.cancelIdleCallback(idleId) }
    }
    const timeoutId = setTimeout(warmModalArtwork, 3000)
    return () => { cancelled = true; clearTimeout(timeoutId) }
  }, [artTrackId])

  useEffect(() => {
    let cancelled = false
    if (trackId) {
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

          try {
            lyrics = await api.getLyricTimestamps(trackId)
          } catch (err) {
            if (cached?.lyricTimestamps) lyrics = cached.lyricTimestamps
            else logger.warn(`[App] Failed to fetch lyric timestamps for ${trackId}:`, err)
          }

          if (!cancelled) setTrackData(features, lyrics, trackId)

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
  }, [trackId, setTrackData])

  return null
}

export function MediaSessionBridge() {
  const { currentTrack, isPlaying, isCrossfading } = useUISelector(state => ({
    currentTrack: state.engineState.currentTrack,
    isPlaying: state.engineState.is_playing,
    isCrossfading: state.engineState.isCrossfading,
  }))
  const { resumePlayback, pausePlayback, previous, next, seek, audio } = usePlaybackActions()
  const themeArtwork = useThemeArtwork()
  const artworkUrl = themeArtwork && themeArtwork.trackId === currentTrack?.id ? themeArtwork.imageUrl : null

  useEffect(() => {
    if ('mediaSession' in navigator && currentTrack) {
      const params = currentTrack?.generation_params || {}

      navigator.mediaSession.metadata = new MediaMetadata({
        title: params.title || currentTrack.title || 'Unknown Track',
        artist: artistCredit(currentTrack) || 'PLAiR Radio',
        album: 'PLAiR',
        artwork: artworkUrl ? [
          { src: artworkUrl, sizes: '512x512', type: 'image/jpeg' }
        ] : [
          { src: `${window.location.origin}/images/plair_icon_512.png`, sizes: '512x512', type: 'image/png' }
        ]
      })
    }
  }, [currentTrack, artworkUrl])

  useEffect(() => {
    if (!('mediaSession' in navigator)) return
    const handlers = {
      play: () => void resumePlayback(),
      pause: () => void pausePlayback(),
      previoustrack: () => void previous(),
      nexttrack: () => void next(),
      seekto: (details) => {
        if (Number.isFinite(details?.seekTime)) void seek(Math.max(0, details.seekTime * 1000))
      },
    }
    Object.entries(handlers).forEach(([action, handler]) => {
      try {
        navigator.mediaSession.setActionHandler(action, handler)
      } catch (error) {
        logger.warn(`[MediaSession] ${action} not supported:`, error)
      }
    })
  }, [resumePlayback, pausePlayback, previous, next, seek])

  useEffect(() => {
    if (!('mediaSession' in navigator)) return
    navigator.mediaSession.playbackState = currentTrack ? (isPlaying ? 'playing' : 'paused') : 'none'
    const durationSec = (currentTrack?.duration_ms || 0) / 1000
    if (!durationSec || typeof navigator.mediaSession.setPositionState !== 'function') return
    const publishPosition = () => {
      const element = audio?.getCurrentElement?.()
      const positionSec = Math.min(Math.max(element?.currentTime || 0, 0), durationSec)
      try {
        navigator.mediaSession.setPositionState({ duration: durationSec, position: positionSec, playbackRate: 1 })
      } catch (error) {
        logger.warn('[MediaSession] setPositionState failed:', error)
      }
    }
    publishPosition()
    if (!isPlaying) return
    const timer = setInterval(publishPosition, MEDIA_POSITION_REFRESH_MS)
    return () => clearInterval(timer)
  }, [currentTrack, isPlaying, isCrossfading, audio])

  return null
}

export function ScreenAwakeBridge() {
  const keepAwake = useUISelector(state => state.interfaceState.isFullscreenVisuals)

  useEffect(() => {
    if (!keepAwake || !navigator.wakeLock) return undefined
    let lock = null
    let pending = false
    let stopped = false

    const acquire = async () => {
      if (stopped || pending || document.visibilityState !== 'visible') return
      if (lock && !lock.released) return
      pending = true
      try {
        const next = await navigator.wakeLock.request('screen')
        if (stopped) next.release().catch(() => {})
        else lock = next
      } catch (error) {
        logger.debug('[ScreenAwake] wake lock refused:', error?.name || error)
      } finally {
        pending = false
      }
    }

    acquire()
    document.addEventListener('visibilitychange', acquire)
    return () => {
      stopped = true
      document.removeEventListener('visibilitychange', acquire)
      lock?.release().catch(() => {})
      lock = null
    }
  }, [keepAwake])

  return null
}

export function OfflineNotice() {
  const { offline, noNetwork, showNotice, hideNotice } = useUISelector(state => ({
    offline: !!state.audioState.offlineMode,
    noNetwork: state.audioState.connectionMode === 'offline',
    showNotice: state.showNotice,
    hideNotice: state.hideNotice,
  }))
  const { storageInfo } = useStorage()
  const countRef = useRef(0)
  useEffect(() => {
    countRef.current = storageInfo?.offlineTrackCount ?? storageInfo?.trackCount ?? 0
  })

  useEffect(() => {
    if (!offline) {
      hideNotice('offline')
      return
    }
    const count = countRef.current
    showNotice({
      key: 'offline',
      tone: 'warning',
      icon: CloudOff,
      priority: 0,
      text: count > 0 ? `Offline · ${count} downloads` : 'Offline · no downloads',
      title: noNetwork
        ? 'No internet connection. Your downloads keep playing, and likes sync when you are back online.'
        : "PLAiR can't be reached right now. Your downloads keep playing, and likes sync when it is back.",
    })
  }, [offline, noNetwork, showNotice, hideNotice])

  return null
}

const LIMIT_NOTICES = {
  requests: { tone: 'warning' },
  low: { tone: 'info', sticky: true },
  out: { tone: 'warning', sticky: true },
}

export function LimitNotice() {
  const showNotice = useUISelector(state => state.showNotice)
  const show = useCallback((data) => {
    const look = LIMIT_NOTICES[data?.state]
    if (look && typeof data.message === 'string') showNotice({ key: 'limits', text: data.message, ...look })
  }, [showNotice])

  useEffect(() => api.onLimit(show), [show])
  useWebSocketSubscribe('allowance', show)
  return null
}

const SERVER_LOAD_KEY = 'server_load'

export function ServerLoadNotice() {
  const { shown, muted, offline, showNotice, hideNotice } = useUISelector(state => ({
    shown: state.notices.some(notice => notice.key === SERVER_LOAD_KEY),
    muted: !!state.settingsState.ttsMuted,
    offline: !!state.audioState.offlineMode,
    showNotice: state.showNotice,
    hideNotice: state.hideNotice,
  }))
  const [load, setLoad] = useState(null)
  const shownSinceRef = useRef(null)
  const dismissedSinceRef = useRef(null)
  useWebSocketSubscribe('server_load', setLoad)

  const busySince = load?.state === 'busy' ? load.since : null
  const message = load?.message

  useEffect(() => {
    if (!shown && shownSinceRef.current && shownSinceRef.current === busySince) {
      dismissedSinceRef.current = busySince
      shownSinceRef.current = null
    }
  }, [shown, busySince])

  useEffect(() => {
    if (!busySince || !message || muted || offline || dismissedSinceRef.current === busySince) {
      shownSinceRef.current = null
      hideNotice(SERVER_LOAD_KEY)
      return
    }
    shownSinceRef.current = busySince
    showNotice({ key: SERVER_LOAD_KEY, tone: 'info', icon: Gauge, priority: 2, sticky: true, text: message })
  }, [busySince, message, muted, offline, showNotice, hideNotice])

  return null
}

export function ConnectionNotice() {
  const connected = usePlaybackConnected()
  const { success, errorToast } = useUISelector(state => ({
    success: state.toastSuccess,
    errorToast: state.toastError,
  }))
  const wasConnectedRef = useRef(false)
  const disconnectNoticeRef = useRef(false)

  useEffect(() => {
    if (!connected && wasConnectedRef.current) {
      wasConnectedRef.current = false
      const timer = setTimeout(() => {
        if (uiState.audioState.offlineMode) return
        disconnectNoticeRef.current = true
        errorToast('Disconnected from server. Attempting to reconnect...', 8000, 'top', 'connection')
      }, DISCONNECT_NOTICE_GRACE_MS)
      return () => clearTimeout(timer)
    } else if (connected && !wasConnectedRef.current) {
      if (disconnectNoticeRef.current) {
        success('Connected to server', 4000, 'top', 'connection')
      }
      disconnectNoticeRef.current = false
      wasConnectedRef.current = true
    }
  }, [connected, errorToast, success])

  return null
}

export function UploadNotice() {
  const { info, publishContentUpdate } = useUISelector(state => ({
    info: state.toastInfo,
    publishContentUpdate: state.publishContentUpdate,
  }))
  const { playTrack } = usePlaybackActions()
  const { taskDone, taskFailed } = useTaskNotices()
  const getUIState = useUIStateGetter()
  const handledRef = useRef(new Set())

  useWebSocketSubscribe('task_progress', useCallback((data) => {
    if (data?.job_type !== 'upload' || data.event?.kind !== 'finished') return
    const uploadId = data.job_id
    const status = data.status
    if (!uploadId || handledRef.current.has(uploadId)) return
    handledRef.current.add(uploadId)
    if (status === 'completed') publishContentUpdate('uploads')
    if (status === 'cancelled') return
    const watched = () => getUIState().uploadWatchId === uploadId
    if (watched()) return
    const key = `upload:${uploadId}`
    const failed = (error) => taskFailed({ jobType: 'upload', key, text: error || 'Your upload could not be processed' })
    const live = (title, trackId) => taskDone({
      jobType: 'upload',
      text: `“${title}” is live`,
      actionLabel: 'Play',
      onAction: trackId ? () => void playTrack(trackId) : undefined,
    })
    if (status === 'failed') {
      failed(data.error)
      return
    }
    fetchFinishedUploadJob(uploadId)
      .then(job => {
        if (watched()) return
        if (job?.status === 'failed') {
          failed(job.error)
          return
        }
        const title = uploadJobTitle(job)
        if (job?.result?.duplicate) info(`Already uploaded: “${title}”`, 6000, 'top', key)
        else live(title, job?.result?.track_id)
      })
      .catch(err => {
        logger.warn('[Upload] Could not load the finished upload:', err)
        if (!watched()) live(uploadJobTitle(null), null)
      })
  }, [getUIState, publishContentUpdate, info, taskDone, taskFailed, playTrack]))

  return null
}

export function ShareVideoNotice() {
  const { status, modalOpen } = useUISelector(state => ({
    status: state.shareVideoState.status,
    modalOpen: state.shareModalState.isOpen,
  }))
  const { taskDone, taskFailed, hideTaskDone } = useTaskNotices()
  const getUIState = useUIStateGetter()
  const lastStatusRef = useRef(status)

  useEffect(() => {
    if (modalOpen) hideTaskDone('share_video')
  }, [modalOpen, hideTaskDone])

  useEffect(() => {
    if (lastStatusRef.current === status) return
    lastStatusRef.current = status
    const { shareVideoState: job, shareModalState, openShareModal } = getUIState()
    if (shareModalState.isOpen || (status !== 'complete' && status !== 'error')) {
      hideTaskDone('share_video')
      return
    }
    if (status === 'error') {
      taskFailed({ jobType: 'share_video', text: `The video of “${job.title}” could not be made: ${job.error}` })
      return
    }
    taskDone({
      jobType: 'share_video',
      text: `Video ready: “${job.title}”`,
      actionLabel: 'Open',
      onAction: () => openShareModal(job.track),
    })
  }, [status, getUIState, taskDone, taskFailed, hideTaskDone])

  return null
}

function takeLinkCode() {
  const url = new URL(window.location.href)
  const code = url.searchParams.get('link')
  if (!code) return null
  url.searchParams.delete('link')
  window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`)
  return code
}

export function DeviceLinkBridge() {
  const { isAuthenticated, loading } = useAuth()
  const toastInfo = useUISelector(state => state.toastInfo)
  const approve = useDeviceLinkApproval()
  const [code] = useState(takeLinkCode)
  const handledRef = useRef(false)

  useEffect(() => {
    if (!code || loading || handledRef.current) return
    handledRef.current = true
    if (isAuthenticated) void approve(code)
    else toastInfo('Sign in on this phone first, then scan the code again', 6000)
  }, [approve, code, isAuthenticated, loading, toastInfo])

  return null
}

const SETTINGS_SAVE_DELAY_MS = 800

export function SettingsSyncBridge() {
  const { user } = useAuth()
  const { publishSettings, backgroundDownloads, showNotice } = useUISelector(state => ({
    publishSettings: state.publishSettings,
    backgroundDownloads: state.settingsState.backgroundDownloads,
    showNotice: state.showNotice,
  }))
  const userId = user?.id ?? null
  const pendingRef = useRef({})
  const timerRef = useRef(null)
  const loadedForRef = useRef(null)
  const downloadsRef = useRef(backgroundDownloads)
  const warnedRef = useRef(false)
  const loadFailedRef = useRef(false)
  const [attempt, setAttempt] = useState(0)

  const warnNotSynced = useCallback(() => {
    if (warnedRef.current || uiState.audioState.offlineMode) return
    warnedRef.current = true
    showNotice({
      key: 'settings-sync',
      tone: 'warning',
      text: "Settings saved on this device. They'll sync when PLAiR is reachable",
    })
  }, [showNotice])

  const flush = useCallback(async () => {
    timerRef.current = null
    const changes = pendingRef.current
    if (loadedForRef.current === null || !Object.keys(changes).length) return
    pendingRef.current = {}
    try {
      await api.saveDeviceSettings(changes)
    } catch (err) {
      pendingRef.current = { ...changes, ...pendingRef.current }
      logger.warn('[Settings] Saving failed, will retry:', err.message)
      warnNotSynced()
    }
  }, [warnNotSynced])

  const schedule = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => void flush(), SETTINGS_SAVE_DELAY_MS)
  }, [flush])

  useEffect(() => onSettingsChanged((changes) => {
    if (!uiState.authState.isAuthenticated) return
    Object.assign(pendingRef.current, changes)
    if (loadedForRef.current !== null) schedule()
    else if (loadFailedRef.current) warnNotSynced()
  }), [schedule, warnNotSynced])

  useEffect(() => api.onConnectivity((event) => {
    if (event.type === 'recovered') setAttempt(count => count + 1)
  }), [])

  useEffect(() => {
    if (!userId) {
      loadedForRef.current = null
      loadFailedRef.current = false
      pendingRef.current = {}
      return
    }
    if (loadedForRef.current === userId) {
      schedule()
      return
    }
    let cancelled = false
    api.getDeviceSettings().then((result) => {
      if (cancelled || !result) return
      const edited = pendingRef.current
      publishSettings(result.settings, { fromServer: true })
      publishSettings(edited, { fromServer: true })
      pendingRef.current = result.stored ? edited : pickValidSettings(uiState.settingsState)
      loadedForRef.current = userId
      loadFailedRef.current = false
      logger.info(`[Settings] ${result.stored ? 'Loaded' : 'First save of'} settings for this ${result.kind}`)
      schedule()
    }).catch((err) => {
      logger.warn('[Settings] Loading the settings for this device failed:', err.message)
      if (cancelled) return
      loadFailedRef.current = true
      if (Object.keys(pendingRef.current).length) warnNotSynced()
    })
    return () => { cancelled = true }
  }, [userId, attempt, publishSettings, schedule, warnNotSynced])

  useWebSocketSubscribe('device_settings_updated', (data) => {
    if (data?.kind === deviceKind()) publishSettings(data.settings, { fromServer: true })
  })

  useEffect(() => {
    if (downloadsRef.current === backgroundDownloads) return
    downloadsRef.current = backgroundDownloads
    backgroundDownloader.toggle(backgroundDownloads)
  }, [backgroundDownloads])

  return null
}
