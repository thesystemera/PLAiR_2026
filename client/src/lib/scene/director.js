import { LOOKS } from './looks'
import { lowerBound, numericAscending } from '../sceneEffects'
import { seededRandom } from './random'
import { analyzeSong, lineAt } from './structure'
import { measureSpread } from './beatFx'

const RECIPES = {
  intro: { camera: 'deck', gain: 0.8, level: 'low', density: 0.3 },
  verse: { camera: 'deck', gain: 0.95, level: 'mid', density: 0.45 },
  build: { camera: 'pushHard', gain: 1.1, level: 'high', density: 0.7, accelerate: true },
  chorus: { camera: 'deck', gain: 1.2, level: 'high', density: 0.75 },
  bridge: { camera: 'deck', gain: 0.9, level: 'mid', density: 0.45 },
  solo: { camera: 'shake', gain: 1.3, level: 'high', density: 0.85 },
  interlude: { camera: 'float', gain: 0.75, level: 'low', density: 0.3 },
  outro: { camera: 'deck', gain: 0.75, level: 'low', density: 0.35 },
}

const PATCH_CARDS = ['zoom', 'depth', 'prism', 'light', 'shake', 'tilt', 'focus']
const PATCH_SHARE = { primary: 1, secondary: 0.55, rest: 0.15 }
const EFFECT_CARDS = ['twitch', 'rotate', 'prism', 'punch', 'hue', 'strobe']
const CAMERA_CARDS = ['push', 'pull', 'pan', 'mid', 'closePan', 'wide', 'orbit', 'crane']
const LOOK_CARDS = [
  ['cover', {}],
  ['cover', {}],
  ['depth', { motion: 'orbit' }],
  ['depth', { motion: 'dolly' }],
  ['depth', { motion: 'track' }],
  ['depth', { motion: 'crane' }],
  ['depth', { motion: 'breathe' }],
  ['split', { layout: 'wipe' }],
  ['split', { layout: 'quad' }],
  ['split', { layout: 'strips' }],
  ['split', { layout: 'columns' }],
  ['split', { layout: 'slices' }],
  ['split', { layout: 'blinds' }],
  ['split', { layout: 'boxes' }],
  ['split', { layout: 'tunnel' }],
  ['split', { layout: 'curtain' }],
  ['split', { layout: 'mosaic' }],
]

const CHORUS_FAMILIES = [
  [['split', { layout: 'boxes' }], ['depth', { motion: 'orbit' }]],
  [['split', { layout: 'strips' }], ['depth', { motion: 'breathe' }]],
  [['split', { layout: 'quad' }], ['cover', {}]],
  [['depth', { motion: 'dolly' }], ['split', { layout: 'wipe' }]],
  [['depth', { motion: 'track' }], ['split', { layout: 'boxes' }]],
  [['split', { layout: 'slices' }], ['cover', {}]],
  [['split', { layout: 'tunnel' }], ['depth', { motion: 'breathe' }]],
  [['split', { layout: 'columns' }], ['depth', { motion: 'crane' }]],
  [['split', { layout: 'blinds' }], ['depth', { motion: 'orbit' }]],
  [['split', { layout: 'mosaic' }], ['depth', { motion: 'dolly' }]],
  [['split', { layout: 'curtain' }], ['depth', { motion: 'track' }]],
]

const PERCUSSIVE = { zoom: 1, shake: 1, tilt: 1, prism: 0.5, twitch: 1, punch: 1, strobe: 1, rotate: 0.5 }
const SUNG_LAYOUTS = { wipe: 0.5, strips: 0.5, columns: 0.5, quad: 0.5, curtain: 0.5 }
const SPACE_SHARE = { intro: 0.15, verse: 0.15, build: 0.4, chorus: 0.35, bridge: 0.2, solo: 0.4, interlude: 0.3, outro: 0.15 }
const SPACE_IN_GAPS = 0.15
const DROP_LIFT = 0.22
const CALM_LIFT = -0.25
const BUILD_TREND = 0.2
const FAST_WORDS = 3.2
const SLOW_WORDS = 1.3
const STILL = Object.freeze({ shots: [Object.freeze({ start: 0, end: Infinity, look: 'cover', variant: {}, level: 'low', role: 'verse', patch: shotPatch('depth', 'light', 0.6, 0.3, false), camera: 'still', space: false, seed: 1, gate: 2, hueShift: 0, firstWord: 0 })], beats: [], song: null })

const clamp = (x, min, max) => Math.min(max, Math.max(min, x))

function shotPatch(primary, secondary, gain, energy, final) {
  const scale = gain * (0.75 + 0.45 * energy) * (final ? 1.2 : 1)
  const patch = { primary, secondary }
  for (const card of PATCH_CARDS) {
    const share = card === primary ? PATCH_SHARE.primary : card === secondary ? PATCH_SHARE.secondary : PATCH_SHARE.rest
    patch[card] = Math.min(1.3, share * scale)
  }
  return patch
}

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

function phraseDensity(recipe, phrase, typicalPunch) {
  const along = phrase.count > 1 ? phrase.index / (phrase.count - 1) - 0.5 : 0
  const punch = clamp((phrase.punchy / Math.max(0.001, typicalPunch) - 1) * 0.2, -0.2, 0.2)
  return clamp(recipe.density + 0.4 * (phrase.energy - 0.5) + punch + 0.15 * along, 0.15, 1)
}

function accentGate(accents, phrase, density) {
  const sorted = Array.from(accents.subarray(phrase.start, phrase.end)).sort(numericAscending)
  if (!sorted.length) return 0
  return sorted[Math.min(sorted.length - 1, Math.floor((1 - density) * sorted.length))]
}

function cutGap(density, phrase) {
  const gap = density < 0.35 ? 8 : density < 0.65 ? 4 : 2
  if (phrase.sung <= 0.5) return gap
  if (phrase.wordRate > FAST_WORDS) return Math.min(gap, 2)
  if (phrase.wordRate < SLOW_WORDS) return Math.max(gap, 8)
  return gap
}

function phraseCuts(song, phrase, role, minGap, fill, lineStarts) {
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

export function createDeck(cards, rand) {
  let pile = []
  let last = null
  return fit => {
    if (!pile.length) {
      pile = cards.slice()
      for (let i = pile.length - 1; i > 0; i--) {
        const j = Math.floor(rand() * (i + 1))
        ;[pile[i], pile[j]] = [pile[j], pile[i]]
      }
      if (pile.length > 1 && pile[pile.length - 1] === last) [pile[0], pile[pile.length - 1]] = [pile[pile.length - 1], pile[0]]
    }
    const top = pile.length - 1
    if (fit && top > 0 && fit(pile[top - 1]) > fit(pile[top])) [pile[top], pile[top - 1]] = [pile[top - 1], pile[top]]
    last = pile.pop()
    return last
  }
}

function median(values) {
  const sorted = values.slice().sort(numericAscending)
  return sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0
}

const lookName = (look, variant) => (look === 'split' ? variant.layout : look === 'depth' ? `depth ${variant.motion}` : look)

export function planShots(features, { sections, lines, still = false } = {}) {
  const beats = features?.beats
  if (still || !beats?.length || beats.length < 8) return STILL
  const song = analyzeSong(features, sections, lines)
  const rand = seededRandom(songSeed(features, beats))
  const family = CHORUS_FAMILIES[Math.floor(rand() * CHORUS_FAMILIES.length)]
  const lineStarts = song.lines.map(line => ({ beat: snapToBeat(beats, line.start), line }))
  const typicalPunch = median(song.sections.flatMap(section => section.phrases.map(phrase => phrase.punchy)))
  const motifs = new Map()
  const shots = []
  const dealEffect = createDeck(EFFECT_CARDS, rand)
  const dealPatch = createDeck(PATCH_CARDS, rand)
  const dealLook = createDeck(LOOK_CARDS, rand)
  const dealCamera = createDeck(CAMERA_CARDS, rand)
  let subject = 0
  const coverage = { effects: {}, looks: {}, patches: {}, cameras: {}, space: {} }
  const spaceOwed = {}
  const count = (group, name) => { coverage[group][name] = (coverage[group][name] || 0) + 1 }
  let chorusShots = 0
  song.sections.forEach((section, sectionIndex) => {
    const recipe = RECIPES[section.role]
    const nextIsChorus = song.sections[sectionIndex + 1]?.role === 'chorus'
    section.phrases.forEach(phrase => {
      const role = section.role
      const percussive = phrase.punchy >= typicalPunch
      const character = card => (percussive ? PERCUSSIVE[card] || 0 : 1 - (PERCUSSIVE[card] || 0))
      const density = phraseDensity(recipe, phrase, typicalPunch)
      const gate = accentGate(song.accents, phrase, density)
      const lastPhrase = phrase.index === phrase.count - 1
      const fill = lastPhrase ? nextIsChorus || role === 'chorus' || role === 'solo' || rand() < 0.3 : role === 'chorus' && rand() < 0.4
      const cuts = phraseCuts(song, phrase, role, cutGap(density, phrase), fill, lineStarts)
      const lead = dealEffect(character)
      const support = dealEffect(character)
      const primary = dealPatch(character)
      const secondary = dealPatch(character)
      const calm = phrase.lift < CALM_LIFT
      const rising = phrase.trend > BUILD_TREND
      const phraseSubject = subject++
      let step = 0
      count('effects', lead)
      count('patches', primary)
      for (const [b, reason] of cuts) {
        const opening = sectionIndex === 0 && phrase.index === 0 && b === phrase.start
        const start = opening ? 0 : beats[b]
        const vocal = !!lineAt(song, beats[b] + 0.05)
        const hook = typeof reason === 'object' && reason.hook >= 0 ? reason.hook : -1
        const filled = reason === 'fill'
        const previous = shots[shots.length - 1]
        let look
        let variant
        let camera
        let space
        let shotSubject = phraseSubject
        let shotStep = step++
        const level = filled ? 'high' : recipe.level
        const motif = hook >= 0 ? motifs.get(hook) : null
        if (motif) {
          ;({ look, variant, camera, space, subject: shotSubject, step: shotStep } = motif)
        } else {
          let prefer = {}
          if (opening) look = 'cover'
          else if (role === 'chorus' && !filled) {
            const pick = family[chorusShots++ % family.length]
            look = pick[0]
            prefer = pick[1]
          } else {
            const card = dealLook(([id, options]) => {
              const sung = id === 'split' ? SUNG_LAYOUTS[options.layout] || 0 : 1
              return vocal ? sung : 1 - sung
            })
            look = card[0]
            prefer = card[1]
          }
          variant = LOOKS[look].variant(rand, level, prefer)
          if (recipe.camera !== 'deck') camera = recipe.camera
          else if (calm) camera = 'float'
          else if (rising && b >= (phrase.start + phrase.end) / 2) camera = 'push'
          else camera = dealCamera()
          if (look === 'depth') space = true
          else {
            const owed = (spaceOwed[role] || 0) + SPACE_SHARE[role] + (vocal ? 0 : SPACE_IN_GAPS)
            space = owed >= 1
            spaceOwed[role] = space ? owed - 1 : owed
          }
          if (hook >= 0) motifs.set(hook, { look, variant, camera, space, subject: shotSubject, step: shotStep })
        }
        const turn = rand()
        const shot = {
          start,
          end: Infinity,
          look,
          level,
          role: filled && nextIsChorus && lastPhrase ? 'build' : role,
          section: section.type,
          energy: phrase.energy,
          patch: filled ? shotPatch(percussive ? 'shake' : 'prism', 'zoom', recipe.gain, phrase.energy, section.final) : shotPatch(primary, secondary, recipe.gain, phrase.energy, section.final),
          density: filled ? 1 : density,
          gate: filled ? -1 : gate,
          onsets: filled || density >= 0.7,
          lead,
          support,
          phraseBeat: phrase.start,
          camera,
          space,
          subject: shotSubject,
          step: shotStep,
          drop: b === phrase.start && ((role === 'chorus' && phrase.index === 0) || phrase.lift > DROP_LIFT),
          hook,
          vocal,
          hueShift: turn < 0.5 ? 0 : (turn < 0.75 ? -1 : 1) * (10 + 18 * rand()),
          firstWord: lowerBound(song.words, start),
          seed: Math.floor(rand() * 1e9),
          variant,
        }
        if (!previous || shot.start > previous.start + 0.05) {
          shots.push(shot)
          count('looks', lookName(look, variant))
          count('cameras', camera)
          count('space', space ? 'in 3D' : 'flat')
        }
      }
    })
  })
  if (!shots.length) return STILL
  shots[0].start = 0
  for (let i = 0; i < shots.length - 1; i++) shots[i].end = shots[i + 1].start
  const plan = { shots, beats, song, coverage, seed: songSeed(features, beats) }
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
  const firstBeat = lowerBound(beats, shot.start - 0.05)
  const words = plan.song?.words
  const wordsSince = words?.length ? Math.max(0, lowerBound(words, t + 0.001) - shot.firstWord) : 0
  return { shot, index: i, beat: b - 1, firstBeat, beatsSince: Math.max(0, b - 1 - firstBeat), wordsSince }
}
