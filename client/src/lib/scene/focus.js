const GRID = 64
const AIM_RADIUS = 0.12
const AIM_PERCENTILE = 0.8
const SPRING = 60
const DAMPING = 2 * Math.sqrt(SPRING) * 0.7
const scratch = new Float32Array(GRID * GRID)

export const FOCUS_RANGE = 0.05
export const APERTURE = 7
const APERTURE_PER_BLUR = 0.15

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

export function applyFocus(uniforms, focus, grid, flipY, dt, blur) {
  uniforms.u_has_depth_map.value = grid ? 1 : 0
  if (!grid) return
  const frame = frameOf(uniforms, focus.frame)
  uniforms.u_focal_depth.value = stepFocus(focus, focusTarget(grid, frame, flipY), dt)
  uniforms.u_focal_range.value = FOCUS_RANGE
  uniforms.u_max_blur.value = APERTURE + blur * APERTURE_PER_BLUR
}
