import { seededRandom } from './random'
import { energyAt, isDownbeat } from './structure'

const ROLE_LIGHT = { intro: -0.06, verse: 0, build: 0.03, chorus: 0.07, bridge: -0.02, solo: 0.06, interlude: -0.08, outro: -0.06 }
const REST = { brightness: 0.6, saturation: 1, contrast: 1 }
const CHROMA_FLOOR = 24

const clamp01 = x => Math.min(1, Math.max(0, x))
const ease = x => x * x * (3 - 2 * x)
const lerp = (a, b, k) => a + (b - a) * k
const reach = zoom => Math.max(0, 0.5 - 0.5 / zoom) * 0.9

function hash(seed, beat, k) {
  let h = (seed ^ Math.imul(beat + 1, 0x9E3779B1) ^ Math.imul(k + 1, 0x85EBCA77)) >>> 0
  h = Math.imul(h ^ (h >>> 16), 0x7FEB352D)
  h = Math.imul(h ^ (h >>> 15), 0x846CA68B)
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296
}

function keyFrame(seed, wide) {
  const rand = seededRandom(seed)
  const zoom = wide ? 1 + rand() * 0.1 : 1.35 + rand() * 0.45
  const limit = reach(zoom)
  return { x: (rand() * 2 - 1) * limit, y: (rand() * 2 - 1) * limit, z: zoom }
}

function cameraAt(shot, at, t, bpb, out) {
  const span = Number.isFinite(shot.end) ? Math.max(0.5, shot.end - shot.start) : 8
  const p = clamp01((t - shot.start) / span)
  const start = keyFrame(shot.seed, true)
  const goal = keyFrame(shot.seed + 1, false)
  switch (shot.camera) {
    case 'push':
      out.z = lerp(1, 1.18, ease(p))
      out.x = lerp(0, goal.x * 0.4, p)
      out.y = lerp(0, goal.y * 0.4, p)
      return out
    case 'pushHard':
      out.z = lerp(1, 1.6, p * p)
      out.x = lerp(start.x, goal.x * 0.6, p * p)
      out.y = lerp(start.y, goal.y * 0.6, p * p)
      return out
    case 'drift':
      out.z = 1.08 + 0.12 * (start.z - 1) * 10
      out.x = lerp(start.x, -start.x, p)
      out.y = lerp(start.y, -start.y, p)
      return out
    case 'coverage': {
      const bar = Math.floor(at.beatsSince / bpb)
      const key = keyFrame(shot.seed + 7 * bar, bar % 2 === 0)
      out.z = key.z
      out.x = key.x
      out.y = key.y
      return out
    }
    case 'shake': {
      const key = keyFrame(shot.seed + 13 * Math.floor(at.beatsSince / 2), false)
      out.z = key.z
      out.x = key.x
      out.y = key.y
      return out
    }
    case 'float':
      out.z = 1.06 + 0.04 * Math.sin(t * 0.3)
      out.x = 0.03 * Math.sin(t * 0.21 + start.x * 9)
      out.y = 0.03 * Math.cos(t * 0.17 + start.y * 9)
      return out
    case 'pull':
      out.z = lerp(1.35, 1, ease(p))
      out.x = lerp(goal.x * 0.5, 0, ease(p))
      out.y = lerp(goal.y * 0.5, 0, ease(p))
      return out
    default:
      out.z = 1
      out.x = 0
      out.y = 0
      return out
  }
}

export function createFx() {
  return {
    chromatic: 0, glitchX: 0, glitchY: 0, rotation: 0, hue: 0, blur: 0, flicker: 1, scale: 1,
    brightness: REST.brightness, saturation: REST.saturation, contrast: REST.contrast,
    frameOffsetX: 0, frameOffsetY: 0, frameScale: 1, beatPulse: 0, currentEnergy: 0,
    punch: 0, flash: 0, twist: 0, hueKick: 0, chromaKick: 0, tempoTime: 0, lastBeat: -1, lastShot: -1, camera: { x: 0, y: 0, z: 1 },
  }
}

export function stepFx(fx, plan, at, t, dt, playing) {
  const song = plan?.song
  const settle = 1 - Math.exp(-dt * 6)
  if (!playing || !song || !at) {
    const fast = 1 - Math.exp(-dt * 8)
    fx.chromatic *= 1 - fast
    fx.chromaKick *= 1 - fast
    fx.glitchX *= 1 - fast
    fx.glitchY *= 1 - fast
    fx.rotation *= 1 - fast
    fx.twist *= 1 - fast
    fx.hue *= 1 - fast
    fx.hueKick *= 1 - fast
    fx.punch *= 1 - fast
    fx.flash *= 1 - fast
    fx.brightness += (REST.brightness - fx.brightness) * settle
    fx.saturation += (REST.saturation - fx.saturation) * settle
    fx.contrast += (REST.contrast - fx.contrast) * settle
    fx.scale += (1 - fx.scale) * settle
    fx.flicker += (1 - fx.flicker) * settle
    fx.frameOffsetX = 0
    fx.frameOffsetY = 0
    fx.frameScale = 1
    fx.beatPulse = 0
    fx.currentEnergy = 0
    return { kick: 0, cut: false }
  }
  const shot = at.shot
  const w = shot.fx
  const energy = energyAt(song, t)
  fx.currentEnergy = energy
  const k = 0.75 + 0.35 * energy
  const period = song.period
  const fastDecay = 1 - Math.exp(-dt / (period * 0.35))
  const slowDecay = 1 - Math.exp(-dt / (period * 1.5))
  let kick = 0
  let cut = false

  if (at.index !== fx.lastShot) {
    cut = fx.lastShot !== -1
    fx.lastShot = at.index
    if (shot.drop) {
      fx.flash = Math.max(fx.flash, 0.3)
      fx.punch = Math.max(fx.punch, 0.1)
    }
  }

  const b = at.beat
  if (b >= 0 && b !== fx.lastBeat) {
    const fresh = fx.lastBeat !== -1 && b === fx.lastBeat + 1
    fx.lastBeat = b
    if (fresh) {
      const seed = shot.seed
      const accent = song.accents[b] ?? 0.5
      const down = isDownbeat(song, b)
      const strength = Math.min(1, (0.35 + 0.65 * accent) * (down ? 1.4 : 1))
      if (hash(seed, b, 1) < w.punch * strength) fx.punch = Math.max(fx.punch, (0.025 + 0.06 * accent) * Math.min(1, w.punch) * k)
      if (hash(seed, b, 2) < w.twitch * strength * 0.8) fx.twist += (hash(seed, b, 3) * 2 - 1) * (3 + 7 * accent) * Math.min(1, w.twitch) * k
      if (hash(seed, b, 4) < w.glitch * strength * 0.7) {
        fx.glitchX = (hash(seed, b, 5) - 0.5) * 150 * accent * w.glitch * k
        fx.glitchY = (hash(seed, b, 6) - 0.5) * 150 * accent * w.glitch * k
        fx.chromaKick = Math.max(fx.chromaKick, 80 * accent * k)
      }
      if (hash(seed, b, 7) < w.chroma * strength) fx.chromaKick = Math.max(fx.chromaKick, (30 + 55 * accent) * Math.min(1, w.chroma) * k)
      if (down && hash(seed, b, 8) < w.hue * 0.7) fx.hueKick += (hash(seed, b, 9) < 0.5 ? -1 : 1) * (25 + 35 * accent) * k
      if (hash(seed, b, 10) < w.strobe * strength * 0.6) fx.flash = Math.max(fx.flash, 0.16 * k)
      if (accent > 0.5) fx.blur += 18 * accent * k
      if (accent > 0.35) kick = Math.min(1, 0.4 + 0.6 * accent)
    }
  }

  fx.glitchX *= 1 - fastDecay
  fx.glitchY *= 1 - fastDecay
  fx.chromaKick *= 1 - fastDecay
  fx.twist *= 1 - fastDecay
  fx.hueKick *= 1 - slowDecay
  fx.punch *= 1 - Math.exp(-dt / 0.25)
  fx.flash *= 1 - Math.exp(-dt / 0.2)

  fx.tempoTime += dt
  const breathing = (Math.sin(fx.tempoTime * (60 / period / 120) * Math.PI * 2) + 1) / 2
  const bars = t / (period * song.bpb)
  const phase = (shot.seed % 1000) / 159
  fx.rotation = fx.twist + Math.sin(Math.PI * bars + phase) * 5 * w.sway * k
  fx.hue = fx.hueKick + Math.sin(Math.PI * bars / 2 + phase) * 35 * w.hue * k
  fx.chromatic = Math.max(fx.chromaKick, CHROMA_FLOOR * Math.min(1, w.chroma) * k * (0.6 + 0.4 * breathing))

  const camera = cameraAt(shot, at, t, song.bpb, fx.camera)
  fx.frameOffsetX = camera.x
  fx.frameOffsetY = camera.y
  fx.frameScale = camera.z

  fx.beatPulse = breathing * (0.2 + energy * 0.8)
  fx.scale += ((1 + breathing * energy * 0.08 + fx.punch) - fx.scale) * (1 - Math.exp(-dt * 20))
  fx.flicker += ((1 - breathing * energy * 0.15) - fx.flicker) * (1 - Math.exp(-dt * 12))
  const light = ROLE_LIGHT[shot.role] || 0
  fx.brightness += ((0.42 + 0.33 * energy + light + fx.flash) - fx.brightness) * (1 - Math.exp(-dt * 10))
  fx.saturation += ((0.85 + 0.35 * energy + light) - fx.saturation) * settle
  fx.contrast += ((0.92 + 0.18 * energy) - fx.contrast) * settle
  return { kick, cut }
}
