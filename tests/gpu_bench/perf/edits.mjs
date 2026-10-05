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
  'no glint': { edits: [['glint += light * max(', 'glint += 0.0 * light * max(']] },
  'no rim': { edits: [['rim += light * max(dot(facing, dir), 0.0);', 'rim += 0.0 * light * max(dot(facing, dir), 0.0);']] },
  'no relief': { edits: [['relief += light * (dot(n, L) - L.z);', 'relief += 0.0 * light * (dot(n, L) - L.z);']] },
  'show edge': { edits: [['return clamp(lit, 0.0, 1.0);', 'return vec3(edge);']] },
  'lights per cover': { edits: [['return skylight(color, hitUV, u_rect.xy + texCoord * u_rect.zw);', 'return skylight(color, hitUV, u_rect.xy + 0.5 * u_rect.zw);']] },
  'bound fixed sizes': { edits: [['vec2 size = vec2(textureSize(u_depth, 0));', 'vec2 size = vec2(1024.0);'], ['ivec2 last = textureSize(u_depth_bound, lod) - 1;', 'ivec2 last = ivec2(1023 >> lod);']] },
  'light 1 of 4': { edits: [['for (int i = 0; i < 4; i++) {', 'for (int i = 0; i < 1; i++) {']] },
  'light 2 of 4': { edits: [['for (int i = 0; i < 4; i++) {', 'for (int i = 0; i < 2; i++) {']] },
  'light no specular': { edits: [['glint += light * max(shine(max(dot(n, H), 0.0)) - shine(H.z), 0.0);', '']] },
  'light no rim': { edits: [['rim += light * max(dot(facing, dir), 0.0);', '']] },
  'light flat normal': { edits: [['SHADE vec2 packedNormal = NORMAL_AT(clamp(hitUV, 0.0, 1.0)).rb * 2.0 - 1.0;', 'SHADE vec2 packedNormal = vec2(0.1, 0.2);']] },
  'march dynamic': { edits: [["for (int i = 0; i < LINEAR_STEPS; i++) {", "for (int i = 0; i < int(u_steps - skipped); i++) {"]] },
  'march dynamic light 1': { edits: [["for (int i = 0; i < LINEAR_STEPS; i++) {", "for (int i = 0; i < int(u_steps - skipped); i++) {"], ["for (int i = 0; i < 4; i++) {", "for (int i = 0; i < 1; i++) {"]] },
  'lights dynamic': { edits: [["for (int i = 0; i < 4; i++) {", "for (int i = 0; i < (u_light > 0.0 ? 4 : 0); i++) {"]] },
  'both dynamic': { edits: [["for (int i = 0; i < LINEAR_STEPS; i++) {", "for (int i = 0; i < int(u_steps - skipped); i++) {"], ["for (int i = 0; i < 4; i++) {", "for (int i = 0; i < (u_light > 0.0 ? 4 : 0); i++) {"]] },
  'steps x0.5': { art: { stepScale: 0.5 } },
  'steps x0.33': { art: { stepScale: 0.33 } },
}

export async function installShaderEdits(p) {
  return p.evaluate(`(() => {
    const proto = WebGL2RenderingContext.prototype
    if (!proto.__originalShaderSource) proto.__originalShaderSource = proto.shaderSource
    window.__shaderEdits = []
    window.__shaderEditHits = []
    proto.shaderSource = function (shader, source) {
      let edited = source
      window.__shaderEdits.forEach(([from, to], index) => {
        if (!edited.includes(from)) return
        edited = edited.split(from).join(to)
        window.__shaderEditHits[index] = (window.__shaderEditHits[index] || 0) + 1
      })
      return proto.__originalShaderSource.call(this, shader, edited)
    }
    return 1
  })()`)
}

export async function applyStage(p, stage) {
  const result = await p.evaluate(`(() => {
    window.__shaderEdits = ${JSON.stringify(stage.edits || [])}
    window.__shaderEditHits = []
    let warned = ''
    const warn = console.warn
    console.warn = (...args) => { warned += args.join(' '); warn(...args) }
    try {
      window.__plairArt.set({ shader: { mediumpShading: true, ...${JSON.stringify(stage.shader || {})} }, stepScale: 1, ...${JSON.stringify(stage.art || {})} })
    } finally {
      console.warn = warn
    }
    window.__plairLight.debug({ off: false, ...${JSON.stringify(stage.light || {})} })
    return JSON.stringify({ hits: window.__shaderEdits.map((_, i) => window.__shaderEditHits[i] || 0), fallback: /unavailable/i.test(warned) })
  })()`)
  if (typeof result === 'string' && result.startsWith('ERR')) throw new Error(result)
  const { hits, fallback } = JSON.parse(result)
  if (fallback) throw new Error('the renderer fell back to its WebGL1 shader: a variant broke the WebGL2 programs')
  if (hits.some(count => !count)) throw new Error('a shader edit matched nothing: ' + JSON.stringify(hits))
  return hits.reduce((a, b) => Math.max(a, b), 0)
}

export async function removeShaderEdits(p) {
  await applyStage(p, {})
  await p.evaluate(`(() => { const proto = WebGL2RenderingContext.prototype; if (proto.__originalShaderSource) proto.shaderSource = proto.__originalShaderSource; return 1 })()`)
}
