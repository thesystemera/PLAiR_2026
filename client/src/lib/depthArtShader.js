import { MAX_LIGHTS } from './lightProbe'
import { logger } from './logger'

// ═══════════════════════════════════════════════════════════════════════════════
// DEPTH ARTWORK SHADER - parallax occlusion mapping + lights from the background
// ═══════════════════════════════════════════════════════════════════════════════
//
// One shader for every depth-mapped image (Now Playing, catalog tiles, shoutout
// profile pictures). 4 stages:
//   1. POM ray march     → proper foreground-over-background occlusion
//   2. Trail detection   → identifies disoccluded streaks behind shifted foreground
//   3. Background fill   → replaces detected trails with nearby background color
//   4. Lighting          → up to 4 light sources found in the background video
//                          (lib/lightProbe.js) light the baked normal map
//                          (server/services/normal_map_service.py, deepMirror method):
//                          relief shading, specular glints and a rim that flash on kicks
//                          and breathes with the beat
//
// HOW TRAIL DETECTION WORKS:
//   trailMask = edgeness × foregroundness × displacementGate
//
//   edgeness:       depth gradient at the POM hit position (high at silhouette edges)
//   foregroundness:  original depth at the screen pixel BEFORE displacement
//                    - High = was foreground → foreground shifted away → trail
//                    - Low  = was background → POM correctly placed foreground → occlusion
//   displacementGate: suppresses correction when barely any parallax is happening
//
// QUICK TUNING GUIDE:
//   Seeing trails?         → Lower FG_DEPTH_SOFT/HARD, lower EDGE_SOFT/HARD
//   Foreground edges soft? → Raise FG_DEPTH_SOFT/HARD (less aggressive detection)
//   Fill looks wrong?      → Increase FILL_SPREAD_MULT, increase FILL_DEPTH_POWER
//   Effect too subtle?     → Raise intensity prop in NowPlaying.jsx (currently 0.05)
//   Lighting too strong?   → Lower LIGHT.STRENGTH (all of it) or LIGHT.KICK (the flash on kicks)
// ═══════════════════════════════════════════════════════════════════════════════
export const POM = {

  // --- POM Ray March Quality ---
  LINEAR_STEPS:     24,     // Ray march steps through depth field (16-48). More = sharper edges, more GPU.

  // --- Auto Zoom ---
  ZOOM_FACTOR:      0.8,    // Zoom multiplier (× intensity) to hide edge reveal. 0.5 = subtle, 1.2 = aggressive crop.

  // --- Trail Detection: Edge Sensitivity ---
  EDGE_RADIUS:      0.003,  // UV offset for depth gradient sampling. Larger = detects wider/softer edges.
  EDGE_SOFT:        0.1,    // Gradient below this → smooth surface, no edge. Lower = more sensitive to shallow edges.
  EDGE_HARD:        0.4,    // Gradient above this → full edge detected. Lower = flags more area as edge.

  // --- Trail Detection: Foreground Threshold (THE KEY KNOBS) ---
  FG_DEPTH_SOFT:    0.3,    // Base depth below this → definitely background → no trail correction.
  FG_DEPTH_HARD:    0.6,    // Base depth above this → definitely foreground → max trail correction.
                            //   ↑ Lower both to catch more trails (but may soften mid-depth edges).
                            //   ↑ Raise both if foreground occlusion is getting wrongly softened.

  // --- Trail Detection: Displacement Gate ---
  DISP_GATE_MIN:    0.001,  // Below this displacement magnitude, zero trail correction.
  DISP_GATE_MAX:    0.015,  // Above this, full trail correction. Linear ramp between.

  // --- Background Fill: Search ---
  FILL_SPREAD_MULT: 3.0,    // Search radius = displacement × this. Higher = finds background further away.
  FILL_SPREAD_MIN:  0.015,  // Minimum search radius even at tiny displacements.

  // --- Background Fill: Depth Weighting ---
  FILL_DEPTH_POWER: 2.0,    // Exponent for inverse-depth weighting. Higher = more aggressively prefer background samples.
                            //   1.0 = linear preference, 2.0 = squared, 3.0 = very aggressive.

  // --- Final Blend ---
  FILL_STRENGTH:    0.85,   // Max blend toward fill in trail regions. 1.0 = fully replace trails, 0.0 = keep all trails.
}

const LIGHT = {

  // --- Light sources found in the background video (lib/lightProbe.js) ---
  HEIGHT:           0.45,   // How far in front of the screen the lights hang. Lower = grazing light, deeper relief.
  FALLOFF:          1.5,    // How fast a light fades with screen distance from the image.
  STRENGTH:         0.6,    // Overall light level per unit of a source's contrast with the scene.

  // --- Relief: surfaces facing a light brighten, facing away darken ---
  RELIEF:           0.5,

  // --- Specular glint where a surface reflects a light toward the viewer ---
  SPECULAR:         0.35,
  SHININESS:        24.0,

  // --- Rim: edges facing a light pick up its colour ---
  RIM:              0.35,
  RIM_SOFT:         0.25,   // How far a normal leans (length of normal.xy) before the rim starts.
  RIM_HARD:         0.85,   // Lean where the rim is full.

  // --- Music ---
  PULSE:            0.35,   // Extra light at the top of the beat's breathing.
  KICK:             0.6,    // Extra rim + relief on a kick flash.
}

const G = v => String(v).includes('.') ? String(v) : String(v) + '.0'

const VERTEX_100 = `
  attribute vec2 a_position;
  attribute vec2 a_texCoord;
  varying vec2 v_texCoord;

  void main() {
    gl_Position = vec4(a_position, 0.0, 1.0);
    v_texCoord = a_texCoord;
  }
`

const VERTEX_300 = `#version 300 es
  in vec2 a_position;
  in vec2 a_texCoord;
  out vec2 v_texCoord;

  void main() {
    gl_Position = vec4(a_position, 0.0, 1.0);
    v_texCoord = a_texCoord;
  }
`

const FLAT_FRAGMENT = `
  precision mediump float;
  uniform sampler2D u_color;
  varying vec2 v_texCoord;

  void main() {
    gl_FragColor = texture2D(u_color, v_texCoord);
  }
`

const FILL_TAPS = 4

const PARALLAX_UNIFORMS = `
  uniform sampler2D u_color;
  uniform sampler2D u_depth;
  uniform vec2 u_disp;
  uniform vec2 u_half_disp;
  uniform float u_disp_gate;
  uniform vec2 u_disp_dir;
  uniform float u_spread;
  uniform float u_uv_scale;
  uniform float u_steps;
  uniform float u_layer_step;
  uniform int u_fill_taps;
`

const LIGHT_UNIFORMS = `
  uniform sampler2D u_normal;
  uniform vec3 u_light_pos[${MAX_LIGHTS}];
  uniform vec3 u_light_amp[${MAX_LIGHTS}];
  uniform vec4 u_rect;
  uniform float u_light;
  uniform float u_energy;
`

const MARCH_BOUND = `
  #ifdef MARCH_BOUND
  uniform highp sampler2D u_depth_bound;
  uniform float u_bound_levels;
  uniform vec2 u_depth_size;
  uniform vec2 u_bound_size;

  float marchBound(vec2 uv, vec2 displacement) {
    if (u_bound_levels <= 0.0) return 2.0;
    vec2 size = u_depth_size;
    vec2 a = clamp(uv - u_half_disp, 0.0, 1.0) * size - 0.5;
    vec2 b = clamp(uv + u_half_disp, 0.0, 1.0) * size - 0.5;
    vec2 lo = clamp(floor(min(a, b)), vec2(0.0), size - 1.0);
    vec2 hi = clamp(floor(max(a, b)) + 1.0, vec2(0.0), size - 1.0);
    float span = max(hi.x - lo.x, hi.y - lo.y);
    float level = span <= 1.0 ? 0.0 : ceil(log2(span));
    float block = exp2(level);
    if (floor(hi.x / block) - floor(lo.x / block) > 1.0 || floor(hi.y / block) - floor(lo.y / block) > 1.0) {
      level += 1.0;
      block *= 2.0;
    }
    if (level >= u_bound_levels) return 2.0;
    int lod = int(level);
    ivec2 last = max(ivec2(u_bound_size) >> lod, ivec2(1)) - 1;
    ivec2 c0 = min(ivec2(floor(lo / block)), last);
    ivec2 c1 = min(ivec2(floor(hi / block)), last);
    float top = max(
      max(texelFetch(u_depth_bound, c0, lod).r, texelFetch(u_depth_bound, ivec2(c1.x, c0.y), lod).r),
      max(texelFetch(u_depth_bound, ivec2(c0.x, c1.y), lod).r, texelFetch(u_depth_bound, c1, lod).r)
    );
    return top + 1.0 / 512.0;
  }
  #else
  float marchBound(vec2 uv, vec2 displacement) {
    return 2.0;
  }
  #endif
`

const SHADE_PRECISION = `
  #ifndef SHADE
  #ifdef MEDIUMP_SHADING
  #define SHADE mediump
  #else
  #define SHADE highp
  #endif
  #endif
`

const SAMPLES = `
  #define DEPTH_AT(uv) texture2D(u_depth, uv).g
  #define COLOR_AT(uv) texture2D(u_color, uv)
  #if __VERSION__ >= 300
  #define FILL_TAP_COUNT u_fill_taps
  #else
  #define FILL_TAP_COUNT ${FILL_TAPS}
  #endif
`

const PARALLAX = `
  ${SHADE_PRECISION}
  ${MARCH_BOUND}
  ${SAMPLES}

  vec4 parallax(vec2 uv, vec2 displacement, out vec2 hitUV, out float hitDepth, out vec4 fillColor, out float fillMix) {
    hitDepth = 0.0;
    fillColor = vec4(0.0);
    fillMix = 0.0;

    const int LINEAR_STEPS = ${POM.LINEAR_STEPS};
    float layerStep = u_layer_step;

    float testDepth = 1.0;
    float prevTestDepth = 1.0;
    vec2 testUV;
    float sampledDepth;
    bool hit = false;
    float bound = marchBound(uv, displacement);
    float prevSampled = min(bound, 1.0);
    float skipped = max(0.0, ceil((1.0 - bound) * u_steps));
    if (skipped > 0.0) {
      prevTestDepth = 1.0 - (skipped - 1.0) * layerStep;
      testDepth = 1.0 - skipped * layerStep;
    }

    for (int i = 0; i < LINEAR_STEPS; i++) {
      if (float(i) + skipped >= u_steps) break;
      testUV = uv - (testDepth - 0.5) * displacement;
      if (testDepth > bound) {
        prevTestDepth = testDepth;
        testDepth -= layerStep;
        continue;
      }
      sampledDepth = DEPTH_AT(clamp(testUV, 0.0, 1.0));

      if (sampledDepth >= testDepth) {
        hit = true;
        break;
      }

      prevSampled = sampledDepth;
      prevTestDepth = testDepth;
      testDepth -= layerStep;
    }

    if (hit) {
      float aboveDepth = prevTestDepth;
      float belowDepth = testDepth;
      float aboveGap = prevTestDepth - prevSampled;
      float belowGap = testDepth - sampledDepth;
      float midDepth = mix(aboveDepth, belowDepth, aboveGap / max(aboveGap - belowGap, 1e-5));
      float midSample = DEPTH_AT(clamp(uv - (midDepth - 0.5) * displacement, 0.0, 1.0));
      if (midSample >= midDepth) {
        belowDepth = midDepth;
        belowGap = midDepth - midSample;
      } else {
        aboveDepth = midDepth;
        aboveGap = midDepth - midSample;
      }
      hitDepth = mix(aboveDepth, belowDepth, aboveGap / max(aboveGap - belowGap, 1e-5));
      testUV = uv - (hitDepth - 0.5) * displacement;
    } else {
      testUV = uv + 0.5 * displacement;
    }

    hitUV = testUV;
    SHADE vec4 pomColor = COLOR_AT(clamp(testUV, 0.001, 0.999));

    SHADE float dispGate = u_disp_gate;
    if (dispGate <= 0.0) return pomColor;

    SHADE float baseDepth = DEPTH_AT(clamp(uv, 0.0, 1.0));
    SHADE float foregroundness = smoothstep(${G(POM.FG_DEPTH_SOFT)}, ${G(POM.FG_DEPTH_HARD)}, baseDepth);
    if (foregroundness <= 0.0) return pomColor;

    float texel = ${G(POM.EDGE_RADIUS)};
    SHADE float dL = DEPTH_AT(clamp(testUV - vec2(texel, 0.0), 0.0, 1.0));
    SHADE float dR = DEPTH_AT(clamp(testUV + vec2(texel, 0.0), 0.0, 1.0));
    SHADE float dU = DEPTH_AT(clamp(testUV - vec2(0.0, texel), 0.0, 1.0));
    SHADE float dD = DEPTH_AT(clamp(testUV + vec2(0.0, texel), 0.0, 1.0));
    SHADE float gradient = abs(dR - dL) + abs(dD - dU);

    SHADE float edgeness = smoothstep(${G(POM.EDGE_SOFT)}, ${G(POM.EDGE_HARD)}, gradient);
    SHADE float trailMask = edgeness * foregroundness * dispGate;
    if (trailMask <= 0.0) return pomColor;

    vec2 dispDir = u_disp_dir;
    vec2 perpDir = vec2(-dispDir.y, dispDir.x);
    float spread = u_spread;

    SHADE vec4 fillSum = vec4(0.0);
    SHADE float weightSum = 0.0;
    for (int k = 0; k < FILL_TAP_COUNT; k++) {
      vec2 s = k < 2 ? uv + perpDir * (k == 0 ? spread : -spread) : uv - dispDir * (spread * float(k - 1));
      SHADE float fd = DEPTH_AT(clamp(s, 0.0, 1.0));
      SHADE float w = max(0.01, pow(1.0 - fd, ${G(POM.FILL_DEPTH_POWER)}));
      fillSum += COLOR_AT(clamp(s - (fd - 0.5) * displacement, 0.001, 0.999)) * w;
      weightSum += w;
    }

    fillColor = fillSum / weightSum;
    fillMix = trailMask * ${G(POM.FILL_STRENGTH)};
    return pomColor;
  }


  vec4 parallaxAt(vec2 texCoord, out vec2 hitUV, out float hitDepth, out vec4 fillColor, out float fillMix) {
    vec2 uv = (texCoord - 0.5) * u_uv_scale + 0.5;
    return parallax(uv, u_disp, hitUV, hitDepth, fillColor, fillMix);
  }

  vec4 parallaxColorAt(vec2 texCoord, out vec2 hitUV) {
    float hitDepth;
    vec4 fillColor;
    float fillMix;
    vec4 color = parallaxAt(texCoord, hitUV, hitDepth, fillColor, fillMix);
    return mix(color, fillColor, fillMix);
  }
`

function powerFunction(exponent) {
  if (!Number.isInteger(exponent) || exponent < 1) return `SHADE float shine(SHADE float x) { return pow(x, ${G(exponent)}); }`
  const lines = []
  let result = null
  let square = 'x'
  for (let bit = exponent, level = 0; bit > 0; bit >>= 1, level++) {
    if (level > 0) {
      lines.push(`SHADE float x${level} = ${square} * ${square};`)
      square = `x${level}`
    }
    if (bit & 1) result = result ? `${result} * ${square}` : square
  }
  return `SHADE float shine(SHADE float x) { ${lines.join(' ')} return ${result}; }`
}

const LIGHT_APPLY = `
  SHADE vec3 applyLight(SHADE vec3 color, SHADE vec3 reliefTerm, SHADE vec3 extraTerm) {
    SHADE vec3 lit = color * (1.0 + reliefTerm);
    lit += extraTerm * (1.0 - lit);
    return clamp(lit, 0.0, 1.0);
  }
`

const SKYLIGHT = `
  #define NORMAL_AT(uv) texture2D(u_normal, uv)
  ${SHADE_PRECISION}
  ${powerFunction(LIGHT.SHININESS)}

  void lightTerms(vec2 hitUV, vec2 screenUV, out SHADE vec3 reliefTerm, out SHADE vec3 extraTerm) {
#ifdef PACKED_NORMALS
    SHADE vec2 packedNormal = NORMAL_AT(clamp(hitUV, 0.0, 1.0)).rb * 2.0 - 1.0;
    SHADE vec3 n = normalize(vec3(packedNormal, sqrt(max(0.0, 1.0 - dot(packedNormal, packedNormal)))));
#else
    SHADE vec3 n = normalize(NORMAL_AT(clamp(hitUV, 0.0, 1.0)).rgb * 2.0 - 1.0);
#endif
    SHADE float slopeLen = length(n.xy);
    SHADE vec2 facing = slopeLen > 0.0001 ? n.xy / slopeLen : vec2(0.0);

    SHADE vec3 relief = vec3(0.0);
    SHADE vec3 glint = vec3(0.0);
    SHADE vec3 rim = vec3(0.0);
    for (int i = 0; i < ${MAX_LIGHTS}; i++) {
      if (u_light_pos[i].z <= 0.001) continue;
      SHADE vec2 toLight = u_light_pos[i].xy - screenUV;
      SHADE float dist2 = dot(toLight, toLight);
      SHADE vec3 L = vec3(toLight, ${G(LIGHT.HEIGHT)}) * inversesqrt(dist2 + ${G(LIGHT.HEIGHT * LIGHT.HEIGHT)});
      SHADE vec3 light = u_light_amp[i] / (1.0 + dist2 * ${G(LIGHT.FALLOFF)});
      relief += light * (dot(n, L) - L.z);
      SHADE vec3 H = vec3(L.xy, L.z + 1.0) * inversesqrt(2.0 + 2.0 * L.z);
      glint += light * max(shine(max(dot(n, H), 0.0)) - shine(H.z), 0.0);
      rim += light * max(dot(facing, L.xy), 0.0);
    }

    SHADE float energy = u_energy;
    SHADE float edge = smoothstep(${G(LIGHT.RIM_SOFT)}, ${G(LIGHT.RIM_HARD)}, slopeLen);
    reliefTerm = relief * ${G(LIGHT.RELIEF)} * energy;
    extraTerm = (glint * ${G(LIGHT.SPECULAR)} + rim * edge * ${G(LIGHT.RIM)}) * energy;
  }

  ${LIGHT_APPLY}

  vec3 lightAt(vec3 color, vec2 hitUV, vec2 texCoord) {
    SHADE vec3 reliefTerm;
    SHADE vec3 extraTerm;
    lightTerms(hitUV, u_rect.xy + texCoord * u_rect.zw, reliefTerm, extraTerm);
    return applyLight(color, reliefTerm, extraTerm);
  }

  void lightTermsAt(vec2 hitUV, vec2 texCoord, out SHADE vec3 reliefTerm, out SHADE vec3 extraTerm) {
    lightTerms(hitUV, u_rect.xy + texCoord * u_rect.zw, reliefTerm, extraTerm);
  }
`

const FULL_FRAGMENT = `
  precision highp float;
  ${PARALLAX_UNIFORMS}
  ${LIGHT_UNIFORMS}
  varying vec2 v_texCoord;
  ${PARALLAX}
  ${SKYLIGHT}
  void main() {
    vec2 hitUV;
    vec4 color = parallaxColorAt(v_texCoord, hitUV);
    if (u_light > 0.0) color.rgb = lightAt(color.rgb, hitUV, v_texCoord);
    gl_FragColor = color;
  }
`

const FULL_FRAGMENT_300 = `#version 300 es
  precision highp float;
  #define texture2D texture
  #define MARCH_BOUND
  ${PARALLAX_UNIFORMS}
  ${LIGHT_UNIFORMS}
  in vec2 v_texCoord;
  out vec4 o_color;
  ${PARALLAX}
  ${SKYLIGHT}
  void main() {
    vec2 hitUV;
    vec4 color = parallaxColorAt(v_texCoord, hitUV);
    if (u_light > 0.0) color.rgb = lightAt(color.rgb, hitUV, v_texCoord);
    o_color = color;
  }
`

const COARSE_FRAGMENT = `#version 300 es
  precision highp float;
  #define texture2D texture
  #define MARCH_BOUND
  ${PARALLAX_UNIFORMS}
  ${LIGHT_UNIFORMS}
  in vec2 v_texCoord;
  layout(location = 0) out vec4 o_hit;
  layout(location = 1) out vec4 o_relief;
  layout(location = 2) out vec4 o_extra;
  layout(location = 3) out vec4 o_fill;
  ${PARALLAX}
  ${SKYLIGHT}
  void main() {
    vec2 hitUV;
    float hitDepth;
    vec4 fillColor;
    float fillMix;
    parallaxAt(v_texCoord, hitUV, hitDepth, fillColor, fillMix);
    SHADE vec3 reliefTerm = vec3(0.0);
    SHADE vec3 extraTerm = vec3(0.0);
    if (u_light > 0.0) lightTermsAt(hitUV, v_texCoord, reliefTerm, extraTerm);
    vec2 uv = (v_texCoord - 0.5) * u_uv_scale + 0.5;
    o_hit = vec4(hitUV - uv, 0.0, 1.0);
    o_relief = vec4(reliefTerm, fillMix);
    o_extra = vec4(extraTerm, 1.0);
    o_fill = fillColor;
  }
`

const COMPOSE_FRAGMENT = `#version 300 es
  precision highp float;
  uniform sampler2D u_color;
  uniform highp sampler2D u_coarse_hit;
  uniform sampler2D u_coarse_relief;
  uniform sampler2D u_coarse_extra;
  uniform sampler2D u_coarse_fill;
  uniform vec4 u_coarse_rect;
  uniform vec2 u_coarse_size;
  uniform float u_uv_scale;
  in vec2 v_texCoord;
  out vec4 o_color;
  ${SHADE_PRECISION}
  ${LIGHT_APPLY}

  void main() {
    vec2 cells = u_coarse_rect.zw;
    vec2 origin = u_coarse_rect.xy;
    vec2 pos = vec2(v_texCoord.x, 1.0 - v_texCoord.y) * cells - 0.5;
    vec2 at = clamp(origin + pos + 0.5, origin + 0.5, origin + cells - 0.5) / u_coarse_size;
    vec2 uv = (v_texCoord - 0.5) * u_uv_scale + 0.5;
    vec2 hitUV = uv + texture(u_coarse_hit, at).xy;
    vec4 relief = texture(u_coarse_relief, at);
    vec4 extra = texture(u_coarse_extra, at);
    vec4 fill = relief.a > 0.001 ? texture(u_coarse_fill, at) : vec4(0.0);

    vec4 color = texture(u_color, clamp(hitUV, 0.001, 0.999));
    color = mix(color, fill, relief.a);
    color.rgb = applyLight(color.rgb, relief.rgb, extra.rgb);
    o_color = color;
  }
`

const HIT_PACKED = {
  write: `
    vec2 fixedUV = floor(clamp(hitUV, 0.0, 1.0) * 65535.0 + 0.5);
    vec2 high = floor(fixedUV / 256.0);
    o_hit = vec4(high.x, fixedUV.x - high.x * 256.0, high.y, fixedUV.y - high.y * 256.0) / 255.0;`,
  read: `
      vec4 bytes = floor(texture2D(u_cached_hit, cell) * 255.0 + 0.5);
      vec2 hitUV = vec2(bytes.x * 256.0 + bytes.y, bytes.z * 256.0 + bytes.w) / 65535.0;`,
}

const HIT_FLOAT = {
  write: `
    o_hit = vec4(hitUV, 0.0, 1.0);`,
  read: `
      vec2 hitUV = texture2D(u_cached_hit, cell).xy;`,
}

const BOUND_UNIT = 3
const BUILD_UNIT = 4

const BOUND_BUILD_FRAGMENT = `#version 300 es
  precision highp float;
  uniform highp sampler2D u_source;
  uniform int u_reduce;
  uniform int u_level;
  out vec4 o_value;

  void main() {
    ivec2 p = ivec2(gl_FragCoord.xy);
    ivec2 last = textureSize(u_source, u_level) - 1;
    if (u_reduce == 0) {
      o_value = vec4(all(lessThanEqual(p, last)) ? texelFetch(u_source, p, u_level).g : 0.0);
      return;
    }
    ivec2 q = p * 2;
    float top = max(
      max(texelFetch(u_source, min(q, last), u_level).r, texelFetch(u_source, min(q + ivec2(1, 0), last), u_level).r),
      max(texelFetch(u_source, min(q + ivec2(0, 1), last), u_level).r, texelFetch(u_source, min(q + ivec2(1, 1), last), u_level).r)
    );
    o_value = vec4(top);
  }
`

const cacheFragment = hit => `#version 300 es
  precision highp float;
  #define texture2D texture
  #define MARCH_BOUND
  ${PARALLAX_UNIFORMS}
  in vec2 v_texCoord;
  layout(location = 0) out vec4 o_color;
  layout(location = 1) out vec4 o_hit;
  ${PARALLAX}
  void main() {
    vec2 hitUV;
    o_color = parallaxColorAt(v_texCoord, hitUV);${hit.write}
  }
`

const relightFragment = hit => `
  precision highp float;
  uniform sampler2D u_cached_color;
  uniform sampler2D u_cached_hit;
  ${LIGHT_UNIFORMS}
  varying vec2 v_texCoord;
  ${SKYLIGHT}
  void main() {
    vec2 cell = vec2(v_texCoord.x, 1.0 - v_texCoord.y);
    vec4 color = texture2D(u_cached_color, cell);
    if (u_light > 0.0) {${hit.read}
      color.rgb = lightAt(color.rgb, hitUV, v_texCoord);
    }
    gl_FragColor = color;
  }
`

const QUAD_POSITIONS = new Float32Array([
  -1, -1,  1, -1,  -1, 1,
  -1, 1,   1, -1,   1, 1
])

const QUAD_TEX_COORDS = new Float32Array([
  0, 1,  1, 1,  0, 0,
  0, 0,  1, 1,  1, 0
])

const PARALLAX_UNIFORM_NAMES = ['u_color', 'u_depth', 'u_disp', 'u_half_disp', 'u_disp_gate', 'u_disp_dir', 'u_spread', 'u_uv_scale', 'u_steps', 'u_layer_step', 'u_fill_taps']
const BOUND_UNIFORM_NAMES = ['u_depth_bound', 'u_bound_levels', 'u_depth_size', 'u_bound_size']
const LIGHT_UNIFORM_NAMES = ['u_normal', 'u_light_pos', 'u_light_amp', 'u_rect', 'u_light', 'u_energy']
const ARRAY_UNIFORMS = new Set(['u_light_pos', 'u_light_amp'])

const MIN_LINEAR_STEPS = 6

function linkProgram(gl, vertexSource, fragmentSource, uniformNames, samplers) {
  const compile = (type, source) => {
    const shader = gl.createShader(type)
    gl.shaderSource(shader, source)
    gl.compileShader(shader)
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      const info = gl.getShaderInfoLog(shader)
      gl.deleteShader(shader)
      throw new Error(`depth art shader: ${info}`)
    }
    return shader
  }

  const vertShader = compile(gl.VERTEX_SHADER, vertexSource)
  const fragShader = compile(gl.FRAGMENT_SHADER, fragmentSource)
  const program = gl.createProgram()
  gl.attachShader(program, vertShader)
  gl.attachShader(program, fragShader)
  gl.bindAttribLocation(program, 0, 'a_position')
  gl.bindAttribLocation(program, 1, 'a_texCoord')
  gl.linkProgram(program)
  gl.deleteShader(vertShader)
  gl.deleteShader(fragShader)
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(`depth art program: ${gl.getProgramInfoLog(program)}`)
  }
  gl.useProgram(program)

  const uniforms = {}
  for (const name of uniformNames) {
    uniforms[name.slice(2)] = gl.getUniformLocation(program, ARRAY_UNIFORMS.has(name) ? `${name}[0]` : name)
  }
  for (const [name, unit] of Object.entries(samplers)) gl.uniform1i(uniforms[name], unit)
  return { program, uniforms }
}

function withDefines(source, defines) {
  if (!defines.length) return source
  const versionEnd = source.startsWith('#version') ? source.indexOf(String.fromCharCode(10)) + 1 : 0
  return [source.slice(0, versionEnd), ...defines.map(name => `#define ${name}`), source.slice(versionEnd)].join(String.fromCharCode(10))
}

export function createDepthArtPrograms(gl, { packedNormals = false, mediumpShading = true } = {}) {
  const shading = mediumpShading ? ['MEDIUMP_SHADING'] : []
  const defines = [packedNormals && 'PACKED_NORMALS', ...shading].filter(Boolean)
  const posBuffer = gl.createBuffer()
  gl.bindBuffer(gl.ARRAY_BUFFER, posBuffer)
  gl.bufferData(gl.ARRAY_BUFFER, QUAD_POSITIONS, gl.STATIC_DRAW)
  gl.enableVertexAttribArray(0)
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0)

  const texBuffer = gl.createBuffer()
  gl.bindBuffer(gl.ARRAY_BUFFER, texBuffer)
  gl.bufferData(gl.ARRAY_BUFFER, QUAD_TEX_COORDS, gl.STATIC_DRAW)
  gl.enableVertexAttribArray(1)
  gl.vertexAttribPointer(1, 2, gl.FLOAT, false, 0, 0)

  const fullUniforms = [...PARALLAX_UNIFORM_NAMES, ...LIGHT_UNIFORM_NAMES]
  const fullSamplers = { color: 0, depth: 1, normal: 2 }
  if (typeof WebGL2RenderingContext !== 'undefined' && gl instanceof WebGL2RenderingContext) {
    try {
      const floatHit = !!gl.getExtension('EXT_color_buffer_float')
      const hit = floatHit ? HIT_FLOAT : HIT_PACKED
      const builder = linkProgram(gl, VERTEX_300, BOUND_BUILD_FRAGMENT, ['u_source', 'u_reduce', 'u_level'], { source: BUILD_UNIT })
      const cache = linkProgram(gl, VERTEX_300, withDefines(cacheFragment(hit), shading), [...PARALLAX_UNIFORM_NAMES, ...BOUND_UNIFORM_NAMES], { color: 0, depth: 1, depth_bound: BOUND_UNIT })
      const relight = linkProgram(gl, VERTEX_100, withDefines(relightFragment(hit), defines), ['u_cached_color', 'u_cached_hit', ...LIGHT_UNIFORM_NAMES], { cached_color: 0, cached_hit: 1, normal: 2 })
      const full = linkProgram(gl, VERTEX_300, withDefines(FULL_FRAGMENT_300, defines), [...fullUniforms, ...BOUND_UNIFORM_NAMES], { ...fullSamplers, depth_bound: BOUND_UNIT })
      const noBound = gl.createTexture()
      gl.bindTexture(gl.TEXTURE_2D, noBound)
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, 1, 1, 0, gl.RED, gl.UNSIGNED_BYTE, new Uint8Array([255]))
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST)
      const flat = linkProgram(gl, VERTEX_100, FLAT_FRAGMENT, ['u_color'], { color: 0 })
      return { full, cache, relight, flat, floatHit, coarse: floatHit ? createCoarsePrograms(gl, defines, fullUniforms, fullSamplers) : null, bound: { builder, noBound } }
    } catch (error) {
      logger.warn('[DepthArt] Parallax cache unavailable:', error)
    }
  }
  const full = linkProgram(gl, VERTEX_100, withDefines(FULL_FRAGMENT, defines), fullUniforms, fullSamplers)
  const flat = linkProgram(gl, VERTEX_100, FLAT_FRAGMENT, ['u_color'], { color: 0 })
  return { full, cache: null, relight: null, flat, floatHit: false, coarse: null, bound: null }
}

export const COARSE_UNITS = { hit: 4, relief: 5, extra: 6, fill: 7 }

function createCoarsePrograms(gl, defines, fullUniforms, fullSamplers) {
  try {
    const pass = linkProgram(gl, VERTEX_300, withDefines(COARSE_FRAGMENT, defines), [...fullUniforms, ...BOUND_UNIFORM_NAMES], { ...fullSamplers, depth_bound: BOUND_UNIT })
    const compose = linkProgram(gl, VERTEX_300, withDefines(COMPOSE_FRAGMENT, defines.filter(name => name !== 'PACKED_NORMALS')),
      ['u_color', 'u_coarse_hit', 'u_coarse_relief', 'u_coarse_extra', 'u_coarse_fill', 'u_coarse_rect', 'u_coarse_size', 'u_uv_scale'],
      { color: 0, coarse_hit: COARSE_UNITS.hit, coarse_relief: COARSE_UNITS.relief, coarse_extra: COARSE_UNITS.extra, coarse_fill: COARSE_UNITS.fill })
    return { pass, compose }
  } catch (error) {
    logger.warn('[DepthArt] Coarse parallax unavailable:', error)
    return null
  }
}

export function createCoarseTargets(gl, width, height) {
  const formats = [
    [gl.RG16F, gl.RG, gl.HALF_FLOAT, gl.LINEAR],
    [gl.RGBA16F, gl.RGBA, gl.HALF_FLOAT, gl.LINEAR],
    [gl.RGBA16F, gl.RGBA, gl.HALF_FLOAT, gl.LINEAR],
    [gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, gl.LINEAR],
  ]
  const textures = formats.map(([internalFormat, format, type, filter]) => {
    const texture = gl.createTexture()
    gl.bindTexture(gl.TEXTURE_2D, texture)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter)
    gl.texImage2D(gl.TEXTURE_2D, 0, internalFormat, width, height, 0, format, type, null)
    return texture
  })
  const framebuffer = gl.createFramebuffer()
  gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer)
  const attachments = textures.map((_, index) => gl.COLOR_ATTACHMENT0 + index)
  textures.forEach((texture, index) => gl.framebufferTexture2D(gl.FRAMEBUFFER, attachments[index], gl.TEXTURE_2D, texture, 0))
  gl.drawBuffers(attachments)
  const complete = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE
  gl.bindFramebuffer(gl.FRAMEBUFFER, null)
  const targets = { width, height, framebuffer, attachments, hit: textures[0], relief: textures[1], extra: textures[2], fill: textures[3] }
  if (complete) return targets
  deleteCoarseTargets(gl, targets)
  return null
}

export function deleteCoarseTargets(gl, targets) {
  if (!targets || gl.isContextLost()) return
  gl.deleteFramebuffer(targets.framebuffer)
  for (const texture of [targets.hit, targets.relief, targets.extra, targets.fill]) gl.deleteTexture(texture)
}

export function createDepthBound(gl, programs, depthTexture, width, height) {
  const state = programs.bound
  if (!state || state.failed) return null
  try {
    const bound = buildDepthBound(gl, state.builder, depthTexture, width, height, !state.verified)
    if (!state.verified) {
      state.verified = true
      if (!bound || gl.getError() !== gl.NO_ERROR) throw new Error('depth bound did not render')
    }
    return bound
  } catch (error) {
    state.failed = true
    logger.warn('[DepthArt] Depth bound unavailable, full march:', error)
    gl.bindFramebuffer(gl.FRAMEBUFFER, null)
    return null
  }
}

function buildDepthBound(gl, builder, depthTexture, width, height, verify) {
  const levelWidth = 2 ** Math.ceil(Math.log2(Math.max(1, width)))
  const levelHeight = 2 ** Math.ceil(Math.log2(Math.max(1, height)))
  const levels = Math.log2(Math.max(levelWidth, levelHeight)) + 1
  const texture = gl.createTexture()
  gl.activeTexture(gl.TEXTURE0 + BUILD_UNIT)
  gl.bindTexture(gl.TEXTURE_2D, texture)
  gl.texStorage2D(gl.TEXTURE_2D, levels, gl.R8, levelWidth, levelHeight)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST_MIPMAP_NEAREST)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)

  const { program, uniforms } = builder
  const framebuffer = gl.createFramebuffer()
  gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer)
  gl.useProgram(program)

  gl.bindTexture(gl.TEXTURE_2D, depthTexture)
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0)
  if (verify && gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) {
    gl.bindFramebuffer(gl.FRAMEBUFFER, null)
    gl.deleteFramebuffer(framebuffer)
    gl.deleteTexture(texture)
    return null
  }
  gl.viewport(0, 0, levelWidth, levelHeight)
  gl.uniform1i(uniforms.reduce, 0)
  gl.uniform1i(uniforms.level, 0)
  gl.drawArrays(gl.TRIANGLES, 0, 6)

  if (levels > 1) {
    const scratch = gl.createTexture()
    gl.bindTexture(gl.TEXTURE_2D, scratch)
    gl.texStorage2D(gl.TEXTURE_2D, 1, gl.R8, Math.max(1, levelWidth >> 1), Math.max(1, levelHeight >> 1))
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, scratch, 0)
    gl.uniform1i(uniforms.reduce, 1)
    for (let level = 1; level < levels; level++) {
      const width = Math.max(1, levelWidth >> level)
      const height = Math.max(1, levelHeight >> level)
      gl.bindTexture(gl.TEXTURE_2D, texture)
      gl.uniform1i(uniforms.level, level - 1)
      gl.viewport(0, 0, width, height)
      gl.drawArrays(gl.TRIANGLES, 0, 6)
      gl.copyTexSubImage2D(gl.TEXTURE_2D, level, 0, 0, 0, 0, width, height)
    }
    gl.deleteTexture(scratch)
  }
  gl.bindFramebuffer(gl.FRAMEBUFFER, null)
  gl.deleteFramebuffer(framebuffer)
  return { texture, levels, width, height, levelWidth, levelHeight }
}

export function bindDepthBound(gl, programs, uniforms, bound) {
  if (!programs.bound) return
  gl.activeTexture(gl.TEXTURE0 + BOUND_UNIT)
  gl.bindTexture(gl.TEXTURE_2D, bound ? bound.texture : programs.bound.noBound)
  gl.uniform1f(uniforms.bound_levels, bound ? bound.levels : 0)
  if (!bound) return
  gl.uniform2f(uniforms.depth_size, bound.width, bound.height)
  gl.uniform2f(uniforms.bound_size, bound.levelWidth, bound.levelHeight)
}

export function createParallaxCache(gl, width, height, floatHit) {
  const formats = [[gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE], floatHit ? [gl.RG32F, gl.RG, gl.FLOAT] : [gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE]]
  const textures = formats.map(([internalFormat, format, type]) => {
    const texture = gl.createTexture()
    gl.bindTexture(gl.TEXTURE_2D, texture)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST)
    gl.texImage2D(gl.TEXTURE_2D, 0, internalFormat, width, height, 0, format, type, null)
    return texture
  })
  const framebuffer = gl.createFramebuffer()
  gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer)
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, textures[0], 0)
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT1, gl.TEXTURE_2D, textures[1], 0)
  gl.drawBuffers([gl.COLOR_ATTACHMENT0, gl.COLOR_ATTACHMENT1])
  gl.bindFramebuffer(gl.FRAMEBUFFER, null)
  return { width, height, color: textures[0], hit: textures[1], framebuffer, art: null, px: 0, py: 0, ready: false }
}

export function deleteParallaxCache(gl, cache) {
  if (!cache || gl.isContextLost()) return
  gl.deleteFramebuffer(cache.framebuffer)
  gl.deleteTexture(cache.color)
  gl.deleteTexture(cache.hit)
}

export function parallaxSteps(travelPx, stepPx) {
  if (stepPx <= 0) return POM.LINEAR_STEPS
  return Math.min(POM.LINEAR_STEPS, Math.max(MIN_LINEAR_STEPS, Math.ceil(travelPx / stepPx)))
}

export const viewport = { width: 1, height: 1 }

function measureViewport() {
  viewport.width = Math.max(1, window.innerWidth)
  viewport.height = Math.max(1, window.innerHeight)
}

if (typeof window !== 'undefined') {
  measureViewport()
  window.addEventListener('resize', measureViewport, { passive: true })
  window.visualViewport?.addEventListener('resize', measureViewport, { passive: true })
}

export function uvScale(intensity) {
  return 1 / (1 + Math.abs(intensity) * POM.ZOOM_FACTOR)
}

export function setFillTaps(gl, uniforms) {
  if (uniforms.fill_taps) gl.uniform1i(uniforms.fill_taps, FILL_TAPS)
}

export function setParallaxUniforms(gl, uniforms, px, py, intensity, steps) {
  const dx = px * intensity
  const dy = py * intensity
  const length = Math.hypot(dx, dy)
  const gate = clamp01((length - POM.DISP_GATE_MIN) / (POM.DISP_GATE_MAX - POM.DISP_GATE_MIN))
  gl.uniform2f(uniforms.disp, dx, dy)
  gl.uniform2f(uniforms.half_disp, 0.5 * dx, 0.5 * dy)
  gl.uniform1f(uniforms.disp_gate, gate * gate * (3 - 2 * gate))
  if (length > 0.001) gl.uniform2f(uniforms.disp_dir, dx / length, dy / length)
  else gl.uniform2f(uniforms.disp_dir, 1, 0)
  gl.uniform1f(uniforms.spread, Math.max(length * POM.FILL_SPREAD_MULT, POM.FILL_SPREAD_MIN))
  gl.uniform1f(uniforms.uv_scale, uvScale(intensity))
  gl.uniform1f(uniforms.steps, steps)
  gl.uniform1f(uniforms.layer_step, 1 / steps)
}

function clamp01(value) {
  return Math.min(1, Math.max(0, value))
}

const lightPositions = new Float32Array(MAX_LIGHTS * 3)
const lightAmplitudes = new Float32Array(MAX_LIGHTS * 3)

export function setLightUniforms(gl, uniforms, probe, hasNormals) {
  const on = probe.active && hasNormals
  gl.uniform1f(uniforms.light, on ? probe.level : 0)
  if (!on) return false
  const aspect = viewport.width / viewport.height
  for (let i = 0; i < MAX_LIGHTS; i++) {
    const strength = probe.lights[i * 4 + 3]
    lightPositions[i * 3] = probe.lights[i * 4] * aspect
    lightPositions[i * 3 + 1] = probe.lights[i * 4 + 1]
    lightPositions[i * 3 + 2] = strength
    for (let c = 0; c < 3; c++) lightAmplitudes[i * 3 + c] = probe.lightColors[i * 3 + c] * strength * LIGHT.STRENGTH
  }
  gl.uniform3fv(uniforms.light_pos, lightPositions)
  gl.uniform3fv(uniforms.light_amp, lightAmplitudes)
  gl.uniform1f(uniforms.energy, probe.level * (1 + probe.pulse * LIGHT.PULSE + probe.kick * LIGHT.KICK))
  return true
}

export function setLightRect(gl, uniforms, rect) {
  const height = viewport.height
  gl.uniform4f(uniforms.rect, rect.left / height, rect.top / height, rect.width / height, rect.height / height)
}
