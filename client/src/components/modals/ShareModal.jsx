import { useState, useCallback, useEffect, useRef } from 'react'
import { Modal, ModalProgress, ModalErrorState } from './Modal'
import { FadeSwap } from '../Motion'
import { PRESETS } from '../../lib/motion'
import { useUISelector } from '../../contexts/UIStateContext'
import { triggerHaptic } from '../../lib/haptics'
import { logger } from '../../lib/logger'
import { api } from '../../lib/api'
import { FULL_PACK_SIZE, blobForUrl, packCache } from '../../lib/mediaCache'
import { TrackArt } from '../DepthArt'
import { Share2, Download, Copy, Check, Video } from 'lucide-react'

export function ShareModal({ isOpen, onClose, track }) {
  const {
    playingTrack,
    videoClipsEnabled,
    setIsOfflineRendering,
    setVideoPreviewPlaying,
  } = useUISelector(state => ({
    playingTrack: state.engineState.currentTrack,
    videoClipsEnabled: state.settingsState.videoClipsEnabled,
    setIsOfflineRendering: state.setIsOfflineRendering,
    setVideoPreviewPlaying: state.setVideoPreviewPlaying,
  }))
  const currentTrack = track || playingTrack

  const [status, setStatus] = useState('idle')
  const [statusText, setStatusText] = useState('')
  const [progress, setProgress] = useState(0)
  const [videoBlob, setVideoBlob] = useState(null)
  const [videoUrl, setVideoUrl] = useState(null)
  const [publishedVideoUrl, setPublishedVideoUrl] = useState(null)
  const [error, setError] = useState(null)
  const [linkCopied, setLinkCopied] = useState(false)
  const [videoLinkCopied, setVideoLinkCopied] = useState(false)

  const includeVideoClips = videoClipsEnabled

  const abortRef = useRef(false)
  const videoRef = useRef(null)
  const videoUrlRef = useRef(null)

  const releaseVideoUrl = useCallback(() => {
    if (videoUrlRef.current) {
      URL.revokeObjectURL(videoUrlRef.current)
      videoUrlRef.current = null
    }
  }, [])

  useEffect(() => releaseVideoUrl, [releaseVideoUrl])

  const trackTitle = currentTrack?.generation_params?.title || 'Untitled'
  const trackArtist = currentTrack?.generation_params?.artist_name || currentTrack?.track_info?.artist || currentTrack?.artist_name || 'Unknown Artist'
  const trackId = currentTrack?.id

  useEffect(() => {
    if (isOpen) {
      setStatus('idle')
      setStatusText('')
      setProgress(0)
      setVideoBlob(null)
      releaseVideoUrl()
      setVideoUrl(null)
      setPublishedVideoUrl(null)
      setError(null)
      setLinkCopied(false)
      setVideoLinkCopied(false)
      abortRef.current = false
    } else {
      setIsOfflineRendering(false)
      setVideoPreviewPlaying(false)
      abortRef.current = true
      setVideoBlob(null)
      releaseVideoUrl()
      setVideoUrl(null)
    }
  }, [isOpen, setIsOfflineRendering, setVideoPreviewPlaying, releaseVideoUrl])

  const getShareLink = useCallback(() => {
    return `https://plair.live/track/${trackId}`
  }, [trackId])

  const handleCopyLink = useCallback(async () => {
    if (!trackId) return

    try {
      await navigator.clipboard.writeText(getShareLink())
      setLinkCopied(true)
      triggerHaptic('success')
      setTimeout(() => setLinkCopied(false), 2000)
    } catch (err) {
      logger.error('[ShareModal] Failed to copy link:', err)
    }
  }, [trackId, getShareLink])

  const handleNativeShare = useCallback(async () => {
    if (!navigator.share || !currentTrack) return

    triggerHaptic('light')
    try {
      await navigator.share({
        title: trackTitle,
        text: `Listen to "${trackTitle}" by ${trackArtist} on plair.live`,
        url: getShareLink()
      })
    } catch (err) {
      if (err.name !== 'AbortError') {
        logger.error('[ShareModal] Share failed:', err)
      }
    }
  }, [currentTrack, trackTitle, trackArtist, getShareLink])

  const handleGenerateVideo = useCallback(async () => {
    if (!currentTrack) return

    setStatus('loading')
    setStatusText('Fetching track data...')
    setProgress(0)
    setError(null)
    abortRef.current = false
    setIsOfflineRendering(true)

    try {
      const fetchPromises = [
        api.getAudioFeatures(trackId).catch(() => null),
        api.getLyricTimestamps(trackId).catch(() => null)
      ]

      if (includeVideoClips) {
        fetchPromises.push(api.getVideoClips(trackId).catch(() => null))
      }

      const [audioFeatures, lyricTimestamps, videoClipsData] = await Promise.all(fetchPromises)

      const videoClips = (videoClipsData?.clips || []).map(clip => ({
        url: `${window.location.origin}${clip.url}`,
        duration: clip.duration
      }))

      if (videoClips.length > 0) {
        logger.info('[ShareModal] Video clips:', videoClipsData.keywords, videoClips.length)
      }

      if (abortRef.current) return

      const packUrl = currentTrack.has_artwork ? await packCache(FULL_PACK_SIZE).getMedia(trackId) : null
      const artworkBlob = packUrl ? blobForUrl(packUrl) : null

      const audioUrl = `${window.location.origin}${api.getRenderAudioUrl(trackId)}`

      if (!artworkBlob) {
        throw new Error('Track has no artwork')
      }

      setStatus('rendering')

      const { renderVideo } = await import('../../lib/offlineVideoRenderer')

      if (abortRef.current) return

      const blob = await renderVideo({
        trackId,
        artworkBlob,
        audioUrl,
        audioFeatures,
        lyricTimestamps,
        videoClips,
        durationMs: currentTrack.duration_ms,
        width: 720,
        height: 1280,
        fps: 30,
        onProgress: (p) => {
          if (!abortRef.current) {
            setProgress(p)
          }
        },
        onStatus: (s) => {
          if (!abortRef.current) {
            setStatusText(s)
            logger.info('[ShareModal]', s)
          }
        }
      })

      if (abortRef.current) return

      releaseVideoUrl()
      const url = URL.createObjectURL(blob)
      videoUrlRef.current = url
      setVideoBlob(blob)
      setVideoUrl(url)

      setStatusText('Publishing video to PLAiR...')
      try {
        const published = await api.uploadShareVideo({
          trackId,
          title: trackTitle,
          artist: trackArtist,
          blob
        })
        setPublishedVideoUrl(published.page_url)
        setError(null)
      } catch (publishErr) {
        logger.warn('[ShareModal] Hosted publish failed, keeping local video share available:', publishErr)
        setPublishedVideoUrl(null)
        setError('Video rendered. Sign in to publish a hosted PLAiR link, or use Share Video to send the MP4 directly.')
      }
      setStatus('complete')
      setIsOfflineRendering(false)
      triggerHaptic('success')

    } catch (err) {
      logger.error('[ShareModal] Generate failed:', err)
      setStatus('error')
      setError(err.message || 'Failed to generate video')
      setIsOfflineRendering(false)
    }
  }, [currentTrack, trackId, includeVideoClips, setIsOfflineRendering, trackTitle, trackArtist, releaseVideoUrl])

  const handleDownload = useCallback(() => {
    if (!videoUrl) return

    triggerHaptic('medium')
    const a = document.createElement('a')
    a.href = videoUrl
    a.download = `${trackTitle.replace(/[^a-z0-9]/gi, '_')}_plair.mp4`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  }, [videoUrl, trackTitle])

  const handleShareVideo = useCallback(async () => {
    if (!navigator.share) return

    setError(null)
    triggerHaptic('light')

    try {
      if (publishedVideoUrl) {
        await navigator.share({
          title: trackTitle,
          text: `Watch "${trackTitle}" by ${trackArtist} on PLAiR`,
          url: publishedVideoUrl
        })
        return
      }

      if (videoBlob) {
        const file = new File(
          [videoBlob],
          `${trackTitle.replace(/[^a-z0-9]/gi, '_')}_plair.mp4`,
          { type: 'video/mp4' }
        )
        if (!navigator.canShare || navigator.canShare({ files: [file] })) {
          await navigator.share({
            title: trackTitle,
            text: `Watch "${trackTitle}" by ${trackArtist} on PLAiR`,
            files: [file]
          })
          return
        }
      }

      setError('This browser cannot share the MP4 directly. Download the MP4 or sign in to publish a hosted link.')
    } catch (err) {
      if (err.name === 'AbortError') return
      logger.error('[ShareModal] Share video failed:', err)
      setError(publishedVideoUrl ? 'Sharing failed. Copy the video link instead.' : 'Sharing failed. Download the MP4 instead.')
    }
  }, [publishedVideoUrl, videoBlob, trackTitle, trackArtist])

  const handleCopyVideoLink = useCallback(async () => {
    if (!publishedVideoUrl) return

    try {
      await navigator.clipboard.writeText(publishedVideoUrl)
      setVideoLinkCopied(true)
      triggerHaptic('success')
      setTimeout(() => setVideoLinkCopied(false), 2000)
    } catch (err) {
      logger.error('[ShareModal] Failed to copy video link:', err)
    }
  }, [publishedVideoUrl])

  const handleCancel = useCallback(() => {
    abortRef.current = true
    setStatus('idle')
    setProgress(0)
    setStatusText('')
    setIsOfflineRendering(false)
  }, [setIsOfflineRendering])

  const isRendering = status === 'loading' || status === 'rendering'

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Share Track"
      maxWidth="max-w-md"
    >
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          {currentTrack?.has_artwork && (
            <div className="relative w-16 h-16 rounded-lg overflow-hidden flex-shrink-0">
              <TrackArt trackId={trackId} alt={trackTitle} />
            </div>
          )}
          <div className="flex-1 min-w-0">
            <div className="font-semibold text-white truncate">{trackTitle}</div>
            <div className="text-sm text-gray-400 truncate">{trackArtist}</div>
          </div>
        </div>

        <div>
          <div className="text-sm text-gray-400 mb-2">Track Link</div>
          <div className="flex gap-2">
            <div className="flex-1 bg-white/5 rounded-lg px-3 py-2 text-sm text-gray-300 truncate">
              {getShareLink()}
            </div>
            <button
              onClick={handleCopyLink}
              title="Copy track link"
              aria-label="Copy track link"
              className="ui-press px-3 py-2 bg-white/10 hover:bg-white/20 rounded-lg transition-colors"
            >
              {linkCopied ? (
                <Check className="w-4 h-4 text-green-400" />
              ) : (
                <Copy className="w-4 h-4 text-white" />
              )}
            </button>
            {navigator.share && (
              <button
                onClick={handleNativeShare}
                title="Share track link"
                aria-label="Share track link"
                className="ui-press px-3 py-2 bg-white/10 hover:bg-white/20 rounded-lg transition-colors"
              >
                <Share2 className="w-4 h-4 text-white" />
              </button>
            )}
          </div>
        </div>

        <div>
          <div className="text-sm text-gray-400 mb-2">Share as Video</div>

          <FadeSwap swapKey={isRendering ? 'rendering' : status} mode="wait" preset={PRESETS.stepSwap}>
            {status === 'idle' && (
              <div className="space-y-3">
                <button
                  onClick={handleGenerateVideo}
                  disabled={!currentTrack}
                  className="ui-press-soft w-full flex items-center justify-center gap-2 px-4 py-3 bg-indigo-600 hover:bg-indigo-500 disabled:bg-gray-600 disabled:cursor-not-allowed rounded-lg transition-colors font-medium"
                >
                  <Video className="w-5 h-5" />
                  Generate Video{includeVideoClips ? ' + Clips' : ''}
                </button>
              </div>
            )}

            {isRendering && (
              <ModalProgress
                progress={progress}
                statusText={statusText || (status === 'loading' ? 'Loading...' : 'Rendering...')}
                showSpinner={false}
                showCancel={true}
                onCancel={handleCancel}
              />
            )}

            {status === 'complete' && videoUrl && (
              <div className="space-y-3">
                <div className="relative aspect-[9/16] max-h-64 mx-auto rounded-lg overflow-hidden bg-black">
                  <video
                    ref={videoRef}
                    src={videoUrl}
                    controls
                    className="w-full h-full object-contain"
                    onPlay={() => setVideoPreviewPlaying(true)}
                    onPause={() => setVideoPreviewPlaying(false)}
                    onEnded={() => setVideoPreviewPlaying(false)}
                  />
                </div>
                {videoBlob && (
                  <div className="text-xs text-gray-400 text-center">
                    {publishedVideoUrl ? 'Hosted MP4 ready' : 'MP4 ready'} - {(videoBlob.size / (1024 * 1024)).toFixed(1)} MB
                  </div>
                )}
                {publishedVideoUrl && (
                  <div className="flex gap-2">
                    <div className="flex-1 bg-white/5 rounded-lg px-3 py-2 text-sm text-gray-300 truncate">
                      {publishedVideoUrl}
                    </div>
                    <button
                      onClick={handleCopyVideoLink}
                      title="Copy hosted video link"
                      aria-label="Copy hosted video link"
                      className="ui-press px-3 py-2 bg-white/10 hover:bg-white/20 rounded-lg transition-colors"
                    >
                      {videoLinkCopied ? (
                        <Check className="w-4 h-4 text-green-400" />
                      ) : (
                        <Copy className="w-4 h-4 text-white" />
                      )}
                    </button>
                  </div>
                )}
                {error && (
                  <div className="text-amber-400 text-sm bg-amber-500/10 rounded-lg px-3 py-2">
                    {error}
                  </div>
                )}
                <div className="flex gap-2">
                  <button
                    onClick={handleDownload}
                    className="ui-press flex-1 flex items-center justify-center gap-2 px-4 py-3 bg-green-600 hover:bg-green-500 rounded-lg transition-colors font-medium"
                  >
                    <Download className="w-5 h-5" />
                    Download MP4
                  </button>
                  {navigator.share && (publishedVideoUrl || videoBlob) && (
                    <button
                      onClick={handleShareVideo}
                      className="ui-press flex-1 flex items-center justify-center gap-2 px-4 py-3 bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors font-medium"
                    >
                      <Share2 className="w-5 h-5" />
                      Share Video
                    </button>
                  )}
                </div>
              </div>
            )}

            {status === 'error' && (
              <ModalErrorState
                title="Generation Failed"
                message={error || 'Failed to generate video'}
                onRetry={handleGenerateVideo}
              />
            )}
          </FadeSwap>
        </div>
      </div>
    </Modal>
  )
}
