import { useState, useEffect, useRef, memo, useCallback, useMemo } from 'react'
import { User as UserIcon, Heart, Star, Ban, LogIn, X, Music, Loader2, HardDrive, Wifi, WifiOff, Trash2, TrendingDown, Mic2, Volume2, MapPin, Cloud, Clock, Edit2, Gauge, Camera, Download, Sparkles, Headphones, Library, Settings, Upload, Play, Video, Image, DollarSign, Smartphone, Megaphone, MessageSquareText, Plus, Lock, EyeOff, Crown, VolumeX } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import { usePreferences } from '../contexts/PreferencesContext'
import { useStorage } from '../contexts/StorageContext'
import { useUISelector } from '../contexts/UIStateContext'
import { usePlaybackShoutout } from '../contexts/PlaybackShoutoutContext'
import { useDeviceSelector } from '../hooks/useDeviceSelector'
import { usePointerInteraction } from '../hooks/usePointerInteraction'
import { useDialog } from '../contexts/DialogContext'
import { api } from '../lib/api'
import { applySoundMode, SOUND_MODES, soundModeFor } from '../lib/soundModes'
import { backgroundDownloader } from '../lib/backgroundDownloader'
import { useArtworkThumb } from '../contexts/UIStateContext'
import { useProfilePicture } from '../hooks/useProfilePicture'
import { useDeletePost } from '../hooks/useDeletePost'
import { profilePictureCache } from '../lib/mediaCache'
import { logger } from '../lib/logger'
import { safeStorage } from '../lib/safeStorage'
import { PanelHeader } from './Panel'
import { Scroller } from './Scroller'
import { Expandable, ExpandChevron } from './Motion'
import { SettingTile, TileGroup, nextOf } from './SettingTile'
import { RadioModeSettings } from './RadioModeSettings'
import { AccountSettings } from './AccountSettings'
import { useDynamicTheme, PANEL } from '../contexts/DynamicThemeContext'
import { CSS_TRANSITION } from '../lib/motion'
import { formatTimeAgo } from '../lib/utils'

const SOUND_MODE_COLOR = { both: '#4ade80', dj: '#38bdf8', ping: '#fbbf24', off: '#f87171' }
const AUDIO_QUALITIES = ['auto', '256k', '192k', '128k']
const AUDIO_QUALITY_LABELS = { auto: 'Auto', '256k': '256k', '192k': '192k', '128k': '128k' }
const VISUAL_QUALITIES = ['high', 'medium', 'low']
const VISUAL_QUALITY_LABELS = { high: 'HIGH', medium: 'MID', low: 'LOW' }
const VISUAL_QUALITY_COLOR = { high: '#c084fc', medium: '#fbbf24', low: '#4ade80' }
const LIBRARY_KINDS = [{ id: 'track', label: 'Tracks' }, { id: 'shoutout', label: 'Shoutouts' }]
const LIBRARY_RATINGS = [
  { id: 'liked', label: 'Liked', icon: Heart, color: 'text-pink-500' },
  { id: 'super_liked', label: 'Super liked', icon: Star, color: 'text-yellow-500' },
  { id: 'banned', label: 'Banned', icon: Ban, color: 'text-red-500' },
]
const CHIP = 'ui-press px-3 py-1 rounded-full text-xs font-medium transition'
const CHIP_ON = 'bg-white/15 text-white'
const CHIP_OFF = 'text-gray-400 hover:text-white hover:bg-white/5'
const SELECT_CLASS = 'w-full px-3 py-2 bg-dark-hover border border-gray-700 rounded-lg text-sm focus:outline-none focus:border-purple-500 transition'

const deviceName = (list, id) => {
  if (!id) return 'Default'
  const label = list.find(device => device.id === id)?.label || 'Default'
  return label.replace(/\s*\(.*?\)\s*/g, ' ').trim() || 'Default'
}

const shortPlace = (location) => String(location || '').split(',')[0].trim()

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
  removeIcon: RemoveIcon = X,
  removeTitle = 'Remove preference',
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
        title={removeTitle}
        aria-label={removeTitle}
        disabled={isLoading}
      >
        {isLoading ? <Loader2 size={16} className="animate-spin text-gray-400" /> : <RemoveIcon size={16} className="text-gray-400 hover:text-red-500" />}
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

const UploadedTrackItem = memo(function UploadedTrackItem({ track, onPlayTrack, onEditUpload, onDeleteUpload, isDeleting }) {
  const artworkUrl = useArtworkThumb(track?.id, track?.has_artwork)
  const params = track.generation_params || {}
  const title = params.title || track.title || 'Untitled'
  const artist = params.artist_name || track.track_info?.artist || 'Unknown Artist'
  const genre = track.derived_tags?.primary_genre || params.style || 'Unknown Genre'
  const visibility = track.visibility || 'public'

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
        <div className="flex items-center gap-1.5 min-w-0">
          {visibility === 'private' && <Lock size={12} className="flex-shrink-0 text-gray-400" aria-label="Private" />}
          {visibility === 'unlisted' && <EyeOff size={12} className="flex-shrink-0 text-gray-400" aria-label="Unlisted" />}
          <span className="font-medium truncate">{title}</span>
        </div>
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
        onClick={() => onEditUpload(track.id)}
        className="ui-press p-2 rounded-full hover:bg-white/10 text-gray-400 hover:text-white transition"
        title="Edit track"
        aria-label="Edit track"
      >
        <Edit2 size={16} />
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

const ARTIST_INPUT_CLASS = 'w-full px-3 py-2 bg-dark-hover border border-gray-700 rounded-lg text-sm focus:outline-none focus:border-purple-500 transition'

const parseLinks = (text) => String(text || '').split(/[\s,]+/).map(link => link.trim()).filter(Boolean)

const ArtistProfileItem = memo(function ArtistProfileItem({ artist, onSave, onDelete, isDeleting }) {
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [name, setName] = useState('')
  const [bio, setBio] = useState('')
  const [links, setLinks] = useState('')
  const count = artist.track_count || 0
  const artistLinks = artist.links || []

  const startEdit = () => {
    setName(artist.name || '')
    setBio(artist.bio || '')
    setLinks(artistLinks.join('\n'))
    setEditing(true)
  }

  const cancel = () => {
    if (!saving) setEditing(false)
  }

  const save = async () => {
    if (saving) return
    const changes = {}
    const nextName = name.split(/\s+/).filter(Boolean).join(' ')
    const nextBio = bio.trim()
    const nextLinks = parseLinks(links)
    if (nextName !== artist.name) changes.name = nextName
    if (nextBio !== (artist.bio || '')) changes.bio = nextBio
    if (nextLinks.join('\n') !== artistLinks.join('\n')) changes.links = nextLinks
    if (!Object.keys(changes).length) {
      setEditing(false)
      return
    }
    setSaving(true)
    const ok = await onSave(artist, changes)
    setSaving(false)
    if (ok) setEditing(false)
  }

  if (editing) {
    return (
      <div className="bg-white/5 p-3 rounded-lg space-y-2">
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Band or artist name"
          maxLength={80}
          aria-label="Name"
          className={ARTIST_INPUT_CLASS}
          autoFocus
          onKeyDown={(e) => {
            if (e.key === 'Enter') void save()
            if (e.key === 'Escape') cancel()
          }}
        />
        <textarea
          value={bio}
          onChange={(e) => setBio(e.target.value)}
          placeholder="Bio (optional)"
          aria-label="Bio"
          rows={3}
          className={`${ARTIST_INPUT_CLASS} resize-y`}
        />
        <textarea
          value={links}
          onChange={(e) => setLinks(e.target.value)}
          placeholder="Links, one per line (optional)"
          aria-label="Links"
          rows={2}
          className={`${ARTIST_INPUT_CLASS} resize-y`}
        />
        <div className="flex gap-2">
          <button onClick={save} disabled={saving} className="ui-press px-3 py-1.5 rounded text-xs font-medium bg-green-500/20 text-green-400 hover:bg-green-500/30 flex items-center gap-1.5 disabled:opacity-60">
            {saving && <Loader2 size={12} className="animate-spin" />}
            Save
          </button>
          <button onClick={cancel} disabled={saving} className="ui-press px-3 py-1.5 rounded text-xs font-medium bg-gray-500/20 text-gray-400 hover:bg-gray-500/30 disabled:opacity-60">
            Cancel
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-3 p-3 rounded-lg bg-white/5">
      <div className="w-10 h-10 rounded-full bg-gradient-to-br from-fuchsia-600 to-purple-600 flex items-center justify-center flex-shrink-0">
        <Mic2 size={18} className="text-white/80" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="font-medium truncate">{artist.name}</div>
        <div className="text-xs text-gray-400 truncate">
          {[`${count} track${count === 1 ? '' : 's'}`, artistLinks.length ? `${artistLinks.length} link${artistLinks.length === 1 ? '' : 's'}` : null].filter(Boolean).join(' • ')}
        </div>
        {artist.bio && <div className="text-xs text-gray-500 truncate">{artist.bio}</div>}
      </div>
      <button onClick={startEdit} className="ui-tap p-2 rounded-full text-gray-400 hover:text-white hover:bg-white/10 transition" title="Edit artist" aria-label={`Edit ${artist.name}`}>
        <Edit2 size={16} />
      </button>
      <button onClick={() => onDelete(artist)} disabled={isDeleting} className="ui-tap p-2 rounded-full text-gray-400 hover:text-red-400 hover:bg-red-500/20 transition" title="Delete artist" aria-label={`Delete ${artist.name}`}>
        {isDeleting ? <Loader2 size={16} className="animate-spin" /> : <Trash2 size={16} />}
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

const postSubtitle = (post) => {
  const when = formatTimeAgo(post?.timestamp)
  const typed = post?.has_audio === false ? 'Typed' : null
  let about = null
  if (post?.kind === 'review') about = post.track?.title ? `On ${post.track.title}` : 'Song review'
  else if (post?.kind === 'reply') about = post.parent_preview?.username ? `Reply to ${post.parent_preview.username}` : 'Reply'
  return [about, typed, when].filter(Boolean).join(' • ')
}

const OwnPostItem = memo(function OwnPostItem({ post, icon, iconColor, onPlay, onDelete, isDeleting, isPlaying }) {
  const { user } = useAuth()
  const profilePictureUrl = useProfilePicture(user?.id, !!user?.profile_picture)
  const userInitial = user?.username?.charAt(0).toUpperCase() || 'U'

  return (
    <MediaRow
      image={profilePictureUrl}
      fallbackIcon={<span className="text-xl font-bold">{userInitial}</span>}
      title={`"${post?.transcription || post?.full_transcription || 'No transcription'}"`}
      subtitle={postSubtitle(post)}
      typeIcon={icon}
      typeIconColor={iconColor}
      onPlay={() => onPlay(post)}
      onRemove={() => onDelete(post)}
      removeIcon={Trash2}
      removeTitle={post?.kind === 'review' ? 'Delete review' : 'Delete'}
      isLoading={isDeleting}
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
    openEditTrack,
    uploadModalOpen,
    openUsageModal,
    tiltEnabled,
    tiltNeedsPermission,
    enableTiltEffects,
    shoutoutUpdates,
    reviewUpdates,
    uploadUpdates,
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
    openEditTrack: state.openEditTrack,
    uploadModalOpen: state.uploadModalOpen,
    openUsageModal: state.openUsageModal,
    tiltEnabled: state.tiltEnabled,
    tiltNeedsPermission: state.tiltNeedsPermission,
    enableTiltEffects: state.enableTiltEffects,
    shoutoutUpdates: state.contentUpdates.shoutouts,
    reviewUpdates: state.contentUpdates.reviews,
    uploadUpdates: state.contentUpdates.uploads,
  }))
  const success = toastSuccess
  const error = toastError

  const { devices, selectedMicrophone, selectedSpeaker, selectMicrophone, selectSpeaker, requestPermissions } = useDeviceSelector()
  const { playingShoutout, playShoutout, stopShoutout } = usePlaybackShoutout()
  const { showConfirm } = useDialog()
  const deletePost = useDeletePost()

  const [showCachedTracks, setShowCachedTracks] = useState(false)
  const [isEditingUsername, setIsEditingUsername] = useState(false)
  const [newUsername, setNewUsername] = useState('')

  const [expandedLibrary, setExpandedLibrary] = useState(false)
  const [libraryKind, setLibraryKind] = useState('track')
  const [libraryRating, setLibraryRating] = useState('liked')

  const [openTile, setOpenTile] = useState(() => safeStorage.get('userPanel_openTile') || null)
  const toggleTile = useCallback((key) => setOpenTile(prev => (prev === key ? null : key)), [])
  const [myArtists, setMyArtists] = useState([])
  const [loadingArtists, setLoadingArtists] = useState(false)
  const [newArtistName, setNewArtistName] = useState('')
  const [addingArtist, setAddingArtist] = useState(false)
  const [deletingArtistId, setDeletingArtistId] = useState(null)
  const [expandedMyShoutouts, setExpandedMyShoutouts] = useState(false)
  const [expandedMyReviews, setExpandedMyReviews] = useState(false)
  const [myPosts, setMyPosts] = useState({ shoutouts: [], reviews: [] })
  const [deletingPostId, setDeletingPostId] = useState(null)

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
    if (!isAuthenticated) return
    let cancelled = false
    api.getBillingStatus()
      .then(status => { if (!cancelled) setBillingStatus(status) })
      .catch(err => logger.warn('Failed to load billing status:', err))
    return () => { cancelled = true }
  }, [isAuthenticated, user?.tier])

  useEffect(() => {
    if (openTile) safeStorage.set('userPanel_openTile', openTile)
    else safeStorage.remove('userPanel_openTile')
  }, [openTile])

  useEffect(() => {
    if (!isAuthenticated) {
      setMyPosts({ shoutouts: [], reviews: [] })
      return
    }
    let cancelled = false
    api.getMyCommunityPosts()
      .then(data => {
        if (cancelled || !data) return
        const shoutoutsAndReplies = [...(data.shoutouts || []), ...(data.replies || [])]
          .sort((a, b) => String(b.timestamp || '').localeCompare(String(a.timestamp || '')))
        setMyPosts({ shoutouts: shoutoutsAndReplies, reviews: data.reviews || [] })
      })
      .catch(err => logger.warn('Failed to load your posts:', err))
    return () => { cancelled = true }
  }, [isAuthenticated, user?.id, shoutoutUpdates, reviewUpdates])

  const handleDeletePost = useCallback(async (post) => {
    if (!post?.id) return
    setDeletingPostId(post.id)
    try {
      if (!await deletePost(post)) return
      setMyPosts(prev => ({
        shoutouts: prev.shoutouts.filter(p => p.id !== post.id && p.parent_id !== post.id),
        reviews: prev.reviews.filter(p => p.id !== post.id),
      }))
    } finally {
      setDeletingPostId(null)
    }
  }, [deletePost])

  const fetchUserUploads = useCallback(async ({ quiet = false } = {}) => {
    if (!isAuthenticated) return
    if (!quiet) setLoadingUploads(true)
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
    if (isAuthenticated) void fetchUserUploads()
  }, [isAuthenticated, fetchUserUploads])

  const uploadModalWasOpenRef = useRef(uploadModalOpen)
  useEffect(() => {
    const wasOpen = uploadModalWasOpenRef.current
    uploadModalWasOpenRef.current = uploadModalOpen
    if (wasOpen && !uploadModalOpen && isAuthenticated) {
      void fetchUserUploads({ quiet: true })
    }
  }, [uploadModalOpen, isAuthenticated, fetchUserUploads])

  const handledUploadUpdatesRef = useRef(uploadUpdates)
  useEffect(() => {
    if (uploadUpdates === handledUploadUpdatesRef.current) return
    handledUploadUpdatesRef.current = uploadUpdates
    if (isAuthenticated) void fetchUserUploads({ quiet: true })
  }, [uploadUpdates, isAuthenticated, fetchUserUploads])

  const fetchMyArtists = useCallback(async () => {
    if (!isAuthenticated) return
    setLoadingArtists(true)
    try {
      const data = await api.getUploadSetup()
      setMyArtists(data?.artists || [])
    } catch (err) {
      logger.error('Failed to fetch your artists:', err)
    } finally {
      setLoadingArtists(false)
    }
  }, [isAuthenticated])

  useEffect(() => {
    if (isAuthenticated) void fetchMyArtists()
  }, [isAuthenticated, fetchMyArtists])

  const handleAddArtist = async () => {
    const name = newArtistName.split(/\s+/).filter(Boolean).join(' ')
    if (!name || addingArtist) return
    setAddingArtist(true)
    try {
      const artist = await api.createArtist({ name })
      setMyArtists(prev => [...prev.filter(a => a.id !== artist.id), { ...artist, track_count: artist.track_count ?? 0 }])
      setNewArtistName('')
      success(`Added ${artist.name}`)
    } catch (err) {
      error(err.message || 'Could not add the artist')
    } finally {
      setAddingArtist(false)
    }
  }

  const handleSaveArtist = useCallback(async (artist, changes) => {
    try {
      const updated = await api.updateArtist(artist.id, changes)
      const { tracks_updated: tracksUpdated, ...profile } = updated
      setMyArtists(prev => prev.map(a => (a.id === artist.id ? { ...a, ...profile } : a)))
      if (profile.name && profile.name !== artist.name) {
        const count = tracksUpdated || 0
        success(`Renamed - ${count} track${count === 1 ? '' : 's'} updated`)
        void fetchUserUploads({ quiet: true })
      } else {
        success('Artist saved')
      }
      return true
    } catch (err) {
      error(err.message || 'Could not save the artist')
      return false
    }
  }, [success, error, fetchUserUploads])

  const handleDeleteArtist = useCallback(async (artist) => {
    const confirmed = await showConfirm({
      title: `Delete ${artist.name}?`,
      message: 'This removes the band/artist from your profile.',
      confirmText: 'Delete',
      cancelText: 'Cancel',
      variant: 'danger'
    })
    if (!confirmed) return
    setDeletingArtistId(artist.id)
    try {
      await api.deleteArtist(artist.id)
      setMyArtists(prev => prev.filter(a => a.id !== artist.id))
      success(`Deleted ${artist.name}`)
    } catch (err) {
      error(err.message || 'Could not delete the artist')
    } finally {
      setDeletingArtistId(null)
    }
  }, [showConfirm, success, error])

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
        void fetchMyArtists()
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

  const soundMode = soundModeFor(settingsState.ttsMuted, settingsState.notificationsMuted)

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
      await applySoundMode(mode, { user, publishSettings, refreshUser })
      success(mode.label, 1500)
    } catch (_err) {
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

  const libraryLists = {
    track: { liked: filteredLikedTracks, super_liked: filteredSuperLikedTracks, banned: filteredBannedTracks },
    shoutout: { liked: filteredLikedShoutouts, super_liked: filteredSuperLikedShoutouts, banned: filteredBannedShoutouts },
  }
  const libraryCount = Object.values(libraryLists).reduce((sum, lists) => sum + Object.values(lists).reduce((n, list) => n + list.length, 0), 0)
  const libraryRatingMeta = LIBRARY_RATINGS.find(rating => rating.id === libraryRating) || LIBRARY_RATINGS[0]
  const libraryItems = libraryLists[libraryKind][libraryRatingMeta.id]
  const hasAboutYou = !!(user?.persona || user?.profile || user?.shoutout_interests)
  const hasLocation = !!(user?.location || user?.timezone || user?.weather_description)
  const isPremium = user?.tier === 'premium'

  const deviceDrawer = (kind) => {
    const isMic = kind === 'mic'
    const list = isMic ? devices.microphones : devices.speakers
    const selected = isMic ? selectedMicrophone : selectedSpeaker
    const onChange = async (e) => {
      try {
        if (isMic) selectMicrophone(e.target.value)
        else await selectSpeaker(e.target.value)
        success(isMic ? 'Microphone updated' : 'Speakers updated')
      } catch (_err) {
        error('Failed to change speakers')
        logger.error('Failed to change speakers:', _err)
      }
    }
    return (
      <div>
        <select value={selected} onChange={onChange} aria-label={isMic ? 'Microphone' : 'Speakers'} className={SELECT_CLASS}>
          <option value="">{isMic ? 'Default Microphone' : 'Default Speakers'}</option>
          {list.map(device => <option key={device.id} value={device.id}>{device.label}</option>)}
        </select>
        {isMic && list.length === 0 && (
          <button onClick={requestPermissions} className="ui-press text-xs text-purple-400 hover:text-purple-300 mt-1">
            Grant microphone access
          </button>
        )}
        <DeviceTester deviceId={selected} type={isMic ? 'mic' : 'speaker'} />
      </div>
    )
  }

  const listeningDrawers = { mic: deviceDrawer('mic'), speakers: deviceDrawer('speaker') }

  const stuffDrawers = {
    library: (
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-1">
          {LIBRARY_KINDS.map(kind => (
            <button key={kind.id} onClick={() => { setLibraryKind(kind.id); setExpandedLibrary(false) }} aria-pressed={libraryKind === kind.id} className={`${CHIP} ${libraryKind === kind.id ? CHIP_ON : CHIP_OFF}`}>
              {kind.label}
            </button>
          ))}
          <span className="w-px h-4 bg-white/10 mx-1" />
          {LIBRARY_RATINGS.map(rating => (
            <button key={rating.id} onClick={() => { setLibraryRating(rating.id); setExpandedLibrary(false) }} aria-pressed={libraryRating === rating.id} className={`${CHIP} ${libraryRating === rating.id ? CHIP_ON : CHIP_OFF}`}>
              {rating.label} {libraryLists[libraryKind][rating.id].length}
            </button>
          ))}
        </div>
        <PreferenceList
          title={`${libraryRatingMeta.label} ${libraryKind === 'track' ? 'tracks' : 'shoutouts'}`}
          items={libraryItems}
          icon={libraryRatingMeta.icon}
          iconColor={libraryRatingMeta.color}
          expanded={expandedLibrary}
          onToggleExpand={setExpandedLibrary}
          emptyMessage={`Nothing ${libraryRatingMeta.label.toLowerCase()} yet`}
          renderItem={(item) => (libraryKind === 'track'
            ? <TrackItem key={item.id} track={item} icon={libraryRatingMeta.icon} iconColor={libraryRatingMeta.color} onPlayTrack={onPlayTrack} onRemovePreference={(id) => handleRemovePreference('track', id)} isLoading={isPending('track', item.id)} />
            : <ShoutoutItem key={item.id} shoutout={item} icon={libraryRatingMeta.icon} iconColor={libraryRatingMeta.color} onPlayShoutout={handlePlayShoutout} onRemovePreference={(id) => handleRemovePreference('shoutout', id)} isLoading={isPending('shoutout', item.id)} isPlaying={playingShoutout?.id === item.id} />)}
        />
      </div>
    ),
    posts: (
      <div className="space-y-6">
        <PreferenceList title="Your Shoutouts & Replies" items={myPosts.shoutouts} icon={Megaphone} iconColor="text-purple-400" expanded={expandedMyShoutouts} onToggleExpand={setExpandedMyShoutouts} emptyMessage="You haven't posted a shoutout yet"
          renderItem={(post) => <OwnPostItem key={post.id} post={post} icon={Megaphone} iconColor="text-purple-400" onPlay={handlePlayShoutout} onDelete={handleDeletePost} isDeleting={deletingPostId === post.id} isPlaying={playingShoutout?.id === post.id} />} />
        <PreferenceList title="Your Reviews" items={myPosts.reviews} icon={MessageSquareText} iconColor="text-pink-400" expanded={expandedMyReviews} onToggleExpand={setExpandedMyReviews} emptyMessage="You haven't reviewed a song yet"
          renderItem={(post) => <OwnPostItem key={post.id} post={post} icon={MessageSquareText} iconColor="text-pink-400" onPlay={handlePlayShoutout} onDelete={handleDeletePost} isDeleting={deletingPostId === post.id} isPlaying={playingShoutout?.id === post.id} />} />
      </div>
    ),
    uploads: (
      <div className="space-y-3">
        <button onClick={openUploadModal} className="ui-press-soft w-full px-4 py-3 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg flex items-center justify-center gap-2 transition font-medium">
          <Upload size={18} />
          Upload Your Music
        </button>
        {loadingUploads && userUploads.length === 0 ? (
          <div className="ui-fade-in flex items-center justify-center py-8">
            <Loader2 size={24} className="animate-spin text-gray-400" />
          </div>
        ) : userUploads.length === 0 ? (
          <div className="ui-fade-in text-center py-4">
            <p className="text-sm text-gray-400">No uploads yet</p>
            <p className="text-xs text-gray-500 mt-1">Upload your music and we&apos;ll analyze it with AI</p>
          </div>
        ) : (
          <div className="ui-fade-in space-y-2">
            {userUploads.map(track => (
              <UploadedTrackItem key={track.id} track={track} onPlayTrack={onPlayTrack} onEditUpload={openEditTrack} onDeleteUpload={handleDeleteUpload} isDeleting={deletingUploadId === track.id} />
            ))}
          </div>
        )}
      </div>
    ),
    artists: (
      <div className="space-y-3">
        <p className="text-xs text-gray-400">Your uploads are credited to these names. Pick one when you upload.</p>
        <div className="flex gap-2">
          <input
            type="text"
            value={newArtistName}
            onChange={(e) => setNewArtistName(e.target.value)}
            placeholder="Add a band or artist"
            maxLength={80}
            aria-label="New band or artist name"
            className={`${ARTIST_INPUT_CLASS} flex-1 min-w-0`}
            enterKeyHint="done"
            onKeyDown={(e) => { if (e.key === 'Enter') void handleAddArtist() }}
          />
          <button onClick={handleAddArtist} disabled={addingArtist || !newArtistName.trim()} className="ui-press px-3 py-2 rounded-lg text-sm font-medium bg-fuchsia-600 hover:bg-fuchsia-700 text-white flex items-center gap-1.5 disabled:opacity-50 transition">
            {addingArtist ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
            Add
          </button>
        </div>
        {loadingArtists && myArtists.length === 0 ? (
          <div className="ui-fade-in flex items-center justify-center py-6"><Loader2 size={20} className="animate-spin text-gray-400" /></div>
        ) : myArtists.length === 0 ? (
          <p className="ui-fade-in text-sm text-gray-400">No artists yet. Uploads use your username until you add one.</p>
        ) : (
          <div className="ui-fade-in space-y-2">
            {myArtists.map(artist => (
              <ArtistProfileItem key={artist.id} artist={artist} onSave={handleSaveArtist} onDelete={handleDeleteArtist} isDeleting={deletingArtistId === artist.id} />
            ))}
          </div>
        )}
      </div>
    ),
    about: hasAboutYou ? (
      <div className="space-y-3">
        {[
          { key: 'persona', label: 'AI Persona', text: user?.persona },
          { key: 'profile', label: 'Listener Profile', text: user?.profile },
          { key: 'interests', label: 'Shoutout Interests', text: user?.shoutout_interests },
        ].filter(part => part.text).map(part => (
          <div key={part.key}>
            <div className="text-xs font-semibold text-purple-300 mb-1">{part.label}</div>
            <p className="text-sm text-gray-300 leading-relaxed">{part.text}</p>
          </div>
        ))}
      </div>
    ) : null,
    location: hasLocation ? (
      <div className="space-y-2 text-sm">
        {user?.location && <div className="flex items-center gap-2"><MapPin size={14} className="text-blue-400" /><span className="text-gray-300">{user.location}</span></div>}
        {user?.timezone && <div className="flex items-center gap-2"><Clock size={14} className="text-purple-400" /><span className="text-gray-300">{user.timezone}</span></div>}
        {user?.weather_description && <div className="flex items-center gap-2"><Cloud size={14} className="text-cyan-400" /><span className="text-gray-300">{user.weather_description}</span></div>}
      </div>
    ) : null,
  }

  const storageDrawer = (
    <div className="space-y-3">
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
      <div>
        <div className="flex items-center justify-between mb-2 text-xs">
          <span className="font-semibold text-gray-300">Local storage</span>
          <span className="text-gray-400">{formatBytes(storageInfo.usedBytes)} / {formatBytes(storageInfo.maxBytes)}</span>
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
                <button onClick={() => handleDeleteCachedTrack(track.trackId)} aria-label="Remove download" className="ui-press transition p-1 hover:bg-red-500/20 rounded"><Trash2 size={12} className="text-gray-400 hover:text-red-500" /></button>
              </div>
            ))}
          </div>
        </Expandable>
        {storageInfo.trackCount > 0 && <button onClick={handleClearAllCache} className="ui-press-soft w-full mt-3 px-3 py-2 bg-red-500/10 hover:bg-red-500/20 text-red-400 rounded text-xs font-medium transition flex items-center justify-center gap-2"><Trash2 size={12} /> Clear All Cache</button>}
      </div>
      <div className="flex justify-between text-xs">
        <span className="text-gray-400">Data this session / total</span>
        <span className="text-gray-300">{formatBytes(dataUsage.sessionDownloaded)} / {formatBytes(dataUsage.totalDownloaded)}</span>
      </div>
    </div>
  )

  const premiumDrawer = isPremium ? (
    <div className="space-y-1 text-sm">
      {generationUsage && <p className="text-gray-300">{generationUsage.remaining} of {generationUsage.limit} AI generations left this {generationUsage.period}</p>}
      {periodEnd && !Number.isNaN(periodEnd.getTime()) && <p className="text-xs text-gray-400">Current period ends {periodEnd.toLocaleDateString()}</p>}
      {billingStatus?.status === 'past_due' && <p className="text-xs text-red-400">Your last payment failed. Update your payment method to keep Premium.</p>}
      {billingStatus?.has_billing_account && (
        <button onClick={handleManageSubscription} disabled={billingBusy} className="ui-press-soft w-full mt-2 px-4 py-2 bg-white/5 hover:bg-white/10 text-gray-200 rounded-lg flex items-center justify-center gap-2 transition text-sm disabled:opacity-60">
          {billingBusy ? <Loader2 size={16} className="animate-spin" /> : <Settings size={16} />} Manage subscription
        </button>
      )}
    </div>
  ) : (
    <div className="space-y-3">
      <p className="text-sm text-gray-300">Get ~100 AI tracks per month and support development!</p>
      <button onClick={handleUpgradeToPremium} disabled={billingBusy} className="ui-press-soft w-full px-4 py-3 text-white rounded-lg font-semibold transition flex items-center justify-center gap-2 disabled:opacity-60" style={{ background: getPremiumGradient() }}>
        {billingBusy ? <Loader2 size={18} className="animate-spin" /> : <Crown size={18} />} Upgrade to Premium - {priceLabel}
      </button>
    </div>
  )

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
            <RadioModeSettings className="mt-4 text-left" />
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
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploadingProfilePicture}
              className="ui-tap relative flex-shrink-0 rounded-full"
              aria-label={profilePictureUrl ? 'Change profile photo' : 'Add a profile photo'}
            >
              {profilePictureUrl ? (
                <img decoding="async" src={profilePictureUrl} alt={user?.username} className="w-10 h-10 rounded-full object-cover" />
              ) : (
                <div className="w-10 h-10 rounded-full flex items-center justify-center" style={{ background: getUserAvatarGradient(), color: 'white' }}>
                  <UserIcon size={20} />
                </div>
              )}
              <span className="absolute -bottom-0.5 -right-0.5 w-5 h-5 rounded-full bg-zinc-900 border border-white/20 flex items-center justify-center text-white">
                {uploadingProfilePicture ? <Loader2 size={11} className="animate-spin" /> : <Camera size={11} />}
              </span>
              <input ref={fileInputRef} type="file" accept="image/jpeg,image/jpg,image/png,image/webp,image/gif" onChange={handleProfilePictureUpload} className="hidden" />
            </button>
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
        <div className="p-3 md:p-4 space-y-3">
          <TileGroup title="Listening" drawerKey={listeningDrawers[openTile] ? openTile : null} drawer={listeningDrawers[openTile] || null}>
            <SettingTile drawer open={openTile === 'mic'} onClick={() => toggleTile('mic')} icon={Mic2} label="Microphone" color="#60a5fa" value={deviceName(devices.microphones, selectedMicrophone)} />
            <SettingTile drawer open={openTile === 'speakers'} onClick={() => toggleTile('speakers')} icon={Volume2} label="Speakers" color="#60a5fa" value={deviceName(devices.speakers, selectedSpeaker)} />
            <SettingTile cycle on icon={Music} label="Audio quality" color="#c084fc" value={AUDIO_QUALITY_LABELS[settingsState.audioQuality] || 'Auto'} hint="Streaming quality. Auto follows your connection." onClick={() => handleAudioQualityChange(nextOf(AUDIO_QUALITIES, settingsState.audioQuality))} />
            <SettingTile cycle on={soundMode?.id !== 'off'} icon={soundMode?.id === 'off' ? VolumeX : Headphones} label="Sounds" color={SOUND_MODE_COLOR[soundMode?.id] || '#4ade80'} value={soundMode?.short || 'DJ+PING'} hint="DJ voice and notification pings" onClick={() => handleSetSoundMode(nextOf(SOUND_MODES, soundMode))} />
            <SettingTile on={settingsState.autoClaimOnOpen !== false} icon={Smartphone} label="Auto-switch" color="#60a5fa" value={settingsState.autoClaimOnOpen !== false ? 'ON' : 'OFF'} hint="Move playback to the device you open" onClick={() => publishSettings({ autoClaimOnOpen: settingsState.autoClaimOnOpen === false })} />
          </TileGroup>

          <RadioModeSettings />

          <TileGroup title="Your stuff" drawerKey={stuffDrawers[openTile] ? openTile : null} drawer={stuffDrawers[openTile] || null}>
            <SettingTile drawer open={openTile === 'library'} onClick={() => toggleTile('library')} icon={Library} label="Library" color="#f472b6" value={`${libraryCount}`} />
            <SettingTile drawer open={openTile === 'posts'} onClick={() => toggleTile('posts')} icon={Megaphone} label="My posts" color="#c084fc" value={`${myPosts.shoutouts.length + myPosts.reviews.length}`} />
            <SettingTile drawer open={openTile === 'uploads'} onClick={() => toggleTile('uploads')} icon={Upload} label="Uploads" color="#34d399" value={loadingUploads && userUploads.length === 0 ? '…' : `${userUploads.length}`} />
            <SettingTile drawer open={openTile === 'artists'} onClick={() => toggleTile('artists')} icon={Mic2} label="Artists & Bands" color="#e879f9" value={`${myArtists.length}`} />
            {hasAboutYou && <SettingTile drawer open={openTile === 'about'} onClick={() => toggleTile('about')} icon={Sparkles} label="About you" color="#a78bfa" value="Hosts" />}
            {hasLocation && <SettingTile drawer open={openTile === 'location'} onClick={() => toggleTile('location')} icon={MapPin} label="Location" color="#4ade80" value={shortPlace(user?.location) || user?.timezone || '…'} />}
          </TileGroup>

          <TileGroup title="Display">
            <SettingTile cycle on icon={Image} label="Visual quality" color={VISUAL_QUALITY_COLOR[settingsState.visualQuality] || '#a78bfa'} value={VISUAL_QUALITY_LABELS[settingsState.visualQuality] || 'HIGH'} hint="Background visual effects. LOW saves battery on phones." onClick={() => handleSetVisualQuality(nextOf(VISUAL_QUALITIES, settingsState.visualQuality))} />
            <SettingTile on={!!settingsState.videoClipsEnabled} icon={Video} label="Video clips" color="#f472b6" value={settingsState.videoClipsEnabled ? 'ON' : 'OFF'} hint="Video clips in visuals and shared videos (uses more bandwidth)" onClick={handleToggleVideoClips} />
            {tiltNeedsPermission && <SettingTile on={!!tiltEnabled} icon={Smartphone} label="Tilt effects" color="#38bdf8" value={tiltEnabled ? 'ON' : 'OFF'} hint="Artwork moves as you tilt your phone. Your phone will ask for motion access." onClick={() => void enableTiltEffects(!tiltEnabled)} />}
            <SettingTile on={!!settingsState.fpsEnabled} icon={Gauge} label="FPS counter" color="#60a5fa" value={settingsState.fpsEnabled ? 'ON' : 'OFF'} hint="Show the frame rate" onClick={handleToggleFpsEnabled} />
          </TileGroup>

          <TileGroup title="Data & storage" drawerKey={openTile === 'storage' ? 'storage' : null} drawer={openTile === 'storage' ? storageDrawer : null}>
            <SettingTile on={audioState.isOnline} icon={audioState.isOnline ? Wifi : WifiOff} label="Network" color={audioState.isOnline ? (getNetworkQualityColor() || '#4ade80') : '#f87171'} value={audioState.isOnline ? getNetworkQualityLabel(audioState.networkQuality) : 'Offline'} hint="Your connection right now" />
            <SettingTile on={!!settingsState.dataSaverMode} icon={TrendingDown} label="Data saver" color="#4ade80" value={settingsState.dataSaverMode ? 'ON' : 'OFF'} hint="Use less mobile data" onClick={() => publishSettings({ dataSaverMode: !settingsState.dataSaverMode })} />
            <SettingTile on={!!downloadState?.isEnabled} busy={!!downloadState?.isDownloading} icon={Download} label="Downloads" color="#c084fc" value={downloadState?.isEnabled ? 'ON' : 'OFF'} hint="Download music in the background so it plays offline" onClick={handleToggleBackgroundDownloads} />
            <SettingTile drawer open={openTile === 'storage'} onClick={() => toggleTile('storage')} icon={HardDrive} label="Storage" color="#22d3ee" value={formatBytes(storageInfo.usedBytes)} />
          </TileGroup>

          {(user?.is_admin || user?.usage_stats_visible) && (
            <TileGroup title="Admin">
              <SettingTile icon={DollarSign} label="AI usage" color="#a78bfa" value="View" hint={user?.is_admin ? 'What PLAiR spends on AI, per listener, with projections' : 'Your AI usage this month'} onClick={openUsageModal} />
              {user?.is_admin && <SettingTile on={!!settingsState.costTickerEnabled} icon={DollarSign} label="Cost ticker" color="#a78bfa" value={settingsState.costTickerEnabled ? 'ON' : 'OFF'} hint="Live AI cost ticker" onClick={() => publishSettings({ costTickerEnabled: !settingsState.costTickerEnabled })} />}
            </TileGroup>
          )}

          <AccountSettings
            onLogout={onLogout}
            openTile={openTile}
            onToggleTile={toggleTile}
            leadingTiles={<SettingTile drawer open={openTile === 'premium'} onClick={() => toggleTile('premium')} icon={Crown} label="Premium" color="#fbbf24" on={isPremium} value={isPremium ? 'PRO' : 'Free'} />}
            extraDrawers={{ premium: premiumDrawer }}
          />
        </div>
      </Scroller>
    </div>
  )
})
