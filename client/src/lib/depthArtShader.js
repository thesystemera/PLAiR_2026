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
//   Lighting too strong?   → Lower LIGHT.RELIEF / LIGHT.RIM
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
  KICK:             1.2,    // Extra rim + relief on a kick flash.
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

const PARALLAX_UNIFORMS = `
  uniform sampler2D u_color;
  uniform sampler2D u_depth;
  uniform vec2 u_gyro;
  uniform float u_intensity;
  uniform float u_zoom;
  uniform float u_steps;
`

const LIGHT_UNIFORMS = `
  uniform sampler2D u_normal;
  uniform vec4 u_lights[${MAX_LIGHTS}];
  uniform vec3 u_light_colors[${MAX_LIGHTS}];
  uniform vec4 u_rect;
  uniform float u_aspect;
  uniform float u_light;
  uniform float u_kick;
  uniform float u_pulse;
`

const MARCH_BOUND = `
  #ifdef MARCH_BOUND
  uniform highp sampler2D u_depth_bound;
  uniform float u_bound_levels;

  float marchBound(vec2 uv, vec2 displacement) {
    vec2 size = vec2(textureSize(u_depth, 0));
    vec2 a = clamp(uv - 0.5 * displacement, 0.0, 1.0) * size - 0.5;
    vec2 b = clamp(uv + 0.5 * displacement, 0.0, 1.0) * size - 0.5;
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
    ivec2 last = textureSize(u_depth_bound, lod) - 1;
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
`

const PARALLAX = `
  ${SHADE_PRECISION}
  ${MARCH_BOUND}
  ${SAMPLES}

  vec4 parallax(vec2 uv, vec2 displacement, out vec2 hitUV) {
    float dispLen = length(displacement);

    const int LINEAR_STEPS = ${POM.LINEAR_STEPS};
    float layerStep = 1.0 / u_steps;

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
      testUV = uv - (mix(aboveDepth, belowDepth, aboveGap / max(aboveGap - belowGap, 1e-5)) - 0.5) * displacement;
    } else {
      testUV = uv + 0.5 * displacement;
    }

    hitUV = testUV;
    SHADE vec4 pomColor = COLOR_AT(clamp(testUV, 0.001, 0.999));

    SHADE float dispGate = smoothstep(${G(POM.DISP_GATE_MIN)}, ${G(POM.DISP_GATE_MAX)}, dispLen);
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

    vec2 dispDir = dispLen > 0.001 ? displacement / dispLen : vec2(1.0, 0.0);
    vec2 perpDir = vec2(-dispDir.y, dispDir.x);
    float spread = max(dispLen * ${G(POM.FILL_SPREAD_MULT)}, ${G(POM.FILL_SPREAD_MIN)});

    vec2 s1 = uv + perpDir * spread;
    vec2 s2 = uv - perpDir * spread;
    vec2 s3 = uv - dispDir * spread;
    vec2 s4 = uv - dispDir * spread * 2.0;

    SHADE float fd1 = DEPTH_AT(clamp(s1, 0.0, 1.0));
    SHADE float fd2 = DEPTH_AT(clamp(s2, 0.0, 1.0));
    SHADE float fd3 = DEPTH_AT(clamp(s3, 0.0, 1.0));
    SHADE float fd4 = DEPTH_AT(clamp(s4, 0.0, 1.0));

    SHADE float w1 = max(0.01, pow(1.0 - fd1, ${G(POM.FILL_DEPTH_POWER)}));
    SHADE float w2 = max(0.01, pow(1.0 - fd2, ${G(POM.FILL_DEPTH_POWER)}));
    SHADE float w3 = max(0.01, pow(1.0 - fd3, ${G(POM.FILL_DEPTH_POWER)}));
    SHADE float w4 = max(0.01, pow(1.0 - fd4, ${G(POM.FILL_DEPTH_POWER)}));

    SHADE vec4 fc1 = COLOR_AT(clamp(s1 - (fd1 - 0.5) * displacement, 0.001, 0.999)) * w1;
    SHADE vec4 fc2 = COLOR_AT(clamp(s2 - (fd2 - 0.5) * displacement, 0.001, 0.999)) * w2;
    SHADE vec4 fc3 = COLOR_AT(clamp(s3 - (fd3 - 0.5) * displacement, 0.001, 0.999)) * w3;
    SHADE vec4 fc4 = COLOR_AT(clamp(s4 - (fd4 - 0.5) * displacement, 0.001, 0.999)) * w4;

    SHADE vec4 fillColor = (fc1 + fc2 + fc3 + fc4) / (w1 + w2 + w3 + w4);

    return mix(pomColor, fillColor, trailMask * ${G(POM.FILL_STRENGTH)});
  }


  vec4 parallaxAt(vec2 texCoord, out vec2 hitUV) {
    vec2 displacement = u_gyro * u_intensity;

    float autoZoom = 1.0 + abs(u_intensity) * ${G(POM.ZOOM_FACTOR)};
    float finalZoom = u_zoom * autoZoom;
    vec2 uv = (texCoord - 0.5) / finalZoom + 0.5;

    return parallax(uv, displacement, hitUV);
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

const SKYLIGHT = `
  #define NORMAL_AT(uv) texture2D(u_normal, uv)
  ${SHADE_PRECISION}
  ${powerFunction(LIGHT.SHININESS)}

  SHADE vec3 skylight(SHADE vec3 color, vec2 hitUV, vec2 screenUV) {
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
      SHADE vec4 source = u_lights[i];
      if (source.w <= 0.001) continue;
      SHADE vec2 toLight = (source.xy - screenUV) * vec2(u_aspect, 1.0);
      SHADE float dist2 = dot(toLight, toLight);
      SHADE vec2 dir = dist2 > 1e-8 ? toLight * inversesqrt(dist2) : vec2(0.0);
      SHADE vec3 L = vec3(toLight, ${G(LIGHT.HEIGHT)}) * inversesqrt(dist2 + ${G(LIGHT.HEIGHT * LIGHT.HEIGHT)});
      SHADE vec3 light = u_light_colors[i] * (source.w * ${G(LIGHT.STRENGTH)} / (1.0 + dist2 * ${G(LIGHT.FALLOFF)}));
      relief += light * (dot(n, L) - L.z);
      SHADE vec3 H = vec3(L.xy, L.z + 1.0) * inversesqrt(2.0 + 2.0 * L.z);
      glint += light * max(shine(max(dot(n, H), 0.0)) - shine(H.z), 0.0);
      rim += light * max(dot(facing, dir), 0.0);
    }

    SHADE float energy = u_light * (1.0 + u_pulse * ${G(LIGHT.PULSE)} + u_kick * ${G(LIGHT.KICK)});
    SHADE float edge = smoothstep(${G(LIGHT.RIM_SOFT)}, ${G(LIGHT.RIM_HARD)}, slopeLen);
    SHADE vec3 lit = color * (1.0 + relief * ${G(LIGHT.RELIEF)} * energy);
    lit += (glint * ${G(LIGHT.SPECULAR)} + rim * edge * ${G(LIGHT.RIM)}) * energy * (1.0 - lit);
    return clamp(lit, 0.0, 1.0);
  }

  vec3 lightAt(vec3 color, vec2 hitUV, vec2 texCoord) {
    return skylight(color, hitUV, u_rect.xy + texCoord * u_rect.zw);
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
    vec4 color = parallaxAt(v_texCoord, hitUV);
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
    vec4 color = parallaxAt(v_texCoord, hitUV);
    if (u_light > 0.0) color.rgb = lightAt(color.rgb, hitUV, v_texCoord);
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
    o_color = parallaxAt(v_texCoord, hitUV);${hit.write}
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

const PARALLAX_UNIFORM_NAMES = ['u_color', 'u_depth', 'u_gyro', 'u_intensity', 'u_zoom', 'u_steps']
const LIGHT_UNIFORM_NAMES = ['u_normal', 'u_lights', 'u_light_colors', 'u_rect', 'u_aspect', 'u_light', 'u_kick', 'u_pulse']

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
    uniforms[name.slice(2)] = gl.getUniformLocation(program, name === 'u_lights' || name === 'u_light_colors' ? `${name}[0]` : name)
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
      const cache = linkProgram(gl, VERTEX_300, withDefines(cacheFragment(hit), shading), [...PARALLAX_UNIFORM_NAMES, 'u_depth_bound', 'u_bound_levels'], { color: 0, depth: 1, depth_bound: BOUND_UNIT })
      const relight = linkProgram(gl, VERTEX_100, withDefines(relightFragment(hit), defines), ['u_cached_color', 'u_cached_hit', ...LIGHT_UNIFORM_NAMES], { cached_color: 0, cached_hit: 1, normal: 2 })
      const full = linkProgram(gl, VERTEX_300, withDefines(FULL_FRAGMENT_300, defines), [...fullUniforms, 'u_depth_bound', 'u_bound_levels'], { ...fullSamplers, depth_bound: BOUND_UNIT })
      const noBound = gl.createTexture()
      gl.bindTexture(gl.TEXTURE_2D, noBound)
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, 1, 1, 0, gl.RED, gl.UNSIGNED_BYTE, new Uint8Array([255]))
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST)
      const flat = linkProgram(gl, VERTEX_100, FLAT_FRAGMENT, ['u_color'], { color: 0 })
      return { full, cache, relight, flat, floatHit, bound: { builder, noBound } }
    } catch (error) {
      logger.warn('[DepthArt] Parallax cache unavailable:', error)
    }
  }
  const full = linkProgram(gl, VERTEX_100, withDefines(FULL_FRAGMENT, defines), fullUniforms, fullSamplers)
  const flat = linkProgram(gl, VERTEX_100, FLAT_FRAGMENT, ['u_color'], { color: 0 })
  return { full, cache: null, relight: null, flat, floatHit: false, bound: null }
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
  return { texture, levels }
}

export function bindDepthBound(gl, programs, uniforms, bound) {
  if (!programs.bound) return
  gl.activeTexture(gl.TEXTURE0 + BOUND_UNIT)
  gl.bindTexture(gl.TEXTURE_2D, bound ? bound.texture : programs.bound.noBound)
  gl.uniform1f(uniforms.bound_levels, bound ? bound.levels : 0)
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

export function setLightUniforms(gl, uniforms, probe, hasNormals) {
  const on = probe.active && hasNormals
  gl.uniform1f(uniforms.light, on ? probe.level : 0)
  if (!on) return false
  gl.uniform4fv(uniforms.lights, probe.lights)
  gl.uniform3fv(uniforms.light_colors, probe.lightColors)
  gl.uniform1f(uniforms.aspect, viewport.width / viewport.height)
  gl.uniform1f(uniforms.kick, probe.kick)
  gl.uniform1f(uniforms.pulse, probe.pulse)
  return true
}

export function setLightRect(gl, uniforms, rect) {
  gl.uniform4f(uniforms.rect, rect.left / viewport.width, rect.top / viewport.height, rect.width / viewport.width, rect.height / viewport.height)
}
