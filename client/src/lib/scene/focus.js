const GRID = 64
const AIM_RADIUS = 0.12
const AIM_PERCENTILE = 0.8
const SPRING = 60
const DAMPING = 2 * Math.sqrt(SPRING) * 0.7
const scratch = new Float32Array(GRID * GRID)

export const FOCUS_RANGE = 0.05
export const APERTURE = 7
const APERTURE_PER_BLUR = 0.15
const FLAT_BLUR_REACH = 0.0004
const MAX_BLUR_LEVELS = 4.5

export function depthGrid(data, width, height) {
  const grid = new Float32Array(GRID * GRID)
  for (let gy = 0; gy < GRID; gy++) {
    const y0 = Math.floor(gy * height / GRID)
    const y1 = Math.max(y0 + 1, Math.floor((gy + 1) * height / GRID))
    for (let gx = 0; gx < GRID; gx++) {
      const x0 = Math.floor(gx * width / GRID)
      const x1 = Math.max(x0 + 1, Math.floor((gx + 1) * width / GRID))
      let total = 0
      for (let y = y0; y < y1; y++) {
        for (let x = x0; x < x1; x++) total += data[(y * width + x) * 4 + 1]
      }
      grid[gy * GRID + gx] = total / ((y1 - y0) * (x1 - x0) * 255)
    }
  }
  return grid
}

function aimDepth(grid, u, v, radius, flipY) {
  const row = flipY ? 1 - v : v
  const x0 = Math.max(0, Math.floor((u - radius) * GRID))
  const x1 = Math.min(GRID - 1, Math.ceil((u + radius) * GRID))
  const y0 = Math.max(0, Math.floor((row - radius) * GRID))
  const y1 = Math.min(GRID - 1, Math.ceil((row + radius) * GRID))
  let n = 0
  for (let y = y0; y <= y1; y++) {
    for (let x = x0; x <= x1; x++) scratch[n++] = grid[y * GRID + x]
  }
  if (!n) return 0.5
  const window = scratch.subarray(0, n).sort()
  return window[Math.min(n - 1, Math.floor(n * AIM_PERCENTILE))]
}

function aimFor(frame, offsetX = 0, offsetY = 0, zoom = 1) {
  const px = frame.offsetX + offsetX - frame.shiftX
  const py = frame.offsetY + offsetY - frame.shiftY
  const cos = Math.cos(frame.rotation)
  const sin = Math.sin(frame.rotation)
  return {
    u: (cos * px + sin * py) / frame.scale + 0.5,
    v: (-sin * px + cos * py) / frame.scale + 0.5,
    radius: AIM_RADIUS / Math.max(0.5, frame.frameScale * frame.scale * zoom),
  }
}

export function focusTarget(grid, frame, flipY, offsetX, offsetY, zoom) {
  const aim = aimFor(frame, offsetX, offsetY, zoom)
  return aimDepth(grid, aim.u, aim.v, aim.radius, flipY)
}

export function createFocus() {
  return { depth: 0.6, speed: 0, primed: false, frame: { offsetX: 0, offsetY: 0, shiftX: 0, shiftY: 0, rotation: 0, scale: 1, frameScale: 1 } }
}

function stepFocus(focus, target, dt) {
  if (!focus.primed) {
    focus.depth = target
    focus.speed = 0
    focus.primed = true
    return focus.depth
  }
  const step = Math.min(dt, 1 / 30)
  focus.speed += (SPRING * (target - focus.depth) - DAMPING * focus.speed) * step
  focus.depth = Math.min(1, Math.max(0, focus.depth + focus.speed * step))
  return focus.depth
}

function frameOf(uniforms, out) {
  const resolution = uniforms.u_canvas_resolution.value
  out.offsetX = uniforms.u_frame_offset.value.x
  out.offsetY = uniforms.u_frame_offset.value.y
  out.shiftX = (uniforms.u_parallax.value.x + uniforms.u_glitch.value.x) / Math.max(1, resolution.x)
  out.shiftY = (uniforms.u_parallax.value.y + uniforms.u_glitch.value.y) / Math.max(1, resolution.y)
  out.rotation = uniforms.u_rotation.value
  out.scale = uniforms.u_scale.value
  out.frameScale = uniforms.u_frame_scale.value
  return out
}

export function applyFocus(uniforms, focus, grid, flipY, dt, fx, depthOfField) {
  uniforms.u_has_depth_map.value = grid ? 1 : 0
  if (!grid) {
    uniforms.u_max_blur.value = depthOfField ? Math.min(MAX_BLUR_LEVELS, Math.log2(1 + fx.blur * FLAT_BLUR_REACH * uniforms.u_tex_resolution.value.y)) : 0
    return
  }
  const frame = frameOf(uniforms, focus.frame)
  uniforms.u_focal_depth.value = stepFocus(focus, focusTarget(grid, frame, flipY), dt)
  uniforms.u_focal_range.value = FOCUS_RANGE
  uniforms.u_max_blur.value = depthOfField ? APERTURE * (1 + fx.aperture) + fx.blur * APERTURE_PER_BLUR : 0
}

export function detailGrid(data, width, height) {
  const detail = new Float32Array(GRID * GRID)
  const colour = new Float32Array(GRID * GRID * 3)
  for (let gy = 0; gy < GRID; gy++) {
    const y0 = Math.floor(gy * height / GRID)
    const y1 = Math.max(y0 + 1, Math.floor((gy + 1) * height / GRID))
    for (let gx = 0; gx < GRID; gx++) {
      const x0 = Math.floor(gx * width / GRID)
      const x1 = Math.max(x0 + 1, Math.floor((gx + 1) * width / GRID))
      let sum = 0
      let squares = 0
      let r = 0
      let g = 0
      let b = 0
      let n = 0
      for (let y = y0; y < y1; y++) {
        for (let x = x0; x < x1; x++) {
          const i = (y * width + x) * 4
          const luma = data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114
          sum += luma
          squares += luma * luma
          r += data[i]
          g += data[i + 1]
          b += data[i + 2]
          n++
        }
      }
      const cell = gy * GRID + gx
      detail[cell] = Math.sqrt(Math.max(0, squares / n - (sum / n) ** 2))
      colour[cell * 3] = r / n
      colour[cell * 3 + 1] = g / n
      colour[cell * 3 + 2] = b / n
    }
  }
  return { detail, colour }
}

function rankGrid(values) {
  const sorted = Float32Array.from(values).sort()
  return Float32Array.from(values, value => {
    let lo = 0
    let hi = sorted.length
    while (lo < hi) {
      const mid = (lo + hi) >> 1
      if (sorted[mid] < value) lo = mid + 1
      else hi = mid
    }
    return lo / (sorted.length - 1)
  })
}

function boxMean(values, radius) {
  const out = new Float32Array(values.length)
  for (let y = 0; y < GRID; y++) {
    for (let x = 0; x < GRID; x++) {
      let total = 0
      let n = 0
      for (let yy = Math.max(0, y - radius); yy <= Math.min(GRID - 1, y + radius); yy++) {
        for (let xx = Math.max(0, x - radius); xx <= Math.min(GRID - 1, x + radius); xx++) {
          total += values[yy * GRID + xx]
          n++
        }
      }
      out[y * GRID + x] = total / n
    }
  }
  return out
}

function colourDistinctness(colour) {
  const mean = [0, 0, 0]
  const cells = GRID * GRID
  for (let i = 0; i < cells; i++) for (let c = 0; c < 3; c++) mean[c] += colour[i * 3 + c] / cells
  const channels = [0, 1, 2].map(c => boxMean(Float32Array.from({ length: cells }, (_, i) => colour[i * 3 + c]), 1))
  return Float32Array.from({ length: cells }, (_, i) => Math.hypot(channels[0][i] - mean[0], channels[1][i] - mean[1], channels[2][i] - mean[2]))
}

export function interestPoints(depth, cover, flipY, count = 5) {
  const margin = Math.round(GRID / 20)
  const surroundings = boxMean(depth, GRID / 8)
  const cues = [
    rankGrid(depth.map((value, i) => value - surroundings[i])),
    rankGrid(boxMean(cover.detail, 1)),
    rankGrid(colourDistinctness(cover.colour)),
  ]
  const interest = boxMean(Float32Array.from(depth, (_, i) => (cues[0][i] + cues[1][i] + cues[2][i]) / 3), 1)
  const inside = cell => {
    const x = cell % GRID
    const y = Math.floor(cell / GRID)
    return x >= margin && y >= margin && x < GRID - margin && y < GRID - margin
  }
  const sorted = Array.from(interest.keys()).filter(inside).sort((a, b) => interest[b] - interest[a])
  const strong = interest[sorted[Math.floor(sorted.length * 0.15)]]
  const points = []
  const taken = new Set()
  for (const cell of sorted) {
    if (points.length >= count) break
    const x = cell % GRID
    const y = Math.floor(cell / GRID)
    if (taken.has(cell) || points.some(p => Math.hypot(p.x - x, p.y - y) < GRID / 5)) continue
    const region = new Set([cell])
    const queue = [cell]
    while (queue.length) {
      const at = queue.pop()
      const ax = at % GRID
      const ay = Math.floor(at / GRID)
      for (const [nx, ny] of [[ax + 1, ay], [ax - 1, ay], [ax, ay + 1], [ax, ay - 1]]) {
        const next = ny * GRID + nx
        if (nx < 0 || ny < 0 || nx >= GRID || ny >= GRID || region.has(next) || interest[next] < strong) continue
        region.add(next)
        queue.push(next)
      }
    }
    for (const member of region) taken.add(member)
    points.push({ x, y, size: Math.sqrt(region.size) / GRID })
  }
  return points.map(({ x, y, size }) => ({
    u: (x + 0.5) / GRID,
    v: flipY ? 1 - (y + 0.5) / GRID : (y + 0.5) / GRID,
    zoom: Math.min(2.4, Math.max(1.1, 0.6 / Math.max(size, 0.05))),
  }))
}
