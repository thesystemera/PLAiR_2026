import { X, Heart, Star, Ban, TrendingUp, Library, Sprout, ListMusic, Music } from 'lucide-react'
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion'
import { forwardRef, useState, useCallback, memo, useMemo, useEffect, useLayoutEffect, useRef } from 'react'
import { useAuth } from '../contexts/AuthContext'
import { usePreferences } from '../contexts/PreferencesContext'
import { useUISelector } from '../contexts/UIStateContext'
import { usePlaybackActions } from '../contexts/PlaybackContext'
import { getFallbackGradientClass } from '../lib/themeManager'
import { triggerHaptic } from '../lib/haptics'
import { PanelHeader } from './Panel'
import { useDynamicTheme } from '../contexts/DynamicThemeContext'
import { usePointerInteraction } from '../hooks/usePointerInteraction'
import { Scroller } from './Scroller'
import { MOTION, PRESETS, TWEEN } from '../lib/motion'
import { arrivalGlow, arrivalPulse } from '../lib/microMotion'
import { TrackArt } from './DepthArt'
import { LoadingRing, MediaEmptyState, PlayingPill } from './MediaShared'
import { MemoizedVirtualScroller as VirtualScroller } from './VirtualScroller'
import { Fade, FadeSwap, Pop } from './Motion'
import { artistCredit } from '../lib/utils'

const QUEUE_ART_PROPS = { 'data-queue-art': true }

const QUEUE_ROW = PRESETS.listReorder
const QUEUE_ROW_GONE = { opacity: 0, transition: { duration: 0 } }
const NO_KEYS = new Set()
const ESTIMATED_ROW_HEIGHT = 97
const queueRowVariants = (key) => ({
  exit: (removal) => (removal?.keys.has(key) ? (removal.bulk ? QUEUE_ROW.bulkExit : QUEUE_ROW.exit) : QUEUE_ROW_GONE)
})
const queueArtworkId = (item) => (item.track.has_artwork === false ? null : item.track.id)
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

const mix = (color, percent, base = 'transparent') => `color-mix(in srgb, ${color} ${percent}%, ${base})`

const NowPlayingHighlight = memo(function NowPlayingHighlight({ box, ringColor, flashing, glowRef }) {
  useEffect(() => {
    if (flashing) arrivalGlow(glowRef.current, true)
  }, [flashing, glowRef])

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
      <div className="ui-current-fill absolute inset-0 rounded-md" />
      <div
        ref={glowRef}
        className="absolute inset-0 rounded-md opacity-0"
        style={{ boxShadow: `0 0 0 6px ${mix(ringColor, 50)}, 0 0 20px ${mix(ringColor, 25)}` }}
      />
    </motion.div>
  )
})

const BADGE_EXIT = { opacity: 0, transition: TWEEN.exit }

const QueueStatus = memo(function QueueStatus({ loading, active, playing }) {
  const status = active ? 'playing' : loading ? 'loading' : null
  return (
    <AnimatePresence initial={false} mode="popLayout">
      {status === 'loading' && (
        <motion.div key="loading" className="w-5 h-5" {...PRESETS.fade}>
          <LoadingRing />
        </motion.div>
      )}
      {status === 'playing' && (
        <motion.div key="playing" initial={false} exit={BADGE_EXIT} className="flex">
          <PlayingPill playing={playing} />
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
    setAnimate(!!preference && !!shown)
  }

  const badges = {
    like: { icon: Heart, getBg: getLikeBadgeBg, fill: true },
    super_like: { icon: Star, getBg: getSuperLikeBadgeBg, fill: true },
    ban: { icon: Ban, getBg: getBanBadgeBg, fill: false }
  }

  const badge = preference ? badges[preference] : null
  const Icon = badge?.icon

  return (
    <Pop show={!!badge} className="flex">
      {badge && (
        <div key={preference} style={{ background: badge.getBg(), color: getPrimaryText() }} className={`rounded-full p-1.5 ${animate ? 'ui-pop' : ''}`} title={preference.replace('_', ' ')}>
          <Icon size={12} fill={badge.fill ? 'currentColor' : 'none'} />
        </div>
      )}
    </Pop>
  )
})

const TrackArtwork = memo(function TrackArtwork({ track }) {
  const [artworkError, setArtworkError] = useState(false)

  if (track.has_artwork && !artworkError) {
    return (
      <TrackArt
        trackId={track.id}
        hasArtwork={track.has_artwork}
        alt={track.title || 'Track artwork'}
        className="relative w-12 h-12 rounded overflow-hidden flex-shrink-0"
        artProps={QUEUE_ART_PROPS}
        onError={() => setArtworkError(true)}
      />
    )
  }

  return (
    <div data-queue-art className={`w-12 h-12 rounded flex-shrink-0 bg-gradient-to-br ${getFallbackGradientClass(track.id)} flex items-center justify-center text-white/80`}>
      <Music size={22} />
    </div>
  )
})

const REMOVE_ICON = <X size={16} />

const QueueRow = memo(forwardRef(function QueueRow({
  track,
  uniqueKey,
  layoutKey,
  entering,
  isReselectFlashing,
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
  const { isPlaying, isLoading, playing } = useUISelector(state => {
    const current = state.engineState.currentTrack?.id === track.id
    return {
      isPlaying: current,
      isLoading: !current && state.engineState.pendingTrackId === track.id,
      playing: current && state.engineState.is_playing,
    }
  })
  const variants = useMemo(() => queueRowVariants(uniqueKey), [uniqueKey])
  return (
    <motion.div
      ref={ref}
      data-queue-key={uniqueKey}
      layout="position"
      layoutDependency={layoutKey}
      initial={entering ? QUEUE_ROW.initial : false}
      animate={isReselectFlashing ? {
        opacity: 1,
        x: 0,
        scale: [1, 1.03, 1]
      } : QUEUE_ROW.animate}
      variants={variants}
      exit="exit"
      transition={isReselectFlashing ? QUEUE_ROW_FLASH_TRANSITION : QUEUE_ROW.transition}
      className="ui-row p-4 cursor-pointer rounded-md"
      data-active={isPlaying ? 'true' : 'false'}
      style={{ borderBottom: `1px solid ${colors.panelBorder}` }}
      onPointerDown={onPlayPointerDown}
      onPointerMove={onPlayPointerMove}
      onPointerUp={(e) => onPlay(e, track.id)}
    >
      <div className="flex items-center gap-3">
        <TrackArtwork track={track} />

        <div className="flex-1 min-w-0">
          <div className={`truncate transition-colors duration-theme ${isPlaying ? 'ui-current-text font-semibold' : 'font-medium'}`} style={isPlaying ? undefined : { color: colors.white }}>{track.title || 'Untitled'}</div>
          <div className="text-sm truncate transition-colors duration-theme" style={{ color: colors.grey300 }}>{artistCredit(track) || ' '}</div>
          <div className="text-sm truncate transition-colors duration-theme" style={{ color: colors.grey400 }}>{track.style || 'No style'}</div>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          <QueueStatus
            loading={isLoading}
            active={isPlaying}
            playing={playing}
          />

          {showPreference && <PreferenceBadge preference={preference} />}

          <button
            onPointerDown={onRemovePointerDown}
            onPointerMove={onRemovePointerMove}
            onPointerUp={(e) => onRemove(e, track.id)}
            className="ui-tap p-1 rounded-lg text-white/50 hover:text-red-300 hover:bg-red-500/15 transition-colors"
            title="Remove from queue"
            aria-label="Remove from queue"
          >
            {REMOVE_ICON}
          </button>
        </div>
      </div>
    </motion.div>
  )
}))

const MODE_CAMPS = {
  favorites: 'mine',
  discovery: 'mine',
  top_hits_all: 'charts',
  top_hits_week: 'charts',
  top_hits_day: 'charts',
}

const MODE_BUTTON_ON = 'text-[var(--mode-color)] border-[color-mix(in_srgb,var(--mode-color)_30%,transparent)] bg-[color-mix(in_srgb,var(--mode-color)_10%,transparent)] hover:bg-[color-mix(in_srgb,var(--mode-color)_20%,transparent)]'
const MODE_BUTTON_OFF = 'border-transparent text-[var(--theme-secondary-text)] hover:text-[var(--theme-primary-text)] hover:bg-[var(--theme-button-hover-bg)]'

function QueueModeButton({ icon: Icon, iconKey, label, active, color, disabled, onOpen }) {
  const { onPointerDown, onPointerMove, shouldTrigger } = usePointerInteraction()

  const handlePointerUp = (e) => {
    e?.preventDefault()
    if (!shouldTrigger()) return
    triggerHaptic('medium')
    onOpen()
  }

  return (
    <button
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={handlePointerUp}
      disabled={disabled}
      aria-label={label}
      title={label}
      className={`ui-tap p-2 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed border ${active ? MODE_BUTTON_ON : MODE_BUTTON_OFF}`}
      style={active ? { '--mode-color': color } : undefined}
    >
      <FadeSwap swapKey={iconKey} preset={PRESETS.badgeSwap} className="block">
        <Icon size={16} />
      </FadeSwap>
    </button>
  )
}

const QueueModes = memo(function QueueModes({ show, onSeedRadio, onList }) {
  const { isAuthenticated } = useAuth()
  const activeSeedMode = useUISelector(state => state.radioState.activeSeedMode)
  const { getCategoryMetadata } = useDynamicTheme()
  const openMine = useCallback(() => onList('mine'), [onList])
  const openCharts = useCallback(() => onList('charts'), [onList])
  const seedMeta = activeSeedMode ? getCategoryMetadata(activeSeedMode) : null
  const activeCamp = activeSeedMode ? MODE_CAMPS[activeSeedMode] || 'seed' : null
  const campLabel = (camp, name) => (activeCamp === camp ? `${name}: ${seedMeta.label}` : name)
  const campIcon = (camp, icon) => (activeCamp === camp ? { icon: seedMeta.icon, iconKey: activeSeedMode } : { icon, iconKey: camp })

  return (
    <Fade show={show} className="flex items-center gap-2">
      <QueueModeButton
        {...campIcon('mine', Library)}
        label={isAuthenticated ? campLabel('mine', 'Your music') : 'Sign in for your music'}
        active={activeCamp === 'mine'}
        color={seedMeta?.color}
        disabled={!isAuthenticated}
        onOpen={openMine}
      />
      <QueueModeButton
        {...campIcon('seed', Sprout)}
        label={campLabel('seed', 'Seed radio')}
        active={activeCamp === 'seed'}
        color={seedMeta?.color}
        onOpen={onSeedRadio}
      />
      <QueueModeButton
        {...campIcon('charts', TrendingUp)}
        label={campLabel('charts', 'Charts')}
        active={activeCamp === 'charts'}
        color={seedMeta?.color}
        onOpen={openCharts}
      />
    </Fade>
  )
})

function QueueComponent({ onSeedRadio, onList }) {
  const playback = usePlaybackActions()
  const { isAuthenticated } = useAuth()
  const { getPreference } = usePreferences()
  const { queue, currentTrackId, currentIndex, toastError } = useUISelector(state => ({
    queue: state.engineState.queue,
    currentTrackId: state.engineState.currentTrack?.id,
    currentIndex: state.engineState.currentIndex,
    toastError: state.toastError,
  }))
  const {
    getWhite,
    getGrey400,
    getGrey300,
    getPanelBorder,
    getPlayingRingColor,
    triggerEffect
  } = useDynamicTheme()

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
    void playback.playTrack(trackId)
  }, [playback, shouldTriggerPlay, triggerEffect, getPlayingRingColor])

  const handleRemove = useCallback((e, trackId) => {
    e?.preventDefault()
    e?.stopPropagation()
    if (!shouldTriggerRemove()) return
    triggerHaptic('medium')
    setRemovingTrackIds(prev => new Set(prev).add(trackId))
    playback.removeFromQueue(trackId).catch((error) => {
      toastError(error?.message || "Couldn't remove that track")
      setRemovingTrackIds(prev => {
        const next = new Set(prev)
        next.delete(trackId)
        return next
      })
    })
  }, [playback, shouldTriggerRemove, toastError])

  const rowColors = useMemo(() => ({
    panelBorder: getPanelBorder(),
    white: getWhite(),
    grey300: getGrey300(),
    grey400: getGrey400(),
  }), [getPanelBorder, getWhite, getGrey300, getGrey400])

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
  const [rowHeight, setRowHeight] = useState(ESTIMATED_ROW_HEIGHT)
  const [removalState, setRemovalState] = useState({ items: visibleQueue, removed: 0, removedKeys: NO_KEYS, addedKeys: NO_KEYS, heldHeight: 0 })
  if (removalState.items !== visibleQueue) {
    const nextKeys = new Set(visibleQueue.map(item => item.key))
    const previousKeys = new Set(removalState.items.map(item => item.key))
    const removedKeys = new Set()
    for (const key of previousKeys) if (!nextKeys.has(key)) removedKeys.add(key)
    const addedKeys = new Set()
    for (const key of nextKeys) if (!previousKeys.has(key)) addedKeys.add(key)
    const heldHeight = removedKeys.size > 0 ? Math.max(removalState.heldHeight, contentHeight) : removalState.heldHeight
    setRemovalState({ items: visibleQueue, removed: removedKeys.size, removedKeys, addedKeys, heldHeight })
  }
  const reduceMotion = useReducedMotion()
  const removal = useMemo(() => ({
    keys: removalState.removedKeys,
    bulk: reduceMotion || removalState.removed > BULK_REMOVAL_THRESHOLD,
  }), [removalState.removedKeys, removalState.removed, reduceMotion])
  const layoutKey = useMemo(() => visibleQueue.map(item => item.key).join(','), [visibleQueue])
  const currentKey = useMemo(() => {
    if (!currentTrackId) return null
    const indexed = queue[currentIndex]
    const exact = indexed?.id === currentTrackId ? visibleQueue.find(item => item.track === indexed) : null
    return (exact ?? visibleQueue.find(item => item.track.id === currentTrackId))?.key ?? null
  }, [queue, currentIndex, visibleQueue, currentTrackId])
  const currentRow = useMemo(() => (currentKey ? visibleQueue.findIndex(item => item.key === currentKey) : -1), [visibleQueue, currentKey])
  const hasQueue = queue.length > 0

  const [highlightBox, setHighlightBox] = useState(null)
  const nextBox = currentRow < 0
    ? (highlightBox?.visible ? { ...highlightBox, visible: false } : highlightBox)
    : (highlightBox?.visible && highlightBox.top === currentRow * rowHeight && highlightBox.height === rowHeight
      ? highlightBox
      : { top: currentRow * rowHeight, height: rowHeight, visible: true })
  if (nextBox !== highlightBox) setHighlightBox(nextBox)

  const glowRef = useRef(null)
  const previousKeyRef = useRef(currentKey)
  const scrollRef = useRef(null)
  const listRef = useRef(null)
  const holdRef = useRef(null)
  const followRef = useRef({ row: currentRow, height: rowHeight })

  useLayoutEffect(() => {
    followRef.current = { row: currentRow, height: rowHeight }
  }, [currentRow, rowHeight])

  useEffect(() => {
    const list = listRef.current
    if (!list || typeof ResizeObserver === 'undefined') return
    let observedRow = null
    const observer = new ResizeObserver(() => {
      setContentHeight(list.offsetHeight)
      const row = [...list.querySelectorAll('[data-queue-key]')].find(element => !element.closest('[data-motion-pop-id]'))
      if (!row) return
      if (row !== observedRow) {
        if (observedRow) observer.unobserve(observedRow)
        observer.observe(row)
        observedRow = row
      }
      const pitch = parseFloat(getComputedStyle(row).height)
      if (pitch > 0) setRowHeight(previous => (Math.abs(previous - pitch) > 0.05 ? pitch : previous))
    })
    observer.observe(list)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!removalState.heldHeight) return
    const timeoutId = setTimeout(() => {
      const hold = holdRef.current
      const scroller = scrollRef.current
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
    const scroller = scrollRef.current
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
  }, [])

  useEffect(() => {
    const previousKey = previousKeyRef.current
    previousKeyRef.current = currentKey
    if (!previousKey || !currentKey || previousKey === currentKey) return
    arrivalGlow(glowRef.current)
    const art = listRef.current?.querySelector(`[data-queue-key="${CSS.escape(currentKey)}"] [data-queue-art]`)
    if (art) arrivalPulse(art)
    const timeoutId = setTimeout(() => {
      const scroller = scrollRef.current
      const list = listRef.current
      const { row, height } = followRef.current
      if (!scroller || !list || row < 0 || scroller.clientHeight === 0) return
      if (performance.now() - lastUserScrollRef.current < FOLLOW_USER_SCROLL_GRACE_MS) return
      const scrollerRect = scroller.getBoundingClientRect()
      if (scrollerRect.right <= 0 || scrollerRect.left >= window.innerWidth) return
      const rowTop = list.getBoundingClientRect().top - scrollerRect.top + scroller.scrollTop + row * height
      const fullyVisible = rowTop >= scroller.scrollTop + FOLLOW_MARGIN_PX &&
        rowTop + height <= scroller.scrollTop + scroller.clientHeight - FOLLOW_MARGIN_PX
      if (fullyVisible) return
      const top = Math.max(0, rowTop - scroller.clientHeight * 0.25)
      scroller.dataset.autoScroll = '1'
      clearTimeout(autoScrollReleaseRef.current)
      autoScrollReleaseRef.current = setTimeout(() => { delete scroller.dataset.autoScroll }, FOLLOW_RELEASE_MS)
      scroller.scrollTo({ top, behavior: reduceMotion ? 'auto' : 'smooth' })
    }, FOLLOW_DELAY_MS)
    return () => clearTimeout(timeoutId)
  }, [currentKey, reduceMotion])


  const renderRow = useCallback(({ track, key: uniqueKey }) => (
    <QueueRow
      key={uniqueKey}
      track={track}
      uniqueKey={uniqueKey}
      layoutKey={layoutKey}
      entering={removalState.addedKeys.has(uniqueKey)}
      isReselectFlashing={reselectFlash === track.id}
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
  ), [layoutKey, removalState.addedKeys, reselectFlash, isAuthenticated, getPreference, rowColors, handlePlay, handleRemove, onPlayPointerDown, onPlayPointerMove, onRemovePointerDown, onRemovePointerMove])

  const wrapRows = useCallback((rows) => (
    <AnimatePresence initial={false} custom={removal} mode="popLayout" presenceAffectsLayout={false}>
      {rows}
    </AnimatePresence>
  ), [removal])

  return (
    <div className="flex flex-col h-full relative">
        <PanelHeader title="Queue">
          <QueueModes show={hasQueue} onSeedRadio={onSeedRadio} onList={onList} />
        </PanelHeader>

      <AnimatePresence>
        {!hasQueue && (
          <div key="queue-empty" className="absolute inset-x-0 top-0">
            <MediaEmptyState icon={ListMusic} title="Queue is empty" subtitle="Click tracks to play or add them to your queue" compact />
          </div>
        )}
      </AnimatePresence>

      <Scroller ref={scrollRef} className="flex-1">
        <div ref={holdRef} style={removalState.heldHeight ? { ...QUEUE_LIST_STYLE, minHeight: removalState.heldHeight } : QUEUE_LIST_STYLE}>
          <div ref={listRef} className="relative">
            {highlightBox && (
              <NowPlayingHighlight
                box={highlightBox}
                ringColor={getPlayingRingColor()}
                flashing={reselectFlash !== null && reselectFlash === currentTrackId}
                glowRef={glowRef}
              />
            )}
            <VirtualScroller
              items={visibleQueue}
              itemHeight={rowHeight}
              renderItem={renderRow}
              getPrefetchId={queueArtworkId}
              scrollContainerRef={scrollRef}
              wrapItems={wrapRows}
            />
          </div>
        </div>
      </Scroller>
    </div>
  )
}

export const Queue = memo(QueueComponent, (prevProps, nextProps) => {
  return prevProps.onSeedRadio === nextProps.onSeedRadio &&
         prevProps.onList === nextProps.onList
})