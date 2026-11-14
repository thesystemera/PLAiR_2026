import { PANEL_SCROLL } from './themeManager'

const FADE = {
  top: parseFloat(PANEL_SCROLL.maskFadeTop),
  bottom: parseFloat(PANEL_SCROLL.maskFadeBottom),
  side: parseFloat(PANEL_SCROLL.maskFadeSide),
}
const EDGES = ['top', 'bottom', 'left', 'right']
const STRIP_OPAQUE = 'rgba(0, 0, 0, 1)'
const STRIP_CLEAR = 'rgba(0, 0, 0, 0)'

const groups = new Set()
const listeners = new Set()
let ready = false
let forced = null
let requestRedraw = null

function notify() {
  for (const listener of listeners) listener()
}

function planStrip(strip, edge, sourceBox, scaleX, scaleY, box, dpr) {
  const left = Math.round(box.left * dpr)
  const top = Math.round(box.top * dpr)
  const right = Math.round(box.right * dpr)
  const bottom = Math.round(box.bottom * dpr)
  const perX = scaleX / dpr
  const perY = scaleY / dpr
  const x = edge === 'right' ? right - Math.ceil(FADE.side * dpr) : left
  const y = edge === 'bottom' ? bottom - Math.ceil(FADE.bottom * dpr) : top
  const spanX = edge === 'left' || edge === 'right' ? Math.ceil(FADE.side * dpr) : right - left
  const spanY = edge === 'top' ? Math.ceil(FADE.top * dpr) : edge === 'bottom' ? Math.ceil(FADE.bottom * dpr) : bottom - top
  const width = Math.max(1, Math.ceil(spanX * perX))
  const height = Math.max(1, Math.ceil(spanY * perY))
  strip.sx = (x / dpr - sourceBox.left) * scaleX
  strip.sy = (y / dpr - sourceBox.top) * scaleY
  const key = `${x - left},${y - top},${width},${height},${perX},${perY}`
  if (key === strip.key) return
  strip.key = key
  strip.layout = { width, height, left: (x - left) / dpr, top: (y - top) / dpr, cssWidth: width / scaleX, cssHeight: height / scaleY }
  strip.ramp = edge === 'top' ? [0, 0, 0, FADE.top * scaleY]
    : edge === 'bottom' ? [0, spanY * perY, 0, spanY * perY - FADE.bottom * scaleY]
      : edge === 'left' ? [0, 0, FADE.side * scaleX, 0]
        : [spanX * perX, 0, spanX * perX - FADE.side * scaleX, 0]
}

function layoutStrip(strip) {
  const layout = strip.layout
  if (!layout) return
  strip.layout = null
  const canvas = strip.canvas
  canvas.width = layout.width
  canvas.height = layout.height
  const style = canvas.style
  style.left = `${layout.left}px`
  style.top = `${layout.top}px`
  style.width = `${layout.cssWidth}px`
  style.height = `${layout.cssHeight}px`
  const ctx = strip.ctx || (strip.ctx = canvas.getContext('2d'))
  if (!ctx) return
  strip.gradient = ctx.createLinearGradient(...strip.ramp)
  strip.gradient.addColorStop(0, STRIP_OPAQUE)
  strip.gradient.addColorStop(1, STRIP_CLEAR)
}

function paintStrip(strip, source) {
  const ctx = strip.ctx
  if (!ctx) return
  const { width, height } = strip.canvas
  ctx.globalCompositeOperation = 'copy'
  ctx.drawImage(source, strip.sx, strip.sy, width, height, 0, 0, width, height)
  ctx.globalCompositeOperation = 'destination-in'
  ctx.fillStyle = strip.gradient
  ctx.fillRect(0, 0, width, height)
}

export const EDGE_FADE_EDGES = EDGES

export const edgeFade = {
  subscribe(listener) {
    listeners.add(listener)
    return () => listeners.delete(listener)
  },
  active: () => forced ?? ready,
  setSource(redraw) {
    requestRedraw = redraw
    const next = !!redraw
    if (ready === next) return
    ready = next
    notify()
  },
  register(wrapper, canvases) {
    const group = { wrapper, strips: Object.fromEntries(EDGES.map(edge => [edge, { canvas: canvases[edge], ctx: null, gradient: null, key: '', layout: null, ramp: null, sx: 0, sy: 0 }])) }
    groups.add(group)
    requestRedraw?.()
    return () => groups.delete(group)
  },
  paint(source) {
    if (!groups.size) return
    const sourceBox = source.getBoundingClientRect()
    if (sourceBox.width < 1 || sourceBox.height < 1) return
    const scaleX = source.width / sourceBox.width
    const scaleY = source.height / sourceBox.height
    const dpr = window.devicePixelRatio || 1
    const shown = []
    for (const group of groups) {
      const box = group.wrapper.getBoundingClientRect()
      if (box.width < 1 || box.height < 1 || box.right <= sourceBox.left || box.left >= sourceBox.right || box.bottom <= sourceBox.top || box.top >= sourceBox.bottom) continue
      for (const edge of EDGES) planStrip(group.strips[edge], edge, sourceBox, scaleX, scaleY, box, dpr)
      shown.push(group)
    }
    for (const group of shown) {
      for (const edge of EDGES) {
        const strip = group.strips[edge]
        layoutStrip(strip)
        paintStrip(strip, source)
      }
    }
  },
}

if (typeof window !== 'undefined') {
  window.__plairEdgeFade = {
    mode: () => (edgeFade.active() ? 'strips' : 'mask'),
    scrollers: () => groups.size,
    force: (mode) => {
      forced = mode === 'strips' ? true : mode === 'mask' ? false : null
      notify()
      requestRedraw?.()
    },
  }
}
