import { seededRandom } from './random'
import { barPosition, signalsAt } from './structure'
import { lowerBound } from '../sceneEffects'

const ROLE_LIGHT = { intro: -0.06, verse: 0, build: 0.03, chorus: 0.07, bridge: -0.02, solo: 0.06, interlude: -0.08, outro: -0.06 }
const REST = { brightness: 0.6, saturation: 1, contrast: 1 }
const KICKS = {
  twitch: [25, 75],
  rotate: [3, 8],
  prism: [45, 100],
  punch: [0.03, 0.1],
  hue: [20, 45],
  strobe: [0.08, 0.25],
}
const FLOW = { zoom: 0.07, chroma: 45, tilt: 3, light: 0.22, colour: 0.25, depth: 0.22, aperture: 0.6, shake: 2.5 }
const HAND = { base: 0.006, energy: 0.01, parallax: 3, tremble: 0.004 }
const PARALLAX = 2.2
const ORBIT_LIMIT = 0.11
const DOLLY = { push: 0.18, hard: 0.3, limit: 0.35 }
const NORM_FPS = 15
const SWELL_RELEASE_S = 0.12
const HIT_DECAY_BEATS = 0.45
const ONSET_FRESH_S = 0.12
const TILT_PARALLAX = 1.5

const clamp01 = x => Math.min(1, Math.max(0, x))
const clamp = (x, limit) => Math.min(limit, Math.max(-limit, x))
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

function handheld(out, shot, t, fx) {
  const phase = (shot.seed % 997) / 97
  const amount = HAND.base + HAND.energy * fx.currentEnergy
  const x = amount * (Math.sin(t * 0.83 + phase) + 0.6 * Math.sin(t * 2.1 + 2 * phase))
  const y = amount * (Math.sin(t * 0.71 + 3 * phase) + 0.6 * Math.sin(t * 1.7 + phase))
  const tremble = HAND.tremble * fx.shake
  const reach = HAND.parallax * (1 + FLOW.shake * fx.shake)
  out.x += x + tremble * Math.sin(t * 33 + phase)
  out.y += y + tremble * Math.sin(t * 27 + 2 * phase)
  out.z += 1.2 * amount * Math.sin(t * 0.53 + 2 * phase)
  out.px += x * reach
  out.py += y * reach
  return out
}

function cameraAt(shot, at, t, out, fx) {
  const interest = fx.interest
  const span = Number.isFinite(shot.end) ? Math.max(0.5, shot.end - shot.start) : 8
  const p = clamp01((t - shot.start) / span)
  const subject = subjectPoint(shot, interest, shot.subject ?? 0)
  const size = STACK[(shot.step ?? 0) % STACK.length]
  const side = shot.seed % 2 ? 1 : -1
  out.px = 0
  out.py = 0
  out.pz = 0
  switch (shot.camera) {
    case 'push':
      blend(out, framing(subject, size, from), framing(subject, Math.min(1, size + 0.5), to), ease(p))
      out.pz = DOLLY.push * (2 * ease(p) - 1)
      break
    case 'pull':
      blend(out, framing(subject, Math.min(1, size + 0.5), from), framing(subject, size, to), ease(p))
      out.pz = -DOLLY.push * (2 * ease(p) - 1)
      break
    case 'pan': {
      const next = subjectPoint(shot, interest, (shot.subject ?? 0) + 1)
      const shotSize = Math.max(size, 0.5)
      blend(out, framing(subject, shotSize, from), framing(next, shotSize, to), p)
      out.px = (to.x - from.x) * PARALLAX * (p - 0.5)
      out.py = (to.y - from.y) * PARALLAX * (p - 0.5)
      break
    }
    case 'mid':
      framing(subject, 0.5, out)
      break
    case 'closePan': {
      framing(subject, 1, out)
      const travel = side * (p - 0.5)
      out.x += 0.06 * travel
      out.y += 0.02 * travel
      out.px = 0.06 * travel * PARALLAX * 2
      out.py = 0.02 * travel * PARALLAX * 2
      break
    }
    case 'wide':
      framing(subject, 0, out)
      break
    case 'orbit': {
      framing(subject, Math.max(size, 0.5), out)
      const angle = (p - 0.5) * Math.PI
      out.px = side * 0.09 * Math.sin(angle)
      out.py = side * 0.025 * Math.cos(angle)
      break
    }
    case 'crane': {
      framing(subject, Math.max(size, 0.5), out)
      const rise = side * (2 * ease(p) - 1)
      out.y += 0.02 * rise
      out.py = 0.08 * rise
      out.pz = 0.06 * rise
      break
    }
    case 'pushHard':
      framing(subject, Math.min(1, p * p * 1.2), out)
      out.pz = DOLLY.hard * p * p
      break
    case 'shake':
      framing(subjectPoint(shot, interest, (shot.subject ?? 0) + Math.floor(at.beatsSince / 2)), 1, out)
      break
    case 'float': {
      const phase = (shot.seed % 997) / 97
      framing(subject, 0.3, out)
      out.z += 0.04 * Math.sin(t * 0.3)
      out.px = 0.06 * Math.sin(t * 0.27 + phase)
      out.py = 0.035 * Math.cos(t * 0.21 + phase)
      break
    }
    default:
      framing(subject, size, out)
      return out
  }
  return handheld(out, shot, t, fx)
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
  const cursor = createCursor()
  const dt = 1 / NORM_FPS
  for (let t = 0; t < plan.song.duration; t += dt) stepFx(fx, plan, directorAt(plan, t, cursor), t, dt, true)
  return { kicks: Object.fromEntries(Object.entries(fx.kickLog).map(([name, values]) => [name, Float32Array.from(values).sort()])) }
}

export function createFx() {
  return {
    chromatic: 0, glitchX: 0, glitchY: 0, rotation: 0, hue: 0, blur: 0, flicker: 1, scale: 1,
    brightness: REST.brightness, saturation: REST.saturation, contrast: REST.contrast,
    frameOffsetX: 0, frameOffsetY: 0, frameScale: 1, orbitX: 0, orbitY: 0, dolly: 0, beatPulse: 0, currentEnergy: 0,
    swell: 0, hit: 0, shake: 0, depthSwell: 0, aperture: 0, tilt: 0, grade: 0, side: 1,
    punch: 0, flash: 0, twist: 0, hueKick: 0, chromaKick: 0, gx: 0, gy: 0, nextOnset: 0,
    interest: null, lastBeat: -1, lastShot: -1, signals: { energy: 0, swell: 0, level: 0 },
    camera: { x: 0, y: 0, z: 1, px: 0, py: 0, pz: 0 },
  }
}

function rest(fx, dt) {
  const settle = 1 - Math.exp(-dt * 6)
  const keep = Math.exp(-dt * 8)
  for (const name of ['chromatic', 'chromaKick', 'glitchX', 'glitchY', 'gx', 'gy', 'rotation', 'twist', 'tilt', 'hue', 'hueKick', 'grade', 'punch', 'flash', 'swell', 'hit', 'shake', 'depthSwell', 'aperture', 'orbitX', 'orbitY', 'dolly']) fx[name] *= keep
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

export function stepFx(fx, plan, at, t, dt, playing) {
  const song = plan?.song
  if (!playing || !song || !at) return rest(fx, dt)
  const settle = 1 - Math.exp(-dt * 6)
  const shot = at.shot
  const patch = shot.patch
  const signals = signalsAt(song, t, fx.signals)
  const energy = signals.energy
  fx.currentEnergy = energy
  fx.swell = Math.max(signals.swell, fx.swell * Math.exp(-dt / SWELL_RELEASE_S))
  const swell = fx.swell
  const k = 0.75 + 0.35 * energy
  const loud = 0.5 + 0.5 * energy
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
      fx.hit = Math.max(fx.hit, (0.25 + 0.75 * accent) * loud)
      fx.side = -fx.side
      if (accent >= shot.gate || b === shot.phraseBeat) fire(fx, shot.lead, shot.seed, b, accent, k, shot.gate < 0 ? 1.2 : 1, plan.norm)
      if (barPosition(song, b) === 0) fire(fx, shot.support, shot.seed + 1, b, accent, k, 0.7, plan.norm)
      if (accent > 0.5) fx.blur += 18 * accent * k
      if (accent > 0.35) kick = Math.min(1, 0.4 + 0.6 * accent)
    }
  }

  const onsets = song.onsets
  let next = fx.nextOnset
  if (next > onsets.length || (next > 0 && onsets[next - 1].time > t)) next = 0
  while (next < onsets.length && onsets[next].time <= t) {
    const onset = onsets[next++]
    if (t - onset.time > ONSET_FRESH_S) continue
    fx.hit = Math.max(fx.hit, 0.6 * onset.strength * loud)
    if (shot.onsets) fire(fx, shot.lead, shot.seed + 77, onset.beat, 0.3 + 0.5 * onset.strength, k, 0.6, plan.norm)
  }
  fx.nextOnset = next

  fx.gx *= 1 - fastDecay
  fx.gy *= 1 - fastDecay
  fx.chromaKick *= 1 - fastDecay
  fx.twist *= 1 - fastDecay
  fx.hueKick *= 1 - slowDecay
  fx.punch *= 1 - Math.exp(-dt / 0.25)
  fx.flash *= 1 - Math.exp(-dt / 0.2)
  fx.hit *= Math.exp(-dt / (period * HIT_DECAY_BEATS))

  fx.shake = patch.shake * swell
  fx.depthSwell = patch.depth * swell
  fx.aperture = FLOW.aperture * patch.focus * swell
  const camera = cameraAt(shot, at, t, fx.camera, fx)
  fx.frameOffsetX = camera.x
  fx.frameOffsetY = camera.y
  fx.frameScale = camera.z
  fx.orbitX = clamp(camera.px, ORBIT_LIMIT)
  fx.orbitY = clamp(camera.py, ORBIT_LIMIT)
  fx.dolly = clamp(camera.pz + FLOW.depth * fx.depthSwell, DOLLY.limit)

  fx.chromatic = Math.max(fx.chromaKick, FLOW.chroma * Math.min(1, patch.prism) * swell)
  fx.tilt += (FLOW.tilt * patch.tilt * swell * fx.side - fx.tilt) * (1 - Math.exp(-dt * 14))
  fx.rotation = fx.twist + fx.tilt
  fx.grade += (shot.hueShift - fx.grade) * settle
  fx.hue = fx.hueKick + fx.grade
  fx.glitchX = fx.gx
  fx.glitchY = fx.gy

  const glow = patch.light * swell
  const light = ROLE_LIGHT[shot.role] || 0
  fx.beatPulse = swell * (0.2 + 0.8 * energy)
  fx.scale += ((1 + FLOW.zoom * patch.zoom * swell + fx.punch) - fx.scale) * (1 - Math.exp(-dt * 20))
  fx.flicker += (1 - fx.flicker) * settle
  fx.brightness += ((0.4 + 0.3 * energy + 0.08 * signals.level + light + fx.flash + FLOW.light * glow) - fx.brightness) * (1 - Math.exp(-dt * 14))
  fx.saturation += ((0.85 + 0.35 * energy + light + FLOW.colour * glow) - fx.saturation) * settle
  fx.contrast += ((0.92 + 0.18 * energy) - fx.contrast) * settle
  return { kick, cut }
}

export function setCamera(uniforms, fx, tiltX = 0, tiltY = 0) {
  const scale = fx.scale || 1
  const rotation = fx.rotation * Math.PI / 180
  const cos = Math.cos(rotation)
  const sin = Math.sin(rotation)
  const x = fx.orbitX + tiltX * TILT_PARALLAX
  const y = fx.orbitY + tiltY * TILT_PARALLAX
  uniforms.u_glitch.value.set(fx.glitchX, fx.glitchY)
  uniforms.u_frame_offset.value.set(fx.frameOffsetX, fx.frameOffsetY)
  uniforms.u_frame_scale.value = fx.frameScale
  uniforms.u_scale.value = scale
  uniforms.u_rotation.value = rotation
  uniforms.u_orbit.value.set((cos * x + sin * y) / scale, (cos * y - sin * x) / scale, -fx.dolly)
}
