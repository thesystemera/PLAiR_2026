import { seededRandom } from './random'
import { createDeck } from './director'
import { CLIP_UNIFORMS } from './glsl/footage'

export const MAX_SLOTS = 4
const FREEZE_RATE = 0.0625
const FOOTAGE_LOOK = { cover: 'footage', depth: 'depthFootage', split: 'splitFootage' }

const SHARE = { intro: 0.45, verse: 0.45, build: 0.7, chorus: 0.7, bridge: 0.55, solo: 0.9, interlude: 0.85, outro: 0.5 }
const SUNG_FULL_FRAME = 0.6
const TREAT_CARDS = ['tint', 'punch', 'mirror', 'kaleido', 'plain', 'key', 'double']
const SPEED_CARDS = ['flow', 'ramp', 'freeze', 'slow']
const PERCUSSIVE_SPEED = { ramp: 1, freeze: 1 }
const PANEL_SLOTS = { wipe: 2, quad: 3, strips: 3, columns: 3, slices: 1, blinds: 2, boxes: 3, tunnel: 2, curtain: 1, mosaic: 4 }
const PANEL_TREATS = ['tint', 'punch']
const PATTERN = 8
const BASE_TINT = 0.25
const SUNG_LEVEL = 0.8
const JUMP_SHARE = 0.4
const CLIP_TAIL_S = 1.5
const DEFAULT_CLIP_S = 30
const LOOKAHEAD_SHOTS = 12
const KALEIDO_SPIN = 0.15
const STACK_SHARE = 0.4
const STACK_SCROLL = 0.6
const PERCUSSIVE_DENSITY = 0.6
const DEFAULT_SIZE = Object.freeze({ width: 16, height: 9 })
const DEFAULT_PALETTE = Object.freeze({ dark: [0.05, 0.04, 0.08], light: [0.75, 0.7, 0.85] })

const clamp01 = x => Math.min(1, Math.max(0, x))
const ease = x => x * x * (3 - 2 * x)

function clipSeconds(clips, index) {
  return Math.max(CLIP_TAIL_S * 2, clips[index]?.duration || DEFAULT_CLIP_S) - CLIP_TAIL_S
}

function drawReel(deal, size) {
  const reel = []
  for (let tries = 0; reel.length < size && tries < size * 4; tries++) {
    const clip = deal()
    if (!reel.includes(clip)) reel.push(clip)
  }
  return reel
}

function treatment(card, mode, clipCount, rand) {
  let use = card
  if (mode === 'panels' && !PANEL_TREATS.includes(card) && card !== 'plain') use = PANEL_TREATS[Math.floor(rand() * PANEL_TREATS.length)]
  if (mode === 'behind' && (card === 'key' || card === 'double')) use = 'tint'
  const t = { card: use, tint: BASE_TINT, contrast: 0.2, grain: 0.12, mirror: 0, kaleido: 0, spin: 0, mix: 0, mixAmount: 0 }
  if (use === 'tint') t.tint = 0.9
  if (use === 'punch') Object.assign(t, { tint: 0.5, contrast: 0.75, grain: 0.6 })
  if (use === 'mirror') t.mirror = 1
  if (use === 'kaleido') Object.assign(t, { kaleido: rand() < 0.5 ? 6 : 8, spin: rand() < 0.5 ? -1 : 1 })
  if (use === 'plain') Object.assign(t, { tint: 0.2, contrast: 0.1, grain: 0.05 })
  if (use === 'key') Object.assign(t, { mix: 1, mixAmount: 0.9 })
  if (use === 'double') Object.assign(t, clipCount > 1 ? { mix: 2, mixAmount: 0.55 } : { mix: 0, mixAmount: 0.5 })
  return t
}

function panelPattern(layout, slots, rand) {
  const all = Array.from({ length: PATTERN }, (_, i) => i % slots)
  const shift = rand() < 0.5 ? 1 : 0
  const alternate = Array.from({ length: PATTERN }, (_, i) => ((i + shift) % 2 ? -1 : Math.floor(i / 2) % slots))
  if (layout === 'quad') {
    const panels = all.slice()
    panels[Math.floor(rand() * 4)] = -1
    return { panels, back: -1 }
  }
  if (layout === 'curtain') {
    const sides = rand() < 0.5
    return { panels: Array.from({ length: PATTERN }, (_, i) => ((i === 1) === sides ? -1 : 0)), back: -1 }
  }
  if (layout === 'boxes') return rand() < 0.4 ? { panels: new Array(PATTERN).fill(-1), back: 0 } : { panels: all, back: -1 }
  if (layout === 'mosaic') return { panels: all, back: -1 }
  return { panels: rand() < 0.5 ? all : alternate, back: -1 }
}

export function planFootage(plan, clips) {
  if (!plan?.song || !clips?.length) return plan
  const rand = seededRandom((plan.seed ^ 0x5f00d) >>> 0)
  const count = clips.length
  const dealClip = createDeck(clips.map((_, i) => i), rand)
  const dealTreat = createDeck(TREAT_CARDS, rand)
  const dealSpeed = createDeck(SPEED_CARDS, rand)
  const cursor = clips.map((_, i) => rand() * clipSeconds(clips, i))
  const owed = {}
  const motifs = new Map()
  const reelSize = Math.min(count, MAX_SLOTS)
  let chorusReel = null
  let phrase = null
  let reel = null
  let card = null
  let speed = null
  let fresh = false
  const shots = plan.shots.map((shot, index) => {
    if (shot.phraseBeat !== phrase) {
      phrase = shot.phraseBeat
      reel = drawReel(dealClip, reelSize)
      if (shot.role === 'chorus') {
        chorusReel ??= reel
        reel = chorusReel
      }
      const percussive = (shot.density ?? 0) >= PERCUSSIVE_DENSITY
      card = dealTreat()
      speed = dealSpeed(c => (percussive ? PERCUSSIVE_SPEED[c] || 0 : 1 - (PERCUSSIVE_SPEED[c] || 0)))
      fresh = true
    }
    if (index === 0 || !FOOTAGE_LOOK[shot.look]) return shot
    if (shot.hook >= 0 && motifs.has(shot.hook)) {
      const motif = motifs.get(shot.hook)
      return motif ? { ...shot, footage: motif } : shot
    }
    const share = SHARE[shot.role] * (shot.vocal && shot.look === 'cover' ? SUNG_FULL_FRAME : 1)
    const due = (owed[shot.role] || 0) + share
    const on = shot.drop || due >= 1
    owed[shot.role] = on ? Math.max(0, due - 1) : due
    if (!on) {
      if (shot.hook >= 0) motifs.set(shot.hook, null)
      return shot
    }
    const mode = shot.look === 'cover' ? 'full' : shot.look === 'depth' ? 'behind' : 'panels'
    const filled = shot.gate < 0
    const treat = treatment(filled ? 'punch' : card, mode, reel.length, rand)
    const wanted = mode === 'panels' ? PANEL_SLOTS[shot.variant.layout] || 2 : treat.mix === 2 ? 2 : 1
    const slots = Math.min(wanted, reel.length)
    const step = shot.step ?? 0
    const chosen = Array.from({ length: slots }, (_, k) => reel[(k + step) % reel.length])
    const length = Math.max(0.25, (Number.isFinite(shot.end) ? shot.end : shot.start + 4) - shot.start)
    const at = chosen.map(clip => {
      const seconds = clipSeconds(clips, clip)
      const start = fresh && rand() < JUMP_SHARE ? rand() * seconds : cursor[clip]
      cursor[clip] = (start + length) % seconds
      return start
    })
    fresh = false
    const footage = {
      look: FOOTAGE_LOOK[shot.look],
      mode,
      clips: chosen,
      at,
      treat,
      speed: filled ? 'ramp' : speed,
      pan: (rand() < 0.5 ? -1 : 1) * (0.6 + 0.4 * rand()),
      stack: mode === 'full' && rand() < STACK_SHARE,
      level: shot.vocal && mode === 'full' ? SUNG_LEVEL : 1,
      ...(mode === 'panels' ? panelPattern(shot.variant.layout, slots, rand) : { panels: null, back: -1 }),
    }
    if (shot.hook >= 0) motifs.set(shot.hook, footage)
    return { ...shot, footage }
  })
  return { ...plan, shots }
}

export function createFootageState() {
  return { shot: -1, slots: [], times: new Float64Array(MAX_SLOTS), rates: new Float32Array(MAX_SLOTS), changed: false, next: [], nextAt: [] }
}

function speedOf(speed, fx) {
  if (speed === 'ramp') return Math.min(3, 0.75 + 2.4 * fx.hit)
  if (speed === 'freeze') return fx.hit > 0.55 ? FREEZE_RATE : 1
  if (speed === 'slow') return 0.45 + 0.35 * fx.swell
  return Math.min(2, 0.6 + 0.8 * fx.currentEnergy + 0.6 * fx.swell)
}

function upcoming(state, plan, index) {
  state.next = []
  state.nextAt = []
  const shots = plan.shots
  for (let i = index + 1; i < Math.min(shots.length, index + 1 + LOOKAHEAD_SHOTS); i++) {
    const footage = shots[i].footage
    if (!footage) continue
    footage.clips.forEach((clip, k) => {
      if (state.slots.includes(clip)) return
      state.next.push(clip)
      state.nextAt.push(footage.at[k])
    })
    return
  }
}

export function stepFootage(state, plan, at, t, dt, fx, clips, playing) {
  state.changed = false
  const shot = at?.shot || null
  const index = at ? at.index : -1
  const footage = shot?.footage || null
  if (index !== state.shot) {
    state.shot = index
    state.slots = footage ? footage.clips : []
    if (footage) footage.clips.forEach((clip, k) => { state.times[k] = (footage.at[k] + Math.max(0, t - shot.start)) % clipSeconds(clips, clip) })
    if (plan) upcoming(state, plan, index)
    state.changed = true
  }
  const rate = footage && playing ? speedOf(footage.speed, fx) : 0
  for (let k = 0; k < MAX_SLOTS; k++) {
    state.rates[k] = k < state.slots.length ? rate : 0
    if (k < state.slots.length) state.times[k] = (state.times[k] + rate * dt) % clipSeconds(clips, state.slots[k])
  }
  return state
}

function clipSize(texture) {
  if (texture?.userData?.width) return texture.userData
  const image = texture?.image
  const width = image?.videoWidth || image?.displayWidth || image?.width
  const height = image?.videoHeight || image?.displayHeight || image?.height
  return width && height ? { width, height } : DEFAULT_SIZE
}

export function setFootageUniforms(u, shot, ctx, textures, palette) {
  const f = shot.footage
  for (let k = 0; k < MAX_SLOTS; k++) {
    const texture = textures[k] || null
    u[CLIP_UNIFORMS[k]].value = texture
    const size = clipSize(texture)
    u.u_clip_res.value[k].set(size.width, size.height)
  }
  const treat = f.treat
  const span = Number.isFinite(shot.end) ? Math.max(0.5, shot.end - shot.start) : 8
  const p = clamp01((ctx.t - shot.start) / span)
  const hit = ctx.pulse
  u.u_ft.value.set(treat.tint, Math.min(1, treat.contrast + 0.3 * hit), treat.grain * (1 + 0.8 * hit), f.level)
  const travel = f.stack ? f.pan * STACK_SCROLL * p : f.pan * (2 * ease(p) - 1)
  u.u_ft_shape.value.set(treat.mirror, treat.kaleido, travel, treat.spin * ctx.t * KALEIDO_SPIN)
  u.u_ft_mix.value.set(treat.mix, treat.mixAmount * (0.8 + 0.2 * hit), f.stack ? 1 : 0, 0)
  const colours = palette || DEFAULT_PALETTE
  u.u_ft_dark.value.fromArray(colours.dark)
  u.u_ft_light.value.fromArray(colours.light)
}
