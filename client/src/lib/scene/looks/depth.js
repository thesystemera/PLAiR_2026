import { Matrix3, Vector2, Vector4 } from 'three'
import { pickWeighted } from '../random'
import { APERTURE, FOCUS_RANGE, analysisPixels, depthBands, depthGrid } from '../focus'
import { gradeMatrix } from '../grade'
import { FAR_LEVEL } from '../softPyramid'
import { FOOTAGE_SIGNATURE } from '../glsl/footage'

const DEG = Math.PI / 180
const MOTIONS = [['orbit', 2], ['dolly', 2], ['track', 2], ['crane', 1], ['breathe', 2]]
const GRADES = [['plain', 2], ['dusk', 2], ['neon', 1]]
const FOCUS_MODES = [['subject', 3], ['rack', 2], ['deep', 1]]
const RACK_BEATS = 8
const RACK_S = 0.6
const MAX_LAYER_BLUR = 3
const FILL_LEVEL = 4
const DEPTH_BIAS = 2.5
const SHADOW_REACH = 0.004
const TILT = [-0.25, 0.4, 1]
const SPREAD_ZOOMS = [1.02, 1.05, 1.09]
const STILL_SLIDE = [0, 0, 0]

const GRADE_LAYERS = {
  plain: [[0, 0.92, 0.9], [0, 0.97, 1], [0, 1, 1]],
  dusk: [[0, 0.5, 0.55], [0, 0.78, 0.85], [0, 1, 1.05]],
  neon: [[150, 0.75, 1.35], [60, 0.9, 1.15], [0, 1, 1]],
}

const easeInOut = x => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2)

function layerFocus(v, ctx, layers) {
  if (v.focus !== 'rack') return layers[2]
  const stage = Math.floor(ctx.beatsSince / RACK_BEATS)
  const target = n => (n % 2 === 0 ? layers[2] : layers[0])
  const beats = ctx.beats
  const start = beats?.length ? beats[Math.min(beats.length - 1, ctx.firstBeat + stage * RACK_BEATS)] : ctx.t
  const k = easeInOut(Math.min(1, Math.max(0, (ctx.t - start) / RACK_S)))
  const from = stage === 0 ? target(0) : target(stage - 1)
  return from + (target(stage) - from) * k
}

export function readDepth(map, makeCanvas) {
  const canvas = makeCanvas(map.width, map.height)
  canvas.getContext('2d', { willReadFrequently: true }).drawImage(map, 0, 0)
  const small = analysisPixels(map, makeCanvas)
  map.close()
  return { canvas, bands: depthBands(small.data), grid: depthGrid(small.data, small.width, small.height) }
}

const COVER_FAR = `        vec2 farUv = vFarShadow.xy;
        float hole = smoothstep(u_layer_bands.y - soft, u_layer_bands.y + soft, layerDepth(farUv, lod));
        vec3 far = hole < 0.998 ? layerColor(farUv, 0, lod) : vec3(0.0);
        if (hole > 0.002) far = mix(far, fillColor(farUv), hole);
`

const FOOTAGE_FAR = `        vec3 far = footageTreat(footageRead(u_clip0, framedClipUv(clipShape(vUv), u_canvas_resolution, u_layers[0].xy, u_layers[0].z, u_clip_res[0], u_ft_shape.z, 0.0)), u_grade, vUv).rgb;
`

const depthGlsl = footage => `
  uniform vec4 u_layers[3];
  uniform vec4 u_layer_grade[3];
  uniform mat3 u_layer_tone[3];
  uniform vec4 u_layer_bands;
  varying vec4 vNearMid;
  varying vec4 vFarShadow;

  float layerDepth(vec2 uv, float lod) {
    return textureLod(u_depth_map, uv, lod + ${DEPTH_BIAS.toFixed(1)}).g;
  }

  vec3 layerColor(vec2 uv, int k, float lod) {
    vec4 grade = u_layer_grade[k];
    vec4 color = gradeColor(softColor(uv, lod, u_layers[k].w), u_layer_tone[k]);
    float luma = dot(color.rgb, vec3(0.299, 0.587, 0.114));
    return mix(vec3(luma), color.rgb, grade.w) * grade.z;
  }

  vec3 fillColor(vec2 uv) {
    vec4 grade = u_layer_grade[0];
    vec4 color = gradeColor(textureLod(u_texture_far, uv, ${(FILL_LEVEL - FAR_LEVEL).toFixed(1)}), u_layer_tone[0]);
    float luma = dot(color.rgb, vec3(0.299, 0.587, 0.114));
    return mix(vec3(luma), color.rgb, grade.w) * grade.z;
  }

  vec4 lookImage() {
    float soft = u_layer_bands.z;
    vec2 nearUv = vNearMid.xy;
    vec2 texel = nearUv * u_tex_resolution;
    float lod = max(0.0, 0.5 * log2(max(dot(dFdx(texel), dFdx(texel)), dot(dFdy(texel), dFdy(texel)))));
    float nearAlpha = smoothstep(u_layer_bands.x - soft, u_layer_bands.x + soft, layerDepth(nearUv, lod));
    vec3 color = vec3(0.0);
    if (nearAlpha > 0.002) color = layerColor(nearUv, 2, lod) * nearAlpha;
    if (nearAlpha < 0.998) {
      float shadow = u_layer_bands.w * smoothstep(u_layer_bands.x - soft, u_layer_bands.x + soft, layerDepth(vFarShadow.zw, lod));
      vec2 midUv = vNearMid.zw;
      float midDepth = layerDepth(midUv, lod);
      float midAlpha = smoothstep(u_layer_bands.y - soft, u_layer_bands.y + soft, midDepth) * (1.0 - smoothstep(u_layer_bands.x - soft, u_layer_bands.x + soft, midDepth));
      vec3 behind = vec3(0.0);
      if (midAlpha > 0.002) behind = layerColor(midUv, 1, lod) * midAlpha;
      if (midAlpha < 0.998) {
${footage ? FOOTAGE_FAR : COVER_FAR}        behind += far * (1.0 - midAlpha);
      }
      color += behind * (1.0 - shadow) * (1.0 - nearAlpha);
    }
    return vec4(color, 1.0);
  }
`

const depth = {
  id: 'depth',
  framing: false,
  needsDepth: true,
  vertex: {
    head: `
  uniform vec4 u_layers[3];
  uniform vec2 u_layer_shadow;
  varying vec4 vNearMid;
  varying vec4 vFarShadow;

  vec2 layerAt(vec2 local, vec4 layer, mat2 rotationMatrix, vec2 shift) {
    vec2 framed = (getCoverUV(local, u_canvas_resolution, u_tex_resolution) - 0.5) / (u_frame_scale * layer.z) + u_frame_offset + layer.xy;
    return rotationMatrix * (framed - shift) / u_scale + 0.5;
  }
`,
    main: `    vec2 shift = u_parallax / u_canvas_resolution + glitchOffset;
    vNearMid = vec4(layerAt(uv, u_layers[2], rotationMatrix, shift), layerAt(uv, u_layers[1], rotationMatrix, shift));
    vFarShadow = vec4(layerAt(uv, u_layers[0], rotationMatrix, shift), layerAt(uv - u_layer_shadow, u_layers[2], rotationMatrix, shift));
`,
  },
  glsl: depthGlsl(false),
  uniforms: () => ({
    u_layers: { value: [new Vector4(0, 0, 1, 0), new Vector4(0, 0, 1, 0), new Vector4(0, 0, 1, 0)] },
    u_layer_grade: { value: [new Vector4(1, 0, 1, 1), new Vector4(1, 0, 1, 1), new Vector4(1, 0, 1, 1)] },
    u_layer_tone: { value: [new Matrix3(), new Matrix3(), new Matrix3()] },
    u_layer_bands: { value: new Vector4(0.66, 0.33, 0.02, 0) },
    u_layer_shadow: { value: new Vector2(0, 0) },
  }),
  signature: ['u_layers', 'u_layer_grade', 'u_layer_bands', 'u_layer_shadow'],

  variant(rand, level, prefer = {}) {
    return {
      motion: prefer.motion || pickWeighted(rand, MOTIONS),
      grade: pickWeighted(rand, GRADES),
      focus: level === 'low' ? 'subject' : pickWeighted(rand, FOCUS_MODES),
      shadow: 0.3 + rand() * 0.25,
      direction: rand() < 0.5 ? -1 : 1,
    }
  },

  frame(shot, ctx, u) {
    const v = shot.variant
    const bands = ctx.bands
    if (!bands) return
    const span = Math.max(1, shot.end - shot.start)
    const p = Math.min(1, Math.max(0, (ctx.t - shot.start) / span))
    const sep = 0.35 + 0.65 * ctx.energy + 0.9 * ctx.depthSwell + 0.25 * ctx.pulse
    const arc = Math.sin((p - 0.5) * Math.PI) * v.direction
    let zooms = SPREAD_ZOOMS
    let slideX = STILL_SLIDE
    let slideY = STILL_SLIDE
    if (v.motion === 'dolly') {
      zooms = [1 + 0.22 * p, 1.08 + 0.04 * p, 1.2 - 0.12 * p]
    } else if (v.motion === 'track') {
      const s = (p - 0.5) * v.direction
      zooms = [1.01, 1.04, 1.08 + 0.02 * sep]
      slideX = [-0.03 * s, 0.02 * s, 0.05 * s]
    } else if (v.motion === 'orbit') {
      slideX = [-0.035 * arc, 0.012 * arc, 0.05 * arc]
    } else if (v.motion === 'crane') {
      slideY = [-0.03 * arc, 0.01 * arc, 0.045 * arc]
    } else {
      zooms = [1, 1.015 + 0.02 * sep, 1.03 + 0.05 * sep]
    }
    const tilt = ctx.tilt
    const grades = GRADE_LAYERS[v.grade]
    const focal = layerFocus(v, ctx, bands.layers)
    const aperture = v.focus === 'deep' ? 0 : APERTURE * (1 + 0.3 * ctx.pulse + ctx.aperture)
    for (let k = 0; k < 3; k++) {
      const away = bands.layers[k] - focal
      const blur = Math.min(MAX_LAYER_BLUR, Math.max(0, (Math.abs(away) - FOCUS_RANGE) * aperture))
      u.u_layers.value[k].set(
        slideX[k] + tilt.x * TILT[k] + ctx.orbit.x * away,
        slideY[k] + tilt.y * TILT[k] + ctx.orbit.y * away,
        zooms[k] * (1 + ctx.dolly * away),
        blur
      )
      const [hue, brightness, saturation] = grades[k]
      const angle = ctx.hue + hue * DEG
      u.u_layer_grade.value[k].set(Math.cos(angle), Math.sin(angle), brightness, saturation)
      gradeMatrix(u.u_layer_tone.value[k], u.u_brightness.value, u.u_contrast.value, u.u_saturation.value, angle)
    }
    u.u_layer_bands.value.set(bands.near, bands.mid, bands.soft, v.shadow)
    const reach = SHADOW_REACH * (1 + sep)
    u.u_layer_shadow.value.set(reach / ctx.aspect, -reach)
  },
}

export default depth

export const depthFootage = {
  ...depth,
  id: 'depthFootage',
  framing: true,
  footage: true,
  glsl: depthGlsl(true),
  signature: [...depth.signature, ...FOOTAGE_SIGNATURE],
}
