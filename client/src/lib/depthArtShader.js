import { MAX_LIGHTS } from './lightProbe'

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
  REFINE_STEPS:     5,      // Binary refinement iterations after hit (3-8). More = sub-pixel precision.

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
  STRENGTH:         0.25,    // Overall light level per unit of a source's contrast with the scene.

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

const depthArtVertexShader = `
  attribute vec2 a_position;
  attribute vec2 a_texCoord;
  varying vec2 v_texCoord;

  void main() {
    gl_Position = vec4(a_position, 0.0, 1.0);
    v_texCoord = a_texCoord;
  }
`

const depthArtFragmentShader = `
  precision highp float;

  uniform sampler2D u_color;
  uniform sampler2D u_depth;
  uniform sampler2D u_normal;
  uniform vec2 u_gyro;
  uniform float u_intensity;
  uniform float u_zoom;
  uniform float u_steps;

  uniform vec4 u_lights[${MAX_LIGHTS}];
  uniform vec3 u_light_colors[${MAX_LIGHTS}];
  uniform vec4 u_rect;
  uniform float u_aspect;
  uniform float u_light;
  uniform float u_kick;
  uniform float u_pulse;

  varying vec2 v_texCoord;

  vec4 parallax(vec2 uv, vec2 displacement, out vec2 hitUV) {
    float dispLen = length(displacement);

    const int LINEAR_STEPS = ${POM.LINEAR_STEPS};
    const int REFINE_STEPS = ${POM.REFINE_STEPS};
    float layerStep = 1.0 / u_steps;

    float testDepth = 1.0;
    float prevTestDepth = 1.0;
    vec2 testUV;
    float sampledDepth;
    bool hit = false;

    for (int i = 0; i < LINEAR_STEPS; i++) {
      if (float(i) >= u_steps) break;
      testUV = uv - (testDepth - 0.5) * displacement;
      sampledDepth = texture2D(u_depth, clamp(testUV, 0.0, 1.0)).r;

      if (sampledDepth >= testDepth) {
        hit = true;
        break;
      }

      prevTestDepth = testDepth;
      testDepth -= layerStep;
    }

    if (hit) {
      float lo = testDepth;
      float hi = prevTestDepth;
      vec2 bestUV = testUV;

      for (int j = 0; j < REFINE_STEPS; j++) {
        float mid = (lo + hi) * 0.5;
        vec2 midUV = uv - (mid - 0.5) * displacement;
        float midSample = texture2D(u_depth, clamp(midUV, 0.0, 1.0)).r;

        if (midSample >= mid) {
          lo = mid;
          bestUV = midUV;
        } else {
          hi = mid;
        }
      }

      testUV = bestUV;
    } else {
      testUV = uv + 0.5 * displacement;
    }

    hitUV = testUV;
    vec4 pomColor = texture2D(u_color, clamp(testUV, 0.001, 0.999));

    float dispGate = smoothstep(${G(POM.DISP_GATE_MIN)}, ${G(POM.DISP_GATE_MAX)}, dispLen);
    if (dispGate <= 0.0) return pomColor;

    float baseDepth = texture2D(u_depth, clamp(uv, 0.0, 1.0)).r;
    float foregroundness = smoothstep(${G(POM.FG_DEPTH_SOFT)}, ${G(POM.FG_DEPTH_HARD)}, baseDepth);
    if (foregroundness <= 0.0) return pomColor;

    float texel = ${G(POM.EDGE_RADIUS)};
    float dL = texture2D(u_depth, clamp(testUV - vec2(texel, 0.0), 0.0, 1.0)).r;
    float dR = texture2D(u_depth, clamp(testUV + vec2(texel, 0.0), 0.0, 1.0)).r;
    float dU = texture2D(u_depth, clamp(testUV - vec2(0.0, texel), 0.0, 1.0)).r;
    float dD = texture2D(u_depth, clamp(testUV + vec2(0.0, texel), 0.0, 1.0)).r;
    float gradient = abs(dR - dL) + abs(dD - dU);

    float edgeness = smoothstep(${G(POM.EDGE_SOFT)}, ${G(POM.EDGE_HARD)}, gradient);
    float trailMask = edgeness * foregroundness * dispGate;
    if (trailMask <= 0.0) return pomColor;

    vec2 dispDir = dispLen > 0.001 ? displacement / dispLen : vec2(1.0, 0.0);
    vec2 perpDir = vec2(-dispDir.y, dispDir.x);
    float spread = max(dispLen * ${G(POM.FILL_SPREAD_MULT)}, ${G(POM.FILL_SPREAD_MIN)});

    vec2 s1 = uv + perpDir * spread;
    vec2 s2 = uv - perpDir * spread;
    vec2 s3 = uv - dispDir * spread;
    vec2 s4 = uv - dispDir * spread * 2.0;

    float fd1 = texture2D(u_depth, clamp(s1, 0.0, 1.0)).r;
    float fd2 = texture2D(u_depth, clamp(s2, 0.0, 1.0)).r;
    float fd3 = texture2D(u_depth, clamp(s3, 0.0, 1.0)).r;
    float fd4 = texture2D(u_depth, clamp(s4, 0.0, 1.0)).r;

    float w1 = max(0.01, pow(1.0 - fd1, ${G(POM.FILL_DEPTH_POWER)}));
    float w2 = max(0.01, pow(1.0 - fd2, ${G(POM.FILL_DEPTH_POWER)}));
    float w3 = max(0.01, pow(1.0 - fd3, ${G(POM.FILL_DEPTH_POWER)}));
    float w4 = max(0.01, pow(1.0 - fd4, ${G(POM.FILL_DEPTH_POWER)}));

    vec4 fc1 = texture2D(u_color, clamp(s1 - (fd1 - 0.5) * displacement, 0.001, 0.999)) * w1;
    vec4 fc2 = texture2D(u_color, clamp(s2 - (fd2 - 0.5) * displacement, 0.001, 0.999)) * w2;
    vec4 fc3 = texture2D(u_color, clamp(s3 - (fd3 - 0.5) * displacement, 0.001, 0.999)) * w3;
    vec4 fc4 = texture2D(u_color, clamp(s4 - (fd4 - 0.5) * displacement, 0.001, 0.999)) * w4;

    vec4 fillColor = (fc1 + fc2 + fc3 + fc4) / (w1 + w2 + w3 + w4);

    return mix(pomColor, fillColor, trailMask * ${G(POM.FILL_STRENGTH)});
  }

  vec3 skylight(vec3 color, vec2 hitUV, vec2 screenUV) {
    vec3 n = normalize(texture2D(u_normal, clamp(hitUV, 0.0, 1.0)).rgb * 2.0 - 1.0);
    float slopeLen = length(n.xy);
    vec2 facing = slopeLen > 0.0001 ? n.xy / slopeLen : vec2(0.0);

    vec3 relief = vec3(0.0);
    vec3 glint = vec3(0.0);
    vec3 rim = vec3(0.0);
    for (int i = 0; i < ${MAX_LIGHTS}; i++) {
      vec4 source = u_lights[i];
      if (source.w <= 0.001) continue;
      vec2 toLight = (source.xy - screenUV) * vec2(u_aspect, 1.0);
      float dist = length(toLight);
      vec2 dir = dist > 0.0001 ? toLight / dist : vec2(0.0);
      vec3 L = normalize(vec3(toLight, ${G(LIGHT.HEIGHT)}));
      vec3 light = u_light_colors[i] * source.w * ${G(LIGHT.STRENGTH)} / (1.0 + dist * dist * ${G(LIGHT.FALLOFF)});
      relief += light * (dot(n, L) - L.z);
      vec3 H = normalize(L + vec3(0.0, 0.0, 1.0));
      glint += light * max(pow(max(dot(n, H), 0.0), ${G(LIGHT.SHININESS)}) - pow(H.z, ${G(LIGHT.SHININESS)}), 0.0);
      rim += light * max(dot(facing, dir), 0.0);
    }

    float energy = u_light * (1.0 + u_pulse * ${G(LIGHT.PULSE)} + u_kick * ${G(LIGHT.KICK)});
    float edge = smoothstep(${G(LIGHT.RIM_SOFT)}, ${G(LIGHT.RIM_HARD)}, slopeLen);
    vec3 lit = color * (1.0 + relief * ${G(LIGHT.RELIEF)} * energy);
    lit += (glint * ${G(LIGHT.SPECULAR)} + rim * edge * ${G(LIGHT.RIM)}) * energy * (1.0 - lit);
    return clamp(lit, 0.0, 1.0);
  }

  void main() {
    vec2 displacement = u_gyro * u_intensity;

    float autoZoom = 1.0 + abs(u_intensity) * ${G(POM.ZOOM_FACTOR)};
    float finalZoom = u_zoom * autoZoom;
    vec2 uv = (v_texCoord - 0.5) / finalZoom + 0.5;

    vec2 hitUV;
    vec4 color = parallax(uv, displacement, hitUV);

    if (u_light > 0.0) {
      vec2 screenUV = u_rect.xy + v_texCoord * u_rect.zw;
      color.rgb = skylight(color.rgb, hitUV, screenUV);
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

const DEPTH_ART_UNIFORMS = ['u_color', 'u_depth', 'u_normal', 'u_gyro', 'u_intensity', 'u_zoom', 'u_steps', 'u_lights', 'u_light_colors', 'u_rect', 'u_aspect', 'u_light', 'u_kick', 'u_pulse']

const MIN_LINEAR_STEPS = 6

export function createDepthArtProgram(gl) {
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

  const vertShader = compile(gl.VERTEX_SHADER, depthArtVertexShader)
  const fragShader = compile(gl.FRAGMENT_SHADER, depthArtFragmentShader)
  const program = gl.createProgram()
  gl.attachShader(program, vertShader)
  gl.attachShader(program, fragShader)
  gl.linkProgram(program)
  gl.deleteShader(vertShader)
  gl.deleteShader(fragShader)
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(`depth art program: ${gl.getProgramInfoLog(program)}`)
  }
  gl.useProgram(program)

  const posBuffer = gl.createBuffer()
  gl.bindBuffer(gl.ARRAY_BUFFER, posBuffer)
  gl.bufferData(gl.ARRAY_BUFFER, QUAD_POSITIONS, gl.STATIC_DRAW)
  const posLoc = gl.getAttribLocation(program, 'a_position')
  gl.enableVertexAttribArray(posLoc)
  gl.vertexAttribPointer(posLoc, 2, gl.FLOAT, false, 0, 0)

  const texBuffer = gl.createBuffer()
  gl.bindBuffer(gl.ARRAY_BUFFER, texBuffer)
  gl.bufferData(gl.ARRAY_BUFFER, QUAD_TEX_COORDS, gl.STATIC_DRAW)
  const texLoc = gl.getAttribLocation(program, 'a_texCoord')
  gl.enableVertexAttribArray(texLoc)
  gl.vertexAttribPointer(texLoc, 2, gl.FLOAT, false, 0, 0)

  const uniforms = {}
  for (const name of DEPTH_ART_UNIFORMS) {
    uniforms[name.slice(2)] = gl.getUniformLocation(program, name === 'u_lights' || name === 'u_light_colors' ? `${name}[0]` : name)
  }
  gl.uniform1i(uniforms.color, 0)
  gl.uniform1i(uniforms.depth, 1)
  gl.uniform1i(uniforms.normal, 2)

  return { program, uniforms }
}

export function parallaxSteps(travelPx, stepPx) {
  if (stepPx <= 0) return POM.LINEAR_STEPS
  return Math.min(POM.LINEAR_STEPS, Math.max(MIN_LINEAR_STEPS, Math.ceil(travelPx / stepPx)))
}

export function setLightUniforms(gl, uniforms, probe, rect, hasNormals) {
  const on = probe.active && hasNormals
  gl.uniform1f(uniforms.light, on ? 1 : 0)
  if (!on) return
  const width = Math.max(1, window.innerWidth)
  const height = Math.max(1, window.innerHeight)
  gl.uniform4fv(uniforms.lights, probe.lights)
  gl.uniform3fv(uniforms.light_colors, probe.lightColors)
  gl.uniform4f(uniforms.rect, rect.left / width, rect.top / height, rect.width / width, rect.height / height)
  gl.uniform1f(uniforms.aspect, width / height)
  gl.uniform1f(uniforms.kick, probe.kick)
  gl.uniform1f(uniforms.pulse, probe.pulse)
}
