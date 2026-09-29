const MAX_WAIT_MS = 4000
const FADE_MS = 500

const pending = new Set(['scene', 'playback'])
let hidden = false

function hideSplash() {
  if (hidden || typeof document === 'undefined') return
  hidden = true
  const splash = document.getElementById('splash')
  if (!splash) return
  splash.classList.add('splash-out')
  setTimeout(() => splash.remove(), FADE_MS + 100)
}

export function splashReady(part) {
  if (hidden || !pending.delete(part)) return
  if (pending.size === 0) requestAnimationFrame(() => requestAnimationFrame(hideSplash))
}

if (typeof window !== 'undefined') setTimeout(hideSplash, MAX_WAIT_MS)
