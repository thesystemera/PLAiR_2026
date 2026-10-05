import { useEffect, useLayoutEffect, useRef, useState, useCallback, memo } from 'react'
import { flushSync } from 'react-dom'
import { prefetchCovers } from '../lib/artworkPrefetcher'
import { depthArtRenderer } from '../lib/depthArtRenderer'

const SETTLE_MS = 140
const VELOCITY_WINDOW_MS = 300
const JUMP_VIEWPORTS = 3
const IDLE_SPEED = 0.05
const RENDER_AHEAD_ROWS = 4
const RENDER_BEHIND_ROWS = 2
const RENDER_IDLE_ROWS = 3
const PREFETCH_SECONDS = 3
const PREFETCH_MIN_AHEAD_ITEMS = 32
const PREFETCH_MAX_AHEAD_ITEMS = 96
const PREFETCH_BEHIND_ITEMS = 8
const PREFETCH_IDLE_BEHIND_ITEMS = 16
const INITIAL_ROWS = 8

const clamp = (value, min, max) => Math.max(min, Math.min(max, value))

function defaultPlaceholder(index, itemHeight) {
  return (
    <div
      key={`skeleton-${index}`}
      style={{ height: itemHeight }}
      className="w-full h-full p-2"
    >
      <div className="w-full h-full bg-white/[0.08] rounded-lg animate-pulse ring-1 ring-white/10" />
    </div>
  )
}

function VirtualScroller({
  items = [],
  totalCount = 0,
  windowStart = 0,
  itemHeight = 100,
  itemsPerRow = 1,
  renderItem,
  renderPlaceholder = null,
  onRangeChange = null,
  getPrefetchId = null,
  scrollContainerRef,
  className = ''
}) {
  const listRef = useRef(null)
  const [range, setRange] = useState({ start: 0, end: INITIAL_ROWS })
  const rangeRef = useRef(range)
  const motionRef = useRef({ top: 0, time: 0, velocity: 0, direction: 1, listTop: 0, scrolling: false, samples: [] })
  const latestRef = useRef(null)
  const demandKeyRef = useRef('')
  const itemsVersionRef = useRef(0)
  const rafRef = useRef(0)
  const settleTimerRef = useRef(null)

  const catalogSize = totalCount || items.length
  const totalRows = Math.ceil(catalogSize / itemsPerRow)

  useEffect(() => {
    window.registerRAFSource?.('VirtualScroller')
  }, [])

  const measure = useCallback(() => {
    const container = scrollContainerRef?.current
    const list = listRef.current
    if (!container || !list) return
    motionRef.current.listTop = list.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop
    motionRef.current.coverPx = 0
  }, [scrollContainerRef])

  const coverSize = useCallback(() => {
    const motion = motionRef.current
    if (!motion.coverPx) {
      const cover = listRef.current?.querySelector('[data-depth-art]')
      motion.coverPx = cover ? Math.max(cover.offsetWidth, cover.offsetHeight) : 0
    }
    return motion.coverPx ? depthArtRenderer.packSizeFor(motion.coverPx) : 0
  }, [])

  const updateDemand = useCallback((firstRow, lastRow, down, speed, idle, jumped) => {
    const latest = latestRef.current
    if (!latest) return
    const { itemHeight: rowHeight, itemsPerRow: perRow, totalRows: rows, items: list, windowStart: offset, onRangeChange: onRange, getPrefetchId: getId } = latest
    const rowsPerSecond = speed * 1000 / rowHeight
    const minAhead = Math.ceil(PREFETCH_MIN_AHEAD_ITEMS / perRow)
    const maxAhead = Math.ceil(PREFETCH_MAX_AHEAD_ITEMS / perRow)
    const aheadRows = idle ? minAhead : clamp(Math.ceil(rowsPerSecond * PREFETCH_SECONDS), minAhead, maxAhead)
    const behindRows = Math.ceil((idle ? PREFETCH_IDLE_BEHIND_ITEMS : PREFETCH_BEHIND_ITEMS) / perRow)
    const fetchStart = clamp(firstRow - (down ? behindRows : aheadRows), 0, rows)
    const fetchEnd = clamp(lastRow + (down ? aheadRows : behindRows), 0, rows)

    const key = `${firstRow}|${lastRow}|${fetchStart}|${fetchEnd}|${itemsVersionRef.current}`
    if (key === demandKeyRef.current) return
    demandKeyRef.current = key

    const focusIndex = (down ? firstRow : Math.max(firstRow, lastRow - 1)) * perRow
    onRange?.(fetchStart * perRow, fetchEnd * perRow, focusIndex)

    if (!getId) return
    const size = coverSize()
    if (!size) {
      demandKeyRef.current = ''
      return
    }
    const ids = []
    const pushRow = (row) => {
      const base = row * perRow
      for (let i = base; i < base + perRow; i++) {
        const item = list[i - offset]
        if (item) {
          const id = getId(item)
          if (id) ids.push(id)
        }
      }
    }
    if (down) {
      for (let row = firstRow; row < lastRow; row++) pushRow(row)
      for (let row = lastRow; row < fetchEnd; row++) pushRow(row)
      for (let row = firstRow - 1; row >= fetchStart; row--) pushRow(row)
    } else {
      for (let row = lastRow - 1; row >= firstRow; row--) pushRow(row)
      for (let row = firstRow - 1; row >= fetchStart; row--) pushRow(row)
      for (let row = lastRow; row < fetchEnd; row++) pushRow(row)
    }
    prefetchCovers(ids, { jumped, size })
  }, [coverSize])

  const compute = useCallback((settled, fromScroll = false) => {
    const container = scrollContainerRef?.current
    const latest = latestRef.current
    if (!container || !latest || !latest.itemHeight) return
    const { itemHeight: rowHeight, totalRows: rows } = latest
    const motion = motionRef.current
    const now = performance.now()
    const scrollTop = container.scrollTop
    const viewHeight = container.clientHeight
    const delta = scrollTop - motion.top
    const elapsed = now - motion.time

    const samples = motion.samples
    if (settled) {
      motion.velocity = 0
      samples.length = 0
    } else if (delta !== 0 && elapsed > 0) {
      motion.direction = delta > 0 ? 1 : -1
      if (Math.abs(delta) > viewHeight * JUMP_VIEWPORTS) {
        samples.length = 0
        motion.velocity = 0
      } else {
        samples.push({ time: now, top: scrollTop })
        while (samples.length > 2 && now - samples[0].time > VELOCITY_WINDOW_MS) samples.shift()
        const oldest = samples[0]
        const span = now - oldest.time
        motion.velocity = span >= 16 ? (scrollTop - oldest.top) / span : motion.velocity
      }
    }
    motion.top = scrollTop
    motion.time = now

    const relativeTop = scrollTop - motion.listTop
    const firstRow = clamp(Math.floor(relativeTop / rowHeight), 0, rows)
    const lastRow = clamp(Math.ceil((relativeTop + viewHeight) / rowHeight), 0, rows)
    const speed = Math.abs(motion.velocity)
    const idle = speed < IDLE_SPEED
    const down = motion.direction >= 0
    const ahead = idle ? RENDER_IDLE_ROWS : RENDER_AHEAD_ROWS
    const behind = idle ? RENDER_IDLE_ROWS : RENDER_BEHIND_ROWS
    const start = clamp(firstRow - (down ? behind : ahead), 0, rows)
    const end = clamp(lastRow + (down ? ahead : behind), 0, rows)

    const current = rangeRef.current
    const disjoint = start >= current.end || end <= current.start
    if (start !== current.start || end !== current.end) {
      const next = { start, end }
      rangeRef.current = next
      if (disjoint && fromScroll) {
        flushSync(() => setRange(next))
      } else {
        setRange(next)
      }
    }

    updateDemand(firstRow, lastRow, down, speed, idle, disjoint)
  }, [scrollContainerRef, updateDemand])

  useLayoutEffect(() => {
    const previous = latestRef.current
    if (!previous || previous.items !== items || previous.windowStart !== windowStart) {
      itemsVersionRef.current++
    }
    latestRef.current = { items, windowStart, itemHeight, itemsPerRow, totalRows, onRangeChange, getPrefetchId }
    if (!previous || previous.itemHeight !== itemHeight || previous.itemsPerRow !== itemsPerRow) {
      measure()
    }
    compute(!motionRef.current.scrolling)
  }, [items, windowStart, itemHeight, itemsPerRow, totalRows, onRangeChange, getPrefetchId, measure, compute])

  useEffect(() => {
    const container = scrollContainerRef?.current
    if (!container) return
    const motion = motionRef.current

    const settle = () => {
      if (settleTimerRef.current) {
        clearTimeout(settleTimerRef.current)
        settleTimerRef.current = null
      }
      motion.scrolling = false
      measure()
      compute(true, true)
    }

    const handleScrollEnd = () => {
      measure()
      compute(false, true)
    }

    const handleScroll = () => {
      motion.scrolling = true
      if (!rafRef.current) {
        rafRef.current = requestAnimationFrame(() => {
          rafRef.current = 0
          compute(false, true)
        })
      }
      if (settleTimerRef.current) clearTimeout(settleTimerRef.current)
      settleTimerRef.current = setTimeout(settle, SETTLE_MS)
    }

    const handleVisibility = () => {
      if (document.visibilityState === 'visible') settle()
    }

    const resizeObserver = new ResizeObserver(() => {
      measure()
      compute(!motion.scrolling)
    })
    resizeObserver.observe(container)
    const listParent = listRef.current?.parentElement
    if (listParent && listParent !== container) resizeObserver.observe(listParent)

    container.addEventListener('scroll', handleScroll, { passive: true })
    container.addEventListener('scrollend', handleScrollEnd)
    document.addEventListener('visibilitychange', handleVisibility)
    window.addEventListener('pageshow', settle)

    return () => {
      resizeObserver.disconnect()
      container.removeEventListener('scroll', handleScroll)
      container.removeEventListener('scrollend', handleScrollEnd)
      document.removeEventListener('visibilitychange', handleVisibility)
      window.removeEventListener('pageshow', settle)
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
      rafRef.current = 0
      if (settleTimerRef.current) clearTimeout(settleTimerRef.current)
      settleTimerRef.current = null
    }
  }, [scrollContainerRef, measure, compute])

  const startRow = Math.min(range.start, totalRows)
  const endRow = Math.min(range.end, totalRows)
  const startIndex = startRow * itemsPerRow
  const endIndex = Math.min(catalogSize, endRow * itemsPerRow)

  const visibleItems = []
  for (let i = startIndex; i < endIndex; i++) {
    const item = items[i - windowStart]
    if (item) {
      visibleItems.push(renderItem(item, i))
    } else if (renderPlaceholder) {
      visibleItems.push(renderPlaceholder(i))
    } else {
      visibleItems.push(defaultPlaceholder(i, itemHeight))
    }
  }

  return (
    <div ref={listRef} style={{ height: `${totalRows * itemHeight}px`, position: 'relative' }}>
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          transform: `translateY(${startRow * itemHeight}px)`,
          willChange: 'transform'
        }}
      >
        <div className={className}>
          {visibleItems}
        </div>
      </div>
    </div>
  )
}

export const MemoizedVirtualScroller = memo(VirtualScroller, (prevProps, nextProps) => {
  return (
    prevProps.items === nextProps.items &&
    prevProps.totalCount === nextProps.totalCount &&
    prevProps.windowStart === nextProps.windowStart &&
    prevProps.itemHeight === nextProps.itemHeight &&
    prevProps.itemsPerRow === nextProps.itemsPerRow &&
    prevProps.className === nextProps.className &&
    prevProps.renderItem === nextProps.renderItem &&
    prevProps.renderPlaceholder === nextProps.renderPlaceholder &&
    prevProps.onRangeChange === nextProps.onRangeChange &&
    prevProps.getPrefetchId === nextProps.getPrefetchId
  )
})
