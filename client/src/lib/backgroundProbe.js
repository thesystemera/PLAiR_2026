import { PROBE_GRID } from './lightProbe'

const SUBSAMPLES = 3
const FALLBACK_TOP = [0.231, 0.509, 0.964]
const FALLBACK_BOTTOM = [0.545, 0.360, 0.964]

const pixelCache = new WeakMap()
const color = new Float32Array(4)
const blended = new Float32Array(4)
const output = new Uint8Array(PROBE_GRID * PROBE_GRID * 4)

function makeCanvas(width, height) {
  if (typeof OffscreenCanvas !== 'undefined') return new OffscreenCanvas(width, height)
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  return canvas
}

export function artworkPixels(image) {
  if (!image || !image.width || !image.height || image.data) return null
  const cached = pixelCache.get(image)
  if (cached) return cached
  try {
    const ctx = image.getContext ? image.getContext('2d', { willReadFrequently: true }) : null
    if (!ctx) return null
    const pixels = { data: ctx.getImageData(0, 0, image.width, image.height).data, width: image.width, height: image.height }
    pixelCache.set(image, pixels)
    return pixels
  } catch {
    return null
  }
}

export function textPixels(draw, sourceWidth, sourceHeight, width, height) {
  const ctx = makeCanvas(width, height).getContext('2d', { willReadFrequently: true })
  ctx.setTransform(width / sourceWidth, 0, 0, height / sourceHeight, 0, 0)
  draw(ctx, sourceWidth, sourceHeight)
  return { data: ctx.getImageData(0, 0, width, height).data, width, height }
}

function sample(pixels, u, v, out) {
  if (!pixels) {
    out[0] = 0; out[1] = 0; out[2] = 0; out[3] = 0
    return out
  }
  const { data, width, height } = pixels
  const x = Math.min(1, Math.max(0, u)) * width - 0.5
  const y = (1 - Math.min(1, Math.max(0, v))) * height - 0.5
  const x0 = Math.max(0, Math.min(width - 1, Math.floor(x)))
  const y0 = Math.max(0, Math.min(height - 1, Math.floor(y)))
  const x1 = Math.min(width - 1, x0 + 1)
  const y1 = Math.min(height - 1, y0 + 1)
  const fx = Math.min(1, Math.max(0, x - x0))
  const fy = Math.min(1, Math.max(0, y - y0))
  const a = (y0 * width + x0) * 4
  const b = (y0 * width + x1) * 4
  const c = (y1 * width + x0) * 4
  const d = (y1 * width + x1) * 4
  for (let i = 0; i < 4; i++) {
    const top = data[a + i] + (data[b + i] - data[a + i]) * fx
    const bottom = data[c + i] + (data[d + i] - data[c + i]) * fx
    out[i] = (top + (bottom - top) * fy) / 255
  }
  return out
}

function backgroundAt(u, v, p) {
  const cover = p.coverX
  let cu = u
  let cv = v
  if (cover) cu = u * p.coverScale + p.coverOffset
  else cv = v * p.coverScale + p.coverOffset
  const framedX = (cu - 0.5) / p.frameScale + p.frameOffsetX
  const framedY = (cv - 0.5) / p.frameScale + p.frameOffsetY
  const px = framedX - p.shiftX
  const py = framedY - p.shiftY
  const fxU = (p.cos * px + p.sin * py) / p.scale + 0.5
  const fxV = (-p.sin * px + p.cos * py) / p.scale + 0.5

  if (p.transition < 1) {
    sample(p.previous, fxU, fxV, blended)
    sample(p.current, fxU, fxV, color)
    const t = p.transition * p.transition * (3 - 2 * p.transition)
    for (let i = 0; i < 4; i++) color[i] = blended[i] + (color[i] - blended[i]) * t
  } else {
    sample(p.current, fxU, fxV, color)
  }

  let r = (color[0] - 0.5) * p.contrast + 0.5
  let g = (color[1] - 0.5) * p.contrast + 0.5
  let b = (color[2] - 0.5) * p.contrast + 0.5
  r *= p.brightness
  g *= p.brightness
  b *= p.brightness
  const luma = r * 0.299 + g * 0.587 + b * 0.114
  r = luma + (r - luma) * p.saturation
  g = luma + (g - luma) * p.saturation
  b = luma + (b - luma) * p.saturation
  if (Math.abs(p.hue) > 0.001) {
    const base = r * 0.213 + g * 0.715 + b * 0.072
    const hr = base + p.hueCos * (r * 0.787 - g * 0.715 - b * 0.072) - p.hueSin * (-r * 0.213 - g * 0.715 + b * 0.928)
    const hg = base + p.hueCos * (-r * 0.213 + g * 0.285 - b * 0.072) + p.hueSin * (r * 0.143 - g * 0.285 + b * 0.142)
    const hb = base + p.hueCos * (-r * 0.213 - g * 0.715 + b * 0.928) + p.hueSin * (-r * 0.787 + g * 0.715 + b * 0.072)
    r = hr
    g = hg
    b = hb
  }
  r = Math.min(1, Math.max(0, r))
  g = Math.min(1, Math.max(0, g))
  b = Math.min(1, Math.max(0, b))

  if (color[3] < 0.01 && p.transition < 0.01) {
    const fallbackY = (-p.sin * framedX + p.cos * framedY) / (p.scale + 0.2) + 0.5
    r = (FALLBACK_BOTTOM[0] + (FALLBACK_TOP[0] - FALLBACK_BOTTOM[0]) * fallbackY) * 0.4
    g = (FALLBACK_BOTTOM[1] + (FALLBACK_TOP[1] - FALLBACK_BOTTOM[1]) * fallbackY) * 0.4
    b = (FALLBACK_BOTTOM[2] + (FALLBACK_TOP[2] - FALLBACK_BOTTOM[2]) * fallbackY) * 0.4
  }

  r *= p.flicker
  g *= p.flicker
  b *= p.flicker

  if (p.text) {
    sample(p.text, u - p.glitchU, v - p.glitchV, blended)
    if (blended[3] > 0.01) {
      r += (blended[0] - r) * blended[3]
      g += (blended[1] - g) * blended[3]
      b += (blended[2] - b) * blended[3]
    }
  }

  color[0] = r
  color[1] = g
  color[2] = b
}

export function sampleBackgroundProbe(p) {
  const canvasAspect = p.width / p.height
  const texAspect = p.texWidth / p.texHeight
  p.coverX = texAspect > canvasAspect
  p.coverScale = p.coverX ? canvasAspect / texAspect : texAspect / canvasAspect
  p.coverOffset = (1 - p.coverScale) / 2
  p.glitchU = p.glitchX / p.width
  p.glitchV = p.glitchY / p.height
  p.shiftX = p.parallaxX / p.width + p.glitchU
  p.shiftY = p.parallaxY / p.height + p.glitchV
  p.cos = Math.cos(p.rotation)
  p.sin = Math.sin(p.rotation)
  p.hueCos = Math.cos(p.hue)
  p.hueSin = Math.sin(p.hue)
  const step = 1 / (PROBE_GRID * SUBSAMPLES)
  for (let row = 0; row < PROBE_GRID; row++) {
    for (let col = 0; col < PROBE_GRID; col++) {
      let r = 0
      let g = 0
      let b = 0
      for (let sy = 0; sy < SUBSAMPLES; sy++) {
        for (let sx = 0; sx < SUBSAMPLES; sx++) {
          backgroundAt((col * SUBSAMPLES + sx + 0.5) * step, (row * SUBSAMPLES + sy + 0.5) * step, p)
          r += color[0]
          g += color[1]
          b += color[2]
        }
      }
      const at = (row * PROBE_GRID + col) * 4
      const n = SUBSAMPLES * SUBSAMPLES
      output[at] = Math.round(Math.min(1, Math.max(0, r / n)) * 255)
      output[at + 1] = Math.round(Math.min(1, Math.max(0, g / n)) * 255)
      output[at + 2] = Math.round(Math.min(1, Math.max(0, b / n)) * 255)
      output[at + 3] = 255
    }
  }
  return output
}
