import { logger } from '../lib/logger'
import { useState, useRef, useEffect, memo } from 'react'
import { Search, X, Sparkles, Loader, Upload } from 'lucide-react'
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion'
import { api } from '../lib/api'
import { triggerHaptic } from '../lib/haptics'
import { useUISound } from '../hooks/useUISound'
import { useDynamicTheme, PANEL, TRANSITIONS, CatalogIcon, ShoutoutsIcon } from '../contexts/DynamicThemeContext'
import { InteractiveEngagementButton } from './InteractiveEngagementButton'
import { useUISelector } from '../contexts/UIStateContext'
import { CurvedBackdrop, GLASS_EFFECT_CONFIG } from './Panel'
import { Pop } from './Motion'
import { MOTION, VARIANTS } from '../lib/motion'
import { useViewport } from '../contexts/ViewportContext'

export const MediaSearch = memo(function MediaSearch({
  type = 'track',
  onSearch,
  onClear,
  searchIntent,
  onGenerate,
  onToggleQueue,
  audio
}) {
  const {
    hasActiveJobs,
    toastError,
    toastInfo,
    catalogView,
    isScrolling,
    openUploadModal,
    toggleCatalogView,
    offlineMode,
  } = useUISelector(state => ({
    hasActiveJobs: state.queueState?.hasActiveJobs,
    toastError: state.toastError,
    toastInfo: state.toastInfo,
    catalogView: state.interfaceState.catalogView,
    isScrolling: state.interfaceState.isScrolling,
    openUploadModal: state.openUploadModal,
    toggleCatalogView: state.toggleCatalogView,
    offlineMode: state.audioState.offlineMode,
  }))
  const { isLG } = useViewport()
  const compact = isLG
  const currentView = catalogView
  const showError = toastError
  const isGenerating = hasActiveJobs
  const [query, setQuery] = useState('')
  const [isFocused, setIsFocused] = useState(false)
  const [isTranscribing, setIsTranscribing] = useState(false)
  const debounceTimeout = useRef(null)
  const inputRef = useRef(null)

  const uiSound = useUISound(audio || window.audioEngine)

  const { getCategoryMetadata } = useDynamicTheme()

  const intentMetadata = searchIntent ? getCategoryMetadata(searchIntent) : null
  const intentMeta = intentMetadata ? {
    color: intentMetadata.color,
    label: intentMetadata.label,
    icon: intentMetadata.icon
  } : null

  const searching = isFocused
  const reduceMotion = useReducedMotion()

  const badgeOpacity = query.length <= 20 ? 1 : Math.max(0, 1 - (query.length - 20) / 20)

  const placeholderText = compact
    ? 'Search...'
    : type === 'track'
      ? 'Search or speak...'
      : 'Search shoutouts or speak...'

  const actionButtonSize = compact ? 'w-8 h-8' : 'w-9 h-9'

  useEffect(() => {
    return () => {
      if (debounceTimeout.current) {
        clearTimeout(debounceTimeout.current)
      }
    }
  }, [])

  const handleRecordingComplete = async (audioBlob) => {
    if (!audioBlob || audioBlob.size === 0) return
    if (offlineMode) {
      toastInfo('Voice search needs a connection to PLAiR. You can still type to search your downloads.', 4000, 'bottom', 'search')
      return
    }

    setIsTranscribing(true)

    try {
      const result = await api.transcribe(audioBlob)

      if (result.text && result.text.trim()) {
        setQuery(result.text.trim())
        inputRef.current?.blur()
        onSearch(result.text.trim(), true)
      }
    } catch (_err) {
      showError('Voice transcription failed. Please try again.')
      logger.error('Transcription error:', _err)
    } finally {
      setIsTranscribing(false)
    }
  }

  const handleChange = (e) => {
    const newQuery = e.target.value
    setQuery(newQuery)

    if (debounceTimeout.current) {
      clearTimeout(debounceTimeout.current)
    }

    debounceTimeout.current = setTimeout(() => {
      if (newQuery.trim()) {
        onSearch(newQuery, false)
      } else {
        onClear()
      }
    }, 300)
  }

  const handleSubmit = (e) => {
    e.preventDefault()
    if (debounceTimeout.current) {
      clearTimeout(debounceTimeout.current)
    }
    inputRef.current?.blur()
    if (query.trim()) {
      onSearch(query, true)
    }
  }

  const handleClear = () => {
    if (debounceTimeout.current) {
      clearTimeout(debounceTimeout.current)
    }
    setQuery('')
    onClear()
    inputRef.current?.blur()
  }

  const keepSearchFocus = (e) => {
    if (isFocused) e.preventDefault()
  }

  const handleGenerate = () => {
    if (query.trim()) {
      triggerHaptic('success')
      onGenerate(query)
      setQuery('')
      onClear()
    }
  }

  return (
    <motion.div
      className="absolute top-0 left-0 right-0 z-10"
      style={{ height: `${PANEL.headerHeight}px` }}
      animate={{
        opacity: isScrolling ? 0.15 : 1
      }}
      transition={TRANSITIONS.fade}
    >
      <CurvedBackdrop baseOpacity={GLASS_EFFECT_CONFIG.opacity.searchBar} />
      <form onSubmit={handleSubmit} className={`relative z-10 h-full flex items-center w-full ${compact ? 'px-3' : 'px-4 md:px-6'}`}>
        <div className="relative flex-1 min-w-0">
          <Search
            className={`absolute ${compact ? 'left-2.5' : 'left-3'} top-1/2 -translate-y-1/2 transition-colors pointer-events-none ${
              isFocused ? 'text-purple-400' : 'text-gray-400'
            }`}
            size={16}
          />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={handleChange}
            onFocus={() => setIsFocused(true)}
            onBlur={() => setIsFocused(false)}
            placeholder={placeholderText}
            className={`w-full ${compact ? 'pl-8 pr-7' : 'pl-10 pr-10'} py-2 bg-dark-card border rounded-full text-sm text-white placeholder-gray-500 focus:outline-none transition ${
              isFocused ? 'border-purple-500 shadow-lg shadow-purple-500/20' : 'border-gray-700'
            }`}
          />

          <div className={`absolute ${compact ? 'right-2' : 'right-3'} top-1/2 -translate-y-1/2 flex items-center gap-2`}>
            <AnimatePresence>
              {query && intentMeta && !isGenerating && !compact && (
                (IntentIcon) => (
                  <motion.div
                    initial={{ opacity: 0, scale: 0.8, x: 5 }}
                    animate={{ opacity: badgeOpacity, scale: 1, x: 0 }}
                    exit={{ opacity: 0, scale: 0.8, x: 5 }}
                    transition={MOTION.quickOpacity}
                    className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-opacity-20 backdrop-blur-sm pointer-events-none select-none"
                    style={{ backgroundColor: `${intentMeta.color}30` }}
                  >
                    <IntentIcon size={10} style={{ color: intentMeta.color }} />
                    <span
                      className="text-[10px] font-bold uppercase tracking-wider"
                      style={{ color: intentMeta.color }}
                    >
                      {intentMeta.label}
                    </span>
                  </motion.div>
                )
              )(intentMeta.icon)}
            </AnimatePresence>

            <Pop show={(searching || !!query) && !isGenerating} className="flex">
              <button
                type="button"
                onMouseDown={keepSearchFocus}
                onClick={handleClear}
                aria-label="Close search"
                title="Close search"
                className="ui-press text-gray-400 hover:text-white transition-colors"
              >
                <X size={16} />
              </button>
            </Pop>
          </div>
        </div>

        <div className={`flex-shrink-0 ${compact ? 'ml-1.5' : 'ml-2'}`} onMouseDown={keepSearchFocus}>
          <InteractiveEngagementButton
            buttonType="search"
            onRecordingComplete={handleRecordingComplete}
            uiSound={uiSound}
            title={isTranscribing ? 'Transcribing...' : 'Hold to record voice'}
          />
        </div>

        <motion.div
          className="flex-shrink-0"
          variants={reduceMotion ? VARIANTS.expandReduced : VARIANTS.expandSideways}
          initial={false}
          animate={searching ? 'collapsed' : 'open'}
        >
          <div className={`flex items-center ${compact ? 'gap-1.5 pl-1.5' : 'gap-2 pl-2'}`}>
            {type === 'track' && (
              <button
                type="button"
                onClick={() => {
                  triggerHaptic('light')
                  openUploadModal()
                }}
                className={`ui-press flex-shrink-0 ${actionButtonSize} bg-emerald-600 text-white rounded-full hover:bg-emerald-700 transition flex items-center justify-center`}
                title="Upload your music"
              >
                <Upload className="w-4 h-4" />
              </button>
            )}
            {type === 'track' && onGenerate && onToggleQueue && (
              <button
                type="button"
                onClick={isGenerating ? onToggleQueue : handleGenerate}
                disabled={!isGenerating && !query.trim()}
                className={`ui-press flex-shrink-0 ${actionButtonSize} bg-purple-600 text-white rounded-full hover:bg-purple-700 transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center`}
                title={isGenerating ? 'View generation queue' : 'Generate tracks'}
              >
                {isGenerating ? (
                  <motion.div
                    animate={{ rotate: 360 }}
                    transition={MOTION.spin}
                  >
                    <Loader className="w-4 h-4" />
                  </motion.div>
                ) : (
                  <Sparkles className="w-4 h-4" />
                )}
              </button>
            )}
            <button
              type="button"
              onClick={toggleCatalogView}
              className={`ui-press flex-shrink-0 ${actionButtonSize} bg-gray-700 text-white rounded-full hover:bg-gray-600 transition flex items-center justify-center`}
              title={currentView === 'tracks' ? 'View Shoutouts' : 'View Catalog'}
            >
              {currentView === 'tracks' ? (
                <ShoutoutsIcon className="w-4 h-4" />
              ) : (
                <CatalogIcon className="w-4 h-4" />
              )}
            </button>
          </div>
        </motion.div>
      </form>
    </motion.div>
  )
})