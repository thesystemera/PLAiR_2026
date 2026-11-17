import { useRef, useState, useEffect, useLayoutEffect, useCallback, forwardRef, useImperativeHandle } from 'react'
import { motion, AnimatePresence, useMotionValue } from 'framer-motion'
import { useDynamicTheme, PANEL, PANEL_SCROLL } from '../contexts/DynamicThemeContext'
import { EDGE_FADE_MASK } from '../lib/themeManager'
import { MOTION, createFade, stepFade } from '../lib/motion'
import { useUISelector } from '../contexts/UIStateContext'
import { useViewport } from '../contexts/ViewportContext'
import { scrollChrome } from '../lib/scrollChrome'

const { maskFadeTop, maskFadeBottom, maskFadeSide } = PANEL_SCROLL

const SCROLLER_MASK = EDGE_FADE_MASK(maskFadeTop, maskFadeBottom, maskFadeSide)

function scrollerMask(lift, hole, holeShown) {
  const edges = lift > 0 ? EDGE_FADE_MASK(maskFadeTop, maskFadeBottom, maskFadeSide, lift) : SCROLLER_MASK
  if (!hole || holeShown <= 0) return edges
  return `${edges}, radial-gradient(circle at 50% ${hole.y}px, rgba(0, 0, 0, ${(1 - holeShown).toFixed(3)}) ${hole.inner}px, black ${hole.outer}px)`
}

function scrollerMaskStyle(mask) {
  return { WebkitMaskImage: mask, WebkitMaskComposite: 'source-in', maskImage: mask, maskComposite: 'intersect' }
}

const SETTLE_MS = 500
const INPUT_WINDOW_MS = 300
const TOP_ZONE_PX = 10
const INDICATOR_HEIGHT = 80
const END_SLACK_PX = 2

let owner = null

export const Scroller = forwardRef(function Scroller({ children, getScrollLabel = null, onScroll = null, className = '', style = {}, hole = null, holeShown = false }, ref) {
  const scrollRef = useRef(null)
  const frameRef = useRef(null)
  const spacerRef = useRef(null)
  const holeRef = useRef(hole)
  const maskRef = useRef('')
  const atEndRef = useRef(false)
  const [holeFade] = useState(() => createFade(holeShown ? 1 : 0))

  useImperativeHandle(ref, () => scrollRef.current, [])
  const [scrollLabel, setScrollLabel] = useState('')
  const [showLabel, setShowLabel] = useState(false)
  const lastScrollRef = useRef({ top: 0, time: 0 })
  const gestureRef = useRef({ touching: false, pressed: false, inputAt: -Infinity, active: false })
  const latestRef = useRef(null)

  useEffect(() => {
    lastScrollRef.current.time = performance.now()
  }, [])

  useEffect(() => {
    window.registerRAFSource?.('Scroller')
  }, [])

  const { isMobile } = useViewport()
  const { getAccentColor, triggerEffect } = useDynamicTheme()
  const { reportInterfaceState, interfaceRef } = useUISelector(state => ({ reportInterfaceState: state.reportInterfaceState, interfaceRef: state.interfaceRef }))

  const mask = scrollerMask(scrollChrome.lift(), hole, holeFade.value)

  useLayoutEffect(() => {
    latestRef.current = { getScrollLabel, onScroll, triggerEffect, isMobile }
    holeRef.current = hole
    maskRef.current = mask
  })

  const writeMask = useCallback(() => {
    const node = frameRef.current
    const value = scrollerMask(scrollChrome.lift(), holeRef.current, holeFade.value)
    if (!node || maskRef.current === value) return
    maskRef.current = value
    node.style.webkitMaskImage = value
    node.style.maskImage = value
  }, [holeFade])

  useEffect(() => scrollChrome.follow(() => {
    writeMask()
    const scroller = scrollRef.current
    const spacer = spacerRef.current
    if (!scroller || !spacer) return
    const lift = scrollChrome.lift()
    const height = lift > 0 ? `${lift}px` : ''
    if (spacer.style.height === height) return
    spacer.style.height = height
    if (scroller === owner && atEndRef.current) scrollChrome.afterWrites(() => { scroller.scrollTop = scroller.scrollHeight })
  }), [writeMask])

  useEffect(() => {
    const target = holeShown ? 1 : 0
    if (holeFade.to === target && holeFade.value === target) return
    let frame = requestAnimationFrame(function step(now) {
      const value = stepFade(holeFade, target, now)
      writeMask()
      frame = value === target ? null : requestAnimationFrame(step)
    })
    return () => { if (frame !== null) cancelAnimationFrame(frame) }
  }, [holeShown, holeFade, writeMask])

  const indicatorY = useMotionValue(0)

  useEffect(() => {
    const container = scrollRef.current
    if (!container) return
    const gesture = gestureRef.current
    const ui = interfaceRef.current
    let frame = null
    let settleTimer = null

    const setActive = (active) => {
      if (gesture.active === active) return
      gesture.active = active
      if (active) owner = container
      reportInterfaceState({ isScrolling: active, chromeAway: active && latestRef.current.isMobile })
    }

    const settle = () => {
      settleTimer = null
      ui.scrollVelocity = 0
      setShowLabel(false)
      setActive(false)
    }

    const settleAfter = (ms) => {
      clearTimeout(settleTimer)
      settleTimer = setTimeout(settle, ms)
    }

    const update = () => {
      frame = null
      const { scrollTop, scrollHeight, clientHeight } = container
      const now = performance.now()
      const scrollable = scrollHeight - clientHeight
      const percent = scrollable > 0 ? scrollTop / scrollable : 0
      indicatorY.set(PANEL.headerHeight + percent * (clientHeight - PANEL.headerHeight - INDICATOR_HEIGHT))

      if (container.dataset.autoScroll) {
        lastScrollRef.current = { top: scrollTop, time: now }
        return
      }

      const latest = latestRef.current
      latest.triggerEffect('scroll', { scrollTop })

      const deltaTime = now - lastScrollRef.current.time
      const velocity = deltaTime > 0 ? Math.abs(scrollTop - lastScrollRef.current.top) / deltaTime : 0
      lastScrollRef.current = { top: scrollTop, time: now }

      if (latest.getScrollLabel) {
        const label = latest.getScrollLabel(scrollTop, scrollHeight, clientHeight)
        setScrollLabel(prevLabel => prevLabel !== label ? (label || '') : prevLabel)
      }

      atEndRef.current = scrollTop + clientHeight >= scrollHeight - END_SLACK_PX
      const away = scrollTop > TOP_ZONE_PX
      ui.scrollVelocity = away ? velocity : 0
      ui.scrollPosition = scrollTop

      const byListener = gesture.active || gesture.touching || gesture.pressed || now - gesture.inputAt < INPUT_WINDOW_MS
      setShowLabel(away && byListener)
      setActive(byListener && away)

      if (gesture.touching || gesture.pressed) clearTimeout(settleTimer)
      else settleAfter(SETTLE_MS)
    }

    const handleScroll = (e) => {
      latestRef.current.onScroll?.(e)
      window.__rafDebug?.sources && (window.__rafDebug.sources['Scroller'] = (window.__rafDebug.sources['Scroller'] || 0) + 1)
      if (frame === null) frame = requestAnimationFrame(update)
    }

    const noteInput = () => {
      gesture.inputAt = performance.now()
    }

    const handleTouchStart = () => {
      gesture.touching = true
      noteInput()
      clearTimeout(settleTimer)
    }

    const handleTouchEnd = () => {
      gesture.touching = false
      noteInput()
      settleAfter(container.scrollTop <= TOP_ZONE_PX ? 0 : SETTLE_MS)
    }

    const handlePointerUp = () => {
      gesture.pressed = false
      noteInput()
      settleAfter(SETTLE_MS)
    }

    const handlePointerDown = (e) => {
      if (e.pointerType !== 'mouse') return
      gesture.pressed = true
      noteInput()
      window.addEventListener('pointerup', handlePointerUp, { once: true })
    }

    container.addEventListener('scroll', handleScroll, { passive: true })
    container.addEventListener('touchstart', handleTouchStart, { passive: true })
    container.addEventListener('touchend', handleTouchEnd, { passive: true })
    container.addEventListener('touchcancel', handleTouchEnd, { passive: true })
    container.addEventListener('wheel', noteInput, { passive: true })
    container.addEventListener('keydown', noteInput)
    container.addEventListener('pointerdown', handlePointerDown)

    return () => {
      container.removeEventListener('scroll', handleScroll)
      container.removeEventListener('touchstart', handleTouchStart)
      container.removeEventListener('touchend', handleTouchEnd)
      container.removeEventListener('touchcancel', handleTouchEnd)
      container.removeEventListener('wheel', noteInput)
      container.removeEventListener('keydown', noteInput)
      container.removeEventListener('pointerdown', handlePointerDown)
      window.removeEventListener('pointerup', handlePointerUp)
      clearTimeout(settleTimer)
      if (frame !== null) cancelAnimationFrame(frame)
      gesture.touching = false
      gesture.pressed = false
      if (gesture.active) {
        ui.scrollVelocity = 0
        setActive(false)
      }
      if (owner === container) owner = null
    }
  }, [interfaceRef, reportInterfaceState, indicatorY])

  return (
    <div ref={frameRef} className="h-full relative overflow-hidden" style={scrollerMaskStyle(mask)}>
      <div
        ref={scrollRef}
        className={`h-full overflow-y-auto ${className}`}
        style={{ paddingTop: `${PANEL.headerHeight}px`, ...style }}
        data-scroller
      >
        {children}
        <div ref={spacerRef} aria-hidden="true" />
      </div>

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