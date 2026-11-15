import { Matrix3, Vector2, Vector3, Vector4 } from 'three'
import { pickWeighted, seededRandom } from '../random'
import { focusTarget } from '../focus'
import { gradeMatrix } from '../grade'
import { lowerBound } from '../../sceneEffects'
import { FRAMING_FUNCTIONS } from '../glsl/background'

const TILES = 8
const DEG = Math.PI / 180
const LAYOUTS = [['wipe', 3], ['quad', 2], ['strips', 2], ['columns', 2], ['slices', 2], ['blinds', 2], ['boxes', 3], ['tunnel', 2], ['curtain', 2], ['mosaic', 2]]
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
const CURTAIN_OPENINGS = [0.34, 0.6, 0.18, 0.76, 0.46]
const MOSAIC_STAGGER_BEATS = 0.5
const MOSAIC_FLIP = 0.3
const MOSAIC_BACK = 0.22
const INK = { share: 0.65, light: 0.7, chroma: 0.6 }
const MAX_EDGES = 2 * TILES + 2
export const MAX_TILE_VERTICES = (MAX_EDGES - 1) * (MAX_EDGES - 1) * 6
const QUAD_RIGHT = [0, 1, 1, 0, 1, 0]
const QUAD_TOP = [0, 0, 1, 0, 1, 1]
const columnEdges = new Float64Array(MAX_EDGES)
const rowEdges = new Float64Array(MAX_EDGES)
const turns = new Int16Array(TILES)
const flipping = new Uint8Array(TILES)
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

function layoutCurtain(v, ctx, rects, crops) {
  const { bar, k } = barProgress(ctx)
  const openings = v.openings
  const width = lerp(bar === 0 ? 0 : openings[(bar - 1) % openings.length], openings[bar % openings.length], k)
  const left = 0.5 - width / 2
  rects[0].set(0, 0, left, 1)
  rects[1].set(left, 0, 1 - left, 1)
  rects[2].set(1 - left, 0, 1, 1)
  crops[0] = { ...v.crops[1], flags: 0 }
  crops[1] = { ...v.crops[0], flags: 0 }
  crops[2] = { ...v.crops[1], flags: MIRROR }
  return 3
}

function layoutMosaic(v, ctx, rects, crops, start) {
  const columns = ctx.aspect < 1 ? 2 : 4
  const rows = TILES / columns
  const beats = ctx.beats
  const period = beats?.length > 1 ? (beats[beats.length - 1] - beats[0]) / (beats.length - 1) : 0.5
  const since = Math.max(0, ctx.t - start)
  const bar = Math.floor(ctx.beatsSince / 4)
  const barStart = beatTime(ctx, ctx.firstBeat + bar * 4)
  turns.fill(0)
  flipping.fill(0)
  for (let b = 1; b <= bar; b++) {
    const roll = seededRandom(v.seed + b * 131)
    for (let i = 0; i < TILES; i++) {
      if (roll() >= MOSAIC_FLIP) continue
      turns[i]++
      if (b === bar) flipping[i] = 1
    }
  }
  for (let i = 0; i < TILES; i++) {
    const born = v.order[i] * period * MOSAIC_STAGGER_BEATS
    let size = since < born ? 0 : easeOutBack(clamp01((since - born) / POP_S))
    if (flipping[i] && since > born + POP_S) size = easeOutBack(clamp01((ctx.t - barStart) / POP_S))
    const x = ((i % columns) + 0.5) / columns
    const y = (Math.floor(i / columns) + 0.5) / rows
    const halfWidth = (0.5 / columns) * size
    const halfHeight = (0.5 / rows) * size
    rects[i].set(x - halfWidth, y - halfHeight, x + halfWidth, y + halfHeight)
    crops[i] = { zoom: v.zoom, x: 0, y: 0, dx: 0, dy: 0, flags: WHOLE | (v.mirror && i % 2 ? MIRROR : 0), hue: i + turns[i] }
  }
  return TILES
}

function inkColor(out, degrees) {
  const h = (((degrees % 360) + 360) % 360) / 60
  const c = INK.chroma
  const x = c * (1 - Math.abs((h % 2) - 1))
  const m = INK.light - c / 2
  if (h < 1) return out.set(c + m, x + m, m)
  if (h < 2) return out.set(x + m, c + m, m)
  if (h < 3) return out.set(m, c + m, x + m)
  if (h < 4) return out.set(m, x + m, c + m)
  if (h < 5) return out.set(x + m, m, c + m)
  return out.set(c + m, m, x + m)
}

function layoutBoxes(v, ctx, rects, crops, sung) {
  const set = Math.floor(ctx.beatsSince / (4 * BOX_SET_BARS))
  const rand = seededRandom(v.seed + set * 7919)
  const count = 2 + Math.floor(rand() * 3)
  const firstBeat = ctx.firstBeat + set * 4 * BOX_SET_BARS
  const setStart = beatTime(ctx, firstBeat)
  const setEnd = beatTime(ctx, firstBeat + 4 * BOX_SET_BARS)
  const words = sung ? ctx.words : null
  const firstWord = words ? lowerBound(words, setStart - 0.05) : 0
  for (let i = 0; i < count; i++) {
    const halfHeight = 0.11 + rand() * 0.1
    const halfWidth = Math.min(0.48, halfHeight * (0.8 + rand() * 0.8) / ctx.aspect)
    const cx = halfWidth + rand() * (1 - 2 * halfWidth)
    const cy = halfHeight + rand() * (1 - 2 * halfHeight)
    crops[i] = crop(rand, 1.3, 2.4)
    const word = i > 0 && words && firstWord + i - 1 < words.length && words[firstWord + i - 1] < setEnd ? words[firstWord + i - 1] : null
    const start = word ?? beatTime(ctx, firstBeat + i)
    const s = ctx.t < start ? 0 : easeOutBack(clamp01((ctx.t - start) / POP_S))
    rects[i].set(cx - halfWidth * s, cy - halfHeight * s, cx + halfWidth * s, cy + halfHeight * s)
  }
  return count
}

function sortedEdges(out, rects, count, low, high) {
  let n = 0
  out[n++] = 0
  out[n++] = 1
  for (let i = 0; i < count; i++) {
    const from = rects[i][low]
    const to = rects[i][high]
    if (from > 0 && from < 1) out[n++] = from
    if (to > 0 && to < 1) out[n++] = to
  }
  const edges = out.subarray(0, n).sort()
  let unique = 1
  for (let i = 1; i < n; i++) if (edges[i] > edges[unique - 1]) edges[unique++] = edges[i]
  return unique
}

function tileOver(rects, count, x, y) {
  for (let i = count - 1; i >= 0; i--) {
    const rect = rects[i]
    if (x >= rect.x && x < rect.z && y >= rect.y && y < rect.w) return i
  }
  return TILES
}

export function tileQuads(rects, count, positions, uvs, tiles) {
  const columns = sortedEdges(columnEdges, rects, count, 'x', 'z')
  const rows = sortedEdges(rowEdges, rects, count, 'y', 'w')
  let vertex = 0
  const quad = (x0, y0, x1, y1, tile) => {
    for (let corner = 0; corner < 6; corner++) {
      const x = QUAD_RIGHT[corner] ? x1 : x0
      const y = QUAD_TOP[corner] ? y1 : y0
      positions[vertex * 3] = x * 2 - 1
      positions[vertex * 3 + 1] = y * 2 - 1
      positions[vertex * 3 + 2] = 0
      uvs[vertex * 2] = x
      uvs[vertex * 2 + 1] = y
      tiles[vertex++] = tile
    }
  }
  for (let j = 0; j < rows - 1; j++) {
    const y0 = rowEdges[j]
    const y1 = rowEdges[j + 1]
    let start = 0
    let run = -1
    for (let i = 0; i < columns; i++) {
      const tile = i < columns - 1 ? tileOver(rects, count, (columnEdges[i] + columnEdges[i + 1]) / 2, (y0 + y1) / 2) : -1
      if (tile === run) continue
      if (run >= 0) quad(columnEdges[start], y0, columnEdges[i], y1, run)
      start = i
      run = tile
    }
  }
  return vertex
}

export default {
  id: 'split',
  framing: true,
  glsl: `
  uniform vec4 u_tile_rect[${TILES}];
  uniform vec4 u_tile_crop[${TILES + 1}];
  uniform mat3 u_tile_grade[${TILES + 1}];
  uniform float u_tile_level[${TILES + 1}];
  uniform float u_tile_focus[${TILES + 1}];
  uniform vec3 u_tile_ink[${TILES + 1}];
  uniform vec4 u_split_frame;
  uniform int u_tile_count;

  vec4 lookImage() {
    int tile = ${TILES};
    for (int i = 0; i < u_tile_count; i++) {
      vec4 rect = u_tile_rect[i];
      if (vUv.x >= rect.x && vUv.x < rect.z && vUv.y >= rect.y && vUv.y < rect.w) tile = i;
    }
    bool framed = tile < ${TILES};
    vec4 frame = vec4(0.0, 0.0, 1.0, 1.0);
    if (framed) frame = u_tile_rect[tile];
    vec2 size = frame.zw - frame.xy;
    vec2 local = (vUv - frame.xy) / size;
    vec4 crop = u_tile_crop[tile];
    focalDepth = u_tile_focus[tile];
    lyricInk = u_tile_ink[tile];
    float aspect = u_canvas_resolution.x / u_canvas_resolution.y;
    vec2 inside = min(vUv - frame.xy, frame.zw - vUv) * vec2(aspect, 1.0);
    float edge = framed ? min(inside.x, inside.y) : 1.0;
    bool whole = crop.w > 1.5;
    vec2 base = whole ? vUv : local;
    if (mod(crop.w, 2.0) > 0.5) base.x = whole ? frame.x + frame.z - base.x : 1.0 - base.x;
    vec2 uv = framedUv(base, whole ? u_canvas_resolution : u_canvas_resolution * size, crop.xy, crop.z);
    vec4 color = chromaticImage(depthView(uv, vec2(0.0)), u_tile_grade[tile]);
    color.rgb *= u_tile_level[tile];
    float border = u_split_frame.y;
    if (border > 0.0) {
      float pixel = 1.0 / u_canvas_resolution.y;
      color.rgb = mix(vec3(u_split_frame.z), color.rgb, smoothstep(border - pixel, border + pixel, edge));
    }
    return color;
  }
`,
  tiled: {
    vertexHead: `
  attribute float tile;
  uniform vec2 u_fx_rot;
  uniform vec4 u_tile_rect[${TILES}];
  uniform vec4 u_tile_crop[${TILES + 1}];
  uniform mat3 u_tile_grade[${TILES + 1}];
  uniform float u_tile_level[${TILES + 1}];
  uniform float u_tile_focus[${TILES + 1}];
  uniform vec3 u_tile_ink[${TILES + 1}];
  varying vec2 vTileUv;
  varying vec4 vTileRect;
  varying vec3 vTileGrade0;
  varying vec3 vTileGrade1;
  varying vec3 vTileGrade2;
  varying vec3 vTileLook;
  varying vec3 vTileInk;
${FRAMING_FUNCTIONS}`,
    vertexMain: `    int tileIndex = int(tile + 0.5);
    bool tileFramed = tileIndex < ${TILES};
    vec4 tileFrame = vec4(0.0, 0.0, 1.0, 1.0);
    if (tileFramed) tileFrame = u_tile_rect[tileIndex];
    vec2 tileSize = tileFrame.zw - tileFrame.xy;
    vec4 tileCrop = u_tile_crop[tileIndex];
    bool tileWhole = tileCrop.w > 1.5;
    vec2 tileBase = tileWhole ? uv : (uv - tileFrame.xy) / tileSize;
    if (mod(tileCrop.w, 2.0) > 0.5) tileBase.x = tileWhole ? tileFrame.x + tileFrame.z - tileBase.x : 1.0 - tileBase.x;
    vTileUv = framedUv(tileBase, tileWhole ? u_canvas_resolution : u_canvas_resolution * tileSize, tileCrop.xy, tileCrop.z);
    vTileRect = tileFrame;
    mat3 tileGrade = u_tile_grade[tileIndex];
    vTileGrade0 = tileGrade[0];
    vTileGrade1 = tileGrade[1];
    vTileGrade2 = tileGrade[2];
    vTileLook = vec3(u_tile_level[tileIndex], u_tile_focus[tileIndex], tileFramed ? 1.0 : 0.0);
    vTileInk = u_tile_ink[tileIndex];
`,
    glsl: `
  uniform vec4 u_split_frame;
  varying vec2 vTileUv;
  varying vec4 vTileRect;
  varying vec3 vTileGrade0;
  varying vec3 vTileGrade1;
  varying vec3 vTileGrade2;
  varying vec3 vTileLook;
  varying vec3 vTileInk;

  vec4 lookImage() {
    focalDepth = vTileLook.y;
    lyricInk = vTileInk;
    vec4 color = chromaticImage(depthView(vTileUv, vec2(0.0)), mat3(vTileGrade0, vTileGrade1, vTileGrade2));
    color.rgb *= vTileLook.x;
    float border = u_split_frame.y;
    if (border > 0.0 && vTileLook.z > 0.5) {
      vec2 inside = min(vUv - vTileRect.xy, vTileRect.zw - vUv) * vec2(u_canvas_resolution.x / u_canvas_resolution.y, 1.0);
      float pixel = 1.0 / u_canvas_resolution.y;
      color.rgb = mix(vec3(u_split_frame.z), color.rgb, smoothstep(border - pixel, border + pixel, min(inside.x, inside.y)));
    }
    return color;
  }
`,
  },
  uniforms: () => ({
    u_tile_rect: { value: Array.from({ length: TILES }, () => new Vector4(0, 0, 0, 0)) },
    u_tile_crop: { value: Array.from({ length: TILES + 1 }, () => new Vector4(0, 0, 1, 0)) },
    u_tile_hue: { value: Array.from({ length: TILES }, () => new Vector2(1, 0)) },
    u_tile_grade: { value: Array.from({ length: TILES + 1 }, () => new Matrix3()) },
    u_tile_level: { value: new Array(TILES + 1).fill(1) },
    u_tile_focus: { value: new Array(TILES + 1).fill(0.5) },
    u_tile_ink: { value: Array.from({ length: TILES + 1 }, () => new Vector3(1, 1, 1)) },
    u_split_frame: { value: new Vector4(0, 0, 0, 0) },
    u_tile_count: { value: 0 },
  }),
  signature: ['u_tile_rect', 'u_tile_crop', 'u_tile_hue', 'u_tile_level', 'u_tile_focus', 'u_tile_ink', 'u_split_frame', 'u_tile_count'],

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
      back: layout === 'mosaic' ? MOSAIC_BACK : layout === 'boxes' && rand() < 0.5 ? 0.3 : 0,
      stops: sequence(rand, WIPE_STOPS, 6),
      points: sequence(rand, QUAD_POINTS, 6),
      openings: sequence(rand, CURTAIN_OPENINGS, 6),
      order: Array.from({ length: TILES }, (_, i) => [rand(), i]).sort((a, b) => a[0] - b[0]).map(([, i]) => i),
      ink: rand() < INK.share ? rand() * 360 : null,
      count: layout === 'columns' ? 3 + Math.floor(rand() * 2) : sliced ? 6 + Math.floor(rand() * 3) : layout === 'tunnel' ? 5 : 0,
      speed: 1.5 + rand() * 2.5,
      amplitude: 0.03 + rand() * 0.05,
      zoom: 1.05 + rand() * 0.15,
      seed: Math.floor(rand() * 1e9),
      chase: level === 'high' && layout !== 'wipe' && layout !== 'tunnel' && layout !== 'curtain' && rand() < 0.6,
    }
  },

  frame(shot, ctx, u) {
    const v = shot.variant
    const rects = u.u_tile_rect.value
    const crops = v.crops.slice()
    let count
    if (v.layout === 'boxes') count = layoutBoxes(v, ctx, rects, crops, shot.vocal)
    else if (v.layout === 'slices' || v.layout === 'blinds') count = layoutSlices(v, ctx, rects, crops)
    else if (v.layout === 'tunnel') count = layoutTunnel(v, ctx, rects, crops, shot.start)
    else if (v.layout === 'curtain') count = layoutCurtain(v, ctx, rects, crops)
    else if (v.layout === 'mosaic') count = layoutMosaic(v, ctx, rects, crops, shot.start)
    else count = layoutPanels(v, ctx, rects)
    for (let i = count; i < TILES; i++) rects[i].set(0, 0, 0, 0)
    u.u_tile_count.value = count
    u.u_split_frame.value.set(v.back, v.border, v.tone, 0)
    const brightness = u.u_brightness.value
    const contrast = u.u_contrast.value
    const saturation = u.u_saturation.value
    u.u_tile_grade.value[TILES].copy(u.u_grade.value)
    u.u_tile_level.value[TILES] = v.back
    u.u_tile_focus.value[TILES] = u.u_focal_depth.value
    const since = Math.max(0, ctx.t - shot.start)
    const lit = (shot.vocal && ctx.words?.length ? ctx.wordsSince : ctx.beatsSince) % Math.max(1, count)
    for (let i = 0; i < TILES; i++) {
      const c = crops[i]
      const zoom = c.zoom * (1 + 0.015 * ctx.pulse)
      const limit = reach(zoom)
      const x = c.flags & WHOLE ? c.x : Math.max(-limit, Math.min(limit, c.x + c.dx * since))
      const y = c.flags & WHOLE ? c.y : Math.max(-limit, Math.min(limit, c.y + c.dy * since))
      const flags = c.flags ?? (v.mirror && i % 2 === 1 ? MIRROR : 0)
      u.u_tile_crop.value[i].set(x, y, zoom, flags)
      const turn = v.hues[(c.hue ?? i) % v.hues.length]
      const angle = ctx.hue + turn * DEG
      if (v.ink === null) u.u_tile_ink.value[i].set(1, 1, 1)
      else inkColor(u.u_tile_ink.value[i], v.ink + turn + ((c.hue ?? i) % 2 ? 180 : 0))
      u.u_tile_hue.value[i].set(Math.cos(angle), Math.sin(angle))
      gradeMatrix(u.u_tile_grade.value[i], brightness, contrast, saturation, angle)
      u.u_tile_level.value[i] = v.chase ? (i === lit ? 1 + 0.2 * ctx.pulse : CHASE_DIM) : 1 + 0.1 * ctx.pulse
      u.u_tile_focus.value[i] = ctx.grid ? focusTarget(ctx.grid, ctx.frame, ctx.flipY, x, y, zoom) : 0.5
    }
  },
}
