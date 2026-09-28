import { X, Radio, Heart, Star, Ban, TrendingUp } from 'lucide-react'
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion'
import { flushSync } from 'react-dom'
import { forwardRef, useState, useCallback, memo, useMemo, useEffect, useLayoutEffect, useRef } from 'react'
import { useAuth } from '../contexts/AuthContext'
import { usePreferences } from '../contexts/PreferencesContext'
import { useUISelector } from '../contexts/UIStateContext'
import { usePlaybackActions } from '../contexts/PlaybackContext'
import { useArtworkThumb } from '../contexts/UIStateContext'
import { getFallbackGradientClass } from '../lib/themeManager'
import { triggerHaptic } from '../lib/haptics'
import { PanelHeader } from './Panel'
import { useDynamicTheme } from '../contexts/DynamicThemeContext'
import { usePointerInteraction } from '../hooks/usePointerInteraction'
import { Scroller } from './Scroller'
import { MOTION, PRESETS, TWEEN } from '../lib/motion'
import { arrivalGlow, arrivalPulse, noteImageMount, revealOnLoad, watchOffscreen } from '../lib/microMotion'

const QUEUE_ROW = PRESETS.listReorder
const QUEUE_ITEM_VARIANTS = {
  exit: (mode) => mode === 'bulk' ? QUEUE_ROW.bulkExit : QUEUE_ROW.exit
}
const QUEUE_ROW_FLASH_TRANSITION = { ...MOTION.settle, layout: TWEEN.layout }
const BULK_REMOVAL_THRESHOLD = 2
const QUEUE_LIST_STYLE = { overflowAnchor: 'none' }
const CONTENT_HOLD_MS = 1500
const FOLLOW_USER_SCROLL_GRACE_MS = 4000
const FOLLOW_DELAY_MS = 120
const FOLLOW_MARGIN_PX = 24
const FOLLOW_RELEASE_MS = 900
const HIGHLIGHT_TRANSITION = { layout: TWEEN.layout, opacity: TWEEN.fade, scale: { duration: 0 } }
const HIGHLIGHT_FLASH_TRANSITION = { layout: TWEEN.layout, opacity: TWEEN.fade, scale: MOTION.settle }

const NowPlayingHighlight = memo(function NowPlayingHighlight({ box, ringColor, background, flashing, glowRef }) {
  useEffect(() => {
    if (flashing) arrivalGlow(glowRef.current, true)
  }, [flashing])

  return (
    <motion.div
      layout
      layoutDependency={box}
      initial={false}
      animate={{
        opacity: box.visible ? 1 : 0,
        scale: flashing ? [1, 1.03, 1] : 1
      }}
      transition={flashing ? HIGHLIGHT_FLASH_TRANSITION : HIGHLIGHT_TRANSITION}
      className="absolute left-0 right-0 pointer-events-none"
      style={{ top: box.top, height: box.height }}
    >
      <div
        className="absolute inset-0 rounded-md transition-colors"
        style={{ backgroundColor: background, boxShadow: `0 0 0 2px ${ringColor}` }}
      />
      <div
        ref={glowRef}
        className="absolute inset-0 rounded-md opacity-0"
        style={{ boxShadow: `0 0 0 6px ${ringColor}80, 0 0 20px ${ringColor}40` }}
      />
    </motion.div>
  )
})

const Equalizer = memo(function Equalizer({ playing }) {
  const ref = useRef(null)

  useEffect(() => watchOffscreen(ref.current), [])

  return (
    <span ref={ref} className="ui-eq" data-playing={playing ? 'true' : 'false'} aria-hidden="true">
      <span />
      <span />
      <span />
    </span>
  )
})

const BADGE_EXIT = { opacity: 0, transition: TWEEN.exit }

const PlayingBadge = memo(function PlayingBadge({ active, playing, background, color }) {
  return (
    <AnimatePresence initial={false}>
      {active && (
        <motion.div
          key="playing"
          initial={false}
          exit={BADGE_EXIT}
          className="ui-badge-in flex items-center gap-1.5 px-2 py-1 text-xs rounded-full font-semibold"
          style={{ backgroundColor: background, color }}
        >
          <Equalizer playing={playing} />
          Playing
        </motion.div>
      )}
    </AnimatePresence>
  )
})

const PreferenceBadge = memo(function PreferenceBadge({ preference }) {
  const { getLikeBadgeBg, getSuperLikeBadgeBg, getBanBadgeBg, getPrimaryText } = useDynamicTheme()
  const [shown, setShown] = useState(preference)
  const [animate, setAnimate] = useState(false)
  if (shown !== preference) {
    setShown(preference)
    setAnimate(!!preference)
  }

  if (!preference) return null

  const badges = {
    like: { icon: Heart, getBg: getLikeBadgeBg, fill: true },
    super_like: { icon: Star, getBg: getSuperLikeBadgeBg, fill: true },
    ban: { icon: Ban, getBg: getBanBadgeBg, fill: false }
  }

  const badge = badges[preference]
  if (!badge) return null

  const Icon = badge.icon

  return (
    <div key={preference} style={{ background: badge.getBg(), color: getPrimaryText() }} className={`rounded-full p-1.5 ${animate ? 'ui-pop' : ''}`} title={preference.replace('_', ' ')}>
      <Icon size={12} fill={badge.fill ? 'currentColor' : 'none'} />
    </div>
  )
})

const TrackArtwork = memo(function TrackArtwork({ track }) {
  const [artworkError, setArtworkError] = useState(false)
  const artworkUrl = useArtworkThumb(track.id, track.has_artwork)

  if (track.has_artwork && !artworkError) {
    return (
      <img
        ref={noteImageMount}
        data-queue-art
        src={artworkUrl}
        alt={track.title || 'Track artwork'}
        className="w-12 h-12 rounded object-cover flex-shrink-0"
        onLoad={revealOnLoad}
        onError={() => setArtworkError(true)}
      />
    )
  }

  return (
    <div data-queue-art className={`w-12 h-12 rounded flex-shrink-0 bg-gradient-to-br ${getFallbackGradientClass(track.id)} flex items-center justify-center text-2xl`}>
      🎵
    </div>
  )
})

const QueueRow = memo(forwardRef(function QueueRow({
  track,
  uniqueKey,
  layoutKey,
  exitMode,
  isPlaying,
  isLoading,
  isReselectFlashing,
  playing,
  showPreference,
  preference,
  colors,
  onPlay,
  onRemove,
  onPlayPointerDown,
  onPlayPointerMove,
  onRemovePointerDown,
  onRemovePointerMove,
}, ref) {
  return (
    <motion.div
      ref={ref}
      data-queue-key={uniqueKey}
      layout="position"
      layoutDependency={layoutKey}
      initial={QUEUE_ROW.initial}
      animate={isReselectFlashing ? {
        opacity: 1,
        x: 0,
        scale: [1, 1.03, 1]
      } : QUEUE_ROW.animate}
      variants={QUEUE_ITEM_VARIANTS}
      custom={exitMode}
      exit="exit"
      transition={isReselectFlashing ? QUEUE_ROW_FLASH_TRANSITION : QUEUE_ROW.transition}
      className="relative p-4 transition-colors cursor-pointer rounded-md"
      style={{
        borderBottom: `1px solid ${colors.panelBorder}`,
        ...(isPlaying ? { backgroundColor: 'transparent' } : {})
      }}
      onMouseEnter={(e) => !isPlaying && (e.currentTarget.style.backgroundColor = colors.buttonHoverBg)}
      onMouseLeave={(e) => !isPlaying && (e.currentTarget.style.backgroundColor = 'transparent')}
      onPointerDown={onPlayPointerDown}
      onPointerMove={onPlayPointerMove}
      onPointerUp={(e) => onPlay(e, track.id)}
    >
      <div className="flex items-center gap-3">
        <TrackArtwork track={track} />

        <div className="flex-1 min-w-0">
          <div className="font-medium truncate transition-colors duration-theme" style={{ color: colors.white }}>{track.title || 'Untitled'}</div>
          {track.artist_name && (
            <div className="text-sm truncate transition-colors duration-theme" style={{ color: colors.grey300 }}>{track.artist_name}</div>
          )}
          <div className="text-sm truncate transition-colors duration-theme" style={{ color: colors.grey400 }}>{track.style || 'No style'}</div>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          {isLoading && (
            <div className="ui-pop w-5 h-5">
              <motion.div
                animate={{ rotate: 360 }}
                transition={MOTION.spin}
                className="w-5 h-5 rounded-full"
                style={{
                  border: `2px solid ${colors.loadingSpinner}`,
                  borderTopColor: 'transparent'
                }}
              />
            </div>
          )}
          <PlayingBadge
            active={isPlaying}
            playing={playing}
            background={colors.loadingSpinner}
            color={colors.white}
          />

          {showPreference && <PreferenceBadge preference={preference} />}

          <button
            onPointerDown={onRemovePointerDown}
            onPointerMove={onRemovePointerMove}
            onPointerUp={(e) => onRemove(e, track.id)}
            className="ui-tap p-1 transition-colors"
            style={{ color: colors.grey400 }}
            onMouseEnter={(e) => e.currentTarget.style.color = colors.dangerActionText}
            onMouseLeave={(e) => e.currentTarget.style.color = colors.grey400}
          >
            <X size={16} />
          </button>
        </div>
      </div>
    </motion.div>
  )
}))

function QueueComponent({ onSeedRadio, onAnalytics }) {
  const playback = usePlaybackActions()
  const { isAuthenticated } = useAuth()
  const { getPreference } = usePreferences()
  const { queue, currentTrackId, currentIndex, isPlayingNow, activeSeedMode } = useUISelector(state => ({
    queue: state.engineState.queue,
    currentTrackId: state.engineState.currentTrack?.id,
    currentIndex: state.engineState.currentIndex,
    isPlayingNow: state.engineState.is_playing,
    activeSeedMode: state.radioState.activeSeedMode,
  }))
  const {
    getWhite,
    getGrey400,
    getGrey300,
    getPrimaryActionText,
    getButtonHoverBg,
    getPanelBorder,
    getPlayingRingColor,
    getPlayingBackground,
    getLoadingSpinner,
    getDangerActionText,
    getCategoryMetadata,
    triggerEffect
  } = useDynamicTheme()

  const [loadingTrackId, setLoadingTrackId] = useState(null)
  const [removingTrackIds, setRemovingTrackIds] = useState(new Set())
  const [reselectFlash, setReselectFlash] = useState(null)
  const {
    onPointerDown: onPlayPointerDown,
    onPointerMove: onPlayPointerMove,
    shouldTrigger: shouldTriggerPlay,
  } = usePointerInteraction()
  const {
    onPointerDown: onRemovePointerDown,
    onPointerMove: onRemovePointerMove,
    shouldTrigger: shouldTriggerRemove,
  } = usePointerInteraction()
  const currentTrackIdRef = useRef(currentTrackId)
  useEffect(() => { currentTrackIdRef.current = currentTrackId }, [currentTrackId])
  const seedInteraction = usePointerInteraction()
  const analyticsInteraction = usePointerInteraction()

  const handlePlay = useCallback((e, trackId) => {
    e?.preventDefault()
    if (!shouldTriggerPlay()) return

    const isReselect = currentTrackIdRef.current === trackId

    if (e.clientX !== undefined && e.clientY !== undefined) {
      triggerEffect('click', {
        x: e.clientX / window.innerWidth,
        y: e.clientY / window.innerHeight,
        intensity: isReselect ? 1.5 : 1.0
      })
    }

    if (isReselect) {
      setReselectFlash(trackId)
      triggerEffect('reselect', {
        targetId: trackId,
        color: getPlayingRingColor()
      })
      setTimeout(() => setReselectFlash(null), 600)
    }

    triggerHaptic(isReselect ? 'strong' : 'medium')
    setLoadingTrackId(trackId)
    void playback.playTrack(trackId)
  }, [playback, shouldTriggerPlay, triggerEffect, getPlayingRingColor])

  const handleRemove = useCallback((e, trackId) => {
    e?.preventDefault()
    e?.stopPropagation()
    if (!shouldTriggerRemove()) return
    triggerHaptic('medium')
    setRemovingTrackIds(prev => new Set(prev).add(trackId))
    playback.removeFromQueue(trackId).catch(() => {
      setRemovingTrackIds(prev => {
        const next = new Set(prev)
        next.delete(trackId)
        return next
      })
    })
  }, [playback, shouldTriggerRemove])

  const handleSeedButtonClick = useCallback((e) => {
    e?.preventDefault()
    if (!seedInteraction.shouldTrigger()) return
    triggerHaptic('medium')
    onSeedRadio()
  }, [seedInteraction, onSeedRadio])

  const handleAnalyticsButtonClick = useCallback((e) => {
    e?.preventDefault()
    if (!analyticsInteraction.shouldTrigger()) return
    triggerHaptic('medium')
    onAnalytics()
  }, [analyticsInteraction, onAnalytics])

  const rowColors = useMemo(() => ({
    panelBorder: getPanelBorder(),
    buttonHoverBg: getButtonHoverBg(),
    white: getWhite(),
    grey300: getGrey300(),
    grey400: getGrey400(),
    loadingSpinner: getLoadingSpinner(),
    dangerActionText: getDangerActionText(),
  }), [getPanelBorder, getButtonHoverBg, getWhite, getGrey300, getGrey400, getLoadingSpinner, getDangerActionText])

  const visibleQueue = useMemo(() => {
    const occurrences = new Map()
    return queue
      .filter(track => !removingTrackIds.has(track.id))
      .map(track => {
        const occurrence = occurrences.get(track.id) || 0
        occurrences.set(track.id, occurrence + 1)
        return { track, key: `${track.id}-${occurrence}` }
      })
  }, [queue, removingTrackIds])

  const [contentHeight, setContentHeight] = useState(0)
  const [removalState, setRemovalState] = useState({ items: visibleQueue, removed: 0, heldHeight: 0 })
  if (removalState.items !== visibleQueue) {
    const nextKeys = new Set(visibleQueue.map(item => item.key))
    const removed = removalState.items.reduce((count, item) => count + (nextKeys.has(item.key) ? 0 : 1), 0)
    const heldHeight = removed > 0 ? Math.max(removalState.heldHeight, contentHeight) : removalState.heldHeight
    setRemovalState({ items: visibleQueue, removed, heldHeight })
  }
  const reduceMotion = useReducedMotion()
  const exitMode = reduceMotion || removalState.removed > BULK_REMOVAL_THRESHOLD ? 'bulk' : 'single'
  const layoutKey = useMemo(() => visibleQueue.map(item => item.key).join(','), [visibleQueue])
  const currentKey = useMemo(() => {
    if (!currentTrackId) return null
    const indexed = queue[currentIndex]
    const exact = indexed?.id === currentTrackId ? visibleQueue.find(item => item.track === indexed) : null
    return (exact ?? visibleQueue.find(item => item.track.id === currentTrackId))?.key ?? null
  }, [queue, currentIndex, visibleQueue, currentTrackId])
  const hasQueue = queue.length > 0

  const glowRef = useRef(null)
  const previousKeyRef = useRef(currentKey)

  const listRef = useRef(null)
  const holdRef = useRef(null)
  const [highlightBox, setHighlightBox] = useState(null)

  const measureHighlight = useCallback(() => {
    const list = listRef.current
    if (!list) return
    const row = currentKey ? list.querySelector(`[data-queue-key="${CSS.escape(currentKey)}"]`) : null
    setHighlightBox(prev => {
      if (!row) return prev && prev.visible ? { ...prev, visible: false } : prev
      const top = row.offsetTop
      const height = row.offsetHeight
      if (prev && prev.visible && prev.top === top && prev.height === height) return prev
      return { top, height, visible: true }
    })
  }, [currentKey])

  const measureHighlightRef = useRef(measureHighlight)

  useLayoutEffect(() => {
    measureHighlightRef.current = measureHighlight
    queueMicrotask(() => flushSync(() => measureHighlightRef.current()))
  }, [measureHighlight, layoutKey])

  useEffect(() => {
    const list = listRef.current
    if (!list || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      setContentHeight(list.offsetHeight)
      measureHighlightRef.current()
    })
    observer.observe(list)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!removalState.heldHeight) return
    const timeoutId = setTimeout(() => {
      const hold = holdRef.current
      const scroller = hold?.closest('[data-scroller]')
      let needed = 0
      if (hold && scroller && listRef.current) {
        const offset = hold.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop
        const required = Math.ceil(scroller.scrollTop + scroller.clientHeight - offset)
        needed = required > listRef.current.offsetHeight ? required : 0
      }
      setRemovalState(prev => prev.heldHeight !== needed ? { ...prev, heldHeight: needed } : prev)
    }, CONTENT_HOLD_MS)
    return () => clearTimeout(timeoutId)
  }, [removalState])

  useEffect(() => {
    if (queue.length === 0) {
      queueMicrotask(() => setRemovingTrackIds(new Set()))
    } else {
      queueMicrotask(() => setRemovingTrackIds(prev => {
        const next = new Set()
        for (const id of prev) {
          if (queue.some(track => track.id === id)) {
            next.add(id)
          }
        }
        return next.size === prev.size ? prev : next
      }))
    }
  }, [queue])

  const lastUserScrollRef = useRef(0)
  const autoScrollReleaseRef = useRef(null)

  useEffect(() => {
    const scroller = listRef.current?.closest('[data-scroller]')
    if (!scroller) return
    const markUserScroll = () => { lastUserScrollRef.current = performance.now() }
    const options = { passive: true }
    scroller.addEventListener('wheel', markUserScroll, options)
    scroller.addEventListener('touchmove', markUserScroll, options)
    scroller.addEventListener('pointerdown', markUserScroll, options)
    scroller.addEventListener('keydown', markUserScroll, options)
    return () => {
      scroller.removeEventListener('wheel', markUserScroll, options)
      scroller.removeEventListener('touchmove', markUserScroll, options)
      scroller.removeEventListener('pointerdown', markUserScroll, options)
      scroller.removeEventListener('keydown', markUserScroll, options)
    }
  }, [hasQueue])

  useEffect(() => {
    const previousKey = previousKeyRef.current
    previousKeyRef.current = currentKey
    if (!previousKey || !currentKey || previousKey === currentKey) return
    arrivalGlow(glowRef.current)
    const row = listRef.current?.querySelector(`[data-queue-key="${CSS.escape(currentKey)}"]`)
    arrivalPulse(row?.querySelector('[data-queue-art]'))
    const timeoutId = setTimeout(() => {
      const target = listRef.current?.querySelector(`[data-queue-key="${CSS.escape(currentKey)}"]`)
      const scroller = target?.closest('[data-scroller]')
      if (!target || !scroller || scroller.clientHeight === 0) return
      if (performance.now() - lastUserScrollRef.current < FOLLOW_USER_SCROLL_GRACE_MS) return
      const scrollerRect = scroller.getBoundingClientRect()
      if (scrollerRect.right <= 0 || scrollerRect.left >= window.innerWidth) return
      const rowRect = target.getBoundingClientRect()
      const fullyVisible = rowRect.top >= scrollerRect.top + FOLLOW_MARGIN_PX && rowRect.bottom <= scrollerRect.bottom - FOLLOW_MARGIN_PX
      if (fullyVisible) return
      const rowTop = rowRect.top - scrollerRect.top + scroller.scrollTop
      const top = Math.max(0, rowTop - scroller.clientHeight * 0.25)
      scroller.dataset.autoScroll = '1'
      clearTimeout(autoScrollReleaseRef.current)
      autoScrollReleaseRef.current = setTimeout(() => { delete scroller.dataset.autoScroll }, FOLLOW_RELEASE_MS)
      scroller.scrollTo({ top, behavior: reduceMotion ? 'auto' : 'smooth' })
    }, FOLLOW_DELAY_MS)
    return () => clearTimeout(timeoutId)
  }, [currentKey, reduceMotion])

  useEffect(() => {
    if (currentTrackId && loadingTrackId === currentTrackId) {
      queueMicrotask(() => setLoadingTrackId(null))
    }
  }, [currentTrackId, loadingTrackId])

  const seedMeta = activeSeedMode ? getCategoryMetadata(activeSeedMode) : null
  const SeedIcon = seedMeta?.icon || Radio

  return (
    <div className="flex flex-col h-full relative">
        <PanelHeader title="Queue">
          {hasQueue && (
            <div className="flex items-center gap-2">
              <button
                onPointerDown={seedInteraction.onPointerDown}
                onPointerMove={seedInteraction.onPointerMove}
                onPointerUp={handleSeedButtonClick}
                disabled={queue.length === 0}
                className="ui-press flex items-center gap-2 px-3 py-2 text-sm rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed border"
                style={{
                  color: activeSeedMode ? seedMeta.color : getPrimaryActionText(),
                  backgroundColor: activeSeedMode ? `${seedMeta.color}15` : 'transparent',
                  borderColor: activeSeedMode ? `${seedMeta.color}30` : 'transparent'
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.backgroundColor = activeSeedMode ? `${seedMeta.color}25` : getButtonHoverBg()
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.backgroundColor = activeSeedMode ? `${seedMeta.color}15` : 'transparent'
                }}
                title={activeSeedMode ? `Active Seed: ${seedMeta.label}` : "Seed Radio"}
              >
                <SeedIcon size={16} />
                {activeSeedMode ? seedMeta.label : "Seed Radio"}
              </button>
              <button
                onPointerDown={analyticsInteraction.onPointerDown}
                onPointerMove={analyticsInteraction.onPointerMove}
                onPointerUp={handleAnalyticsButtonClick}
                disabled={queue.length === 0}
                className="ui-tap p-2 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed border"
                style={{
                  color: getGrey400(),
                  backgroundColor: 'transparent',
                  borderColor: 'transparent'
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.backgroundColor = getButtonHoverBg()
                  e.currentTarget.style.color = getWhite()
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.backgroundColor = 'transparent'
                  e.currentTarget.style.color = getGrey400()
                }}
                title="Station Analytics"
              >
                <TrendingUp size={16} />
              </button>
            </div>
          )}
        </PanelHeader>

      <AnimatePresence>
        {!hasQueue && (
          <motion.div key="queue-empty" {...PRESETS.emptyState} className="absolute inset-x-0 top-0 p-6 text-center transition-colors duration-theme" style={{ color: getGrey400() }}>
            <p>Queue is empty</p>
            <p className="text-sm mt-2" style={{ color: getGrey300() }}>Click tracks to play or add them to your queue</p>
          </motion.div>
        )}
      </AnimatePresence>

      <Scroller className="flex-1">
        <div ref={holdRef} style={removalState.heldHeight ? { ...QUEUE_LIST_STYLE, minHeight: removalState.heldHeight } : QUEUE_LIST_STYLE}>
          <div ref={listRef} className="relative">
            {highlightBox && (
              <NowPlayingHighlight
                box={highlightBox}
                ringColor={getPlayingRingColor()}
                background={getPlayingBackground()}
                flashing={reselectFlash !== null && reselectFlash === currentTrackId}
                glowRef={glowRef}
              />
            )}
            <AnimatePresence initial={false} custom={exitMode} mode="popLayout">
              {visibleQueue.map(({ track, key: uniqueKey }) => {
                const isPlaying = track.id === currentTrackId
                return (
                  <QueueRow
                    key={uniqueKey}
                    track={track}
                    uniqueKey={uniqueKey}
                    layoutKey={layoutKey}
                    exitMode={exitMode}
                    isPlaying={isPlaying}
                    isLoading={loadingTrackId === track.id && !isPlaying}
                    isReselectFlashing={reselectFlash === track.id}
                    playing={isPlaying && isPlayingNow}
                    showPreference={isAuthenticated}
                    preference={isAuthenticated ? getPreference('track', track.id) : null}
                    colors={rowColors}
                    onPlay={handlePlay}
                    onRemove={handleRemove}
                    onPlayPointerDown={onPlayPointerDown}
                    onPlayPointerMove={onPlayPointerMove}
                    onRemovePointerDown={onRemovePointerDown}
                    onRemovePointerMove={onRemovePointerMove}
                  />
                )
              })}
            </AnimatePresence>
          </div>
        </div>
      </Scroller>
    </div>
  )
}

export const Queue = memo(QueueComponent, (prevProps, nextProps) => {
  return prevProps.onSeedRadio === nextProps.onSeedRadio &&
         prevProps.onAnalytics === nextProps.onAnalytics
})