import {logger} from '../lib/logger'
import {memo, useEffect, useRef, useState} from 'react'
import {useArtwork, useUIState} from '../contexts/UIStateContext'
import MediaActions from './MediaActions'
import {PanelHeader} from './Panel'
import {useGenerationQueue} from '../contexts/GenerationQueueContext'
import {BUTTON} from '../lib/themeManager.js'
import {Scroller} from './Scroller'
import {ParallaxArtwork} from './ParallaxArtwork'
import {api} from '../lib/api'

const SyncedLyrics = memo(function SyncedLyrics({ lyricTimestamps }) {
  const { engineState, engineRef, isScreenVisible } = useUIState()
  const isPlaying = engineState.is_playing

  const lyrics = lyricTimestamps?.lyrics || []
  const wordRefs = useRef(new Map())
  const containerRef = useRef(null)
  const [isVisible, setIsVisible] = useState(true)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const observer = new IntersectionObserver(
      ([entry]) => {
        setIsVisible(entry.isIntersecting)
      },
      { threshold: 0.1 }
    )

    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  const currentProgressRef = useRef(0)

  useEffect(() => {
    if (!lyrics.length || !isVisible || !isScreenVisible) return

    const updateLyrics = () => {
      const currentProgress = engineRef.current.progress_ms || 0
      const currentTimeSec = currentProgress / 1000

      lyrics.forEach((line, lineIndex) => {
        if (line.words) {
          line.words.forEach((word, wordIndex) => {
            const key = `${lineIndex}-${wordIndex}`
            const node = wordRefs.current.get(key)

            if (node) {
               const isActive = currentTimeSec >= word.start && currentTimeSec < word.end
               const isPast = currentTimeSec > line.end

               if (isActive) {
                 if (!node.classList.contains('text-white')) {
                   node.className = 'px-1 rounded transition-colors duration-150 text-white font-bold bg-white/20'
                 }
               } else if (isPast) {
                 if (!node.classList.contains('text-gray-500')) {
                   node.className = 'px-1 rounded transition-colors duration-150 text-gray-500'
                 }
               } else {
                  const isLineCurr = currentTimeSec >= line.start && currentTimeSec < line.end
                  if (isLineCurr) {
                      if (!node.classList.contains('text-gray-300')) {
                        node.className = 'px-1 rounded transition-colors duration-150 text-gray-300'
                      }
                  } else {
                      if (!node.classList.contains('text-gray-400')) {
                        node.className = 'px-1 rounded transition-colors duration-150 text-gray-400'
                      }
                  }
               }
            }
          })
        }
      })
    }

    updateLyrics()
    const intervalId = setInterval(updateLyrics, 100)

    return () => clearInterval(intervalId)
  }, [lyrics, isPlaying, isVisible, isScreenVisible])

  if (!lyricTimestamps || lyrics.length === 0) {
    return null
  }

  return (
    <div className="bg-white/5 p-4 rounded-lg mb-6" ref={containerRef}>
      <div className="text-xs text-gray-400 mb-2">Lyrics</div>
      <div className="space-y-1">
        {lyrics.map((line, lineIndex) => (
          <div key={lineIndex} className="transition-all duration-200">
            <div className="flex flex-wrap gap-x-1">
              {line.words && line.words.length > 0 ? (
                line.words.map((word, wordIndex) => (
                  <span
                    key={wordIndex}
                    ref={(el) => {
                      if (el) wordRefs.current.set(`${lineIndex}-${wordIndex}`, el)
                      else wordRefs.current.delete(`${lineIndex}-${wordIndex}`)
                    }}
                    className="px-1 rounded transition-colors duration-150 text-gray-400"
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
                className={`h-full bg-gradient-to-r ${feature.color} transition-all duration-300`}
                style={{ width: `${feature.value * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
})

export const NowPlaying = memo(function NowPlaying({ track, onToggleFullscreen, onOpenGenerationModal, onOpenShareModal }) {
  const [layerA, setLayerA] = useState(null)
  const [layerB, setLayerB] = useState(null)
  const [frontLayer, setFrontLayer] = useState('A')
  const [analytics, setAnalytics] = useState(null)
  const previousArtworkUrlRef = useRef(null)

  const { togglePanel: toggleQueuePanel } = useGenerationQueue()
  const { audioFeatures, lyricTimestamps, queueState, interfaceState } = useUIState()
  const isFullscreen = interfaceState.isFullscreenVisuals
  const hasActiveJobs = queueState.hasActiveJobs
  const artworkUrl = useArtwork(track?.id, track?.has_artwork)

  useEffect(() => {
    if (artworkUrl && artworkUrl !== previousArtworkUrlRef.current) {
      const newImage = {
        id: track?.id,
        url: artworkUrl,
        hasArtwork: track?.has_artwork,
        title: track?.generation_params?.title || 'Track artwork'
      }

      if (frontLayer === 'A') {
        queueMicrotask(() => setLayerB(newImage))
        setTimeout(() => setFrontLayer('B'), 50)
      } else {
        queueMicrotask(() => setLayerA(newImage))
        setTimeout(() => setFrontLayer('A'), 50)
      }

      previousArtworkUrlRef.current = artworkUrl
    }
  }, [artworkUrl, track?.id, track?.has_artwork, track?.generation_params?.title])

  useEffect(() => {
    if (!track?.id) return

    const fetchAnalytics = async () => {
      try {
        const data = await api.getTrackAnalytics(track.id)
        if (data) {
          setAnalytics(data)
        }
      } catch (error) {
        logger.error('Failed to fetch analytics:', error)
      }
    }

    void fetchAnalytics()
  }, [track?.id])

  if (!track) return null

  const params = track.generation_params || {}

  return (
    <div className="flex flex-col h-full relative">
      <PanelHeader title="Now Playing" />

      <Scroller className="flex-1 p-4 md:p-6">
        <div
          className="aspect-square w-full rounded-xl mb-4 flex items-center justify-center shadow-2xl overflow-hidden relative"
        >
          {layerA && (
            <div className={`absolute inset-0 transition-opacity duration-700 ${
              frontLayer === 'A' ? 'opacity-100' : 'opacity-0'
            }`}>
              <ParallaxArtwork
                trackId={layerA.id}
                artworkUrl={layerA.url}
                alt={layerA.title}
                className="w-full h-full"
                intensity={0.05}
                isActive={frontLayer === 'A'}
              />
            </div>
          )}

          {layerB && (
            <div className={`absolute inset-0 transition-opacity duration-700 ${
              frontLayer === 'B' ? 'opacity-100' : 'opacity-0'
            }`}>
              <ParallaxArtwork
                trackId={layerB.id}
                artworkUrl={layerB.url}
                alt={layerB.title}
                className="w-full h-full"
                intensity={0.05}
                isActive={frontLayer === 'B'}
              />
            </div>
          )}

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

        <div className="mb-6">
          <h1 className="text-2xl font-bold mb-2">{params.title || 'Untitled'}</h1>
          {params.artist_name && (
            <p className="text-lg text-gray-300 mb-1">{params.artist_name}</p>
          )}
          <p className="text-lg text-gray-400">{params.style_canonical || params.style || 'No style'}</p>
        </div>

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
      </Scroller>
    </div>
  )
})