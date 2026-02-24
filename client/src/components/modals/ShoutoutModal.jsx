import { useState, useEffect, useRef, memo, useCallback } from 'react'
import { X, Calendar, Clock, Tag, AlertCircle, Volume2, MapPin, Users, Frown, Meh, Smile, MessageCircle, Mic, ChevronRight, Play, Pause, Square, Loader } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { useDynamicTheme } from '../../contexts/DynamicThemeContext'
import { useProfilePicture } from '../../hooks/useProfilePicture'
import { usePlaybackShoutout } from '../../contexts/PlaybackShoutoutContext'
import { useUIState } from '../../contexts/UIStateContext'
import { useVoiceRecording } from '../../contexts/VoiceRecordingContext'
import { useAuth } from '../../contexts/AuthContext'
import { logger } from '../../lib/logger'
import { api } from '../../lib/api'
import { triggerHaptic } from '../../lib/haptics'
import MediaActions from '../MediaActions'
import Modal, { ModalSection, ModalMetadataField, ModalCard } from './Modal'

const NUM_BARS = 32

const FFTVisualizer = memo(function FFTVisualizer({ fftData, isPlaying, categoryColor }) {
  const canvasRef = useRef(null)
  const fftDataRef = useRef(fftData)
  const categoryColorRef = useRef(categoryColor)

  useEffect(() => {
    fftDataRef.current = fftData
  }, [fftData])

  useEffect(() => {
    categoryColorRef.current = categoryColor
  }, [categoryColor])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext('2d')
    const dpr = window.devicePixelRatio || 1

    const resizeCanvas = () => {
      const rect = canvas.getBoundingClientRect()
      canvas.width = rect.width * dpr
      canvas.height = rect.height * dpr
      ctx.scale(dpr, dpr)
    }

    resizeCanvas()
    window.addEventListener('resize', resizeCanvas)

    let animId
    const draw = () => {
      const rect = canvas.getBoundingClientRect()
      ctx.clearRect(0, 0, rect.width, rect.height)

      const barWidth = rect.width / NUM_BARS
      const maxHeight = rect.height
      const color = categoryColorRef.current

      const r = parseInt(color.slice(1, 3), 16)
      const g = parseInt(color.slice(3, 5), 16)
      const b = parseInt(color.slice(5, 7), 16)

      const data = fftDataRef.current
      for (let i = 0; i < NUM_BARS; i++) {
        const value = (data[i] || 0) / 255
        const barHeight = value * maxHeight * 0.9
        const x = i * barWidth
        const y = maxHeight - barHeight

        const topAlpha = Math.min(1.0, 0.6 + (value * 0.4))
        const midAlpha = Math.min(1.0, 0.4 + (value * 0.4))
        const bottomAlpha = 0.15 + (value * 0.15)

        const brightenedR = Math.min(255, r + (value * 40))
        const brightenedG = Math.min(255, g + (value * 40))
        const brightenedB = Math.min(255, b + (value * 40))

        const gradient = ctx.createLinearGradient(x, y, x, maxHeight)
        gradient.addColorStop(0, `rgba(${brightenedR}, ${brightenedG}, ${brightenedB}, ${topAlpha})`)
        gradient.addColorStop(0.5, `rgba(${r}, ${g}, ${b}, ${midAlpha})`)
        gradient.addColorStop(1, `rgba(${r}, ${g}, ${b}, ${bottomAlpha})`)

        ctx.fillStyle = gradient
        const gap = 1.5
        ctx.fillRect(x + gap / 2, y, barWidth - gap, barHeight)

        if (value > 0.4) {
          const glowIntensity = (value - 0.4) / 0.6
          ctx.shadowBlur = 12 * glowIntensity
          ctx.shadowColor = color
          ctx.fillRect(x + gap / 2, y, barWidth - gap, barHeight)
          ctx.shadowBlur = 0
        }
      }

      animId = requestAnimationFrame(draw)
    }

    animId = requestAnimationFrame(draw)
    return () => {
      if (animId) cancelAnimationFrame(animId)
      window.removeEventListener('resize', resizeCanvas)
    }
  }, [])

  const displayOpacity = isPlaying ? 0.4 : 0.1

  return (
    <div
      className="fixed bottom-0 left-0 right-0 h-12 pointer-events-none overflow-hidden z-[60]"
      style={{
        opacity: displayOpacity,
        transition: 'opacity 0.3s ease'
      }}
    >
      <canvas
        ref={canvasRef}
        className="w-full h-full"
        style={{
          filter: 'blur(0.5px)',
          mixBlendMode: 'screen'
        }}
      />
    </div>
  )
})

const SyncedTranscription = memo(function SyncedTranscription({ words, progress, isPlaying }) {
  const wordRefs = useRef(new Map())
  const requestRef = useRef(null)
  const lastUpdateTimeRef = useRef(0)
  const currentProgressRef = useRef(progress)

  useEffect(() => {
    lastUpdateTimeRef.current = Date.now()
  }, [progress])

  useEffect(() => {
    currentProgressRef.current = progress
    lastUpdateTimeRef.current = Date.now()
  }, [progress])

  useEffect(() => {
    if (!words?.length) return

    const timeOffset = words[0].start

    const animate = () => {
      const now = Date.now()
      if (isPlaying) {
        const dt = now - lastUpdateTimeRef.current
        currentProgressRef.current += dt / 1000
      }
      lastUpdateTimeRef.current = now

      const adjustedTimeSec = currentProgressRef.current + timeOffset

      words.forEach((word, wordIndex) => {
        const node = wordRefs.current.get(wordIndex)
        if (!node) return

        const isLastWord = wordIndex === words.length - 1
        const isActive = adjustedTimeSec >= word.start && adjustedTimeSec < word.end
        const isPast = isLastWord && isPlaying ? false : adjustedTimeSec >= word.end

        if (isActive) {
          if (!node.classList.contains('text-white')) {
            node.className = 'px-1 rounded transition-colors duration-150 text-white font-bold bg-white/20'
          }
        } else if (isPast) {
          if (!node.classList.contains('text-gray-500')) {
            node.className = 'px-1 rounded transition-colors duration-150 text-gray-500'
          }
        } else {
          if (!node.classList.contains('text-gray-400')) {
            node.className = 'px-1 rounded transition-colors duration-150 text-gray-400'
          }
        }
      })

      requestRef.current = requestAnimationFrame(animate)
    }

    requestRef.current = requestAnimationFrame(animate)

    return () => {
      if (requestRef.current) {
        cancelAnimationFrame(requestRef.current)
      }
    }
  }, [words, isPlaying])

  if (!words?.length) {
    return null
  }

  return (
    <div className="flex flex-wrap gap-x-1 gap-y-1">
      {words.map((word, wordIndex) => (
        <span
          key={wordIndex}
          ref={(el) => {
            if (el) wordRefs.current.set(wordIndex, el)
            else wordRefs.current.delete(wordIndex)
          }}
          className="px-1 rounded transition-colors duration-150 text-gray-400"
        >
          {word.word}
        </span>
      ))}
    </div>
  )
})


const SENTIMENT_CONFIG = {
  positive: { icon: Smile, label: 'Positive' },
  negative: { icon: Frown, label: 'Negative' },
  neutral: { icon: Meh, label: 'Neutral' }
}

const ReplyCard = memo(function ReplyCard({ reply, isPlaying, onPlay, onStop }) {
  const { getWhite, getGrey300, getGrey400, getBorder } = useDynamicTheme()
  const profilePictureUrl = useProfilePicture(reply?.user_id, !!reply?.profile_picture)

  const getUserInitial = () => {
    return reply.username?.charAt(0).toUpperCase() || 'U'
  }

  const getDuration = () => {
    if (reply.word_level_transcription?.length > 0) {
      const words = reply.word_level_transcription
      const duration = (words[words.length - 1].end || 0) - (words[0].start || 0)
      return `${duration.toFixed(1)}s`
    }
    if (reply.transcription_metadata?.duration) {
      return `${reply.transcription_metadata.duration.toFixed(1)}s`
    }
    return null
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex items-start gap-3 p-3 rounded-lg transition-colors"
      style={{ backgroundColor: isPlaying ? getBorder(0.15) : getBorder(0.05) }}
    >
      {profilePictureUrl ? (
        <img
          src={profilePictureUrl}
          alt={reply.username || 'User'}
          className="w-10 h-10 rounded-full object-cover flex-shrink-0"
        />
      ) : (
        <div className="w-10 h-10 rounded-full bg-gradient-to-br from-purple-500 to-blue-500 flex items-center justify-center text-white text-sm font-bold flex-shrink-0">
          {getUserInitial()}
        </div>
      )}

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-sm font-medium" style={{ color: getWhite() }}>
            {reply.username || 'Anonymous'}
          </span>
          {getDuration() && (
            <span className="text-xs" style={{ color: getGrey400() }}>
              {getDuration()}
            </span>
          )}
        </div>
        <p className="text-sm line-clamp-2" style={{ color: getGrey300() }}>
          &ldquo;{reply.transcription}&rdquo;
        </p>
      </div>

      <motion.button
        whileHover={{ scale: 1.1 }}
        whileTap={{ scale: 0.9 }}
        onClick={() => isPlaying ? onStop() : onPlay(reply)}
        className="p-2 rounded-full flex-shrink-0"
        style={{ backgroundColor: isPlaying ? 'rgba(139, 92, 246, 0.3)' : getBorder(0.1) }}
      >
        {isPlaying ? (
          <Pause size={16} style={{ color: getWhite() }} />
        ) : (
          <Play size={16} style={{ color: getWhite() }} />
        )}
      </motion.button>

      <div className="flex-shrink-0">
        <MediaActions type="shoutout" itemId={reply.id} compact={true} />
      </div>
    </motion.div>
  )
})

export function ShoutoutModal({ isOpen, onClose, shoutout }) {
  const [analytics, setAnalytics] = useState(null)
  const [replies, setReplies] = useState([])
  const [repliesLoading, setRepliesLoading] = useState(false)
  const [replySortBy, setReplySortBy] = useState('popularity')
  const [isSubmittingReply, setIsSubmittingReply] = useState(false)
  const { playingShoutout, playShoutout, stopShoutout, progress } = usePlaybackShoutout()
  const { getWhite, getGrey300, getGrey400, getBorder, getCategoryMetadata } = useDynamicTheme()
  const { shoutoutFftDataRef, contentUpdates, toastSuccess, toastError } = useUIState()
  const { isRecording, startRecording, stopRecording, abortRecording } = useVoiceRecording()
  const { user } = useAuth()

  const profilePictureUrl = useProfilePicture(
    shoutout?.user_id,
    !!shoutout?.profile_picture
  )

  const isRootShoutout = shoutout && !shoutout.is_reply && !shoutout.parent_id

  useEffect(() => {
    if (!shoutout?.id) return

    const fetchAnalytics = async () => {
      try {
        const data = await api.getShoutoutAnalytics(shoutout.id)
        if (data) {
          setAnalytics(data)
        }
      } catch (error) {
        logger.error('Failed to fetch shoutout analytics:', error)
      }
    }

    void fetchAnalytics()
  }, [shoutout?.id])

  useEffect(() => {
    if (!shoutout?.id || !isRootShoutout) {
      setReplies([])
      return
    }

    const fetchReplies = async () => {
      setRepliesLoading(true)
      try {
        const data = await api.getShoutoutReplies(shoutout.id, replySortBy)
        setReplies(data.replies || [])
      } catch (error) {
        logger.error('Failed to fetch replies:', error)
        setReplies([])
      } finally {
        setRepliesLoading(false)
      }
    }

    void fetchReplies()
  }, [shoutout?.id, isRootShoutout, replySortBy, contentUpdates.shoutouts])

  const handleStartRecording = useCallback(async () => {
    if (isRecording) return
    triggerHaptic('medium')
    if (playingShoutout) {
      stopShoutout()
    }
    await startRecording('reply')
  }, [isRecording, startRecording, playingShoutout, stopShoutout])

  const handleStopRecording = useCallback(async () => {
    if (!isRecording || !shoutout?.id) return
    triggerHaptic('medium')

    const audioBlob = await stopRecording()
    if (!audioBlob) {
      return
    }

    setIsSubmittingReply(true)

    try {
      const base64Audio = await new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onloadend = () => resolve(reader.result.split(',')[1])
        reader.onerror = reject
        reader.readAsDataURL(audioBlob)
      })

      const result = await api.uploadShoutoutReply(shoutout.id, base64Audio)
      logger.info('[ShoutoutModal] Reply uploaded directly:', result)
      toastSuccess('Reply submitted!', 3000)

      const data = await api.getShoutoutReplies(shoutout.id, replySortBy)
      setReplies(data.replies || [])
    } catch (err) {
      logger.error('[ShoutoutModal] Failed to submit reply:', err)
      toastError(err instanceof Error ? err.message : 'Failed to submit reply', 3000)
    } finally {
      setIsSubmittingReply(false)
    }
  }, [isRecording, stopRecording, shoutout?.id, replySortBy, toastSuccess, toastError])

  const handleCancelRecording = useCallback(() => {
    if (isRecording) {
      abortRecording()
      triggerHaptic('light')
    }
  }, [isRecording, abortRecording])

  const handleClose = () => {
    if (isRecording) {
      abortRecording()
    }
    if (playingShoutout?.id === shoutout?.id) {
      stopShoutout()
    }
    onClose()
  }

  if (!isOpen || !shoutout) return null

  const getCategoryLabel = (category) => {
    if (!category) return 'General'
    return category.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())
  }

  const formatDate = (timestamp) => {
    try {
      const date = new Date(timestamp)
      return date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      })
    } catch {
      return 'Unknown'
    }
  }

  const getDuration = () => {
    if (shoutout.word_level_transcription?.length > 0) {
      const words = shoutout.word_level_transcription
      const firstWord = words[0]
      const lastWord = words[words.length - 1]
      const duration = (lastWord.end || 0) - (firstWord.start || 0)
      return `${duration.toFixed(1)}s`
    }
    if (shoutout.transcription_metadata?.duration) {
      return `${shoutout.transcription_metadata.duration.toFixed(1)}s`
    }
    if (shoutout.metadata?.duration) {
      return `${shoutout.metadata.duration.toFixed(1)}s`
    }
    return null
  }

  const getDurationSeconds = () => {
    if (shoutout.word_level_transcription?.length > 0) {
      const words = shoutout.word_level_transcription
      const firstWord = words[0]
      const lastWord = words[words.length - 1]
      return (lastWord.end || 0) - (firstWord.start || 0)
    }
    if (shoutout.transcription_metadata?.duration) {
      return shoutout.transcription_metadata.duration
    }
    if (shoutout.metadata?.duration) {
      return shoutout.metadata.duration
    }
    return 0
  }

  const getUserInitial = () => {
    return shoutout.username?.charAt(0).toUpperCase() || 'U'
  }

  const metadata = shoutout.transcription_metadata || shoutout.metadata || {}
  const userData = shoutout.user_data || {}
  const sentimentConfig = SENTIMENT_CONFIG[metadata.sentiment]
  const durationSeconds = getDurationSeconds()

  const metadataFields = [
    { icon: Tag, label: 'Category', value: metadata.category ? getCategoryLabel(metadata.category) : null },
    {
      icon: AlertCircle,
      label: 'Urgency',
      value: metadata.urgency_label,
      extraLabel: metadata.time_sensitive ? <span title="Time Sensitive">⏰</span> : null
    },
    { icon: AlertCircle, label: 'Importance', value: metadata.importance_label?.replace(/_/g, ' ') },
    { icon: Users, label: 'Audience', value: metadata.target_audience },
    {
      icon: sentimentConfig?.icon,
      label: 'Sentiment',
      value: sentimentConfig?.label
    },
    { icon: Clock, label: 'Duration', value: getDuration() },
    { icon: Calendar, label: 'Created', value: formatDate(shoutout.timestamp) },
    { icon: MapPin, label: 'Location', value: userData.location, span: 2 }
  ]

  const isPlaying = playingShoutout?.id === shoutout.id
  const categoryMeta = getCategoryMetadata('shoutouts')
  const categoryColor = categoryMeta?.color || '#9333ea'
  const fftData = shoutoutFftDataRef?.current || new Array(NUM_BARS).fill(0)

  return (
    <>
      <FFTVisualizer
        fftData={fftData}
        isPlaying={isPlaying}
        categoryColor={categoryColor}
      />

      <Modal
        isOpen={isOpen}
        onClose={onClose}
        maxWidth="max-w-lg"
        maxHeight="max-h-[85vh]"
        showCloseButton={false}
      >
        <div className="relative pb-6">
          <div className="relative pb-6 border-b mb-6" style={{ borderColor: getBorder(0.1) }}>
            <div className="flex items-start gap-4">
              {profilePictureUrl ? (
                <img
                  src={profilePictureUrl}
                  alt={shoutout.username || 'User'}
                  className="w-16 h-16 rounded-full object-cover shadow-lg flex-shrink-0"
                />
              ) : (
                <div className="w-16 h-16 rounded-full bg-gradient-to-br from-purple-500 to-blue-500 flex items-center justify-center text-white text-2xl font-bold shadow-lg flex-shrink-0">
                  {getUserInitial()}
                </div>
              )}

              <div className="flex-1 min-w-0">
                <h2
                  className="text-xl font-bold mb-1 transition-colors duration-700"
                  style={{ color: getWhite() }}
                >
                  Shoutout
                </h2>
                <p
                  className="text-sm transition-colors duration-700"
                  style={{ color: getGrey300() }}
                >
                  {shoutout.username || 'Anonymous'}
                </p>
              </div>

              <motion.button
                whileHover={{ scale: 1.1 }}
                whileTap={{ scale: 0.9 }}
                onClick={handleClose}
                className="p-2 rounded-full transition-colors flex-shrink-0"
                style={{
                  backgroundColor: getBorder(0.1),
                  color: getGrey400()
                }}
              >
                <X size={20} />
              </motion.button>
            </div>
          </div>

          {durationSeconds > 0 && (
            <div className="relative h-1" style={{ backgroundColor: getBorder(0.1) }}>
              <div
                className="h-full transition-all duration-100"
                style={{
                  backgroundColor: getWhite(),
                  opacity: isPlaying ? 0.6 : 0.3,
                  width: `${Math.min(100, (progress / durationSeconds) * 100)}%`
                }}
              />
            </div>
          )}

          <div className="flex-1 overflow-y-auto space-y-4">
            <ModalSection title={
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <motion.button
                    onClick={() => isPlaying ? stopShoutout() : playShoutout(shoutout, { showModal: false })}
                    className="p-1 rounded hover:bg-white/10 transition-colors cursor-pointer"
                    whileHover={{ scale: 1.1 }}
                    whileTap={{ scale: 0.9 }}
                    animate={isPlaying ? { scale: [1, 1.2, 1] } : {}}
                    transition={isPlaying ? { duration: 0.8, repeat: Infinity } : {}}
                  >
                    <Volume2 size={14} />
                  </motion.button>
                  Transcription
                </div>
                <MediaActions type="shoutout" itemId={shoutout.id} compact={true} />
              </div>
            }>
              {shoutout.word_level_transcription?.length > 0 ? (
                <div className="text-lg leading-relaxed">
                  <SyncedTranscription
                    words={shoutout.word_level_transcription}
                    progress={progress}
                    isPlaying={isPlaying}
                  />
                </div>
              ) : (
                <p
                  className="text-lg leading-relaxed transition-colors duration-700"
                  style={{ color: getWhite() }}
                >
                  &ldquo;{shoutout.transcription}&rdquo;
                </p>
              )}
            </ModalSection>

            <ModalSection title="Details">
              <div className="grid grid-cols-2 gap-3">
              {metadataFields.map((field, idx) => (
                <ModalMetadataField key={idx} {...field} />
              ))}
              </div>
            </ModalSection>

            {metadata.tags?.length > 0 && (
              <ModalSection title={
                <div className="flex items-center gap-2">
                  <Tag size={14} />
                  Tags
                </div>
              }>
                <div className="flex flex-wrap gap-2">
                  {metadata.tags.map((tag, idx) => (
                    <span
                      key={idx}
                      className="text-xs px-2.5 py-1 rounded-full"
                      style={{
                        backgroundColor: getBorder(0.15),
                        color: getGrey300()
                      }}
                    >
                      #{tag}
                    </span>
                  ))}
                </div>
              </ModalSection>
            )}

            {analytics?.total_plays > 0 && (
              <ModalSection title="Analytics">
                <ModalCard background="linear-gradient(135deg, rgba(139, 92, 246, 0.1) 0%, rgba(236, 72, 153, 0.1) 100%)" className="border border-purple-500/20">
                <div className="grid grid-cols-2 gap-2.5">
                  <ModalCard background="rgba(0,0,0,0.2)" className="p-2.5">
                    <div className="text-xs text-gray-400 mb-0.5">Total Plays</div>
                    <div className="font-bold text-base text-purple-300">
                      {analytics.total_plays}
                    </div>
                    {analytics.unique_listeners > 0 && (
                      <div className="text-xs text-gray-500">
                        {analytics.unique_listeners} listener{analytics.unique_listeners !== 1 ? 's' : ''}
                      </div>
                    )}
                  </ModalCard>

                  <ModalCard background="rgba(0,0,0,0.2)" className="p-2.5">
                    <div className="text-xs text-gray-400 mb-0.5">👍 Likes</div>
                    <div className="font-bold text-base text-green-400">
                      {analytics.likes || 0}
                    </div>
                  </ModalCard>

                  <ModalCard background="rgba(0,0,0,0.2)" className="p-2.5">
                    <div className="text-xs text-gray-400 mb-0.5">⭐ Super Likes</div>
                    <div className="font-bold text-base text-yellow-400">
                      {analytics.superlikes || 0}
                    </div>
                  </ModalCard>

                  <ModalCard background="rgba(0,0,0,0.2)" className="p-2.5">
                    <div className="text-xs text-gray-400 mb-0.5">🚫 Bans</div>
                    <div className="font-bold text-base text-red-400">
                      {analytics.bans || 0}
                    </div>
                  </ModalCard>

                  {analytics.avg_completion_pct > 0 && (
                    <ModalCard background="rgba(0,0,0,0.2)" className="p-2.5">
                      <div className="text-xs text-gray-400 mb-0.5">Avg Completion</div>
                      <div className="font-bold text-base text-blue-300">
                        {Math.round(analytics.avg_completion_pct)}%
                      </div>
                    </ModalCard>
                  )}

                  {analytics.skip_count > 0 && (
                    <ModalCard background="rgba(0,0,0,0.2)" className="p-2.5">
                      <div className="text-xs text-gray-400 mb-0.5">Skips</div>
                      <div className="font-bold text-base text-orange-300">
                        {analytics.skip_count}
                      </div>
                      <div className="text-xs text-gray-500">
                        {Math.round(analytics.skip_rate * 100)}% rate
                      </div>
                    </ModalCard>
                  )}

                  {analytics.percentile !== undefined && (
                    <ModalCard background="rgba(0,0,0,0.2)" className="p-2.5 col-span-2">
                      <div className="text-xs text-gray-400 mb-0.5">Popularity Ranking</div>
                      <div className="font-bold text-xl bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent">
                        {analytics.percentile >= 95 ? '🔥 ' : analytics.percentile >= 75 ? '⭐ ' : ''}
                        Top {Math.round(100 - analytics.percentile)}%
                      </div>
                      <div className="text-xs text-gray-500 mt-0.5">
                        Score: {analytics.popularity_score.toFixed(1)}
                      </div>
                    </ModalCard>
                  )}
                </div>
                </ModalCard>
              </ModalSection>
            )}

            {isRootShoutout && (
              <ModalSection title={
                <div className="flex items-center justify-between w-full">
                  <div className="flex items-center gap-2">
                    <MessageCircle size={14} />
                    Replies
                    {(shoutout.reply_count > 0 || replies.length > 0) && (
                      <span className="text-xs px-1.5 py-0.5 rounded-full bg-purple-500/20 text-purple-300">
                        {replies.length || shoutout.reply_count || 0}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {replies.length > 1 && (
                      <button
                        onClick={() => setReplySortBy(prev => prev === 'popularity' ? 'recent' : 'popularity')}
                        className="text-xs px-2 py-1 rounded transition-colors"
                        style={{ backgroundColor: getBorder(0.1), color: getGrey400() }}
                      >
                        {replySortBy === 'popularity' ? '🔥 Top' : '🕐 Recent'}
                      </button>
                    )}
                    {user && !isSubmittingReply && (
                      isRecording ? (
                        <div className="flex items-center gap-2">
                          <motion.button
                            whileHover={{ scale: 1.05 }}
                            whileTap={{ scale: 0.95 }}
                            onClick={handleCancelRecording}
                            className="flex items-center gap-1.5 text-xs px-2 py-1.5 rounded-full font-medium"
                            style={{ backgroundColor: getBorder(0.2), color: getGrey400() }}
                          >
                            <X size={12} />
                          </motion.button>
                          <motion.button
                            whileHover={{ scale: 1.05 }}
                            whileTap={{ scale: 0.95 }}
                            onClick={handleStopRecording}
                            className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full font-medium"
                            style={{
                              background: 'linear-gradient(135deg, rgba(239, 68, 68, 0.5) 0%, rgba(239, 68, 68, 0.3) 100%)',
                              color: getWhite()
                            }}
                            animate={{ scale: [1, 1.05, 1] }}
                            transition={{ duration: 1, repeat: Infinity }}
                          >
                            <Square size={12} fill="currentColor" />
                            Stop
                          </motion.button>
                        </div>
                      ) : (
                        <motion.button
                          whileHover={{ scale: 1.05 }}
                          whileTap={{ scale: 0.95 }}
                          onClick={handleStartRecording}
                          className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full font-medium"
                          style={{
                            background: 'linear-gradient(135deg, rgba(139, 92, 246, 0.3) 0%, rgba(236, 72, 153, 0.3) 100%)',
                            color: getWhite()
                          }}
                        >
                          <Mic size={12} />
                          Reply
                        </motion.button>
                      )
                    )}
                    {isSubmittingReply && (
                      <div className="flex items-center gap-1.5 text-xs px-3 py-1.5">
                        <Loader size={12} className="animate-spin" style={{ color: getGrey400() }} />
                        <span style={{ color: getGrey400() }}>Submitting...</span>
                      </div>
                    )}
                  </div>
                </div>
              }>
                {repliesLoading ? (
                  <div className="flex items-center justify-center py-6">
                    <div className="animate-spin w-5 h-5 border-2 border-purple-500 border-t-transparent rounded-full" />
                  </div>
                ) : replies.length === 0 ? (
                  <div className="text-center py-6">
                    <MessageCircle size={24} className="mx-auto mb-2 opacity-30" style={{ color: getGrey400() }} />
                    <p className="text-sm" style={{ color: getGrey400() }}>
                      No replies yet
                    </p>
                    {user && (
                      <p className="text-xs mt-1" style={{ color: getGrey400() }}>
                        Be the first to reply!
                      </p>
                    )}
                  </div>
                ) : (
                  <div className="space-y-2 max-h-60 overflow-y-auto">
                    <AnimatePresence>
                      {replies.map((reply) => (
                        <ReplyCard
                          key={reply.id}
                          reply={reply}
                          isPlaying={playingShoutout?.id === reply.id}
                          onPlay={(r) => playShoutout(r, { showModal: false })}
                          onStop={stopShoutout}
                        />
                      ))}
                    </AnimatePresence>
                  </div>
                )}
              </ModalSection>
            )}

            {shoutout?.parent_id && (
              <ModalSection title={
                <div className="flex items-center gap-2">
                  <ChevronRight size={14} className="rotate-180" />
                  Reply To
                </div>
              }>
                <p className="text-sm" style={{ color: getGrey300() }}>
                  This is a reply to another shoutout
                </p>
              </ModalSection>
            )}
          </div>
        </div>
      </Modal>
    </>
  )
}