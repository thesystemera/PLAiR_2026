const PROBE_COUNT = 9
const SMOOTHING_PER_SECOND = 14
const KICK_DECAY_SECONDS = 0.18

const base = new Float32Array(PROBE_COUNT * 3)
const colors = new Float32Array(PROBE_COUNT * 3)
const state = { version: 0, lastBlendAt: 0, kickLevel: 0, kickAt: 0, pulse: 0, hasProbe: false, glow: null }
const debug = { off: false, kick: null }

export function publishLightProbe(rgbaBottomUp) {
  for (let row = 0; row < 3; row++) {
    for (let col = 0; col < 3; col++) {
      const src = ((2 - row) * 3 + col) * 4
      const dst = (row * 3 + col) * 3
      base[dst] = rgbaBottomUp[src] / 255
      base[dst + 1] = rgbaBottomUp[src + 1] / 255
      base[dst + 2] = rgbaBottomUp[src + 2] / 255
    }
  }
  if (!state.hasProbe) colors.set(base)
  state.hasProbe = true
}

export function setLightGlow(glow) {
  state.glow = glow
  return () => {
    if (state.glow === glow) state.glow = null
  }
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

export function readLightProbe(now) {
  if (state.hasProbe && state.lastBlendAt !== now) {
    const dt = state.lastBlendAt ? Math.min(0.1, Math.max(0, now - state.lastBlendAt) / 1000) : 0
    state.lastBlendAt = now
    const blend = 1 - Math.exp(-SMOOTHING_PER_SECOND * dt)
    let moved = 0
    for (let row = 0; row < 3; row++) {
      for (let col = 0; col < 3; col++) {
        const glow = state.glow ? state.glow(col, row) : null
        for (let c = 0; c < 3; c++) {
          const i = (row * 3 + col) * 3 + c
          const target = glow ? base[i] + glow[c] * (1 - base[i]) : base[i]
          const next = colors[i] + (target - colors[i]) * blend
          moved = Math.max(moved, Math.abs(next - colors[i]))
          colors[i] = next
        }
      }
    }
    if (moved > 1 / 1024) state.version++
  }
  const kick = debug.kick ?? currentKick(now)
  return { colors, kick: kick < 0.002 ? 0 : kick, pulse: state.pulse, version: state.version, active: state.hasProbe && !debug.off }
}

if (typeof window !== 'undefined') {
  window.__plairLight = {
    read: () => {
      const probe = readLightProbe(performance.now())
      return { active: probe.active, kick: probe.kick, pulse: probe.pulse, version: probe.version, colors: Array.from(probe.colors, v => +v.toFixed(3)) }
    },
    kick: (level = 1) => publishBeat(level, state.pulse),
    debug: (options) => { Object.assign(debug, options); state.version++ },
  }
}
