import { motion, AnimatePresence } from 'framer-motion'
import { memo, useState, useCallback } from 'react'
import { useDynamicTheme, BUTTON, CatalogIcon, ShoutoutsIcon, RecentIcon, AlphabeticalIcon, GenreIcon } from '../contexts/DynamicThemeContext'
import { Play, Pause } from 'lucide-react'
import { formatDuration } from '../lib/utils'
import { CATEGORY_FALLBACK_COLORS, getCategoryColorIndex } from '../lib/themeManager'
import { MOTION, PRESETS, staggerDelay } from '../lib/motion'
import { useViewport } from '../contexts/ViewportContext'

export function useMediaGridColumns() {
  const { isMobile, isLandscape } = useViewport()
  const itemsPerRow = isMobile && isLandscape ? 4 : 2
  return {
    itemsPerRow,
    gridClassName: `grid ${itemsPerRow === 4 ? 'grid-cols-4' : 'grid-cols-2'} gap-3 md:gap-4 px-3 md:px-6`
  }
}

const CARD_ENTRANCE_LIMIT = 16

export const isCardEntering = (index, shouldAnimate) => shouldAnimate && index < CARD_ENTRANCE_LIMIT

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
      className="ui-fade-in absolute inset-0 flex items-center justify-center"
      style={offsetHeader ? { paddingTop: '72px' } : undefined}
    >
      <motion.div
        animate={{
          scale: [1, 1.15, 1],
          opacity: [0.7, 1, 0.7]
        }}
        transition={MOTION.breathe}
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
  title = 'Nothing here',
  subtitle = 'Try adjusting your search',
  compact = false
}) {
  const { getGrey400, getGrey300 } = useDynamicTheme()

  return (
    <motion.div
      {...PRESETS.emptyState}
      className={`flex flex-col items-center justify-center px-6 text-center transition-colors duration-theme ${compact ? 'py-6' : 'min-h-64 py-8'}`}
      style={{ color: getGrey400() }}
    >
      {Icon && <Icon className="w-16 h-16 mb-4 opacity-50" />}
      <div className="text-lg max-w-sm text-balance">{title}</div>
      {subtitle && (
        <div className="text-sm mt-2 max-w-sm text-balance" style={{ color: getGrey300() }}>
          {subtitle}
        </div>
      )}
    </motion.div>
  )
})

export const MediaOfflineState = memo(function MediaOfflineState({ icon, title, compact = false }) {
  return (
    <MediaEmptyState
      icon={icon}
      title={title}
      subtitle="It'll be back when PLAiR is online. Your downloads keep playing in the meantime."
      compact={compact}
    />
  )
})

export const MediaPlayingOverlay = memo(function MediaPlayingOverlay({ active }) {
  return (
    <AnimatePresence initial={false}>
      {active && (
        <motion.div key="playing" className="absolute inset-0 z-10 pointer-events-none" {...PRESETS.fadeSlow}>
          <motion.div
            className="absolute inset-0 bg-gradient-to-br from-purple-600/50 to-blue-600/50"
            animate={{ opacity: [0.3, 0.5, 0.3] }}
            transition={MOTION.shimmer}
          />
        </motion.div>
      )}
    </AnimatePresence>
  )
})

export const MediaStatusSlot = memo(function MediaStatusSlot({ status, className = '' }) {
  return (
    <AnimatePresence initial={false}>
      {status && (
        <motion.div key={status} className={className} {...PRESETS.fade}>
          {status === 'loading' ? (
            <div className="px-2 py-1">
              <motion.div
                animate={{ rotate: 360 }}
                transition={MOTION.spin}
                className="w-4 h-4 md:w-5 md:h-5 border-2 border-purple-500 border-t-transparent rounded-full"
              />
            </div>
          ) : (
            <MediaStatusBadge variant={status} />
          )}
        </motion.div>
      )}
    </AnimatePresence>
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
  const popClass = variant === 'new' ? '' : 'ui-pop'

  return (
    <div
      className={`px-2 py-1 text-xs rounded-full font-semibold z-40 ${popClass} ${variantStyles[variant]} ${className}`}
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
  style,
  ...props
}) {
  const animateEntrance = isCardEntering(index, shouldAnimate)

  return (
    <div
      className={animateEntrance ? `ui-fade-in ${className}` : className}
      style={animateEntrance ? { ...style, animationDelay: `${Math.round(staggerDelay(index) * 1000)}ms` } : style}
      {...props}
    >
      {children}
    </div>
  )
})

export const MediaGrid = memo(function MediaGrid({
  children,
  className = '',
  withAnimation = false
}) {
  const { gridClassName } = useMediaGridColumns()
  const gridClasses = `${gridClassName} pb-3 md:pb-6 ${className}`

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
      <button
        onClick={(e) => {
          e.stopPropagation()
          onPlayPause(e)
        }}
        className={`ui-tap ui-hover ${BUTTON.card.play}`}
      >
        {isPlaying ? (
          <Pause fill="currentColor" className="w-5 h-5 md:w-6 md:h-6" />
        ) : (
          <Play fill="currentColor" className="w-5 h-5 md:w-6 md:h-6" />
        )}
      </button>
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
    <button
      onClick={(e) => {
        e.stopPropagation()
        onClick(e)
      }}
      className={`ui-tap ui-hover p-1.5 rounded-lg shadow-lg transition-colors ${variantStyles[variant]} ${className}`}
      title={title}
      aria-label={title}
    >
      <Icon className="w-4 h-4" />
    </button>
  )
})


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
      className={`${positionClass} px-2 py-1 text-xs whitespace-nowrap rounded-full text-white font-semibold z-20 ${className}`}
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
          className="text-[9px] md:text-[10px] px-1.5 py-0.5 rounded bg-purple-500/20 transition-colors duration-theme"
          style={{ color: getGrey400() }}
        >
          #{tag}
        </span>
      ))}
      {tags.length > maxDisplay && (
        <span
          className="text-[9px] md:text-[10px] px-1.5 py-0.5 rounded bg-purple-500/20 transition-colors duration-theme"
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
      className={`text-[10px] md:text-xs truncate w-full transition-colors duration-theme ${className}`}
      style={{ color: getGrey400() }}
    >
      {text}
    </p>
  )
})