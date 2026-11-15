import { LOOKS } from './looks'
import { lowerBound } from '../sceneEffects'
import { pickWeighted, seededRandom } from './random'
import { analyzeSong, lineAt } from './structure'
import { measureSpread } from './beatFx'

const RECIPES = {
  intro: { cameras: [['push', 2], ['coverage', 1]], palettes: [['sway', 2], ['prism', 2], ['colour', 1]], gain: 0.8, level: 'low' },
  verse: { cameras: [['coverage', 2], ['drift', 1]], palettes: [['prism', 2], ['sway', 2], ['colour', 2], ['jolt', 1], ['glitch', 1]], gain: 0.95, level: 'mid' },
  build: { cameras: [['pushHard', 1]], palettes: [['glitch', 2], ['prism', 2], ['jolt', 1]], gain: 1.1, level: 'high', accelerate: true },
  chorus: { cameras: [['coverage', 1]], palettes: [['jolt', 2], ['prism', 2], ['glitch', 2], ['colour', 1]], gain: 1.2, level: 'high' },
  bridge: { cameras: [['drift', 1], ['coverage', 1]], palettes: [['colour', 2], ['sway', 2], ['prism', 1]], gain: 0.9, level: 'mid' },
  solo: { cameras: [['shake', 1]], palettes: [['glitch', 2], ['jolt', 2], ['prism', 1]], gain: 1.3, level: 'high' },
  interlude: { cameras: [['float', 1]], palettes: [['sway', 2], ['colour', 2], ['prism', 1]], gain: 0.75, level: 'low' },
  outro: { cameras: [['pull', 1]], palettes: [['sway', 2], ['colour', 1], ['prism', 1]], gain: 0.75, level: 'low' },
}

const PALETTES = {
  prism: { chroma: 1, hue: 0.35, glitch: 0.2, twitch: 0.15, punch: 0.45, sway: 0.25, strobe: 0.1 },
  jolt: { chroma: 0.45, hue: 0.25, glitch: 0.4, twitch: 0.9, punch: 1, sway: 0.1, strobe: 0.25 },
  glitch: { chroma: 0.8, hue: 0.35, glitch: 1, twitch: 0.35, punch: 0.4, sway: 0.1, strobe: 0.45 },
  sway: { chroma: 0.55, hue: 0.45, glitch: 0.1, twitch: 0.1, punch: 0.3, sway: 1, strobe: 0 },
  colour: { chroma: 0.6, hue: 1, glitch: 0.2, twitch: 0.2, punch: 0.4, sway: 0.35, strobe: 0.1 },
}

function shotFx(palette, gain, energy, final) {
  const scale = gain * (0.75 + 0.45 * energy) * (final ? 1.2 : 1)
  const fx = { palette }
  for (const [key, weight] of Object.entries(PALETTES[palette])) fx[key] = Math.min(1.3, weight * scale)
  return fx
}

const RHYTHMS = {
  intro: ['sparse', 'down', 'half'],
  verse: ['sparse', 'down', 'off', 'half', 'beat'],
  build: ['half', 'beat', 'eighth'],
  chorus: ['half', 'off', 'beat', 'eighth'],
  bridge: ['down', 'off', 'half'],
  solo: ['beat', 'eighth'],
  interlude: ['sparse', 'down'],
  outro: ['down', 'sparse'],
}

const MIN_GAP = { sparse: 8, down: 4, off: 4, half: 4, beat: 2, eighth: 2 }
const EFFECT_CARDS = ['twitch', 'rotate', 'prism', 'punch', 'hue', 'strobe']
const LOOK_CARDS = [
  ['cover', {}],
  ['depth', {}],
  ['split', { layout: 'wipe' }],
  ['split', { layout: 'quad' }],
  ['split', { layout: 'strips' }],
  ['split', { layout: 'columns' }],
  ['split', { layout: 'slices' }],
  ['split', { layout: 'blinds' }],
  ['split', { layout: 'boxes' }],
  ['split', { layout: 'tunnel' }],
]

const CHORUS_FAMILIES = [
  [['split', { layout: 'boxes' }], ['cover', {}]],
  [['split', { layout: 'strips' }], ['depth', { motion: 'pulse' }]],
  [['split', { layout: 'quad' }], ['cover', {}]],
  [['depth', { motion: 'pulse' }], ['split', { layout: 'wipe' }]],
  [['split', { layout: 'wipe' }], ['split', { layout: 'boxes' }]],
  [['split', { layout: 'slices' }], ['cover', {}]],
  [['split', { layout: 'tunnel' }], ['depth', { motion: 'pulse' }]],
  [['split', { layout: 'columns' }], ['split', { layout: 'boxes' }]],
  [['split', { layout: 'blinds' }], ['split', { layout: 'tunnel' }]],
]

const PULSE_DECAY_S = 0.18
const STILL = Object.freeze({ shots: [Object.freeze({ start: 0, end: Infinity, look: 'cover', variant: {}, level: 'low', role: 'verse', fx: shotFx('sway', 0.6, 0.3, false), camera: 'still', seed: 1 })], beats: [], song: null })

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

function accelerating(first, last, bpb, step) {
  const cuts = []
  let end = last
  for (const [beats, count] of [[1, bpb], [2, 2], [4, 2]]) {
    for (let i = 0; i < count && end - beats >= first; i++) {
      end -= beats
      cuts.push(end)
    }
  }
  for (let b = first; b < end; b += step) cuts.push(b)
  return cuts.sort((x, y) => x - y)
}

function phraseRhythm(role, phrase, sectionEnergy, rand) {
  const list = RHYTHMS[role]
  let index = Math.floor((phrase.index / Math.max(1, phrase.count)) * list.length)
  const lift = phrase.energy - sectionEnergy
  if (lift > 0.08) index++
  if (lift < -0.08) index--
  if (rand() < 0.2) index += rand() < 0.5 ? -1 : 1
  return list[Math.max(0, Math.min(list.length - 1, index))]
}

function phraseCuts(song, phrase, role, rhythm, fill, lineStarts) {
  const { beats, bpb } = song
  const cuts = new Map([[phrase.start, 'phrase']])
  if (RECIPES[role].accelerate) {
    for (const b of accelerating(phrase.start, phrase.end, bpb, 2 * bpb)) if (!cuts.has(b)) cuts.set(b, 'fill')
    return cuts
  }
  const fillStart = fill ? Math.max(phrase.start + 1, phrase.end - bpb) : phrase.end
  for (const { beat, line } of lineStarts) if (beat > phrase.start && beat < fillStart) cuts.set(beat, line)
  for (let b = phrase.start + bpb; b < fillStart; b += bpb) {
    if (!cuts.has(b) && !lineAt(song, beats[b] + 0.05)) cuts.set(b, 'gap')
  }
  for (let b = fillStart; b < phrase.end; b += role === 'solo' ? 1 : 2) cuts.set(b, 'fill')
  const minGap = MIN_GAP[rhythm]
  const kept = new Map()
  let last = -Infinity
  for (const b of [...cuts.keys()].sort((x, y) => x - y)) {
    const reason = cuts.get(b)
    const gap = reason === 'phrase' ? 0 : reason === 'fill' ? 1 : minGap
    if (b - last < gap) continue
    kept.set(b, reason)
    last = b
  }
  return kept
}

function createDeck(cards, rand) {
  let pile = []
  let last = null
  return () => {
    if (!pile.length) {
      pile = cards.slice()
      for (let i = pile.length - 1; i > 0; i--) {
        const j = Math.floor(rand() * (i + 1))
        ;[pile[i], pile[j]] = [pile[j], pile[i]]
      }
      if (pile.length > 1 && pile[pile.length - 1] === last) [pile[0], pile[pile.length - 1]] = [pile[pile.length - 1], pile[0]]
    }
    last = pile.pop()
    return last
  }
}

export function planShots(features, { sections, lines, still = false } = {}) {
  const beats = features?.beats
  if (still || !beats?.length || beats.length < 8) return STILL
  const song = analyzeSong(features, sections, lines)
  const rand = seededRandom(songSeed(features, beats))
  const family = CHORUS_FAMILIES[Math.floor(rand() * CHORUS_FAMILIES.length)]
  const lineStarts = song.lines.map(line => ({ beat: snapToBeat(beats, line.start), line }))
  const motifs = new Map()
  const shots = []
  const dealEffect = createDeck(EFFECT_CARDS, rand)
  const dealLook = createDeck(LOOK_CARDS, rand)
  const coverage = { effects: {}, looks: {} }
  let chorusShots = 0
  song.sections.forEach((section, sectionIndex) => {
    const recipe = RECIPES[section.role]
    const nextIsChorus = song.sections[sectionIndex + 1]?.role === 'chorus'
    section.phrases.forEach(phrase => {
      const role = section.role
      const rhythm = phraseRhythm(role, phrase, section.energy, rand)
      const palette = pickWeighted(rand, recipe.palettes)
      const lastPhrase = phrase.index === phrase.count - 1
      const fill = lastPhrase ? nextIsChorus || role === 'chorus' || role === 'solo' || rand() < 0.3 : role === 'chorus' && rand() < 0.4
      const cuts = phraseCuts(song, phrase, role, rhythm, fill, lineStarts)
      const lead = dealEffect()
      const support = dealEffect()
      coverage.effects[lead] = (coverage.effects[lead] || 0) + 1
      for (const [b, reason] of cuts) {
        const opening = sectionIndex === 0 && phrase.index === 0 && b === phrase.start
        const start = opening ? 0 : beats[b]
        const vocal = lineAt(song, beats[b] + 0.05)
        const hook = typeof reason === 'object' && reason.hook >= 0 ? reason.hook : -1
        const filled = reason === 'fill'
        const previous = shots[shots.length - 1]
        let look
        let variant
        let camera
        const level = filled ? 'high' : recipe.level
        const motif = hook >= 0 ? motifs.get(hook) : null
        if (motif) {
          ;({ look, variant, camera } = motif)
        } else {
          let prefer = {}
          if (opening) look = 'cover'
          else if (role === 'chorus' && !filled) {
            const pick = family[chorusShots++ % family.length]
            look = pick[0]
            prefer = pick[1]
          } else {
            const card = dealLook()
            look = card[0]
            prefer = card[1]
          }
          variant = LOOKS[look].variant(rand, level, prefer)
          const held = typeof reason === 'object' && reason.end - reason.start > 2 * song.bpb * song.period
          if (b === phrase.start) camera = role === 'chorus' || role === 'solo' ? 'coverage' : 'push'
          else if (held) camera = 'push'
          else if (!vocal) camera = role === 'solo' ? 'shake' : 'coverage'
          else camera = pickWeighted(rand, recipe.cameras)
          if (hook >= 0) motifs.set(hook, { look, variant, camera })
        }
        const shot = {
          start,
          end: Infinity,
          look,
          level,
          role: filled && nextIsChorus && lastPhrase ? 'build' : role,
          section: section.type,
          energy: phrase.energy,
          fx: shotFx(filled ? (rand() < 0.5 ? 'glitch' : 'jolt') : palette, recipe.gain, phrase.energy, section.final),
          rhythm: filled ? 'fill' : rhythm,
          lead,
          support,
          phraseBeat: phrase.start,
          camera,
          drop: role === 'chorus' && phrase.index === 0 && b === phrase.start,
          hook,
          vocal: !!vocal,
          seed: Math.floor(rand() * 1e9),
          variant,
        }
        if (!previous || shot.start > previous.start + 0.05) {
          shots.push(shot)
          const name = look === 'split' ? variant.layout : look
          coverage.looks[name] = (coverage.looks[name] || 0) + 1
        }
      }
    })
  })
  if (!shots.length) return STILL
  shots[0].start = 0
  for (let i = 0; i < shots.length - 1; i++) shots[i].end = shots[i + 1].start
  const plan = { shots, beats, song, coverage }
  plan.norm = measureSpread(plan, directorAt, createDirectorCursor)
  return plan
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
