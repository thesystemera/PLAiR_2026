import { useState, useEffect, useRef } from 'react'
import { motion } from 'framer-motion'
import { Music, List, Clock } from 'lucide-react'
import { useDynamicTheme, CATALOG_HEADER, TRANSITIONS, RecentIcon, AlphabeticalIcon, GenreIcon } from '../contexts/DynamicThemeContext'
import { CurvedBackdrop, GLASS_EFFECT_CONFIG } from './Panel'

export function CatalogHeader({ stats, sortMode, onSortChange, selectedGenre = null, onBackToGenres = null, contentType = 'catalog' }) {
  const { getGrey400 } = useDynamicTheme()
  const [visible, setVisible] = useState(true)
  const scrollTimeoutRef = useRef(null)
  const overlayRef = useRef(null)

  useEffect(() => {
    const handleScrollOrTouch = () => {
      setVisible(false)

      if (scrollTimeoutRef.current) {
        clearTimeout(scrollTimeoutRef.current)
      }

      scrollTimeoutRef.current = setTimeout(() => {
        setVisible(true)
      }, 500)
    }

    const scrollContainer = overlayRef.current?.closest('.overflow-y-auto')

    if (scrollContainer) {
      scrollContainer.addEventListener('scroll', handleScrollOrTouch, { passive: true })
      scrollContainer.addEventListener('touchstart', handleScrollOrTouch, { passive: true })
      scrollContainer.addEventListener('touchmove', handleScrollOrTouch, { passive: true })
    }

    return () => {
      if (scrollContainer) {
        scrollContainer.removeEventListener('scroll', handleScrollOrTouch)
        scrollContainer.removeEventListener('touchstart', handleScrollOrTouch)
        scrollContainer.removeEventListener('touchmove', handleScrollOrTouch)
      }
      if (scrollTimeoutRef.current) {
        clearTimeout(scrollTimeoutRef.current)
      }
    }
  }, [])

  if (!stats) return null

  return (
    <motion.div
      ref={overlayRef}
      className="sticky top-0 z-40 pointer-events-none"
      animate={{
        opacity: visible ? 1 : 0
      }}
      transition={TRANSITIONS.fade}
    >
      <div className="relative px-3 md:px-4 pt-2 md:pt-3 pb-2 md:pb-3 w-full rounded-2xl overflow-hidden">
        <CurvedBackdrop baseOpacity={GLASS_EFFECT_CONFIG.opacity.catalogHeader} />
        <div className="relative z-10 flex flex-col gap-1.5 md:gap-2 text-white">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 md:gap-3">
              <div className="flex items-center gap-1.5 md:gap-2">
                <div className={`${CATALOG_HEADER.statIcon.base} ${CATALOG_HEADER.statIcon.music}`}>
                  <Music className={CATALOG_HEADER.statIcon.size} />
                </div>
                <div>
                  {stats.showing_genres ? (
                    <>
                      <div className={CATALOG_HEADER.statText.value}>
                        {stats.genre_count?.toLocaleString() || 0}
                      </div>
                      <div className={CATALOG_HEADER.statText.label}>
                        {contentType === 'shoutouts' ? 'Categories' : 'Genres'}
                      </div>
                    </>
                  ) : stats.is_search_mode ? (
                    <>
                      <div className={CATALOG_HEADER.statText.value}>
                        {stats.search_result_count?.toLocaleString() || 0}
                      </div>
                      <div className={CATALOG_HEADER.statText.label}>
                        {stats.search_result_count === 1 ? 'Match' : 'Matches'}
                      </div>
                    </>
                  ) : (
                    <>
                      <div className={CATALOG_HEADER.statText.value}>
                        {(contentType === 'shoutouts' ? stats.total_shoutouts : stats.total_tracks)?.toLocaleString() || 0}
                      </div>
                      <div className={CATALOG_HEADER.statText.label}>
                        {contentType === 'shoutouts' ? 'Shouts' : 'Tracks'}
                      </div>
                    </>
                  )}
                </div>
              </div>

              {stats.showing_genres && contentType === 'catalog' && stats.total_subgenre_count > 0 ? (
                <div className="flex items-center gap-1.5 md:gap-2">
                  <div className={`${CATALOG_HEADER.statIcon.base} ${CATALOG_HEADER.statIcon.clock}`}>
                    <List className={CATALOG_HEADER.statIcon.size} />
                  </div>
                  <div>
                    <div className={CATALOG_HEADER.statText.value}>
                      {stats.total_subgenre_count?.toLocaleString() || 0}
                    </div>
                    <div className={CATALOG_HEADER.statText.label}>
                      SubGenres
                    </div>
                  </div>
                </div>
              ) : !stats.showing_genres && (
                <div className="flex items-center gap-1.5 md:gap-2">
                  <div className={`${CATALOG_HEADER.statIcon.base} ${CATALOG_HEADER.statIcon.clock}`}>
                    <Clock className={CATALOG_HEADER.statIcon.size} />
                  </div>
                  <div>
                    <div className={CATALOG_HEADER.statText.value}>
                      {stats.total_duration_formatted || '0m'}
                    </div>
                    <div className={CATALOG_HEADER.statText.label}>
                      Duration
                    </div>
                  </div>
                </div>
              )}
            </div>

            {!stats.is_search_mode && (
              <div className="flex items-center gap-1.5 md:gap-2 pointer-events-auto">
                {selectedGenre && onBackToGenres ? (
                  <button
                    onClick={onBackToGenres}
                    className="flex items-center gap-1 px-2 py-1 rounded text-xs font-medium bg-purple-500/20 text-purple-300 hover:bg-purple-500/30 transition-colors"
                    title="Back to genres"
                  >
                    <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                    </svg>
                    <span>Back</span>
                  </button>
                ) : (
                  <>
                    <button
                      onClick={() => onSortChange('recent')}
                      className={`p-2 rounded transition-colors ${sortMode === 'recent' ? 'bg-purple-500/30 text-purple-300' : 'hover:bg-white/10'}`}
                      style={sortMode !== 'recent' ? { color: getGrey400() } : {}}
                      title="Recent"
                    >
                      <RecentIcon className="w-5 h-5" />
                    </button>
                    <button
                      onClick={() => onSortChange('alphabetical')}
                      className={`p-2 rounded transition-colors ${sortMode === 'alphabetical' ? 'bg-purple-500/30 text-purple-300' : 'hover:bg-white/10'}`}
                      style={sortMode !== 'alphabetical' ? { color: getGrey400() } : {}}
                      title="A-Z"
                    >
                      <AlphabeticalIcon className="w-5 h-5" />
                    </button>
                    <button
                      onClick={() => onSortChange('genre')}
                      className={`p-2 rounded transition-colors ${sortMode === 'genre' ? 'bg-purple-500/30 text-purple-300' : 'hover:bg-white/10'}`}
                      style={sortMode !== 'genre' ? { color: getGrey400() } : {}}
                      title="Genre"
                    >
                      <GenreIcon className="w-5 h-5" />
                    </button>
                  </>
                )}
              </div>
            )}
          </div>

          {stats.track_range && !stats.showing_genres && !stats.selected_genre && (
            <div className={`${CATALOG_HEADER.statText.range} text-center`}>
              {stats.track_range}
            </div>
          )}

          {stats.selected_genre && (
            <div className="text-center">
              <div className={`${CATALOG_HEADER.statText.range} font-semibold`}>
                {stats.selected_genre}
              </div>
              {stats.sub_genres && stats.sub_genres.length > 0 && (
                <div className={`${CATALOG_HEADER.statText.detail} mt-0.5`}>
                  {stats.sub_genres.join(' • ')}
                </div>
              )}
            </div>
          )}

          {stats.is_search_mode && !stats.showing_genres && (
            <div className={`${CATALOG_HEADER.statText.detail} text-center`}>
              of {(contentType === 'shoutouts' ? stats.total_shoutouts : stats.total_tracks)?.toLocaleString() || 0} total {contentType === 'shoutouts' ? 'shouts' : 'tracks'}
            </div>
          )}
        </div>
      </div>
    </motion.div>
  )
}