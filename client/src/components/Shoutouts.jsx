import { useState, useEffect, useRef, useCallback, memo, useMemo } from 'react'
import { Trash2, MessageCircle } from 'lucide-react'
import { motion } from 'framer-motion'
import { Scroller } from './Scroller'
import { MediaSearch } from './MediaSearch'
import { MediaSearchMatchBadge } from './MediaSearchMatchBadge'
import { CatalogHeader } from './MediaStatsOverlay'
import MediaActions from './MediaActions'
import { useDynamicTheme, ShoutoutsIcon } from '../contexts/DynamicThemeContext'
import { usePointerInteraction } from '../hooks/usePointerInteraction'
import { useAuth } from '../contexts/AuthContext'
import { useUIState } from '../contexts/UIStateContext'
import { usePlaybackShoutout } from '../contexts/PlaybackShoutoutContext'
import { useProfilePicture } from '../hooks/useProfilePicture'
import { useDialog } from '../contexts/DialogContext'
import { triggerHaptic } from '../lib/haptics'
import { api } from '../lib/api'
import { logger } from '../lib/logger'
import { formatDateShort } from '../lib/utils'
import { MediaLoadingSpinner, MediaEmptyState, MediaPlayingOverlay, MediaStatusBadge, MediaCardAnimation, MediaGrid, useMediaSearch, getCategoryLabel, MediaCardDurationBar, MediaCardPlayOverlay, MediaCardActionButton, MediaCardCategoryBadge, MediaCardTags, MediaCardMetadata } from './MediaShared'

const CATEGORY_FALLBACK_COLORS = [
  '#8b5cf6', // purple
  '#ec4899', // pink
  '#06b6d4', // cyan
  '#10b981', // green
  '#f59e0b', // orange
]

function getCategoryColorIndex(category) {
  if (!category) return 0
  let hash = 0
  for (let i = 0; i < category.length; i++) {
    hash = ((hash << 5) - hash) + category.charCodeAt(i)
    hash = hash & hash
  }
  return Math.abs(hash) % CATEGORY_FALLBACK_COLORS.length
}

const CategoryCard = memo(function CategoryCard({ category, count, onSelectCategory, index }) {
  const { getWhite, getGrey400, getCategoryMetadata } = useDynamicTheme()

  const metadata = getCategoryMetadata(category)
  const color = metadata?.color || CATEGORY_FALLBACK_COLORS[getCategoryColorIndex(category)]

  const hexToRgb = (hex) => {
    const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex)
    return result ? {
      r: parseInt(result[1], 16),
      g: parseInt(result[2], 16),
      b: parseInt(result[3], 16)
    } : { r: 139, g: 92, b: 246 }
  }

  const rgb = hexToRgb(color)
  const gradient1 = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.2)`
  const gradient2 = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.3)`

  return (
    <MediaCardAnimation
      index={index}
      onClick={() => onSelectCategory(category)}
      className="bg-dark-card rounded-lg p-4 hover:bg-dark-hover active:bg-dark-card transition-all duration-200 cursor-pointer group/card"
    >
      <div className="aspect-square rounded-lg mb-2 flex flex-col items-center justify-center relative overflow-hidden group-hover/card:scale-105 transition-transform duration-200"
           style={{ background: `linear-gradient(135deg, ${gradient1} 0%, ${gradient2} 100%)` }}>
        <motion.div
          className="relative z-10 mb-2"
          whileHover={{ scale: 1.1 }}
        >
          <ShoutoutsIcon className="w-10 h-10 text-white/80" />
        </motion.div>
        <div className="relative z-10 text-white text-lg font-bold">
          {count}
        </div>
      </div>
      <h3 className="font-semibold text-sm mb-1 text-center transition-colors duration-700" style={{ color: getWhite() }}>
        {getCategoryLabel(category)}
      </h3>
      <p className="text-xs text-center transition-colors duration-700" style={{ color: getGrey400() }}>
        {count} {count === 1 ? 'shoutout' : 'shoutouts'}
      </p>
    </MediaCardAnimation>
  )
})

const ShoutoutCard = memo(function ShoutoutCard({ shoutout, isPlaying, onPlayPause, onDelete, index }) {
  const { getWhite } = useDynamicTheme()
  const { user } = useAuth()
  const deleteInteraction = usePointerInteraction()
  const profilePictureUrl = useProfilePicture(
    shoutout.user_id,
    !!shoutout.profile_picture
  )

  const getUserInitial = () => {
    return shoutout.username?.charAt(0).toUpperCase() || user?.username?.charAt(0).toUpperCase() || 'U'
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

  const handleDelete = useCallback((e) => {
    e?.preventDefault()
    e?.stopPropagation()
    if (!deleteInteraction.shouldTrigger()) return
    onDelete(e, shoutout.id)
  }, [shoutout, onDelete, deleteInteraction])

  const metadata = shoutout.transcription_metadata || shoutout.metadata || {}
  const tags = metadata.tags || []
  const duration = getDurationSeconds()

  return (
    <MediaCardAnimation
      index={index}
      className={`bg-dark-card rounded-lg p-2 md:p-4 hover:bg-dark-hover active:bg-dark-card transition-all duration-200 cursor-pointer group/card ${
        isPlaying ? 'ring-2 ring-purple-500 shadow-lg shadow-purple-500/20' : ''
      }`}
    >
      <div className="aspect-square bg-gradient-to-br from-purple-500/20 to-blue-500/20 rounded-lg mb-2 flex items-center justify-center relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-br from-purple-600/30 to-blue-600/30" />

        {isPlaying && <MediaPlayingOverlay />}

        <div className="absolute inset-0 w-full h-full bg-purple-600 flex items-center justify-center text-white text-5xl md:text-6xl font-bold">
          {getUserInitial()}
        </div>
        {profilePictureUrl && (
          <img
            src={profilePictureUrl}
            alt={shoutout.username || 'User'}
            className="absolute inset-0 w-full h-full object-cover"
            onError={(e) => { e.target.style.display = 'none' }}
          />
        )}

        <div className="absolute left-2 z-20 top-2">
          <MediaActions type="shoutout" itemId={shoutout.id} compact />
        </div>

        <div className="absolute top-2 right-2 z-30 flex items-center gap-2 pointer-events-none">
          {user && (shoutout.user_data?.user_id === user.id || shoutout.user_id === user.id || shoutout.user_id === String(user.id)) && (
            <MediaCardActionButton
              icon={Trash2}
              onClick={(e) => {
                deleteInteraction.onPointerDown(e)
                deleteInteraction.onPointerMove(e)
                handleDelete(e)
              }}
              title="Delete shoutout"
              variant="delete"
              className="pointer-events-auto"
            />
          )}
        </div>

        <MediaCardPlayOverlay
          isPlaying={isPlaying}
          onPlayPause={(e) => onPlayPause(e, shoutout)}
          hasAudio={shoutout.has_audio}
          variant="voice"
        />

        <MediaCardDurationBar duration={duration} />

        <div className="absolute bottom-2 right-2 z-30 flex items-end gap-2">
          {shoutout.reply_count > 0 && !shoutout.is_reply && (
            <div className="flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-black/60 backdrop-blur-sm">
              <MessageCircle size={10} className="text-purple-300" />
              <span className="text-xs text-purple-300 font-medium">{shoutout.reply_count}</span>
            </div>
          )}
          <MediaCardCategoryBadge category={metadata.category} position="bottom-right" />
          {isPlaying && <MediaStatusBadge variant="playing" />}
        </div>
      </div>

      {shoutout.is_reply && (
        <div className="flex items-center gap-1 mb-1">
          <MessageCircle size={10} className="text-purple-400" />
          <span className="text-xs text-purple-400">Reply</span>
        </div>
      )}

      <MediaSearchMatchBadge
        similarityScore={shoutout.similarity_score}
        intentCategory={shoutout.intent_category}
        matchWeights={shoutout.match_weights}
      />

      <h3 className="font-semibold text-xs md:text-sm mb-1 line-clamp-2 transition-colors duration-700" style={{ color: getWhite() }}>
        &ldquo;{shoutout.transcription}&rdquo;
      </h3>

      <MediaCardMetadata text={shoutout.username || 'Anonymous'} className="mb-2" />

      <MediaCardTags tags={tags} maxDisplay={3} />
    </MediaCardAnimation>
  )
}, (prev, next) => {
  return (
    prev.shoutout.id === next.shoutout.id &&
    prev.isPlaying === next.isPlaying &&
    prev.shoutout.reply_count === next.shoutout.reply_count
  )
})

export function Shoutouts({ onToggleView, currentView }) {
  const [shoutouts, setShoutouts] = useState([])
  const [loading, setLoading] = useState(true)
  const [removingIds, setRemovingIds] = useState(new Set())
  const [sortMode, setSortMode] = useState('recent')
  const [selectedCategory, setSelectedCategory] = useState(null)
  const [stats, setStats] = useState(null)

  const { user } = useAuth()
  const { playingShoutout, playShoutout, stopShoutout } = usePlaybackShoutout()
  const scrollContainerRef = useRef(null)
  const { contentUpdates, toastError, toastSuccess } = useUIState()
  const { showConfirm } = useDialog()

  const errorToast = toastError
  const successToast = toastSuccess

  const {
    isSearchMode,
    searchResults,
    searchIntent,
    isLoading: searchLoading,
    handleSearch: performSearch,
    handleClearSearch: clearSearch
  } = useMediaSearch(
    useCallback(async (query, isManual = false) => {
      return await api.searchShoutouts(query, 50, isManual)
    }, []),
    {
      onError: (error) => {
        errorToast('Search failed')
        console.error('Error searching shoutouts:', error)
      }
    }
  )

  useEffect(() => {
    void fetchShoutouts()
  }, [])

  useEffect(() => {
    if (contentUpdates.shoutouts > 0) {
      void fetchShoutouts()
    }
  }, [contentUpdates.shoutouts])

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const statsData = await api.getShoutoutStats(selectedCategory)
        setStats(statsData)
      } catch (err) {
        logger.error('Failed to fetch shoutout stats:', err)
        setStats(null)
      }
    }
    void fetchStats()
  }, [selectedCategory])

  const fetchShoutouts = async () => {
    try {
      setLoading(true)

      let discoveryQuery = 'greeting message shoutout hello family friends announcement'
      if (user?.shoutout_interests) {
        const interests = user.shoutout_interests.split(',').map(s => s.trim()).filter(s => s)
        if (interests.length > 0) {
          discoveryQuery = interests[Math.floor(Math.random() * interests.length)]
        }
      }

      const result = await api.searchShoutouts(discoveryQuery, 50)
      const discoveredShoutouts = result?.results || []

      setShoutouts(discoveredShoutouts)
    } catch (err) {
      errorToast('Failed to load shoutouts')
      console.error('Error fetching shoutouts:', err)
    } finally {
      setLoading(false)
    }
  }

  const handleDelete = useCallback(async (e, id) => {
    e?.preventDefault()
    e?.stopPropagation()

    const confirmed = await showConfirm({
      title: 'Delete Shoutout?',
      message: 'Are you sure you want to delete this shoutout? This action cannot be undone.',
      confirmText: 'Delete',
      cancelText: 'Cancel',
      variant: 'danger'
    })

    if (!confirmed) return

    try {
      triggerHaptic('medium')
      setRemovingIds(prev => new Set(prev).add(id))

      const response = await fetch(
        `/api/user_content/shoutouts/${id}`,
        {
          method: 'DELETE',
          headers: api.getHeaders(),
          credentials: 'include'
        }
      )

      if (!response.ok) {
        errorToast('Failed to delete shoutout')
        setRemovingIds(prev => {
          const next = new Set(prev)
          next.delete(id)
          return next
        })
        return
      }

      setShoutouts(prev => prev.filter(s => s.id !== id))
      successToast('Shoutout deleted')

      if (playingShoutout?.id === id) {
        stopShoutout()
      }
    } catch (err) {
      errorToast('Failed to delete shoutout')
      console.error('Error deleting:', err)
      setRemovingIds(prev => {
        const next = new Set(prev)
        next.delete(id)
        return next
      })
    }
  }, [errorToast, successToast, playingShoutout, stopShoutout, showConfirm])

  const handlePlayPause = useCallback((e, shoutout) => {
    e?.preventDefault()

    if (playingShoutout?.id === shoutout.id) {
      stopShoutout()
      triggerHaptic('light')
    } else {
      triggerHaptic('medium')
      playShoutout(shoutout, { showModal: true })
    }
  }, [playingShoutout, playShoutout, stopShoutout])

  const handleSearch = useCallback(async (query, isManual = false) => {
    try {
      await performSearch(query, isManual)
    } catch (_err) {
      logger.error('Search failed:', _err)
    }
  }, [performSearch])

  const handleClearSearch = useCallback(() => {
    clearSearch()
  }, [clearSearch])

  const handleSortChange = useCallback((newSortMode) => {
    setSortMode(newSortMode)
    setSelectedCategory(null)
  }, [])

  const handleSelectCategory = useCallback((category) => {
    setSelectedCategory(category)
  }, [])

  const handleBackToCategories = useCallback(() => {
    setSelectedCategory(null)
  }, [])

  const categories = useMemo(() => {
    const toGroup = isSearchMode ? searchResults : shoutouts
    const categoryMap = new Map()

    toGroup.forEach(shoutout => {
      const category = shoutout.transcription_metadata?.category ||
                       shoutout.metadata?.category ||
                       'general'

      if (!categoryMap.has(category)) {
        categoryMap.set(category, { category, count: 0, shoutouts: [] })
      }

      const catData = categoryMap.get(category)
      catData.count++
      catData.shoutouts.push(shoutout)
    })

    return Array.from(categoryMap.values()).sort((a, b) => b.count - a.count)
  }, [shoutouts, searchResults, isSearchMode])

  const displayShoutouts = useMemo(() => {
    const toSort = isSearchMode ? searchResults : shoutouts
    const sorted = [...toSort]

    const filtered = (sortMode === 'genre' && selectedCategory)
      ? sorted.filter(s => {
          const cat = s.transcription_metadata?.category || s.metadata?.category || 'general'
          return cat === selectedCategory
        })
      : sorted

    if (sortMode === 'alphabetical') {
      filtered.sort((a, b) => {
        const nameA = (a.username || '').toLowerCase()
        const nameB = (b.username || '').toLowerCase()
        return nameA.localeCompare(nameB)
      })
    } else if (sortMode === 'genre') {
      filtered.sort((a, b) => {
        const catA = (a.transcription_metadata?.category || a.metadata?.category || 'general').toLowerCase()
        const catB = (b.transcription_metadata?.category || b.metadata?.category || 'general').toLowerCase()
        return catA.localeCompare(catB)
      })
    } else {
      filtered.sort((a, b) => {
        const dateA = new Date(a.timestamp || 0)
        const dateB = new Date(b.timestamp || 0)
        return dateB - dateA
      })
    }

    return filtered
  }, [shoutouts, searchResults, isSearchMode, sortMode, selectedCategory])

  const visibleShoutouts = displayShoutouts.filter(s => !removingIds.has(s.id))

  const adaptiveStats = useMemo(() => {
    let trackRange = null
    if (displayShoutouts.length > 0) {
      const first = displayShoutouts[0]
      const last = displayShoutouts[displayShoutouts.length - 1]

      if (sortMode === 'alphabetical') {
        const firstName = first.username || 'Unknown'
        const lastName = last.username || 'Unknown'
        const firstLetter = firstName.charAt(0).toUpperCase()
        const lastLetter = lastName.charAt(0).toUpperCase()
        trackRange = firstLetter === lastLetter ? firstLetter : `${firstLetter} - ${lastLetter}`
      } else if (sortMode === 'genre') {
        const firstCat = first.transcription_metadata?.category || first.metadata?.category || 'general'
        const lastCat = last.transcription_metadata?.category || last.metadata?.category || 'general'
        const firstLabel = getCategoryLabel(firstCat)
        const lastLabel = getCategoryLabel(lastCat)
        trackRange = firstLabel === lastLabel ? firstLabel : `${firstLabel} - ${lastLabel}`
      } else {
        const firstDate = formatDateShort(first.timestamp || first.date)
        const lastDate = formatDateShort(last.timestamp || last.date)
        trackRange = firstDate === lastDate ? firstDate : `${lastDate} - ${firstDate}`
      }
    }

    return {
      ...stats,
      track_range: trackRange,
      is_search_mode: isSearchMode,
      search_result_count: isSearchMode ? searchResults.length : 0,
      displayed_tracks: sortMode === 'genre' && !selectedCategory ? categories.length : stats?.total_shoutouts,
      showing_genres: sortMode === 'genre' && !selectedCategory,
      genre_count: categories.length,
      selected_genre: selectedCategory
    }
  }, [stats, displayShoutouts, isSearchMode, searchResults.length, sortMode, selectedCategory, categories.length])

  const showingCategories = sortMode === 'genre' && !selectedCategory && categories.length > 0

  return (
    <div className="flex flex-col h-full relative">
      <MediaSearch
        type="shoutout"
        onSearch={handleSearch}
        onClear={handleClearSearch}
        searchIntent={searchIntent}
        onToggleView={onToggleView}
        currentView={currentView}
      />
      <Scroller ref={scrollContainerRef} className="flex-1 relative">
        {(loading || searchLoading) ? (
          <MediaLoadingSpinner type="shoutout" offsetHeader />
        ) : showingCategories ? (
          <>
            <CatalogHeader
              stats={adaptiveStats}
              sortMode={sortMode}
              onSortChange={handleSortChange}
              selectedGenre={selectedCategory}
              onBackToGenres={handleBackToCategories}
              contentType="shoutouts"
            />
            <MediaGrid withAnimation>
              {categories.map((categoryData, index) => (
                <CategoryCard
                  key={categoryData.category}
                  category={categoryData.category}
                  count={categoryData.count}
                  onSelectCategory={handleSelectCategory}
                  index={index}
                />
              ))}
            </MediaGrid>
          </>
        ) : visibleShoutouts.length === 0 ? (
          <>
            <CatalogHeader
              stats={adaptiveStats}
              sortMode={sortMode}
              onSortChange={handleSortChange}
              selectedGenre={selectedCategory}
              onBackToGenres={handleBackToCategories}
              contentType="shoutouts"
            />
            <MediaEmptyState
              icon={ShoutoutsIcon}
              title={isSearchMode ? 'No shoutouts found' : selectedCategory ? `No ${selectedCategory} shoutouts` : 'No shoutouts yet!'}
              subtitle={isSearchMode ? 'Try adjusting your search' : selectedCategory ? 'Try selecting a different category' : 'Use voice recording to create one.'}
            />
          </>
        ) : (
          <>
            <CatalogHeader
              stats={adaptiveStats}
              sortMode={sortMode}
              onSortChange={handleSortChange}
              selectedGenre={selectedCategory}
              onBackToGenres={handleBackToCategories}
              contentType="shoutouts"
            />
            <MediaGrid withAnimation>
              {visibleShoutouts.map((shoutout, index) => (
                <ShoutoutCard
                  key={shoutout.id}
                  shoutout={shoutout}
                  isPlaying={playingShoutout?.id === shoutout.id}
                  onPlayPause={handlePlayPause}
                  onDelete={handleDelete}
                  index={index}
                />
              ))}
            </MediaGrid>
          </>
        )}
      </Scroller>
    </div>
  )
}