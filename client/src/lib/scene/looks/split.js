import { Vector2, Vector4 } from 'three'
import { pickWeighted, seededRandom } from '../random'
import { focusTarget } from '../focus'

const TILES = 8
const DEG = Math.PI / 180
const LAYOUTS = [['wipe', 3], ['quad', 2], ['strips', 2], ['columns', 2], ['slices', 2], ['blinds', 2], ['boxes', 3], ['tunnel', 2]]
const HUES = [
  [[0, 0, 0, 0], 2],
  [[0, 180, 0, 180], 1],
  [[0, 120, 240, 60], 2],
  [[-35, 35, -35, 35], 2],
]
const BORDERS = [[[0.003, 0], 2], [[0.006, 0], 2], [[0.004, 0.92], 1]]
const WIPE_STOPS = [0.75, 0.25, 0.5, 0.66, 0.33]
const QUAD_POINTS = [[0.5, 0.5], [0.72, 0.3], [0.3, 0.66], [0.66, 0.72], [0.34, 0.34]]
const STRIPS = 3
const MOVE_S = 0.45
const POP_S = 0.28
const BOX_SET_BARS = 2
const DRIFT = 0.004
const CHASE_DIM = 0.45
const TUNNEL_STEP = 0.7
const MIRROR = 1
const WHOLE = 2

const clamp01 = x => Math.min(1, Math.max(0, x))
const easeInOut = x => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2)
const easeOutBack = x => 1 + 2.70158 * Math.pow(x - 1, 3) + 1.70158 * Math.pow(x - 1, 2)
const reach = zoom => Math.max(0, 0.5 - 0.5 / zoom) * 0.9
const lerp = (a, b, k) => a + (b - a) * k

function sequence(rand, values, length) {
  const out = []
  while (out.length < length) {
    const value = values[Math.floor(rand() * values.length)]
    if (value !== out[out.length - 1]) out.push(value)
  }
  return out
}

function crop(rand, zoomMin, zoomMax) {
  const zoom = zoomMin + rand() * (zoomMax - zoomMin)
  const limit = reach(zoom)
  return {
    zoom,
    x: (rand() * 2 - 1) * limit,
    y: (rand() * 2 - 1) * limit,
    dx: (rand() * 2 - 1) * DRIFT,
    dy: (rand() * 2 - 1) * DRIFT,
  }
}

function beatTime(ctx, index) {
  const beats = ctx.beats
  if (!beats?.length) return ctx.t
  return beats[Math.max(0, Math.min(beats.length - 1, index))]
}

function barProgress(ctx) {
  const bar = Math.floor(ctx.beatsSince / 4)
  return { bar, k: easeInOut(clamp01((ctx.t - beatTime(ctx, ctx.firstBeat + bar * 4)) / MOVE_S)) }
}

function along(rects, i, portrait, a0, a1) {
  if (portrait) rects[i].set(0, 1 - a1, 1, 1 - a0)
  else rects[i].set(a0, 0, a1, 1)
}

function sizesFor(count, wide, widest) {
  return Array.from({ length: count }, (_, i) => (i === wide % count ? widest : (1 - widest) / (count - 1)))
}

function layoutPanels(v, ctx, rects) {
  const portrait = ctx.aspect < 1
  const { bar, k } = barProgress(ctx)
  if (v.layout === 'wipe') {
    const stops = v.stops
    const d = lerp(bar === 0 ? 1 : stops[(bar - 1) % stops.length], stops[bar % stops.length], k)
    along(rects, 0, portrait, 0, d)
    along(rects, 1, portrait, d, 1)
    return 2
  }
  if (v.layout === 'quad') {
    const points = v.points
    const from = bar === 0 ? [1, 1] : points[(bar - 1) % points.length]
    const to = points[bar % points.length]
    const cx = lerp(from[0], to[0], k)
    const cy = lerp(from[1], to[1], k)
    rects[0].set(0, 0, cx, cy)
    rects[1].set(cx, 0, 1, cy)
    rects[2].set(0, cy, cx, 1)
    rects[3].set(cx, cy, 1, 1)
    return 4
  }
  const count = v.layout === 'columns' ? v.count : STRIPS
  const widest = count === 3 ? 0.5 : 0.4
  const from = bar === 0 ? Array.from({ length: count }, () => 1 / count) : sizesFor(count, bar - 1, widest)
  const to = sizesFor(count, bar, widest)
  let edge = 0
  for (let i = 0; i < count; i++) {
    const size = lerp(from[i], to[i], k)
    if (v.layout === 'columns') rects[i].set(edge, 0, edge + size, 1)
    else along(rects, i, portrait, edge, edge + size)
    edge += size
  }
  return count
}

function layoutSlices(v, ctx, rects, crops) {
  const count = v.count
  const vertical = v.layout === 'slices'
  const wave = ctx.t * v.speed
  const jolt = seededRandom(v.seed + ctx.beatsSince * 31)
  const jumped = Math.floor(jolt() * count)
  const jump = (jolt() * 2 - 1) * 0.08 * ctx.pulse
  for (let i = 0; i < count; i++) {
    const a = i / count
    const b = (i + 1) / count
    if (vertical) rects[i].set(a, 0, b, 1)
    else rects[i].set(0, a, 1, b)
    const shift = v.amplitude * Math.sin(i * 1.7 + wave) + (i === jumped ? jump : 0)
    crops[i] = { zoom: v.zoom, x: vertical ? 0 : shift, y: vertical ? shift : 0, dx: 0, dy: 0, flags: WHOLE | (v.mirror && i % 2 ? MIRROR : 0) }
  }
  return count
}

function layoutTunnel(v, ctx, rects, crops, start) {
  const beats = ctx.beats
  const period = beats?.length > 1 ? (beats[beats.length - 1] - beats[0]) / (beats.length - 1) : 0.5
  const cycles = Math.max(0, ctx.t - start) / (period * 2)
  const phase = cycles - Math.floor(cycles)
  const base = Math.floor(cycles)
  for (let i = 0; i < v.count; i++) {
    const half = 0.5 * Math.pow(TUNNEL_STEP, i - phase)
    const halfWidth = half / ctx.aspect
    rects[i].set(0.5 - halfWidth, 0.5 - half, 0.5 + halfWidth, 0.5 + half)
    const level = i + base
    crops[i] = { zoom: 1, x: 0, y: 0, dx: 0, dy: 0, flags: v.mirror && level % 2 ? MIRROR : 0, hue: level }
  }
  return v.count
}

function layoutBoxes(v, ctx, rects, crops) {
  const set = Math.floor(ctx.beatsSince / (4 * BOX_SET_BARS))
  const rand = seededRandom(v.seed + set * 7919)
  const count = 2 + Math.floor(rand() * 3)
  const firstBeat = ctx.firstBeat + set * 4 * BOX_SET_BARS
  for (let i = 0; i < count; i++) {
    const halfHeight = 0.11 + rand() * 0.1
    const halfWidth = Math.min(0.48, halfHeight * (0.8 + rand() * 0.8) / ctx.aspect)
    const cx = halfWidth + rand() * (1 - 2 * halfWidth)
    const cy = halfHeight + rand() * (1 - 2 * halfHeight)
    crops[i] = crop(rand, 1.3, 2.4)
    const start = beatTime(ctx, firstBeat + i)
    const s = ctx.t < start ? 0 : easeOutBack(clamp01((ctx.t - start) / POP_S))
    rects[i].set(cx - halfWidth * s, cy - halfHeight * s, cx + halfWidth * s, cy + halfHeight * s)
  }
  return count
}

export default {
  id: 'split',
  framing: true,
  glsl: `
  uniform vec4 u_tile_rect[${TILES}];
  uniform vec4 u_tile_crop[${TILES}];
  uniform vec2 u_tile_hue[${TILES}];
  uniform float u_tile_level[${TILES}];
  uniform float u_tile_focus[${TILES}];
  uniform vec4 u_split_frame;

  vec4 lookImage() {
    vec2 local = vUv;
    vec2 size = vec2(1.0);
    vec4 frame = vec4(0.0, 0.0, 1.0, 1.0);
    vec4 crop = vec4(0.0, 0.0, 1.0, 0.0);
    vec2 hue = vHue;
    float level = u_split_frame.x;
    float edge = 1.0;
    focalDepth = u_focal_depth;
    float aspect = u_canvas_resolution.x / u_canvas_resolution.y;
    for (int i = 0; i < ${TILES}; i++) {
      vec4 rect = u_tile_rect[i];
      if (vUv.x >= rect.x && vUv.x < rect.z && vUv.y >= rect.y && vUv.y < rect.w) {
        frame = rect;
        size = rect.zw - rect.xy;
        local = (vUv - rect.xy) / size;
        crop = u_tile_crop[i];
        hue = u_tile_hue[i];
        level = u_tile_level[i];
        focalDepth = u_tile_focus[i];
        vec2 inside = min(vUv - rect.xy, rect.zw - vUv) * vec2(aspect, 1.0);
        edge = min(inside.x, inside.y);
      }
    }
    bool whole = crop.w > 1.5;
    vec2 base = whole ? vUv : local;
    if (mod(crop.w, 2.0) > 0.5) base.x = whole ? frame.x + frame.z - base.x : 1.0 - base.x;
    vec2 uv = framedUv(base, whole ? u_canvas_resolution : u_canvas_resolution * size, crop.xy, crop.z);
    vec4 color = chromaticImage(uv, getBlurAmount(uv), hue);
    color.rgb *= level;
    float border = u_split_frame.y;
    if (border > 0.0) {
      float pixel = 1.0 / u_canvas_resolution.y;
      color.rgb = mix(vec3(u_split_frame.z), color.rgb, smoothstep(border - pixel, border + pixel, edge));
    }
    return color;
  }
`,
  uniforms: () => ({
    u_tile_rect: { value: Array.from({ length: TILES }, () => new Vector4(0, 0, 0, 0)) },
    u_tile_crop: { value: Array.from({ length: TILES }, () => new Vector4(0, 0, 1, 0)) },
    u_tile_hue: { value: Array.from({ length: TILES }, () => new Vector2(1, 0)) },
    u_tile_level: { value: new Array(TILES).fill(1) },
    u_tile_focus: { value: new Array(TILES).fill(0.5) },
    u_split_frame: { value: new Vector4(0, 0, 0, 0) },
  }),
  signature: ['u_tile_rect', 'u_tile_crop', 'u_tile_hue', 'u_tile_level', 'u_tile_focus', 'u_split_frame'],

  variant(rand, level, prefer = {}) {
    const layout = prefer.layout || pickWeighted(rand, LAYOUTS)
    const mirror = layout !== 'boxes' && rand() < 0.4
    const crops = []
    for (let i = 0; i < TILES; i++) {
      if (mirror && i % 2 === 1) crops.push(crops[i - 1])
      else crops.push(i === 0 && !mirror ? crop(rand, 1, 1) : crop(rand, 1.2, 2))
    }
    const [border, tone] = pickWeighted(rand, BORDERS)
    const sliced = layout === 'slices' || layout === 'blinds'
    return {
      layout,
      mirror,
      crops,
      hues: pickWeighted(rand, HUES),
      border: sliced ? 0 : border,
      tone,
      back: layout === 'boxes' && rand() < 0.5 ? 0.3 : 0,
      stops: sequence(rand, WIPE_STOPS, 6),
      points: sequence(rand, QUAD_POINTS, 6),
      count: layout === 'columns' ? 3 + Math.floor(rand() * 2) : sliced ? 6 + Math.floor(rand() * 3) : layout === 'tunnel' ? 5 : 0,
      speed: 1.5 + rand() * 2.5,
      amplitude: 0.03 + rand() * 0.05,
      zoom: 1.05 + rand() * 0.15,
      seed: Math.floor(rand() * 1e9),
      chase: level === 'high' && layout !== 'wipe' && layout !== 'tunnel' && rand() < 0.6,
    }
  },

  frame(shot, ctx, u) {
    const v = shot.variant
    const rects = u.u_tile_rect.value
    const crops = v.crops.slice()
    let count
    if (v.layout === 'boxes') count = layoutBoxes(v, ctx, rects, crops)
    else if (v.layout === 'slices' || v.layout === 'blinds') count = layoutSlices(v, ctx, rects, crops)
    else if (v.layout === 'tunnel') count = layoutTunnel(v, ctx, rects, crops, shot.start)
    else count = layoutPanels(v, ctx, rects)
    for (let i = count; i < TILES; i++) rects[i].set(0, 0, 0, 0)
    u.u_split_frame.value.set(v.back, v.border, v.tone, 0)
    const since = Math.max(0, ctx.t - shot.start)
    const lit = ctx.beatsSince % Math.max(1, count)
    for (let i = 0; i < TILES; i++) {
      const c = crops[i]
      const zoom = c.zoom * (1 + 0.015 * ctx.pulse)
      const limit = reach(zoom)
      const x = c.flags & WHOLE ? c.x : Math.max(-limit, Math.min(limit, c.x + c.dx * since))
      const y = c.flags & WHOLE ? c.y : Math.max(-limit, Math.min(limit, c.y + c.dy * since))
      const flags = c.flags ?? (v.mirror && i % 2 === 1 ? MIRROR : 0)
      u.u_tile_crop.value[i].set(x, y, zoom, flags)
      const angle = ctx.hue + v.hues[(c.hue ?? i) % v.hues.length] * DEG
      u.u_tile_hue.value[i].set(Math.cos(angle), Math.sin(angle))
      u.u_tile_level.value[i] = v.chase ? (i === lit ? 1 + 0.2 * ctx.pulse : CHASE_DIM) : 1 + 0.1 * ctx.pulse
      u.u_tile_focus.value[i] = ctx.grid ? focusTarget(ctx.grid, ctx.frame, ctx.flipY, x, y, zoom) : 0.5
    }
  },
}
