import { LOOKS } from './looks'
import { lowerBound } from '../sceneEffects'
import { pickWeighted, seededRandom } from './random'
import { analyzeSong } from './structure'

const RECIPES = {
  intro: { bars: 4, looks: [['cover', 3], ['depth', 2]], camera: 'push', level: 'low' },
  verse: { bars: 4, looks: [['cover', 3], ['depth', 2], ['split', 1]], camera: 'drift', level: 'mid' },
  build: { bars: 2, looks: [['split', 3], ['cover', 2], ['depth', 1]], camera: 'pushHard', level: 'high', accelerate: true },
  chorus: { bars: 2, looks: null, camera: 'coverage', level: 'high' },
  bridge: { bars: 2, looks: [['depth', 3], ['split', 1], ['cover', 1]], camera: 'drift', level: 'mid' },
  solo: { bars: 1, looks: [['split', 3], ['depth', 1], ['cover', 1]], camera: 'shake', level: 'high' },
  interlude: { bars: 4, looks: [['depth', 3], ['cover', 1]], camera: 'float', level: 'low' },
  outro: { bars: 4, looks: [['cover', 3], ['depth', 1]], camera: 'pull', level: 'low' },
}

const FX = {
  intro: { punch: 0, twitch: 0, glitch: 0, chroma: 0, hue: 0, flash: 0, breathe: 0.3, blur: 0.2 },
  verse: { punch: 0.3, twitch: 0, glitch: 0.1, chroma: 0.1, hue: 0, flash: 0, breathe: 0.5, blur: 0.4 },
  build: { punch: 0.6, twitch: 0.2, glitch: 0.4, chroma: 0.6, hue: 0.2, flash: 0.3, breathe: 0.8, blur: 0.5 },
  chorus: { punch: 1, twitch: 0.45, glitch: 0.6, chroma: 0.6, hue: 0.6, flash: 0.6, breathe: 1, blur: 0.8 },
  bridge: { punch: 0.2, twitch: 0, glitch: 0.2, chroma: 0.3, hue: 0.4, flash: 0, breathe: 0.4, blur: 0.6 },
  solo: { punch: 0.8, twitch: 0.9, glitch: 0.9, chroma: 0.9, hue: 0.8, flash: 0.5, breathe: 1, blur: 0.6 },
  interlude: { punch: 0, twitch: 0, glitch: 0, chroma: 0.1, hue: 0.2, flash: 0, breathe: 0.3, blur: 0.8 },
  outro: { punch: 0.1, twitch: 0, glitch: 0, chroma: 0, hue: 0, flash: 0, breathe: 0.3, blur: 0.4 },
}

const CHORUS_FAMILIES = [
  [['split', { layout: 'boxes' }], ['cover', {}]],
  [['split', { layout: 'strips' }], ['depth', { motion: 'pulse' }]],
  [['split', { layout: 'quad' }], ['cover', {}]],
  [['depth', { motion: 'pulse' }], ['split', { layout: 'wipe' }]],
  [['split', { layout: 'wipe' }], ['split', { layout: 'boxes' }]],
]

const PULSE_DECAY_S = 0.18
const STILL = Object.freeze({ shots: [Object.freeze({ start: 0, end: Infinity, look: 'cover', variant: {}, level: 'low', role: 'verse', fx: FX.verse, camera: 'still', seed: 1 })], beats: [], song: null })

function songSeed(features, beats) {
  let h = 2166136261
  const mix = value => {
    h ^= Math.round(value * 1000) >>> 0
    h = Math.imul(h, 16777619) >>> 0
  }
  mix(features.duration || 0)
  mix(features.tempo || 0)
  mix(beats.length)
  for (let i = 0; i < Math.min(8, beats.length); i++) mix(beats[i])
  return h
}

function snapToBeat(beats, time) {
  const i = lowerBound(beats, time)
  if (i <= 0) return 0
  if (i >= beats.length) return beats.length - 1
  return time - beats[i - 1] < beats[i] - time ? i - 1 : i
}

function cutLengths(total, recipe, bpb, energy, leadsToChorus, final) {
  let bars = recipe.bars
  if (energy > 0.75 && bars > 1) bars /= 2
  if (energy < 0.25 && recipe.level !== 'high') bars *= 2
  if (final) bars /= 2
  const step = Math.max(1, Math.round(bars * bpb))
  const lengths = []
  let left = total
  if (recipe.accelerate) {
    const tail = []
    for (const [beats, count] of [[1, bpb], [2, 2], [4, 2]]) {
      for (let i = 0; i < count && left - beats >= 0; i++) {
        tail.push(beats)
        left -= beats
      }
    }
    while (left > 0) {
      lengths.push(Math.min(step, left))
      left -= step
    }
    return [...lengths, ...tail.reverse()].filter(n => n > 0)
  }
  let tail = []
  if (leadsToChorus && left > 2 * bpb) {
    tail = Array.from({ length: Math.round(bpb / 2) }, () => 2)
    left -= tail.reduce((a, b) => a + b, 0)
  }
  while (left > 0) {
    const n = left - step < step / 2 ? left : step
    lengths.push(n)
    left -= n
  }
  return [...lengths, ...tail]
}

export function planShots(features, { sections, still = false } = {}) {
  const beats = features?.beats
  if (still || !beats?.length || beats.length < 8) return STILL
  const song = analyzeSong(features, sections)
  const rand = seededRandom(songSeed(features, beats))
  const family = CHORUS_FAMILIES[Math.floor(rand() * CHORUS_FAMILIES.length)]
  const shots = []
  let previous = null
  song.sections.forEach((section, index) => {
    const recipe = RECIPES[section.role]
    const first = index === 0 ? 0 : snapToBeat(beats, section.start)
    const last = index === song.sections.length - 1 ? beats.length : snapToBeat(beats, section.end)
    const leadsToChorus = song.sections[index + 1]?.role === 'chorus' && section.role !== 'build'
    const lengths = cutLengths(Math.max(1, last - first), recipe, song.bpb, section.energy, leadsToChorus, section.final)
    const tailFrom = leadsToChorus ? lengths.length - Math.round(song.bpb / 2) : Infinity
    let b = first
    lengths.forEach((length, c) => {
      const role = c >= tailFrom ? 'build' : section.role
      let look = null
      let prefer = {}
      if (role === 'chorus') {
        look = family[c % family.length][0]
        prefer = family[c % family.length][1]
      } else if (index === 0 && c === 0) {
        look = 'cover'
      } else {
        const weights = RECIPES[role].looks
        for (let tries = 0; !look && tries < 4; tries++) {
          const candidate = pickWeighted(rand, weights)
          if (candidate !== previous?.look || tries === 3) look = candidate
        }
      }
      const level = RECIPES[role].level
      const fx = { ...FX[role] }
      if (section.final) for (const key of Object.keys(fx)) fx[key] = Math.min(1, fx[key] * 1.25)
      const shot = {
        start: index === 0 && c === 0 ? 0 : beats[Math.min(b, beats.length - 1)],
        end: Infinity,
        look,
        level,
        role,
        section: section.type,
        energy: section.energy,
        fx,
        camera: RECIPES[role].camera,
        drop: role === 'chorus' && c === 0,
        seed: Math.floor(rand() * 1e9),
        variant: LOOKS[look].variant(rand, level, prefer),
      }
      if (!previous || shot.start > previous.start) {
        shots.push(shot)
        previous = shot
      }
      b += length
    })
  })
  for (let i = 0; i < shots.length - 1; i++) shots[i].end = shots[i + 1].start
  return { shots, beats, song }
}

export function createDirectorCursor() {
  return { shot: 0, beat: 0 }
}

export function directorAt(plan, t, cursor) {
  const { shots, beats } = plan
  let i = Math.min(cursor.shot, shots.length - 1)
  if (shots[i].start > t) i = 0
  while (i < shots.length - 1 && shots[i + 1].start <= t) i++
  cursor.shot = i
  const shot = shots[i]
  let b = cursor.beat
  if (b > beats.length || (b > 0 && beats[b - 1] > t)) b = 0
  while (b < beats.length && beats[b] <= t) b++
  cursor.beat = b
  const lastBeat = b > 0 ? beats[b - 1] : -Infinity
  const pulse = Math.exp(-Math.max(0, t - lastBeat) / PULSE_DECAY_S)
  const firstBeat = lowerBound(beats, shot.start - 0.05)
  return { shot, index: i, pulse, beat: b - 1, firstBeat, beatsSince: Math.max(0, b - 1 - firstBeat) }
}
