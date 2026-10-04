export const PROBE_GRID = 16
export const MAX_LIGHTS = 4

const CELLS = PROBE_GRID * PROBE_GRID
const GRID_SMOOTHING_PER_SECOND = 14
const LIGHT_FOLLOW_PER_SECOND = 10
const KICK_DECAY_SECONDS = 0.18
const MIN_LIGHT_SPACING = 0.22
const CONTRAST_FLOOR = 0.03
const MAX_INTENSITY = 2.5

const base = new Float32Array(CELLS * 3)
const grid = new Float32Array(CELLS * 3)
const luma = new Float32Array(CELLS)
const order = Array.from({ length: CELLS }, (_, i) => i)
const lights = new Float32Array(MAX_LIGHTS * 4)
const lightColors = new Float32Array(MAX_LIGHTS * 3)
const picked = Array.from({ length: MAX_LIGHTS }, () => ({ x: 0, y: 0, r: 0, g: 0, b: 0, intensity: 0, taken: false }))
const state = { version: 0, lastBlendAt: 0, kickLevel: 0, kickAt: 0, pulse: 0, level: 0, hasProbe: false, glow: null }
const debug = { off: false, kick: null, level: null }

export function publishLightProbe(rgbaBottomUp) {
  for (let row = 0; row < PROBE_GRID; row++) {
    for (let col = 0; col < PROBE_GRID; col++) {
      const src = ((PROBE_GRID - 1 - row) * PROBE_GRID + col) * 4
      const dst = (row * PROBE_GRID + col) * 3
      base[dst] = rgbaBottomUp[src] / 255
      base[dst + 1] = rgbaBottomUp[src + 1] / 255
      base[dst + 2] = rgbaBottomUp[src + 2] / 255
    }
  }
  if (!state.hasProbe) grid.set(base)
  state.hasProbe = true
}

export function setLightGlow(glow) {
  state.glow = glow
  return () => {
    if (state.glow === glow) state.glow = null
  }
}

export function publishLightLevel(level) {
  if (Math.abs(level - state.level) > 1 / 512 || (level === 0 && state.level !== 0)) state.version++
  state.level = level
}

export function publishBeat(kick, pulse) {
  if (kick > 0) {
    const now = performance.now()
    state.kickLevel = Math.max(kick, currentKick(now))
    state.kickAt = now
  }
  state.pulse = pulse
}

function currentKick(now) {
  return state.kickLevel * Math.exp(-Math.max(0, now - state.kickAt) / 1000 / KICK_DECAY_SECONDS)
}

function blendGrid(blend) {
  for (let row = 0; row < PROBE_GRID; row++) {
    for (let col = 0; col < PROBE_GRID; col++) {
      const cell = row * PROBE_GRID + col
      const glow = state.glow ? state.glow((col + 0.5) / PROBE_GRID, 1 - (row + 0.5) / PROBE_GRID) : null
      for (let c = 0; c < 3; c++) {
        const i = cell * 3 + c
        const target = glow ? base[i] + glow[c] * (1 - base[i]) : base[i]
        grid[i] += (target - grid[i]) * blend
      }
      luma[cell] = grid[cell * 3] * 0.2126 + grid[cell * 3 + 1] * 0.7152 + grid[cell * 3 + 2] * 0.0722
    }
  }
}

function findLightSources() {
  let mean = 0
  for (let i = 0; i < CELLS; i++) mean += luma[i]
  mean /= CELLS
  order.sort((a, b) => luma[b] - luma[a])
  const found = []
  for (const cell of order) {
    if (found.length === MAX_LIGHTS) break
    const contrast = (luma[cell] - mean) / Math.max(mean, CONTRAST_FLOOR)
    if (contrast <= 0) break
    const x = ((cell % PROBE_GRID) + 0.5) / PROBE_GRID
    const y = (Math.floor(cell / PROBE_GRID) + 0.5) / PROBE_GRID
    if (found.some(light => Math.hypot(light.x - x, light.y - y) < MIN_LIGHT_SPACING)) continue
    const level = Math.max(luma[cell], 1e-3)
    found.push({
      x, y,
      r: grid[cell * 3] / level, g: grid[cell * 3 + 1] / level, b: grid[cell * 3 + 2] / level,
      intensity: Math.min(MAX_INTENSITY, contrast),
    })
  }
  return found
}

function followLights(found, follow) {
  for (const slot of picked) slot.taken = false
  for (const light of found) {
    let best = null
    let bestDistance = Infinity
    for (const slot of picked) {
      if (slot.taken) continue
      const distance = slot.intensity > 0.01 ? Math.hypot(slot.x - light.x, slot.y - light.y) : 2
      if (distance < bestDistance) {
        best = slot
        bestDistance = distance
      }
    }
    best.taken = true
    if (best.intensity <= 0.01) {
      best.x = light.x
      best.y = light.y
    }
    best.x += (light.x - best.x) * follow
    best.y += (light.y - best.y) * follow
    best.r += (light.r - best.r) * follow
    best.g += (light.g - best.g) * follow
    best.b += (light.b - best.b) * follow
    best.intensity += (light.intensity - best.intensity) * follow
  }
  for (const slot of picked) {
    if (!slot.taken) slot.intensity -= slot.intensity * follow
  }
  let moved = 0
  picked.forEach((slot, i) => {
    const values = [slot.x, slot.y, 0, slot.intensity]
    for (let k = 0; k < 4; k++) {
      moved = Math.max(moved, Math.abs(lights[i * 4 + k] - values[k]))
      lights[i * 4 + k] = values[k]
    }
    lightColors[i * 3] = slot.r
    lightColors[i * 3 + 1] = slot.g
    lightColors[i * 3 + 2] = slot.b
  })
  return moved
}

export function readLightProbe(now) {
  if (state.hasProbe && state.lastBlendAt !== now) {
    const dt = state.lastBlendAt ? Math.min(0.1, Math.max(0, now - state.lastBlendAt) / 1000) : 0
    state.lastBlendAt = now
    blendGrid(1 - Math.exp(-GRID_SMOOTHING_PER_SECOND * dt))
    const moved = followLights(findLightSources(), dt ? 1 - Math.exp(-LIGHT_FOLLOW_PER_SECOND * dt) : 1)
    if (moved > 1 / 1024) state.version++
  }
  const kick = debug.kick ?? currentKick(now)
  const level = debug.level ?? state.level
  const active = state.hasProbe && !debug.off && level > 0.001
  return { lights, lightColors, kick: kick < 0.002 ? 0 : kick, pulse: state.pulse, level, version: state.version, active, key: active ? `${state.version}|${kick}|${state.pulse}` : 'off' }
}

if (typeof window !== 'undefined') {
  window.__plairLight = {
    read: () => {
      const probe = readLightProbe(performance.now())
      return {
        active: probe.active,
        kick: probe.kick,
        pulse: probe.pulse,
        level: +probe.level.toFixed(3),
        lights: picked.map(slot => ({ x: +slot.x.toFixed(2), y: +slot.y.toFixed(2), intensity: +slot.intensity.toFixed(2), color: [slot.r, slot.g, slot.b].map(v => +v.toFixed(2)) })),
      }
    },
    kick: (level = 1) => publishBeat(level, state.pulse),
    debug: (options) => { Object.assign(debug, options); state.version++ },
  }
}
