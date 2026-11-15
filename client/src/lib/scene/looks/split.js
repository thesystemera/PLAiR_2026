import { Vector2, Vector4 } from 'three'
import { pickWeighted } from '../random'

const TILES = 4
const DEG = Math.PI / 180
const LAYOUTS = [['two', 3], ['three', 2], ['grid', 2]]
const HUES = [
  [[0, 0, 0, 0], 2],
  [[0, 180, 0, 180], 1],
  [[0, 120, 240, 60], 2],
  [[-35, 35, -35, 35], 2],
]
const GUTTER = 0.004
const DRIFT = 0.004
const CHASE_DIM = 0.5

function splitGrid(layout, aspect) {
  if (layout === 'grid') return [2, 2]
  const n = layout === 'three' ? 3 : 2
  return aspect < 1 ? [1, n] : [n, 1]
}

const reach = zoom => Math.max(0, 0.5 - 0.5 / zoom) * 0.9

export default {
  id: 'split',
  framing: true,
  glsl: `
  uniform vec2 u_split_grid;
  uniform float u_split_gutter;
  uniform vec4 u_tile_crop[${TILES}];
  uniform vec2 u_tile_hue[${TILES}];
  uniform float u_tile_level[${TILES}];

  float gutterMask(vec2 cellPos, vec2 grid) {
    if (u_split_gutter <= 0.0) return 1.0;
    vec2 lines = clamp(floor(cellPos + 0.5), vec2(1.0), max(grid - 1.0, vec2(1.0)));
    vec2 d = abs(cellPos - lines) / grid * vec2(u_canvas_resolution.x / u_canvas_resolution.y, 1.0);
    float edge = min(grid.x > 1.5 ? d.x : 1.0, grid.y > 1.5 ? d.y : 1.0);
    float pixel = 1.0 / u_canvas_resolution.y;
    return smoothstep(u_split_gutter * 0.5 - pixel, u_split_gutter * 0.5 + pixel, edge);
  }

  vec4 lookImage() {
    vec2 grid = u_split_grid;
    vec2 cellPos = vUv * grid;
    vec2 cell = min(floor(cellPos), grid - 1.0);
    vec2 local = cellPos - cell;
    int tile = int(cell.y * grid.x + cell.x + 0.5);
    vec4 crop = u_tile_crop[tile];
    if (crop.w > 0.5) local.x = 1.0 - local.x;
    vec2 uv = framedUv(local, u_canvas_resolution / grid, crop.xy, crop.z);
    vec4 color = chromaticImage(uv, getBlurAmount(uv), u_tile_hue[tile]);
    color.rgb *= u_tile_level[tile] * gutterMask(cellPos, grid);
    return color;
  }
`,
  uniforms: () => ({
    u_split_grid: { value: new Vector2(1, 1) },
    u_split_gutter: { value: 0 },
    u_tile_crop: { value: Array.from({ length: TILES }, () => new Vector4(0, 0, 1, 0)) },
    u_tile_hue: { value: Array.from({ length: TILES }, () => new Vector2(1, 0)) },
    u_tile_level: { value: new Array(TILES).fill(1) },
  }),
  signature: ['u_split_grid', 'u_split_gutter', 'u_tile_crop', 'u_tile_hue', 'u_tile_level'],

  variant(rand, level) {
    const layout = pickWeighted(rand, LAYOUTS)
    const mirror = rand() < 0.4
    const crops = []
    for (let i = 0; i < TILES; i++) {
      if (mirror && i % 2 === 1) {
        crops.push(crops[i - 1])
        continue
      }
      const zoom = i === 0 && !mirror ? 1 : 1.2 + rand() * 0.8
      const limit = reach(zoom)
      crops.push({
        zoom,
        x: (rand() * 2 - 1) * limit,
        y: (rand() * 2 - 1) * limit,
        dx: (rand() * 2 - 1) * DRIFT,
        dy: (rand() * 2 - 1) * DRIFT,
      })
    }
    return {
      layout,
      mirror,
      crops,
      hues: pickWeighted(rand, HUES),
      gutter: rand() < 0.5 ? GUTTER : 0,
      chase: level === 'high' && rand() < 0.6,
    }
  },

  frame(shot, ctx, u) {
    const v = shot.variant
    const [cols, rows] = splitGrid(v.layout, ctx.aspect)
    const count = cols * rows
    const since = Math.max(0, ctx.t - shot.start)
    u.u_split_grid.value.set(cols, rows)
    u.u_split_gutter.value = v.gutter
    const lit = ctx.beatsSince % count
    for (let i = 0; i < TILES; i++) {
      const crop = v.crops[i]
      const zoom = crop.zoom * (1 + 0.015 * ctx.pulse)
      const limit = reach(zoom)
      const x = Math.max(-limit, Math.min(limit, crop.x + crop.dx * since))
      const y = Math.max(-limit, Math.min(limit, crop.y + crop.dy * since))
      u.u_tile_crop.value[i].set(x, y, zoom, v.mirror && i % 2 === 1 ? 1 : 0)
      const angle = ctx.hue + v.hues[i] * DEG
      u.u_tile_hue.value[i].set(Math.cos(angle), Math.sin(angle))
      u.u_tile_level.value[i] = v.chase
        ? (i === lit ? 1 + 0.2 * ctx.pulse : CHASE_DIM)
        : 1 + 0.1 * ctx.pulse
    }
  },
}
