import { useState, useEffect } from 'react'

const DROPPED_FACTOR = 1.5
const NEAR_FASTEST = 1.15
const FASTEST_CONFIRMATIONS = 3
const MAX_INTERVALS = 4096

export function FPSCounter() {
  const [stats, setStats] = useState({ fps: 60, screenHz: 0, dropped: 0, worst: 0 })

  useEffect(() => {
    const intervals = new Float32Array(MAX_INTERVALS)
    let count = 0
    let frames = 0
    let worst = 0
    let fastest = Infinity
    let last = null
    let windowStart = performance.now()
    let rafId
    const closeWindow = () => {
      let windowMin = Infinity
      for (let i = 0; i < count; i++) windowMin = Math.min(windowMin, intervals[i])
      let near = 0
      for (let i = 0; i < count; i++) if (intervals[i] <= windowMin * NEAR_FASTEST) near++
      if (near >= FASTEST_CONFIRMATIONS) fastest = Math.min(fastest, windowMin)
      const refresh = Number.isFinite(fastest) ? fastest : windowMin
      let dropped = 0
      for (let i = 0; i < count; i++) {
        if (intervals[i] > refresh * DROPPED_FACTOR) dropped += Math.round(intervals[i] / refresh) - 1
      }
      return { refresh, dropped }
    }
    const tick = (now) => {
      if (last !== null) {
        const delta = now - last
        if (count < MAX_INTERVALS) intervals[count++] = delta
        worst = Math.max(worst, delta)
      }
      last = now
      frames++
      if (now - windowStart >= 1000) {
        const { refresh, dropped } = closeWindow()
        setStats({
          fps: Math.round((frames * 1000) / (now - windowStart)),
          screenHz: Number.isFinite(refresh) ? Math.round(1000 / refresh) : 0,
          dropped,
          worst: Math.round(worst)
        })
        count = 0
        frames = 0
        worst = 0
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
      {stats.fps} FPS · screen {stats.screenHz} Hz · {stats.dropped} dropped · worst {stats.worst} ms
    </div>
  )
}
