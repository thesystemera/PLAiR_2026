import { useEffect, useRef, useState, memo, useLayoutEffect } from 'react'

export function VirtualScroller({
  items = [],
  totalCount = 0,
  windowStart = 0,
  itemHeight = 100,
  itemsPerRow = 1,
  renderItem,
  onLoadForward,
  onLoadBackward,
  hasMore = false,
  loadThreshold = 15,
  bufferItems = 25,
  scrollContainerRef,
  className = '',
  renderPlaceholder = null
}) {
  const gridRef = useRef(null)
  const [visibleRange, setVisibleRange] = useState({ start: 0, end: bufferItems + 50 })
  const loadingRef = useRef({ forward: false, backward: false })
  const lastWindowChangeRef = useRef(0)
  const prevWindowStartRef = useRef(windowStart)
  const scrollAdjustmentRef = useRef(false)
  const resizeObserverRef = useRef(null)
  const lastUserScrollRef = useRef(0)
  const isUserScrollingRef = useRef(false)

  const WINDOW_SHIFT_DEBOUNCE = 100
  const USER_SCROLL_TIMEOUT = 150

  // Register RAF source for debugging
  useEffect(() => {
    window.registerRAFSource?.('VirtualScroller')
  }, [])

  useLayoutEffect(() => {
    const prev = prevWindowStartRef.current
    const delta = windowStart - prev

    if (delta !== 0 && !scrollAdjustmentRef.current) {
      const scrollContainer = scrollContainerRef?.current
      const timeSinceUserScroll = Date.now() - lastUserScrollRef.current

      if (scrollContainer && !isUserScrollingRef.current && timeSinceUserScroll > USER_SCROLL_TIMEOUT) {
        const rowsTrimmed = Math.floor(delta / itemsPerRow)
        const scrollAdjustment = rowsTrimmed * itemHeight

        if (scrollAdjustment !== 0) {
          const targetScrollTop = scrollContainer.scrollTop + scrollAdjustment
          window.__rafDebug?.sources && (window.__rafDebug.sources['VirtualScroller'] = (window.__rafDebug.sources['VirtualScroller'] || 0) + 1)
          requestAnimationFrame(() => {
            if (scrollContainerRef.current) {
              scrollContainerRef.current.scrollTop = targetScrollTop
            }
          })
          scrollAdjustmentRef.current = true
          window.__rafDebug?.sources && (window.__rafDebug.sources['VirtualScroller'] = (window.__rafDebug.sources['VirtualScroller'] || 0) + 1)
          requestAnimationFrame(() => {
            scrollAdjustmentRef.current = false
          })
        }
      }
    }

    if (prev !== windowStart) {
      lastWindowChangeRef.current = Date.now()
      prevWindowStartRef.current = windowStart
    }
  }, [windowStart, scrollContainerRef, itemsPerRow, itemHeight])

  useEffect(() => {
    const container = scrollContainerRef?.current
    if (!container) return

    const handleResize = () => {
      const scrollTop = container.scrollTop
      const clientHeight = container.clientHeight

      const startRow = Math.floor(scrollTop / itemHeight)
      const visibleRows = Math.ceil(clientHeight / itemHeight)
      const bufferRows = Math.ceil(bufferItems / itemsPerRow)

      const startRowAligned = Math.max(0, startRow - bufferRows)
      const absoluteStartIndex = startRowAligned * itemsPerRow

      const absoluteEndIndex = (startRow + visibleRows + bufferRows) * itemsPerRow

      setVisibleRange({ start: absoluteStartIndex, end: absoluteEndIndex })
    }

    const resizeObserver = new ResizeObserver(handleResize)
    resizeObserver.observe(container)
    resizeObserverRef.current = resizeObserver

    return () => {
      if (resizeObserverRef.current) {
        resizeObserverRef.current.disconnect()
      }
    }
  }, [scrollContainerRef, itemHeight, itemsPerRow, bufferItems])

  useEffect(() => {
    let rafId = null
    let scrollTimeoutId = null

    const handleScroll = (e) => {
      const target = e.target
      if (!target || scrollAdjustmentRef.current) return

      lastUserScrollRef.current = Date.now()
      isUserScrollingRef.current = true

      if (scrollTimeoutId) clearTimeout(scrollTimeoutId)
      scrollTimeoutId = setTimeout(() => {
        isUserScrollingRef.current = false
      }, USER_SCROLL_TIMEOUT)

      if (rafId) cancelAnimationFrame(rafId)

      rafId = requestAnimationFrame(() => {
        const scrollTop = target.scrollTop
        const clientHeight = target.clientHeight

        const startRow = Math.floor(scrollTop / itemHeight)
        const visibleRows = Math.ceil(clientHeight / itemHeight)
        const bufferRows = Math.ceil(bufferItems / itemsPerRow)

        const startRowAligned = Math.max(0, startRow - bufferRows)
        const absoluteStartIndex = startRowAligned * itemsPerRow
        const absoluteEndIndex = (startRow + visibleRows + bufferRows) * itemsPerRow

        setVisibleRange(prev => {
          const startDiff = Math.abs(prev.start - absoluteStartIndex)
          const endDiff = Math.abs(prev.end - absoluteEndIndex)

          if (startDiff >= itemsPerRow || endDiff >= itemsPerRow) {
            return { start: absoluteStartIndex, end: absoluteEndIndex }
          }
          return prev
        })

        const itemsLength = items?.length || 0

        const viewportStartInWindow = Math.max(0, absoluteStartIndex - windowStart)
        const viewportEndInWindow = absoluteEndIndex - windowStart

        const timeSinceWindowChange = Date.now() - lastWindowChangeRef.current
        const canTriggerLoads = timeSinceWindowChange > WINDOW_SHIFT_DEBOUNCE

        if (canTriggerLoads && !loadingRef.current.forward && onLoadForward && hasMore) {
          if (viewportEndInWindow > itemsLength - loadThreshold) {
            loadingRef.current.forward = true
            onLoadForward()
            setTimeout(() => { loadingRef.current.forward = false }, 300)
          }
        }

        if (canTriggerLoads && !loadingRef.current.backward && onLoadBackward && windowStart > 0) {
          if (viewportStartInWindow < loadThreshold) {
            loadingRef.current.backward = true
            onLoadBackward()
            setTimeout(() => { loadingRef.current.backward = false }, 300)
          }
        }
      })
    }

    const container = scrollContainerRef?.current || gridRef.current?.closest('.overflow-y-auto')
    if (container) {
      container.addEventListener('scroll', handleScroll, { passive: true })
      return () => {
        container.removeEventListener('scroll', handleScroll)
        if (rafId) cancelAnimationFrame(rafId)
        if (scrollTimeoutId) clearTimeout(scrollTimeoutId)
      }
    }
  }, [onLoadForward, onLoadBackward, hasMore, items?.length, windowStart, scrollContainerRef, itemHeight, itemsPerRow, bufferItems, loadThreshold])

  const catalogSize = totalCount || items.length
  const totalHeight = Math.ceil(catalogSize / itemsPerRow) * itemHeight

  const startAbs = visibleRange.start
  const endAbs = Math.min(catalogSize, visibleRange.end)

  const transformY = Math.floor(startAbs / itemsPerRow) * itemHeight

  const visibleItems = []

  for (let i = startAbs; i < endAbs; i++) {
    const itemIndex = i - windowStart
    const item = items[itemIndex]

    if (item) {
      visibleItems.push(renderItem(item, i))
    } else {
      if (renderPlaceholder) {
        visibleItems.push(renderPlaceholder(i))
      } else {
        visibleItems.push(
          <div
            key={`skeleton-${i}`}
            style={{ height: itemHeight }}
            className="w-full h-full p-2"
          >
            <div className="w-full h-full bg-white/[0.08] rounded-lg animate-pulse ring-1 ring-white/10" />
          </div>
        )
      }
    }
  }

  return (
    <div ref={gridRef} style={{ height: `${totalHeight}px`, position: 'relative' }}>
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          transform: `translateY(${transformY}px)`,
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
    prevProps.hasMore === nextProps.hasMore &&
    prevProps.className === nextProps.className &&
    prevProps.renderItem === nextProps.renderItem
  )
})