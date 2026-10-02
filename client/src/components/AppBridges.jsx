import { useCallback, useEffect, useRef, useState } from 'react'
import { CloudOff } from 'lucide-react'
import { api } from '../lib/api'
import { cacheManager } from '../lib/cacheManager'
import { logger } from '../lib/logger'
import { fetchFinishedUploadJob, uploadJobTitle, UPLOAD_FINISHED_STATUSES } from '../lib/uploadJobs'
import { useArtwork, useUISelector, useUIStateGetter, uiState } from '../contexts/UIStateContext'
import { usePlaybackActions, usePlaybackConnected } from '../contexts/PlaybackContext'
import { useStorage } from '../contexts/StorageContext'
import { useWebSocketSubscribe } from '../contexts/WebSocketContext'
import { useAuth } from '../contexts/AuthContext'
import { useDeviceLinkApproval } from '../hooks/useDeviceLinkApproval'

const DISCONNECT_NOTICE_GRACE_MS = 4000
const MEDIA_POSITION_REFRESH_MS = 10000

export function TrackDataLoader() {
  const { trackId, hasArtwork, setTrackData } = useUISelector(state => ({
    trackId: state.engineState.currentTrack?.id,
    hasArtwork: state.engineState.currentTrack?.has_artwork,
    setTrackData: state.setTrackData,
  }))
  const currentTrackArtwork = useArtwork(trackId, hasArtwork)

  useEffect(() => {
    if (!currentTrackArtwork || currentTrackArtwork.startsWith('data:')) return
    let cancelled = false
    const warmModalArtwork = () => {
      import('./modals/Modal')
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

  useEffect(() => {
    if ('mediaSession' in navigator && currentTrack) {
      const params = currentTrack?.generation_params || {}
      const derivedTags = currentTrack?.derived_tags || {}

      navigator.mediaSession.metadata = new MediaMetadata({
        title: params.title || currentTrack.title || 'Unknown Track',
        artist: params.artist_name || currentTrack.artist_name || derivedTags?.inspired_artist || 'PLAiR Radio',
        album: 'PLAiR',
        artwork: currentTrack.has_artwork ? [
          { src: `${window.location.origin}/api/artwork/${currentTrack.id}/thumb/512`, sizes: '512x512', type: 'image/jpeg' }
        ] : [
          { src: `${window.location.origin}/images/plair_icon_512.png`, sizes: '512x512', type: 'image/png' }
        ]
      })
    }
  }, [currentTrack])

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
  const { success, errorToast, info, publishContentUpdate } = useUISelector(state => ({
    success: state.toastSuccess,
    errorToast: state.toastError,
    info: state.toastInfo,
    publishContentUpdate: state.publishContentUpdate,
  }))
  const getUIState = useUIStateGetter()
  const handledRef = useRef(new Set())

  useWebSocketSubscribe('upload_progress', useCallback((data) => {
    const uploadId = data?.upload_id
    const status = data?.status
    if (!uploadId || !UPLOAD_FINISHED_STATUSES.has(status) || handledRef.current.has(uploadId)) return
    handledRef.current.add(uploadId)
    if (status === 'done') publishContentUpdate('uploads')
    if (status === 'cancelled') return
    const watched = () => getUIState().uploadWatchId === uploadId
    if (watched()) return
    const key = `upload:${uploadId}`
    if (status === 'failed') {
      errorToast(data.message || 'Your upload could not be processed', 8000, 'top', key)
      return
    }
    fetchFinishedUploadJob(uploadId)
      .then(job => {
        if (watched()) return
        if (job?.status === 'failed') {
          errorToast(job.error || 'Your upload could not be processed', 8000, 'top', key)
          return
        }
        const title = uploadJobTitle(job)
        if (job?.result?.duplicate) info(`Already uploaded: “${title}”`, 6000, 'top', key)
        else success(`“${title}” is live`, 6000, 'top', key)
      })
      .catch(err => {
        logger.warn('[Upload] Could not load the finished upload:', err)
        if (!watched()) success('Your upload is live', 6000, 'top', key)
      })
  }, [getUIState, publishContentUpdate, errorToast, info, success]))

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
