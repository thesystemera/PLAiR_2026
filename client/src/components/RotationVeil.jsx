import { useEffect, useRef, useState } from 'react'
import { DURATION } from '../lib/motion'

const SETTLE_MS = 250
const LONGEST_MS = 1500
const VEIL = { position: 'fixed', inset: 0, background: '#000', pointerEvents: 'none', zIndex: 2147483000 }

export function RotationVeil() {
  const [phase, setPhase] = useState('idle')
  const phaseRef = useRef(phase)

  useEffect(() => {
    phaseRef.current = phase
  }, [phase])

  useEffect(() => {
    let settleTimer = null
    let longestTimer = null
    let frame = null
    const lift = () => {
      clearTimeout(settleTimer)
      clearTimeout(longestTimer)
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => { frame = requestAnimationFrame(() => setPhase('out')) })
    }
    const settle = () => {
      clearTimeout(settleTimer)
      settleTimer = setTimeout(lift, SETTLE_MS)
    }
    const rotate = () => {
      cancelAnimationFrame(frame)
      setPhase('in')
      settle()
      clearTimeout(longestTimer)
      longestTimer = setTimeout(lift, LONGEST_MS)
    }
    const resize = () => {
      if (phaseRef.current === 'in') settle()
    }
    const orientation = window.screen?.orientation
    if (orientation?.addEventListener) orientation.addEventListener('change', rotate)
    else window.addEventListener('orientationchange', rotate)
    window.addEventListener('resize', resize, { passive: true })
    return () => {
      if (orientation?.addEventListener) orientation.removeEventListener('change', rotate)
      else window.removeEventListener('orientationchange', rotate)
      window.removeEventListener('resize', resize)
      clearTimeout(settleTimer)
      clearTimeout(longestTimer)
      cancelAnimationFrame(frame)
    }
  }, [])

  useEffect(() => {
    if (phase !== 'out') return
    const timer = setTimeout(() => setPhase('idle'), DURATION.fade * 1000)
    return () => clearTimeout(timer)
  }, [phase])

  if (phase === 'idle') return null
  const veiled = phase === 'in'
  return (
    <div
      aria-hidden="true"
      style={{
        ...VEIL,
        opacity: veiled ? 1 : 0,
        transition: `opacity ${veiled ? DURATION.micro : DURATION.fade}s var(--ease-${veiled ? 'accelerate' : 'decelerate'})`,
        animation: veiled ? `rotation-veil-in ${DURATION.micro}s var(--ease-accelerate)` : undefined,
      }}
    />
  )
}
