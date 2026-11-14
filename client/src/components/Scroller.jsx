import { memo, useRef, useState, useCallback, useEffect, forwardRef, useImperativeHandle, useSyncExternalStore } from 'react'
import { motion, AnimatePresence, useMotionValue } from 'framer-motion'
import { useDynamicTheme, PANEL, PANEL_SCROLL } from '../contexts/DynamicThemeContext'
import { EDGE_FADE_MASK } from '../lib/themeManager'
import { MOTION } from '../lib/motion'
import { edgeFade, EDGE_FADE_EDGES } from '../lib/edgeFade'
import { useUISelector } from '../contexts/UIStateContext'

const { maskFadeTop, maskFadeBottom, maskFadeSide } = PANEL_SCROLL

const SCROLLER_MASK = EDGE_FADE_MASK(maskFadeTop, maskFadeBottom, maskFadeSide)

function scrollerMaskStyle(mask) {
  return { WebkitMaskImage: mask, WebkitMaskComposite: 'source-in', maskImage: mask, maskComposite: 'intersect' }
}

const SCROLLER_MASK_STYLE = scrollerMaskStyle(SCROLLER_MASK)
const SCROLLER_STRIPS_STYLE = { isolation: 'isolate' }

const EdgeFadeStrips = memo(function EdgeFadeStrips({ wrapperRef }) {
  const canvasesRef = useRef({})

  useEffect(() => edgeFade.register(wrapperRef.current, canvasesRef.current), [wrapperRef])

  return EDGE_FADE_EDGES.map(edge => (
    <canvas
      key={edge}
      ref={(canvas) => { canvasesRef.current[edge] = canvas }}
      aria-hidden="true"
      width={1}
      height={1}
      className="absolute pointer-events-none z-[90]"
    />
  ))
})

export const Scroller = forwardRef(function Scroller({ children, getScrollLabel = null, onScroll = null, className = '', style = {}, mask = null }, ref) {
  const scrollRef = useRef(null)
  const wrapperRef = useRef(null)
  const fadeWithStrips = useSyncExternalStore(edgeFade.subscribe, edgeFade.active) && !mask

  useImperativeHandle(ref, () => scrollRef.current)
  const [scrollLabel, setScrollLabel] = useState('')
  const [showLabel, setShowLabel] = useState(false)
  const hideTimeoutRef = useRef(null)
  const rafRef = useRef(null)
  const isTouchingRef = useRef(false)
  const lastScrollRef = useRef({ top: 0, time: 0 })

  useEffect(() => {
    lastScrollRef.current.time = performance.now()
  }, [])

  useEffect(() => {
    window.registerRAFSource?.('Scroller')
  }, [])

  const { getAccentColor, triggerEffect } = useDynamicTheme()
  const { reportInterfaceState, interfaceRef } = useUISelector(state => ({ reportInterfaceState: state.reportInterfaceState, interfaceRef: state.interfaceRef }))

  const reportScrollState = useCallback((isScrolling, scrollVelocity, scrollPosition) => {
    const current = interfaceRef.current
    current.scrollVelocity = scrollVelocity
    if (scrollPosition !== undefined) current.scrollPosition = scrollPosition
    if (current.isScrolling !== isScrolling) {
      reportInterfaceState({ isScrolling })
    }
  }, [interfaceRef, reportInterfaceState])

  const indicatorY = useMotionValue(0)

  const handleScroll = useCallback((e) => {
    if (onScroll) {
      onScroll(e)
    }

    const container = scrollRef.current
    if (!container) return

    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current)
    }

    window.__rafDebug?.sources && (window.__rafDebug.sources['Scroller'] = (window.__rafDebug.sources['Scroller'] || 0) + 1)
    rafRef.current = requestAnimationFrame(() => {
      const { scrollTop, scrollHeight, clientHeight } = container

      if (container.dataset.autoScroll) {
        lastScrollRef.current = { top: scrollTop, time: performance.now() }
        const scrollable = scrollHeight - clientHeight
        const pct = scrollable > 0 ? scrollTop / scrollable : 0
        indicatorY.set(PANEL.headerHeight + pct * (clientHeight - PANEL.headerHeight - 80))
        return
      }

      triggerEffect('scroll', { scrollTop })

      const now = performance.now()
      const deltaTime = now - lastScrollRef.current.time
      const deltaScroll = Math.abs(scrollTop - lastScrollRef.current.top)
      const velocity = deltaTime > 0 ? deltaScroll / deltaTime : 0

      lastScrollRef.current = { top: scrollTop, time: now }

      const scrollableHeight = scrollHeight - clientHeight
      const percent = scrollableHeight > 0 ? scrollTop / scrollableHeight : 0

      const indicatorHeight = 80
      const availableHeight = clientHeight - PANEL.headerHeight - indicatorHeight
      const indicatorPosition = PANEL.headerHeight + (percent * availableHeight)
      indicatorY.set(indicatorPosition)

      if (getScrollLabel) {
        const label = getScrollLabel(scrollTop, scrollHeight, clientHeight)
        setScrollLabel(prevLabel => prevLabel !== label ? (label || '') : prevLabel)
      }

      const isActuallyScrolling = scrollTop > 10
      setShowLabel(isActuallyScrolling)

      if (isActuallyScrolling) {
        reportScrollState(true, velocity, scrollTop)
      } else {
        reportScrollState(false, 0, scrollTop)
      }

      if (hideTimeoutRef.current) {
        clearTimeout(hideTimeoutRef.current)
      }

      if (!isTouchingRef.current) {
        hideTimeoutRef.current = setTimeout(() => {
          setShowLabel(false)
          reportScrollState(false, 0)
        }, 500)
      }
    })
  }, [getScrollLabel, onScroll, indicatorY, triggerEffect, reportScrollState])

  const handleTouchStart = useCallback(() => {
    isTouchingRef.current = true
    reportScrollState(true, interfaceRef.current.scrollVelocity)
  }, [reportScrollState, interfaceRef])

  const handleTouchEnd = useCallback(() => {
    isTouchingRef.current = false
    const container = scrollRef.current
    const scrollTop = container?.scrollTop || 0

    if (scrollTop <= 10) {
      reportScrollState(false, 0)
    } else {
      if (hideTimeoutRef.current) {
        clearTimeout(hideTimeoutRef.current)
      }
      hideTimeoutRef.current = setTimeout(() => {
        reportScrollState(false, 0)
      }, 500)
    }
  }, [reportScrollState])

  useEffect(() => {
    const container = scrollRef.current
    if (!container) return

    container.addEventListener('scroll', handleScroll, { passive: true })
    container.addEventListener('touchstart', handleTouchStart, { passive: true })
    container.addEventListener('touchend', handleTouchEnd, { passive: true })
    container.addEventListener('touchcancel', handleTouchEnd, { passive: true })

    return () => {
      container.removeEventListener('scroll', handleScroll)
      container.removeEventListener('touchstart', handleTouchStart)
      container.removeEventListener('touchend', handleTouchEnd)
      container.removeEventListener('touchcancel', handleTouchEnd)
      if (hideTimeoutRef.current) {
        clearTimeout(hideTimeoutRef.current)
      }
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current)
      }
      reportScrollState(false, 0)
    }
  }, [handleScroll, handleTouchStart, handleTouchEnd, reportScrollState])

  return (
    <div
      ref={wrapperRef}
      className="h-full relative overflow-hidden"
      style={fadeWithStrips ? SCROLLER_STRIPS_STYLE : mask ? scrollerMaskStyle(`${SCROLLER_MASK}, ${mask}`) : SCROLLER_MASK_STYLE}
    >
      <div
        ref={scrollRef}
        className={`h-full overflow-y-auto ${className}`}
        style={{ paddingTop: `${PANEL.headerHeight}px`, ...style }}
        data-scroller
      >
        {children}
      </div>

      {fadeWithStrips && <EdgeFadeStrips wrapperRef={wrapperRef} />}

      <AnimatePresence>
        {showLabel && (
          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.9 }}
            transition={MOTION.quickOpacityScale}
            style={{
              y: indicatorY,
              top: 0,
              position: 'absolute'
            }}
            className="right-4 pointer-events-none z-[100]"
          >
            <div
              className={`rounded-full shadow-2xl font-bold text-sm ${scrollLabel ? 'px-4 py-2' : 'w-3 h-16 bg-white/40'}`}
              style={{
                background: scrollLabel ? `linear-gradient(135deg, ${getAccentColor(0.9)}, ${getAccentColor(0.7)})` : undefined,
                boxShadow: `0 8px 32px ${getAccentColor(0.4)}`,
                border: `1px solid ${getAccentColor(0.5)}`
              }}
            >
              {scrollLabel || ''}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
})