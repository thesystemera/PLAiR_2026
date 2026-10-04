import { useState, useEffect } from 'react'

const DROPPED_FACTOR = 1.5

export function FPSCounter() {
  const [stats, setStats] = useState({ fps: 60, dropped: 0, worst: 0 })

  useEffect(() => {
    let frames = 0
    let dropped = 0
    let worst = 0
    let interval = Infinity
    let last = null
    let windowStart = performance.now()
    let rafId
    const tick = (now) => {
      if (last !== null) {
        const delta = now - last
        interval = Math.min(interval, delta)
        if (delta > interval * DROPPED_FACTOR) dropped += Math.round(delta / interval) - 1
        worst = Math.max(worst, delta)
      }
      last = now
      frames++
      if (now - windowStart >= 1000) {
        setStats({ fps: Math.round((frames * 1000) / (now - windowStart)), dropped, worst: Math.round(worst) })
        frames = 0
        dropped = 0
        worst = 0
        interval = Infinity
        windowStart = now
      }
      rafId = requestAnimationFrame(tick)
    }
    rafId = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafId)
  }, [])

  const color = stats.dropped === 0 ? '#22c55e' : stats.dropped <= 3 ? '#eab308' : '#ef4444'

  return (
    <div
      style={{
        position: 'fixed',
        top: '10px',
        right: '10px',
        zIndex: 9999,
        backgroundColor: 'rgba(0, 0, 0, 0.8)',
        color: color,
        padding: '8px 12px',
        borderRadius: '6px',
        fontFamily: 'monospace',
        fontSize: '14px',
        fontWeight: 'bold',
        pointerEvents: 'none',
      }}
    >
      {stats.fps} FPS · {stats.dropped} dropped · worst {stats.worst} ms
    </div>
  )
}
