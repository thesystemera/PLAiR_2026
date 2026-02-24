import { useState, useEffect } from 'react'

export function FPSCounter() {
  const [fps, setFps] = useState(60)

  useEffect(() => {
    // Read from the global RAF debugger - no separate RAF loop needed
    const intervalId = setInterval(() => {
      if (window.__rafDebug) {
        setFps(window.__rafDebug.count || 60)
      }
    }, 1000)
    
    return () => clearInterval(intervalId)
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
