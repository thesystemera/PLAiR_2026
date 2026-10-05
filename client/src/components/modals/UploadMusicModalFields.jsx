import { useState, useRef, useMemo, memo } from 'react'
import { Upload, Loader2, Check, X, Edit2, Sparkles, Image, Gauge, Plus, Globe, EyeOff, Lock } from 'lucide-react'
import { api } from '../../lib/api'
import { triggerHaptic } from '../../lib/haptics'
import { useDynamicTheme } from '../../contexts/DynamicThemeContext'
import { useUISelector } from '../../contexts/UIStateContext'
import { ToggleChip } from '../SettingRow'
import { ModalTagList } from './Modal'
import { TrackArt } from '../DepthArt'
import { PACK_SIZES, packCache } from '../../lib/mediaCache'

const DESCRIPTION_MAX = 2000

const VISIBILITY_OPTIONS = [
  { value: 'public', label: 'Public', icon: Globe, hint: 'Plays on the station and shows everywhere.' },
  { value: 'unlisted', label: 'Unlisted', icon: EyeOff, hint: 'Only people with the link.' },
  { value: 'private', label: 'Private', icon: Lock, hint: 'Only you.' }
]

export const cleanText = (text) => String(text || '').split(/\s+/).filter(Boolean).join(' ')

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

export const FieldLabel = memo(function FieldLabel({ children }) {
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

export const MetadataField = memo(function MetadataField({
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

export const TagEditor = memo(function TagEditor({ label, tags, color, onSave, placeholder = 'Type and press Enter' }) {
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

export const VisibilityControl = memo(function VisibilityControl({ value, onChange }) {
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

export const DescriptionField = memo(function DescriptionField({ value, onSave }) {
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

export const SettingToggle = memo(function SettingToggle({ label, on, onToggle }) {
  const { getWhite } = useDynamicTheme()
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs whitespace-nowrap" style={{ color: getWhite() }}>{label}</span>
      <ToggleChip on={on} onClick={onToggle} label={label} />
    </div>
  )
})

const ADD_ARTIST = '__add_artist__'

export const ArtistChooser = memo(function ArtistChooser({ artists, value, onChange, onCreate, fallbackLabel, disabled = false }) {
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

export const QualityBadge = memo(function QualityBadge({ tier, sampleRate, bitDepth, isLossless }) {
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

export const ArtworkSection = memo(function ArtworkSection({ trackId, hasArtwork, artworkGenerated, onArtworkUploaded }) {
  const { getWhite, getGrey400, getBorder } = useDynamicTheme()
  const { toastError } = useUISelector(state => ({ toastError: state.toastError }))
  const [uploading, setUploading] = useState(false)
  const [artwork, setArtwork] = useState({ shown: !!hasArtwork, version: 0 })
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
      await Promise.all(PACK_SIZES.map(size => packCache(size).invalidate(trackId)))
      setArtwork(prev => ({ shown: true, version: prev.version + 1 }))
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
    if (!artwork.shown) return 'No Artwork'
    if (artworkGenerated) return 'Generated Artwork'
    return 'Cover Artwork'
  }

  return (
    <div className="flex items-start gap-4">
      <div
        className="relative w-24 h-24 rounded-lg overflow-hidden flex-shrink-0 border"
        style={{ borderColor: getBorder(0.3), backgroundColor: 'rgba(0,0,0,0.3)' }}
      >
        {artwork.shown ? (
          <TrackArt key={artwork.version} trackId={trackId} alt="Track artwork" />
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
          {artwork.shown
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
          {uploading ? 'Uploading...' : artwork.shown ? 'Replace Artwork' : 'Upload Artwork'}
        </button>
      </div>
    </div>
  )
})

export const AudioFeaturesDisplay = memo(function AudioFeaturesDisplay({ features }) {
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
