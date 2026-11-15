import { seededRandom } from './random'
import { barPosition, energyAt } from './structure'
import { lowerBound } from '../sceneEffects'

const ROLE_LIGHT = { intro: -0.06, verse: 0, build: 0.03, chorus: 0.07, bridge: -0.02, solo: 0.06, interlude: -0.08, outro: -0.06 }
const REST = { brightness: 0.6, saturation: 1, contrast: 1 }
const CHROMA_FLOOR = 35
const SPREAD = {
  chromatic: [6, 90],
  glitch: [0, 75],
  rotation: [0, 6],
  hue: [0, 40],
  punch: [0, 0.1],
  flash: [0, 0.25],
}
const IMPULSE_FLOOR = { glitch: 0.5, punch: 0.001, flash: 0.002 }
const NORM_FPS = 15

const clamp01 = x => Math.min(1, Math.max(0, x))
const ease = x => x * x * (3 - 2 * x)
const lerp = (a, b, k) => a + (b - a) * k

function hash(seed, beat, k) {
  let h = (seed ^ Math.imul(beat + 1, 0x9E3779B1) ^ Math.imul(k + 1, 0x85EBCA77)) >>> 0
  h = Math.imul(h ^ (h >>> 16), 0x7FEB352D)
  h = Math.imul(h ^ (h >>> 15), 0x846CA68B)
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296
}

function keyFrame(seed, energy) {
  const rand = seededRandom(seed)
  if (rand() < 0.2) return { x: 0, y: 0, z: 1 }
  const magnitude = 0.3 + 0.4 * energy
  return { z: 1 + rand() * magnitude, x: (rand() - 0.5) * magnitude * 0.5, y: (rand() - 0.5) * magnitude * 0.5 }
}

function hold(out, key, zoom = 0) {
  out.z = key.z + zoom
  out.x = key.x
  out.y = key.y
  return out
}

function cameraAt(shot, at, t, bpb, out) {
  const span = Number.isFinite(shot.end) ? Math.max(0.5, shot.end - shot.start) : 8
  const p = clamp01((t - shot.start) / span)
  const energy = shot.energy ?? 0.5
  const start = keyFrame(shot.seed, energy)
  switch (shot.camera) {
    case 'push':
      return hold(out, start, 0.15 * ease(p))
    case 'pushHard':
      return hold(out, start, 0.5 * p * p)
    case 'drift': {
      const goal = keyFrame(shot.seed + 1, energy)
      out.z = lerp(start.z, goal.z, p)
      out.x = lerp(start.x, goal.x, p)
      out.y = lerp(start.y, goal.y, p)
      return out
    }
    case 'coverage':
      return hold(out, keyFrame(shot.seed + 7 * Math.floor(at.beatsSince / bpb), energy))
    case 'shake':
      return hold(out, keyFrame(shot.seed + 13 * Math.floor(at.beatsSince / 2), energy))
    case 'float':
      hold(out, start, 0.04 * Math.sin(t * 0.3))
      out.x += 0.03 * Math.sin(t * 0.21)
      out.y += 0.03 * Math.cos(t * 0.17)
      return out
    case 'pull':
      out.z = lerp(start.z + 0.3, 1, ease(p))
      out.x = lerp(start.x, 0, ease(p))
      out.y = lerp(start.y, 0, ease(p))
      return out
    default:
      return hold(out, start)
  }
}

function onRhythm(shot, song, b) {
  const position = barPosition(song, b)
  switch (shot.rhythm) {
    case 'sparse': return b === shot.phraseBeat || (position === 0 && hash(shot.seed, b, 11) < 0.5)
    case 'down': return position === 0
    case 'half': return position % 2 === 0
    case 'off': return position % 2 === 1
    default: return true
  }
}

function fire(fx, w, seed, b, accent, down, k, scale) {
  const strength = Math.min(1, (0.35 + 0.65 * accent) * (down ? 1.4 : 1)) * scale
  if (hash(seed, b, 1) < w.punch * strength) fx.punch = Math.max(fx.punch, (0.025 + 0.06 * accent) * Math.min(1, w.punch) * k * scale)
  if (hash(seed, b, 2) < w.twitch * strength * 0.8) fx.twist += (hash(seed, b, 3) * 2 - 1) * (3 + 7 * accent) * Math.min(1, w.twitch) * k * scale
  if (hash(seed, b, 4) < w.glitch * strength * 0.7) {
    fx.gx = (hash(seed, b, 5) - 0.5) * 150 * accent * w.glitch * k * scale
    fx.gy = (hash(seed, b, 6) - 0.5) * 150 * accent * w.glitch * k * scale
    fx.chromaKick = Math.max(fx.chromaKick, 90 * accent * k * scale)
  }
  if (hash(seed, b, 7) < w.chroma * strength) fx.chromaKick = Math.max(fx.chromaKick, (45 + 60 * accent) * Math.min(1, w.chroma) * k * scale)
  if (down && hash(seed, b, 8) < w.hue * 0.7) fx.hueKick += (hash(seed, b, 9) < 0.5 ? -1 : 1) * (25 + 35 * accent) * k
  if (hash(seed, b, 10) < w.strobe * strength * 0.6) fx.flash = Math.max(fx.flash, 0.16 * k * scale)
}

function spread(sorted, value, [min, max]) {
  if (!sorted.length) return value
  return min + (lowerBound(sorted, value) / sorted.length) * (max - min)
}

function impulse(sorted, value, channel) {
  return value < IMPULSE_FLOOR[channel] ? 0 : spread(sorted, value, SPREAD[channel])
}

export function measureSpread(plan, directorAt, createCursor) {
  const fx = createFx()
  const cursor = createCursor()
  const samples = { chromatic: [], rotation: [], hue: [], glitch: [], punch: [], flash: [] }
  const dt = 1 / NORM_FPS
  for (let t = 0; t < plan.song.duration; t += dt) {
    stepFx(fx, plan, directorAt(plan, t, cursor), t, dt, true)
    const raw = fx.raw
    samples.chromatic.push(raw.chromatic)
    samples.rotation.push(Math.abs(raw.rotation))
    samples.hue.push(Math.abs(raw.hue))
    for (const channel of ['glitch', 'punch', 'flash']) if (raw[channel] >= IMPULSE_FLOOR[channel]) samples[channel].push(raw[channel])
  }
  const sorted = {}
  for (const [channel, values] of Object.entries(samples)) sorted[channel] = Float32Array.from(values).sort()
  return sorted
}

export function createFx() {
  return {
    chromatic: 0, glitchX: 0, glitchY: 0, rotation: 0, hue: 0, blur: 0, flicker: 1, scale: 1,
    brightness: REST.brightness, saturation: REST.saturation, contrast: REST.contrast,
    frameOffsetX: 0, frameOffsetY: 0, frameScale: 1, beatPulse: 0, currentEnergy: 0,
    punch: 0, flash: 0, twist: 0, hueKick: 0, chromaKick: 0, gx: 0, gy: 0, tempoTime: 0,
    raw: { chromatic: 0, rotation: 0, hue: 0, glitch: 0, punch: 0, flash: 0 }, lastBeat: -1, lastHalf: -1, lastShot: -1, camera: { x: 0, y: 0, z: 1 },
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
    fx.gx *= 1 - fast
    fx.gy *= 1 - fast
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
      const accent = song.accents[b] ?? 0.5
      const down = barPosition(song, b) === 0
      if (onRhythm(shot, song, b)) fire(fx, w, shot.seed, b, accent, down, k, shot.rhythm === 'fill' ? 1.2 : 1)
      if (accent > 0.5) fx.blur += 18 * accent * k
      if (accent > 0.35) kick = Math.min(1, 0.4 + 0.6 * accent)
    }
  }

  if ((shot.rhythm === 'eighth' || shot.rhythm === 'fill') && b >= 0 && b + 1 < song.beats.length && fx.lastHalf !== b) {
    const beats = song.beats
    if (t >= (beats[b] + beats[b + 1]) / 2) {
      fx.lastHalf = b
      fire(fx, w, shot.seed + 77, b, (song.accents[b] ?? 0.5) * 0.8, false, k, 0.6)
    }
  }

  fx.gx *= 1 - fastDecay
  fx.gy *= 1 - fastDecay
  fx.chromaKick *= 1 - fastDecay
  fx.twist *= 1 - fastDecay
  fx.hueKick *= 1 - slowDecay
  fx.punch *= 1 - Math.exp(-dt / 0.25)
  fx.flash *= 1 - Math.exp(-dt / 0.2)

  fx.tempoTime += dt
  const breathing = (Math.sin(fx.tempoTime * (60 / period / 120) * Math.PI * 2) + 1) / 2
  const bars = t / (period * song.bpb)
  const phase = (shot.seed % 1000) / 159
  const raw = fx.raw
  raw.rotation = fx.twist + Math.sin(Math.PI * bars + phase) * 5 * w.sway * k
  raw.hue = fx.hueKick + Math.sin(Math.PI * bars / 2 + phase) * 35 * w.hue * k
  raw.chromatic = Math.max(fx.chromaKick, CHROMA_FLOOR * Math.min(1, w.chroma) * k * (0.6 + 0.4 * breathing))
  raw.glitch = Math.hypot(fx.gx, fx.gy)
  raw.punch = fx.punch
  raw.flash = fx.flash
  const norm = plan.norm
  fx.chromatic = norm ? spread(norm.chromatic, raw.chromatic, SPREAD.chromatic) : raw.chromatic
  fx.rotation = norm ? Math.sign(raw.rotation) * spread(norm.rotation, Math.abs(raw.rotation), SPREAD.rotation) : raw.rotation
  fx.hue = norm ? Math.sign(raw.hue) * spread(norm.hue, Math.abs(raw.hue), SPREAD.hue) : raw.hue
  const glitch = norm ? impulse(norm.glitch, raw.glitch, 'glitch') : raw.glitch
  fx.glitchX = raw.glitch > 0 ? fx.gx * glitch / raw.glitch : 0
  fx.glitchY = raw.glitch > 0 ? fx.gy * glitch / raw.glitch : 0
  const punch = norm ? impulse(norm.punch, raw.punch, 'punch') : raw.punch
  const flash = norm ? impulse(norm.flash, raw.flash, 'flash') : raw.flash

  const camera = cameraAt(shot, at, t, song.bpb, fx.camera)
  fx.frameOffsetX = camera.x
  fx.frameOffsetY = camera.y
  fx.frameScale = camera.z

  fx.beatPulse = breathing * (0.2 + energy * 0.8)
  fx.scale += ((1 + breathing * energy * 0.08 + punch) - fx.scale) * (1 - Math.exp(-dt * 20))
  fx.flicker += ((1 - breathing * energy * 0.15) - fx.flicker) * (1 - Math.exp(-dt * 12))
  const light = ROLE_LIGHT[shot.role] || 0
  fx.brightness += ((0.42 + 0.33 * energy + light + flash) - fx.brightness) * (1 - Math.exp(-dt * 10))
  fx.saturation += ((0.85 + 0.35 * energy + light) - fx.saturation) * settle
  fx.contrast += ((0.92 + 0.18 * energy) - fx.contrast) * settle
  return { kick, cut }
}
