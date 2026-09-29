import { useState, useEffect, useRef, memo, useCallback } from 'react'
import { X, Calendar, Clock, Tag, AlertCircle, Volume2, MapPin, Users, Frown, Meh, Smile, MessageCircle, Mic, ChevronRight, Play, Pause, Square, Loader, Send, Keyboard } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { useDynamicTheme } from '../../contexts/DynamicThemeContext'
import { useProfilePicture } from '../../hooks/useProfilePicture'
import { usePlaybackShoutout, useShoutoutProgress } from '../../contexts/PlaybackShoutoutContext'
import { useUISelector } from '../../contexts/UIStateContext'
import { useVoiceRecording } from '../../contexts/VoiceRecordingContext'
import { useAuth } from '../../contexts/AuthContext'
import { logger } from '../../lib/logger'
import { api } from '../../lib/api'
import { triggerHaptic } from '../../lib/haptics'
import { blobToBase64, formatTimeAgo } from '../../lib/utils'
import MediaActions from '../MediaActions'
import { useDeletePost } from '../../hooks/useDeletePost'
import { Modal, ModalSection, ModalMetadataField, ModalCard } from './Modal'
import { CSS_TRANSITION, MOTION, PRESETS } from '../../lib/motion'

const NO_TRANSITION = {}
const FFT_FADE_TRANSITION = CSS_TRANSITION.fadeOpacity

const NUM_BARS = 32
const REPLY_MIN_CHARS = 2
const REPLY_MAX_CHARS = 600
const KIND_TITLES = { shoutout: 'Shoutout', reply: 'Reply', review: 'Review' }

const hasPlayableAudio = (item) => item?.has_audio !== false && !!item?.audio_url

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
    const dpr = Math.min(window.devicePixelRatio || 1, 2)

    const rect = { width: 0, height: 0 }

    const resizeCanvas = () => {
      const bounds = canvas.getBoundingClientRect()
      rect.width = bounds.width
      rect.height = bounds.height
      canvas.width = bounds.width * dpr
      canvas.height = bounds.height * dpr
      ctx.scale(dpr, dpr)
    }

    resizeCanvas()
    window.addEventListener('resize', resizeCanvas, { passive: true })

    let animId
    let cleared = false
    const draw = () => {
      const samples = fftDataRef.current
      let silent = true
      for (let i = 0; i < NUM_BARS; i++) {
        if (samples[i]) {
          silent = false
          break
        }
      }
      if (silent && cleared) {
        animId = requestAnimationFrame(draw)
        return
      }
      cleared = silent
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
        transition: FFT_FADE_TRANSITION
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
            node.className = 'px-1 rounded transition-colors duration-micro text-white font-bold bg-white/20'
          }
        } else if (isPast) {
          if (!node.classList.contains('text-gray-500')) {
            node.className = 'px-1 rounded transition-colors duration-micro text-gray-500'
          }
        } else {
          if (!node.classList.contains('text-gray-400')) {
            node.className = 'px-1 rounded transition-colors duration-micro text-gray-400'
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
          className="px-1 rounded transition-colors duration-micro text-gray-400"
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

const ReplyCard = memo(function ReplyCard({ reply, isPlaying, onPlay, onStop, onDelete }) {
  const { getWhite, getGrey300, getGrey400, getBorder } = useDynamicTheme()
  const profilePictureUrl = useProfilePicture(reply?.user_id, !!reply?.profile_picture)

  const getUserInitial = () => {
    return reply.username?.charAt(0).toUpperCase() || 'U'
  }

  const canPlay = hasPlayableAudio(reply)

  const getDuration = () => {
    if (!canPlay) return null
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
      {...PRESETS.listItem}
      className="flex items-start gap-3 p-3 rounded-lg transition-colors"
      style={{ backgroundColor: isPlaying ? getBorder(0.15) : getBorder(0.05) }}
    >
      {profilePictureUrl ? (
        <img decoding="async"
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
          {reply.timestamp && (
            <span className="text-xs" style={{ color: getGrey400() }}>
              {formatTimeAgo(reply.timestamp)}
            </span>
          )}
          {!canPlay && (
            <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full" style={{ backgroundColor: getBorder(0.1), color: getGrey400() }}>
              <Keyboard size={10} />
              Typed
            </span>
          )}
        </div>
        <p className="text-sm line-clamp-3" style={{ color: getGrey300() }}>
          &ldquo;{reply.transcription}&rdquo;
        </p>
      </div>

      {canPlay && (
        <motion.button
          whileHover={PRESETS.hoverPressLarge.whileHover}
          whileTap={PRESETS.hoverPressLarge.whileTap}
          onClick={() => isPlaying ? onStop() : onPlay(reply)}
          className="p-2 rounded-full flex-shrink-0"
          style={{ backgroundColor: isPlaying ? 'rgba(139, 92, 246, 0.3)' : getBorder(0.1) }}
          aria-label={isPlaying ? 'Stop reply' : 'Play reply'}
        >
          {isPlaying ? (
            <Pause size={16} style={{ color: getWhite() }} />
          ) : (
            <Play size={16} style={{ color: getWhite() }} />
          )}
        </motion.button>
      )}

      <div className="flex-shrink-0">
        <MediaActions type="shoutout" itemId={reply.id} compact={true} onDelete={() => onDelete(reply)} />
      </div>
    </motion.div>
  )
})

const ShoutoutProgressBar = memo(function ShoutoutProgressBar({ durationSeconds, isPlaying, trackColor, barColor }) {
  const progress = useShoutoutProgress()
  return (
    <div className="relative h-1" style={{ backgroundColor: trackColor }}>
      <div
        className="h-full w-full origin-left transition-[transform,opacity] duration-progress"
        style={{
          backgroundColor: barColor,
          opacity: isPlaying ? 0.6 : 0.3,
          transform: `scaleX(${Math.min(1, Math.max(0, progress / durationSeconds))})`
        }}
      />
    </div>
  )
})

const LiveSyncedTranscription = memo(function LiveSyncedTranscription({ words, isPlaying }) {
  const progress = useShoutoutProgress()
  return <SyncedTranscription words={words} progress={progress} isPlaying={isPlaying} />
})

export function ShoutoutModal({ isOpen, onClose, shoutout: activeShoutout }) {
  const [retainedShoutout, setRetainedShoutout] = useState(activeShoutout)
  if (activeShoutout && activeShoutout !== retainedShoutout) setRetainedShoutout(activeShoutout)
  const shoutout = activeShoutout || retainedShoutout
  const [analytics, setAnalytics] = useState(null)
  const [replies, setReplies] = useState([])
  const [repliesLoading, setRepliesLoading] = useState(false)
  const [replySortBy, setReplySortBy] = useState('popularity')
  const [isSubmittingReply, setIsSubmittingReply] = useState(false)
  const [replyText, setReplyText] = useState('')
  const [isOpeningParent, setIsOpeningParent] = useState(false)
  const { playingShoutout, playShoutout, stopShoutout } = usePlaybackShoutout()
  const deletePost = useDeletePost()

  const handleDeleteReply = useCallback(async (reply) => {
    if (await deletePost(reply)) setReplies(prev => prev.filter(r => r.id !== reply.id))
  }, [deletePost])

  const handleDeleteShoutout = useCallback(async () => {
    if (await deletePost(shoutout)) onClose()
  }, [deletePost, shoutout, onClose])
  const { getWhite, getGrey300, getGrey400, getBorder, getCategoryMetadata } = useDynamicTheme()
  const {
    shoutoutFftDataRef,
    shoutoutsUpdateCount,
    openShoutoutModal,
    toastSuccess,
    toastError,
    toastWarning,
  } = useUISelector(state => ({
    shoutoutFftDataRef: state.shoutoutFftDataRef,
    shoutoutsUpdateCount: state.contentUpdates.shoutouts,
    openShoutoutModal: state.openShoutoutModal,
    toastSuccess: state.toastSuccess,
    toastError: state.toastError,
    toastWarning: state.toastWarning,
  }))
  const { isRecording, startRecording, stopRecording, abortRecording } = useVoiceRecording()
  const { user } = useAuth()

  const profilePictureUrl = useProfilePicture(
    shoutout?.user_id,
    !!shoutout?.profile_picture
  )

  const isRootShoutout = !!shoutout && (shoutout.kind ? shoutout.kind === 'shoutout' : !shoutout.is_reply && !shoutout.parent_id)
  const shoutoutHasAudio = hasPlayableAudio(shoutout)

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
  }, [shoutout?.id, isRootShoutout, replySortBy, shoutoutsUpdateCount])

  const refreshReplies = useCallback(async () => {
    if (!shoutout?.id) return
    const data = await api.getShoutoutReplies(shoutout.id, replySortBy)
    setReplies(data.replies || [])
  }, [shoutout?.id, replySortBy])

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
      const base64Audio = await blobToBase64(audioBlob)
      const result = await api.uploadShoutoutReply(shoutout.id, base64Audio)
      if (result?.status === 'scrapped') {
        toastWarning(`Reply not posted: ${result.feedback || 'the editor passed on it'}`, 6000)
        return
      }
      toastSuccess(result?.feedback ? `Reply posted! ${result.feedback}` : 'Reply posted!', 5000)
      await refreshReplies()
    } catch (err) {
      logger.error('[ShoutoutModal] Failed to submit reply:', err)
      toastError(err instanceof Error ? err.message : 'Failed to submit reply', 3000)
    } finally {
      setIsSubmittingReply(false)
    }
  }, [isRecording, stopRecording, shoutout?.id, refreshReplies, toastSuccess, toastError, toastWarning])

  const trimmedReply = replyText.trim()
  const canSendReply = !isSubmittingReply && !isRecording && trimmedReply.length >= REPLY_MIN_CHARS

  const handleSendTypedReply = useCallback(async (e) => {
    e?.preventDefault()
    if (!canSendReply || !shoutout?.id) return
    triggerHaptic('medium')
    setIsSubmittingReply(true)
    try {
      const result = await api.typeShoutoutReply(shoutout.id, trimmedReply)
      if (result?.status === 'scrapped') {
        toastWarning(`Reply not posted: ${result.feedback || 'the editor passed on it'}`, 6000)
        return
      }
      setReplyText('')
      toastSuccess(result?.feedback ? `Reply posted! ${result.feedback}` : 'Reply posted!', 5000)
      await refreshReplies()
    } catch (err) {
      logger.error('[ShoutoutModal] Failed to post typed reply:', err)
      toastError(err instanceof Error ? err.message : 'Failed to post reply', 3000)
    } finally {
      setIsSubmittingReply(false)
    }
  }, [canSendReply, shoutout?.id, trimmedReply, refreshReplies, toastSuccess, toastError, toastWarning])

  const handleOpenParent = useCallback(async () => {
    const parentId = shoutout?.parent_id
    if (!parentId || isOpeningParent) return
    triggerHaptic('light')
    setIsOpeningParent(true)
    try {
      const parent = await api.getShoutout(parentId)
      if (parent) {
        if (playingShoutout?.id === shoutout.id) stopShoutout()
        openShoutoutModal(parent)
      } else {
        toastError('That shoutout is no longer available', 3000)
      }
    } catch (err) {
      logger.error('[ShoutoutModal] Failed to open parent shoutout:', err)
      toastError(err instanceof Error ? err.message : 'Could not open that shoutout', 3000)
    } finally {
      setIsOpeningParent(false)
    }
  }, [shoutout?.parent_id, shoutout?.id, isOpeningParent, playingShoutout, stopShoutout, openShoutoutModal, toastError])

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

  if (!shoutout) return null

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
      {isOpen && shoutoutHasAudio && (
        <FFTVisualizer
          fftData={fftData}
          isPlaying={isPlaying}
          categoryColor={categoryColor}
        />
      )}

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
                <img decoding="async"
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
                  className="text-xl font-bold mb-1 transition-colors duration-theme"
                  style={{ color: getWhite() }}
                >
                  {KIND_TITLES[shoutout.kind] || 'Shoutout'}
                </h2>
                <p
                  className="text-sm transition-colors duration-theme"
                  style={{ color: getGrey300() }}
                >
                  {shoutout.username || 'Anonymous'}
                  {shoutout.kind === 'review' && shoutout.track?.title && (
                    <span style={{ color: getGrey400() }}> · on {shoutout.track.title}</span>
                  )}
                </p>
              </div>

              <motion.button
                whileHover={PRESETS.hoverPressLarge.whileHover}
                whileTap={PRESETS.hoverPressLarge.whileTap}
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
            <ShoutoutProgressBar
              durationSeconds={durationSeconds}
              isPlaying={isPlaying}
              trackColor={getBorder(0.1)}
              barColor={getWhite()}
            />
          )}

          <div className="flex-1 overflow-y-auto space-y-4">
            <ModalSection title={
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  {shoutoutHasAudio ? (
                    <motion.button
                      onClick={() => isPlaying ? stopShoutout() : playShoutout(shoutout, { showModal: false })}
                      className="p-1 rounded hover:bg-white/10 transition-colors cursor-pointer"
                      whileHover={PRESETS.hoverPressLarge.whileHover}
                      whileTap={PRESETS.hoverPressLarge.whileTap}
                      animate={isPlaying ? { scale: [1, 1.2, 1] } : {}}
                      transition={isPlaying ? MOTION.pulse : NO_TRANSITION}
                    >
                      <Volume2 size={14} />
                    </motion.button>
                  ) : (
                    <Keyboard size={14} />
                  )}
                  {shoutoutHasAudio ? 'Transcription' : 'Typed message'}
                </div>
                <MediaActions type="shoutout" itemId={shoutout.id} compact={true} onDelete={handleDeleteShoutout} />
              </div>
            }>
              {shoutout.word_level_transcription?.length > 0 ? (
                <div className="text-lg leading-relaxed">
                  <LiveSyncedTranscription
                    words={shoutout.word_level_transcription}
                    isPlaying={isPlaying}
                  />
                </div>
              ) : (
                <p
                  className="text-lg leading-relaxed transition-colors duration-theme"
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
                        className="ui-press text-xs px-2 py-1 rounded transition-colors"
                        style={{ backgroundColor: getBorder(0.1), color: getGrey400() }}
                      >
                        {replySortBy === 'popularity' ? '🔥 Top' : '🕐 Recent'}
                      </button>
                    )}
                    {user && !isSubmittingReply && (
                      isRecording ? (
                        <div className="flex items-center gap-2">
                          <motion.button
                            whileHover={PRESETS.hoverPress.whileHover}
                            whileTap={PRESETS.hoverPress.whileTap}
                            onClick={handleCancelRecording}
                            className="flex items-center gap-1.5 text-xs px-2 py-1.5 rounded-full font-medium"
                            style={{ backgroundColor: getBorder(0.2), color: getGrey400() }}
                          >
                            <X size={12} />
                          </motion.button>
                          <motion.button
                            whileHover={PRESETS.hoverPress.whileHover}
                            whileTap={PRESETS.hoverPress.whileTap}
                            onClick={handleStopRecording}
                            className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full font-medium"
                            style={{
                              background: 'linear-gradient(135deg, rgba(239, 68, 68, 0.5) 0%, rgba(239, 68, 68, 0.3) 100%)',
                              color: getWhite()
                            }}
                            animate={{ scale: [1, 1.05, 1] }}
                            transition={MOTION.beat}
                          >
                            <Square size={12} fill="currentColor" />
                            Stop
                          </motion.button>
                        </div>
                      ) : (
                        <motion.button
                          whileHover={PRESETS.hoverPress.whileHover}
                          whileTap={PRESETS.hoverPress.whileTap}
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
                {user && !isRecording && (
                  <form onSubmit={handleSendTypedReply} className="flex items-center gap-2 mb-3">
                    <input
                      type="text"
                      value={replyText}
                      onChange={(e) => setReplyText(e.target.value)}
                      maxLength={REPLY_MAX_CHARS}
                      disabled={isSubmittingReply}
                      placeholder="Type a reply..."
                      className="flex-1 min-w-0 rounded-xl px-3 py-2 text-sm bg-black/30 border outline-none disabled:opacity-50"
                      style={{ borderColor: getBorder(0.2), color: getWhite() }}
                      aria-label="Type a reply"
                    />
                    <button
                      type="submit"
                      disabled={!canSendReply}
                      className="ui-tap p-2.5 rounded-xl flex-shrink-0 disabled:opacity-40"
                      style={{ background: 'linear-gradient(135deg, rgba(139, 92, 246, 0.4) 0%, rgba(236, 72, 153, 0.4) 100%)', color: getWhite() }}
                      aria-label="Post reply"
                    >
                      <Send size={16} />
                    </button>
                  </form>
                )}
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
                          onDelete={handleDeleteReply}
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
                <button
                  type="button"
                  onClick={handleOpenParent}
                  disabled={isOpeningParent}
                  className="ui-press w-full text-left p-3 rounded-lg flex items-start gap-2 disabled:opacity-60"
                  style={{ backgroundColor: getBorder(0.05) }}
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-xs mb-1" style={{ color: getGrey400() }}>
                      Replying to <span style={{ color: getWhite() }}>{shoutout.parent_preview?.username || 'another listener'}</span>
                    </div>
                    <p className="text-sm line-clamp-3" style={{ color: getGrey300() }}>
                      {shoutout.parent_preview?.transcription
                        ? <>&ldquo;{shoutout.parent_preview.transcription}&rdquo;</>
                        : 'Tap to open the original shoutout'}
                    </p>
                  </div>
                  {isOpeningParent ? (
                    <Loader size={14} className="animate-spin flex-shrink-0 mt-1" style={{ color: getGrey400() }} />
                  ) : (
                    <ChevronRight size={14} className="flex-shrink-0 mt-1" style={{ color: getGrey400() }} />
                  )}
                </button>
              </ModalSection>
            )}
          </div>
        </div>
      </Modal>
    </>
  )
}