const NEAR_FASTEST = 1.15
const CONFIRMATIONS = 3
const WINDOW_MS = 1000
const RECENT_WINDOWS = 5
const MAX_INTERVALS = 1024
const FALLBACK_MS = 1000 / 60

const intervals = new Float32Array(MAX_INTERVALS)
const recent = new Float32Array(RECENT_WINDOWS).fill(Infinity)
let recentIndex = 0
let count = 0
let last = null
let windowStart = 0
let users = 0
let rafId = 0

function closeWindow() {
  let windowMin = Infinity
  for (let i = 0; i < count; i++) if (intervals[i] < windowMin) windowMin = intervals[i]
  let near = 0
  for (let i = 0; i < count; i++) if (intervals[i] <= windowMin * NEAR_FASTEST) near++
  recent[recentIndex] = near >= CONFIRMATIONS && windowMin > 0 ? windowMin : Infinity
  recentIndex = (recentIndex + 1) % RECENT_WINDOWS
  count = 0
}

function tick(now) {
  if (last === null) {
    windowStart = now
  } else {
    if (count < MAX_INTERVALS) intervals[count++] = now - last
    if (now - windowStart >= WINDOW_MS) {
      closeWindow()
      windowStart = now
    }
  }
  last = now
  rafId = requestAnimationFrame(tick)
}

export function watchScreenRefresh() {
  if (users++ === 0) {
    last = null
    count = 0
    rafId = requestAnimationFrame(tick)
  }
  return () => {
    if (--users === 0) cancelAnimationFrame(rafId)
  }
}

export function screenRefreshMs() {
  let fastest = Infinity
  for (let i = 0; i < RECENT_WINDOWS; i++) if (recent[i] < fastest) fastest = recent[i]
  return Number.isFinite(fastest) ? fastest : FALLBACK_MS
}
