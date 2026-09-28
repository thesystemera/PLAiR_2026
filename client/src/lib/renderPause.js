export const MODAL_OPEN_PAUSE_MS = 750
export const MODAL_CLOSE_PAUSE_MS = 420

const pause = { until: 0 }

export function pauseSceneRendering(ms) {
  pause.until = Math.max(pause.until, performance.now() + ms)
}

export function isSceneRenderingPaused(time) {
  return time < pause.until
}
