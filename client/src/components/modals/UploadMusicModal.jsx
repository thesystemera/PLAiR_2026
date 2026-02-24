import { useState, useRef, useCallback, memo } from 'react'
import { Upload, Loader2, Check, X, Edit2, Sparkles, FileAudio, Image, Gauge } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { api } from '../../lib/api'
import { triggerHaptic } from '../../lib/haptics'
import { useDynamicTheme } from '../../contexts/DynamicThemeContext'
import { useWebSocketSubscribe } from '../../contexts/WebSocketContext'
import Modal, { ModalSection, ModalButton, ModalFooter, ModalCard, ModalProgress, ModalErrorState, ModalSuccessBanner, ModalTagList } from './Modal'

const SUPPORTED_FORMATS = ['mp3', 'wav', 'flac', 'ogg', 'm4a', 'aac', 'opus', 'webm']
const MAX_FILE_SIZE = 100 * 1024 * 1024 // 100MB

const UploadStage = {
  SELECT: 'select',
  UPLOADING: 'uploading',
  ANALYZING: 'analyzing',
  PREVIEW: 'preview',
  ERROR: 'error'
}

const MetadataField = memo(function MetadataField({
  label,
  value,
  onEdit,
  isEditing,
  editValue,
  setEditValue,
  onSave,
  onCancel,
  multiline = false
}) {
  const { getWhite, getGrey400, getBorder } = useDynamicTheme()

  if (isEditing) {
    return (
      <div className="space-y-2">
        <label className="text-xs uppercase tracking-wide" style={{ color: getGrey400() }}>{label}</label>
        {multiline ? (
          <textarea
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            className="w-full px-3 py-2 bg-white/5 border rounded-lg text-sm focus:outline-none focus:border-purple-500 resize-none"
            style={{ borderColor: getBorder(0.3), color: getWhite() }}
            rows={3}
            autoFocus
          />
        ) : (
          <input
            type="text"
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            className="w-full px-3 py-2 bg-white/5 border rounded-lg text-sm focus:outline-none focus:border-purple-500"
            style={{ borderColor: getBorder(0.3), color: getWhite() }}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === 'Enter') onSave()
              if (e.key === 'Escape') onCancel()
            }}
          />
        )}
        <div className="flex gap-2">
          <button onClick={onSave} className="px-2 py-1 bg-green-500/20 text-green-400 rounded text-xs hover:bg-green-500/30">Save</button>
          <button onClick={onCancel} className="px-2 py-1 bg-gray-500/20 text-gray-400 rounded text-xs hover:bg-gray-500/30">Cancel</button>
        </div>
      </div>
    )
  }

  return (
    <div className="group">
      <label className="text-xs uppercase tracking-wide" style={{ color: getGrey400() }}>{label}</label>
      <div className="flex items-start justify-between gap-2 mt-1">
        <p className="text-sm" style={{ color: getWhite() }}>{value || 'Unknown'}</p>
        {onEdit && (
          <button
            onClick={onEdit}
            className="opacity-0 group-hover:opacity-100 p-1 hover:text-white transition"
            style={{ color: getGrey400() }}
          >
            <Edit2 size={12} />
          </button>
        )}
      </div>
    </div>
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
  const [uploading, setUploading] = useState(false)
  const [artworkUrl, setArtworkUrl] = useState(hasArtwork ? `/api/artwork/${trackId}?t=${Date.now()}` : null)
  const fileInputRef = useRef(null)

  const handleFileSelect = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return

    const validTypes = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']
    if (!validTypes.includes(file.type)) {
      alert('Please select a valid image file (JPEG, PNG, WebP, or GIF)')
      return
    }

    if (file.size > 10 * 1024 * 1024) {
      alert('Image too large. Maximum size is 10MB')
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
      alert(err.message || 'Failed to upload artwork')
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
          <img
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
          className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center gap-1.5"
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

export const UploadMusicModal = memo(function UploadMusicModal({ isOpen, onClose, onUploadComplete }) {
  const { getCategoryMetadata, getWhite, getGrey400 } = useDynamicTheme()
  const categoryColor = getCategoryMetadata('all')?.color || '#6366f1'

  const [stage, setStage] = useState(UploadStage.SELECT)
  const [file, setFile] = useState(null)
  const [progress, setProgress] = useState(0)
  const [stageText, setStageText] = useState('')
  const [error, setError] = useState(null)
  const [metadata, setMetadata] = useState(null)
  const [trackId, setTrackId] = useState(null)

  const [editingField, setEditingField] = useState(null)
  const [editValue, setEditValue] = useState('')

  const fileInputRef = useRef(null)
  const dragCounterRef = useRef(0)
  const [isDragging, setIsDragging] = useState(false)

  useWebSocketSubscribe('upload_progress', useCallback((data) => {
    if (data?.stage && data?.percent !== undefined) {
      setProgress(data.percent)
      setStageText(data.stage)
    }
  }, []))

  const resetState = useCallback(() => {
    setStage(UploadStage.SELECT)
    setFile(null)
    setProgress(0)
    setStageText('')
    setError(null)
    setMetadata(null)
    setTrackId(null)
    setEditingField(null)
    setEditValue('')
  }, [])

  const handleClose = useCallback(() => {
    resetState()
    onClose()
  }, [onClose, resetState])

  const validateFile = (file) => {
    const ext = file.name.split('.').pop()?.toLowerCase()
    if (!SUPPORTED_FORMATS.includes(ext)) {
      return `Unsupported format. Supported: ${SUPPORTED_FORMATS.join(', ')}`
    }
    if (file.size > MAX_FILE_SIZE) {
      return `File too large. Maximum size: ${MAX_FILE_SIZE / (1024 * 1024)}MB`
    }
    return null
  }

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
    if (!file) return

    setStage(UploadStage.UPLOADING)
    setProgress(10)

    try {
      setProgress(20)
      setStage(UploadStage.ANALYZING)

      const result = await api.uploadMusic(file)

      setProgress(100)
      setMetadata(result.metadata)
      setTrackId(result.track_id)
      setStage(UploadStage.PREVIEW)

      triggerHaptic('success')

    } catch {
      setError('Upload failed. Please try again.')
      setStage(UploadStage.ERROR)
      triggerHaptic('error')
    }
  }

  const handleSaveAndClose = () => {
    triggerHaptic('success')
    onUploadComplete?.(trackId, metadata)
    handleClose()
  }

  const startEditField = (field, currentValue) => {
    setEditingField(field)
    setEditValue(currentValue || '')
  }

  const saveEditField = async () => {
    if (!editingField || !trackId) return

    try {
      await api.put(`/api/user/music/tracks/${trackId}`, {
        [editingField]: editValue
      })

      setMetadata(prev => ({
        ...prev,
        [editingField]: editValue
      }))

      setEditingField(null)
      setEditValue('')
      triggerHaptic('light')
    } catch {
      // Edit cancellation is intentional, no error handling needed
    }
  }

  const cancelEdit = () => {
    setEditingField(null)
    setEditValue('')
  }

  const handleClearFile = () => {
    setFile(null)
    triggerHaptic('light')
  }

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      title="Upload Music"
      maxWidth="max-w-lg"
      categoryOverride="all"
    >
      <AnimatePresence mode="wait">
        {stage === UploadStage.SELECT && (
          <motion.div
            key="select"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
          >
            <ModalSection title="Select Audio File">
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
                className={`relative block border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-all ${
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
                  accept={SUPPORTED_FORMATS.map(f => `.${f}`).join(',')}
                  onChange={(e) => {
                    e.stopPropagation()
                    if (e.target.files?.[0]) handleFileSelect(e.target.files[0])
                  }}
                  className="sr-only"
                />

                {file ? (
                  <div className="flex items-center justify-center gap-3">
                    <div className="p-2 rounded-lg bg-green-500/20">
                      <FileAudio size={24} className="text-green-400" />
                    </div>
                    <div className="text-left flex-1 min-w-0">
                      <p className="font-medium truncate" style={{ color: getWhite() }}>{file.name}</p>
                      <p className="text-sm" style={{ color: getGrey400() }}>{(file.size / (1024 * 1024)).toFixed(2)} MB</p>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); handleClearFile() }}
                      className="p-2 rounded-lg hover:bg-red-500/20 transition"
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
                      {isDragging ? 'Drop your audio file here' : 'Drag & drop or click to select'}
                    </p>
                    <p className="mt-2 text-xs" style={{ color: getGrey400() }}>
                      {SUPPORTED_FORMATS.join(', ').toUpperCase()} • Max 100MB
                    </p>
                  </>
                )}
              </div>
            </ModalSection>

            {file && (
              <ModalFooter>
                <ModalButton
                  onClick={handleUpload}
                  disabled={!file}
                  variant="primary"
                >
                  <Upload size={16} className="mr-2" />
                  Upload
                </ModalButton>
              </ModalFooter>
            )}
          </motion.div>
        )}

        {(stage === UploadStage.UPLOADING || stage === UploadStage.ANALYZING) && (
          <motion.div
            key="uploading"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="py-8"
          >
            <ModalProgress
              progress={progress / 100}
              statusText={stageText || (stage === UploadStage.UPLOADING ? 'Uploading...' : 'Processing...')}
            />
          </motion.div>
        )}

        {stage === UploadStage.PREVIEW && metadata && (
          <motion.div
            key="preview"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
          >
            <ModalSuccessBanner message="Ready! Review and edit details if needed." className="mb-4" />

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
              <div className="grid grid-cols-2 gap-4">
                <ModalCard>
                  <MetadataField
                    label="Title"
                    value={metadata.title}
                    onEdit={() => startEditField('title', metadata.title)}
                    isEditing={editingField === 'title'}
                    editValue={editValue}
                    setEditValue={setEditValue}
                    onSave={saveEditField}
                    onCancel={cancelEdit}
                  />
                </ModalCard>
                <ModalCard>
                  <MetadataField
                    label="Artist"
                    value={metadata.artist}
                    onEdit={() => startEditField('artist', metadata.artist)}
                    isEditing={editingField === 'artist'}
                    editValue={editValue}
                    setEditValue={setEditValue}
                    onSave={saveEditField}
                    onCancel={cancelEdit}
                  />
                </ModalCard>
              </div>
            </ModalSection>

            <ModalSection title="Genre & Classification">
              <div className="space-y-3">
                <ModalCard>
                  <MetadataField
                    label="Primary Genre"
                    value={metadata.primary_genre}
                    onEdit={() => startEditField('primary_genre', metadata.primary_genre)}
                    isEditing={editingField === 'primary_genre'}
                    editValue={editValue}
                    setEditValue={setEditValue}
                    onSave={saveEditField}
                    onCancel={cancelEdit}
                  />
                </ModalCard>
                <ModalCard>
                  <label className="text-xs uppercase tracking-wide" style={{ color: getGrey400() }}>Secondary Genres</label>
                  <div className="mt-1">
                    <ModalTagList tags={metadata.secondary_genres} color={categoryColor} />
                  </div>
                </ModalCard>
              </div>
            </ModalSection>

            <ModalSection title="Mood & Vibe">
              <div className="grid grid-cols-2 gap-3">
                <ModalCard>
                  <label className="text-xs uppercase tracking-wide" style={{ color: getGrey400() }}>Mood</label>
                  <div className="mt-1">
                    <ModalTagList tags={metadata.mood_keywords} color="#10b981" />
                  </div>
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

            {metadata.has_lyrics && (
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
                {metadata.transcribed_lyrics && (
                  <ModalCard className="max-h-48 overflow-y-auto">
                    <label className="text-xs uppercase tracking-wide mb-2 block" style={{ color: getGrey400() }}>
                      Transcribed Lyrics
                    </label>
                    <pre
                      className="text-sm whitespace-pre-wrap font-sans leading-relaxed"
                      style={{ color: getWhite(), opacity: 0.9 }}
                    >
                      {metadata.transcribed_lyrics}
                    </pre>
                  </ModalCard>
                )}
                {!metadata.transcribed_lyrics && (
                  <ModalCard className="bg-green-500/10 border border-green-500/20">
                    <p className="text-xs text-green-400 flex items-center gap-2">
                      <Check size={14} />
                      Lyrics detected
                    </p>
                  </ModalCard>
                )}
              </ModalSection>
            )}

            <ModalFooter>
              <ModalButton onClick={resetState} variant="secondary">
                Upload Another
              </ModalButton>
              <ModalButton onClick={handleSaveAndClose} variant="primary">
                <Check size={16} className="mr-2" />
                Save to Library
              </ModalButton>
            </ModalFooter>
          </motion.div>
        )}

        {stage === UploadStage.ERROR && (
          <motion.div
            key="error"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="py-8"
          >
            <ModalErrorState
              title="Upload Failed"
              message={error}
              onRetry={resetState}
            />
          </motion.div>
        )}
      </AnimatePresence>
    </Modal>
  )
})