import { safeStorage } from './safeStorage'

const OVERRIDE_KEY = 'plair_panel_reveal'
const LEARNED_KEY = 'plair_panel_reveal_learned'
const COLLAPSED_SHARE = 0.04
const WATCH_MS = 700
const SLOW_FRAME_FACTOR = 1.8
const SLOW_FRAMES_FOR_JANK = 3
const JANKY_SLIDES_TO_LEARN = 2

let jankySlides = 0
let watching = false

export function panelRevealEnabled() {
  const override = safeStorage.get(OVERRIDE_KEY)
  if (override === 'on') return true
  if (override === 'off') return false
  return safeStorage.get(LEARNED_KEY) === '1'
}

export function finalPanelWidths(row, stateKeys, nextStates) {
  const rowStyle = getComputedStyle(row)
  const inner = row.clientWidth - parseFloat(rowStyle.paddingLeft) - parseFloat(rowStyle.paddingRight)
  const gap = parseFloat(rowStyle.columnGap) || 0
  const panel = row.querySelector(':scope > [data-shader-panel]')
  const panelStyle = panel ? getComputedStyle(panel) : null
  const border = panelStyle ? parseFloat(panelStyle.borderLeftWidth) + parseFloat(panelStyle.borderRightWidth) : 0
  const open = stateKeys.filter(key => nextStates[key])
  if (!open.length) return null
  const closed = stateKeys.length - open.length
  const share = (inner - gap * (stateKeys.length - 1) - closed * COLLAPSED_SHARE * inner) / open.length
  return Object.fromEntries(open.map(key => [key, Math.max(0, share - border)]))
}

export function watchPanelSlide() {
  if (watching || typeof requestAnimationFrame === 'undefined') return
  watching = true
  const deltas = []
  let last = null
  let start = null
  const step = (now) => {
    if (last !== null) deltas.push(now - last)
    if (start === null) start = now
    last = now
    if (now - start < WATCH_MS && !document.hidden) {
      requestAnimationFrame(step)
      return
    }
    watching = false
    if (document.hidden || deltas.length < 4) return
    const interval = Math.min(20, Math.max(6, Math.min(...deltas)))
    const slow = deltas.filter(delta => delta > interval * SLOW_FRAME_FACTOR).length
    if (slow < SLOW_FRAMES_FOR_JANK) return
    jankySlides++
    if (jankySlides >= JANKY_SLIDES_TO_LEARN) safeStorage.set(LEARNED_KEY, '1')
  }
  requestAnimationFrame(step)
}
