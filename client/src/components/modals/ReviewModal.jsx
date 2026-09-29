import { useState, useEffect, useCallback, memo } from 'react'
import { X, Mic, Square, Loader, Send, Play, Pause, MessageSquareText, Keyboard, Radio, LogIn } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { useDynamicTheme } from '../../contexts/DynamicThemeContext'
import { useProfilePicture } from '../../hooks/useProfilePicture'
import { usePlaybackShoutout } from '../../contexts/PlaybackShoutoutContext'
import { useUISelector } from '../../contexts/UIStateContext'
import { useVoiceRecording } from '../../contexts/VoiceRecordingContext'
import { useAuth } from '../../contexts/AuthContext'
import { logger } from '../../lib/logger'
import { api } from '../../lib/api'
import { triggerHaptic } from '../../lib/haptics'
import { blobToBase64, formatTimeAgo } from '../../lib/utils'
import MediaActions from '../MediaActions'
import Modal, { ModalSection } from './Modal'
import { MOTION, PRESETS } from '../../lib/motion'

const REVIEW_MIN_CHARS = 2
const REVIEW_MAX_CHARS = 600

function trackLabel(track) {
  if (!track) return { title: null, artist: null }
  const params = track.generation_params || {}
  return {
    title: params.title || track.title || null,
    artist: params.artist_name || track.artist_name || track.artist || null,
  }
}

const ReviewCard = memo(function ReviewCard({ review, isPlaying, onPlay, onStop }) {
  const { getWhite, getGrey300, getGrey400, getBorder } = useDynamicTheme()
  const profilePictureUrl = useProfilePicture(review?.user_id, !!review?.profile_picture)
  const canPlay = review.has_audio !== false && !!review.audio_url

  return (
    <motion.div
      {...PRESETS.listItem}
      className="flex items-start gap-3 p-3 rounded-lg transition-colors"
      style={{ backgroundColor: isPlaying ? getBorder(0.15) : getBorder(0.05) }}
    >
      {profilePictureUrl ? (
        <img decoding="async"
          src={profilePictureUrl}
          alt={review.username || 'User'}
          className="w-10 h-10 rounded-full object-cover flex-shrink-0"
        />
      ) : (
        <div className="w-10 h-10 rounded-full bg-gradient-to-br from-purple-500 to-blue-500 flex items-center justify-center text-white text-sm font-bold flex-shrink-0">
          {review.username?.charAt(0).toUpperCase() || 'U'}
        </div>
      )}

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1 flex-wrap">
          <span className="text-sm font-medium" style={{ color: getWhite() }}>
            {review.username || 'Anonymous'}
          </span>
          {review.timestamp && (
            <span className="text-xs" style={{ color: getGrey400() }}>
              {formatTimeAgo(review.timestamp)}
            </span>
          )}
          {!canPlay && (
            <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full" style={{ backgroundColor: getBorder(0.1), color: getGrey400() }}>
              <Keyboard size={10} />
              Typed
            </span>
          )}
          {review.sting_url && (
            <span
              className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-300"
              title="A short clip of this review can play over the song on air"
            >
              <Radio size={10} />
              On air
            </span>
          )}
        </div>
        <p className="text-sm" style={{ color: getGrey300() }}>
          &ldquo;{review.transcription}&rdquo;
        </p>
      </div>

      {canPlay && (
        <motion.button
          whileHover={PRESETS.hoverPressLarge.whileHover}
          whileTap={PRESETS.hoverPressLarge.whileTap}
          onClick={() => isPlaying ? onStop() : onPlay(review)}
          className="p-2 rounded-full flex-shrink-0"
          style={{ backgroundColor: isPlaying ? 'rgba(139, 92, 246, 0.3)' : getBorder(0.1) }}
          aria-label={isPlaying ? 'Stop review' : 'Play review'}
        >
          {isPlaying ? (
            <Pause size={16} style={{ color: getWhite() }} />
          ) : (
            <Play size={16} style={{ color: getWhite() }} />
          )}
        </motion.button>
      )}

      <div className="flex-shrink-0">
        <MediaActions type="shoutout" itemId={review.id} compact={true} />
      </div>
    </motion.div>
  )
})

export function ReviewModal({ isOpen, onClose, trackId, track, onLogin }) {
  const [reviews, setReviews] = useState([])
  const [loadedTrackId, setLoadedTrackId] = useState(null)
  const [loading, setLoading] = useState(false)
  const [text, setText] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const { playingShoutout, playShoutout, stopShoutout } = usePlaybackShoutout()
  const { getWhite, getGrey300, getGrey400, getBorder } = useDynamicTheme()
  const {
    reviewsUpdateCount,
    currentTrack,
    toastSuccess,
    toastError,
  } = useUISelector(state => ({
    reviewsUpdateCount: state.contentUpdates.reviews,
    currentTrack: state.engineState.currentTrack?.id === trackId ? state.engineState.currentTrack : null,
    toastSuccess: state.toastSuccess,
    toastError: state.toastError,
  }))
  const { isRecording, startRecording, stopRecording, abortRecording } = useVoiceRecording()
  const { user } = useAuth()

  const loadReviews = useCallback(async (id) => {
    const data = await api.getTrackReviews(id)
    return data?.reviews || []
  }, [])

  useEffect(() => {
    if (!isOpen || !trackId) return
    let cancelled = false
    setLoading(true)
    loadReviews(trackId)
      .then(list => {
        if (cancelled) return
        setReviews(list)
        setLoadedTrackId(trackId)
      })
      .catch(error => {
        logger.error('[ReviewModal] Failed to fetch reviews:', error)
        if (!cancelled) setReviews([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [isOpen, trackId, reviewsUpdateCount, loadReviews])

  const refresh = useCallback(async () => {
    try {
      setReviews(await loadReviews(trackId))
    } catch (error) {
      logger.error('[ReviewModal] Failed to refresh reviews:', error)
    }
  }, [trackId, loadReviews])

  const handleStartRecording = useCallback(async () => {
    if (isRecording || isSubmitting) return
    triggerHaptic('medium')
    if (playingShoutout) stopShoutout()
    await startRecording('review')
  }, [isRecording, isSubmitting, startRecording, playingShoutout, stopShoutout])

  const handleStopRecording = useCallback(async () => {
    if (!isRecording || !trackId) return
    triggerHaptic('medium')
    const audioBlob = await stopRecording()
    if (!audioBlob) return

    setIsSubmitting(true)
    try {
      const base64Audio = await blobToBase64(audioBlob)
      await api.uploadTrackReview(trackId, base64Audio)
      toastSuccess('Review posted!', 3000)
      await refresh()
    } catch (err) {
      logger.error('[ReviewModal] Failed to post voice review:', err)
      toastError(err instanceof Error ? err.message : 'Failed to post review', 3000)
    } finally {
      setIsSubmitting(false)
    }
  }, [isRecording, stopRecording, trackId, toastSuccess, toastError, refresh])

  const handleCancelRecording = useCallback(() => {
    if (!isRecording) return
    abortRecording()
    triggerHaptic('light')
  }, [isRecording, abortRecording])

  const trimmed = text.trim()
  const canSend = !isSubmitting && !isRecording && trimmed.length >= REVIEW_MIN_CHARS

  const handleSendText = useCallback(async (e) => {
    e?.preventDefault()
    if (!canSend || !trackId) return
    triggerHaptic('medium')
    setIsSubmitting(true)
    try {
      await api.typeTrackReview(trackId, trimmed)
      setText('')
      toastSuccess('Review posted!', 3000)
      await refresh()
    } catch (err) {
      logger.error('[ReviewModal] Failed to post typed review:', err)
      toastError(err instanceof Error ? err.message : 'Failed to post review', 3000)
    } finally {
      setIsSubmitting(false)
    }
  }, [canSend, trackId, trimmed, toastSuccess, toastError, refresh])

  const handleClose = useCallback(() => {
    if (isRecording) abortRecording()
    if (playingShoutout && reviews.some(r => r.id === playingShoutout.id)) stopShoutout()
    onClose()
  }, [isRecording, abortRecording, playingShoutout, reviews, stopShoutout, onClose])

  const handleLogin = useCallback(() => {
    handleClose()
    onLogin?.()
  }, [handleClose, onLogin])

  const shownReviews = loadedTrackId === trackId ? reviews : []
  const label = trackLabel(track || currentTrack || shownReviews[0]?.track)

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      maxWidth="max-w-lg"
      maxHeight="max-h-[85vh]"
      showCloseButton={false}
    >
      <div className="relative pb-6">
        <div className="relative pb-6 border-b mb-6" style={{ borderColor: getBorder(0.1) }}>
          <div className="flex items-start gap-4">
            <div className="w-12 h-12 rounded-full bg-gradient-to-br from-purple-500/60 to-pink-500/60 flex items-center justify-center flex-shrink-0">
              <MessageSquareText size={22} style={{ color: getWhite() }} />
            </div>
            <div className="flex-1 min-w-0">
              <h2 className="text-xl font-bold mb-1 transition-colors duration-theme" style={{ color: getWhite() }}>
                Reviews
              </h2>
              <p className="text-sm truncate transition-colors duration-theme" style={{ color: getGrey300() }}>
                {label.title || 'This track'}
                {label.artist && <span style={{ color: getGrey400() }}> · {label.artist}</span>}
              </p>
            </div>
            <motion.button
              whileHover={PRESETS.hoverPressLarge.whileHover}
              whileTap={PRESETS.hoverPressLarge.whileTap}
              onClick={handleClose}
              className="p-2 rounded-full transition-colors flex-shrink-0"
              style={{ backgroundColor: getBorder(0.1), color: getGrey400() }}
              aria-label="Close"
            >
              <X size={20} />
            </motion.button>
          </div>
        </div>

        <ModalSection title="Your review">
          {user ? (
            <div className="space-y-2">
              <form onSubmit={handleSendText} className="flex items-center gap-2">
                <input
                  type="text"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  maxLength={REVIEW_MAX_CHARS}
                  disabled={isSubmitting || isRecording}
                  placeholder="What do you think of this song?"
                  className="flex-1 min-w-0 rounded-xl px-3 py-2 text-sm bg-black/30 border outline-none disabled:opacity-50"
                  style={{ borderColor: getBorder(0.2), color: getWhite() }}
                  aria-label="Type a review"
                />
                <button
                  type="submit"
                  disabled={!canSend}
                  className="ui-tap p-2.5 rounded-xl flex-shrink-0 disabled:opacity-40"
                  style={{ background: 'linear-gradient(135deg, rgba(139, 92, 246, 0.4) 0%, rgba(236, 72, 153, 0.4) 100%)', color: getWhite() }}
                  aria-label="Post review"
                >
                  <Send size={16} />
                </button>
                {isRecording ? (
                  <>
                    <button
                      type="button"
                      onClick={handleCancelRecording}
                      className="ui-tap p-2.5 rounded-xl flex-shrink-0"
                      style={{ backgroundColor: getBorder(0.2), color: getGrey400() }}
                      aria-label="Cancel recording"
                    >
                      <X size={16} />
                    </button>
                    <motion.button
                      type="button"
                      onClick={handleStopRecording}
                      className="flex items-center gap-1.5 text-xs px-3 py-2.5 rounded-xl font-medium flex-shrink-0"
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
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={handleStartRecording}
                    disabled={isSubmitting}
                    className="ui-tap p-2.5 rounded-xl flex-shrink-0 disabled:opacity-40"
                    style={{ backgroundColor: getBorder(0.15), color: getWhite() }}
                    aria-label="Record a voice review"
                    title="Record a voice review"
                  >
                    <Mic size={16} />
                  </button>
                )}
              </form>
              <div className="flex items-center gap-1.5 text-xs" style={{ color: getGrey400() }}>
                {isSubmitting ? (
                  <>
                    <Loader size={12} className="animate-spin" />
                    Posting...
                  </>
                ) : isRecording ? (
                  'Recording... tap Stop when you are done'
                ) : (
                  'Type it, or tap the mic and say it. Spoken reviews can play over the song on air.'
                )}
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-between gap-3 p-3 rounded-lg" style={{ backgroundColor: getBorder(0.05) }}>
              <p className="text-sm" style={{ color: getGrey300() }}>
                Sign in to leave a review
              </p>
              <button
                type="button"
                onClick={handleLogin}
                className="ui-press flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full font-medium flex-shrink-0"
                style={{
                  background: 'linear-gradient(135deg, rgba(139, 92, 246, 0.3) 0%, rgba(236, 72, 153, 0.3) 100%)',
                  color: getWhite()
                }}
              >
                <LogIn size={12} />
                Sign in
              </button>
            </div>
          )}
        </ModalSection>

        <ModalSection title={
          <div className="flex items-center gap-2">
            <MessageSquareText size={14} />
            What listeners said
            {shownReviews.length > 0 && (
              <span className="text-xs px-1.5 py-0.5 rounded-full bg-purple-500/20 text-purple-300">
                {shownReviews.length}
              </span>
            )}
          </div>
        }>
          {loading && shownReviews.length === 0 ? (
            <div className="flex items-center justify-center py-6">
              <div className="animate-spin w-5 h-5 border-2 border-purple-500 border-t-transparent rounded-full" />
            </div>
          ) : shownReviews.length === 0 ? (
            <div className="text-center py-6">
              <MessageSquareText size={24} className="mx-auto mb-2 opacity-30" style={{ color: getGrey400() }} />
              <p className="text-sm" style={{ color: getGrey400() }}>
                No reviews yet
              </p>
              {user && (
                <p className="text-xs mt-1" style={{ color: getGrey400() }}>
                  Be the first to say what you think!
                </p>
              )}
            </div>
          ) : (
            <div className="space-y-2">
              <AnimatePresence>
                {shownReviews.map((review) => (
                  <ReviewCard
                    key={review.id}
                    review={review}
                    isPlaying={playingShoutout?.id === review.id}
                    onPlay={(r) => playShoutout(r, { showModal: false })}
                    onStop={stopShoutout}
                  />
                ))}
              </AnimatePresence>
            </div>
          )}
        </ModalSection>
      </div>
    </Modal>
  )
}
