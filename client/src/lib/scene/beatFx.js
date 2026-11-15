import { seededRandom } from './random'
import { barPosition, energyAt } from './structure'
import { lowerBound } from '../sceneEffects'

const ROLE_LIGHT = { intro: -0.06, verse: 0, build: 0.03, chorus: 0.07, bridge: -0.02, solo: 0.06, interlude: -0.08, outro: -0.06 }
const REST = { brightness: 0.6, saturation: 1, contrast: 1 }
const CHROMA_FLOOR = 35
const KICKS = {
  twitch: [25, 75],
  rotate: [3, 8],
  prism: [45, 100],
  punch: [0.03, 0.1],
  hue: [20, 45],
  strobe: [0.08, 0.25],
}
const FLOWS = { chroma: [6, 45], sway: [0, 3], hue: [0, 25] }
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

function randomPoint(seed, energy) {
  const rand = seededRandom(seed)
  if (rand() < 0.2) return { u: 0.5, v: 0.5, zoom: 1 }
  const magnitude = 0.3 + 0.4 * energy
  const zoom = 1 + rand() * magnitude
  return { u: 0.5 + (rand() - 0.5) * magnitude * 0.5, v: 0.5 + (rand() - 0.5) * magnitude * 0.5, zoom }
}

function subjectPoint(shot, interest, n) {
  if (interest?.length) return interest[((n % interest.length) + interest.length) % interest.length]
  return randomPoint(shot.seed + 101 * n, shot.energy ?? 0.5)
}

function framing(point, size, out) {
  const toward = 0.3 + 0.7 * size
  out.x = (point.u - 0.5) * toward
  out.y = (point.v - 0.5) * toward
  out.z = 1 + (point.zoom - 1) * size
  return out
}

const STACK = [0, 0.5, 1, 0.5]
const from = { x: 0, y: 0, z: 1 }
const to = { x: 0, y: 0, z: 1 }

function blend(out, a, b, k) {
  out.x = lerp(a.x, b.x, k)
  out.y = lerp(a.y, b.y, k)
  out.z = lerp(a.z, b.z, k)
  return out
}

function handheld(out, shot, t) {
  const phase = (shot.seed % 997) / 97
  out.x += 0.01 * Math.sin(t * 0.83 + phase) + 0.006 * Math.sin(t * 2.1 + 2 * phase)
  out.y += 0.01 * Math.sin(t * 0.71 + 3 * phase) + 0.006 * Math.sin(t * 1.7 + phase)
  out.z += 0.012 * Math.sin(t * 0.53 + 2 * phase)
  return out
}

function cameraAt(shot, at, t, out, interest) {
  const span = Number.isFinite(shot.end) ? Math.max(0.5, shot.end - shot.start) : 8
  const p = clamp01((t - shot.start) / span)
  const subject = subjectPoint(shot, interest, shot.subject ?? 0)
  const size = STACK[(shot.step ?? 0) % STACK.length]
  switch (shot.camera) {
    case 'push':
      blend(out, framing(subject, size, from), framing(subject, Math.min(1, size + 0.5), to), ease(p))
      break
    case 'pull':
      blend(out, framing(subject, Math.min(1, size + 0.5), from), framing(subject, size, to), ease(p))
      break
    case 'pan': {
      const next = subjectPoint(shot, interest, (shot.subject ?? 0) + 1)
      const shotSize = Math.max(size, 0.5)
      blend(out, framing(subject, shotSize, from), framing(next, shotSize, to), p)
      break
    }
    case 'mid':
      framing(subject, 0.5, out)
      break
    case 'closePan': {
      framing(subject, 1, out)
      const side = shot.seed % 2 ? 1 : -1
      out.x += side * 0.06 * (p - 0.5)
      out.y += side * 0.02 * (p - 0.5)
      break
    }
    case 'wide':
      framing(subject, 0, out)
      break
    case 'pushHard':
      framing(subject, Math.min(1, p * p * 1.2), out)
      break
    case 'shake':
      framing(subjectPoint(shot, interest, (shot.subject ?? 0) + Math.floor(at.beatsSince / 2)), 1, out)
      break
    case 'float':
      framing(subject, 0.3, out)
      out.z += 0.04 * Math.sin(t * 0.3)
      break
    default:
      framing(subject, size, out)
      return out
  }
  return handheld(out, shot, t)
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

function spread(sorted, value, [min, max]) {
  if (!sorted?.length) return value
  return min + (lowerBound(sorted, value) / sorted.length) * (max - min)
}

function fire(fx, card, seed, b, accent, k, scale, norm) {
  const strength = (0.5 + 0.5 * accent) * k * scale
  fx.kickLog?.[card].push(strength)
  const range = KICKS[card]
  const size = norm ? spread(norm.kicks[card], strength, range) : strength * range[1]
  switch (card) {
    case 'twitch': {
      const x = hash(seed, b, 5) * 2 - 1
      const y = hash(seed, b, 6) * 2 - 1
      const length = Math.hypot(x, y) || 1
      fx.gx = (x / length) * size
      fx.gy = (y / length) * size
      break
    }
    case 'rotate':
      fx.twist += (hash(seed, b, 3) < 0.5 ? -1 : 1) * size
      break
    case 'prism':
      fx.chromaKick = Math.max(fx.chromaKick, size)
      break
    case 'punch':
      fx.punch = Math.max(fx.punch, size)
      break
    case 'hue':
      fx.hueKick = (b % 2 ? -1 : 1) * size
      break
    case 'strobe':
      fx.flash = Math.max(fx.flash, size)
      break
    default:
      break
  }
}

export function measureSpread(plan, directorAt, createCursor) {
  const fx = createFx()
  fx.kickLog = Object.fromEntries(Object.keys(KICKS).map(card => [card, []]))
  fx.flowLog = Object.fromEntries(Object.keys(FLOWS).map(flow => [flow, []]))
  const cursor = createCursor()
  const dt = 1 / NORM_FPS
  for (let t = 0; t < plan.song.duration; t += dt) stepFx(fx, plan, directorAt(plan, t, cursor), t, dt, true)
  const sort = log => Object.fromEntries(Object.entries(log).map(([name, values]) => [name, Float32Array.from(values).sort()]))
  return { kicks: sort(fx.kickLog), flows: sort(fx.flowLog) }
}

export function createFx() {
  return {
    chromatic: 0, glitchX: 0, glitchY: 0, rotation: 0, hue: 0, blur: 0, flicker: 1, scale: 1,
    brightness: REST.brightness, saturation: REST.saturation, contrast: REST.contrast,
    frameOffsetX: 0, frameOffsetY: 0, frameScale: 1, beatPulse: 0, currentEnergy: 0,
    punch: 0, flash: 0, twist: 0, hueKick: 0, chromaKick: 0, gx: 0, gy: 0, tempoTime: 0,
    interest: null, lastBeat: -1, lastHalf: -1, lastShot: -1, camera: { x: 0, y: 0, z: 1 },
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
  const fastDecay = 1 - Math.exp(-dt / (period * 0.8))
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
      if (onRhythm(shot, song, b)) fire(fx, shot.lead, shot.seed, b, accent, k, shot.rhythm === 'fill' ? 1.2 : 1, plan.norm)
      if (down) fire(fx, shot.support, shot.seed + 1, b, accent, k, 0.7, plan.norm)
      if (accent > 0.5) fx.blur += 18 * accent * k
      if (accent > 0.35) kick = Math.min(1, 0.4 + 0.6 * accent)
    }
  }

  if ((shot.rhythm === 'eighth' || shot.rhythm === 'fill') && b >= 0 && b + 1 < song.beats.length && fx.lastHalf !== b) {
    const beats = song.beats
    if (t >= (beats[b] + beats[b + 1]) / 2) {
      fx.lastHalf = b
      fire(fx, shot.lead, shot.seed + 77, b, (song.accents[b] ?? 0.5) * 0.8, k, 0.6, plan.norm)
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
  const norm = plan.norm
  const flowChroma = CHROMA_FLOOR * Math.min(1, w.chroma) * k * (0.6 + 0.4 * breathing)
  const flowSway = Math.sin(Math.PI * bars + phase) * 5 * w.sway * k
  const flowHue = Math.sin(Math.PI * bars / 2 + phase) * 35 * w.hue * k
  if (fx.flowLog) {
    fx.flowLog.chroma.push(flowChroma)
    fx.flowLog.sway.push(Math.abs(flowSway))
    fx.flowLog.hue.push(Math.abs(flowHue))
  }
  fx.chromatic = Math.max(fx.chromaKick, norm ? spread(norm.flows.chroma, flowChroma, FLOWS.chroma) : flowChroma)
  fx.rotation = fx.twist + (norm ? Math.sign(flowSway) * spread(norm.flows.sway, Math.abs(flowSway), FLOWS.sway) : flowSway)
  fx.hue = fx.hueKick + (norm ? Math.sign(flowHue) * spread(norm.flows.hue, Math.abs(flowHue), FLOWS.hue) : flowHue)
  fx.glitchX = fx.gx
  fx.glitchY = fx.gy
  const punch = fx.punch
  const flash = fx.flash

  const camera = cameraAt(shot, at, t, fx.camera, fx.interest)
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
