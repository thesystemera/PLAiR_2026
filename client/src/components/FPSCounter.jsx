import { useState, useEffect } from 'react'
import { screenRefreshMs, watchScreenRefresh } from '../lib/screenRefresh'
import { addFrameWork, takeFrameWork, watchFrameStats } from '../lib/frameStats'

const DROPPED_FACTOR = 1.5
const MAX_INTERVALS = 4096

const formatWork = (work, name) => {
  const entry = work[name]
  if (entry) return entry.avg.toFixed(1)
  return name.endsWith('gpu') && !work.gpuTimers ? '?' : '-'
}

export function FPSCounter() {
  const [stats, setStats] = useState({ fps: 60, screenHz: 0, dropped: 0, worst: 0, work: {} })

  useEffect(() => {
    const intervals = new Float32Array(MAX_INTERVALS)
    let count = 0
    let frames = 0
    let worst = 0
    let last = null
    let frameBegan = 0
    let windowStart = performance.now()
    let rafId
    const stopWatching = watchScreenRefresh()
    const stopStats = watchFrameStats()
    const channel = new MessageChannel()
    channel.port1.onmessage = () => addFrameWork('main', performance.now() - frameBegan)
    const closeWindow = () => {
      const refresh = screenRefreshMs()
      let dropped = 0
      for (let i = 0; i < count; i++) {
        if (intervals[i] > refresh * DROPPED_FACTOR) dropped += Math.round(intervals[i] / refresh) - 1
      }
      return { refresh, dropped }
    }
    const tick = (now) => {
      frameBegan = now
      channel.port2.postMessage(0)
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
          screenHz: Math.round(1000 / refresh),
          dropped,
          worst: Math.round(worst),
          work: takeFrameWork()
        })
        count = 0
        frames = 0
        worst = 0
        windowStart = now
      }
      rafId = requestAnimationFrame(tick)
    }
    rafId = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(rafId)
      channel.port1.close()
      stopWatching()
      stopStats()
    }
  }, [])

  const color = stats.dropped === 0 ? '#22c55e' : stats.dropped <= 3 ? '#eab308' : '#ef4444'
  const work = stats.work

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
      <div>{stats.fps} FPS · screen {stats.screenHz} Hz · {stats.dropped} dropped · worst {stats.worst} ms</div>
      <div style={{ color: '#e5e7eb' }}>
        ms per frame: main {formatWork(work, 'main')} · scene js {formatWork(work, 'scene js')} · scene gpu {formatWork(work, 'scene gpu')} · art gpu {formatWork(work, 'art gpu')}
      </div>
    </div>
  )
}
