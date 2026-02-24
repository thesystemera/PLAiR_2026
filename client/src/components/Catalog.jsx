import { ListPlus } from 'lucide-react'
import { motion } from 'framer-motion'
import { memo, useCallback, useState, useEffect, useRef, useMemo } from 'react'
import { useArtwork } from '../contexts/UIStateContext'
import { formatDateShort } from '../lib/utils'
import { FALLBACK_GRADIENTS } from '../lib/themeManager'
import { triggerHaptic } from '../lib/haptics'
import MediaActions from './MediaActions'
import { CatalogHeader } from './MediaStatsOverlay'
import { useDynamicTheme, GenreIcon, CatalogIcon } from '../contexts/DynamicThemeContext'
import { usePointerInteraction } from '../hooks/usePointerInteraction'
import { MemoizedVirtualScroller as VirtualScroller } from './VirtualScroller'
import { MediaSearchMatchBadge } from './MediaSearchMatchBadge'
import { useUIState } from '../contexts/UIStateContext'
import { usePlayback } from '../contexts/PlaybackContext'
import { MediaSearch } from './MediaSearch'
import { Scroller } from './Scroller'
import { api } from '../lib/api'
import { logger } from '../lib/logger'
import { retryableAPICall } from '../lib/retryUtils'
import { useVirtualWindow } from '../hooks/useVirtualWindow'
import { useGenerationQueue } from '../contexts/GenerationQueueContext'
import { MediaLoadingSpinner, MediaEmptyState, MediaPlayingOverlay, MediaStatusBadge, MediaCardAnimation, MediaGrid, useMediaSearch, MediaCardDurationBar, MediaCardActionButton, MediaCardTags, MediaCardMetadata } from './MediaShared'
import { useViewport } from '../contexts/ViewportContext'

export function createCatalogScrollLabel(tracks, sortMode, options = {}) {
  const { totalCount = 0, windowStart = 0, isVirtual = false, itemsPerRow = 2, itemHeight = 320 } = options

  if (!tracks || tracks.length === 0) return () => null

  return (scrollTop, scrollHeight, clientHeight) => {
    const scrollableHeight = scrollHeight - clientHeight
    if (scrollableHeight <= 0) return null

    let visibleStartIndex, visibleEndIndex

    if (isVirtual && totalCount > 0) {
      const currentRow = Math.floor(scrollTop / itemHeight)
      const absoluteIndex = currentRow * itemsPerRow
      const indexInWindow = absoluteIndex - windowStart

      if (indexInWindow < 0) {
        visibleStartIndex = 0
        visibleEndIndex = Math.min(tracks.length - 1, 9)
      } else if (indexInWindow >= tracks.length) {
        visibleEndIndex = tracks.length - 1
        visibleStartIndex = Math.max(0, visibleEndIndex - 9)
      } else {
        visibleStartIndex = Math.max(0, Math.min(indexInWindow, tracks.length - 1))
        const windowSize = Math.min(10, Math.ceil(tracks.length * 0.05))
        visibleEndIndex = Math.min(visibleStartIndex + windowSize, tracks.length - 1)
      }
    } else {
      const scrollPercent = scrollTop / scrollableHeight
      const estimatedIndex = Math.floor(scrollPercent * tracks.length)
      visibleStartIndex = Math.max(0, Math.min(estimatedIndex, tracks.length - 1))
      const windowSize = Math.min(10, Math.ceil(tracks.length * 0.05))
      visibleEndIndex = Math.min(visibleStartIndex + windowSize, tracks.length - 1)
    }

    const startTrack = tracks[visibleStartIndex]
    const endTrack = tracks[visibleEndIndex]

    if (!startTrack || !endTrack) return null

    if (sortMode === 'alphabetical') {
      const startTitle = startTrack.generation_params?.title || 'Unknown'
      const endTitle = endTrack.generation_params?.title || 'Unknown'
      const startLetter = startTitle.charAt(0).toUpperCase()
      const endLetter = endTitle.charAt(0).toUpperCase()
      return startLetter === endLetter ? startLetter : `${startLetter} - ${endLetter}`
    } else if (sortMode === 'genre') {
      const startGenre = startTrack.derived_tags?.primary_genre || 'Unknown'
      const endGenre = endTrack.derived_tags?.primary_genre || 'Unknown'
      return startGenre === endGenre ? startGenre : `${startGenre} - ${endGenre}`
    } else {
      const startDate = formatDateShort(startTrack.created_at)
      const endDate = formatDateShort(endTrack.created_at)
      return startDate === endDate ? startDate : `${startDate} - ${endDate}`
    }
  }
}

const TrackCard = memo(function TrackCard({ track, isPlaying, isQueued, onPlayNow, onSeedFromTrack, index, shouldAnimate, selectedGenre = null }) {
  const { getWhite, triggerEffect } = useDynamicTheme()
  const { radioState } = useUIState()
  const params = track.generation_params || {}
  const trackInfo = track.track_info || {}
  const [isLoading, setIsLoading] = useState(false)
  const cardInteraction = usePointerInteraction()
  const artworkUrl = useArtwork(track.id, track.has_artwork)

  useEffect(() => {
    if (isPlaying) queueMicrotask(() => setIsLoading(false))
  }, [isPlaying])

  const isNew = useCallback(() => {
    if (!track.created_at) return false
    const createdTime = new Date(track.created_at).getTime()
    const now = Date.now()
    const thirtyMinutes = 30 * 60 * 1000
    return (now - createdTime) < thirtyMinutes
  }, [track.created_at])

  const handlePlayNow = useCallback((e) => {
    e?.stopPropagation()
    e?.preventDefault()
    if (!cardInteraction.shouldTrigger()) return

    if (e.clientX !== undefined && e.clientY !== undefined) {
      triggerEffect('click', {
        x: e.clientX / window.innerWidth,
        y: e.clientY / window.innerHeight,
        intensity: 1.0
      })
    }

    triggerHaptic('strong')
    setIsLoading(true)
    onPlayNow(track.id)
  }, [track.id, onPlayNow, cardInteraction, triggerEffect])

  const handleSeedFromTrack = useCallback((e) => {
    e?.stopPropagation()
    e?.preventDefault()
    if (!cardInteraction.shouldTrigger()) return
    const seedMode = radioState.activeSeedMode || 'all'
    triggerHaptic('success')
    onSeedFromTrack(track.id, seedMode)
  }, [track.id, radioState.activeSeedMode, onSeedFromTrack, cardInteraction])

  return (
    <MediaCardAnimation
      index={index}
      shouldAnimate={shouldAnimate}
      className={`bg-dark-card rounded-lg p-2 md:p-4 hover:bg-dark-hover active:bg-dark-card transition-all duration-200 group/card cursor-pointer ${
        isPlaying ? 'ring-2 ring-purple-500 shadow-lg shadow-purple-500/20' : ''
      } ${isQueued && !isPlaying ? 'ring-1 ring-blue-400/40' : ''}`}
      onPointerDown={cardInteraction.onPointerDown}
      onPointerMove={cardInteraction.onPointerMove}
      onPointerUp={handlePlayNow}
    >
      <div className="aspect-square bg-gradient-to-br from-white/[0.06] to-white/[0.03] rounded-lg mb-2 flex items-center justify-center relative overflow-hidden">
        <img
          src={artworkUrl}
          alt={params.title || 'Track artwork'}
          className="absolute inset-0 w-full h-full object-cover"
          onError={(e) => { e.target.style.display = 'none' }}
        />
        {isPlaying && <MediaPlayingOverlay />}

        <div className="absolute left-2 z-20 top-2">
          <MediaActions type="track" itemId={track.id} compact />
        </div>

        <div className="absolute top-2 right-2 z-30 flex items-center gap-2 pointer-events-none">
          <MediaCardActionButton
            icon={ListPlus}
            onClick={handleSeedFromTrack}
            title={radioState.activeSeedMode ? `Seed ${radioState.activeSeedMode} playlist from this track` : "Seed playlist from this track"}
            variant="seed"
            className="pointer-events-auto"
          />
        </div>

        {isNew() && (
          <div className="absolute inset-0 flex items-center justify-center z-40 pointer-events-none">
            <MediaStatusBadge variant="new" className="text-[10px] font-bold shadow-lg" />
          </div>
        )}

        <MediaCardDurationBar duration={trackInfo.duration || 0} />

        {isLoading && !isPlaying && (
          <div className="absolute bottom-2 right-2 px-2 py-1 z-40">
            <motion.div
              animate={{ rotate: 360 }}
              transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
              className="w-4 h-4 md:w-5 md:h-5 border-2 border-purple-500 border-t-transparent rounded-full"
            />
          </div>
        )}
        {isPlaying && (
          <div className="absolute bottom-2 right-2 z-30">
            <MediaStatusBadge variant="playing" />
          </div>
        )}
        {!isPlaying && isQueued && (
          <div className="absolute bottom-2 right-2 z-30">
            <MediaStatusBadge variant="queued" />
          </div>
        )}
      </div>

      <MediaSearchMatchBadge
        similarityScore={track.similarity_score}
        intentCategory={track.intent_category}
        matchWeights={track.match_weights}
      />

      <h3 className="font-semibold truncate text-xs md:text-sm transition-colors duration-700" style={{ color: getWhite() }}>
        {params.title || 'Untitled'}
      </h3>

      {selectedGenre && track.derived_tags?.secondary_genres ? (
        <MediaCardTags tags={track.derived_tags.secondary_genres} maxDisplay={2} />
      ) : (
        <MediaCardMetadata text={params.style_canonical || params.style || 'No style'} />
      )}
    </MediaCardAnimation>
  )
}, (prev, next) => {
  return (
    prev.track.id === next.track.id &&
    prev.isPlaying === next.isPlaying &&
    prev.isQueued === next.isQueued &&
    prev.selectedGenre === next.selectedGenre
  )
})

const GenreCard = memo(function GenreCard({ genre, count, subGenres, onSelectGenre, index }) {
  const { getWhite, getGrey400 } = useDynamicTheme()
  const gradientIndex = index % 5

  return (
    <MediaCardAnimation
      index={index}
      className="bg-dark-card rounded-lg p-4 hover:bg-dark-hover active:bg-dark-card transition-all duration-200 cursor-pointer group/card"
      onClick={() => onSelectGenre(genre)}
    >
      <div className={`aspect-square bg-gradient-to-br ${FALLBACK_GRADIENTS[gradientIndex]} opacity-80 rounded-lg mb-3 flex items-center justify-center relative overflow-hidden group-hover/card:scale-105 transition-transform duration-200`}>
        <GenreIcon className="w-12 h-12 md:w-16 md:h-16 text-white opacity-90" />
        <div className="absolute bottom-2 right-2 px-2 py-1 bg-black/50 backdrop-blur-sm rounded-full text-xs font-bold text-white">
          {count}
        </div>
      </div>
      <h3 className="font-bold text-sm md:text-base mb-2 transition-colors duration-700" style={{ color: getWhite() }}>
        {genre}
      </h3>
      <div className="flex flex-wrap gap-1">
        {subGenres.slice(0, 3).map((sub, i) => (
          <span key={i} className="text-[10px] px-2 py-0.5 rounded-full bg-white/10 transition-colors duration-700" style={{ color: getGrey400() }}>
            {sub}
          </span>
        ))}
        {subGenres.length > 3 && (
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-white/10 transition-colors duration-700" style={{ color: getGrey400() }}>
            +{subGenres.length - 3}
          </span>
        )}
      </div>
    </MediaCardAnimation>
  )
})

const ITEMS_PER_ROW = 2
const BUFFER_ITEMS = 50
const LOAD_THRESHOLD = 30

function CatalogComponent({ onPlayNow, onSeedFromTrack, contentUpdateCounter = 0, onToggleView, currentView }) {
  const playback = usePlayback()
  const { engineState } = useUIState()
  const currentTrackId = engineState.currentTrack?.id
  const queuedTrackIds = useMemo(() => engineState.queue.map(t => t.id), [engineState.queue])

  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [sortMode, setSortMode] = useState('recent')
  const [genres, setGenres] = useState([])
  const [selectedGenre, setSelectedGenre] = useState(null)
  const scrollContainerRef = useRef(null)
  const lastHandledCounterRef = useRef(0)

  const { isMobile } = useViewport()
  const [measuredRowHeight, setMeasuredRowHeight] = useState(isMobile ? 308 : 344)
  const measurementRef = useRef(null)

  const { toastInfo, toastError, audioState } = useUIState()
  const { togglePanel: toggleQueuePanel, addJob } = useGenerationQueue()
  const { queueState } = useUIState()

  const info = toastInfo
  const errorToast = toastError
  const hasActiveJobs = queueState.hasActiveJobs

  const {
    isSearchMode,
    searchResults: searchTracks,
    searchIntent,
    handleSearch: performSearch,
    handleClearSearch: clearSearch
  } = useMediaSearch(
    useCallback(async (query, isManual = false, autoPlayTop = false) => {
      const result = await api.searchSemantic(query, 50, isManual)
      if (isManual && result?.results) {
        info(`Found ${result.results.length} tracks matching "${query}"`, 4000, 'bottom', 'search')
      }
      if (autoPlayTop && result?.results?.length > 0) {
        try {
          await playback.playTrack(result.results[0].id)
        } catch (playError) {
          logger.error('Auto-play failed:', playError)
          errorToast('Found tracks but playback failed', 3000, 'bottom', 'playback')
        }
      } else if (autoPlayTop && result?.results?.length === 0) {
        info('No tracks found for your voice search', 3000, 'bottom', 'search')
      }
      return result
    }, [info, errorToast, playback]),
    {
      onError: (error) => {
        logger.error('Search failed:', error)
        errorToast('Search failed. Please try again.', 5000, 'bottom', 'search')
      }
    }
  )

  const getSortParams = useCallback((mode) => {
    if (mode === 'recent') return { sort_by: 'created_at', order: 'desc' }
    if (mode === 'alphabetical') return { sort_by: 'title', order: 'asc' }
    if (mode === 'genre') return { sort_by: 'genre', order: 'asc' }
    return { sort_by: 'created_at', order: 'desc' }
  }, [])

  const catalogWindow = useVirtualWindow({
    fetchFn: useCallback(async (skip, limit, params) => {
      const sortParams = getSortParams(params.sortMode || 'recent')
      const data = await api.getTracks(skip, limit, sortParams.sort_by, sortParams.order, params.genre)
      return {
        items: data?.tracks || [],
        total: data?.total_tracks || 0
      }
    }, [getSortParams]),
    windowSize: 150,
    bufferSize: 10
  })

  useEffect(() => {
    const loadData = async () => {
      logger.info(`[Catalog] loadData triggered - sortMode: ${sortMode}, isOnline: ${audioState.isOnline}, selectedGenre: ${selectedGenre}`)
      setLoading(true)
      try {
        if (sortMode === 'genre' && !selectedGenre) {
          const [statsData, genresData] = await Promise.all([
            retryableAPICall(() => api.getStats(), 'getStats'),
            retryableAPICall(() => api.getGenres(), 'getGenres'),
          ])
          setStats(statsData)
          setGenres(genresData?.genres || [])
        } else {
          const statsData = await retryableAPICall(() => api.getStats(selectedGenre), 'getStats')
          setStats(statsData)
          await catalogWindow.reset({ sortMode, genre: selectedGenre })

          if (sortMode === 'genre' && !selectedGenre) {
            const genresData = await retryableAPICall(() => api.getGenres(), 'getGenres')
            setGenres(genresData?.genres || [])
          }
        }
      } catch (_error) {
        logger.error('[Catalog] Failed to load data after retries:', _error)
        errorToast('Failed to load tracks. Please check your connection and try again.')
      } finally {
        setLoading(false)
      }
    }
    void loadData()
  }, [sortMode, selectedGenre, audioState.isOnline])

  const sortModeRef = useRef(sortMode)
  const selectedGenreRef = useRef(selectedGenre)
  useEffect(() => {
    sortModeRef.current = sortMode
    selectedGenreRef.current = selectedGenre
  }, [sortMode, selectedGenre])

  const reloadCatalog = useCallback(async () => {
    try {
      const currentSortMode = sortModeRef.current || sortMode
      const currentGenre = selectedGenreRef.current || selectedGenre

      if (currentSortMode === 'genre' && !currentGenre) {
        const [statsData, genresData] = await Promise.all([
          retryableAPICall(() => api.getStats(), 'getStats (reload)'),
          retryableAPICall(() => api.getGenres(), 'getGenres (reload)'),
        ])
        setStats(statsData)
        setGenres(genresData?.genres || [])
      } else {
        const [statsData] = await Promise.all([
          retryableAPICall(() => api.getStats(currentGenre), 'getStats (reload)'),
          catalogWindow.reset({ sortMode: currentSortMode, genre: currentGenre })
        ])
        setStats(statsData)
      }
    } catch (error) {
      logger.error('[Catalog] Failed to reload catalog after retries:', error)
    }
  }, [])

  useEffect(() => {
    if (contentUpdateCounter > 0 && contentUpdateCounter !== lastHandledCounterRef.current) {
      lastHandledCounterRef.current = contentUpdateCounter
      void reloadCatalog()
    }
  }, [contentUpdateCounter, reloadCatalog])

  const handleSearch = useCallback(async (query, isManual = false, autoPlayTop = false) => {
    setLoading(true)
    try {
      await performSearch(query, isManual, autoPlayTop)
    } finally {
      setLoading(false)
    }
  }, [performSearch])

  const handleClearSearch = useCallback(() => {
    clearSearch()
    void reloadCatalog()
  }, [clearSearch, reloadCatalog])

  const handleGenerate = useCallback(async (query) => {
    if (hasActiveJobs) {
      info('Please wait for current generation to complete', 3000, 'bottom')
      return
    }

    try {
      const response = await api.generate({
        type: 'new',
        userRequest: query,
        batchCount: 3
      })
      if (response?.job_ids && Array.isArray(response.job_ids)) {
        response.job_ids.forEach((jobId) => {
          addJob({
            job_id: jobId,
            type: 'generate',
            query: response.title || query,
            total_tracks: 2
          })
        })
      }
    } catch (error) {
      logger.error('Generation failed:', error)
      const errorMsg = error.message || 'Failed to start generation. Please try again.'
      if (errorMsg.includes('rate limit') || errorMsg.includes('cooldown')) {
        errorToast(errorMsg, 5000, 'bottom')
      } else if (errorMsg.includes('daily limit')) {
        errorToast(errorMsg, 7000, 'bottom')
      } else {
        errorToast('Failed to start generation. Please try again.', 5000, 'bottom')
      }
    }
  }, [addJob, errorToast, hasActiveJobs, info])

  const handleToggleQueue = useCallback(() => {
    toggleQueuePanel()
  }, [toggleQueuePanel])

  const handleSelectGenre = useCallback((genre) => {
    setLoading(true)
    setSelectedGenre(genre)
  }, [])

  const handleBackToGenres = useCallback(() => {
    setLoading(true)
    setSelectedGenre(null)
  }, [])

  const displayTracks = isSearchMode ? searchTracks : catalogWindow.items
  const queuedTrackSet = useMemo(() => new Set(queuedTrackIds), [queuedTrackIds])

  const trackRange = useMemo(() => {
    if (displayTracks.length === 0) return null
    const firstTrack = displayTracks[0]
    const lastTrack = displayTracks[displayTracks.length - 1]

    if (!firstTrack || !lastTrack) return null

    if (sortMode === 'alphabetical') {
      const firstName = firstTrack.generation_params?.title || 'Unknown'
      const lastName = lastTrack.generation_params?.title || 'Unknown'
      return `${firstName.charAt(0).toUpperCase()} - ${lastName.charAt(0).toUpperCase()}`
    } else if (sortMode === 'genre') {
      const firstGenre = firstTrack.derived_tags?.primary_genre || 'Unknown'
      const lastGenre = lastTrack.derived_tags?.primary_genre || 'Unknown'
      return firstGenre === lastGenre ? firstGenre : `${firstGenre} - ${lastGenre}`
    } else {
      return `${formatDateShort(lastTrack.created_at)} - ${formatDateShort(firstTrack.created_at)}`
    }
  }, [displayTracks, sortMode])

  const subGenres = useMemo(() => {
    if (!selectedGenre || sortMode !== 'genre') return []
    const genresSet = new Set()
    displayTracks.forEach(track => {
      if (!track) return
      const secondaryGenres = track.derived_tags?.secondary_genres || []
      secondaryGenres.forEach(g => genresSet.add(g))
    })
    return Array.from(genresSet).slice(0, 5)
  }, [displayTracks, selectedGenre, sortMode])

  const totalSubGenreCount = useMemo(() => {
    if (sortMode !== 'genre' || selectedGenre) return 0
    const allSubGenres = new Set()
    genres.forEach(genreData => {
      genreData.sub_genres?.forEach(sub => allSubGenres.add(sub))
    })
    return allSubGenres.size
  }, [genres, sortMode, selectedGenre])

  useEffect(() => {
    if (!measurementRef.current) return

    const firstRow = measurementRef.current.querySelector('.grid > :first-child')
    if (!firstRow) return

    const observer = new ResizeObserver(() => {
      const gridContainer = measurementRef.current?.querySelector('.grid')
      if (!gridContainer) return

      const firstCard = gridContainer.children[0]
      const thirdCard = gridContainer.children[2]

      if (firstCard && thirdCard) {
        const firstRect = firstCard.getBoundingClientRect()
        const thirdRect = thirdCard.getBoundingClientRect()
        const actualRowHeight = thirdRect.top - firstRect.top

        if (actualRowHeight > 0 && Math.abs(actualRowHeight - measuredRowHeight) > 2) {
          setMeasuredRowHeight(actualRowHeight)
        }
      }
    })

    observer.observe(firstRow)
    return () => observer.disconnect()
  }, [displayTracks.length])

  const catalogScrollLabel = useMemo(
    () => createCatalogScrollLabel(displayTracks, sortMode, {
      totalCount: catalogWindow.totalCount,
      windowStart: catalogWindow.windowStart,
      isVirtual: !isSearchMode,
      itemsPerRow: 2,
      itemHeight: measuredRowHeight
    }),
    [displayTracks, sortMode, catalogWindow.totalCount, catalogWindow.windowStart, isSearchMode, measuredRowHeight]
  )

  const renderTrack = useCallback((track, absoluteIndex) => (
    <TrackCard
      key={track.id}
      track={track}
      isPlaying={track.id === currentTrackId}
      isQueued={queuedTrackSet.has(track.id)}
      onPlayNow={onPlayNow}
      onSeedFromTrack={onSeedFromTrack}
      index={absoluteIndex}
      shouldAnimate={false}
      selectedGenre={selectedGenre}
    />
  ), [currentTrackId, queuedTrackSet, onPlayNow, onSeedFromTrack, selectedGenre])

  const renderPlaceholder = useCallback((absoluteIndex) => (
    <div
      key={`skeleton-${absoluteIndex}`}
      className="bg-dark-card rounded-lg p-2 md:p-4"
    >
      <div className="aspect-square bg-white/[0.06] rounded-lg mb-2 flex items-center justify-center relative overflow-hidden">
        <div className="w-full h-full bg-white/[0.04] animate-pulse ring-1 ring-white/10" />
      </div>
      <div className="h-4 md:h-5 bg-white/[0.06] rounded mb-2 w-3/4 animate-pulse" />
      <div className="h-3 md:h-4 bg-white/[0.06] rounded w-1/2 animate-pulse" />
    </div>
  ), [])

  const adaptiveStats = {
    ...stats,
    displayed_tracks: sortMode === 'genre' && !selectedGenre ? genres.length : stats?.total_tracks,
    is_search_mode: isSearchMode,
    search_result_count: searchTracks.length,
    track_range: trackRange,
    current_skip: 0,
    visible_end: displayTracks.length,
    showing_genres: sortMode === 'genre' && !selectedGenre,
    genre_count: genres.length,
    selected_genre: selectedGenre,
    sub_genres: subGenres,
    total_subgenre_count: totalSubGenreCount
  }

  const showingGenres = sortMode === 'genre' && !selectedGenre && genres.length > 0

  // Only show full-screen spinner on initial load (no items yet), not during virtual scroll
  // VirtualScroller handles its own loading states via renderPlaceholder
  const isLoadingContent = loading && catalogWindow.items.length === 0

  return (
    <div className="flex flex-col h-full relative">
      <MediaSearch
        type="track"
        onSearch={handleSearch}
        onClear={handleClearSearch}
        onGenerate={handleGenerate}
        onToggleQueue={handleToggleQueue}
        audio={playback?.audio}
        searchIntent={searchIntent}
        isSearchMode={isSearchMode}
        onToggleView={onToggleView}
        currentView={currentView}
      />
      <Scroller ref={scrollContainerRef} className="flex-1 relative" getScrollLabel={catalogScrollLabel}>
        {isLoadingContent ? (
          <MediaLoadingSpinner type="track" sortMode={sortMode} offsetHeader />
        ) : showingGenres ? (
          <div className="relative">
            <CatalogHeader
              stats={adaptiveStats}
              sortMode={sortMode}
              onSortChange={setSortMode}
              selectedGenre={selectedGenre}
              onBackToGenres={handleBackToGenres}
              contentType="catalog"
            />
            <MediaGrid>
              {genres.map((genreData, index) => (
                <GenreCard
                  key={genreData.genre}
                  genre={genreData.genre}
                  count={genreData.count}
                  subGenres={genreData.sub_genres}
                  onSelectGenre={handleSelectGenre}
                  index={index}
                />
              ))}
            </MediaGrid>
          </div>
        ) : displayTracks.length === 0 ? (
          <div className="relative">
            <CatalogHeader
              stats={adaptiveStats}
              sortMode={sortMode}
              onSortChange={setSortMode}
              selectedGenre={selectedGenre}
              onBackToGenres={handleBackToGenres}
              contentType="catalog"
            />
            <MediaEmptyState
              icon={CatalogIcon}
              title="No tracks found"
              subtitle="Try adjusting your search or browse the catalog"
            />
          </div>
        ) : (
          <div ref={measurementRef} className="relative">
            <CatalogHeader
              stats={adaptiveStats}
              sortMode={sortMode}
              onSortChange={setSortMode}
              selectedGenre={selectedGenre}
              onBackToGenres={handleBackToGenres}
              contentType="catalog"
            />
            <VirtualScroller
              items={displayTracks}
              totalCount={isSearchMode ? displayTracks.length : catalogWindow.totalCount}
              windowStart={isSearchMode ? 0 : catalogWindow.windowStart}
              itemHeight={measuredRowHeight}
              itemsPerRow={ITEMS_PER_ROW}
              renderItem={renderTrack}
              renderPlaceholder={renderPlaceholder}
              onLoadForward={!isSearchMode ? catalogWindow.loadForward : undefined}
              onLoadBackward={!isSearchMode ? catalogWindow.loadBackward : undefined}
              hasMore={catalogWindow.hasMore}
              loadThreshold={LOAD_THRESHOLD}
              bufferItems={BUFFER_ITEMS}
              scrollContainerRef={scrollContainerRef}
              className="grid grid-cols-2 md:grid-cols-2 lg:grid-cols-2 gap-3 md:gap-4 px-3 md:px-6"
            />
          </div>
        )}
      </Scroller>
    </div>
  )
}

export const Catalog = memo(CatalogComponent)