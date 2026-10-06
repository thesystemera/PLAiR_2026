import { useEffect, useRef } from 'react'
import { DURATION } from '../lib/motion'

const SETTLE_MS = 100
const CALM_FRAMES = 4
const CALM_FRAME_MS = 50
const LONGEST_MS = 1500
const VEIL = { position: 'fixed', inset: 0, background: '#000', pointerEvents: 'none', zIndex: 2147483000, opacity: 0, visibility: 'hidden' }

const screenOrientation = () => {
  const type = window.screen?.orientation?.type
  if (type) return type
  if (typeof window.orientation === 'number') return String(window.orientation)
  return window.innerWidth > window.innerHeight ? 'landscape' : 'portrait'
}

export function RotationVeil() {
  const veilRef = useRef(null)

  useEffect(() => {
    const veil = veilRef.current
    const landscapeQuery = window.matchMedia?.('(orientation: landscape)')
    const orientation = window.screen?.orientation
    let shown = screenOrientation()
    let covered = false
    let coveredAt = 0
    let lastResize = 0
    let lastFrame = 0
    let calm = 0
    let frame = null

    const lift = () => {
      covered = false
      cancelAnimationFrame(frame)
      frame = null
      veil.style.transition = `opacity ${DURATION.quick}s var(--ease-decelerate)`
      veil.style.opacity = '0'
    }

    const watch = (now) => {
      calm = lastFrame && now - lastFrame < CALM_FRAME_MS ? calm + 1 : 0
      lastFrame = now
      if ((calm >= CALM_FRAMES && now - lastResize >= SETTLE_MS) || now - coveredAt >= LONGEST_MS) {
        lift()
        return
      }
      frame = requestAnimationFrame(watch)
    }

    const cover = () => {
      if (document.hidden) return
      lastResize = performance.now()
      calm = 0
      lastFrame = 0
      if (covered) return
      covered = true
      coveredAt = lastResize
      veil.style.transition = 'none'
      veil.style.visibility = 'visible'
      veil.style.opacity = '1'
      frame = requestAnimationFrame(watch)
    }

    const check = () => {
      const now = screenOrientation()
      if (now !== shown) {
        shown = now
        cover()
      } else if (covered) {
        lastResize = performance.now()
        calm = 0
      }
    }

    const hidden = () => {
      if (!covered) veil.style.visibility = 'hidden'
    }

    veil.addEventListener('transitionend', hidden)
    window.addEventListener('resize', check, { passive: true })
    landscapeQuery?.addEventListener?.('change', check)
    if (orientation?.addEventListener) orientation.addEventListener('change', check)
    else window.addEventListener('orientationchange', check)
    return () => {
      veil.removeEventListener('transitionend', hidden)
      window.removeEventListener('resize', check)
      landscapeQuery?.removeEventListener?.('change', check)
      if (orientation?.addEventListener) orientation.removeEventListener('change', check)
      else window.removeEventListener('orientationchange', check)
      cancelAnimationFrame(frame)
    }
  }, [])

  return <div ref={veilRef} aria-hidden="true" style={VEIL} />
}
