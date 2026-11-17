const FILL_LOOP = [
    ['vec2 s1 = uv + perpDir * spread;', 'SHADE vec4 fsum = vec4(0.0); SHADE float wsum = 0.0; int fillTaps = u_steps > 0.0 ? 4 : 0; for (int k = 0; k < fillTaps; k++) { vec2 s = k < 2 ? uv + perpDir * (k == 0 ? spread : -spread) : uv - dispDir * (spread * float(k - 1)); SHADE float fd = DEPTH_AT(clamp(s, 0.0, 1.0)); SHADE float w = max(0.01, pow(1.0 - fd, 2.0)); fsum += COLOR_AT(clamp(s - (fd - 0.5) * displacement, 0.001, 0.999)) * w; wsum += w; } /*'],
    ['fillColor = (fc1 + fc2 + fc3 + fc4) / (w1 + w2 + w3 + w4);', '*/ fillColor = fsum / wsum;'],
]
const ES3_ONLY = (es3, es1) => `\n#if __VERSION__ >= 300\n${es3}\n#else\n${es1}\n#endif\n`
const LIGHTS_DYNAMIC = [['for (int i = 0; i < 4; i++) {', ES3_ONLY('for (int i = 0; i < (u_light > 0.0 ? 4 : 0); i++) {', 'for (int i = 0; i < 4; i++) {')]]
const MARCH_DYNAMIC = [['for (int i = 0; i < LINEAR_STEPS; i++) {', 'for (int i = 0; i < int(u_steps - skipped); i++) {']]

const PER_COVER = [
  ['uniform float u_steps;', 'uniform float u_steps; uniform vec2 u_disp; uniform vec2 u_half_disp; uniform float u_disp_len; uniform float u_disp_gate; uniform vec2 u_disp_dir; uniform float u_spread; uniform float u_uv_scale; uniform float u_layer_step;'],
  ['uniform float u_pulse;', 'uniform float u_pulse; uniform float u_energy; uniform vec3 u_light_amp[4]; uniform vec2 u_light_pos[4]; uniform vec4 u_rect_a;'],
  ['vec2 displacement = u_gyro * u_intensity;', 'vec2 displacement = u_disp;'],
  ['vec2 uv = (texCoord - 0.5) / finalZoom + 0.5;', 'vec2 uv = (texCoord - 0.5) * u_uv_scale + 0.5;'],
  ['float dispLen = length(displacement);', 'float dispLen = u_disp_len;'],
  ['float layerStep = 1.0 / u_steps;', 'float layerStep = u_layer_step;'],
  ['SHADE float dispGate = smoothstep(0.001, 0.015, dispLen);', 'SHADE float dispGate = u_disp_gate;'],
  ['vec2 dispDir = dispLen > 0.001 ? displacement / dispLen : vec2(1.0, 0.0);', 'vec2 dispDir = u_disp_dir;'],
  ['float spread = max(dispLen * 3.0, 0.015);', 'float spread = u_spread;'],
  ['vec2 a = clamp(uv - 0.5 * displacement, 0.0, 1.0) * size - 0.5;', 'vec2 a = clamp(uv - u_half_disp, 0.0, 1.0) * size - 0.5;'],
  ['vec2 b = clamp(uv + 0.5 * displacement, 0.0, 1.0) * size - 0.5;', 'vec2 b = clamp(uv + u_half_disp, 0.0, 1.0) * size - 0.5;'],
  ['SHADE vec2 toLight = (source.xy - screenUV) * vec2(u_aspect, 1.0);', 'SHADE vec2 toLight = u_light_pos[i] - screenUV;'],
  ['SHADE vec3 light = u_light_colors[i] * (source.w * 0.6 / (1.0 + dist2 * 1.5));', 'SHADE vec3 light = u_light_amp[i] / (1.0 + dist2 * 1.5);'],
  ['SHADE float energy = u_light * (1.0 + u_pulse * 0.35 + u_kick * 0.6);', 'SHADE float energy = u_energy;'],
  ['lightTerms(hitUV, u_rect.xy + texCoord * u_rect.zw, reliefTerm, extraTerm);', 'lightTerms(hitUV, u_rect_a.xy + texCoord * u_rect_a.zw, reliefTerm, extraTerm);'],
]
const PER_COVER_DERIVE = `(u) => {
  const out = {}
  const g = u.u_gyro, it = u.u_intensity && u.u_intensity[0], zoom = u.u_zoom ? u.u_zoom[0] : 1
  if (g && it !== undefined) {
    const dx = g[0] * it, dy = g[1] * it, len = Math.hypot(dx, dy)
    const t = Math.min(1, Math.max(0, (len - 0.001) / (0.015 - 0.001)))
    out.u_disp = ['2f', dx, dy]
    out.u_half_disp = ['2f', 0.5 * dx, 0.5 * dy]
    out.u_disp_len = ['1f', len]
    out.u_disp_gate = ['1f', t * t * (3 - 2 * t)]
    out.u_disp_dir = len > 0.001 ? ['2f', dx / len, dy / len] : ['2f', 1, 0]
    out.u_spread = ['1f', Math.max(len * 3, 0.015)]
    out.u_uv_scale = ['1f', 1 / (zoom * (1 + Math.abs(it) * 0.8))]
  }
  if (u.u_steps) out.u_layer_step = ['1f', 1 / u.u_steps[0]]
  if (u.u_light) out.u_energy = ['1f', u.u_light[0] * (1 + (u.u_pulse ? u.u_pulse[0] : 0) * 0.35 + (u.u_kick ? u.u_kick[0] : 0) * 0.6)]
  if (u.u_lights && u.u_light_colors && u.u_aspect) {
    const a = u.u_aspect[0], L = u.u_lights[0], C = u.u_light_colors[0], amp = [], pos = []
    for (let i = 0; i < 4; i++) {
      for (let c = 0; c < 3; c++) amp.push(C[i * 3 + c] * L[i * 4 + 3] * 0.6)
      pos.push(L[i * 4] * a, L[i * 4 + 1])
    }
    out.u_light_amp = ['3fv', new Float32Array(amp)]
    out.u_light_pos = ['2fv', new Float32Array(pos)]
  }
  if (u.u_rect && u.u_aspect) { const r = u.u_rect, a = u.u_aspect[0]; out.u_rect_a = ['4f', r[0] * a, r[1], r[2] * a, r[3]] }
  return out
}`

export const STAGES = {
  'as is': {},
  'lights off': { light: { off: true } },
  'no edge fill': { edits: [['SHADE float dispGate = smoothstep(', 'return pomColor; SHADE float dispGate = smoothstep(']] },
  'no depth bound': { edits: [['float bound = marchBound(uv, displacement);', 'float bound = 2.0;']] },
  'no parallax': { edits: [['return parallax(uv, displacement, hitUV, hitDepth, fillColor, fillMix);', 'hitUV = uv; hitDepth = 0.0; fillColor = vec4(0.0); fillMix = 0.0; return texture2D(u_color, uv);']] },
  'no parallax no lights': { edits: [['return parallax(uv, displacement, hitUV, hitDepth, fillColor, fillMix);', 'hitUV = uv; hitDepth = 0.0; fillColor = vec4(0.0); fillMix = 0.0; return texture2D(u_color, uv);']], light: { off: true } },
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
  'no rim': { edits: [['rim += light * max(dot(facing, L.xy), 0.0);', 'rim += 0.0 * light * max(dot(facing, L.xy), 0.0);']] },
  'no relief': { edits: [['relief += light * (dot(n, L) - L.z);', 'relief += 0.0 * light * (dot(n, L) - L.z);']] },
  'show edge': { edits: [['return clamp(lit, 0.0, 1.0);', 'return vec3(edge);']] },
  'lights per cover': { edits: [['return skylight(color, hitUV, u_rect.xy + texCoord * u_rect.zw);', 'return skylight(color, hitUV, u_rect.xy + 0.5 * u_rect.zw);']] },
  'bound fixed sizes': { edits: [['vec2 size = vec2(textureSize(u_depth, 0));', 'vec2 size = vec2(1024.0);'], ['ivec2 last = textureSize(u_depth_bound, lod) - 1;', 'ivec2 last = ivec2(1023 >> lod);']] },
  'light 1 of 4': { edits: [['for (int i = 0; i < 4; i++) {', 'for (int i = 0; i < 1; i++) {']] },
  'light 2 of 4': { edits: [['for (int i = 0; i < 4; i++) {', 'for (int i = 0; i < 2; i++) {']] },
  'light no specular': { edits: [['glint += light * max(shine(max(dot(n, H), 0.0)) - shine(H.z), 0.0);', '']] },
  'light no rim': { edits: [['rim += light * max(dot(facing, L.xy), 0.0);', '']] },
  'lights H const': { edits: [['SHADE vec3 H = vec3(L.xy, L.z + 1.0) *', 'SHADE vec3 H = vec3(0.2127, -0.2659, 0.9402); //']] },
  'lights falloff const': { edits: [['SHADE vec3 light = u_light_colors[i] * (source.w *', 'SHADE vec3 light = u_light_colors[i] * (source.w * 0.4); //']] },
  'lights L exact only': { edits: [
    ['SHADE vec3 light = u_light_colors[i] * (source.w *', 'SHADE vec3 light = u_light_colors[i] * (source.w * 0.4); //'],
    ['SHADE vec3 H = vec3(L.xy, L.z + 1.0) *', 'SHADE vec3 H = vec3(0.2127, -0.2659, 0.9402); //'],
  ] },
  'lights L unnormalised': { edits: [['SHADE vec3 L = vec3(toLight,', 'SHADE vec3 L = vec3(toLight, 0.45) * 1.9; //']] },
  'lights uniform': { edits: [
    ['SHADE vec2 toLight = (source.xy - screenUV) * vec2(u_aspect, 1.0);', ''],
    ['SHADE float dist2 = dot(toLight, toLight);', ''],
    ['SHADE vec3 L = vec3(toLight,', 'SHADE vec3 L = u_light_colors[i].yzx; //'],
    ['SHADE vec3 light = u_light_colors[i] * (source.w *', 'SHADE vec3 light = u_light_colors[i] * (source.w * 0.4); //'],
    ['SHADE vec3 H = vec3(L.xy, L.z + 1.0) *', 'SHADE vec3 H = u_light_colors[i].zxy; //'],
    ['glint += light * max(shine(max(dot(n, H), 0.0)) - shine(H.z), 0.0);', 'glint += light * max(shine(max(dot(n, H), 0.0)) - source.z, 0.0);'],
  ] },
  'lights linear': { edits: [
    ['SHADE vec2 toLight = (source.xy - screenUV) * vec2(u_aspect, 1.0);', ''],
    ['SHADE float dist2 = dot(toLight, toLight);', ''],
    ['SHADE vec3 L = vec3(toLight,', 'SHADE vec3 L = u_light_colors[i].yzx + u_light_colors[i] * screenUV.x + u_light_colors[i].zxy * screenUV.y; //'],
    ['SHADE vec3 light = u_light_colors[i] * (source.w *', 'SHADE vec3 light = u_light_colors[i] * (source.w * 0.4 + source.x * screenUV.x + source.y * screenUV.y); //'],
    ['SHADE vec3 H = vec3(L.xy, L.z + 1.0) *', 'SHADE vec3 H = u_light_colors[i].zxy + u_light_colors[i].yzx * screenUV.x + u_light_colors[i] * screenUV.y; //'],
    ['glint += light * max(shine(max(dot(n, H), 0.0)) - shine(H.z), 0.0);', 'glint += light * max(shine(max(dot(n, H), 0.0)) - (source.z + source.x * screenUV.x), 0.0);'],
  ] },
  'lights const': { edits: [
    ['SHADE vec2 toLight = (source.xy - screenUV) * vec2(u_aspect, 1.0);', ''],
    ['SHADE float dist2 = dot(toLight, toLight);', ''],
    ['SHADE vec3 L = vec3(toLight,', 'SHADE vec3 L = vec3(0.4, -0.5, 0.768); //'],
    ['SHADE vec3 light = u_light_colors[i] * (source.w *', 'SHADE vec3 light = u_light_colors[i] * (source.w * 0.4); //'],
    ['SHADE vec3 H = vec3(L.xy, L.z + 1.0) *', 'SHADE vec3 H = vec3(0.2127, -0.2659, 0.9402); //'],
  ] },
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
  'fill taps uniform loop': { edits: FILL_LOOP },
  'fill loop + lights dyn': { edits: [...FILL_LOOP, ...LIGHTS_DYNAMIC] },
  'fill loop + march dyn': { edits: [...FILL_LOOP, ...MARCH_DYNAMIC] },
  'fill loop + both dyn': { edits: [...FILL_LOOP, ...LIGHTS_DYNAMIC, ...MARCH_DYNAMIC] },
  'per cover uniforms': { edits: PER_COVER, derive: PER_COVER_DERIVE },
  'fill loop + per cover': { edits: [...FILL_LOOP, ...PER_COVER], derive: PER_COVER_DERIVE },
  'fill taps const loop': { edits: [
    ['vec2 s1 = uv + perpDir * spread;', 'SHADE vec4 fsum = vec4(0.0); SHADE float wsum = 0.0; for (int k = 0; k < 4; k++) { vec2 s = k < 2 ? uv + perpDir * (k == 0 ? spread : -spread) : uv - dispDir * (spread * float(k - 1)); SHADE float fd = DEPTH_AT(clamp(s, 0.0, 1.0)); SHADE float w = max(0.01, pow(1.0 - fd, 2.0)); fsum += COLOR_AT(clamp(s - (fd - 0.5) * displacement, 0.001, 0.999)) * w; wsum += w; } /*'],
    ['fillColor = (fc1 + fc2 + fc3 + fc4) / (w1 + w2 + w3 + w4);', '*/ fillColor = fsum / wsum;'],
  ] },
  'coarse': { art: { coarse: true } },
  'steps x0.5': { art: { stepScale: 0.5 } },
  'steps x0.33': { art: { stepScale: 0.33 } },
  ...lightCountStages(),
  ...coarseSplitStages(),
}

function lightCountStages() {
  const fixed = count => `(u) => ({
    u_light_pos: ['3fv', new Float32Array([0.1, 0.2, 1, 0.4, 0.3, 1, 0.15, 0.7, 1, 0.4, 0.85, 1])],
    u_light_amp: ['3fv', new Float32Array([0.5, 0.45, 0.4, 0.4, 0.5, 0.45, 0.45, 0.4, 0.5, 0.4, 0.4, 0.4])],
    u_lcount: ['1i', ${count}],
  })`
  const bound = [
    ['uniform float u_light;', 'uniform float u_light; uniform int u_lcount;'],
    ['for (int i = 0; i < 4; i++) {', ES3_ONLY('for (int i = 0; i < u_lcount; i++) {', 'for (int i = 0; i < 4; i++) {')],
  ]
  const stages = {}
  for (const [suffix, art] of [['', {}], [' coarse', { coarse: true }]]) {
    stages['L4 const' + suffix] = { derive: fixed(4), art }
    for (const n of [4, 2, 1]) stages[`L${n} ubound${suffix}`] = { edits: bound, derive: fixed(n), art }
    stages['L off' + suffix] = { light: { off: true }, art }
  }
  return stages
}

export async function installShaderEdits(p) {
  return p.evaluate(`(() => {
    const proto = WebGL2RenderingContext.prototype
    if (!proto.__originalShaderSource) proto.__originalShaderSource = proto.shaderSource
    window.__shaderEdits = []
    window.__shaderEditHits = []
    window.__derive = null
    proto.shaderSource = function (shader, source) {
      let edited = source
      window.__shaderEdits.forEach(([from, to], index) => {
        if (!edited.includes(from)) return
        edited = edited.split(from).join(to)
        window.__shaderEditHits[index] = (window.__shaderEditHits[index] || 0) + 1
      })
      return proto.__originalShaderSource.call(this, shader, edited)
    }
    if (!proto.__originals) {
      const names = ['getUniformLocation', 'useProgram', 'drawArrays', 'uniform1i', 'uniform1f', 'uniform2f', 'uniform4f', 'uniform3fv', 'uniform4fv']
      proto.__originals = Object.fromEntries(names.map(name => [name, proto[name]]))
      const o = proto.__originals
      const locationNames = new WeakMap()
      const standIns = new WeakSet()
      const coverInputs = new Set(['u_gyro', 'u_intensity', 'u_zoom', 'u_steps', 'u_rect', 'u_aspect', 'u_light', 'u_lights', 'u_light_colors', 'u_kick', 'u_pulse'])
      proto.getUniformLocation = function (program, name) {
        let location = o.getUniformLocation.call(this, program, name)
        const base = name.replace('[0]', '')
        if (!location && coverInputs.has(base)) {
          location = Object.create(WebGLUniformLocation.prototype)
          standIns.add(location)
        }
        if (location) locationNames.set(location, { program, name: base })
        return location
      }
      proto.useProgram = function (program) { this.__program = program; return o.useProgram.call(this, program) }
      const record = method => function (location, ...values) {
        const owner = location && locationNames.get(location)
        if (owner) (owner.program.__values ||= {})[owner.name] = values.map(v => (ArrayBuffer.isView(v) ? Array.from(v) : v))
        if (location && standIns.has(location)) return undefined
        return o[method].call(this, location, ...values)
      }
      for (const method of ['uniform1i', 'uniform1f', 'uniform2f', 'uniform4f', 'uniform3fv', 'uniform4fv']) proto[method] = record(method)
      proto.drawArrays = function (...args) {
        const program = this.__program
        if (window.__derive && program && program.__values) {
          const derived = window.__derive(program.__values)
          const seen = (window.__deriveSeen ||= [])
          seen.push({ set: Object.keys(program.__values), derived: Object.fromEntries(Object.entries(derived).map(([k, v]) => [k, v.slice(1).map(x => (ArrayBuffer.isView(x) ? Array.from(x) : x))])) })
          if (seen.length > 5) seen.shift()
          const cache = (program.__derivedLocations ||= {})
          for (const [name, [type, ...values]] of Object.entries(derived)) {
            if (!(name in cache)) cache[name] = o.getUniformLocation.call(this, program, name)
            if (cache[name]) this['uniform' + type](cache[name], ...values)
          }
        }
        return o.drawArrays.apply(this, args)
      }
    }
    return 1
  })()`)
}

export async function applyStage(p, stage) {
  const result = await p.evaluate(`(() => {
    window.__shaderEdits = ${JSON.stringify(stage.edits || [])}
    window.__derive = ${stage.derive || 'null'}
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
  await p.evaluate(`(() => { window.__plairArt.set({ coarse: null }); const proto = WebGL2RenderingContext.prototype; if (proto.__originalShaderSource) proto.shaderSource = proto.__originalShaderSource; if (proto.__originals) { Object.assign(proto, proto.__originals); delete proto.__originals } window.__derive = null; return 1 })()`)
}

function coarseSplitStages() {
  const noMarch = ['return parallax(uv, u_disp, hitUV, hitDepth, fillColor, fillMix);', 'hitUV = uv; hitDepth = 0.0; fillColor = vec4(0.0); fillMix = 0.0; return COLOR_AT(uv);']
  const noFill = ['SHADE float dispGate = u_disp_gate;', 'SHADE float dispGate = 0.0;']
  const composeOneRead = ['vec2 hitUV = uv + texture(u_coarse_hit, at).xy;', 'o_color = texture(u_color, uv); return; vec2 hitUV = uv + texture(u_coarse_hit, at).xy;']
  const coarse = { coarse: true }
  return {
    'C as is': { art: coarse },
    'C no march': { art: coarse, edits: [noMarch] },
    'C no fill': { art: coarse, edits: [noFill] },
    'C lights off': { art: coarse, light: { off: true } },
    'C bare': { art: coarse, edits: [noMarch], light: { off: true } },
    'C bare 1 read': { art: coarse, edits: [noMarch, composeOneRead], light: { off: true } },
    'flat covers': { art: { lit: false } },
  }
}
