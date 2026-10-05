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

const sceneOnMain = () => window.__plairScene?.thread?.() !== 'worker'

const otherWork = (work) => {
  if (!work.main) return '-'
  const ours = (sceneOnMain() ? work['scene js']?.avg || 0 : 0) + (work['art js']?.avg || 0)
  return Math.max(0, work.main.avg - ours).toFixed(1)
}

const perSceneFrame = (work, name, frames) => {
  const entry = work[name]
  const scenes = work['scene frames']?.count
  if (!entry || !scenes) return formatWork(work, name)
  return (entry.avg * frames / scenes).toFixed(1)
}

export function FPSCounter() {
  const [stats, setStats] = useState({ fps: 60, sceneFps: 0, frames: 1, screenHz: 0, dropped: 0, worst: 0, work: {} })

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
        const work = takeFrameWork(frames)
        setStats({
          fps: Math.round((frames * 1000) / (now - windowStart)),
          sceneFps: Math.round(((work['scene frames']?.count || 0) * 1000) / (now - windowStart)),
          frames,
          screenHz: Math.round(1000 / refresh),
          dropped,
          worst: Math.round(worst),
          work
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
      <div>{stats.fps} FPS · scene {stats.sceneFps} FPS · screen {stats.screenHz} Hz · {stats.dropped} dropped · worst {stats.worst} ms</div>
      <div style={{ color: '#e5e7eb' }}>
        page ms/frame: main {formatWork(work, 'main')} = {sceneOnMain() ? `scene js ${formatWork(work, 'scene js')} + ` : ''}art js {formatWork(work, 'art js')} + other {otherWork(work)} · art gpu {formatWork(work, 'art gpu')}
      </div>
      <div style={{ color: '#e5e7eb' }}>
        scene ({sceneOnMain() ? 'main thread' : 'worker'}) ms/frame: js {perSceneFrame(work, 'scene js', stats.frames)} · gpu {perSceneFrame(work, 'scene gpu', stats.frames)}
      </div>
    </div>
  )
}
