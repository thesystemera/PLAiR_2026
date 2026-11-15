import { LOOKS } from './looks'
import { lowerBound } from '../sceneEffects'
import { pickWeighted, seededRandom } from './random'

const LEVELS = {
  low: { cutBeats: 32, looks: [['cover', 4], ['depth', 1]] },
  mid: { cutBeats: 16, looks: [['cover', 3], ['depth', 2], ['split', 2]] },
  high: { cutBeats: 8, looks: [['split', 3], ['depth', 2], ['cover', 2]] },
}
const MIN_SECTION_S = 6
const FALLBACK_SECTION_BEATS = 32
const PULSE_DECAY_S = 0.18
const STILL = Object.freeze({ shots: [Object.freeze({ start: 0, end: Infinity, look: 'cover', variant: {}, level: 'low' })], beats: [] })

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

function songDuration(features, beats) {
  const segments = features.loudness_segments
  const last = segments?.length ? segments[segments.length - 1] : null
  return features.duration || (last ? last.start + last.duration : beats[beats.length - 1] + 1)
}

function sectionStarts(features, beats, duration) {
  const starts = (features.sections || [])
    .map(section => section?.start)
    .filter(start => Number.isFinite(start) && start > 0 && start < duration)
    .sort((a, b) => a - b)
  if (starts.length) return [0, ...starts]
  const out = [0]
  for (let i = FALLBACK_SECTION_BEATS; i < beats.length; i += FALLBACK_SECTION_BEATS) out.push(beats[i])
  return out
}

function meanLoudness(segments, start, end) {
  if (!segments?.length) return 0
  let total = 0
  let weight = 0
  for (let i = Math.max(0, lowerBound(segments.map(s => s.start), start) - 1); i < segments.length; i++) {
    const segment = segments[i]
    if (segment.start >= end) break
    const overlap = Math.min(end, segment.start + segment.duration) - Math.max(start, segment.start)
    if (overlap <= 0) continue
    total += segment.loudness * overlap
    weight += overlap
  }
  return weight ? total / weight : -60
}

function snapToBeat(beats, time) {
  const i = lowerBound(beats, time)
  if (i <= 0) return 0
  if (i >= beats.length) return beats.length - 1
  return time - beats[i - 1] < beats[i] - time ? i - 1 : i
}

export function planShots(features, { still = false } = {}) {
  const beats = features?.beats
  if (still || !beats?.length || beats.length < 8) return STILL
  const duration = songDuration(features, beats)
  const rand = seededRandom(songSeed(features, beats))
  const beatPeriod = (beats[beats.length - 1] - beats[0]) / (beats.length - 1)
  const starts = sectionStarts(features, beats, duration)
  const merged = []
  for (const start of starts) {
    if (merged.length && start - merged[merged.length - 1] < MIN_SECTION_S) continue
    merged.push(start)
  }
  const sections = merged.map((start, i) => ({ start, end: merged[i + 1] ?? duration }))
  const loudness = sections.map(s => meanLoudness(features.loudness_segments, s.start, s.end))
  const ranked = [...loudness].sort((a, b) => a - b)
  const rank = value => (ranked.length > 1 ? lowerBound(ranked, value) / (ranked.length - 1) : 0.5)

  const shots = []
  let previous = null
  sections.forEach((section, index) => {
    const energy = rank(loudness[index])
    const level = index === 0 ? 'low' : energy < 0.34 ? 'low' : energy > 0.67 ? 'high' : 'mid'
    const { cutBeats, looks } = LEVELS[level]
    const first = index === 0 ? 0 : snapToBeat(beats, section.start)
    const cuts = [index === 0 ? 0 : beats[first]]
    const lastCut = section.end - beatPeriod * cutBeats / 2
    for (let b = first + cutBeats; b < beats.length && beats[b] < lastCut; b += cutBeats) cuts.push(beats[b])
    cuts.forEach((start, c) => {
      let look = index === 0 && c === 0 ? 'cover' : null
      for (let tries = 0; !look && tries < 4; tries++) {
        const candidate = pickWeighted(rand, looks)
        if (candidate !== previous?.look || tries === 3) look = candidate
      }
      const shot = { start, end: Infinity, look, level, variant: LOOKS[look].variant(rand, level) }
      shots.push(shot)
      previous = shot
    })
  })
  for (let i = 0; i < shots.length - 1; i++) shots[i].end = shots[i + 1].start
  return { shots, beats }
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
  return { shot, index: i, pulse, beatsSince: Math.max(0, b - 1 - firstBeat) }
}
