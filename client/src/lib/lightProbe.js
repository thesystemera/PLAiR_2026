export const PROBE_GRID = 16
export const MAX_LIGHTS = 4

const CELLS = PROBE_GRID * PROBE_GRID
const GRID_SMOOTHING_PER_SECOND = 14
const LIGHT_FOLLOW_PER_SECOND = 10
const KICK_DECAY_SECONDS = 0.18
const MIN_LIGHT_SPACING = 0.22
const CONTRAST_FLOOR = 0.03
const MAX_INTENSITY = 2.5
const CONSUMER_TIMEOUT_MS = 250
const STALE_PROBE_MS = 1000
const MOTION_QUIET_MS = 300

const base = new Float32Array(CELLS * 3)
const grid = new Float32Array(CELLS * 3)
const luma = new Float32Array(CELLS)
const taken = new Uint8Array(CELLS)
const found = Array.from({ length: MAX_LIGHTS }, () => ({ x: 0, y: 0, r: 0, g: 0, b: 0, intensity: 0 }))
const lights = new Float32Array(MAX_LIGHTS * 4)
const lightColors = new Float32Array(MAX_LIGHTS * 3)
const picked = Array.from({ length: MAX_LIGHTS }, () => ({ x: 0, y: 0, r: 0, g: 0, b: 0, intensity: 0, taken: false }))
const state = {
  version: 0, lastBlendAt: 0, kickLevel: 0, kickAt: 0, pulse: 0, level: 0, target: 0, hasProbe: false, glow: null,
  consumerAt: -Infinity, probeAt: -Infinity, motionAt: -Infinity, wasActive: false, snap: false,
}
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
  const now = performance.now()
  if (!state.hasProbe) grid.set(base)
  if (now - state.probeAt > STALE_PROBE_MS) state.snap = true
  state.probeAt = now
  state.hasProbe = true
}

export function noteLightConsumer(now) {
  state.consumerAt = now
}

export function noteLayoutMotion() {
  state.motionAt = performance.now()
}

export function lightProbeWanted(now) {
  if (debug.off || now - state.consumerAt > CONSUMER_TIMEOUT_MS || now - state.motionAt < MOTION_QUIET_MS) return false
  return (debug.level ?? Math.max(state.level, state.target)) > 0
}

export function setLightGlow(glow) {
  state.glow = glow
  return () => {
    if (state.glow === glow) state.glow = null
  }
}

export function publishLightLevel(level, target) {
  if (Math.abs(level - state.level) > 1 / 512 || (level === 0 && state.level !== 0)) state.version++
  state.level = level
  state.target = target
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
  const contrastBase = Math.max(mean, CONTRAST_FLOOR)
  taken.fill(0)
  let count = 0
  for (let pick = 0; pick < CELLS && count < MAX_LIGHTS; pick++) {
    let cell = 0
    let brightest = -Infinity
    for (let i = 0; i < CELLS; i++) {
      if (!taken[i] && luma[i] > brightest) {
        brightest = luma[i]
        cell = i
      }
    }
    taken[cell] = 1
    const contrast = (brightest - mean) / contrastBase
    if (contrast <= 0) break
    const x = ((cell % PROBE_GRID) + 0.5) / PROBE_GRID
    const y = (Math.floor(cell / PROBE_GRID) + 0.5) / PROBE_GRID
    let crowded = false
    for (let k = 0; k < count && !crowded; k++) crowded = Math.hypot(found[k].x - x, found[k].y - y) < MIN_LIGHT_SPACING
    if (crowded) continue
    const level = Math.max(brightest, 1e-3)
    const light = found[count++]
    light.x = x
    light.y = y
    light.r = grid[cell * 3] / level
    light.g = grid[cell * 3 + 1] / level
    light.b = grid[cell * 3 + 2] / level
    light.intensity = Math.min(MAX_INTENSITY, contrast)
  }
  return count
}

function followLights(count, follow) {
  for (const slot of picked) slot.taken = false
  for (let n = 0; n < count; n++) {
    const light = found[n]
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
  for (let i = 0; i < MAX_LIGHTS; i++) {
    const slot = picked[i]
    const at = i * 4
    moved = Math.max(moved, Math.abs(lights[at] - slot.x), Math.abs(lights[at + 1] - slot.y), Math.abs(lights[at + 2]), Math.abs(lights[at + 3] - slot.intensity))
    lights[at] = slot.x
    lights[at + 1] = slot.y
    lights[at + 2] = 0
    lights[at + 3] = slot.intensity
    lightColors[i * 3] = slot.r
    lightColors[i * 3 + 1] = slot.g
    lightColors[i * 3 + 2] = slot.b
  }
  return moved
}

export function readLightProbe(now) {
  const kick = debug.kick ?? currentKick(now)
  const level = debug.level ?? state.level
  const active = state.hasProbe && !debug.off && level > 0.001
  if (active && state.lastBlendAt !== now) {
    const dt = state.lastBlendAt ? Math.min(0.1, Math.max(0, now - state.lastBlendAt) / 1000) : 0
    const snap = state.snap || !state.wasActive
    state.snap = false
    state.lastBlendAt = now
    blendGrid(snap ? 1 : 1 - Math.exp(-GRID_SMOOTHING_PER_SECOND * dt))
    const moved = followLights(findLightSources(), snap || !dt ? 1 : 1 - Math.exp(-LIGHT_FOLLOW_PER_SECOND * dt))
    if (moved > 1 / 1024) state.version++
  }
  state.wasActive = active
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
    probe: () => Array.from(base, value => Math.round(value * 255)),
    kick: (level = 1) => publishBeat(level, state.pulse),
    debug: (options) => { Object.assign(debug, options); state.version++ },
  }
}
