import { useState, useRef, useCallback, useEffect, useMemo, memo } from 'react'
import { Upload, Loader2, Check, X, Edit2, Sparkles, FileAudio, FileVideo, Image, Gauge, Mic2, Wand2, Plus, Globe, EyeOff, Lock, Info } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { PRESETS } from '../../lib/motion'
import { api } from '../../lib/api'
import { logger } from '../../lib/logger'
import { triggerHaptic } from '../../lib/haptics'
import { fetchFinishedUploadJob, isUploadFinished, UPLOAD_FINISHED_STATUSES } from '../../lib/uploadJobs'
import { useDynamicTheme } from '../../contexts/DynamicThemeContext'
import { useWebSocketSubscribe } from '../../contexts/WebSocketContext'
import { useAuth } from '../../contexts/AuthContext'
import { useUISelector } from '../../contexts/UIStateContext'
import { useDialog } from '../../contexts/DialogContext'
import { ToggleChip } from '../SettingRow'
import { Modal, ModalSection, ModalButton, ModalFooter, ModalCard, ModalProgress, ModalErrorState, ModalSuccessBanner, ModalTagList } from './Modal'

const AUDIO_FORMATS = ['mp3', 'wav', 'flac', 'ogg', 'm4a', 'aac', 'opus', 'webm']
const VIDEO_FORMATS = ['mp4', 'mov', 'm4v', 'mkv', 'avi']
const SUPPORTED_FORMATS = [...AUDIO_FORMATS, ...VIDEO_FORMATS]
const MAX_AUDIO_FILE_SIZE = 100 * 1024 * 1024
const MAX_VIDEO_FILE_SIZE = 10 * 1024 * 1024 * 1024
const ENHANCE_HINT = 'Restores detail on low-quality files. Slower.'
const RIGHTS_LABEL = 'I made this music or have the rights to share it'
const DESCRIPTION_MAX = 2000
const FILE_ACCEPT = ['audio/*', 'video/*', ...SUPPORTED_FORMATS.map(f => `.${f}`)].join(',')
const UPLOAD_EVENT_QUIET_MS = 10000
const UPLOAD_POLL_MS = 5000
const ANALYZING_HINT = "You can close this - we'll let you know when it's live."
const DUPLICATE_NOTICE = "You've already uploaded this song - here it is."
const LOST_UPLOAD_MESSAGE = 'This upload was interrupted. Please try again.'

const newUploadId = () => `up_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`

const VISIBILITY_OPTIONS = [
  { value: 'public', label: 'Public', icon: Globe, hint: 'Plays on the station and shows everywhere.' },
  { value: 'unlisted', label: 'Unlisted', icon: EyeOff, hint: 'Only people with the link.' },
  { value: 'private', label: 'Private', icon: Lock, hint: 'Only you.' }
]

const UploadStage = {
  SELECT: 'select',
  UPLOADING: 'uploading',
  ANALYZING: 'analyzing',
  PREVIEW: 'preview',
  ERROR: 'error'
}

const cleanText = (text) => String(text || '').split(/\s+/).filter(Boolean).join(' ')

const splitTags = (text) => String(text || '').split(',').map(cleanText).filter(Boolean)

const mergeTags = (list, extra) => {
  const seen = new Set()
  return [...list, ...extra].filter(tag => {
    const key = tag.toLowerCase()
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

const sameTags = (a = [], b = []) => a.length === b.length && a.every((tag, i) => tag === b[i])

const pickDefaultArtist = (artists, lastId) => {
  if (artists.some(a => a.id === lastId)) return lastId
  return artists[0]?.id ?? null
}

const isRightsError = (err) => err?.status === 400 && /rights/i.test(err?.message || '')

const trackToMetadata = (track) => {
  const params = track?.generation_params || {}
  const info = track?.track_info || {}
  const tags = track?.derived_tags || {}
  const quality = track?.source_quality
  const lyrics = track?.transcribed_lyrics || (params.instrumental ? '' : params.prompt || '')
  return {
    title: params.title || info.title || '',
    artist: params.artist_name || info.artist || '',
    artist_profile_id: track?.artist_profile_id ?? null,
    style: params.style || null,
    primary_genre: tags.primary_genre || '',
    secondary_genres: tags.secondary_genres || [],
    mood_keywords: tags.mood_keywords || [],
    similar_artists: tags.similar_artists || [],
    vocal_style_keywords: tags.vocal_style_keywords || [],
    transcribed_lyrics: lyrics || null,
    has_lyrics: !!lyrics,
    lyrical_interpretation: tags.lyrical_interpretation || null,
    visibility: track?.visibility || 'public',
    explicit: !!track?.explicit,
    ai_assisted: !!track?.ai_assisted,
    description: track?.description || '',
    has_artwork: !!track?.has_artwork,
    artwork_generated: !!track?.artwork_generated,
    artwork_prompt: track?.artwork_prompt || null,
    mastering_applied: !!track?.mastering_applied,
    enhancement_applied: !!track?.enhancement_applied,
    sonic_master_applied: !!track?.sonic_master_applied,
    source_quality: quality ? {
      tier: quality.quality_tier || quality.tier || 'unknown',
      sample_rate: quality.sample_rate,
      bit_depth: quality.bit_depth,
      is_lossless: quality.is_lossless,
      processing_notes: quality.processing_notes || ''
    } : null
  }
}

const FieldLabel = memo(function FieldLabel({ children }) {
  const { getGrey400 } = useDynamicTheme()
  return <label className="text-xs uppercase tracking-wide" style={{ color: getGrey400() }}>{children}</label>
})

const EditButton = memo(function EditButton({ label, onClick }) {
  const { getGrey400 } = useDynamicTheme()
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`Edit ${label}`}
      className="ui-tap p-1.5 -m-1 rounded-md flex-shrink-0 hover:bg-white/10 hover:text-white transition"
      style={{ color: getGrey400() }}
    >
      <Edit2 size={14} />
    </button>
  )
})

const EditActions = memo(function EditActions({ onSave, onCancel, saving }) {
  return (
    <div className="flex gap-2">
      <button type="button" onClick={onSave} disabled={saving} className="ui-press px-3 py-1.5 bg-green-500/20 text-green-400 rounded text-xs hover:bg-green-500/30 flex items-center gap-1.5 disabled:opacity-60">
        {saving && <Loader2 size={12} className="animate-spin" />}
        Save
      </button>
      <button type="button" onClick={onCancel} disabled={saving} className="ui-press px-3 py-1.5 bg-gray-500/20 text-gray-400 rounded text-xs hover:bg-gray-500/30 disabled:opacity-60">Cancel</button>
    </div>
  )
})

const MetadataField = memo(function MetadataField({
  label,
  value,
  onSave,
  multiline = false,
  rows = 3,
  placeholder = '',
  emptyText = 'Unknown',
  renderValue,
  maxLength,
  startEditing = false,
  onCancel
}) {
  const { getWhite, getGrey400, getBorder } = useDynamicTheme()
  const [editing, setEditing] = useState(startEditing)
  const [draft, setDraft] = useState(startEditing ? (value || '') : '')
  const [saving, setSaving] = useState(false)

  const startEdit = () => {
    setDraft(value || '')
    setEditing(true)
  }

  const cancel = () => {
    if (saving) return
    setEditing(false)
    onCancel?.()
  }

  const save = async () => {
    if (saving) return
    if (draft === (value || '')) {
      cancel()
      return
    }
    setSaving(true)
    const ok = await onSave(draft)
    setSaving(false)
    if (ok) setEditing(false)
  }

  if (editing) {
    return (
      <div className="space-y-2">
        <FieldLabel>{label}</FieldLabel>
        {multiline ? (
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={placeholder}
            maxLength={maxLength}
            className="w-full px-3 py-2 bg-white/5 border rounded-lg text-sm focus:outline-none focus:border-purple-500 resize-y"
            style={{ borderColor: getBorder(0.3), color: getWhite() }}
            rows={rows}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === 'Escape') cancel()
            }}
          />
        ) : (
          <input
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={placeholder}
            maxLength={maxLength}
            className="w-full px-3 py-2 bg-white/5 border rounded-lg text-sm focus:outline-none focus:border-purple-500"
            style={{ borderColor: getBorder(0.3), color: getWhite() }}
            autoFocus
            enterKeyHint="done"
            onKeyDown={(e) => {
              if (e.key === 'Enter') void save()
              if (e.key === 'Escape') cancel()
            }}
          />
        )}
        <EditActions onSave={save} onCancel={cancel} saving={saving} />
      </div>
    )
  }

  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <FieldLabel>{label}</FieldLabel>
        {onSave && <EditButton label={label} onClick={startEdit} />}
      </div>
      <div className="mt-1">
        {value
          ? (renderValue ? renderValue(value) : <p className="text-sm break-words" style={{ color: getWhite() }}>{value}</p>)
          : <p className="text-sm" style={{ color: getGrey400() }}>{emptyText}</p>}
      </div>
    </div>
  )
})

const TagEditor = memo(function TagEditor({ label, tags, color, onSave, placeholder = 'Type and press Enter' }) {
  const { getWhite, getBorder } = useDynamicTheme()
  const [editing, setEditing] = useState(false)
  const [draftTags, setDraftTags] = useState([])
  const [input, setInput] = useState('')
  const [saving, setSaving] = useState(false)
  const current = useMemo(() => tags || [], [tags])

  const startEdit = () => {
    setDraftTags(current)
    setInput('')
    setEditing(true)
  }

  const cancel = () => {
    if (!saving) setEditing(false)
  }

  const addInput = () => {
    const extra = splitTags(input)
    if (extra.length) setDraftTags(prev => mergeTags(prev, extra))
    setInput('')
  }

  const save = async () => {
    if (saving) return
    const next = mergeTags(draftTags, splitTags(input))
    if (sameTags(next, current)) {
      setEditing(false)
      return
    }
    setSaving(true)
    const ok = await onSave(next)
    setSaving(false)
    if (ok) setEditing(false)
  }

  if (editing) {
    return (
      <div className="space-y-2">
        <FieldLabel>{label}</FieldLabel>
        {draftTags.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {draftTags.map((tag, i) => (
              <span
                key={`${tag}-${i}`}
                className="pl-2 pr-1 py-0.5 rounded-full text-xs flex items-center gap-1"
                style={{ backgroundColor: `${color}20`, color }}
              >
                {tag}
                <button
                  type="button"
                  onClick={() => setDraftTags(prev => prev.filter((_, j) => j !== i))}
                  aria-label={`Remove ${tag}`}
                  className="ui-tap p-0.5 rounded-full hover:bg-white/10"
                >
                  <X size={12} />
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={placeholder}
            className="flex-1 min-w-0 px-3 py-2 bg-white/5 border rounded-lg text-sm focus:outline-none focus:border-purple-500"
            style={{ borderColor: getBorder(0.3), color: getWhite() }}
            autoFocus
            enterKeyHint="enter"
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ',') {
                e.preventDefault()
                addInput()
              } else if (e.key === 'Backspace' && !input && draftTags.length) {
                setDraftTags(prev => prev.slice(0, -1))
              } else if (e.key === 'Escape') {
                cancel()
              }
            }}
          />
          <button
            type="button"
            onClick={addInput}
            disabled={!input.trim()}
            aria-label={`Add to ${label}`}
            className="ui-tap px-2.5 rounded-lg bg-white/10 hover:bg-white/15 disabled:opacity-40 transition"
            style={{ color: getWhite() }}
          >
            <Plus size={16} />
          </button>
        </div>
        <EditActions onSave={save} onCancel={cancel} saving={saving} />
      </div>
    )
  }

  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <FieldLabel>{label}</FieldLabel>
        <EditButton label={label} onClick={startEdit} />
      </div>
      <div className="mt-1">
        <ModalTagList tags={current} color={color} />
      </div>
    </div>
  )
})

const VisibilityControl = memo(function VisibilityControl({ value, onChange }) {
  const { getGrey400 } = useDynamicTheme()
  const current = VISIBILITY_OPTIONS.find(opt => opt.value === value) || VISIBILITY_OPTIONS[0]

  return (
    <div>
      <div role="radiogroup" aria-label="Visibility" className="grid grid-cols-3 gap-1 p-1 rounded-lg bg-white/5">
        {VISIBILITY_OPTIONS.map(({ value: optValue, label, icon: Icon }) => {
          const active = optValue === current.value
          return (
            <button
              key={optValue}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => { if (!active) onChange(optValue) }}
              className={`ui-press flex items-center justify-center gap-1.5 py-2 rounded-md text-xs font-medium transition ${active ? 'bg-purple-500 text-white' : 'text-gray-400 hover:bg-white/10'}`}
            >
              <Icon size={14} />
              {label}
            </button>
          )
        })}
      </div>
      <p className="mt-1.5 text-xs" style={{ color: getGrey400() }}>{current.hint}</p>
    </div>
  )
})

const DescriptionField = memo(function DescriptionField({ value, onSave }) {
  const [open, setOpen] = useState(false)

  if (!value && !open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="ui-press text-xs text-purple-400 hover:text-purple-300"
      >
        + Add description
      </button>
    )
  }

  return (
    <MetadataField
      label="Description"
      value={value}
      onSave={onSave}
      multiline
      rows={3}
      maxLength={DESCRIPTION_MAX}
      placeholder="A line or two about this track"
      emptyText="No description"
      startEditing={!value}
      onCancel={() => setOpen(false)}
    />
  )
})

const SettingToggle = memo(function SettingToggle({ label, on, onToggle }) {
  const { getWhite } = useDynamicTheme()
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs whitespace-nowrap" style={{ color: getWhite() }}>{label}</span>
      <ToggleChip on={on} onClick={onToggle} label={label} />
    </div>
  )
})

const ADD_ARTIST = '__add_artist__'

const ArtistChooser = memo(function ArtistChooser({ artists, value, onChange, onCreate, fallbackLabel, disabled = false }) {
  const { getWhite, getBorder } = useDynamicTheme()
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  const selected = artists.find(a => a.id === value)

  const cancelAdd = () => {
    if (creating) return
    setAdding(false)
    setName('')
  }

  const create = async () => {
    const trimmed = cleanText(name)
    if (!trimmed || creating) return
    setCreating(true)
    const artist = await onCreate(trimmed)
    setCreating(false)
    if (!artist) return
    setAdding(false)
    setName('')
    onChange(artist.id, artist)
  }

  if (adding) {
    return (
      <div className="flex items-center gap-1.5 flex-1 min-w-0">
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Band or artist name"
          maxLength={80}
          className="flex-1 min-w-0 px-2.5 py-1.5 bg-white/5 border rounded-lg text-sm focus:outline-none focus:border-purple-500"
          style={{ borderColor: getBorder(0.3), color: getWhite() }}
          autoFocus
          enterKeyHint="done"
          onKeyDown={(e) => {
            if (e.key === 'Enter') void create()
            if (e.key === 'Escape') cancelAdd()
          }}
        />
        <button
          type="button"
          onClick={create}
          disabled={creating || !name.trim()}
          className="ui-press px-2.5 py-1.5 rounded-lg text-xs font-medium bg-green-500/20 text-green-400 hover:bg-green-500/30 disabled:opacity-50 flex items-center gap-1"
        >
          {creating ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
          Add
        </button>
        <button type="button" onClick={cancelAdd} aria-label="Cancel" className="ui-tap p-1.5 rounded-lg text-gray-400 hover:bg-white/10">
          <X size={14} />
        </button>
      </div>
    )
  }

  if (artists.length === 0) {
    return (
      <div className="flex items-center gap-2 flex-1 min-w-0">
        <span className="text-sm truncate" style={{ color: getWhite() }}>{fallbackLabel}</span>
        <button
          type="button"
          onClick={() => setAdding(true)}
          disabled={disabled}
          className="ui-press text-xs text-purple-400 hover:text-purple-300 whitespace-nowrap disabled:opacity-50"
        >
          + Add band/artist
        </button>
      </div>
    )
  }

  return (
    <select
      value={selected ? String(selected.id) : ''}
      disabled={disabled}
      aria-label="Artist"
      onChange={(e) => {
        if (e.target.value === ADD_ARTIST) setAdding(true)
        else if (e.target.value) {
          const id = Number(e.target.value)
          onChange(id, artists.find(a => a.id === id))
        }
      }}
      className="flex-1 min-w-0 px-2.5 py-1.5 bg-dark-hover border rounded-lg text-sm focus:outline-none focus:border-purple-500 transition disabled:opacity-60"
      style={{ borderColor: getBorder(0.3), color: getWhite() }}
    >
      {!selected && <option value="">{fallbackLabel}</option>}
      {artists.map(artist => (
        <option key={artist.id} value={String(artist.id)}>{artist.name}</option>
      ))}
      <option value={ADD_ARTIST}>+ Add band/artist</option>
    </select>
  )
})

const QualityBadge = memo(function QualityBadge({ tier, sampleRate, bitDepth, isLossless }) {
  const tierColors = {
    studio: { bg: '#10b98120', color: '#10b981', label: 'Studio Quality' },
    high: { bg: '#3b82f620', color: '#3b82f6', label: 'High Quality' },
    medium: { bg: '#f59e0b20', color: '#f59e0b', label: 'Medium Quality' },
    low: { bg: '#ef444420', color: '#ef4444', label: 'Low Quality' },
    unknown: { bg: '#6b728020', color: '#6b7280', label: 'Unknown' }
  }

  const { bg, color, label } = tierColors[tier] || tierColors.unknown

  return (
    <div className="flex items-center gap-2">
      <span
        className="px-2 py-1 rounded-lg text-xs font-semibold flex items-center gap-1"
        style={{ backgroundColor: bg, color }}
      >
        <Gauge size={12} />
        {label}
      </span>
      {sampleRate && (
        <span className="text-xs text-gray-400">
          {(sampleRate / 1000).toFixed(1)}kHz {bitDepth && `• ${bitDepth}`} {isLossless && '• Lossless'}
        </span>
      )}
    </div>
  )
})

const ArtworkSection = memo(function ArtworkSection({ trackId, hasArtwork, artworkGenerated, onArtworkUploaded }) {
  const { getWhite, getGrey400, getBorder } = useDynamicTheme()
  const { toastError } = useUISelector(state => ({ toastError: state.toastError }))
  const [uploading, setUploading] = useState(false)
  const [artworkUrl, setArtworkUrl] = useState(hasArtwork ? `/api/artwork/${trackId}?t=${Date.now()}` : null)
  const fileInputRef = useRef(null)

  const handleFileSelect = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return

    const validTypes = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']
    if (!validTypes.includes(file.type)) {
      toastError('Please select a valid image file (JPEG, PNG, WebP, or GIF)')
      if (fileInputRef.current) fileInputRef.current.value = ''
      return
    }

    if (file.size > 10 * 1024 * 1024) {
      toastError('Image too large. Maximum size is 10MB')
      if (fileInputRef.current) fileInputRef.current.value = ''
      return
    }

    setUploading(true)
    triggerHaptic('light')

    try {
      await api.uploadTrackArtwork(trackId, file)
      setArtworkUrl(`/api/artwork/${trackId}?t=${Date.now()}`)
      onArtworkUploaded?.(true)
      triggerHaptic('success')
    } catch (err) {
      toastError(err.message || 'Failed to upload artwork')
      triggerHaptic('error')
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const getArtworkLabel = () => {
    if (!artworkUrl) return 'No Artwork'
    if (artworkGenerated) return 'Generated Artwork'
    return 'Cover Artwork'
  }

  return (
    <div className="flex items-start gap-4">
      <div
        className="relative w-24 h-24 rounded-lg overflow-hidden flex-shrink-0 border"
        style={{ borderColor: getBorder(0.3), backgroundColor: 'rgba(0,0,0,0.3)' }}
      >
        {artworkUrl ? (
          <img decoding="async"
            src={artworkUrl}
            alt="Track artwork"
            className="w-full h-full object-cover"
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Image size={32} className="text-gray-500" />
          </div>
        )}
        {uploading && (
          <div className="absolute inset-0 bg-black/60 flex items-center justify-center">
            <Loader2 size={24} className="animate-spin text-white" />
          </div>
        )}
      </div>

      <div className="flex-1">
        <p className="text-sm font-medium mb-1 flex items-center gap-2" style={{ color: getWhite() }}>
          {getArtworkLabel()}
          {artworkGenerated && <Sparkles size={12} className="text-purple-400" />}
        </p>
        <p className="text-xs mb-3" style={{ color: getGrey400() }}>
          {artworkUrl
            ? 'Upload your own image to replace'
            : 'No artwork available'}
        </p>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/jpeg,image/png,image/webp,image/gif"
          onChange={handleFileSelect}
          style={{ display: 'none' }}
        />
        <button
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading}
          className="ui-press px-3 py-1.5 rounded-lg text-xs font-medium transition flex items-center gap-1.5"
          style={{
            backgroundColor: 'rgba(255,255,255,0.1)',
            color: getWhite(),
            border: `1px solid ${getBorder(0.3)}`,
            opacity: uploading ? 0.5 : 1
          }}
        >
          <Upload size={14} />
          {uploading ? 'Uploading...' : artworkUrl ? 'Replace Artwork' : 'Upload Artwork'}
        </button>
      </div>
    </div>
  )
})

const AudioFeaturesDisplay = memo(function AudioFeaturesDisplay({ features }) {
  const { getWhite, getGrey400 } = useDynamicTheme()

  if (!features) return null

  const formatKey = (key, mode) => {
    if (!key) return null
    const keyNames = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    const keyName = keyNames[key % 12] || key
    const modeName = mode === 1 ? 'Major' : mode === 0 ? 'Minor' : ''
    return `${keyName} ${modeName}`.trim()
  }

  return (
    <div className="grid grid-cols-3 gap-2">
      {features.tempo && (
        <div className="text-center p-2 rounded-lg bg-white/5">
          <div className="text-lg font-bold" style={{ color: getWhite() }}>
            {Math.round(features.tempo)}
          </div>
          <div className="text-xs" style={{ color: getGrey400() }}>BPM</div>
        </div>
      )}
      {features.key !== undefined && (
        <div className="text-center p-2 rounded-lg bg-white/5">
          <div className="text-lg font-bold" style={{ color: getWhite() }}>
            {formatKey(features.key, features.mode) || '—'}
          </div>
          <div className="text-xs" style={{ color: getGrey400() }}>Key</div>
        </div>
      )}
      {features.energy !== undefined && (
        <div className="text-center p-2 rounded-lg bg-white/5">
          <div className="text-lg font-bold" style={{ color: getWhite() }}>
            {Math.round(features.energy * 100)}%
          </div>
          <div className="text-xs" style={{ color: getGrey400() }}>Energy</div>
        </div>
      )}
    </div>
  )
})

export const UploadMusicModal = memo(function UploadMusicModal({ isOpen, onClose, onUploadComplete, onLogin, editTrackId = null }) {
  const { getCategoryMetadata, getWhite, getGrey400 } = useDynamicTheme()
  const { isAuthenticated, user } = useAuth()
  const { toastError, setUploadWatchId } = useUISelector(state => ({
    toastError: state.toastError,
    setUploadWatchId: state.setUploadWatchId,
  }))
  const { showConfirm } = useDialog()
  const categoryColor = getCategoryMetadata('all')?.color || '#6366f1'

  const [stage, setStage] = useState(UploadStage.SELECT)
  const [file, setFile] = useState(null)
  const [progress, setProgress] = useState(0)
  const [stageText, setStageText] = useState('')
  const [error, setError] = useState(null)
  const [metadata, setMetadata] = useState(null)
  const [trackId, setTrackId] = useState(null)
  const [sendProgress, setSendProgress] = useState(0)
  const [jobName, setJobName] = useState('')
  const [duplicate, setDuplicate] = useState(false)
  const [cancelling, setCancelling] = useState(false)

  const [artists, setArtists] = useState([])
  const [selectedArtistId, setSelectedArtistId] = useState(null)
  const [enhance, setEnhance] = useState(null)
  const [savingArtist, setSavingArtist] = useState(false)
  const [rightsConfirmed, setRightsConfirmed] = useState(null)
  const [rightsTicked, setRightsTicked] = useState(false)

  const isEditing = !!editTrackId
  const needsRights = rightsConfirmed === false && !rightsTicked

  const fileInputRef = useRef(null)
  const dragCounterRef = useRef(0)
  const uploadIdRef = useRef(null)
  const uploadAbortRef = useRef(null)
  const settlingRef = useRef(null)
  const lastEventAtRef = useRef(0)
  const [isDragging, setIsDragging] = useState(false)

  const ownName = user?.username || 'You'

  const applySetup = useCallback((data) => {
    const list = data?.artists || []
    setArtists(list)
    setSelectedArtistId(pickDefaultArtist(list, data?.last_artist_profile_id))
    if (!data?.offline) {
      setEnhance(!!data?.upload_enhance)
      setRightsConfirmed(!!data?.rights_confirmed)
    }
  }, [])

  const loadSetup = useCallback(() => {
    api.getUploadSetup()
      .then(applySetup)
      .catch(err => logger.warn('[Upload] Could not load upload setup:', err))
  }, [applySetup])

  const watchUpload = useCallback((uploadId) => {
    uploadIdRef.current = uploadId
    settlingRef.current = null
    lastEventAtRef.current = Date.now()
    setUploadWatchId(uploadId)
  }, [setUploadWatchId])

  const clearWatch = useCallback(() => {
    uploadIdRef.current = null
    settlingRef.current = null
    setUploadWatchId(null)
  }, [setUploadWatchId])

  const applyFinishedJob = useCallback((uploadId, job) => {
    if (uploadIdRef.current !== uploadId) return
    if (!isUploadFinished(job)) {
      settlingRef.current = null
      return
    }
    uploadIdRef.current = null
    setCancelling(false)
    if (job.status === 'cancelled') {
      clearWatch()
      setProgress(0)
      setSendProgress(0)
      setStageText('')
      setJobName('')
      setStage(UploadStage.SELECT)
      return
    }
    if (job.status === 'done' && job.result) {
      setRightsConfirmed(true)
      setProgress(100)
      setMetadata(job.result.metadata)
      setTrackId(job.result.track_id)
      setDuplicate(!!job.result.duplicate)
      setStage(UploadStage.PREVIEW)
      loadSetup()
      triggerHaptic('success')
      return
    }
    triggerHaptic('error')
    setError(job.error || 'Upload failed. Please try again.')
    setStage(UploadStage.ERROR)
  }, [clearWatch, loadSetup])

  const settleUpload = useCallback((uploadId, knownJob = null) => {
    if (uploadIdRef.current !== uploadId || settlingRef.current === uploadId) return
    settlingRef.current = uploadId
    const load = knownJob
      ? Promise.resolve(knownJob)
      : fetchFinishedUploadJob(uploadId, { shouldStop: () => uploadIdRef.current !== uploadId })
    load
      .then(job => applyFinishedJob(uploadId, job))
      .catch(err => {
        logger.warn('[Upload] Could not load the finished upload:', err)
        if (uploadIdRef.current !== uploadId) return
        if (err?.status === 404) applyFinishedJob(uploadId, { status: 'failed', error: LOST_UPLOAD_MESSAGE })
        else settlingRef.current = null
      })
  }, [applyFinishedJob])

  useWebSocketSubscribe('upload_progress', useCallback((data) => {
    const uploadId = uploadIdRef.current
    if (!uploadId || data?.upload_id !== uploadId) return
    lastEventAtRef.current = Date.now()
    if (data.stage && data.percent !== undefined) {
      setProgress(prev => Math.max(prev, data.percent))
      setStageText(data.stage)
    }
    if (UPLOAD_FINISHED_STATUSES.has(data.status)) {
      settleUpload(uploadId, data.status === 'done' ? null : { status: data.status, error: data.message })
    }
  }, [settleUpload]))

  useEffect(() => {
    if (!isOpen || stage !== UploadStage.ANALYZING) return
    const timer = setInterval(() => {
      const uploadId = uploadIdRef.current
      if (!uploadId || settlingRef.current === uploadId) return
      if (Date.now() - lastEventAtRef.current < UPLOAD_EVENT_QUIET_MS) return
      api.getUploadJob(uploadId)
        .then(job => {
          if (uploadIdRef.current !== uploadId) return
          if (isUploadFinished(job)) {
            settleUpload(uploadId, job)
            return
          }
          setProgress(prev => Math.max(prev, job?.percent || 0))
          if (job?.stage) setStageText(job.stage)
        })
        .catch(err => {
          if (err?.status === 404 && uploadIdRef.current === uploadId) {
            applyFinishedJob(uploadId, { status: 'failed', error: LOST_UPLOAD_MESSAGE })
            return
          }
          logger.warn('[Upload] Could not check the upload:', err)
        })
    }, UPLOAD_POLL_MS)
    return () => clearInterval(timer)
  }, [isOpen, stage, settleUpload, applyFinishedJob])

  useEffect(() => {
    if (!isOpen || !isAuthenticated || editTrackId) return
    let cancelled = false
    api.listUploadJobs()
      .then(data => {
        if (cancelled || uploadIdRef.current) return
        const running = (data?.uploads || []).find(job => job.status === 'running')
        if (!running?.upload_id) return
        watchUpload(running.upload_id)
        setJobName(running.filename || '')
        setProgress(running.percent || 0)
        setStageText(running.stage || '')
        setStage(UploadStage.ANALYZING)
      })
      .catch(err => logger.warn('[Upload] Could not check for running uploads:', err))
    return () => { cancelled = true }
  }, [isOpen, isAuthenticated, editTrackId, watchUpload])

  useEffect(() => {
    if (!isOpen || !isAuthenticated) return
    let cancelled = false
    api.getUploadSetup()
      .then(data => { if (!cancelled) applySetup(data) })
      .catch(err => logger.warn('[Upload] Could not load upload setup:', err))
    return () => { cancelled = true }
  }, [isOpen, isAuthenticated, applySetup])

  useEffect(() => {
    if (!isOpen || !isAuthenticated || !editTrackId) return
    let cancelled = false
    api.getTrack(editTrackId)
      .then(track => {
        if (cancelled) return
        setTrackId(editTrackId)
        setMetadata(trackToMetadata(track))
        setStage(UploadStage.PREVIEW)
      })
      .catch(err => {
        if (cancelled) return
        logger.warn('[Upload] Could not load the track to edit:', err)
        setError(err.message || 'Could not load the track')
        setStage(UploadStage.ERROR)
      })
    return () => { cancelled = true }
  }, [isOpen, isAuthenticated, editTrackId])

  const resetState = useCallback(() => {
    uploadAbortRef.current?.abort()
    uploadAbortRef.current = null
    clearWatch()
    setStage(UploadStage.SELECT)
    setFile(null)
    setProgress(0)
    setSendProgress(0)
    setStageText('')
    setJobName('')
    setDuplicate(false)
    setCancelling(false)
    setError(null)
    setMetadata(null)
    setTrackId(null)
    setSavingArtist(false)
  }, [clearWatch])

  const handleClose = useCallback(async () => {
    if (uploadAbortRef.current) {
      const stop = await showConfirm({
        title: 'Stop uploading?',
        message: "Your file hasn't finished uploading. Closing now stops the upload.",
        confirmText: 'Stop upload',
        cancelText: 'Keep uploading',
        variant: 'danger'
      })
      if (!stop) return
      if (uploadAbortRef.current) {
        uploadAbortRef.current.abort()
      } else if (uploadIdRef.current) {
        api.cancelUploadJob(uploadIdRef.current).catch(err => logger.warn('[Upload] Could not cancel the upload:', err))
      }
    }
    resetState()
    onClose()
  }, [onClose, resetState, showConfirm])

  const handleLoginRequired = useCallback(() => {
    void handleClose()
    onLogin?.()
  }, [handleClose, onLogin])

  const validateFile = (file) => {
    const ext = file.name.split('.').pop()?.toLowerCase()
    if (!SUPPORTED_FORMATS.includes(ext)) {
      return `Unsupported format. Supported: ${SUPPORTED_FORMATS.join(', ')}`
    }
    const isVideo = VIDEO_FORMATS.includes(ext) || file.type?.startsWith('video/')
    const maxFileSize = isVideo ? MAX_VIDEO_FILE_SIZE : MAX_AUDIO_FILE_SIZE
    if (file.size > maxFileSize) {
      const maxLabel = isVideo ? '10GB' : `${maxFileSize / (1024 * 1024)}MB`
      return `File too large. Maximum size: ${maxLabel}`
    }
    return null
  }

  const isSelectedVideo = file && (
    VIDEO_FORMATS.includes(file.name.split('.').pop()?.toLowerCase()) ||
    file.type?.startsWith('video/')
  )

  const handleFileSelect = (selectedFile) => {
    const validationError = validateFile(selectedFile)
    if (validationError) {
      setError(validationError)
      setStage(UploadStage.ERROR)
      return
    }
    setFile(selectedFile)
    setError(null)
    triggerHaptic('light')
  }

  const handleDragEnter = (e) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounterRef.current++
    if (e.dataTransfer.items && e.dataTransfer.items.length > 0) {
      setIsDragging(true)
    }
  }

  const handleDragLeave = (e) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounterRef.current--
    if (dragCounterRef.current === 0) {
      setIsDragging(false)
    }
  }

  const handleDragOver = (e) => {
    e.preventDefault()
    e.stopPropagation()
  }

  const handleDrop = (e) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(false)
    dragCounterRef.current = 0

    const files = e.dataTransfer.files
    if (files && files.length > 0) {
      handleFileSelect(files[0])
    }
  }

  const handleUpload = async () => {
    if (!file || needsRights || uploadIdRef.current) return

    const uploadId = newUploadId()
    const controller = new AbortController()
    uploadAbortRef.current = controller
    watchUpload(uploadId)
    setJobName(file.name)
    setSendProgress(0)
    setProgress(0)
    setStageText('')
    setDuplicate(false)
    setCancelling(false)
    setStage(UploadStage.UPLOADING)

    try {
      const response = await api.uploadMusic(file, uploadId, {
        artistProfileId: selectedArtistId,
        enableUpscaling: enhance,
        rightsConfirmed: rightsTicked,
        onProgress: (fraction) => setSendProgress(Math.round(fraction * 100)),
        signal: controller.signal
      })
      if (uploadAbortRef.current === controller) uploadAbortRef.current = null
      if (uploadIdRef.current !== uploadId) return
      setRightsConfirmed(true)
      setSendProgress(100)
      if (response?.upload_id && response.upload_id !== uploadId) watchUpload(response.upload_id)
      else lastEventAtRef.current = Date.now()
      setStage(prev => prev === UploadStage.UPLOADING ? UploadStage.ANALYZING : prev)
    } catch (err) {
      if (uploadAbortRef.current === controller) uploadAbortRef.current = null
      if (uploadIdRef.current !== uploadId) return
      clearWatch()
      setSendProgress(0)
      if (err?.name === 'AbortError') {
        setStage(UploadStage.SELECT)
        return
      }
      triggerHaptic('error')
      if (isRightsError(err)) {
        setRightsConfirmed(false)
        setRightsTicked(false)
        setProgress(0)
        setStage(UploadStage.SELECT)
        toastError(err.message)
        return
      }
      setError(err.message || 'Upload failed. Please try again.')
      setStage(UploadStage.ERROR)
    }
  }

  const handleCancelSend = () => {
    uploadAbortRef.current?.abort()
  }

  const handleCancelProcessing = async () => {
    const uploadId = uploadIdRef.current
    if (!uploadId || cancelling) return
    setCancelling(true)
    try {
      const response = await api.cancelUploadJob(uploadId)
      if (uploadIdRef.current !== uploadId) return
      if (response?.status === 'cancelled') settleUpload(uploadId, { status: 'cancelled' })
      else if (response?.status === 'cancelling') lastEventAtRef.current = 0
      else settleUpload(uploadId)
    } catch (err) {
      if (uploadIdRef.current !== uploadId) return
      setCancelling(false)
      toastError(err.message || 'Could not cancel the upload')
    }
  }

  const handleSaveAndClose = () => {
    triggerHaptic('success')
    onUploadComplete?.(trackId, metadata)
    void handleClose()
  }

  const handleCreateArtist = useCallback(async (name) => {
    try {
      const artist = await api.createArtist({ name })
      setArtists(prev => [...prev.filter(a => a.id !== artist.id), { ...artist, track_count: artist.track_count ?? 0 }])
      triggerHaptic('light')
      return artist
    } catch (err) {
      toastError(err.message || 'Could not add the artist')
      triggerHaptic('error')
      return null
    }
  }, [toastError])

  const saveTrack = useCallback(async (updates) => {
    if (!trackId) return false
    try {
      await api.updateUserTrack(trackId, updates)
      triggerHaptic('light')
      return true
    } catch (err) {
      toastError(err.message || 'Could not save the change')
      triggerHaptic('error')
      return false
    }
  }, [trackId, toastError])

  const saveTitle = useCallback(async (value) => {
    const title = cleanText(value)
    if (!await saveTrack({ title })) return false
    setMetadata(prev => ({ ...prev, title }))
    return true
  }, [saveTrack])

  const saveGenre = useCallback(async (value) => {
    const primaryGenre = cleanText(value)
    if (!await saveTrack({ primary_genre: primaryGenre })) return false
    setMetadata(prev => ({ ...prev, primary_genre: primaryGenre }))
    return true
  }, [saveTrack])

  const saveSecondaryGenres = useCallback(async (list) => {
    if (!await saveTrack({ secondary_genres: list })) return false
    setMetadata(prev => ({ ...prev, secondary_genres: list }))
    return true
  }, [saveTrack])

  const saveMoods = useCallback(async (list) => {
    if (!await saveTrack({ mood_keywords: list })) return false
    setMetadata(prev => ({ ...prev, mood_keywords: list }))
    return true
  }, [saveTrack])

  const saveLyrics = useCallback(async (value) => {
    const lyrics = String(value || '').trim()
    if (!await saveTrack({ lyrics })) return false
    setMetadata(prev => ({ ...prev, transcribed_lyrics: lyrics || null, has_lyrics: !!lyrics }))
    return true
  }, [saveTrack])

  const saveSetting = useCallback(async (key, value) => {
    const previous = metadata?.[key]
    setMetadata(prev => ({ ...prev, [key]: value }))
    if (!await saveTrack({ [key]: value })) setMetadata(prev => ({ ...prev, [key]: previous }))
  }, [metadata, saveTrack])

  const saveDescription = useCallback(async (value) => {
    const description = String(value || '').trim()
    if (!await saveTrack({ description })) return false
    setMetadata(prev => ({ ...prev, description }))
    return true
  }, [saveTrack])

  const handleTrackArtistChange = useCallback(async (artistId, artist) => {
    if (artistId === metadata?.artist_profile_id || savingArtist) return
    setSavingArtist(true)
    const ok = await saveTrack({ artist_profile_id: artistId })
    setSavingArtist(false)
    if (!ok) return
    setMetadata(current => ({ ...current, artist_profile_id: artistId, artist: artist?.name ?? current.artist }))
    setSelectedArtistId(artistId)
  }, [metadata?.artist_profile_id, savingArtist, saveTrack])

  const handleSelectArtist = useCallback((artistId) => setSelectedArtistId(artistId), [])

  const handleClearFile = () => {
    setFile(null)
    triggerHaptic('light')
  }

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      title={isEditing ? 'Edit Track' : 'Upload Music'}
      maxWidth="max-w-lg"
      categoryOverride="all"
    >
      <AnimatePresence mode="wait">
        {!isAuthenticated && (
          <motion.div
            key="auth-required"
            {...PRESETS.stepSwap}
            className="py-8"
          >
            <ModalErrorState
              title="Login Required"
              message="Sign in before uploading music or video."
              onRetry={handleLoginRequired}
              retryText="Login"
            />
          </motion.div>
        )}

        {isAuthenticated && isEditing && stage === UploadStage.SELECT && (
          <motion.div
            key="edit-loading"
            {...PRESETS.stepSwap}
            className="py-12 flex items-center justify-center"
          >
            <Loader2 size={28} className="animate-spin" style={{ color: getGrey400() }} />
          </motion.div>
        )}

        {isAuthenticated && !isEditing && stage === UploadStage.SELECT && (
          <motion.div
            key="select"
            {...PRESETS.stepSwap}
          >
            <ModalSection title="Select Audio or Video File">
              <div
                onPointerDown={(e) => e.stopPropagation()}
                onPointerUp={(e) => {
                  e.stopPropagation()
                  if (!file) fileInputRef.current?.click()
                }}
                onClick={(e) => e.stopPropagation()}
                onDragEnter={handleDragEnter}
                onDragLeave={handleDragLeave}
                onDragOver={handleDragOver}
                onDrop={handleDrop}
                className={`relative block border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition ${
                  isDragging
                    ? 'border-purple-500 bg-purple-500/10'
                    : file
                      ? 'border-green-500 bg-green-500/10'
                      : 'border-gray-600 hover:border-purple-500 hover:bg-white/5'
                }`}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={FILE_ACCEPT}
                  onChange={(e) => {
                    e.stopPropagation()
                    if (e.target.files?.[0]) handleFileSelect(e.target.files[0])
                  }}
                  className="sr-only"
                />

                {file ? (
                  <div className="flex items-center justify-center gap-3">
                    <div className="p-2 rounded-lg bg-green-500/20">
                      {isSelectedVideo ? (
                        <FileVideo size={24} className="text-green-400" />
                      ) : (
                        <FileAudio size={24} className="text-green-400" />
                      )}
                    </div>
                    <div className="text-left flex-1 min-w-0">
                      <p className="font-medium truncate" style={{ color: getWhite() }}>{file.name}</p>
                      <p className="text-sm" style={{ color: getGrey400() }}>{(file.size / (1024 * 1024)).toFixed(2)} MB</p>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); handleClearFile() }}
                      className="ui-press p-2 rounded-lg hover:bg-red-500/20 transition"
                    >
                      <X size={18} className="text-gray-400 hover:text-red-400" />
                    </button>
                  </div>
                ) : (
                  <>
                    <div className="p-3 rounded-full bg-white/5 inline-block mb-3">
                      <Upload size={28} className={isDragging ? 'text-purple-400' : 'text-gray-400'} />
                    </div>
                    <p style={{ color: getWhite() }}>
                      {isDragging ? 'Drop your media file here' : 'Drag & drop or click to select'}
                    </p>
                    <p className="mt-2 text-xs" style={{ color: getGrey400() }}>
                      Audio preferred: {AUDIO_FORMATS.join(', ').toUpperCase()}
                    </p>
                    <p className="mt-1 text-xs" style={{ color: getGrey400() }}>
                      Video accepted: {VIDEO_FORMATS.join(', ').toUpperCase()} | Audio max 100MB | Video max 10GB
                    </p>
                  </>
                )}
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
                <div className="flex items-center gap-2 flex-1 min-w-[12rem]">
                  <Mic2 size={14} className="flex-shrink-0" style={{ color: getGrey400() }} />
                  <ArtistChooser
                    artists={artists}
                    value={selectedArtistId}
                    onChange={handleSelectArtist}
                    onCreate={handleCreateArtist}
                    fallbackLabel={ownName}
                  />
                </div>
                <div className="flex items-center gap-2" title={ENHANCE_HINT}>
                  <Wand2 size={14} className="flex-shrink-0" style={{ color: getGrey400() }} />
                  <span className="text-xs whitespace-nowrap" style={{ color: getWhite() }}>Enhance audio</span>
                  <ToggleChip on={!!enhance} onClick={() => setEnhance(prev => !prev)} label="Enhance audio" />
                </div>
              </div>
              <p className="mt-1.5 text-xs text-right" style={{ color: getGrey400() }}>{ENHANCE_HINT}</p>
              {rightsConfirmed === false && (
                <label className="mt-2 flex items-center gap-2 py-1 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={rightsTicked}
                    onChange={(e) => setRightsTicked(e.target.checked)}
                    className="w-4 h-4 flex-shrink-0 accent-purple-500 cursor-pointer"
                  />
                  <span className="text-xs" style={{ color: getWhite() }}>{RIGHTS_LABEL}</span>
                </label>
              )}
            </ModalSection>

            {file && (
              <ModalFooter>
                <ModalButton
                  onClick={handleUpload}
                  disabled={!file || needsRights}
                  variant="primary"
                >
                  <Upload size={16} className="mr-2" />
                  Upload
                </ModalButton>
              </ModalFooter>
            )}
          </motion.div>
        )}

        {isAuthenticated && (stage === UploadStage.UPLOADING || stage === UploadStage.ANALYZING) && (
          <motion.div
            key="uploading"
            {...PRESETS.stepSwap}
            className="py-8"
          >
            <ModalProgress
              progress={(stage === UploadStage.UPLOADING ? sendProgress : progress) / 100}
              statusText={stage === UploadStage.UPLOADING
                ? `Uploading ${sendProgress}%`
                : (cancelling ? 'Cancelling...' : stageText || 'Processing...')}
              showCancel={!cancelling}
              onCancel={stage === UploadStage.UPLOADING ? handleCancelSend : handleCancelProcessing}
            />
            {jobName && (
              <p className="mt-4 text-xs text-center truncate" style={{ color: getGrey400() }}>{jobName}</p>
            )}
            {stage === UploadStage.ANALYZING && !cancelling && (
              <p className="mt-2 text-xs text-center" style={{ color: getGrey400() }}>{ANALYZING_HINT}</p>
            )}
          </motion.div>
        )}

        {isAuthenticated && stage === UploadStage.PREVIEW && metadata && (
          <motion.div
            key="preview"
            {...PRESETS.stepSwap}
          >
            {!isEditing && duplicate && (
              <div className="mb-4 flex items-center gap-2 p-3 rounded-lg bg-white/5 border border-white/10">
                <Info size={20} className="flex-shrink-0" style={{ color: getGrey400() }} />
                <p className="text-sm" style={{ color: getWhite() }}>{DUPLICATE_NOTICE}</p>
              </div>
            )}
            {!isEditing && !duplicate && <ModalSuccessBanner message="Ready! Review and edit details if needed." className="mb-4" />}

            <ModalSection title="Sharing">
              <ModalCard>
                <VisibilityControl
                  value={metadata.visibility}
                  onChange={(value) => saveSetting('visibility', value)}
                />
                <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2">
                  <SettingToggle
                    label="Explicit"
                    on={!!metadata.explicit}
                    onToggle={() => saveSetting('explicit', !metadata.explicit)}
                  />
                  <SettingToggle
                    label="Made with AI help"
                    on={!!metadata.ai_assisted}
                    onToggle={() => saveSetting('ai_assisted', !metadata.ai_assisted)}
                  />
                </div>
                <div className="mt-3">
                  <DescriptionField value={metadata.description} onSave={saveDescription} />
                </div>
              </ModalCard>
            </ModalSection>

            {metadata.audio_extracted_from_video && (
              <ModalSection title="Source Media">
                <ModalCard>
                  <div className="flex items-center gap-2 text-sm mb-2" style={{ color: getWhite() }}>
                    <FileVideo size={16} />
                    <span>Audio extracted from video for radio playback.</span>
                  </div>
                  {metadata.artwork_generation_deferred && (
                    <p className="text-xs" style={{ color: getGrey400() }}>
                      Cover artwork can be added after upload.
                    </p>
                  )}
                </ModalCard>
              </ModalSection>
            )}

            {metadata.source_quality && (
              <ModalSection title="Source Quality">
                <ModalCard>
                  <QualityBadge
                    tier={metadata.source_quality.tier}
                    sampleRate={metadata.source_quality.sample_rate}
                    bitDepth={metadata.source_quality.bit_depth}
                    isLossless={metadata.source_quality.is_lossless}
                  />
                  {metadata.source_quality.processing_notes && (
                    <p className="text-xs mt-2" style={{ color: getGrey400() }}>
                      {metadata.source_quality.processing_notes}
                    </p>
                  )}
                  {(metadata.mastering_applied || metadata.enhancement_applied || metadata.sonic_master_applied) && (
                    <div className="flex flex-wrap gap-2 mt-2">
                      {metadata.mastering_applied && (
                        <span className="text-xs px-2 py-0.5 rounded bg-purple-500/20 text-purple-300">
                          Mastered
                        </span>
                      )}
                      {metadata.enhancement_applied && (
                        <span className="text-xs px-2 py-0.5 rounded bg-blue-500/20 text-blue-300">
                          Enhanced
                        </span>
                      )}
                      {metadata.sonic_master_applied && (
                        <span className="text-xs px-2 py-0.5 rounded bg-green-500/20 text-green-300">
                          Mix Improved
                        </span>
                      )}
                    </div>
                  )}
                </ModalCard>
              </ModalSection>
            )}

            {(metadata.mix_analysis || metadata.sonic_master_prompt) && (
              <ModalSection title="Mix Analysis">
                <ModalCard>
                  {metadata.mix_analysis && (
                    <p className="text-sm mb-3" style={{ color: getGrey400() }}>
                      {metadata.mix_analysis}
                    </p>
                  )}
                  {metadata.sonic_master_prompt ? (
                    <div className="mt-2">
                      <p className="text-xs font-medium mb-2" style={{ color: getWhite() }}>
                        {metadata.sonic_master_applied ? 'Applied Enhancement:' : 'Suggested Enhancement:'}
                      </p>
                      <div
                        className="text-sm px-3 py-2 rounded-lg"
                        style={{
                          backgroundColor: metadata.sonic_master_applied ? 'rgba(34, 197, 94, 0.15)' : 'rgba(251, 191, 36, 0.15)',
                          borderLeft: `3px solid ${metadata.sonic_master_applied ? '#22c55e' : '#fbbf24'}`,
                          color: getWhite()
                        }}
                      >
                        &ldquo;{metadata.sonic_master_prompt}&rdquo;
                        <span className="ml-2 text-xs opacity-60">
                          @ {metadata.sonic_master_blend_used || metadata.sonic_master_blend}% blend
                        </span>
                      </div>
                    </div>
                  ) : metadata.mix_analysis && !metadata.sonic_master_prompt && (
                    <p className="text-sm text-green-400 mt-2">
                      Professional quality mix - no enhancement needed
                    </p>
                  )}
                </ModalCard>
              </ModalSection>
            )}

            <ModalSection title="Cover Artwork">
              <ModalCard>
                <ArtworkSection
                  trackId={trackId}
                  hasArtwork={metadata.has_artwork}
                  artworkGenerated={metadata.artwork_generated}
                  onArtworkUploaded={(hasArt) => {
                    setMetadata(prev => ({ ...prev, has_artwork: hasArt, artwork_generated: false }))
                  }}
                />
                {metadata.artwork_prompt && (
                  <div className="mt-3">
                    <p className="text-xs font-medium mb-2" style={{ color: getGrey400() }}>
                      Artwork Description:
                    </p>
                    <div
                      className="text-xs px-3 py-2 rounded-lg"
                      style={{
                        backgroundColor: 'rgba(139, 92, 246, 0.15)',
                        borderLeft: '3px solid #8b5cf6',
                        color: getWhite()
                      }}
                    >
                      {metadata.artwork_prompt}
                    </div>
                  </div>
                )}
                {(metadata.video_search_terms?.length > 0 || metadata.derived_tags?.video_search_terms?.length > 0) && (
                  <div className="mt-3">
                    <p className="text-xs font-medium mb-2 flex items-center gap-2" style={{ color: getGrey400() }}>
                      <span>🎬</span>
                      <span>Video Search Terms</span>
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {(metadata.video_search_terms || metadata.derived_tags?.video_search_terms || []).map((term, i) => (
                        <span
                          key={i}
                          className="px-2 py-1 rounded text-xs"
                          style={{
                            backgroundColor: 'rgba(6, 182, 212, 0.2)',
                            color: '#67e8f9'
                          }}
                        >
                          {term}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </ModalCard>
            </ModalSection>

            {metadata.audio_features && (
              <ModalSection title="Audio Analysis">
                <ModalCard>
                  <AudioFeaturesDisplay features={metadata.audio_features} />
                </ModalCard>
              </ModalSection>
            )}

            <ModalSection title="Track Info">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <ModalCard>
                  <MetadataField
                    label="Title"
                    value={metadata.title}
                    onSave={saveTitle}
                  />
                </ModalCard>
                <ModalCard>
                  <div className="flex items-center justify-between gap-2">
                    <FieldLabel>Artist</FieldLabel>
                    {savingArtist && <Loader2 size={14} className="animate-spin" style={{ color: getGrey400() }} />}
                  </div>
                  <div className="mt-1 flex items-center">
                    <ArtistChooser
                      artists={artists}
                      value={metadata.artist_profile_id}
                      onChange={handleTrackArtistChange}
                      onCreate={handleCreateArtist}
                      fallbackLabel={metadata.artist || ownName}
                      disabled={savingArtist}
                    />
                  </div>
                </ModalCard>
              </div>
            </ModalSection>

            <ModalSection title="Genre & Classification">
              <div className="space-y-3">
                <ModalCard>
                  <MetadataField
                    label="Primary Genre"
                    value={metadata.primary_genre}
                    onSave={saveGenre}
                  />
                </ModalCard>
                <ModalCard>
                  <TagEditor
                    label="Secondary Genres"
                    tags={metadata.secondary_genres}
                    color={categoryColor}
                    onSave={saveSecondaryGenres}
                  />
                </ModalCard>
              </div>
            </ModalSection>

            <ModalSection title="Mood & Vibe">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <ModalCard>
                  <TagEditor
                    label="Mood"
                    tags={metadata.mood_keywords}
                    color="#10b981"
                    onSave={saveMoods}
                  />
                </ModalCard>
                <ModalCard>
                  <label className="text-xs uppercase tracking-wide" style={{ color: getGrey400() }}>Similar Artists</label>
                  <div className="mt-1">
                    <ModalTagList tags={metadata.similar_artists} color="#f59e0b" />
                  </div>
                </ModalCard>
              </div>
            </ModalSection>

            {metadata.style && (
              <ModalSection title="Production Style">
                <ModalCard>
                  <p className="text-sm leading-relaxed" style={{ color: getGrey400() }}>
                    {metadata.style}
                  </p>
                </ModalCard>
              </ModalSection>
            )}

            {metadata.vocal_style_keywords?.length > 0 && (
              <ModalSection title="Vocal Style">
                <ModalCard>
                  <ModalTagList tags={metadata.vocal_style_keywords} color="#ec4899" />
                </ModalCard>
              </ModalSection>
            )}

            <ModalSection title="Lyrics">
              {metadata.lyrical_interpretation && (
                <ModalCard className="mb-3">
                  <label className="text-xs uppercase tracking-wide" style={{ color: getGrey400() }}>
                    About the Lyrics
                  </label>
                  <p className="text-sm mt-1 leading-relaxed" style={{ color: getWhite() }}>
                    {metadata.lyrical_interpretation}
                  </p>
                </ModalCard>
              )}
              <ModalCard>
                <MetadataField
                  label={metadata.transcribed_lyrics ? 'Transcribed Lyrics' : 'Lyrics'}
                  value={metadata.transcribed_lyrics || ''}
                  onSave={saveLyrics}
                  multiline
                  rows={10}
                  placeholder="Paste or type the lyrics. Leave empty for an instrumental."
                  emptyText={metadata.has_lyrics ? 'Lyrics detected' : 'Instrumental (no lyrics)'}
                  renderValue={(lyrics) => (
                    <pre
                      className="max-h-48 overflow-y-auto text-sm whitespace-pre-wrap font-sans leading-relaxed"
                      style={{ color: getWhite(), opacity: 0.9 }}
                    >
                      {lyrics}
                    </pre>
                  )}
                />
              </ModalCard>
            </ModalSection>

            <ModalFooter>
              {isEditing ? (
                <ModalButton onClick={handleClose} variant="primary" className="ml-auto">
                  <Check size={16} className="mr-2" />
                  Done
                </ModalButton>
              ) : (
                <>
                  <ModalButton onClick={resetState} variant="secondary">
                    Upload Another
                  </ModalButton>
                  <ModalButton onClick={handleSaveAndClose} variant="primary">
                    <Check size={16} className="mr-2" />
                    Save to Library
                  </ModalButton>
                </>
              )}
            </ModalFooter>
          </motion.div>
        )}

        {isAuthenticated && stage === UploadStage.ERROR && (
          <motion.div
            key="error"
            {...PRESETS.stepSwap}
            className="py-8"
          >
            <ModalErrorState
              title={isEditing ? "Couldn't open the track" : 'Upload Failed'}
              message={error}
              onRetry={isEditing ? handleClose : resetState}
              retryText={isEditing ? 'Close' : 'Try Again'}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </Modal>
  )
})
