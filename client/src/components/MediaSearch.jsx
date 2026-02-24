import { useState, useRef, useEffect, memo } from 'react'
import { Search, X, Sparkles, Loader, Upload } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { api } from '../lib/api'
import { triggerHaptic } from '../lib/haptics'
import { useUISound } from '../hooks/useUISound'
import { useDynamicTheme, PANEL, TRANSITIONS, CatalogIcon, ShoutoutsIcon } from '../contexts/DynamicThemeContext'
import { InteractiveEngagementButton } from './InteractiveEngagementButton'
import { useUIState } from '../contexts/UIStateContext'
import { CurvedBackdrop, GLASS_EFFECT_CONFIG } from './Panel'

export const MediaSearch = memo(function MediaSearch({
  type = 'track',
  onSearch,
  onClear,
  searchIntent,
  onGenerate,
  onToggleQueue,
  audio,
  onToggleView,
  currentView
}) {
  const { queueState, toastError, interfaceState, openUploadModal } = useUIState()
  const showError = toastError
  const isGenerating = queueState?.hasActiveJobs
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

  const badgeOpacity = query.length <= 20 ? 1 : Math.max(0, 1 - (query.length - 20) / 20)

  const placeholderText = type === 'track'
    ? 'Search tracks or speak...'
    : 'Search shoutouts or speak...'

  useEffect(() => {
    return () => {
      if (debounceTimeout.current) {
        clearTimeout(debounceTimeout.current)
      }
    }
  }, [])

  const handleRecordingComplete = async (audioBlob) => {
    if (!audioBlob || audioBlob.size === 0) return

    setIsTranscribing(true)

    try {
      const result = await api.transcribe(audioBlob)

      if (result.text && result.text.trim()) {
        setQuery(result.text.trim())
        onSearch(result.text.trim(), true, true)
      }
    } catch (_err) {
      showError('Voice transcription failed. Please try again.')
      console.error('Transcription error:', _err)
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
        opacity: interfaceState.isScrolling ? 0.15 : 1
      }}
      transition={TRANSITIONS.fade}
    >
      <CurvedBackdrop baseOpacity={GLASS_EFFECT_CONFIG.opacity.searchBar} />
      <form onSubmit={handleSubmit} className="relative z-10 h-full px-4 md:px-6 flex items-center gap-2 w-full">
        <div className="relative flex-grow">
          <Search
            className={`absolute left-3 top-1/2 -translate-y-1/2 transition-colors pointer-events-none ${
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
            className={`w-full pl-10 pr-10 py-2 bg-dark-card border rounded-full text-sm text-white placeholder-gray-500 focus:outline-none transition-all ${
              isFocused ? 'border-purple-500 shadow-lg shadow-purple-500/20' : 'border-gray-700'
            }`}
          />

          <div className="absolute right-3 top-1/2 -translate-y-1/2 flex items-center gap-2">
            <AnimatePresence>
              {query && intentMeta && !isGenerating && (
                (IntentIcon) => (
                  <motion.div
                    initial={{ opacity: 0, scale: 0.8, x: 5 }}
                    animate={{ opacity: badgeOpacity, scale: 1, x: 0 }}
                    exit={{ opacity: 0, scale: 0.8, x: 5 }}
                    transition={{ opacity: { duration: 0.2 } }}
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

            {query && !isGenerating && (
              <button
                type="button"
                onClick={handleClear}
                className="text-gray-400 hover:text-white transition-colors"
              >
                <X size={16} />
              </button>
            )}
          </div>
        </div>

        <InteractiveEngagementButton
          buttonType="search"
          onRecordingComplete={handleRecordingComplete}
          uiSound={uiSound}
          title={isTranscribing ? 'Transcribing...' : 'Hold to record voice'}
        />

        {type === 'track' && (
          <button
            type="button"
            onClick={() => {
              triggerHaptic('light')
              openUploadModal()
            }}
            className="flex-shrink-0 w-9 h-9 bg-emerald-600 text-white rounded-full hover:bg-emerald-700 transition flex items-center justify-center"
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
            className="flex-shrink-0 w-9 h-9 bg-purple-600 text-white rounded-full hover:bg-purple-700 transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center"
            title={isGenerating ? 'View generation queue' : 'Generate tracks'}
          >
            {isGenerating ? (
              <motion.div
                animate={{ rotate: 360 }}
                transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
              >
                <Loader className="w-4 h-4" />
              </motion.div>
            ) : (
              <Sparkles className="w-4 h-4" />
            )}
          </button>
        )}

        {onToggleView && (
          <button
            type="button"
            onClick={onToggleView}
            className="flex-shrink-0 w-9 h-9 bg-gray-700 text-white rounded-full hover:bg-gray-600 transition flex items-center justify-center"
            title={currentView === 'tracks' ? 'View Shoutouts' : 'View Catalog'}
          >
            {currentView === 'tracks' ? (
              <ShoutoutsIcon className="w-4 h-4" />
            ) : (
              <CatalogIcon className="w-4 h-4" />
            )}
          </button>
        )}
      </form>
    </motion.div>
  )
})