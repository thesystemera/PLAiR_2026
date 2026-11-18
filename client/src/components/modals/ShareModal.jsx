import { useState, useCallback, useEffect } from 'react'
import { Modal, ModalButton, ModalErrorState, ModalProgress, ModalSection } from './Modal'
import { Fade, FadeSwap } from '../Motion'
import { OptionCard } from '../PlayerSheet'
import { PRESETS } from '../../lib/motion'
import { useUISelector } from '../../contexts/UIStateContext'
import { useViewport } from '../../contexts/ViewportContext'
import { useGenerationQueue } from '../../contexts/GenerationQueueContext'
import { triggerHaptic } from '../../lib/haptics'
import { logger } from '../../lib/logger'
import { SHARE_QUALITIES, shareVideoBusy, shareVideoJob, shareVideoSupport } from '../../lib/shareVideoJob'
import { TrackArt } from '../DepthArt'
import { InlineNote } from '../Notice'
import { artistByline, artistCredit, formatTimeLeft } from '../../lib/utils'
import { Share2, Download, Copy, Check, Video, RectangleVertical, RectangleHorizontal, Sparkles, Film, Zap } from 'lucide-react'

const ORIENTATION_CHOICES = [
  { id: 'portrait', icon: RectangleVertical, title: 'Portrait', subtitle: '9:16, for phones' },
  { id: 'landscape', icon: RectangleHorizontal, title: 'Landscape', subtitle: '16:9, widescreen' },
]

const QUALITY_CHOICES = [
  { id: '1080p60', icon: Sparkles, title: '1080p · 60 fps', subtitle: 'Sharpest picture, takes longest' },
  { id: '720p60', icon: Film, title: '720p · 60 fps', subtitle: 'Smooth, smaller file' },
  { id: '720p30', icon: Zap, title: '720p · 30 fps', subtitle: 'Quickest to make' },
]

const NOT_ON_DEVICE = "This device can't encode this format"

export function ShareModal({ isOpen, onClose, track }) {
  const { isPortrait } = useViewport()
  const {
    playingTrack,
    videoClipsEnabled,
    savedQuality,
    publishSettings,
    setVideoPreviewPlaying,
    jobTrackId,
    jobTitle,
    jobOrientation,
    jobStatus,
    jobStage,
    jobProgress,
    jobEtaSeconds,
    jobUrl,
    jobSizeBytes,
    jobPublishedUrl,
    jobError,
    jobNote,
    toastError,
  } = useUISelector(state => ({
    toastError: state.toastError,
    playingTrack: state.engineState.currentTrack,
    videoClipsEnabled: state.settingsState.videoClipsEnabled,
    savedQuality: state.settingsState.shareVideoQuality,
    publishSettings: state.publishSettings,
    setVideoPreviewPlaying: state.setVideoPreviewPlaying,
    jobTrackId: state.shareVideoState.track?.id,
    jobTitle: state.shareVideoState.title,
    jobOrientation: state.shareVideoState.orientation,
    jobStatus: state.shareVideoState.status,
    jobStage: state.shareVideoState.stage,
    jobProgress: state.shareVideoState.progress,
    jobEtaSeconds: state.shareVideoState.etaSeconds,
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
  const [orientation, setOrientation] = useState(isPortrait ? 'portrait' : 'landscape')
  const [support, setSupport] = useState(null)
  const [wasOpen, setWasOpen] = useState(isOpen)
  if (wasOpen !== isOpen) {
    setWasOpen(isOpen)
    if (isOpen) {
      setShareError(null)
      setLinkCopied(false)
      setVideoLinkCopied(false)
      setOrientation(isPortrait ? 'portrait' : 'landscape')
    }
  }

  useEffect(() => {
    if (!isOpen) return
    let live = true
    shareVideoSupport().then(result => { if (live) setSupport(result) })
    return () => { live = false }
  }, [isOpen])

  const includeVideoClips = videoClipsEnabled
  const canMake = (quality, shape) => !support || support[`${quality}:${shape}`]?.supported
  const quality = canMake(savedQuality, orientation)
    ? savedQuality
    : Object.keys(SHARE_QUALITIES).find(id => canMake(id, orientation)) || savedQuality
  const noFormat = !!support && !Object.keys(SHARE_QUALITIES).some(id => canMake(id, orientation))

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
      toastError("Couldn't copy the link")
    }
  }, [trackId, getShareLink, toastError])

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
        toastError('Sharing failed, copy the link instead')
      }
    }
  }, [currentTrack, trackTitle, trackByline, getShareLink, toastError])

  const handleGenerateVideo = useCallback(() => {
    if (!currentTrack) return
    setShareError(null)
    setJobsPanelOpen(true)
    void shareVideoJob.start(currentTrack, { includeClips: includeVideoClips, quality, orientation })
  }, [currentTrack, includeVideoClips, quality, orientation, setJobsPanelOpen])

  const chooseOrientation = useCallback((shape) => {
    triggerHaptic('light')
    setOrientation(shape)
  }, [])

  const chooseQuality = useCallback((id) => {
    triggerHaptic('light')
    publishSettings({ shareVideoQuality: id })
  }, [publishSettings])

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
      toastError("Couldn't copy the video link")
    }
  }, [publishedVideoUrl, toastError])

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Share Track"
      maxWidth="max-w-md"
    >
      <div>
        <ModalSection className="flex items-center gap-4">
          {currentTrack?.has_artwork && (
            <div className="relative w-16 h-16 rounded-lg overflow-hidden flex-shrink-0">
              <TrackArt trackId={trackId} alt={trackTitle} />
            </div>
          )}
          <div className="flex-1 min-w-0">
            <div className="font-semibold text-white truncate">{trackTitle}</div>
            <div className="text-sm text-gray-400 truncate">{trackArtist}</div>
          </div>
        </ModalSection>

        <ModalSection title="Track link">
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
        </ModalSection>

        <ModalSection title="Share as video">

          <FadeSwap swapKey={isRendering ? 'rendering' : status} mode="wait" preset={PRESETS.stepSwap}>
            {status === 'idle' && (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-2">
                  {ORIENTATION_CHOICES.map(choice => (
                    <OptionCard
                      key={choice.id}
                      icon={choice.icon}
                      title={choice.title}
                      subtitle={choice.subtitle}
                      selected={orientation === choice.id}
                      onSelect={() => chooseOrientation(choice.id)}
                    />
                  ))}
                </div>
                <div className="grid grid-cols-1 gap-2">
                  {QUALITY_CHOICES.map(choice => {
                    const available = canMake(choice.id, orientation)
                    return (
                      <OptionCard
                        key={choice.id}
                        icon={choice.icon}
                        title={choice.title}
                        subtitle={available ? choice.subtitle : NOT_ON_DEVICE}
                        selected={quality === choice.id}
                        disabled={!available}
                        onSelect={() => chooseQuality(choice.id)}
                      />
                    )
                  })}
                </div>
                {noFormat && (
                  <InlineNote tone="warning">This browser can&apos;t make videos. Share the track link instead.</InlineNote>
                )}
                <ModalButton
                  onClick={handleGenerateVideo}
                  disabled={!currentTrack || otherTrackRendering || noFormat}
                  className="w-full flex items-center justify-center gap-2"
                >
                  <Video className="w-5 h-5" />
                  Generate video{includeVideoClips ? ' + clips' : ''}
                </ModalButton>
                {otherTrackRendering && (
                  <InlineNote tone="info">A video of “{jobTitle}” is rendering. This one can start when it is done.</InlineNote>
                )}
              </div>
            )}

            {isRendering && (
              <div className="space-y-3">
                <ModalProgress
                  progress={jobProgress}
                  statusText={[jobStage || 'Rendering...', formatTimeLeft(jobEtaSeconds)].filter(Boolean).join(' · ')}
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
                <div className={`relative ${jobOrientation === 'landscape' ? 'aspect-video w-full' : 'aspect-[9/16] max-h-64'} mx-auto rounded-lg overflow-hidden bg-black`}>
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
        </ModalSection>
      </div>
    </Modal>
  )
}
