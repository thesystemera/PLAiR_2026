import { useEffect, useLayoutEffect, useRef, useState, useCallback, useMemo, memo } from 'react'
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
const MEASURE_EPSILON_PX = 0.5
const END_SLACK_PX = 4

const clamp = (value, min, max) => Math.max(min, Math.min(max, value))
const pinToEnd = (element) => { element.scrollTop = element.scrollHeight }
const shiftScroll = (element, delta) => { element.scrollTop += delta }

function fixedLayout(rows, rowHeight) {
  return {
    rows,
    rowHeight,
    total: rows * rowHeight,
    offset: row => row * rowHeight,
    rowAt: y => clamp(Math.floor(y / rowHeight), 0, rows),
  }
}

const rowKeyOf = (items, row, perRow, itemKey) => (
  perRow === 1 ? itemKey(items[row]) : `${perRow}:${itemKey(items[row * perRow])}`
)

function measuredLayout(items, perRow, itemKey, heights, estimate) {
  const rows = Math.ceil(items.length / perRow)
  const offsets = new Float64Array(rows + 1)
  const indexOf = new Map()
  for (let i = 0; i < rows; i++) {
    const key = rowKeyOf(items, i, perRow, itemKey)
    indexOf.set(key, i)
    offsets[i + 1] = offsets[i] + (heights.get(key) ?? estimate)
  }
  const rowAt = (y) => {
    if (y <= 0) return 0
    let low = 0
    let high = rows
    while (low < high) {
      const mid = (low + high + 1) >> 1
      if (offsets[mid] <= y) low = mid
      else high = mid - 1
    }
    return clamp(low, 0, rows)
  }
  return {
    rows,
    rowHeight: rows ? offsets[rows] / rows : estimate,
    total: offsets[rows],
    offset: row => offsets[clamp(row, 0, rows)],
    rowAt,
    indexOf,
  }
}

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
  itemKey = null,
  estimatedItemHeight = 100,
  stickToEnd = false,
  renderItem,
  renderPlaceholder = null,
  onRangeChange = null,
  getPrefetchId = null,
  scrollContainerRef,
  wrapItems = null,
  itemClassName = undefined,
  className = ''
}) {
  const measured = typeof itemKey === 'function'
  const listRef = useRef(null)
  const windowRef = useRef(null)
  const [range, setRange] = useState({ start: 0, end: INITIAL_ROWS })
  const rangeRef = useRef(range)
  const motionRef = useRef({ top: 0, time: 0, velocity: 0, direction: 1, listTop: 0, scrolling: false, samples: [] })
  const latestRef = useRef(null)
  const demandKeyRef = useRef('')
  const itemsVersionRef = useRef(0)
  const rafRef = useRef(0)
  const settleTimerRef = useRef(null)
  const [heights, setHeights] = useState(() => new Map())
  const heightsRef = useRef(heights)
  const atEndRef = useRef(false)
  const observedRef = useRef(null)

  const perRow = Math.max(1, itemsPerRow)
  const catalogSize = measured ? items.length : (totalCount || items.length)
  const totalRows = Math.ceil(catalogSize / perRow)

  const layout = useMemo(() => (
    measured
      ? measuredLayout(items, perRow, itemKey, heights, estimatedItemHeight)
      : fixedLayout(totalRows, itemHeight)
  ), [measured, items, perRow, itemKey, estimatedItemHeight, totalRows, itemHeight, heights])

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
    const { layout: current, perRow: rowItems, items: list, windowStart: offset, onRangeChange: onRange, getPrefetchId: getId } = latest
    const rows = current.rows
    const rowsPerSecond = speed * 1000 / current.rowHeight
    const minAhead = Math.ceil(PREFETCH_MIN_AHEAD_ITEMS / rowItems)
    const maxAhead = Math.ceil(PREFETCH_MAX_AHEAD_ITEMS / rowItems)
    const aheadRows = idle ? minAhead : clamp(Math.ceil(rowsPerSecond * PREFETCH_SECONDS), minAhead, maxAhead)
    const behindRows = Math.ceil((idle ? PREFETCH_IDLE_BEHIND_ITEMS : PREFETCH_BEHIND_ITEMS) / rowItems)
    const fetchStart = clamp(firstRow - (down ? behindRows : aheadRows), 0, rows)
    const fetchEnd = clamp(lastRow + (down ? aheadRows : behindRows), 0, rows)

    const key = `${firstRow}|${lastRow}|${fetchStart}|${fetchEnd}|${itemsVersionRef.current}`
    if (key === demandKeyRef.current) return
    demandKeyRef.current = key

    const focusIndex = (down ? firstRow : Math.max(firstRow, lastRow - 1)) * rowItems
    onRange?.(fetchStart * rowItems, fetchEnd * rowItems, focusIndex)

    if (!getId) return
    const size = coverSize()
    if (!size) {
      demandKeyRef.current = ''
      return
    }
    const ids = []
    const pushRow = (row) => {
      const base = row * rowItems
      for (let i = base; i < base + rowItems; i++) {
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
    if (!container || !latest || !latest.layout.rowHeight) return
    const { layout: current } = latest
    const rows = current.rows
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
    atEndRef.current = scrollTop + viewHeight >= container.scrollHeight - END_SLACK_PX

    const relativeTop = scrollTop - motion.listTop
    const firstRow = current.rowAt(relativeTop)
    const lastRow = clamp(current.rowAt(relativeTop + viewHeight) + 1, 0, rows)
    const speed = Math.abs(motion.velocity)
    const idle = speed < IDLE_SPEED
    const down = motion.direction >= 0
    const ahead = idle ? RENDER_IDLE_ROWS : RENDER_AHEAD_ROWS
    const behind = idle ? RENDER_IDLE_ROWS : RENDER_BEHIND_ROWS
    const start = clamp(firstRow - (down ? behind : ahead), 0, rows)
    const end = clamp(lastRow + (down ? ahead : behind), 0, rows)

    const previous = rangeRef.current
    const disjoint = start >= previous.end || end <= previous.start
    if (start !== previous.start || end !== previous.end) {
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
    latestRef.current = { items, windowStart, layout, perRow, onRangeChange, getPrefetchId }
    if (!previous || previous.layout.rowHeight !== layout.rowHeight || previous.perRow !== perRow) {
      measure()
    }
    const container = scrollContainerRef?.current
    if (measured && stickToEnd && container && atEndRef.current) pinToEnd(container)
    compute(!motionRef.current.scrolling)
  }, [items, windowStart, layout, perRow, onRangeChange, getPrefetchId, measure, compute, measured, stickToEnd, scrollContainerRef])

  useEffect(() => {
    if (!measured || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver((entries) => {
      const latest = latestRef.current
      const container = scrollContainerRef?.current
      if (!latest || !container) return
      const { layout: current } = latest
      const firstVisible = current.rowAt(container.scrollTop - motionRef.current.listTop)
      const known = heightsRef.current
      const next = new Map(known)
      let shiftAbove = 0
      let changed = false
      for (const entry of entries) {
        const key = entry.target.dataset.vsKey
        if (key === undefined) continue
        const height = entry.borderBoxSize?.[0]?.blockSize ?? entry.target.offsetHeight
        if (!height) continue
        const index = current.indexOf.get(key)
        const before = known.get(key) ?? (index === undefined ? height : current.offset(index + 1) - current.offset(index))
        if (Math.abs(height - before) < MEASURE_EPSILON_PX) {
          if (!next.has(key)) next.set(key, height)
          continue
        }
        next.set(key, height)
        changed = true
        if (index !== undefined && index < firstVisible) shiftAbove += height - before
      }
      heightsRef.current = next
      if (!changed) return
      if (shiftAbove && !atEndRef.current) shiftScroll(container, shiftAbove)
      setHeights(next)
    })
    observedRef.current = { observer, elements: new Set() }
    return () => {
      observer.disconnect()
      observedRef.current = null
    }
  }, [measured, scrollContainerRef])

  useLayoutEffect(() => {
    const observed = observedRef.current
    const windowEl = windowRef.current
    if (!measured || !observed || !windowEl) return
    const present = new Set(windowEl.querySelectorAll('[data-vs-key]'))
    for (const element of observed.elements) {
      if (!present.has(element)) {
        observed.observer.unobserve(element)
        observed.elements.delete(element)
      }
    }
    for (const element of present) {
      if (!observed.elements.has(element)) {
        observed.observer.observe(element)
        observed.elements.add(element)
      }
    }
  })

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
  const startIndex = startRow * perRow
  const endIndex = Math.min(catalogSize, endRow * perRow)

  const visibleItems = []
  if (measured) {
    for (let row = startRow; row < endRow; row++) {
      const key = rowKeyOf(items, row, perRow, itemKey)
      const first = row * perRow
      let cells = null
      if (perRow === 1) {
        cells = renderItem(items[first], first)
      } else {
        cells = []
        for (let i = first; i < Math.min(items.length, first + perRow); i++) cells.push(renderItem(items[i], i))
      }
      visibleItems.push(
        <div key={key} data-vs-key={key} className={itemClassName}>
          {cells}
        </div>
      )
    }
  }
  for (let i = startIndex; i < endIndex && !measured; i++) {
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
    <div ref={listRef} style={{ height: `${layout.total}px`, position: 'relative' }}>
      <div
        ref={windowRef}
        data-virtual-window
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          transform: `translateY(${layout.offset(startRow)}px)`,
          willChange: 'transform'
        }}
      >
        <div className={className}>
          {wrapItems ? wrapItems(visibleItems) : visibleItems}
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
    prevProps.itemKey === nextProps.itemKey &&
    prevProps.estimatedItemHeight === nextProps.estimatedItemHeight &&
    prevProps.stickToEnd === nextProps.stickToEnd &&
    prevProps.className === nextProps.className &&
    prevProps.itemClassName === nextProps.itemClassName &&
    prevProps.renderItem === nextProps.renderItem &&
    prevProps.renderPlaceholder === nextProps.renderPlaceholder &&
    prevProps.onRangeChange === nextProps.onRangeChange &&
    prevProps.getPrefetchId === nextProps.getPrefetchId &&
    prevProps.wrapItems === nextProps.wrapItems
  )
})
