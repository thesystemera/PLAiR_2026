import { motion, AnimatePresence } from 'framer-motion'
import { memo, useState, useCallback } from 'react'
import { useDynamicTheme, BUTTON, CatalogIcon, ShoutoutsIcon, RecentIcon, AlphabeticalIcon, GenreIcon } from '../contexts/DynamicThemeContext'
import { Play, Pause } from 'lucide-react'
import { formatDuration } from '../lib/utils'

export const MediaLoadingSpinner = memo(function MediaLoadingSpinner({
  type = 'track', // 'track' or 'shoutout'
  sortMode = null, // 'recent', 'alphabetical', 'genre' (for track type)
  offsetHeader = false // Set to true to offset by header height (matches InteractiveEngagementButton positioning)
}) {
  const { getLoadingSpinner, getCardBackground } = useDynamicTheme()

  const Icon = (() => {
    if (type === 'shoutout') return ShoutoutsIcon
    // For tracks, use sortMode-specific icons
    if (sortMode === 'recent') return RecentIcon
    if (sortMode === 'alphabetical') return AlphabeticalIcon
    if (sortMode === 'genre') return GenreIcon
    // Default to CatalogIcon for tracks
    return CatalogIcon
  })()

  return (
    <div 
      className="absolute inset-0 flex items-center justify-center"
      style={offsetHeader ? { paddingTop: '72px' } : undefined}
    >
      <motion.div
        animate={{
          scale: [1, 1.15, 1],
          opacity: [0.7, 1, 0.7]
        }}
        transition={{
          duration: 1.5,
          repeat: Infinity,
          ease: 'easeInOut'
        }}
        className="w-20 h-20 md:w-24 md:h-24 rounded-full flex items-center justify-center"
        style={{
          backgroundColor: getCardBackground(),
          boxShadow: `0 0 30px ${getLoadingSpinner()}`
        }}
      >
        <Icon
          className="w-10 h-10 md:w-12 md:h-12"
          strokeWidth={1.5}
          style={{ color: getLoadingSpinner() }}
        />
      </motion.div>
    </div>
  )
})

export const MediaEmptyState = memo(function MediaEmptyState({
  icon: Icon = null,
  emoji = null, // Deprecated: use icon prop instead
  title = 'Nothing here',
  subtitle = 'Try adjusting your search'
}) {
  const { getGrey400, getGrey300 } = useDynamicTheme()

  return (
    <div
      className="flex flex-col items-center justify-center h-64 transition-colors duration-700"
      style={{ color: getGrey400() }}
    >
      <div className="mb-4">
        {Icon ? (
          <Icon className="w-16 h-16 opacity-50" />
        ) : emoji ? (
          <span className="text-6xl">{emoji}</span>
        ) : null}
      </div>
      <div className="text-lg">{title}</div>
      <div className="text-sm mt-2" style={{ color: getGrey300() }}>
        {subtitle}
      </div>
    </div>
  )
})

export const MediaPlayingOverlay = memo(function MediaPlayingOverlay() {
  return (
    <motion.div
      className="absolute inset-0 bg-gradient-to-br from-purple-600/50 to-blue-600/50 z-10"
      animate={{ opacity: [0.3, 0.5, 0.3] }}
      transition={{ duration: 2, repeat: Infinity }}
    />
  )
})

export const MediaStatusBadge = memo(function MediaStatusBadge({
  variant = 'playing',
  text,
  className = ''
}) {
  const variantStyles = {
    playing: 'bg-purple-600 text-white',
    queued: 'bg-blue-500/80 text-white',
    new: 'bg-gradient-to-r from-green-500 to-emerald-500 text-white'
  }

  const displayText = text || {
    playing: 'Playing',
    queued: 'Queued',
    new: 'NEW'
  }[variant]

  return (
    <div
      className={`px-2 py-1 text-xs rounded-full font-semibold z-40 ${variantStyles[variant]} ${className}`}
    >
      {displayText}
    </div>
  )
})

export const MediaCardAnimation = memo(function MediaCardAnimation({
  index = 0,
  shouldAnimate = true,
  children,
  className = '',
  ...motionProps
}) {
  return (
    <motion.div
      initial={shouldAnimate ? { opacity: 0, y: 20 } : { opacity: 1, y: 0 }}
      animate={{ opacity: 1, y: 0 }}
      transition={shouldAnimate ? { delay: Math.min(index * 0.03, 0.3) } : { duration: 0 }}
      className={className}
      {...motionProps}
    >
      {children}
    </motion.div>
  )
})

export const MediaGrid = memo(function MediaGrid({
  children,
  className = '',
  withAnimation = false
}) {
  const gridClasses = `grid grid-cols-2 md:grid-cols-2 lg:grid-cols-2 gap-3 md:gap-4 px-3 md:px-6 pb-3 md:pb-6 ${className}`

  if (withAnimation) {
    return (
      <AnimatePresence initial={false}>
        <div className={gridClasses}>
          {children}
        </div>
      </AnimatePresence>
    )
  }

  return <div className={gridClasses}>{children}</div>
})

export function useMediaSearch(searchFn, options = {}) {
  const { onSearchComplete, onError } = options

  const [isSearchMode, setIsSearchMode] = useState(false)
  const [searchResults, setSearchResults] = useState([])
  const [searchIntent, setSearchIntent] = useState(null)
  const [isLoading, setIsLoading] = useState(false)

  const handleSearch = useCallback(async (query, ...args) => {
    setIsLoading(true)
    setIsSearchMode(true)

    try {
      const result = await searchFn(query, ...args)
      const results = result?.results || []

      setSearchResults(results)

      if (results.length > 0 && results[0]?.intent_category) {
        setSearchIntent(results[0].intent_category)
      } else {
        setSearchIntent(null)
      }

      onSearchComplete?.(results, query)
    } catch (error) {
      onError?.(error)
      throw error
    } finally {
      setIsLoading(false)
    }
  }, [searchFn, onSearchComplete, onError])

  const handleClearSearch = useCallback(() => {
    setIsSearchMode(false)
    setSearchResults([])
    setSearchIntent(null)
  }, [])

  return {
    isSearchMode,
    searchResults,
    searchIntent,
    isLoading,
    handleSearch,
    handleClearSearch
  }
}

export function getCategoryLabel(category) {
  if (!category) return 'Shoutout'
  return category.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())
}

export const MediaCardDurationBar = memo(function MediaCardDurationBar({ duration }) {
  if (!duration || duration <= 0) return null

  return (
    <div className={BUTTON.card.durationBar}>
      <span className="text-white text-xs md:text-sm font-semibold tabular-nums">
        {formatDuration(duration)}
      </span>
    </div>
  )
})

export const MediaCardPlayOverlay = memo(function MediaCardPlayOverlay({
  isPlaying,
  onPlayPause,
  hasAudio = true,
  variant: _variant = 'music'
}) {
  if (!hasAudio) return null

  return (
    <div className={BUTTON.card.playOverlay}>
      <motion.button
        whileHover={{ scale: 1.1 }}
        whileTap={{ scale: 0.95 }}
        onClick={(e) => {
          e.stopPropagation()
          onPlayPause(e)
        }}
        className={BUTTON.card.play}
      >
        {isPlaying ? (
          <Pause fill="currentColor" className="w-5 h-5 md:w-6 md:h-6" />
        ) : (
          <Play fill="currentColor" className="w-5 h-5 md:w-6 md:h-6" />
        )}
      </motion.button>
    </div>
  )
})

export const MediaCardActionButton = memo(function MediaCardActionButton({
  icon: Icon,
  onClick,
  title,
  variant = 'default',
  className = ''
}) {
  const variantStyles = {
    default: 'bg-blue-500 text-white hover:bg-blue-600',
    delete: 'bg-red-500/80 text-white hover:bg-red-600',
    seed: 'bg-blue-500 text-white hover:bg-blue-600'
  }

  return (
    <motion.button
      whileHover={{ scale: 1.1 }}
      whileTap={{ scale: 0.9 }}
      onClick={(e) => {
        e.stopPropagation()
        onClick(e)
      }}
      className={`p-1.5 rounded-lg shadow-lg transition ${variantStyles[variant]} ${className}`}
      title={title}
    >
      <Icon className="w-4 h-4" />
    </motion.button>
  )
})

const CATEGORY_FALLBACK_COLORS = [
  '#8b5cf6',
  '#ec4899',
  '#06b6d4',
  '#10b981',
  '#f59e0b',
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

export const MediaCardCategoryBadge = memo(function MediaCardCategoryBadge({
  category,
  position = 'bottom-right',
  className = ''
}) {
  const { getCategoryMetadata } = useDynamicTheme()

  if (!category) return null

  const metadata = getCategoryMetadata(category)
  const color = metadata?.color || CATEGORY_FALLBACK_COLORS[getCategoryColorIndex(category)]
  const label = metadata?.label || getCategoryLabel(category)

  const positionClass = position === 'top-right'
    ? 'absolute top-2 right-2'
    : 'absolute bottom-2 right-2'

  return (
    <div
      className={`${positionClass} px-2 py-1 text-xs rounded-full text-white font-semibold z-20 ${className}`}
      style={{ backgroundColor: color }}
    >
      {label}
    </div>
  )
})

export const MediaCardTags = memo(function MediaCardTags({
  tags,
  maxDisplay = 3,
  className = ''
}) {
  const { getGrey400 } = useDynamicTheme()

  if (!tags || tags.length === 0) return null

  return (
    <div className={`flex flex-wrap gap-1 ${className}`}>
      {tags.slice(0, maxDisplay).map((tag, i) => (
        <span
          key={i}
          className="text-[9px] md:text-[10px] px-1.5 py-0.5 rounded bg-purple-500/20 transition-colors duration-700"
          style={{ color: getGrey400() }}
        >
          #{tag}
        </span>
      ))}
      {tags.length > maxDisplay && (
        <span
          className="text-[9px] md:text-[10px] px-1.5 py-0.5 rounded bg-purple-500/20 transition-colors duration-700"
          style={{ color: getGrey400() }}
        >
          +{tags.length - maxDisplay}
        </span>
      )}
    </div>
  )
})

export const MediaCardMetadata = memo(function MediaCardMetadata({
  text,
  className = ''
}) {
  const { getGrey400 } = useDynamicTheme()

  if (!text) return null

  return (
    <p
      className={`text-[10px] md:text-xs truncate w-full transition-colors duration-700 ${className}`}
      style={{ color: getGrey400() }}
    >
      {text}
    </p>
  )
})