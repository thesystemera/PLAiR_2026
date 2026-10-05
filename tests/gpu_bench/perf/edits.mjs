export const STAGES = {
  'as is': {},
  'lights off': { light: { off: true } },
  'no edge fill': { edits: [['SHADE float dispGate = smoothstep(', 'return pomColor; SHADE float dispGate = smoothstep(']] },
  'no depth bound': { edits: [['float bound = marchBound(uv, displacement);', 'float bound = 2.0;']] },
  'no parallax': { edits: [['return parallax(uv, displacement, hitUV);', 'hitUV = uv; return texture2D(u_color, uv);']] },
  'no parallax no lights': { edits: [['return parallax(uv, displacement, hitUV);', 'hitUV = uv; return texture2D(u_color, uv);']], light: { off: true } },
  'lights mediump': { edits: [
    ['uniform vec4 u_lights[', 'uniform mediump vec4 u_lights['],
    ['uniform vec3 u_light_colors[', 'uniform mediump vec3 u_light_colors['],
    ['uniform float u_aspect;', 'uniform mediump float u_aspect;'],
    ['SHADE vec3 skylight(SHADE vec3 color, vec2 hitUV, vec2 screenUV) {', 'SHADE vec3 skylight(SHADE vec3 color, vec2 hitUV, SHADE vec2 screenUV) {'],
  ] },
  'nolight nofill': { light: { off: true }, edits: [['SHADE float dispGate = smoothstep(', 'return pomColor; SHADE float dispGate = smoothstep(']] },
  'nolight nomarch': { light: { off: true }, edits: [['for (int i = 0; i < LINEAR_STEPS; i++) {', 'for (int i = 0; i < 0; i++) {']] },
  'nolight nobound': { light: { off: true }, edits: [['float bound = marchBound(uv, displacement);', 'float bound = 2.0;']] },
  'reference': { art: { stepScale: 4 }, edits: [['const int LINEAR_STEPS = 24;', 'const int LINEAR_STEPS = 96;']] },
  'steps x0.5': { art: { stepScale: 0.5 } },
  'steps x0.33': { art: { stepScale: 0.33 } },
}

export async function installShaderEdits(p) {
  return p.evaluate(`(() => {
    const proto = WebGL2RenderingContext.prototype
    if (!proto.__originalShaderSource) proto.__originalShaderSource = proto.shaderSource
    window.__shaderEdits = []
    window.__shaderEditHits = 0
    proto.shaderSource = function (shader, source) {
      let edited = source
      if (source.includes('vec4 parallax(')) {
        for (const [from, to] of window.__shaderEdits) {
          if (!edited.includes(from)) throw new Error('shader edit not found: ' + from)
          edited = edited.split(from).join(to)
        }
        if (window.__shaderEdits.length) window.__shaderEditHits++
      }
      return proto.__originalShaderSource.call(this, shader, edited)
    }
    return 1
  })()`)
}

export async function applyStage(p, stage) {
  const result = await p.evaluate(`(() => {
    window.__shaderEdits = ${JSON.stringify(stage.edits || [])}
    window.__shaderEditHits = 0
    window.__plairArt.set({ shader: { mediumpShading: true }, stepScale: 1, ...${JSON.stringify(stage.art || {})} })
    window.__plairLight.debug({ off: false, ...${JSON.stringify(stage.light || {})} })
    return window.__shaderEditHits
  })()`)
  if (typeof result === 'string') throw new Error(result)
  return result
}

export async function removeShaderEdits(p) {
  await applyStage(p, {})
  await p.evaluate(`(() => { const proto = WebGL2RenderingContext.prototype; if (proto.__originalShaderSource) proto.shaderSource = proto.__originalShaderSource; return 1 })()`)
}
