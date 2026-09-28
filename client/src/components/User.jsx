import { useState, useEffect, useRef, memo, useCallback, useMemo } from 'react'
import { User as UserIcon, Heart, Star, Ban, LogIn, LogOut, X, Music, Loader2, HardDrive, Wifi, WifiOff, Trash2, Database, TrendingDown, Mic2, Volume2, MapPin, Cloud, Clock, Edit2, RotateCcw, MessageSquareX, Radio, Gauge, Camera, Download, Sparkles, Headphones, Library, Settings, Upload, Play, Video, Image, DollarSign, Smartphone } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import { usePreferences } from '../contexts/PreferencesContext'
import { useStorage } from '../contexts/StorageContext'
import { useUISelector } from '../contexts/UIStateContext'
import { usePlaybackShoutout } from '../contexts/PlaybackShoutoutContext'
import { useDeviceSelector } from '../hooks/useDeviceSelector'
import { usePointerInteraction } from '../hooks/usePointerInteraction'
import { useDialog } from '../contexts/DialogContext'
import { api } from '../lib/api'
import { saveGuestSettings } from '../lib/accountSettings'
import { backgroundDownloader } from '../lib/backgroundDownloader'
import { useArtworkThumb } from '../contexts/UIStateContext'
import { useProfilePicture } from '../hooks/useProfilePicture'
import { profilePictureCache } from '../lib/mediaCache'
import { logger } from '../lib/logger'
import { safeStorage } from '../lib/safeStorage'
import { PanelHeader } from './Panel'
import { Scroller } from './Scroller'
import { ExpandSection, Expandable, ExpandChevron } from './Motion'
import { SettingRow, ToggleChip } from './SettingRow'
import { RadioModeSettings } from './RadioModeSettings'
import { useDynamicTheme, PANEL } from '../contexts/DynamicThemeContext'
import { CSS_TRANSITION } from '../lib/motion'

const DeviceTester = memo(function DeviceTester({ deviceId, type = 'mic' }) {
  const [isTesting, setIsTesting] = useState(false)
  const meterRef = useRef(null)
  const audioContextRef = useRef(null)
  const sourceRef = useRef(null)
  const analyserRef = useRef(null)
  const rafRef = useRef(null)
  const timeoutRef = useRef(null)

  const stopTest = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current)
      timeoutRef.current = null
    }
    if (sourceRef.current) {
      if (sourceRef.current.oscillator) {
        try { sourceRef.current.oscillator.stop() } catch { /* oscillator may already be stopped */ }
      }
      if (sourceRef.current.mediaStream) {
        sourceRef.current.mediaStream.getTracks().forEach(t => t.stop())
      }
      sourceRef.current = null
    }
    if (audioContextRef.current && audioContextRef.current.state !== 'closed') {
      audioContextRef.current.close()
    }
    audioContextRef.current = null
    analyserRef.current = null
    setIsTesting(false)
    if (meterRef.current) meterRef.current.style.transform = 'scaleX(0)'
  }, [])

  const startTest = useCallback(async () => {
    if (isTesting) {
      stopTest()
      return
    }

    try {
      setIsTesting(true)
      const ctx = new (window.AudioContext || window['webkitAudioContext'])()
      audioContextRef.current = ctx

      const analyser = ctx.createAnalyser()
      analyser.fftSize = 256
      analyserRef.current = analyser
      const dataArray = new Uint8Array(analyser.frequencyBinCount)

      if (type === 'mic') {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: deviceId ? { deviceId: { exact: deviceId } } : true
        })
        const source = ctx.createMediaStreamSource(stream)
        source.connect(analyser)
        sourceRef.current = { mediaStream: stream }
      } else {
        if (deviceId && typeof ctx.setSinkId === 'function') {
          await ctx.setSinkId(deviceId)
        }
        const osc = ctx.createOscillator()
        const gain = ctx.createGain()
        osc.type = 'sine'
        osc.frequency.setValueAtTime(440, ctx.currentTime)
        gain.gain.setValueAtTime(0.15, ctx.currentTime)
        osc.connect(gain)
        gain.connect(analyser)
        analyser.connect(ctx.destination)
        osc.start()
        sourceRef.current = { oscillator: osc }
      }

      const updateLevel = () => {
        if (!analyserRef.current) return
        analyserRef.current.getByteFrequencyData(dataArray)
        let sum = 0
        for (let i = 0; i < dataArray.length; i++) {
          sum += dataArray[i]
        }
        const average = sum / dataArray.length
        if (meterRef.current) meterRef.current.style.transform = `scaleX(${Math.min(1, average / 128)})`
        rafRef.current = requestAnimationFrame(updateLevel)
      }
      updateLevel()

      timeoutRef.current = setTimeout(stopTest, 4000)
    } catch (err) {
      logger.error('[DeviceTester] Test failed:', err)
      stopTest()
    }
  }, [deviceId, isTesting, stopTest, type])

  useEffect(() => () => stopTest(), [stopTest])

  return (
    <div className="flex items-center gap-2 mt-2">
      <div className="flex-1 h-1.5 bg-gray-800 rounded-full overflow-hidden">
        <div
          ref={meterRef}
          className="h-full w-full origin-left rounded-full"
          style={{
            transform: 'scaleX(0)',
            opacity: isTesting ? 1 : 0.3,
            transition: CSS_TRANSITION.meter,
            background: `linear-gradient(to right, #a855f7, #3b82f6, #22d3ee)`
          }}
        />
      </div>
      <button
        onClick={startTest}
        className={`ui-press text-xs px-2 py-1 rounded transition-colors flex items-center gap-1 ${
          isTesting
            ? 'text-purple-300 bg-purple-500/20'
            : 'text-gray-400 hover:text-white hover:bg-white/10'
        }`}
      >
        {type === 'mic' ? <Mic2 size={12} /> : <Volume2 size={12} />}
        {isTesting ? 'Testing...' : 'Test'}
      </button>
    </div>
  )
})

const formatBytes = (bytes) => {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i]
}

const getNetworkQualityLabel = (networkQuality) => {
  switch (networkQuality) {
    case 'excellent': return 'Excellent'
    case 'good': return 'Good'
    case 'fair': return 'Fair'
    case 'poor': return 'Poor'
    default: return 'Unknown'
  }
}

const MediaRow = memo(function MediaRow({
  image,
  fallbackIcon,
  title,
  subtitle,
  typeIcon: TypeIcon,
  typeIconColor,
  onPlay,
  onRemove,
  isLoading,
  isPlaying
}) {
  const { getButtonHoverBg } = useDynamicTheme()
  const playInteraction = usePointerInteraction()
  const removeInteraction = usePointerInteraction()
  const hoverBg = useMemo(() => getButtonHoverBg(), [getButtonHoverBg])

  const handlePlay = () => {
    if (!playInteraction.shouldTrigger()) return
    onPlay()
  }

  const handleRemove = (e) => {
    e.stopPropagation()
    if (!removeInteraction.shouldTrigger()) return
    onRemove()
  }

  return (
    <div
      className={`flex items-center gap-3 p-3 rounded-lg transition-[background-color,box-shadow] duration-quick group mb-1 ${isPlaying ? 'bg-white/10 ring-1 ring-purple-500/50' : ''}`}
      onMouseEnter={(e) => !isPlaying && (e.currentTarget.style.backgroundColor = hoverBg)}
      onMouseLeave={(e) => !isPlaying && (e.currentTarget.style.backgroundColor = 'transparent')}
    >
      <div
        onPointerDown={playInteraction.onPointerDown}
        onPointerMove={playInteraction.onPointerMove}
        onPointerUp={handlePlay}
        className="flex items-center gap-3 flex-1 min-w-0 cursor-pointer"
      >
        {image ? (
          <div className="relative">
            <img decoding="async" loading="lazy" src={image} alt={title} className={`w-12 h-12 rounded object-cover flex-shrink-0 ${isPlaying ? 'opacity-50' : ''}`} />
            {isPlaying && (
              <div className="absolute inset-0 flex items-center justify-center">
                <Loader2 size={20} className="animate-spin text-white" />
              </div>
            )}
          </div>
        ) : (
          <div className="w-12 h-12 rounded bg-gradient-to-br from-purple-600 to-blue-600 flex items-center justify-center flex-shrink-0 text-white">
            {isPlaying ? <Loader2 size={20} className="animate-spin" /> : fallbackIcon}
          </div>
        )}
        <div className="flex-1 min-w-0">
          <div className={`font-medium truncate ${isPlaying ? 'text-purple-300' : ''}`}>{title}</div>
          <div className="text-sm text-gray-400 truncate">{subtitle}</div>
        </div>
        <TypeIcon size={16} className={typeIconColor} />
      </div>
      <button
        onPointerDown={removeInteraction.onPointerDown}
        onPointerMove={removeInteraction.onPointerMove}
        onPointerUp={handleRemove}
        className="ui-press transition p-1 hover:bg-red-500/20 rounded"
        title="Remove preference"
        disabled={isLoading}
      >
        {isLoading ? <Loader2 size={16} className="animate-spin text-gray-400" /> : <X size={16} className="text-gray-400 hover:text-red-500" />}
      </button>
    </div>
  )
})

const PreferenceList = memo(function PreferenceList({ title, items, icon: Icon, iconColor, expanded, onToggleExpand, emptyMessage, renderItem }) {
  const previewItems = items.slice(0, 5)
  const overflowItems = items.slice(5)

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Icon size={18} className={iconColor} fill={['Heart', 'Star'].includes(Icon.displayName) || (Icon === Heart || Icon === Star) ? "currentColor" : "none"} />
          <h3 className="font-semibold">{title}</h3>
          <span className="text-sm text-gray-400">({items.length})</span>
        </div>
        {items.length > 5 && (
          <button
            onClick={() => onToggleExpand(!expanded)}
            aria-expanded={expanded}
            className="ui-press text-xs text-purple-400 hover:text-purple-300 transition flex items-center gap-1"
          >
            {expanded ? 'Show Less' : 'Show All'} <ExpandChevron open={expanded} size={14} />
          </button>
        )}
      </div>
      {items.length === 0 ? (
        <p className="text-sm text-gray-400 pl-7">{emptyMessage}</p>
      ) : (
        <div className="space-y-1">
          {previewItems.map(item => renderItem(item))}
          <Expandable open={expanded && overflowItems.length > 0} innerClassName="space-y-1 pt-1">
            {overflowItems.map(item => renderItem(item))}
          </Expandable>
        </div>
      )}
    </div>
  )
})

const UploadedTrackItem = memo(function UploadedTrackItem({ track, onPlayTrack, onDeleteUpload, isDeleting }) {
  const artworkUrl = useArtworkThumb(track?.id, track?.has_artwork)
  const params = track.generation_params || {}
  const title = params.title || track.title || 'Untitled'
  const artist = params.primary_artist || 'Unknown Artist'
  const genre = track.derived_tags?.primary_genre || params.style || 'Unknown Genre'

  return (
    <div className="flex items-center gap-3 p-3 rounded-lg bg-white/5 hover:bg-white/10 transition group">
      <div className="w-12 h-12 rounded bg-gradient-to-br from-emerald-600 to-teal-600 flex items-center justify-center flex-shrink-0 overflow-hidden">
        {artworkUrl ? (
          <img decoding="async" loading="lazy"
            src={artworkUrl}
            alt={title}
            className="w-full h-full rounded object-cover"
          />
        ) : (
          <Music size={20} className="text-white/70" />
        )}
      </div>

      <div
        className="flex-1 min-w-0 cursor-pointer"
        onClick={() => onPlayTrack(track.id)}
      >
        <div className="font-medium truncate">{title}</div>
        <div className="text-sm text-gray-400 truncate">{artist} • {genre}</div>
      </div>

      <button
        onClick={() => onPlayTrack(track.id)}
        className="ui-press p-2 rounded-full bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 transition"
        title="Play track"
      >
        <Play size={16} fill="currentColor" />
      </button>

      <button
        onClick={() => onDeleteUpload(track.id)}
        disabled={isDeleting}
        className="ui-press p-2 rounded-full hover:bg-red-500/20 text-gray-400 hover:text-red-400 transition"
        title="Delete track"
      >
        {isDeleting ? (
          <Loader2 size={16} className="animate-spin" />
        ) : (
          <Trash2 size={16} />
        )}
      </button>
    </div>
  )
})

const TrackItem = memo(function TrackItem({ track, icon, iconColor, onPlayTrack, onRemovePreference, isLoading }) {
  const artworkUrl = useArtworkThumb(track?.id, track?.has_artwork)
  const params = track?.generation_params || {}
  const title = params.title || track?.title || 'Untitled'
  const style = params.style || track?.style || 'No style'

  return (
    <MediaRow
      image={artworkUrl}
      fallbackIcon={<span className="text-lg">🎵</span>}
      title={title}
      subtitle={style}
      typeIcon={icon}
      typeIconColor={iconColor}
      onPlay={() => onPlayTrack(track?.id)}
      onRemove={() => onRemovePreference(track?.id)}
      isLoading={isLoading}
      isPlaying={false}
    />
  )
})

const ShoutoutItem = memo(function ShoutoutItem({ shoutout, icon, iconColor, onPlayShoutout, onRemovePreference, isLoading, isPlaying }) {
  const { user } = useAuth()
  const profilePictureUrl = useProfilePicture(shoutout?.user_id, !!shoutout?.profile_picture)
  const userInitial = shoutout?.username?.charAt(0).toUpperCase() || user?.username?.charAt(0).toUpperCase() || 'U'

  return (
    <MediaRow
      image={profilePictureUrl}
      fallbackIcon={<span className="text-xl font-bold">{userInitial}</span>}
      title={`"${shoutout?.transcription || 'No transcription'}"`}
      subtitle={shoutout?.username || 'Anonymous'}
      typeIcon={icon}
      typeIconColor={iconColor}
      onPlay={() => onPlayShoutout(shoutout)}
      onRemove={() => onRemovePreference(shoutout?.id)}
      isLoading={isLoading}
      isPlaying={isPlaying}
    />
  )
})

export const User = memo(function User({ onLogin, onRegister, onLogout, onPlayTrack, onReloadTrackQuality }) {
  const { isAuthenticated, user, refreshUser } = useAuth()
  const { getPreferences, removePreference, isPending } = usePreferences()
  const { getUserAvatarGradient, getPremiumGradient, getNetworkExcellent, getNetworkGood, getNetworkFair, getNetworkPoor } = useDynamicTheme()
  const { storageInfo, dataUsage, deleteTrack: deleteCachedTrack, clearAllCache, refreshStorageInfo } = useStorage()
  const {
    audioState,
    downloadState,
    publishDownloadState,
    settingsState,
    publishSettings,
    toastSuccess,
    toastError,
    toastInfo,
    openUploadModal,
    openUsageModal,
    tiltEnabled,
    tiltNeedsPermission,
    enableTiltEffects,
  } = useUISelector(state => ({
    audioState: state.audioState,
    downloadState: state.downloadState,
    publishDownloadState: state.publishDownloadState,
    settingsState: state.settingsState,
    publishSettings: state.publishSettings,
    toastSuccess: state.toastSuccess,
    toastError: state.toastError,
    toastInfo: state.toastInfo,
    openUploadModal: state.openUploadModal,
    openUsageModal: state.openUsageModal,
    tiltEnabled: state.tiltEnabled,
    tiltNeedsPermission: state.tiltNeedsPermission,
    enableTiltEffects: state.enableTiltEffects,
  }))
  const success = toastSuccess
  const error = toastError

  const { devices, selectedMicrophone, selectedSpeaker, selectMicrophone, selectSpeaker, requestPermissions } = useDeviceSelector()
  const { playingShoutout, playShoutout, stopShoutout } = usePlaybackShoutout()
  const { showConfirm } = useDialog()

  const [loading, setLoading] = useState(true)
  const [showCachedTracks, setShowCachedTracks] = useState(false)
  const [isEditingUsername, setIsEditingUsername] = useState(false)
  const [newUsername, setNewUsername] = useState('')

  const [expandedLiked, setExpandedLiked] = useState(false)
  const [expandedSuperLiked, setExpandedSuperLiked] = useState(false)
  const [expandedBanned, setExpandedBanned] = useState(false)
  const [expandedSuperLikedShoutouts, setExpandedSuperLikedShoutouts] = useState(false)
  const [expandedLikedShoutouts, setExpandedLikedShoutouts] = useState(false)
  const [expandedBannedShoutouts, setExpandedBannedShoutouts] = useState(false)

  const [profilePersonaExpanded, setProfilePersonaExpanded] = useState(() => safeStorage.get('userPanel_profilePersona') === 'true')
  const [audioDevicesExpanded, setAudioDevicesExpanded] = useState(() => safeStorage.get('userPanel_audioDevices') === 'true')
  const [locationExpanded, setLocationExpanded] = useState(() => safeStorage.get('userPanel_location') === 'true')
  const [libraryExpanded, setLibraryExpanded] = useState(() => safeStorage.get('userPanel_library') === 'true')
  const [storageExpanded, setStorageExpanded] = useState(() => safeStorage.get('userPanel_storage') === 'true')
  const [dataManagementExpanded, setDataManagementExpanded] = useState(() => safeStorage.get('userPanel_dataManagement') === 'true')
  const [uploadsExpanded, setUploadsExpanded] = useState(() => safeStorage.get('userPanel_uploads') === 'true')

  const [userUploads, setUserUploads] = useState([])
  const [loadingUploads, setLoadingUploads] = useState(false)
  const [deletingUploadId, setDeletingUploadId] = useState(null)

  const [uploadingProfilePicture, setUploadingProfilePicture] = useState(false)
  const [billingStatus, setBillingStatus] = useState(null)
  const [billingBusy, setBillingBusy] = useState(false)
  const fileInputRef = useRef(null)
  const profilePictureUrl = useProfilePicture(user?.id, !!user?.profile_picture)

  const trackPreferences = getPreferences('track')
  const shoutoutPreferences = getPreferences('shoutout')

  const tracks = useMemo(() => ({
    liked: trackPreferences.likes || [],
    super_liked: trackPreferences.super_likes || [],
    banned: trackPreferences.bans || []
  }), [trackPreferences])

  const shoutouts = useMemo(() => ({
    super_liked: shoutoutPreferences.super_likes || [],
    liked: shoutoutPreferences.likes || [],
    banned: shoutoutPreferences.bans || []
  }), [shoutoutPreferences])

  useEffect(() => {
    if (!isAuthenticated) { setLoading(false); return }
    setLoading(false)
  }, [isAuthenticated])

  useEffect(() => {
    if (!isAuthenticated) return
    let cancelled = false
    api.getBillingStatus()
      .then(status => { if (!cancelled) setBillingStatus(status) })
      .catch(err => logger.warn('Failed to load billing status:', err))
    return () => { cancelled = true }
  }, [isAuthenticated, user?.tier])

  useEffect(() => {
    safeStorage.set('userPanel_profilePersona', String(profilePersonaExpanded))
    safeStorage.set('userPanel_audioDevices', String(audioDevicesExpanded))
    safeStorage.set('userPanel_location', String(locationExpanded))
    safeStorage.set('userPanel_library', String(libraryExpanded))
    safeStorage.set('userPanel_storage', String(storageExpanded))
    safeStorage.set('userPanel_dataManagement', String(dataManagementExpanded))
    safeStorage.set('userPanel_uploads', String(uploadsExpanded))
  }, [profilePersonaExpanded, audioDevicesExpanded, locationExpanded, libraryExpanded, storageExpanded, dataManagementExpanded, uploadsExpanded])

  const fetchUserUploads = useCallback(async () => {
    if (!isAuthenticated) return
    setLoadingUploads(true)
    try {
      const data = await api.getUserUploads()
      setUserUploads(data.tracks || [])
    } catch (err) {
      logger.error('Failed to fetch user uploads:', err)
    } finally {
      setLoadingUploads(false)
    }
  }, [isAuthenticated])

  useEffect(() => {
    if (uploadsExpanded && isAuthenticated) {
      void fetchUserUploads()
    }
  }, [uploadsExpanded, isAuthenticated, fetchUserUploads])

  const handleDeleteUpload = async (trackId) => {
    const confirmed = await showConfirm({
      title: 'Delete Uploaded Track?',
      message: 'Are you sure you want to delete this track? This action cannot be undone.',
      confirmText: 'Delete',
      cancelText: 'Cancel',
      variant: 'danger'
    })
    if (!confirmed) return

    setDeletingUploadId(trackId)
    try {
      const result = await api.deleteUserTrack(trackId)
      if (result.ok) {
        setUserUploads(prev => prev.filter(t => t.id !== trackId))
        success('Track deleted')
      } else {
        error('Failed to delete track')
      }
    } catch (err) {
      error(err.message || 'Failed to delete track')
      logger.error('Failed to delete upload:', err)
    } finally {
      setDeletingUploadId(null)
    }
  }

  const soundMode = useMemo(() => {
    if (!settingsState.ttsMuted && !settingsState.notificationsMuted) return 'both'
    if (settingsState.ttsMuted && !settingsState.notificationsMuted) return 'notifications'
    return 'off'
  }, [settingsState])

  const handlePlayShoutout = useCallback((shoutout) => {
    if (!shoutout) return

    if (playingShoutout?.id === shoutout.id) {
      stopShoutout()
    } else {
      playShoutout(shoutout, { showModal: true })
    }
  }, [playingShoutout, playShoutout, stopShoutout])

  const handleProfilePictureUpload = async (event) => {
    const file = event.target.files?.[0]
    if (!file) return
    if (file.size > 5 * 1024 * 1024) { error('File too large (Max 5MB)'); return }
    if (!['image/jpeg', 'image/jpg', 'image/png', 'image/webp', 'image/gif'].includes(file.type)) {
      error('Invalid file type'); return
    }

    try {
      setUploadingProfilePicture(true)
      await api.uploadProfilePicture(file)
      await profilePictureCache.invalidate(user?.id)
      await refreshUser()
      success('Profile picture uploaded')
    } catch (err) {
      error(err.message || 'Upload failed')
      logger.error('Profile picture upload error:', err)
    } finally {
      setUploadingProfilePicture(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleProfilePictureDelete = async () => {
    const confirmed = await showConfirm({
      title: 'Delete Profile Picture?',
      message: 'Are you sure you want to delete your profile picture?',
      confirmText: 'Delete',
      cancelText: 'Cancel',
      variant: 'danger'
    })
    if (!confirmed) return
    try {
      const result = await api.deleteProfilePicture()
      if (!result.ok) {
        error('Delete failed')
        return
      }
      await profilePictureCache.invalidate(user?.id)
      await refreshUser()
      success('Profile picture deleted')
    } catch (err) {
      error(err.message || 'Failed to delete profile picture')
    }
  }

  const handleRemovePreference = useCallback(async (type, id) => {
    try { await removePreference(type, id) }
    catch (_err) { logger.error('Failed to remove preference:', _err) }
  }, [removePreference])

  const handleAudioQualityChange = async (newQuality) => {
    try {
      publishSettings({ audioQuality: newQuality })

      await api.updateAudioQuality(newQuality)
      if (refreshUser) await refreshUser()
      if (onReloadTrackQuality) await onReloadTrackQuality()
      success(`Quality set to ${newQuality}`)
    } catch (_err) {
      publishSettings({ audioQuality: user?.audio_quality ?? 'auto' })
      error('Failed to update quality')
      logger.error('Failed to update audio quality:', _err)
    }
  }

  const handleToggleFpsEnabled = async () => {
    try {
      const newVal = !settingsState.fpsEnabled

      publishSettings({ fpsEnabled: newVal })

      await api.updateUserProfile({ fps_enabled: newVal })
      if (refreshUser) await refreshUser()
      success(`FPS ${newVal ? 'enabled' : 'disabled'}`)
    } catch (_err) {
      publishSettings({ fpsEnabled: user?.fps_enabled ?? false })
      error('Failed to update FPS')
      logger.error('Failed to update FPS setting:', _err)
    }
  }

  const handleToggleVideoClips = async () => {
    try {
      const newVal = !settingsState.videoClipsEnabled

      publishSettings({ videoClipsEnabled: newVal })

      await api.updateUserProfile({ video_clips_enabled: newVal })
      if (refreshUser) await refreshUser()
      success(`Video clips ${newVal ? 'enabled' : 'disabled'}`)
    } catch (_err) {
      publishSettings({ videoClipsEnabled: user?.video_clips_enabled ?? false })
      error('Failed to update video clips setting')
      logger.error('Failed to update video clips setting:', _err)
    }
  }

  const handleSetVisualQuality = async (quality) => {
    try {
      publishSettings({ visualQuality: quality })
      await api.updateUserProfile({ visual_quality: quality })
      if (refreshUser) await refreshUser()
      success(`Visual quality: ${quality}`)
    } catch (_err) {
      publishSettings({ visualQuality: user?.visual_quality ?? 'high' })
      error('Failed to update visual quality')
      logger.error('Failed to update visual quality:', _err)
    }
  }

  const handleToggleBackgroundDownloads = () => {
    const newValue = !downloadState.isEnabled
    publishDownloadState({ isEnabled: newValue })
    backgroundDownloader.toggle(newValue)
    success(`Background downloads ${newValue ? 'enabled' : 'disabled'}`)
  }

  const handleSetSoundMode = async (mode) => {
    try {
      let newTtsMuted, newNotificationsMuted, message

      if (mode === 'both') {
        newTtsMuted = false
        newNotificationsMuted = false
        message = 'DJ + PING'
      } else if (mode === 'notifications') {
        newTtsMuted = true
        newNotificationsMuted = false
        message = 'PING only'
      } else {
        newTtsMuted = true
        newNotificationsMuted = true
        message = 'OFF'
      }

      publishSettings({ ttsMuted: newTtsMuted, notificationsMuted: newNotificationsMuted })

      if (!user) {
        saveGuestSettings({ ttsMuted: newTtsMuted, notificationsMuted: newNotificationsMuted })
        success(message)
        return
      }

      await api.updateUserProfile({
        tts_muted: newTtsMuted,
        notifications_muted: newNotificationsMuted
      })
      if (refreshUser) await refreshUser()
      success(message)
    } catch (_err) {
      publishSettings({ ttsMuted: user?.tts_muted ?? false, notificationsMuted: user?.notifications_muted ?? false })
      error('Failed to update sound settings')
      logger.error('Failed to update sound settings:', _err)
    }
  }

  const handleDeleteCachedTrack = async (trackId) => {
    try { await deleteCachedTrack(trackId); await refreshStorageInfo() }
    catch (_err) { logger.error('Failed to delete cached track:', _err) }
  }

  const handleClearAllCache = async () => {
    const confirmed = await showConfirm({
      title: 'Clear All Cache?',
      message: 'Are you sure you want to clear all cached tracks? This will free up storage space.',
      confirmText: 'Clear Cache',
      cancelText: 'Cancel',
      variant: 'danger'
    })
    if (confirmed) {
      try { await clearAllCache() } catch (_err) { logger.error('Failed to clear cache:', _err) }
    }
  }

  const handleUsernameEdit = () => { setNewUsername(user?.username || ''); setIsEditingUsername(true) }

  const handleUsernameSave = async () => {
    if (!newUsername.trim()) { error('Username cannot be empty'); return }
    try {
      await api.updateUsername(newUsername.trim())
      await refreshUser()
      setIsEditingUsername(false)
      success('Username updated successfully')
    } catch (_err) {
      error('Failed to update username')
      logger.error('Failed to update username:', _err)
    }
  }

  const handleDeleteConversationHistory = async () => {
    const confirmed = await showConfirm({
      title: 'Delete Conversation History?',
      message: 'Are you sure you want to delete all conversation history? This action cannot be undone.',
      confirmText: 'Delete',
      cancelText: 'Cancel',
      variant: 'danger'
    })
    if (!confirmed) return
    try { await api.deleteConversationHistory(); success('Conversation history deleted') }
    catch (_err) {
      error('Failed to delete conversation history')
      logger.error('Failed to delete conversation history:', _err)
    }
  }

  const handleResetPersona = async () => {
    const confirmed = await showConfirm({
      title: 'Reset Persona & Profile?',
      message: 'This will reset your DJ persona and clear all learned preferences. This action cannot be undone.',
      confirmText: 'Reset',
      cancelText: 'Cancel',
      variant: 'danger'
    })
    if (!confirmed) return
    try { await api.resetPersona(); await refreshUser(); success('Persona reset successfully') }
    catch (_err) {
      error('Failed to reset persona')
      logger.error('Failed to reset persona:', _err)
    }
  }

  const handleUpgradeToPremium = async () => {
    if (billingBusy) return
    setBillingBusy(true)
    try {
      const { url } = await api.createStripeCheckout()
      window.location.assign(url)
    } catch (err) {
      setBillingBusy(false)
      if (err.status === 409) {
        await refreshUser()
        toastInfo(err.message)
      } else {
        error(err.message || 'Could not start checkout. Please try again.')
      }
    }
  }

  const handleManageSubscription = async () => {
    if (billingBusy) return
    setBillingBusy(true)
    try {
      const { url } = await api.createBillingPortal()
      window.location.assign(url)
    } catch (err) {
      setBillingBusy(false)
      error(err.message || 'Could not open subscription management. Please try again.')
    }
  }

  const priceLabel = billingStatus?.price_label || 'US$4.99/mo'
  const generationUsage = billingStatus?.generation_usage
  const periodEnd = billingStatus?.current_period_end ? new Date(billingStatus.current_period_end) : null

  const getNetworkQualityColor = () => {
    switch (audioState.networkQuality) {
      case 'excellent': return getNetworkExcellent()
      case 'good': return getNetworkGood()
      case 'fair': return getNetworkFair()
      case 'poor': return getNetworkPoor()
      default: return getNetworkFair()
    }
  }

  const filteredLikedTracks = useMemo(() => tracks.liked.filter(t => t?.id), [tracks.liked])
  const filteredSuperLikedTracks = useMemo(() => tracks.super_liked.filter(t => t?.id), [tracks.super_liked])
  const filteredBannedTracks = useMemo(() => tracks.banned.filter(t => t?.id), [tracks.banned])
  const filteredSuperLikedShoutouts = useMemo(() => shoutouts.super_liked.filter(s => s?.id), [shoutouts.super_liked])
  const filteredLikedShoutouts = useMemo(() => shoutouts.liked.filter(s => s?.id), [shoutouts.liked])
  const filteredBannedShoutouts = useMemo(() => shoutouts.banned.filter(s => s?.id), [shoutouts.banned])

  if (!isAuthenticated) {
    return (
      <div className="flex flex-col h-full relative">
        <PanelHeader title="Account" />
        <Scroller className="flex-1">
        <div className="min-h-full flex items-center justify-center p-6" style={{ paddingTop: `${PANEL.headerHeight}px` }}>
          <div className="max-w-sm w-full space-y-4">
            <div className="text-center mb-6">
              <img src="/images/plair_icon_192.png" alt="PLAiR" width="80" height="80" decoding="async" className="w-20 h-20 rounded-2xl mx-auto mb-4 shadow-lg" />
              <h3 className="text-xl font-bold mb-2">Welcome to PLAiR</h3>
              <p className="text-gray-400 text-sm">Sign in to save your likes, downloads and settings</p>
            </div>
            <button onClick={onRegister} className="ui-press-soft w-full px-4 py-3 rounded-lg transition font-semibold bg-amber-500 hover:bg-amber-400 text-zinc-950">
              Create account
            </button>
            <button onClick={onLogin} className="ui-press-soft w-full px-4 py-3 rounded-lg border border-amber-500/60 text-amber-300 hover:bg-amber-500/10 flex items-center justify-center gap-2 transition">
              <LogIn size={20} /> Login
            </button>
            <RadioModeSettings className="pt-4 border-t border-gray-800" />
          </div>
        </div>
        </Scroller>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full relative">
      <PanelHeader
        title={
          <div className="flex items-center gap-3 flex-1 min-w-0">
            <div className="relative group">
              {profilePictureUrl ? (
                <img decoding="async" src={profilePictureUrl} alt={user?.username} className="w-10 h-10 rounded-full object-cover flex-shrink-0" />
              ) : (
                <div className="w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0" style={{ background: getUserAvatarGradient(), color: 'white' }}>
                  <UserIcon size={20} />
                </div>
              )}
              <div className="absolute inset-0 bg-black/60 rounded-full opacity-0 group-hover:opacity-100 transition flex items-center justify-center gap-2">
                <button onClick={() => fileInputRef.current?.click()} disabled={uploadingProfilePicture} className="ui-press p-1 text-white hover:text-purple-300 transition">
                  {uploadingProfilePicture ? <Loader2 size={16} className="animate-spin" /> : <Camera size={16} />}
                </button>
                {profilePictureUrl && <button onClick={handleProfilePictureDelete} className="ui-press p-1 text-white hover:text-red-400 transition"><Trash2 size={16} /></button>}
              </div>
              <input ref={fileInputRef} type="file" accept="image/jpeg,image/jpg,image/png,image/webp,image/gif" onChange={handleProfilePictureUpload} className="hidden" />
            </div>
            <div className="flex-1 min-w-0">
              {isEditingUsername ? (
                <div className="flex items-center gap-2">
                  <input type="text" value={newUsername} onChange={(e) => setNewUsername(e.target.value)} className="flex-1 px-2 py-1 bg-dark-hover border border-purple-500 rounded text-sm focus:outline-none" autoFocus onKeyDown={(e) => { if (e.key === 'Enter') void handleUsernameSave(); if (e.key === 'Escape') setIsEditingUsername(false); }} />
                  <button onClick={handleUsernameSave} className="ui-press text-green-400 p-1"><X size={16} className="rotate-45" /></button>
                  <button onClick={() => setIsEditingUsername(false)} className="ui-press text-gray-400 p-1"><X size={16} /></button>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  <h2 className="text-lg md:text-xl font-bold truncate">{user?.username}</h2>
                  <button onClick={handleUsernameEdit} className="ui-press text-gray-400 hover:text-white transition p-1"><Edit2 size={14} /></button>
                </div>
              )}
              <div className="flex items-center gap-2">
                <p className="text-xs text-gray-400">{user?.tier === 'premium' ? 'Premium Account' : 'Free Account'}</p>
                {user?.tier === 'premium' && <span className="text-xs bg-gradient-to-r from-yellow-500 to-orange-500 text-white px-2 py-0.5 rounded-full font-semibold">PRO</span>}
              </div>
            </div>
          </div>
        }
      />

      <Scroller className="flex-1">
        {(user?.persona || user?.profile || user?.shoutout_interests) && (
          <ExpandSection
            className="p-4 md:p-6 border-b border-gray-800"
            open={profilePersonaExpanded}
            onToggle={setProfilePersonaExpanded}
            icon={Sparkles}
            iconClassName="text-purple-400"
            title="Profile & Persona"
            contentClassName="space-y-3 pl-2"
          >
            {user?.persona && (
              <div className="bg-gradient-to-br from-purple-500/10 to-blue-500/10 p-3 rounded-lg border border-purple-500/20">
                <div className="flex items-center gap-2 mb-2">
                  <UserIcon size={14} className="text-purple-400" />
                  <label className="text-xs font-semibold text-purple-300">AI Persona</label>
                </div>
                <p className="text-sm text-gray-300 leading-relaxed">{user.persona}</p>
              </div>
            )}
            {user?.profile && (
              <div className="bg-gradient-to-br from-blue-500/10 to-cyan-500/10 p-3 rounded-lg border border-blue-500/20">
                <div className="flex items-center gap-2 mb-2">
                  <Star size={14} className="text-blue-400" />
                  <label className="text-xs font-semibold text-blue-300">Listener Profile</label>
                </div>
                <p className="text-sm text-gray-300 leading-relaxed">{user.profile}</p>
              </div>
            )}
            {user?.shoutout_interests && (
              <div className="bg-gradient-to-br from-cyan-500/10 to-teal-500/10 p-3 rounded-lg border border-cyan-500/20">
                <div className="flex items-center gap-2 mb-2">
                  <Mic2 size={14} className="text-cyan-400" />
                  <label className="text-xs font-semibold text-cyan-300">Shoutout Interests</label>
                </div>
                <p className="text-sm text-gray-300 leading-relaxed">{user.shoutout_interests}</p>
              </div>
            )}
          </ExpandSection>
        )}

        <ExpandSection
          className="p-4 md:p-6 border-b border-gray-800"
          open={audioDevicesExpanded}
          onToggle={setAudioDevicesExpanded}
          icon={Headphones}
          iconClassName="text-blue-400"
          title="Audio & Devices"
          contentClassName="space-y-3 pl-2"
        >
          <SettingRow icon={Mic2} label="Microphone">
            <select value={selectedMicrophone} onChange={(e) => { selectMicrophone(e.target.value); success('Microphone updated') }} className="w-full px-3 py-2 bg-dark-hover border border-gray-700 rounded-lg text-sm focus:outline-none focus:border-purple-500 transition">
              <option value="">Default Microphone</option>
              {devices.microphones.map(mic => <option key={mic.id} value={mic.id}>{mic.label}</option>)}
            </select>
            {devices.microphones.length === 0 && (
              <button onClick={requestPermissions} className="ui-press text-xs text-purple-400 hover:text-purple-300 mt-1">
                Grant microphone access
              </button>
            )}
            <DeviceTester deviceId={selectedMicrophone} type="mic" />
          </SettingRow>

          <SettingRow icon={Volume2} label="Speakers">
            <select
              value={selectedSpeaker}
              onChange={async (e) => {
                try {
                  await selectSpeaker(e.target.value)
                  success('Speakers updated')
                } catch (_err) {
                  error('Failed to change speakers')
                  logger.error('Failed to change speakers:', _err)
                }
              }}
              className="w-full px-3 py-2 bg-dark-hover border border-gray-700 rounded-lg text-sm focus:outline-none focus:border-purple-500 transition"
            >
              <option value="">Default Speakers</option>
              {devices.speakers.map(speaker => <option key={speaker.id} value={speaker.id}>{speaker.label}</option>)}
            </select>
            <DeviceTester deviceId={selectedSpeaker} type="speaker" />
          </SettingRow>

          <SettingRow
            icon={Smartphone}
            label="Auto-switch playback to the device I open"
            color="text-blue-400"
            headerContent={
              <ToggleChip
                on={settingsState.autoClaimOnOpen !== false}
                onClick={() => publishSettings({ autoClaimOnOpen: settingsState.autoClaimOnOpen === false })}
                activeClassName="bg-blue-500 text-white"
                label="Auto-switch playback to the device I open"
              />
            }
          />

          <SettingRow icon={Music} label="Audio Quality" headerContent={null}>
            <select value={settingsState.audioQuality} onChange={(e) => handleAudioQualityChange(e.target.value)} className="w-full px-3 py-2 bg-dark-hover border border-gray-700 rounded-lg text-sm focus:outline-none focus:border-purple-500 transition">
              <option value="auto">Auto</option>
              <option value="256k">Premium (256kbps)</option>
              <option value="192k">Standard (192kbps)</option>
              <option value="128k">Economy (128kbps)</option>
            </select>
          </SettingRow>

          <SettingRow
            icon={Radio}
            label="Sounds"
            headerContent={
              <div className="flex gap-1">
                <button
                  onClick={() => handleSetSoundMode('both')}
                  className={`ui-press px-2 py-1 rounded text-xs font-medium transition ${soundMode === 'both' ? 'bg-green-500/30 text-green-300 border border-green-500/50' : 'bg-dark-card text-gray-400 border border-gray-700/50'}`}
                >
                  DJ+PING
                </button>
                <button
                  onClick={() => handleSetSoundMode('notifications')}
                  className={`ui-press px-2 py-1 rounded text-xs font-medium transition ${soundMode === 'notifications' ? 'bg-amber-500/30 text-amber-300 border border-amber-500/50' : 'bg-dark-card text-gray-400 border border-gray-700/50'}`}
                >
                  PING
                </button>
                <button
                  onClick={() => handleSetSoundMode('off')}
                  className={`ui-press px-2 py-1 rounded text-xs font-medium transition ${soundMode === 'off' ? 'bg-red-500/30 text-red-300 border border-red-500/50' : 'bg-dark-card text-gray-400 border border-gray-700/50'}`}
                >
                  OFF
                </button>
              </div>
            }
          />
        </ExpandSection>

        <RadioModeSettings className="p-4 md:p-6 border-b border-gray-800" />

        {(user?.location || user?.timezone || user?.weather_description) && (
          <ExpandSection
            className="p-4 md:p-6 border-b border-gray-800"
            open={locationExpanded}
            onToggle={setLocationExpanded}
            icon={MapPin}
            iconClassName="text-green-400"
            title="Location & Environment"
            contentClassName="space-y-3 pl-2"
          >
            {user?.location && <div className="flex items-center gap-2 text-sm"><MapPin size={14} className="text-blue-400" /><span className="text-gray-300">{user.location}</span></div>}
            {user?.timezone && <div className="flex items-center gap-2 text-sm"><Clock size={14} className="text-purple-400" /><span className="text-gray-300">{user.timezone}</span></div>}
            {user?.weather_description && <div className="flex items-center gap-2 text-sm"><Cloud size={14} className="text-cyan-400" /><span className="text-gray-300">{user.weather_description}</span></div>}
          </ExpandSection>
        )}

        <ExpandSection
          className="p-4 md:p-6 border-b border-gray-800"
          open={storageExpanded}
          onToggle={setStorageExpanded}
          icon={Settings}
          iconClassName="text-cyan-400"
          title="Storage & Performance"
          contentClassName="space-y-3 pl-2"
        >
          <SettingRow
            icon={audioState.isOnline ? Wifi : WifiOff}
            label="Network Status"
            color={audioState.isOnline ? getNetworkQualityColor() : "text-red-500"}
            headerContent={
              <span className="text-xs font-medium" style={{ color: audioState.isOnline ? getNetworkQualityColor() : getNetworkPoor() }}>{audioState.isOnline ? getNetworkQualityLabel(audioState.networkQuality) : 'Offline'}</span>
            }
          />

          <SettingRow
            icon={TrendingDown}
            label="Data Saver Mode"
            color="text-green-400"
            headerContent={
              <button onClick={() => publishSettings({ dataSaverMode: !settingsState.dataSaverMode })} className={`ui-press px-3 py-1 rounded text-xs font-medium transition ${settingsState.dataSaverMode ? 'bg-green-500 text-white' : 'bg-dark-hover text-gray-400'}`}>{settingsState.dataSaverMode ? 'ON' : 'OFF'}</button>
            }
          />

          <SettingRow
            icon={Gauge}
            label="FPS Counter"
            color="text-blue-400"
            headerContent={
              <button onClick={handleToggleFpsEnabled} className={`ui-press px-3 py-1 rounded text-xs font-medium transition ${settingsState.fpsEnabled ? 'bg-blue-500 text-white' : 'bg-dark-hover text-gray-400'}`}>{settingsState.fpsEnabled ? 'ON' : 'OFF'}</button>
            }
          />

          {(user?.is_admin || user?.usage_stats_visible) && (
            <SettingRow
              icon={DollarSign}
              label="AI Usage"
              color="text-violet-400"
              headerContent={
                <button onClick={openUsageModal} className="ui-press px-3 py-1 rounded text-xs font-medium transition bg-violet-500/30 text-violet-200 border border-violet-500/50">VIEW</button>
              }
            >
              <div className="text-xs text-gray-400">
                {user?.is_admin ? 'What PLAiR spends on AI, per listener, with projections' : 'Your AI usage this month'}
              </div>
            </SettingRow>
          )}

          {user?.is_admin && (
            <SettingRow
              icon={DollarSign}
              label="Cost Ticker"
              color="text-violet-400"
              headerContent={
                <button onClick={() => publishSettings({ costTickerEnabled: !settingsState.costTickerEnabled })} className={`ui-press px-3 py-1 rounded text-xs font-medium transition ${settingsState.costTickerEnabled ? 'bg-violet-500 text-white' : 'bg-dark-hover text-gray-400'}`}>{settingsState.costTickerEnabled ? 'ON' : 'OFF'}</button>
              }
            />
          )}

          <SettingRow
            icon={Image}
            label="Visual Quality"
            color="text-purple-400"
            headerContent={
              <div className="flex gap-1">
                <button
                  onClick={() => handleSetVisualQuality('high')}
                  className={`ui-press px-2 py-1 rounded text-xs font-medium transition ${settingsState.visualQuality === 'high' ? 'bg-purple-500/30 text-purple-300 border border-purple-500/50' : 'bg-dark-card text-gray-400 border border-gray-700/50'}`}
                >
                  HIGH
                </button>
                <button
                  onClick={() => handleSetVisualQuality('medium')}
                  className={`ui-press px-2 py-1 rounded text-xs font-medium transition ${settingsState.visualQuality === 'medium' ? 'bg-amber-500/30 text-amber-300 border border-amber-500/50' : 'bg-dark-card text-gray-400 border border-gray-700/50'}`}
                >
                  MID
                </button>
                <button
                  onClick={() => handleSetVisualQuality('low')}
                  className={`ui-press px-2 py-1 rounded text-xs font-medium transition ${settingsState.visualQuality === 'low' ? 'bg-green-500/30 text-green-300 border border-green-500/50' : 'bg-dark-card text-gray-400 border border-gray-700/50'}`}
                >
                  LOW
                </button>
              </div>
            }
          >
            <div className="text-xs text-gray-400">
              Controls background visual effects intensity. Use LOW for better battery life on mobile devices.
            </div>
          </SettingRow>

          {tiltNeedsPermission && (
            <SettingRow
              icon={Smartphone}
              label="Tilt Effects"
              color="text-sky-400"
              headerContent={
                <button onClick={() => void enableTiltEffects(!tiltEnabled)} className={`ui-press px-3 py-1 rounded text-xs font-medium transition ${tiltEnabled ? 'bg-sky-500 text-white' : 'bg-dark-hover text-gray-400'}`}>{tiltEnabled ? 'ON' : 'OFF'}</button>
              }
            >
              <div className="text-xs text-gray-400">
                Artwork moves as you tilt your phone. Your phone will ask for motion access.
              </div>
            </SettingRow>
          )}

          <SettingRow
            icon={Video}
            label="Video Clips"
            color="text-pink-400"
            headerContent={
              <button onClick={handleToggleVideoClips} className={`ui-press px-3 py-1 rounded text-xs font-medium transition ${settingsState.videoClipsEnabled ? 'bg-pink-500 text-white' : 'bg-dark-hover text-gray-400'}`}>{settingsState.videoClipsEnabled ? 'ON' : 'OFF'}</button>
            }
          >
            <div className="text-xs text-gray-400">
              Video clips in visuals and shared videos (uses more bandwidth)
            </div>
          </SettingRow>

          <SettingRow
            icon={Download}
            label="Background Downloads"
            color="text-purple-400"
            headerContent={
              <button onClick={handleToggleBackgroundDownloads} className={`ui-press px-3 py-1 rounded text-xs font-medium transition ${downloadState?.isEnabled ? 'bg-purple-500 text-white' : 'bg-dark-hover text-gray-400'}`}>{downloadState?.isEnabled ? 'ON' : 'OFF'}</button>
            }
          >
            {downloadState?.isEnabled && (
              <div className="text-xs text-gray-400 space-y-1">
                {downloadState.isDownloading && (
                  <div className="flex items-center gap-2">
                    <Loader2 size={12} className="animate-spin text-purple-400" />
                    <span>Downloading: {downloadState.currentTrackTitle}</span>
                  </div>
                )}
                <div className="flex items-center justify-between">
                  <span>Today: {formatBytes(downloadState.dailyDownloadedBytes)} / {formatBytes(downloadState.dailyLimit)}</span>
                  {downloadState.downloadedCount > 0 && <span>{downloadState.downloadedCount} tracks downloaded</span>}
                </div>
              </div>
            )}
          </SettingRow>

          <div className="bg-white/5 p-3 rounded-lg">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2"><HardDrive size={14} className="text-blue-400" /><label className="text-xs font-semibold text-gray-300">Local Storage</label></div>
              <span className="text-xs text-gray-400">{formatBytes(storageInfo.usedBytes)} / {formatBytes(storageInfo.maxBytes)}</span>
            </div>
            <div className="w-full bg-gray-700 rounded-full h-2 mb-2 overflow-hidden">
              <div className={`h-2 w-full origin-left transition-[transform,background-color] duration-base ${storageInfo.usedPercentage > 80 ? 'bg-red-500' : storageInfo.usedPercentage > 60 ? 'bg-yellow-500' : 'bg-blue-500'}`} style={{ transform: `scaleX(${Math.min(storageInfo.usedPercentage, 100) / 100})` }} />
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-400">{storageInfo.trackCount} tracks cached</span>
              {storageInfo.trackCount > 0 && <button onClick={() => setShowCachedTracks(!showCachedTracks)} aria-expanded={showCachedTracks} className="ui-press text-purple-400 hover:text-purple-300 transition">{showCachedTracks ? 'Hide' : 'Show'}</button>}
            </div>
            <Expandable open={showCachedTracks && storageInfo.trackCount > 0} innerClassName="pt-3">
              <div className="space-y-2 max-h-60 overflow-y-auto">
                {storageInfo.tracks.map(track => (
                  <div key={track.trackId} className="flex items-center justify-between p-2 bg-dark-hover rounded hover:bg-gray-700 transition group">
                    <div className="flex-1 min-w-0 cursor-pointer" onClick={() => onPlayTrack(track.trackId)}>
                      <div className="text-xs font-medium truncate">{track.metadata?.generation_params?.title || track.metadata?.title || 'Untitled'}</div>
                      <div className="text-xs text-gray-500 truncate">{formatBytes(track.size)} • {track.bitrate}</div>
                    </div>
                    <button onClick={() => handleDeleteCachedTrack(track.trackId)} className="ui-press transition p-1 hover:bg-red-500/20 rounded"><Trash2 size={12} className="text-gray-400 hover:text-red-500" /></button>
                  </div>
                ))}
              </div>
            </Expandable>
            {storageInfo.trackCount > 0 && <button onClick={handleClearAllCache} className="ui-press-soft w-full mt-3 px-3 py-2 bg-red-500/10 hover:bg-red-500/20 text-red-400 rounded text-xs font-medium transition flex items-center justify-center gap-2"><Trash2 size={12} /> Clear All Cache</button>}
          </div>

          <div className="bg-white/5 p-3 rounded-lg">
            <div className="flex items-center gap-2 mb-2"><Database size={14} className="text-purple-400" /><label className="text-xs font-semibold text-gray-300">Data Usage</label></div>
            <div className="space-y-1">
              <div className="flex justify-between text-xs"><span className="text-gray-400">Session:</span><span className="text-gray-300">{formatBytes(dataUsage.sessionDownloaded)}</span></div>
              <div className="flex justify-between text-xs"><span className="text-gray-400">Total:</span><span className="text-gray-300">{formatBytes(dataUsage.totalDownloaded)}</span></div>
            </div>
          </div>
        </ExpandSection>

        {!loading && (
          <ExpandSection
            className="p-4 md:p-6 border-b border-gray-800"
            open={libraryExpanded}
            onToggle={setLibraryExpanded}
            icon={Library}
            iconClassName="text-pink-400"
            title="My Library"
            meta={
              <span className="text-xs text-gray-400">
                ({filteredLikedTracks.length + filteredSuperLikedTracks.length + filteredBannedTracks.length + filteredLikedShoutouts.length + filteredSuperLikedShoutouts.length + filteredBannedShoutouts.length})
              </span>
            }
            contentClassName="space-y-6 pl-2"
          >
            <PreferenceList title="Liked Tracks" items={filteredLikedTracks} icon={Heart} iconColor="text-pink-500" expanded={expandedLiked} onToggleExpand={setExpandedLiked} emptyMessage="No liked tracks yet"
              renderItem={(track) => <TrackItem key={track.id} track={track} icon={Heart} iconColor="text-pink-500" onPlayTrack={onPlayTrack} onRemovePreference={(id) => handleRemovePreference('track', id)} isLoading={isPending('track', track.id)} />} />

            <PreferenceList title="Super Liked Tracks" items={filteredSuperLikedTracks} icon={Star} iconColor="text-yellow-500" expanded={expandedSuperLiked} onToggleExpand={setExpandedSuperLiked} emptyMessage="No super liked tracks yet"
              renderItem={(track) => <TrackItem key={track.id} track={track} icon={Star} iconColor="text-yellow-500" onPlayTrack={onPlayTrack} onRemovePreference={(id) => handleRemovePreference('track', id)} isLoading={isPending('track', track.id)} />} />

            <PreferenceList title="Banned Tracks" items={filteredBannedTracks} icon={Ban} iconColor="text-red-500" expanded={expandedBanned} onToggleExpand={setExpandedBanned} emptyMessage="No banned tracks"
              renderItem={(track) => <TrackItem key={track.id} track={track} icon={Ban} iconColor="text-red-500" onPlayTrack={onPlayTrack} onRemovePreference={(id) => handleRemovePreference('track', id)} isLoading={isPending('track', track.id)} />} />

            <PreferenceList title="Liked Shoutouts" items={filteredLikedShoutouts} icon={Heart} iconColor="text-pink-500" expanded={expandedLikedShoutouts} onToggleExpand={setExpandedLikedShoutouts} emptyMessage="No liked shoutouts yet"
              renderItem={(shoutout) => <ShoutoutItem key={shoutout.id} shoutout={shoutout} icon={Heart} iconColor="text-pink-500" onPlayShoutout={handlePlayShoutout} onRemovePreference={(id) => handleRemovePreference('shoutout', id)} isLoading={isPending('shoutout', shoutout.id)} isPlaying={playingShoutout?.id === shoutout.id} />} />

            <PreferenceList title="Super Liked Shoutouts" items={filteredSuperLikedShoutouts} icon={Star} iconColor="text-yellow-500" expanded={expandedSuperLikedShoutouts} onToggleExpand={setExpandedSuperLikedShoutouts} emptyMessage="No super liked shoutouts yet"
              renderItem={(shoutout) => <ShoutoutItem key={shoutout.id} shoutout={shoutout} icon={Star} iconColor="text-yellow-500" onPlayShoutout={handlePlayShoutout} onRemovePreference={(id) => handleRemovePreference('shoutout', id)} isLoading={isPending('shoutout', shoutout.id)} isPlaying={playingShoutout?.id === shoutout.id} />} />

            <PreferenceList title="Banned Shoutouts" items={filteredBannedShoutouts} icon={Ban} iconColor="text-red-500" expanded={expandedBannedShoutouts} onToggleExpand={setExpandedBannedShoutouts} emptyMessage="No banned shoutouts"
              renderItem={(shoutout) => <ShoutoutItem key={shoutout.id} shoutout={shoutout} icon={Ban} iconColor="text-red-500" onPlayShoutout={handlePlayShoutout} onRemovePreference={(id) => handleRemovePreference('shoutout', id)} isLoading={isPending('shoutout', shoutout.id)} isPlaying={playingShoutout?.id === shoutout.id} />} />
          </ExpandSection>
        )}

        <ExpandSection
          className="p-4 md:p-6 border-b border-gray-800"
          open={uploadsExpanded}
          onToggle={setUploadsExpanded}
          icon={Upload}
          iconClassName="text-emerald-400"
          title="My Uploads"
          meta={userUploads.length > 0 && (
            <span className="text-xs text-gray-400">({userUploads.length})</span>
          )}
          contentClassName="space-y-4 pl-2"
        >
          <button
            onClick={openUploadModal}
            className="ui-press-soft w-full px-4 py-3 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg flex items-center justify-center gap-2 transition font-medium"
          >
            <Upload size={18} />
            Upload Your Music
          </button>

          {loadingUploads ? (
            <div className="ui-fade-in flex items-center justify-center py-8">
              <Loader2 size={24} className="animate-spin text-gray-400" />
            </div>
          ) : userUploads.length === 0 ? (
            <div className="ui-fade-in text-center py-6">
              <Music size={32} className="mx-auto text-gray-600 mb-2" />
              <p className="text-sm text-gray-400">No uploads yet</p>
              <p className="text-xs text-gray-500 mt-1">Upload your music and we&apos;ll analyze it with AI</p>
            </div>
          ) : (
            <div className="ui-fade-in space-y-2">
              {userUploads.map(track => (
                <UploadedTrackItem
                  key={track.id}
                  track={track}
                  onPlayTrack={onPlayTrack}
                  onDeleteUpload={handleDeleteUpload}
                  isDeleting={deletingUploadId === track.id}
                />
              ))}
            </div>
          )}
        </ExpandSection>

        <ExpandSection
          className="p-4 md:p-6 border-b border-gray-800"
          open={dataManagementExpanded}
          onToggle={setDataManagementExpanded}
          icon={Trash2}
          iconClassName="text-red-400"
          title="Data Management"
          contentClassName="space-y-3 pl-2"
        >
          <button onClick={handleDeleteConversationHistory} className="ui-press-soft w-full px-4 py-2 bg-white/5 hover:bg-red-500/20 text-gray-400 hover:text-red-400 rounded-lg flex items-center justify-center gap-2 transition text-sm">
            <MessageSquareX size={16} /> Delete Conversation History
          </button>
          <button onClick={handleResetPersona} className="ui-press-soft w-full px-4 py-2 bg-white/5 hover:bg-yellow-500/20 text-gray-400 hover:text-yellow-400 rounded-lg flex items-center justify-center gap-2 transition text-sm">
            <RotateCcw size={16} /> Reset Persona & Profile
          </button>
        </ExpandSection>

        {user?.tier === 'premium' ? (
          <div className="p-4 md:p-6 border-b border-gray-800">
            <div className="bg-gradient-to-br from-yellow-500/10 to-orange-500/10 p-4 rounded-lg border border-yellow-500/30">
              <div className="flex items-center gap-2 mb-2"><span className="text-2xl">⭐</span><h3 className="font-bold text-yellow-400">PLAiR Premium</h3></div>
              {generationUsage && (
                <p className="text-sm text-gray-300 mb-1">{generationUsage.remaining} of {generationUsage.limit} AI generations left this {generationUsage.period}</p>
              )}
              {periodEnd && !Number.isNaN(periodEnd.getTime()) && (
                <p className="text-xs text-gray-400 mb-1">Current period ends {periodEnd.toLocaleDateString()}</p>
              )}
              {billingStatus?.status === 'past_due' && (
                <p className="text-xs text-red-400 mb-1">Your last payment failed. Update your payment method to keep Premium.</p>
              )}
              {billingStatus?.has_billing_account && (
                <button onClick={handleManageSubscription} disabled={billingBusy} className="ui-press-soft w-full mt-3 px-4 py-2 bg-white/5 hover:bg-white/10 text-gray-200 rounded-lg flex items-center justify-center gap-2 transition text-sm disabled:opacity-60">
                  {billingBusy ? <Loader2 size={16} className="animate-spin" /> : <Settings size={16} />} Manage subscription
                </button>
              )}
            </div>
          </div>
        ) : (
          <div className="p-4 md:p-6 border-b border-gray-800">
            <div className="bg-gradient-to-br from-yellow-500/10 to-orange-500/10 p-4 rounded-lg border border-yellow-500/30">
              <div className="flex items-center gap-2 mb-2"><span className="text-2xl">⭐</span><h3 className="font-bold text-yellow-400">Upgrade to Premium</h3></div>
              <p className="text-sm text-gray-300 mb-3">Get ~100 AI tracks per month and support development!</p>
              <button onClick={handleUpgradeToPremium} disabled={billingBusy} className="ui-press-soft w-full px-4 py-3 text-white rounded-lg font-semibold transition flex items-center justify-center gap-2 disabled:opacity-60" style={{ background: getPremiumGradient() }}>
                {billingBusy ? <Loader2 size={18} className="animate-spin" /> : <span>🚀</span>} Upgrade to Premium - {priceLabel}
              </button>
            </div>
          </div>
        )}

        <div className="p-4 md:p-6">
          <button onClick={onLogout} className="ui-press-soft w-full px-4 py-2 bg-white/5 hover:bg-white/10 text-gray-400 hover:text-white rounded-lg flex items-center justify-center gap-2 transition text-sm">
            <LogOut size={16} /> Logout
          </button>
        </div>
      </Scroller>
    </div>
  )
})