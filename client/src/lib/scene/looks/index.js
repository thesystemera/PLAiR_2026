import { Vector2 } from 'three'
import cover from './cover'
import split from './split'
import depth from './depth'

export const LOOKS = { cover, split, depth }
export const LOOK_IDS = Object.keys(LOOKS)
export { readDepth } from './depth'

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
