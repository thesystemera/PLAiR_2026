import { useState, useEffect } from 'react'

export function FPSCounter() {
  const [fps, setFps] = useState(60)

  useEffect(() => {
    if (window.__rafDebug) {
      const intervalId = setInterval(() => {
        setFps(window.__rafDebug.count || 60)
      }, 1000)
      return () => clearInterval(intervalId)
    }

    let frames = 0
    let last = performance.now()
    let rafId
    const tick = (now) => {
      frames++
      if (now - last >= 1000) {
        setFps(Math.round((frames * 1000) / (now - last)))
        frames = 0
        last = now
      }
      rafId = requestAnimationFrame(tick)
    }
    rafId = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafId)
  }, [])

  const color = fps >= 55 ? '#22c55e' : fps >= 30 ? '#eab308' : '#ef4444'

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
      {fps} FPS
    </div>
  )
}
