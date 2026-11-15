import { Vector2 } from 'three'
import cover from './cover'
import split from './split'
import depth from './depth'

export const LOOKS = { cover, split, depth }
export const LOOK_IDS = Object.keys(LOOKS)
export { readDepth } from './depth'
export { MAX_TILE_VERTICES, tileQuads } from './split'

export function lookUniforms() {
  const uniforms = { u_fx_rot: { value: new Vector2(1, 0) } }
  for (const look of Object.values(LOOKS)) Object.assign(uniforms, look.uniforms())
  return uniforms
}

export const lookSignature = id => ['u_fx_rot', ...LOOKS[id].signature]

export function setLookUniforms(uniforms, shot, ctx) {
  uniforms.u_fx_rot.value.set(Math.cos(ctx.rotation), Math.sin(ctx.rotation))
  LOOKS[shot.look].frame(shot, ctx, uniforms)
}

export function createLookContext(frame, flipY) {
  return {
    t: 0, aspect: 1, hue: 0, rotation: 0, pulse: 0, beatsSince: 0, wordsSince: 0, firstBeat: 0, beats: null, words: null,
    energy: 0, depthSwell: 0, aperture: 0, dolly: 0, orbit: { x: 0, y: 0 }, bands: null, grid: null, frame, flipY, tilt: { x: 0, y: 0 },
  }
}

export function directLook(ctx, plan, at, fx, t) {
  ctx.t = t
  ctx.pulse = fx.hit
  ctx.beatsSince = at.beatsSince
  ctx.wordsSince = at.wordsSince
  ctx.firstBeat = at.firstBeat
  ctx.beats = plan.beats
  ctx.words = plan.song?.words || null
  ctx.energy = fx.currentEnergy
  ctx.depthSwell = fx.depthSwell
  ctx.aperture = fx.aperture
  ctx.dolly = fx.dolly
  ctx.orbit.x = fx.orbitX
  ctx.orbit.y = fx.orbitY
  return ctx
}
