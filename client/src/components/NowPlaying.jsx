import {logger} from '../lib/logger'
import {memo, useCallback, useEffect, useRef, useState} from 'react'
import { useUISelector } from '../contexts/UIStateContext'
import { usePlaybackActions } from '../contexts/PlaybackContext'
import MediaActions from './MediaActions'
import {PanelHeader} from './Panel'
import {useGenerationQueue} from '../contexts/GenerationQueueContext'
import {BUTTON} from '../lib/themeManager.js'
import {Scroller} from './Scroller'
import { TrackArt, TrackArtCrossfade } from './DepthArt'
import {api} from '../lib/api'
import {useViewport} from '../contexts/ViewportContext'
import {PANEL} from '../lib/themeManager'
import {artPop, watchOffscreen} from '../lib/microMotion'
import {ExternalLink, MessageSquareText, Music} from 'lucide-react'
import {AnimatePresence, motion} from 'framer-motion'
import {PRESETS} from '../lib/motion'

const NO_LYRICS = []
const NOW_PLAYING_INTENSITY = 0.05
const LYRIC_STATE_MARKERS = ['text-white', 'text-gray-500', 'text-gray-300', 'text-gray-400']
const LYRIC_STATE_CLASSES = [
  'px-1 rounded transition-colors duration-micro text-white font-bold bg-white/20',
  'px-1 rounded transition-colors duration-micro text-gray-500',
  'px-1 rounded transition-colors duration-micro text-gray-300',
  'px-1 rounded transition-colors duration-micro text-gray-400',
]

const SyncedLyrics = memo(function SyncedLyrics({ lyricTimestamps }) {
  const { engineRef, isScreenVisible } = useUISelector(state => ({
    engineRef: state.engineRef,
    isScreenVisible: state.isScreenVisible,
  }))

  const lyrics = lyricTimestamps?.lyrics || NO_LYRICS
  const wordRefs = useRef(new Map())
  const [container, setContainer] = useState(null)
  const [isVisible, setIsVisible] = useState(true)

  useEffect(() => {
    if (!container) return

    const observer = new IntersectionObserver(
      ([entry]) => {
        setIsVisible(entry.isIntersecting)
      },
      { threshold: 0.1 }
    )

    observer.observe(container)
    return () => observer.disconnect()
  }, [container])

  useEffect(() => {
    if (!lyrics.length || !isVisible || !isScreenVisible) return

    const entries = []
    lyrics.forEach((line, lineIndex) => {
      if (!line.words) return
      line.words.forEach((word, wordIndex) => {
        entries.push({ key: `${lineIndex}-${wordIndex}`, word, line, node: null, state: -1 })
      })
    })

    let lastTimeSec = -1

    const updateLyrics = () => {
      const currentTimeSec = (engineRef.current.progress_ms || 0) / 1000
      if (currentTimeSec === lastTimeSec) return
      lastTimeSec = currentTimeSec
      const nodes = wordRefs.current

      for (let i = 0; i < entries.length; i++) {
        const entry = entries[i]
        const node = nodes.get(entry.key)
        if (!node) continue
        const { word, line } = entry
        let state
        if (currentTimeSec >= word.start && currentTimeSec < word.end) state = 0
        else if (currentTimeSec > line.end) state = 1
        else if (currentTimeSec >= line.start && currentTimeSec < line.end) state = 2
        else state = 3
        if (entry.node === node && entry.state === state) continue
        entry.node = node
        entry.state = state
        if (!node.classList.contains(LYRIC_STATE_MARKERS[state])) node.className = LYRIC_STATE_CLASSES[state]
      }
    }

    updateLyrics()
    const intervalId = setInterval(updateLyrics, 100)

    return () => clearInterval(intervalId)
  }, [lyrics, isVisible, isScreenVisible, engineRef])

  if (!lyricTimestamps || lyrics.length === 0) {
    return null
  }

  return (
    <div className="bg-white/5 p-4 rounded-lg mb-6" ref={setContainer}>
      <div className="text-xs text-gray-400 mb-2">Lyrics</div>
      <div className="space-y-1">
        {lyrics.map((line, lineIndex) => (
          <div key={lineIndex}>
            <div className="flex flex-wrap gap-x-1">
              {line.words && line.words.length > 0 ? (
                line.words.map((word, wordIndex) => (
                  <span
                    key={wordIndex}
                    ref={(el) => {
                      if (el) wordRefs.current.set(`${lineIndex}-${wordIndex}`, el)
                      else wordRefs.current.delete(`${lineIndex}-${wordIndex}`)
                    }}
                    className="px-1 rounded transition-colors duration-micro text-gray-400"
                  >
                    {word.word}
                  </span>
                ))
              ) : (
                <span className="text-gray-400">{line.line}</span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
})

const TrackAudioFeatures = memo(function TrackAudioFeatures({ audioFeatures }) {
  if (!audioFeatures) return null

  const normalizedTempo = audioFeatures.tempo
    ? Math.max(0, Math.min(1, (audioFeatures.tempo - 40) / 160))
    : 0

  const normalizedLoudness = audioFeatures.overall_loudness !== undefined
    ? Math.max(0, Math.min(1, (audioFeatures.overall_loudness + 60) / 60))
    : 0

  const features = [
    { label: 'Energy', value: audioFeatures.energy ?? 0, color: 'from-red-500 to-orange-500' },
    { label: 'Danceability', value: audioFeatures.danceability ?? 0, color: 'from-purple-500 to-pink-500' },
    { label: 'Valence', value: audioFeatures.valence ?? 0, color: 'from-yellow-500 to-green-500' },
    { label: 'Speechiness', value: audioFeatures.speechiness ?? 0, color: 'from-blue-500 to-cyan-500' },
    { label: 'Acousticness', value: audioFeatures.acousticness ?? 0, color: 'from-green-500 to-teal-500' },
    { label: 'Instrumentalness', value: audioFeatures.instrumentalness ?? 0, color: 'from-indigo-500 to-purple-500' },
    {
      label: 'Tempo',
      value: normalizedTempo,
      color: 'from-orange-500 to-red-500',
      display: audioFeatures.tempo ? `${Math.round(audioFeatures.tempo)} BPM` : 'N/A'
    },
    {
      label: 'Loudness',
      value: normalizedLoudness,
      color: 'from-pink-500 to-rose-500',
      display: audioFeatures.overall_loudness !== undefined
        ? `${audioFeatures.overall_loudness.toFixed(1)} dB`
        : 'N/A'
    }
  ]

  return (
    <div className="bg-white/5 p-4 rounded-lg mb-6">
      <div className="text-xs text-gray-400 mb-3">Audio Features</div>
      <div className="space-y-3">
        {features.map((feature) => (
          <div key={feature.label}>
            <div className="flex justify-between items-center mb-1">
              <span className="text-xs text-gray-300">{feature.label}</span>
              <span className="text-xs text-gray-400">
                {feature.display || `${Math.round(feature.value * 100)}%`}
              </span>
            </div>
            <div className="w-full h-2 bg-white/10 rounded-full overflow-hidden">
              <div
                className={`h-full w-full origin-left bg-gradient-to-r ${feature.color} transition-transform duration-base`}
                style={{ transform: `scaleX(${feature.value})` }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
})

const artistCache = new Map()

const linkLabel = (url) => {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

const ArtistTrackRow = memo(function ArtistTrackRow({ item, onPlay }) {
  return (
    <button
      type="button"
      onClick={() => onPlay(item.id)}
      className="ui-press w-full flex items-center gap-3 p-2 rounded-lg hover:bg-white/5 transition-colors text-left"
    >
      <div className="relative w-10 h-10 rounded bg-white/10 flex items-center justify-center overflow-hidden flex-shrink-0">
        {item.has_artwork !== false ? <TrackArt trackId={item.id} hasArtwork={item.has_artwork} alt="" /> : <Music size={16} className="text-white/50" />}
      </div>
      <span className="text-sm truncate">{item.title || 'Untitled'}</span>
    </button>
  )
})

const ArtistInfo = memo(function ArtistInfo({ artistId, currentTrackId }) {
  const { playTrack } = usePlaybackActions()
  const [artist, setArtist] = useState(() => (artistId ? artistCache.get(artistId) || null : null))

  useEffect(() => {
    if (!artistId) return
    let cancelled = false
    api.getArtist(artistId)
      .then(data => {
        if (!data) return
        artistCache.set(artistId, data)
        if (!cancelled) setArtist(data)
      })
      .catch(error => logger.warn('Failed to load artist info:', error))
    return () => { cancelled = true }
  }, [artistId])

  const shown = artist && artist.id === artistId ? artist : (artistId ? artistCache.get(artistId) : null)
  if (!shown) return null
  const others = (shown.tracks || []).filter(item => item.id !== currentTrackId).slice(0, 5)
  const links = shown.links || []
  if (!shown.bio && links.length === 0 && others.length === 0) return null

  return (
    <div className="bg-white/5 p-4 rounded-lg mb-6">
      <div className="text-xs text-gray-400 mb-2">About {shown.name}</div>
      {shown.bio && <p className="text-sm text-gray-200 whitespace-pre-line break-words mb-3">{shown.bio}</p>}
      {links.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-3">
          {links.map(url => (
            <a
              key={url}
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="ui-press inline-flex items-center gap-1 px-3 py-1 bg-white/10 hover:bg-white/15 rounded-full text-xs text-gray-200 transition-colors"
            >
              {linkLabel(url)}
              <ExternalLink size={12} />
            </a>
          ))}
        </div>
      )}
      {others.length > 0 && (
        <>
          <div className="text-xs text-gray-400 mb-1">More from {shown.name}</div>
          <div>
            {others.map(item => <ArtistTrackRow key={item.id} item={item} onPlay={playTrack} />)}
          </div>
        </>
      )}
    </div>
  )
})

const OVERLAY_BUTTON_CLASS = `${BUTTON.overlay.base} ${BUTTON.overlay.padding.small} ${BUTTON.overlay.rounded} ${BUTTON.overlay.shadow} ${BUTTON.overlay.transition} ${BUTTON.overlay.disabled} text-white`

const reviewCounts = new Map()

const ReviewsButton = memo(function ReviewsButton({ track }) {
  const { reviewsUpdateCount, openReviewModal } = useUISelector(state => ({
    reviewsUpdateCount: state.contentUpdates.reviews,
    openReviewModal: state.openReviewModal,
  }))
  const trackId = track?.id
  const [reviewCount, setReviewCount] = useState(() => ({ trackId, count: reviewCounts.get(trackId) ?? 0 }))

  useEffect(() => {
    if (!trackId) return
    let cancelled = false
    api.getTrackReviews(trackId)
      .then(data => {
        const fetched = data?.count ?? data?.reviews?.length ?? 0
        reviewCounts.set(trackId, fetched)
        if (!cancelled) setReviewCount({ trackId, count: fetched })
      })
      .catch(error => logger.warn('Failed to fetch review count:', error))
    return () => { cancelled = true }
  }, [trackId, reviewsUpdateCount])

  const count = reviewCount.trackId === trackId ? reviewCount.count : (reviewCounts.get(trackId) ?? 0)
  const label = count > 0 ? `Reviews (${count})` : 'Reviews'

  return (
    <button
      onClick={(e) => {
        e.stopPropagation()
        openReviewModal(trackId, track)
      }}
      disabled={!trackId}
      className={`relative ${OVERLAY_BUTTON_CLASS}`}
      title={label}
      aria-label={label}
    >
      <MessageSquareText className={BUTTON.icon.small} />
      <AnimatePresence initial={false}>
        {count > 0 && (
          <motion.span
            key={trackId}
            {...PRESETS.pop}
            className="absolute -top-1.5 -right-1.5 min-w-[1.125rem] h-[1.125rem] px-1 rounded-full bg-pink-500 text-white text-[10px] font-bold leading-[1.125rem] text-center shadow pointer-events-none"
          >
            {count > 99 ? '99+' : count}
          </motion.span>
        )}
      </AnimatePresence>
    </button>
  )
})

export const NowPlaying = memo(function NowPlaying({ onToggleFullscreen, onOpenGenerationModal, onOpenShareModal }) {
  const [analytics, setAnalytics] = useState(null)
  const artBoxRef = useRef(null)
  const popArt = useCallback(() => artPop(artBoxRef.current, 'artPopLarge'), [])

  const { togglePanel: toggleQueuePanel } = useGenerationQueue()
  const {
    audioFeatures,
    lyricTimestamps,
    queueState,
    interfaceState,
    engineState,
  } = useUISelector(state => ({
    audioFeatures: state.audioFeatures,
    lyricTimestamps: state.lyricTimestamps,
    queueState: state.queueState,
    interfaceState: state.interfaceState,
    engineState: state.engineState,
  }))
  const { isMobile, isLandscape, isPhoneLandscape } = useViewport()
  const isSplit = isMobile && isLandscape
  const splitOffset = interfaceState.playerHeight + (isPhoneLandscape ? 0 : 64) + PANEL.headerHeight + 32
  const track = engineState.currentTrack
  const isFullscreen = interfaceState.isFullscreenVisuals
  const hasActiveJobs = queueState.hasActiveJobs
  const hasTrack = !!track
  useEffect(() => watchOffscreen(artBoxRef.current), [hasTrack])

  useEffect(() => {
    if (!track?.id) return

    let cancelled = false
    const fetchAnalytics = async () => {
      try {
        const data = await api.getTrackAnalytics(track.id)
        if (data && !cancelled) {
          setAnalytics(data)
        }
      } catch (error) {
        logger.error('Failed to fetch analytics:', error)
      }
    }

    void fetchAnalytics()
    return () => { cancelled = true }
  }, [track?.id])

  if (!track) {
    return (
      <div className="flex flex-col h-full">
        <PanelHeader title="Now Playing" />
        <div className="flex-1 flex items-center justify-center text-gray-400">
          No track playing
        </div>
      </div>
    )
  }

  const params = track.generation_params || {}

  return (
    <div className="flex flex-col h-full relative">
      <PanelHeader title="Now Playing" />

      <Scroller className="flex-1 p-4 md:p-6">
        <div className={isSplit ? 'flex items-start gap-5' : undefined}>
        <div
          className={isSplit ? 'np-split-art flex-shrink-0 sticky' : undefined}
          style={isSplit ? { '--np-offset': `calc(${splitOffset}px + var(--safe-top))`, top: 0 } : undefined}
        >
        <div
          ref={artBoxRef}
          className={`aspect-square w-full rounded-xl flex items-center justify-center shadow-2xl overflow-hidden relative ${isSplit ? '' : 'mb-4'}`}
        >
          <TrackArtCrossfade
            trackId={track.id}
            hasArtwork={track.has_artwork}
            alt={params.title || 'Track artwork'}
            intensity={NOW_PLAYING_INTENSITY}
            onShow={popArt}
          />

          <div className="absolute top-4 left-4 z-10">
            <button
              onClick={(e) => {
                e.stopPropagation()
                onToggleFullscreen()
              }}
              disabled={!track}
              className={`${BUTTON.overlay.base} ${BUTTON.overlay.padding.small} ${BUTTON.overlay.rounded} ${BUTTON.overlay.shadow} ${BUTTON.overlay.transition} ${BUTTON.overlay.disabled} text-white`}
              title={isFullscreen ? 'Exit fullscreen visuals' : 'Fullscreen visuals'}
            >
              {isFullscreen ? (
                <svg className={BUTTON.icon.small} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              ) : (
                <svg className={BUTTON.icon.small} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
                </svg>
              )}
            </button>
          </div>

          <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-10">
            <MediaActions type="track" itemId={track.id} overlay />
          </div>

          <div className="absolute bottom-4 left-4 z-10 pointer-events-none select-none">
            <div className={`
              px-2 py-1 rounded-sm text-[10px] font-bold tracking-wider uppercase
              border border-white/30 bg-black/40 backdrop-blur-sm
              ${track.is_ai_generated === false ? 'text-emerald-300/70' : 'text-purple-300/70'}
            `}>
              {track.is_ai_generated === false ? '100% Human' : '100% AI'}
            </div>
          </div>

          <div className="absolute top-4 right-4 flex gap-2 z-10">
            <ReviewsButton track={track} />
            <button
              onClick={(e) => {
                e.stopPropagation()
                onOpenShareModal?.(track)
              }}
              disabled={!track}
              className={`${BUTTON.overlay.base} ${BUTTON.overlay.padding.small} ${BUTTON.overlay.rounded} ${BUTTON.overlay.shadow} ${BUTTON.overlay.transition} ${BUTTON.overlay.disabled} text-white`}
              title="Share track"
            >
              <svg className={BUTTON.icon.small} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z" />
              </svg>
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation()
                if (hasActiveJobs) {
                  toggleQueuePanel()
                } else {
                  onOpenGenerationModal(track)
                }
              }}
              disabled={!track}
              className={`${BUTTON.overlay.base} ${BUTTON.overlay.padding.small} ${BUTTON.overlay.rounded} ${BUTTON.overlay.shadow} ${BUTTON.overlay.transition} ${BUTTON.overlay.disabled} text-white`}
              title={hasActiveJobs ? 'View generation queue' : 'Generate music'}
            >
              {hasActiveJobs ? (
                <svg className={`animate-spin ${BUTTON.icon.small}`} xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
              ) : (
                <svg className={BUTTON.icon.small} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
              )}
            </button>
          </div>
        </div>

        </div>
        <div className={isSplit ? 'flex-1 min-w-0' : undefined}>
        <div className="mb-6">
          <h1 className="text-2xl font-bold mb-2 break-words">{params.title || 'Untitled'}</h1>
          {params.artist_name && (
            <p className="text-lg text-gray-300 mb-1">{params.artist_name}</p>
          )}
          <p className="text-lg text-gray-400">{params.style_canonical || params.style || 'No style'}</p>
        </div>

        {track.artist_profile_id && <ArtistInfo artistId={track.artist_profile_id} currentTrackId={track.id} />}

        {analytics && analytics.total_plays > 0 && (
          <div className="bg-gradient-to-br from-purple-500/10 to-pink-500/10 border border-purple-500/20 p-4 rounded-lg mb-6">
            <div className="text-xs text-purple-300 mb-3 font-semibold">TRACK ANALYTICS</div>
            <div className="grid grid-cols-2 gap-3">
              <div className="bg-black/20 p-3 rounded-lg">
                <div className="text-xs text-gray-400 mb-1">Total Plays</div>
                <div className="font-bold text-lg text-purple-300">
                  {analytics.total_plays}
                </div>
                {analytics.unique_listeners > 0 && (
                  <div className="text-xs text-gray-500">
                    {analytics.unique_listeners} listener{analytics.unique_listeners !== 1 ? 's' : ''}
                  </div>
                )}
              </div>

              <div className="bg-black/20 p-3 rounded-lg">
                <div className="text-xs text-gray-400 mb-1">👍 Likes</div>
                <div className="font-bold text-lg text-green-400">
                  {analytics.likes || 0}
                </div>
              </div>

              <div className="bg-black/20 p-3 rounded-lg">
                <div className="text-xs text-gray-400 mb-1">⭐ Super Likes</div>
                <div className="font-bold text-lg text-yellow-400">
                  {analytics.superlikes || 0}
                </div>
              </div>

              <div className="bg-black/20 p-3 rounded-lg">
                <div className="text-xs text-gray-400 mb-1">🚫 Bans</div>
                <div className="font-bold text-lg text-red-400">
                  {analytics.bans || 0}
                </div>
              </div>

              {analytics.avg_completion_pct > 0 && (
                <div className="bg-black/20 p-3 rounded-lg">
                  <div className="text-xs text-gray-400 mb-1">Avg Completion</div>
                  <div className="font-bold text-lg text-blue-300">
                    {Math.round(analytics.avg_completion_pct)}%
                  </div>
                </div>
              )}

              {analytics.skip_count > 0 && (
                <div className="bg-black/20 p-3 rounded-lg">
                  <div className="text-xs text-gray-400 mb-1">Skips</div>
                  <div className="font-bold text-lg text-orange-300">
                    {analytics.skip_count}
                  </div>
                  <div className="text-xs text-gray-500">
                    {Math.round(analytics.skip_rate * 100)}% skip rate
                  </div>
                </div>
              )}

              {analytics.percentile !== undefined && (
                <div className="bg-black/20 p-3 rounded-lg col-span-2">
                  <div className="text-xs text-gray-400 mb-1">Popularity Ranking</div>
                  <div className="font-bold text-2xl bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent">
                    {analytics.percentile >= 95 ? '🔥 ' : analytics.percentile >= 75 ? '⭐ ' : ''}
                    Top {Math.round(100 - analytics.percentile)}%
                  </div>
                  <div className="text-xs text-gray-500 mt-1">
                    Score: {analytics.popularity_score.toFixed(1)}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        <TrackAudioFeatures audioFeatures={audioFeatures} />

        {track.derived_tags && (
          <>
            {track.derived_tags.primary_genre && (
              <div className="bg-white/5 p-4 rounded-lg mb-4">
                <div className="text-xs text-gray-400 mb-2">Genre</div>
                <div className="flex flex-wrap gap-2">
                  <span className="px-3 py-1 bg-purple-500/20 text-purple-300 rounded-full text-sm font-medium">
                    {track.derived_tags.primary_genre}
                  </span>
                  {track.derived_tags.secondary_genres?.map((genre, i) => (
                    <span key={i} className="px-3 py-1 bg-purple-500/10 text-purple-400 rounded-full text-sm">
                      {genre}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {track.derived_tags.inspired_artist && (
              <div className="bg-white/5 p-4 rounded-lg mb-4">
                <div className="text-xs text-gray-400 mb-2">Inspired By</div>
                <p className="text-sm font-medium">{track.derived_tags.inspired_artist}</p>
              </div>
            )}

            {track.derived_tags.mood_keywords && track.derived_tags.mood_keywords.length > 0 && (
              <div className="bg-white/5 p-4 rounded-lg mb-4">
                <div className="text-xs text-gray-400 mb-2">Mood</div>
                <div className="flex flex-wrap gap-2">
                  {track.derived_tags.mood_keywords.map((mood, i) => (
                    <span key={i} className="px-3 py-1 bg-blue-500/20 text-blue-300 rounded-full text-sm">
                      {mood}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {track.derived_tags.lyrical_interpretation && (
              <div className="bg-white/5 border-l-4 border-pink-500/50 p-4 rounded-lg mb-4">
                <div className="text-xs text-gray-400 mb-2">Theme</div>
                <p className="text-sm italic text-gray-200">{track.derived_tags.lyrical_interpretation}</p>
              </div>
            )}

            {track.derived_tags.vocal_style_keywords && track.derived_tags.vocal_style_keywords.length > 0 && (
              <div className="bg-white/5 p-4 rounded-lg mb-4">
                <div className="text-xs text-gray-400 mb-2">Vocal Production</div>
                <div className="flex flex-wrap gap-2">
                  {track.derived_tags.vocal_style_keywords.map((style, i) => (
                    <span key={i} className="px-2 py-1 bg-green-500/20 text-green-300 rounded text-xs">
                      {style}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {track.derived_tags.similar_artists && track.derived_tags.similar_artists.length > 0 && (
              <div className="bg-white/5 p-4 rounded-lg mb-4">
                <div className="text-xs text-gray-400 mb-2">Similar Artists</div>
                <div className="flex flex-wrap gap-2">
                  {track.derived_tags.similar_artists.map((artist, i) => (
                    <span key={i} className="px-3 py-1 bg-pink-500/20 text-pink-300 rounded-full text-sm">
                      {artist}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        {track.artwork_prompt && track.is_ai_generated === false && (
          <div className="bg-gradient-to-r from-amber-500/10 to-orange-500/10 border-l-4 border-amber-500/50 p-4 rounded-lg mb-4">
            <div className="text-xs text-amber-400/80 mb-2 flex items-center gap-2">
              <span>🎨</span>
              <span>AI Artwork Description</span>
            </div>
            <p className="text-sm text-gray-300 italic leading-relaxed">{track.artwork_prompt}</p>
          </div>
        )}

        {(() => {
          const videoTerms = track.video_search_terms || track.derived_tags?.video_search_terms
          if (!videoTerms || videoTerms.length === 0) return null
          return (
            <div className="bg-gradient-to-r from-cyan-500/10 to-blue-500/10 border-l-4 border-cyan-500/50 p-4 rounded-lg mb-4">
              <div className="text-xs text-cyan-400/80 mb-2 flex items-center gap-2">
                <span>🎬</span>
                <span>Video Search Terms</span>
              </div>
              <div className="flex flex-wrap gap-2">
                {videoTerms.map((term, i) => (
                  <span key={i} className="px-2 py-1 bg-cyan-500/20 text-cyan-300 rounded text-xs">
                    {term}
                  </span>
                ))}
              </div>
            </div>
          )
        })()}

        {!params.instrumental && (
          <SyncedLyrics lyricTimestamps={lyricTimestamps} />
        )}
        </div>
        </div>
      </Scroller>
    </div>
  )
})