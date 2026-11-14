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
  'bound per draw': { edits: [["float span = max(hi.x - lo.x, hi.y - lo.y);", "float span = max(abs(displacement.x) * size.x, abs(displacement.y) * size.y) + 2.0;"]] },
  'no loop check': { edits: [["if (testDepth > bound) {", "if (false) {"]] },
  'no normalize': { edits: [["SHADE vec3 n = normalize(vec3(packedNormal, sqrt(max(0.0, 1.0 - dot(packedNormal, packedNormal)))));", "SHADE vec3 n = vec3(packedNormal, sqrt(max(0.0, 1.0 - dot(packedNormal, packedNormal))));"]] },
  'fill square': { edits: [["pow(1.0 - fd1, 2.0)", "(1.0 - fd1) * (1.0 - fd1)"], ["pow(1.0 - fd2, 2.0)", "(1.0 - fd2) * (1.0 - fd2)"], ["pow(1.0 - fd3, 2.0)", "(1.0 - fd3) * (1.0 - fd3)"], ["pow(1.0 - fd4, 2.0)", "(1.0 - fd4) * (1.0 - fd4)"]] },
  'all four': { edits: [["float span = max(hi.x - lo.x, hi.y - lo.y);", "float span = max(abs(displacement.x) * size.x, abs(displacement.y) * size.y) + 2.0;"], ["if (testDepth > bound) {", "if (false) {"], ["SHADE vec3 n = normalize(vec3(packedNormal, sqrt(max(0.0, 1.0 - dot(packedNormal, packedNormal)))));", "SHADE vec3 n = vec3(packedNormal, sqrt(max(0.0, 1.0 - dot(packedNormal, packedNormal))));"], ["pow(1.0 - fd1, 2.0)", "(1.0 - fd1) * (1.0 - fd1)"], ["pow(1.0 - fd2, 2.0)", "(1.0 - fd2) * (1.0 - fd2)"], ["pow(1.0 - fd3, 2.0)", "(1.0 - fd3) * (1.0 - fd3)"], ["pow(1.0 - fd4, 2.0)", "(1.0 - fd4) * (1.0 - fd4)"]] },
  'light algebra': { edits: [
    ['SHADE vec3 L = vec3(toLight,', 'SHADE float invL = inversesqrt(dist2 + 0.2025); SHADE float Lz = 0.45 * invL; SHADE float nL = (dot(n.xy, toLight) + n.z * 0.45) * invL; //'],
    ['relief += light * (dot(n, L) - L.z);', 'relief += light * (nL - Lz);'],
    ['SHADE vec3 H = vec3(L.xy, L.z + 1.0) * inversesqrt(2.0 + 2.0 * L.z);', 'SHADE float invH = inversesqrt(2.0 + 2.0 * Lz);'],
    ['glint += light * max(shine(max(dot(n, H), 0.0)) - shine(H.z), 0.0);', 'glint += light * max(shine(max((nL + n.z) * invH, 0.0)) - shine((Lz + 1.0) * invH), 0.0);'],
  ] },
  'normal algebra': { edits: [
    ['SHADE vec3 n = normalize(vec3(packedNormal, sqrt(max(0.0, 1.0 - dot(packedNormal, packedNormal)))));', 'SHADE float pp = dot(packedNormal, packedNormal); SHADE float invP = inversesqrt(max(pp, 1e-6)); SHADE vec3 n = vec3(pp > 1.0 ? packedNormal * invP : packedNormal, sqrt(max(0.0, 1.0 - pp)));'],
    ['SHADE float slopeLen = length(n.xy);', 'SHADE float slopeLen = min(pp * invP, 1.0);'],
    ['SHADE vec2 facing = slopeLen > 0.0001 ? n.xy / slopeLen : vec2(0.0);', 'SHADE vec2 facing = slopeLen > 0.0001 ? packedNormal * invP : vec2(0.0);'],
  ] },
  'glint gated': { edits: [
    ['glint += light * max(shine(max(dot(n, H), 0.0)) - shine(H.z), 0.0);', 'SHADE float nH = dot(n, H); if (nH > H.z) glint += light * (shine(nH) - shine(H.z));'],
  ] },
  'bound ints': { edits: [
    ['float span = max(hi.x - lo.x, hi.y - lo.y);', 'ivec2 ilo = ivec2(lo); ivec2 ihi = ivec2(hi); int ispan = max(ihi.x - ilo.x, ihi.y - ilo.y); int lod = ispan <= 1 ? 0 : (floatBitsToInt(float(ispan) - 0.5) >> 23) - 126; ivec2 c0 = ilo >> lod; ivec2 c1 = ihi >> lod; if (c1.x - c0.x > 1 || c1.y - c0.y > 1) { lod += 1; c0 = ilo >> lod; c1 = ihi >> lod; } if (float(lod) >= u_bound_levels) return 2.0; ivec2 last = max(ivec2(u_bound_size) >> lod, ivec2(1)) - 1; c0 = min(c0, last); c1 = min(c1, last); /*'],
    ['ivec2 c1 = min(ivec2(floor(hi / block)), last);', '*/'],
  ] },
  'fill never': { edits: [['if (trailMask <= 0.0) return pomColor;', 'if (trailMask <= 2.0) return pomColor;']] },
  'taps gone fill never': { edits: [['if (trailMask <= 0.0) return pomColor;', 'if (trailMask <= 2.0) return pomColor;'], ['SHADE float gradient = abs(dR - dL) + abs(dD - dU);', 'SHADE float gradient = pomColor.a * 0.3;']] },
  'taps gone': { edits: [['SHADE float gradient = abs(dR - dL) + abs(dD - dU);', 'SHADE float gradient = pomColor.a * 0.3;']] },
  'taps only': { edits: [['if (trailMask <= 0.0) return pomColor;', 'fillMix = trailMask * 0.85; return pomColor;']] },
  'gate only': { edits: [['if (foregroundness <= 0.0) return pomColor;', 'fillMix = foregroundness * dispGate * 0.0001; return pomColor;']] },
  'fill lod': { edits: [
    ['SHADE float fd1 = DEPTH_AT(clamp(s1, 0.0, 1.0));', 'SHADE float fd1 = textureLod(u_depth, clamp(s1, 0.0, 1.0), 0.0).g;'],
    ['SHADE float fd2 = DEPTH_AT(clamp(s2, 0.0, 1.0));', 'SHADE float fd2 = textureLod(u_depth, clamp(s2, 0.0, 1.0), 0.0).g;'],
    ['SHADE float fd3 = DEPTH_AT(clamp(s3, 0.0, 1.0));', 'SHADE float fd3 = textureLod(u_depth, clamp(s3, 0.0, 1.0), 0.0).g;'],
    ['SHADE float fd4 = DEPTH_AT(clamp(s4, 0.0, 1.0));', 'SHADE float fd4 = textureLod(u_depth, clamp(s4, 0.0, 1.0), 0.0).g;'],
    ['SHADE vec4 fc1 = COLOR_AT(', 'SHADE vec4 fc1 = textureLod(u_color, '],
    ['SHADE vec4 fc2 = COLOR_AT(', 'SHADE vec4 fc2 = textureLod(u_color, '],
    ['SHADE vec4 fc3 = COLOR_AT(', 'SHADE vec4 fc3 = textureLod(u_color, '],
    ['SHADE vec4 fc4 = COLOR_AT(', 'SHADE vec4 fc4 = textureLod(u_color, '],
    ['* displacement, 0.001, 0.999)) * w1;', '* displacement, 0.001, 0.999), 0.0) * w1;'],
    ['* displacement, 0.001, 0.999)) * w2;', '* displacement, 0.001, 0.999), 0.0) * w2;'],
    ['* displacement, 0.001, 0.999)) * w3;', '* displacement, 0.001, 0.999), 0.0) * w3;'],
    ['* displacement, 0.001, 0.999)) * w4;', '* displacement, 0.001, 0.999), 0.0) * w4;'],
    ['SHADE float dL = DEPTH_AT(clamp(testUV - vec2(texel, 0.0), 0.0, 1.0));', 'SHADE float dL = textureLod(u_depth, clamp(testUV - vec2(texel, 0.0), 0.0, 1.0), 0.0).g;'],
    ['SHADE float dR = DEPTH_AT(clamp(testUV + vec2(texel, 0.0), 0.0, 1.0));', 'SHADE float dR = textureLod(u_depth, clamp(testUV + vec2(texel, 0.0), 0.0, 1.0), 0.0).g;'],
    ['SHADE float dU = DEPTH_AT(clamp(testUV - vec2(0.0, texel), 0.0, 1.0));', 'SHADE float dU = textureLod(u_depth, clamp(testUV - vec2(0.0, texel), 0.0, 1.0), 0.0).g;'],
    ['SHADE float dD = DEPTH_AT(clamp(testUV + vec2(0.0, texel), 0.0, 1.0));', 'SHADE float dD = textureLod(u_depth, clamp(testUV + vec2(0.0, texel), 0.0, 1.0), 0.0).g;'],
  ] },
  'fill loop': { edits: [
    ['vec2 dispDir = dispLen > 0.001 ? displacement / dispLen : vec2(1.0, 0.0);', 'for (int k = 0; k < int(ceil(trailMask)); k++) { vec2 dispDir = dispLen > 0.001 ? displacement / dispLen : vec2(1.0, 0.0);'],
    ['fillMix = trailMask * 0.85;', 'fillMix = trailMask * 0.85; }'],
  ] },
  'fill loop lod': { edits: [
    ['vec2 dispDir = dispLen > 0.001 ? displacement / dispLen : vec2(1.0, 0.0);', 'for (int k = 0; k < int(ceil(trailMask)); k++) { vec2 dispDir = dispLen > 0.001 ? displacement / dispLen : vec2(1.0, 0.0);'],
    ['fillMix = trailMask * 0.85;', 'fillMix = trailMask * 0.85; }'],
    ['SHADE float fd1 = DEPTH_AT(clamp(s1, 0.0, 1.0));', 'SHADE float fd1 = textureLod(u_depth, clamp(s1, 0.0, 1.0), 0.0).g;'],
    ['SHADE float fd2 = DEPTH_AT(clamp(s2, 0.0, 1.0));', 'SHADE float fd2 = textureLod(u_depth, clamp(s2, 0.0, 1.0), 0.0).g;'],
    ['SHADE float fd3 = DEPTH_AT(clamp(s3, 0.0, 1.0));', 'SHADE float fd3 = textureLod(u_depth, clamp(s3, 0.0, 1.0), 0.0).g;'],
    ['SHADE float fd4 = DEPTH_AT(clamp(s4, 0.0, 1.0));', 'SHADE float fd4 = textureLod(u_depth, clamp(s4, 0.0, 1.0), 0.0).g;'],
    ['SHADE vec4 fc1 = COLOR_AT(', 'SHADE vec4 fc1 = textureLod(u_color, '],
    ['SHADE vec4 fc2 = COLOR_AT(', 'SHADE vec4 fc2 = textureLod(u_color, '],
    ['SHADE vec4 fc3 = COLOR_AT(', 'SHADE vec4 fc3 = textureLod(u_color, '],
    ['SHADE vec4 fc4 = COLOR_AT(', 'SHADE vec4 fc4 = textureLod(u_color, '],
    ['* displacement, 0.001, 0.999)) * w1;', '* displacement, 0.001, 0.999), 0.0) * w1;'],
    ['* displacement, 0.001, 0.999)) * w2;', '* displacement, 0.001, 0.999), 0.0) * w2;'],
    ['* displacement, 0.001, 0.999)) * w3;', '* displacement, 0.001, 0.999), 0.0) * w3;'],
    ['* displacement, 0.001, 0.999)) * w4;', '* displacement, 0.001, 0.999), 0.0) * w4;'],
    ['SHADE float dL = DEPTH_AT(clamp(testUV - vec2(texel, 0.0), 0.0, 1.0));', 'SHADE float dL = textureLod(u_depth, clamp(testUV - vec2(texel, 0.0), 0.0, 1.0), 0.0).g;'],
    ['SHADE float dR = DEPTH_AT(clamp(testUV + vec2(texel, 0.0), 0.0, 1.0));', 'SHADE float dR = textureLod(u_depth, clamp(testUV + vec2(texel, 0.0), 0.0, 1.0), 0.0).g;'],
    ['SHADE float dU = DEPTH_AT(clamp(testUV - vec2(0.0, texel), 0.0, 1.0));', 'SHADE float dU = textureLod(u_depth, clamp(testUV - vec2(0.0, texel), 0.0, 1.0), 0.0).g;'],
    ['SHADE float dD = DEPTH_AT(clamp(testUV + vec2(0.0, texel), 0.0, 1.0));', 'SHADE float dD = textureLod(u_depth, clamp(testUV + vec2(0.0, texel), 0.0, 1.0), 0.0).g;'],
  ] },
  'coarse': { art: { coarse: true } },
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
      window.__plairArt.set({ shader: { mediumpShading: true, ...${JSON.stringify(stage.shader || {})} }, stepScale: 1, coarse: false, ...${JSON.stringify(stage.art || {})} })
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
  await p.evaluate(`(() => { window.__plairArt.set({ coarse: null }); const proto = WebGL2RenderingContext.prototype; if (proto.__originalShaderSource) proto.shaderSource = proto.__originalShaderSource; return 1 })()`)
}
