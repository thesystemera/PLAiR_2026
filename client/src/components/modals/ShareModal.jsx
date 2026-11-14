import { useState, useCallback, useEffect } from 'react'
import { Modal, ModalProgress, ModalErrorState } from './Modal'
import { Fade, FadeSwap } from '../Motion'
import { PRESETS } from '../../lib/motion'
import { useUISelector } from '../../contexts/UIStateContext'
import { useGenerationQueue } from '../../contexts/GenerationQueueContext'
import { triggerHaptic } from '../../lib/haptics'
import { logger } from '../../lib/logger'
import { shareVideoBusy, shareVideoJob } from '../../lib/shareVideoJob'
import { TrackArt } from '../DepthArt'
import { InlineNote } from '../Notice'
import { artistByline, artistCredit } from '../../lib/utils'
import { Share2, Download, Copy, Check, Video } from 'lucide-react'

export function ShareModal({ isOpen, onClose, track }) {
  const {
    playingTrack,
    videoClipsEnabled,
    setVideoPreviewPlaying,
    jobTrackId,
    jobTitle,
    jobStatus,
    jobStage,
    jobProgress,
    jobUrl,
    jobSizeBytes,
    jobPublishedUrl,
    jobError,
    jobNote,
  } = useUISelector(state => ({
    playingTrack: state.engineState.currentTrack,
    videoClipsEnabled: state.settingsState.videoClipsEnabled,
    setVideoPreviewPlaying: state.setVideoPreviewPlaying,
    jobTrackId: state.shareVideoState.track?.id,
    jobTitle: state.shareVideoState.title,
    jobStatus: state.shareVideoState.status,
    jobStage: state.shareVideoState.stage,
    jobProgress: state.shareVideoState.progress,
    jobUrl: state.shareVideoState.url,
    jobSizeBytes: state.shareVideoState.sizeBytes,
    jobPublishedUrl: state.shareVideoState.publishedUrl,
    jobError: state.shareVideoState.error,
    jobNote: state.shareVideoState.note,
  }))
  const { setIsOpen: setJobsPanelOpen } = useGenerationQueue()
  const currentTrack = track || playingTrack

  const [shareError, setShareError] = useState(null)
  const [linkCopied, setLinkCopied] = useState(false)
  const [videoLinkCopied, setVideoLinkCopied] = useState(false)
  const [wasOpen, setWasOpen] = useState(isOpen)
  if (wasOpen !== isOpen) {
    setWasOpen(isOpen)
    if (isOpen) {
      setShareError(null)
      setLinkCopied(false)
      setVideoLinkCopied(false)
    }
  }

  const includeVideoClips = videoClipsEnabled

  const trackTitle = currentTrack?.generation_params?.title || 'Untitled'
  const trackArtist = artistCredit(currentTrack) || 'Unknown Artist'
  const trackByline = artistByline(currentTrack)
  const trackId = currentTrack?.id

  const jobIsThisTrack = !!trackId && jobTrackId === trackId
  const otherTrackRendering = !jobIsThisTrack && shareVideoBusy(jobStatus)
  const status = jobIsThisTrack ? jobStatus : 'idle'
  const isRendering = shareVideoBusy(status)
  const videoUrl = jobIsThisTrack ? jobUrl : null
  const publishedVideoUrl = jobIsThisTrack ? jobPublishedUrl : null
  const error = shareError || (jobIsThisTrack ? jobNote : null)

  useEffect(() => {
    if (!isOpen) setVideoPreviewPlaying(false)
  }, [isOpen, setVideoPreviewPlaying])

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
        text: `Listen to "${trackTitle}" ${trackByline} on plair.live`,
        url: getShareLink()
      })
    } catch (err) {
      if (err.name !== 'AbortError') {
        logger.error('[ShareModal] Share failed:', err)
      }
    }
  }, [currentTrack, trackTitle, trackByline, getShareLink])

  const handleGenerateVideo = useCallback(() => {
    if (!currentTrack) return
    setShareError(null)
    setJobsPanelOpen(true)
    void shareVideoJob.start(currentTrack, { includeClips: includeVideoClips })
  }, [currentTrack, includeVideoClips, setJobsPanelOpen])

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

    setShareError(null)
    triggerHaptic('light')

    try {
      const videoBlob = shareVideoJob.blob()
      if (publishedVideoUrl) {
        await navigator.share({
          title: trackTitle,
          text: `Watch "${trackTitle}" ${trackByline} on PLAiR`,
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
            text: `Watch "${trackTitle}" ${trackByline} on PLAiR`,
            files: [file]
          })
          return
        }
      }

      setShareError('This browser cannot share the MP4 directly. Download the MP4 or sign in to publish a hosted link.')
    } catch (err) {
      if (err.name === 'AbortError') return
      logger.error('[ShareModal] Share video failed:', err)
      setShareError(publishedVideoUrl ? 'Sharing failed. Copy the video link instead.' : 'Sharing failed. Download the MP4 instead.')
    }
  }, [publishedVideoUrl, trackTitle, trackByline])

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
              className="ui-press relative px-3 py-2 bg-white/10 hover:bg-white/20 rounded-lg transition-colors"
            >
              <FadeSwap swapKey={linkCopied ? 'copied' : 'copy'} preset={PRESETS.iconSwap}>
                {linkCopied ? (
                  <Check className="w-4 h-4 text-green-400" />
                ) : (
                  <Copy className="w-4 h-4 text-white" />
                )}
              </FadeSwap>
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
                  disabled={!currentTrack || otherTrackRendering}
                  className="ui-press-soft w-full flex items-center justify-center gap-2 px-4 py-3 bg-indigo-600 hover:bg-indigo-500 disabled:bg-gray-600 disabled:cursor-not-allowed rounded-lg transition-colors font-medium"
                >
                  <Video className="w-5 h-5" />
                  Generate Video{includeVideoClips ? ' + Clips' : ''}
                </button>
                {otherTrackRendering && (
                  <InlineNote tone="info">A video of “{jobTitle}” is rendering. This one can start when it is done.</InlineNote>
                )}
              </div>
            )}

            {isRendering && (
              <div className="space-y-3">
                <ModalProgress
                  progress={jobProgress}
                  statusText={jobStage || 'Rendering...'}
                  showSpinner={false}
                  showCancel={true}
                  onCancel={shareVideoJob.cancel}
                />
                <InlineNote tone="info" className="justify-center">
                  You can close this. The video keeps rendering and a notice tells you when it is ready.
                </InlineNote>
              </div>
            )}

            {status === 'complete' && videoUrl && (
              <div className="space-y-3">
                <div className="relative aspect-[9/16] max-h-64 mx-auto rounded-lg overflow-hidden bg-black">
                  <video
                    src={videoUrl}
                    controls
                    className="w-full h-full object-contain"
                    onPlay={() => setVideoPreviewPlaying(true)}
                    onPause={() => setVideoPreviewPlaying(false)}
                    onEnded={() => setVideoPreviewPlaying(false)}
                  />
                </div>
                <div className="text-xs text-gray-400 text-center">
                  {publishedVideoUrl ? 'Hosted MP4 ready' : 'MP4 ready'} - {(jobSizeBytes / (1024 * 1024)).toFixed(1)} MB
                </div>
                {publishedVideoUrl && (
                  <div className="flex gap-2">
                    <div className="flex-1 bg-white/5 rounded-lg px-3 py-2 text-sm text-gray-300 truncate">
                      {publishedVideoUrl}
                    </div>
                    <button
                      onClick={handleCopyVideoLink}
                      title="Copy hosted video link"
                      aria-label="Copy hosted video link"
                      className="ui-press relative px-3 py-2 bg-white/10 hover:bg-white/20 rounded-lg transition-colors"
                    >
                      <FadeSwap swapKey={videoLinkCopied ? 'copied' : 'copy'} preset={PRESETS.iconSwap}>
                        {videoLinkCopied ? (
                          <Check className="w-4 h-4 text-green-400" />
                        ) : (
                          <Copy className="w-4 h-4 text-white" />
                        )}
                      </FadeSwap>
                    </button>
                  </div>
                )}
                <Fade show={!!error}>
                  <InlineNote tone="warning">{error}</InlineNote>
                </Fade>
                <div className="flex gap-2">
                  <button
                    onClick={handleDownload}
                    className="ui-press flex-1 flex items-center justify-center gap-2 px-4 py-3 bg-green-600 hover:bg-green-500 rounded-lg transition-colors font-medium"
                  >
                    <Download className="w-5 h-5" />
                    Download MP4
                  </button>
                  {navigator.share && (
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
                message={jobError || 'Failed to generate video'}
                onRetry={handleGenerateVideo}
              />
            )}
          </FadeSwap>
        </div>
      </div>
    </Modal>
  )
}
