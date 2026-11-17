import {
  BufferAttribute,
  BufferGeometry,
  CanvasTexture,
  ColorManagement,
  DataTexture,
  DynamicDrawUsage,
  Float32BufferAttribute,
  LinearFilter,
  LinearMipmapLinearFilter,
  LinearSRGBColorSpace,
  MathUtils,
  MirroredRepeatWrapping,
  Matrix3,
  Mesh,
  NoToneMapping,
  OrthographicCamera,
  PerspectiveCamera,
  PlaneGeometry,
  RepeatWrapping,
  RGBAFormat,
  Scene,
  ShaderMaterial,
  Vector2,
  Vector3,
  Vector4,
  VideoFrameTexture,
  VideoTexture,
  WebGLRenderer,
  WebGLRenderTarget,
} from 'three'
import { backgroundVertexShader, backgroundFragmentShader, sceneFragmentShader, sceneGlassFragmentShader, sceneGlassButtonFragmentShader, backdropFragmentShader, lookShaders } from './sceneShaders'
import { FOOTAGE_IDS, LOOK_IDS, createLookContext, directLook, lookById, lookSignature, lookUniforms, setLookUniforms } from './scene/looks'
import { createDirectorCursor, directorAt, planShots } from './scene/director'
import { applyFocus, createFocus } from './scene/focus'
import { createFx, setCamera, stepFx } from './scene/beatFx'
import { setGrade } from './scene/grade'
import { createSoftener } from './scene/softPyramid'
import { MAX_SLOTS, createFootageState, planFootage, setFootageUniforms, stepFootage } from './scene/footage'
import { lowerBound, numericAscending } from './sceneEffects'
import { artworkPixels, sampleBackgroundProbe } from './backgroundProbe'
import { addFrameWork, createGpuTimer, takeFrameWork, watchFrameStats } from './frameStats'
import { decodePack } from './packImage'
import { DURATION, createFade, stepFade, trackFadeEase } from './motion'

const GLOW_FALLOFF_SCALE = Math.sqrt(6 / Math.LN2)
const NO_PARALLAX = Object.freeze({ parallaxX: 0, parallaxY: 0 })

const VOICE_ATTACK = 18
const VOICE_RELEASE = 5
const VOICE_COLOR_RATE = 4
const ON_AIR_RATE = 2.5
const ON_AIR_PAUSED = 0.5
const ON_AIR_BREATHE_SECONDS = 2.4
const AMBIENT_FLOOR = 0.002
const CROSSFADE_GLOW = 0.7
const CROSSFADE_GLOW_MIN_MS = 1000
const CROSSFADE_RELEASE = 1.2
const PROBE_INTERVAL_MS = 66
const REFERENCE_FPS = 60
const LIGHT_CURVE_STEP_S = 0.5
const LIGHT_CURVE_WINDOW_S = 4
const LIGHT_IN_RANK = 0.55
const LIGHT_FULL_RANK = 0.85
const LIGHT_ATTACK_RATE = 1.2
const LIGHT_RELEASE_RATE = 0.35
const LIGHT_WITHOUT_ANALYSIS = 0.5
const LIGHT_KICK_FLOOR = 0.25
const SIGNATURE_EPSILON = 1e-4
const PLAN_DELAY_MS = 450
const SIGNATURE_SIZE = 512
const PARALLAX_SIGNATURE_SCALE = SIGNATURE_EPSILON / 0.05
const UNDERLAY_LEVEL = 0x0a / 255 * 0.5
const CLICK_DECAY_MS = 500
const ARTWORK_SWAP_FRAMES = 2
const ARTWORK_FADE_MAX_STEP_S = 1 / 30
const PANEL_CORNER_RADIUS = 0.015
const OPAQUE_PANEL_OPACITY = 0.9995
const PANEL_FEATHER = 0.01
const MIN_PANEL_SIZE = 0.01
const MIN_PANEL_OPACITY = 0.01
const PLAYER_PANEL = 5
const MOBILE_PANELS = 2
const MAX_PANELS = 7
const MAX_SPLIT_RECTS = MAX_PANELS * 2
const SPLIT_GRID = 128
const RADIO_BUTTON_REACH = 1.25
const RADIO_BUTTON_MIN_STATE = 0.005
const RADIO_BUTTON_MIN_RADIUS = 0.001
const CLIP_RATE_STEP = 0.08
export const LYRIC_WIDTH = 1024
export const LYRIC_HEIGHT = 512

const BG_SIGNATURE_UNIFORMS = [
  'u_has_depth_map', 'u_focal_depth', 'u_focal_range', 'u_transition', 'u_tex_resolution', 'u_glitch', 'u_scale',
  'u_frame_offset', 'u_frame_scale', 'u_rotation', 'u_orbit', 'u_brightness', 'u_contrast', 'u_saturation',
  'u_hue', 'u_max_blur', 'u_chromatic', 'u_flicker', 'u_canvas_resolution', 'u_text_empty',
]

const FG_SIGNATURE_UNIFORMS = [
  'u_scroll_offset', 'u_panel_regions', 'u_panel_opacities', 'u_panel_ids', 'u_panel_count', 'u_panels_bounding_box', 'u_header_height',
  'u_radio_button_pos', 'u_radio_button_radius', 'u_radio_button_state', 'u_radio_button_hover',
  'u_radio_button_pressed', 'u_radio_progress', 'u_radio_state_int', 'u_visual_state_color',
  'u_glass_blur_factor', 'u_enable_refraction', 'u_audio_pulse', 'u_player_gradient_color',
  'u_player_gradient_intensity', 'u_glass_taps',
  'u_glow_center', 'u_glow_scale', 'u_voice_glow', 'u_on_air_glow', 'u_panel_glow', 'u_glow_active',
]

const approach = (current, target, rate, delta) => current + (target - current) * (1 - Math.exp(-rate * delta))

function makeCanvas(width, height) {
  if (typeof OffscreenCanvas !== 'undefined') return new OffscreenCanvas(width, height)
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  return canvas
}

function buildLightCurve(features) {
  const segments = features?.loudness_segments
  if (!segments?.length) return null
  const last = segments[segments.length - 1]
  const bins = Math.max(1, Math.ceil((features.duration || last.start + last.duration) / LIGHT_CURVE_STEP_S))
  const sum = new Float32Array(bins)
  const count = new Float32Array(bins)
  for (const segment of segments) {
    const bin = Math.min(bins - 1, Math.floor(segment.start / LIGHT_CURVE_STEP_S))
    sum[bin] += segment.loudness
    count[bin]++
  }
  const loudness = Float32Array.from(sum, (total, i) => (count[i] ? total / count[i] : -60))
  const half = Math.round(LIGHT_CURVE_WINDOW_S / LIGHT_CURVE_STEP_S / 2)
  const smooth = Float32Array.from(loudness, (_, i) => {
    let total = 0
    let n = 0
    for (let j = Math.max(0, i - half); j <= Math.min(bins - 1, i + half); j++) {
      total += loudness[j]
      n++
    }
    return total / n
  })
  const sorted = Array.from(smooth).sort(numericAscending)
  return Float32Array.from(smooth, value => MathUtils.smoothstep(lowerBound(sorted, value) / bins, LIGHT_IN_RANK, LIGHT_FULL_RANK))
}

function writeSignatureValue(out, index, value) {
  if (typeof value === 'number') {
    out[index] = value
    return index + 1
  }
  if (Array.isArray(value)) {
    let next = index
    for (let i = 0; i < value.length; i++) next = writeSignatureValue(out, next, value[i])
    return next
  }
  out[index] = value.x
  out[index + 1] = value.y
  if (value.isVector2) return index + 2
  out[index + 2] = value.z
  if (value.isVector3) return index + 3
  out[index + 3] = value.w
  return index + 4
}

function writeUniformSignature(out, index, uniforms, names) {
  let next = index
  for (let i = 0; i < names.length; i++) next = writeSignatureValue(out, next, uniforms[names[i]].value)
  return next
}

function signatureChanged(current, previous, length) {
  for (let i = 0; i < length; i++) {
    if (Math.abs(current[i] - previous[i]) > SIGNATURE_EPSILON) return true
  }
  return false
}

function generateNoiseTexture() {
  const width = 256
  const height = 256
  const canvas = makeCanvas(width, height)
  const ctx = canvas.getContext('2d')
  const imageData = ctx.createImageData(width, height)
  const data = imageData.data
  for (let i = 0; i < data.length; i += 4) {
    const value = Math.floor(Math.random() * 255)
    data[i] = value
    data[i + 1] = value
    data[i + 2] = value
    data[i + 3] = 255
  }
  ctx.putImageData(imageData, 0, 0)
  const texture = new CanvasTexture(canvas)
  texture.wrapS = RepeatWrapping
  texture.wrapT = RepeatWrapping
  texture.needsUpdate = true
  return texture
}

async function loadArtworkTextures(blob) {
  const { color, map, analysis } = await decodePack(blob, { map: true, analysis: true })
  const canvas = makeCanvas(color.width, color.height)
  const context = canvas.getContext('2d', { willReadFrequently: true })
  context.drawImage(color, 0, 0)
  color.close()
  const texture = new CanvasTexture(canvas)
  texture.wrapS = MirroredRepeatWrapping
  texture.wrapT = MirroredRepeatWrapping
  texture.generateMipmaps = true
  texture.needsUpdate = true
  const palette = analysis?.palette || null
  if (!analysis?.bands) {
    map?.close()
    return { texture, depth: null, palette }
  }
  const depthCanvas = makeCanvas(map.width, map.height)
  depthCanvas.getContext('2d', { willReadFrequently: true }).drawImage(map, 0, 0)
  map.close()
  const depthTexture = new CanvasTexture(depthCanvas)
  depthTexture.wrapS = MirroredRepeatWrapping
  depthTexture.wrapT = MirroredRepeatWrapping
  depthTexture.minFilter = LinearMipmapLinearFilter
  depthTexture.needsUpdate = true
  return { texture, depth: { texture: depthTexture, ...analysis }, palette }
}

function addRect(out, n, x0, y0, x1, y1) {
  out[n * 4] = Math.max(0, x0)
  out[n * 4 + 1] = Math.max(0, y0)
  out[n * 4 + 2] = Math.min(1, x1)
  out[n * 4 + 3] = Math.min(1, y1)
  return out[n * 4] < out[n * 4 + 2] && out[n * 4 + 1] < out[n * 4 + 3] ? n + 1 : n
}

function opaquePanelRects(regions, opacities, count, out) {
  let n = 0
  for (let i = 0; i < count; i++) {
    const region = regions[i]
    if (region.z < MIN_PANEL_SIZE || opacities[i] < OPAQUE_PANEL_OPACITY) continue
    const halfWidth = region.z * 0.5
    const halfHeight = region.w * 0.5
    const insetWidth = halfWidth - PANEL_CORNER_RADIUS
    const insetHeight = halfHeight - PANEL_CORNER_RADIUS
    if (insetWidth <= 0 || insetHeight <= 0) continue
    n = addRect(out, n, region.x - halfWidth, region.y - insetHeight, region.x + halfWidth, region.y + insetHeight)
    n = addRect(out, n, region.x - insetWidth, region.y - halfHeight, region.x + insetWidth, region.y + halfHeight)
  }
  return n
}

function glassPanelRects(regions, opacities, count, out) {
  let n = 0
  for (let i = 0; i < count; i++) {
    const region = regions[i]
    if (region.z < MIN_PANEL_SIZE || opacities[i] < MIN_PANEL_OPACITY) continue
    const halfWidth = region.z * 0.5 + PANEL_FEATHER
    const halfHeight = region.w * 0.5 + PANEL_FEATHER
    n = addRect(out, n, region.x - halfWidth, region.y - halfHeight, region.x + halfWidth, region.y + halfHeight)
  }
  return n
}

function insideAny(rects, count, x, y) {
  for (let k = 0; k < count; k++) {
    if (x > rects[k * 4] && x < rects[k * 4 + 2] && y > rects[k * 4 + 1] && y < rects[k * 4 + 3]) return true
  }
  return false
}

function radioButtonBounds(uniforms, out) {
  const radius = uniforms.u_radio_button_radius.value
  if (uniforms.u_radio_button_state.value <= RADIO_BUTTON_MIN_STATE || radius.x <= RADIO_BUTTON_MIN_RADIUS) return false
  const center = uniforms.u_radio_button_pos.value
  const reach = Math.max(RADIO_BUTTON_REACH, 1.08 + 0.1 * uniforms.u_audio_pulse.value)
  out[0] = Math.max(0, Math.floor((center.x - radius.x * reach) * SPLIT_GRID) / SPLIT_GRID)
  out[1] = Math.max(0, Math.floor((center.y - radius.y * reach) * SPLIT_GRID) / SPLIT_GRID)
  out[2] = Math.min(1, Math.ceil((center.x + radius.x * reach) * SPLIT_GRID) / SPLIT_GRID)
  out[3] = Math.min(1, Math.ceil((center.y + radius.y * reach) * SPLIT_GRID) / SPLIT_GRID)
  return out[0] < out[2] && out[1] < out[3]
}

function pushQuad(positions, uvs, x0, y0, x1, y1) {
  const corners = [x0, y0, x1, y0, x1, y1, x0, y0, x1, y1, x0, y1]
  for (let i = 0; i < corners.length; i += 2) {
    positions.push(corners[i] * 2 - 1, corners[i + 1] * 2 - 1, 0)
    uvs.push(corners[i], corners[i + 1])
  }
}

function fullScreenQuads() {
  const quads = { positions: [], uvs: [] }
  pushQuad(quads.positions, quads.uvs, 0, 0, 1, 1)
  return quads
}

function splitScreen(opaque, opaqueCount, glass, glassCount, button) {
  const xs = [0, 1]
  const ys = [0, 1]
  for (let i = 0; i < opaqueCount; i++) {
    xs.push(opaque[i * 4], opaque[i * 4 + 2])
    ys.push(opaque[i * 4 + 1], opaque[i * 4 + 3])
  }
  for (let i = 0; i < glassCount; i++) {
    xs.push(glass[i * 4], glass[i * 4 + 2])
    ys.push(glass[i * 4 + 1], glass[i * 4 + 3])
  }
  if (button) {
    xs.push(button[0], button[2])
    ys.push(button[1], button[3])
  }
  const sortUnique = values => [...new Set(values)].sort((a, b) => a - b)
  const columns = sortUnique(xs)
  const rows = sortUnique(ys)
  const inner = { positions: [], uvs: [] }
  const innerButton = { positions: [], uvs: [] }
  const outer = { positions: [], uvs: [] }
  const backdrop = { positions: [], uvs: [] }
  for (let j = 0; j < rows.length - 1; j++) {
    const y0 = rows[j]
    const y1 = rows[j + 1]
    const cy = (y0 + y1) * 0.5
    let runStart = 0
    let runTarget = null
    for (let i = 0; i <= columns.length - 1; i++) {
      let target = null
      if (i < columns.length - 1) {
        const cx = (columns[i] + columns[i + 1]) * 0.5
        const underButton = !!button && cx > button[0] && cx < button[2] && cy > button[1] && cy < button[3]
        if (insideAny(opaque, opaqueCount, cx, cy)) target = underButton ? innerButton : inner
        else target = underButton || insideAny(glass, glassCount, cx, cy) ? outer : backdrop
      }
      if (target !== runTarget) {
        if (runTarget !== null) pushQuad(runTarget.positions, runTarget.uvs, columns[runStart], y0, columns[i], y1)
        runStart = i
        runTarget = target
      }
    }
  }
  return { inner, innerButton, outer, backdrop }
}

function setQuads(geometry, quads) {
  geometry.dispose()
  geometry.setAttribute('position', new Float32BufferAttribute(quads.positions, 3))
  geometry.setAttribute('uv', new Float32BufferAttribute(quads.uvs, 2))
}

function createDefaultState() {
  return {
    isPlaying: false,
    progressMs: 0,
    durationMs: 0,
    talkBreak: null,
    crossfadeMs: 0,
    scrollPosition: 0,
    scrollVelocity: 0,
    panelRegions: [],
    panelOpacities: [],
    radioButtonPos: null,
    radioButton: { opacity: 0, isHovered: false, isPressed: false },
    chromeReveal: 0,
    chromeLift: 0,
    radioProgress: null,
    speakerColor: null,
    voiceTarget: 0,
    gyro: NO_PARALLAX,
    mouse: NO_PARALLAX,
    accentRgb: { r: 0, g: 0, b: 0 },
    seedColor: { r: 0, g: 0, b: 0 },
    visualQuality: 'high',
    glassTaps: 3,
    reduceMotion: false,
    levelIndex: 2,
    headerHeight: 0,
    probeWanted: false,
    renderPaused: false,
    statsActive: false,
  }
}

export class SceneRenderer {
  constructor({ canvas, captureResolution = 128, glassBlurFactor = 1, emit, relayWork = false }) {
    this.emit = emit
    this.relayWork = relayWork
    this.captureResolution = captureResolution
    this.glassBlurFactor = glassBlurFactor
    this.state = createDefaultState()
    this.size = { width: 1, height: 1, dpr: 1, referenceDpr: 1 }
    this.visible = true
    this.bench = { skip: false, force: false, extra: 0, uniforms: null, timing: null }
    this.lastFrameTime = null
    this.rafId = null
    this.programsReady = false
    this.splashSent = false
    this.disposed = false

    ColorManagement.enabled = true
    this.renderer = new WebGLRenderer({ canvas, antialias: false, powerPreference: 'high-performance', stencil: false, depth: false, alpha: false })
    this.renderer.outputColorSpace = LinearSRGBColorSpace
    this.renderer.toneMapping = NoToneMapping
    this.renderer.sortObjects = false
    this.gl = this.renderer.getContext()
    this.sceneTimer = createGpuTimer(this.gl, 'scene gpu')
    this.statsStop = null

    canvas.addEventListener?.('webglcontextlost', event => { event.preventDefault?.(); this.emit({ type: 'context', lost: true }) })
    canvas.addEventListener?.('webglcontextrestored', () => this.emit({ type: 'context', lost: false }))

    this.transparentPixel = new DataTexture(new Uint8Array([0, 0, 0, 0]), 1, 1, RGBAFormat)
    this.transparentPixel.needsUpdate = true
    this.geometry = new PlaneGeometry(2, 2)
    this.noiseTexture = generateNoiseTexture()
    this.lyricCanvas = makeCanvas(LYRIC_WIDTH, LYRIC_HEIGHT)
    this.textTexture = new CanvasTexture(this.lyricCanvas)
    this.textTexture.userData.empty = true
    this.textTexture.needsUpdate = true
    this.textProbe = null

    this.effects = createFx()
    this.signature = {
      current: new Float64Array(SIGNATURE_SIZE), previous: new Float64Array(SIGNATURE_SIZE), length: 0,
      textures: [null, null, null, null], textVersion: -1, fgVisible: false, force: true, captureStale: true,
      captureLength: 0, captured: new Float64Array(SIGNATURE_SIZE),
    }
    this.audioFeatures = null
    this.interpolatedProgress = 0
    this.lastSeenProgress = 0
    this.localProgressUpdateTime = Date.now()
    this.scratchColor = new Vector3()
    this.ambient = { voice: 0, onAir: 0, breatheTime: 0, crossfade: 0 }
    this.crossfadeColor = new Vector3()
    this.smoothProgress = 0
    this.panelOpacitiesLerp = [0, 0, 0, 0, 0, 0, 0]
    this.radioFade = createFade(0)
    this.panelRegionVecs = Array.from({ length: 7 }, () => new Vector4())
    this.probeAt = 0
    this.probeParams = {}
    this.lightCurve = null
    this.lightLevel = 0
    this.click = { intensity: 0, at: 0, active: false }
    this.transitionProgress = 1
    this.layers = { A: this.transparentPixel, B: this.transparentPixel }
    this.layerDepth = { A: null, B: null }
    this.layerSoft = { A: null, B: null }
    this.layerPalette = { A: null, B: null }
    this.palette = null
    this.bands = null
    this.depthGrid = null
    this.focus = createFocus()
    this.plan = null
    this.basePlan = null
    this.planTimer = null
    this.directorCursor = createDirectorCursor()
    this.lookContext = createLookContext(this.focus.frame, true)
    this.softener = createSoftener(this.renderer)
    this.frontLayer = 'A'
    this.artworkUrl = null
    this.artworkTokens = { A: 0, B: 0 }
    this.pendingSwap = null
    this.clips = { meta: [], textures: [] }
    this.footage = createFootageState()
    this.footageBuilt = false
    this.slotTextures = new Array(MAX_SLOTS).fill(null)
    this.sentRates = new Float32Array(MAX_SLOTS).fill(-1)

    this.captureScene = new Scene()
    this.captureCamera = new OrthographicCamera(-1, 1, 1, -1, 0, 1)
    this.mainScene = new Scene()
    this.mainCamera = new PerspectiveCamera(75, 1, 0.1, 1000)
    this.captureTarget = new WebGLRenderTarget(captureResolution, captureResolution, {
      minFilter: LinearFilter, magFilter: LinearFilter, format: RGBAFormat, generateMipmaps: false, stencilBuffer: false, depthBuffer: false,
    })

    this.bgMaterial = new ShaderMaterial({
      vertexShader: backgroundVertexShader,
      fragmentShader: backgroundFragmentShader,
      uniforms: {
        u_texture: { value: this.transparentPixel },
        u_texture_prev: { value: this.transparentPixel },
        u_texture_near: { value: this.transparentPixel },
        u_texture_far: { value: this.transparentPixel },
        u_texture_prev_far: { value: this.transparentPixel },
        u_depth_map: { value: this.transparentPixel },
        u_orbit: { value: new Vector3(0, 0, 0) },
        u_text_texture: { value: this.transparentPixel },
        u_text_empty: { value: 0.0 },
        u_transition: { value: 1.0 },
        u_tex_resolution: { value: new Vector2(1, 1) },
        u_canvas_resolution: { value: new Vector2(1, 1) },
        u_parallax: { value: new Vector2(0, 0) },
        u_glitch: { value: new Vector2(0, 0) },
        u_time: { value: 0.0 },
        u_scale: { value: 1.0 },
        u_frame_offset: { value: new Vector2(0, 0) },
        u_frame_scale: { value: 1.0 },
        u_rotation: { value: 0.0 },
        u_brightness: { value: 0.6 },
        u_contrast: { value: 1.0 },
        u_saturation: { value: 1.0 },
        u_hue: { value: 0.0 },
        u_grade: { value: new Matrix3() },
        u_grade_lift: { value: 0.0 },
        u_max_blur: { value: 0.0 },
        u_chromatic: { value: 0.0 },
        u_flicker: { value: 1.0 },
        u_has_depth_map: { value: 0.0 },
        u_focal_depth: { value: 0.5 },
        u_focal_range: { value: 0.1 },
        u_is_capture: { value: 0.0 },
        ...lookUniforms(),
      },
    })

    this.fgMaterial = new ShaderMaterial({
      vertexShader: backgroundVertexShader,
      fragmentShader: sceneFragmentShader,
      uniforms: {
        ...this.bgMaterial.uniforms,
        u_capture_texture: { value: this.captureTarget.texture },
        u_noise_texture: { value: this.noiseTexture },
        u_glass_taps: { value: 3.0 },
        u_underlay: { value: new Vector3(UNDERLAY_LEVEL, UNDERLAY_LEVEL, UNDERLAY_LEVEL) },
        u_scroll_offset: { value: 0.0 },
        u_glass_blur_factor: { value: 1.0 },
        u_enable_refraction: { value: 1.0 },
        u_panel_regions: { value: Array.from({ length: 7 }, () => new Vector4()) },
        u_panel_opacities: { value: [0, 0, 0, 0, 0, 0, 0] },
        u_panel_ids: { value: [0, 0, 0, 0, 0, 0, 0] },
        u_panel_count: { value: 0 },
        u_panels_bounding_box: { value: new Vector4(0, 0, 0, 0) },
        u_header_height: { value: 0.063 },
        u_radio_button_pos: { value: new Vector2(0.5, 0.5) },
        u_radio_button_radius: { value: new Vector2(0.08, 0.08) },
        u_radio_button_state: { value: 0.0 },
        u_radio_button_hover: { value: 0.0 },
        u_radio_button_pressed: { value: 0.0 },
        u_radio_progress: { value: 0.0 },
        u_radio_state_int: { value: 0 },
        u_visual_state_color: { value: new Vector3(0.5, 0.5, 0.5) },
        u_radio_time: { value: 0.0 },
        u_audio_pulse: { value: 0.0 },
        u_player_gradient_color: { value: new Vector3(0.0, 0.0, 0.0) },
        u_player_gradient_intensity: { value: 0.0 },
        u_voice_color: { value: new Vector3(0.58, 0.2, 0.92) },
        u_voice_level: { value: 0.0 },
        u_on_air_color: { value: new Vector3(0.96, 0.62, 0.04) },
        u_on_air: { value: 0.0 },
        u_glow_center: { value: new Vector2(0.5, 0.55) },
        u_glow_scale: { value: new Vector2(GLOW_FALLOFF_SCALE, GLOW_FALLOFF_SCALE) },
        u_voice_glow: { value: new Vector3(0, 0, 0) },
        u_on_air_glow: { value: new Vector3(0, 0, 0) },
        u_panel_glow: { value: new Vector3(0.12, 0.14, 0.18) },
        u_glow_active: { value: 0.0 },
      },
    })

    const fg = this.fgMaterial.uniforms
    this.backdropMaterial = new ShaderMaterial({
      vertexShader: backgroundVertexShader,
      fragmentShader: backdropFragmentShader,
      uniforms: {
        ...this.bgMaterial.uniforms,
        u_underlay: fg.u_underlay,
        u_glow_center: fg.u_glow_center,
        u_glow_scale: fg.u_glow_scale,
        u_voice_glow: fg.u_voice_glow,
        u_on_air_glow: fg.u_on_air_glow,
        u_glow_active: fg.u_glow_active,
      },
    })

    this.glassInteriorMaterial = new ShaderMaterial({
      vertexShader: backgroundVertexShader,
      fragmentShader: sceneGlassFragmentShader,
      uniforms: this.fgMaterial.uniforms,
    })
    this.glassButtonMaterial = new ShaderMaterial({
      vertexShader: backgroundVertexShader,
      fragmentShader: sceneGlassButtonFragmentShader,
      uniforms: this.fgMaterial.uniforms,
    })
    this.lookMaterials = { cover: { capture: this.bgMaterial, full: this.fgMaterial, backdrop: this.backdropMaterial } }
    this.lookTiles = {}
    for (const id of LOOK_IDS) if (id !== 'cover') this.buildLook(id)
    const allLooks = [...LOOK_IDS, ...FOOTAGE_IDS]
    this.lookSignatures = Object.fromEntries(allLooks.map(id => [id, id === 'cover' ? [] : lookSignature(id)]))
    this.lookIndex = Object.fromEntries(allLooks.map((id, i) => [id, i]))
    this.footageLooks = new Set(FOOTAGE_IDS)
    this.lookReady = { cover: true }
    this.look = 'cover'
    this.glassGeometry = new BufferGeometry()
    this.glassInteriorGeometry = new BufferGeometry()
    this.glassButtonGeometry = new BufferGeometry()
    this.glassBackdropGeometry = new BufferGeometry()
    for (const geometry of [this.glassGeometry, this.glassInteriorGeometry, this.glassButtonGeometry, this.glassBackdropGeometry]) setQuads(geometry, fullScreenQuads())
    this.splitRects = new Float64Array(MAX_SPLIT_RECTS * 4)
    this.splitScratch = new Float64Array(MAX_SPLIT_RECTS * 4)
    this.splitGlass = new Float64Array(MAX_PANELS * 4)
    this.splitGlassScratch = new Float64Array(MAX_PANELS * 4)
    this.splitButton = new Float64Array(4)
    this.splitButtonScratch = new Float64Array(4)
    this.splitCount = -1
    this.splitGlassCount = -1
    this.splitHasButton = false
    this.splitHasInterior = false
    this.splitHasInteriorButton = false
    this.splitHasOuter = true
    this.splitHasBackdrop = false

    this.captureMesh = new Mesh(this.geometry, this.bgMaterial)
    this.glassMesh = new Mesh(this.glassGeometry, this.fgMaterial)
    this.glassInteriorMesh = new Mesh(this.glassInteriorGeometry, this.glassInteriorMaterial)
    this.glassButtonMesh = new Mesh(this.glassButtonGeometry, this.glassButtonMaterial)
    this.glassBackdropMesh = new Mesh(this.glassBackdropGeometry, this.backdropMaterial)
    this.backdropMesh = new Mesh(this.geometry, this.backdropMaterial)
    for (const mesh of [this.captureMesh, this.glassMesh, this.glassInteriorMesh, this.glassButtonMesh, this.glassBackdropMesh, this.backdropMesh]) mesh.frustumCulled = false
    this.captureScene.add(this.captureMesh)
    this.mainScene.add(this.glassMesh)
    this.mainScene.add(this.glassInteriorMesh)
    this.mainScene.add(this.glassButtonMesh)
    this.mainScene.add(this.glassBackdropMesh)
    this.mainScene.add(this.backdropMesh)
    for (const node of [this.mainScene, this.captureScene, this.mainCamera, this.captureCamera]) {
      node.updateMatrixWorld(true)
      node.matrixWorldAutoUpdate = false
    }

    Promise.all([
      this.renderer.compileAsync(this.captureScene, this.captureCamera),
      this.renderer.compileAsync(this.mainScene, this.mainCamera),
    ]).then(() => {
      this.programsReady = true
      return this.compileLooks(LOOK_IDS).then(() => this.compileLooks(FOOTAGE_IDS))
    })

    let rendererName = ''
    try {
      const info = this.gl.getExtension('WEBGL_debug_renderer_info')
      rendererName = String(this.gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : this.gl.RENDERER) || '')
    } catch {
      rendererName = ''
    }
    this.emit({ type: 'renderer', name: rendererName })
  }

  buildLook(id) {
    const shaders = lookShaders(id)
    this.lookMaterials[id] = {
      capture: new ShaderMaterial({ vertexShader: shaders.vertex, fragmentShader: shaders.background, uniforms: this.bgMaterial.uniforms }),
      full: new ShaderMaterial({ vertexShader: shaders.vertex, fragmentShader: shaders.scene, uniforms: this.fgMaterial.uniforms }),
      backdrop: new ShaderMaterial({ vertexShader: shaders.vertex, fragmentShader: shaders.backdrop, uniforms: this.backdropMaterial.uniforms }),
    }
    const tiled = lookById(id).tiled
    if (!tiled) return
    const materials = Array.from({ length: tiled.groups + (tiled.extra || 0) }, (_, index) => {
      const clip = index >= tiled.groups
      const border = !clip && tiled.isBorder(index)
      return new ShaderMaterial({
        vertexShader: clip ? shaders.tileClipVertex : border ? shaders.vertex : shaders.tileVertex,
        fragmentShader: clip ? shaders.tileClip : border ? shaders.tileBorder : shaders.tilePicture,
        uniforms: { ...this.backdropMaterial.uniforms, ...tiled.uniformsFor(this.bgMaterial.uniforms, index) },
        transparent: border,
      })
    })
    const geometry = new BufferGeometry()
    geometry.setAttribute('position', new BufferAttribute(new Float32Array(tiled.vertices * 3), 3).setUsage(DynamicDrawUsage))
    geometry.setAttribute('uv', new BufferAttribute(new Float32Array(tiled.vertices * 2), 2).setUsage(DynamicDrawUsage))
    const groups = Array.from({ length: tiled.groups }, (_, materialIndex) => ({ start: 0, count: 0, materialIndex }))
    this.lookTiles[id] = { materials, geometry, ranges: new Int32Array(tiled.groups * 2), groups }
  }

  async compileLooks(ids) {
    for (const id of ids) {
      if (this.lookReady[id] || this.disposed || !this.lookMaterials[id]) continue
      const scene = new Scene()
      const tiles = this.lookTiles[id]
      const tiled = lookById(id).tiled
      const programs = tiles ? [tiles.materials[0], tiles.materials[tiled.groups - 1], ...(tiled.extra ? [tiles.materials[tiled.groups]] : [])] : []
      for (const material of [...Object.values(this.lookMaterials[id]), ...programs]) {
        const mesh = new Mesh(this.geometry, material)
        mesh.frustumCulled = false
        scene.add(mesh)
      }
      await this.renderer.compileAsync(scene, this.captureCamera)
      this.lookReady[id] = true
    }
  }

  enableFootage() {
    if (this.footageBuilt || this.disposed) return
    this.footageBuilt = true
    for (const id of FOOTAGE_IDS) this.buildLook(id)
    if (this.programsReady) this.compileLooks(FOOTAGE_IDS)
  }

  setLook(id) {
    if (id === this.look) return
    const materials = this.lookMaterials[id]
    this.captureMesh.material = materials.capture
    this.glassMesh.material = materials.full
    this.glassBackdropMesh.material = materials.backdrop
    this.look = id
  }

  setBackdrop() {
    const mesh = this.backdropMesh
    const tiles = this.lookTiles[this.look]
    if (!tiles) {
      mesh.geometry = this.geometry
      mesh.material = this.lookMaterials[this.look].backdrop
      return
    }
    const { geometry, materials, ranges, groups } = tiles
    const { position, uv } = geometry.attributes
    const tiled = lookById(this.look).tiled
    const vertices = tiled.layout(this.bgMaterial.uniforms, materials, position.array, uv.array, ranges)
    for (const attribute of [position, uv]) {
      attribute.clearUpdateRanges()
      attribute.addUpdateRange(0, vertices * attribute.itemSize)
      attribute.needsUpdate = true
    }
    geometry.groups.length = 0
    for (let group = 0; group < groups.length; group++) {
      if (!ranges[group * 2 + 1]) continue
      groups[group].start = ranges[group * 2]
      groups[group].count = ranges[group * 2 + 1]
      groups[group].materialIndex = tiled.materialIndex ? tiled.materialIndex(this.bgMaterial.uniforms, group) : group
      geometry.groups.push(groups[group])
    }
    geometry.setDrawRange(0, vertices)
    mesh.geometry = geometry
    mesh.material = materials
  }

  showable(look) {
    return !!this.lookReady[look] && !(lookById(look).needsDepth && (!this.bands || this.transitionProgress < 1))
  }

  footageTextures(footage) {
    const slots = this.slotTextures
    for (let k = 0; k < MAX_SLOTS; k++) {
      const clip = footage.clips[k]
      const texture = clip === undefined ? null : this.clips.textures[clip]
      if (clip !== undefined && !(texture?.version > 0)) return null
      slots[k] = texture || null
    }
    return slots
  }

  direct(at, reduceMotion, width, height, parallaxX, parallaxY) {
    const plan = this.plan
    if (!plan || !at || reduceMotion) {
      this.setLook('cover')
      return
    }
    const shot = at.shot
    const bgUniforms = this.bgMaterial.uniforms
    const ctx = this.lookContext
    const textures = shot.footage && this.showable(shot.footage.look) ? this.footageTextures(shot.footage) : null
    let look = textures ? shot.footage.look : shot.look
    if (!this.showable(look)) look = 'cover'
    ctx.footage = !!textures
    if (look !== 'cover') {
      directLook(ctx, plan, at, this.effects, this.interpolatedProgress / 1000)
      ctx.aspect = width / Math.max(height, 1)
      ctx.hue = bgUniforms.u_hue.value
      ctx.rotation = bgUniforms.u_rotation.value
      ctx.bands = this.bands
      ctx.grid = bgUniforms.u_has_depth_map.value > 0.5 ? this.depthGrid : null
      ctx.tilt.x = -parallaxX / Math.max(width, 1)
      ctx.tilt.y = -parallaxY / Math.max(height, 1)
      setLookUniforms(bgUniforms, shot, ctx, look)
      if (textures) setFootageUniforms(bgUniforms, shot, ctx, textures, this.palette)
    }
    this.setLook(look)
  }

  stepClips(plan, at, t, delta, playing) {
    const state = stepFootage(this.footage, plan, at, t, delta, this.effects, this.clips.meta, playing)
    const sent = this.sentRates
    if (state.changed) {
      this.emit({
        type: 'footage',
        play: state.slots.map((index, k) => ({ index, at: state.times[k] })),
        next: state.next.map((index, k) => ({ index, at: state.nextAt[k] })),
      })
      sent.fill(-1)
    }
    let changed = false
    for (let k = 0; k < state.slots.length; k++) {
      const rate = state.rates[k]
      if (Math.abs(rate - sent[k]) > CLIP_RATE_STEP || (rate === 0) !== (sent[k] === 0)) changed = true
    }
    if (!changed) return
    sent.set(state.rates)
    this.emit({ type: 'clipRates', rates: state.slots.map((index, k) => [index, state.rates[k]]) })
  }

  handle(message) {
    switch (message.type) {
      case 'size': this.setSize(message); break
      case 'state': Object.assign(this.state, message.state); this.updateStats(); break
      case 'features': this.setFeatures(message.features, message.sections, message.lines); break
      case 'artwork': this.setArtwork(message.key, message.blob); break
      case 'lyric': this.setLyric(message); break
      case 'clips': this.setClips(message.clips); break
      case 'clipFrame': this.setClipFrame(message); break
      case 'click': this.click = { intensity: message.intensity, at: Date.now() - message.age, active: true }; break
      case 'visible': this.setVisible(message.visible); break
      case 'bench': Object.assign(this.bench, message.bench); break
      case 'eval': this.runEval(message); break
      case 'dispose': this.dispose(); break
      default: break
    }
  }

  setSize({ width, height, dpr, referenceDpr }) {
    this.size = { width, height, dpr, referenceDpr }
    this.renderer.setPixelRatio(dpr)
    this.renderer.setSize(width, height, false)
    this.mainCamera.aspect = width / Math.max(height, 1)
    this.mainCamera.updateProjectionMatrix()
    this.signature.force = true
  }

  setVisible(visible) {
    this.visible = visible
    if (visible) this.start()
    else this.stop()
  }

  updateStats() {
    if (this.state.statsActive && !this.statsStop) this.statsStop = watchFrameStats()
    if (!this.state.statsActive && this.statsStop) {
      this.statsStop()
      this.statsStop = null
    }
  }

  setFeatures(features, sections, lines) {
    clearTimeout(this.planTimer)
    this.audioFeatures = features || null
    this.lightCurve = buildLightCurve(features)
    this.plan = null
    this.basePlan = null
    this.directorCursor = createDirectorCursor()
    this.effects.lastBeat = -1
    this.effects.lastShot = -1
    if (!features) return
    this.planTimer = setTimeout(() => {
      if (this.disposed) return
      this.basePlan = planShots(features, { sections, lines })
      this.updateFootagePlan()
    }, PLAN_DELAY_MS)
  }

  updateFootagePlan() {
    this.plan = this.clips.meta.length ? planFootage(this.basePlan, this.clips.meta) : this.basePlan
    this.directorCursor = createDirectorCursor()
    this.footage = createFootageState()
  }

  setArtwork(url, blob) {
    if (!url || !blob || url === this.artworkUrl) return
    const layer = this.frontLayer === 'A' ? 'B' : 'A'
    const token = ++this.artworkTokens[layer]
    this.artworkUrl = url
    loadArtworkTextures(blob).then(({ texture, depth, palette }) => {
      if (this.disposed || this.artworkTokens[layer] !== token) {
        texture.dispose()
        depth?.texture.dispose()
        return
      }
      const old = this.layers[layer]
      this.layers[layer] = texture
      if (old !== this.transparentPixel) old.dispose()
      this.layerSoft[layer] = this.softener.soften(texture, this.layerSoft[layer])
      this.layerDepth[layer]?.texture.dispose()
      this.layerDepth[layer] = depth
      this.layerPalette[layer] = palette
      this.pendingSwap = { layer, frames: ARTWORK_SWAP_FRAMES }
    }).catch(error => {
      if (this.artworkTokens[layer] === token) this.emit({ type: 'warn', message: `Failed to load artwork texture: ${error?.message || error}` })
    })
  }

  applyLayers() {
    const u = this.bgMaterial.uniforms
    const tex = this.layers[this.frontLayer]
    const prev = this.layers[this.frontLayer === 'A' ? 'B' : 'A']
    u.u_texture.value = tex
    const img = tex.image
    u.u_tex_resolution.value.set(img && img.width ? img.width : 1, img && img.height ? img.height : 1)
    u.u_texture_prev.value = prev
    const soft = this.layerSoft[this.frontLayer]
    const prevSoft = this.layerSoft[this.frontLayer === 'A' ? 'B' : 'A']
    u.u_texture_near.value = soft ? soft.near.texture : this.transparentPixel
    u.u_texture_far.value = soft ? soft.far.texture : this.transparentPixel
    u.u_texture_prev_far.value = prevSoft ? prevSoft.far.texture : this.transparentPixel
    u.u_has_depth_map.value = 0.0
    const depth = this.layerDepth[this.frontLayer]
    u.u_depth_map.value = depth ? depth.texture : this.transparentPixel
    this.bands = depth ? depth.bands : null
    this.depthGrid = depth ? depth.grid : null
    this.effects.interest = depth ? depth.interest : null
    this.palette = this.layerPalette[this.frontLayer]
    this.focus.primed = false
  }

  setLyric({ image, empty, probe }) {
    if (image && (image.width !== this.lyricCanvas.width || image.height !== this.lyricCanvas.height)) {
      this.lyricCanvas.width = image.width
      this.lyricCanvas.height = image.height
      this.textTexture.dispose()
      this.textTexture = new CanvasTexture(this.lyricCanvas)
    }
    const ctx = this.lyricCanvas.getContext('2d')
    ctx.clearRect(0, 0, this.lyricCanvas.width, this.lyricCanvas.height)
    if (image) {
      ctx.drawImage(image, 0, 0)
      image.close?.()
    }
    this.textTexture.userData.empty = !!empty
    this.textProbe = probe || null
    this.textTexture.needsUpdate = true
  }

  setClips(clips) {
    for (const texture of this.clips.textures) {
      if (!texture) continue
      texture.image?.close?.()
      texture.dispose()
    }
    this.clips = { meta: clips || [], textures: [] }
    this.slotTextures.fill(null)
    if (this.clips.meta.length) this.enableFootage()
    if (this.basePlan) this.updateFootagePlan()
    else this.footage = createFootageState()
  }

  setClipFrame({ index, frame, video }) {
    const textures = this.clips.textures
    let texture = textures[index]
    if (!texture) {
      texture = video ? new VideoTexture(video) : new VideoFrameTexture()
      texture.generateMipmaps = true
      texture.minFilter = LinearMipmapLinearFilter
      texture.magFilter = LinearFilter
      texture.wrapS = MirroredRepeatWrapping
      texture.wrapT = MirroredRepeatWrapping
      textures[index] = texture
    }
    if (!frame) return
    const previous = texture.image
    texture.userData.width = frame.displayWidth
    texture.userData.height = frame.displayHeight
    texture.setFrame(frame)
    if (previous !== frame) previous?.close?.()
  }

  runEval({ id, code }) {
    let result
    try {
      result = new Function('scene', code)(this)
    } catch (error) {
      result = `error: ${error?.message || error}`
    }
    Promise.resolve(result).then(value => this.emit({ type: 'evalResult', id, value }))
  }

  start() {
    if (this.rafId !== null || this.disposed || !this.visible) return
    this.lastFrameTime = null
    const tick = time => {
      this.rafId = requestAnimationFrame(tick)
      const delta = this.lastFrameTime === null ? 0 : (time - this.lastFrameTime) / 1000
      this.lastFrameTime = time
      this.frame(delta)
    }
    this.rafId = requestAnimationFrame(tick)
  }

  stop() {
    if (this.rafId !== null) cancelAnimationFrame(this.rafId)
    this.rafId = null
  }

  dispose() {
    this.disposed = true
    clearTimeout(this.planTimer)
    this.stop()
    this.statsStop?.()
    for (const texture of [this.layers.A, this.layers.B, this.layerDepth.A?.texture, this.layerDepth.B?.texture, this.textTexture, this.noiseTexture, ...this.clips.textures]) {
      if (texture && texture !== this.transparentPixel) texture.dispose()
    }
    this.layerSoft.A?.dispose()
    this.layerSoft.B?.dispose()
    this.softener.dispose()
    this.captureTarget.dispose()
    this.bgMaterial.dispose()
    this.fgMaterial.dispose()
    this.backdropMaterial.dispose()
    this.glassInteriorMaterial.dispose()
    this.glassButtonMaterial.dispose()
    for (const [id, materials] of Object.entries(this.lookMaterials)) {
      if (id === 'cover') continue
      for (const material of Object.values(materials)) material.dispose()
    }
    for (const tiles of Object.values(this.lookTiles)) {
      tiles.geometry.dispose()
      for (const material of tiles.materials) material.dispose()
    }
    this.glassButtonGeometry.dispose()
    this.glassBackdropGeometry.dispose()
    this.glassGeometry.dispose()
    this.glassInteriorGeometry.dispose()
    this.geometry.dispose()
    this.sceneTimer.dispose()
    this.renderer.dispose()
  }

  updateGlassSplit(regions, opacities, count) {
    const rects = this.splitScratch
    const glass = this.splitGlassScratch
    const n = opaquePanelRects(regions, opacities, count, rects)
    const glassCount = glassPanelRects(regions, opacities, count, glass)
    const button = radioButtonBounds(this.fgMaterial.uniforms, this.splitButtonScratch) ? this.splitButtonScratch : null
    let changed = n !== this.splitCount || glassCount !== this.splitGlassCount || !!button !== this.splitHasButton
    for (let i = 0; !changed && i < n * 4; i++) changed = rects[i] !== this.splitRects[i]
    for (let i = 0; !changed && i < glassCount * 4; i++) changed = glass[i] !== this.splitGlass[i]
    for (let i = 0; !changed && button && i < 4; i++) changed = button[i] !== this.splitButton[i]
    if (!changed) return
    this.splitCount = n
    this.splitRects.set(rects)
    this.splitGlassCount = glassCount
    this.splitGlass.set(glass)
    this.splitHasButton = !!button
    if (button) this.splitButton.set(button)
    const { inner, innerButton, outer, backdrop } = splitScreen(rects, n, glass, glassCount, button)
    if (inner.positions.length) setQuads(this.glassInteriorGeometry, inner)
    if (innerButton.positions.length) setQuads(this.glassButtonGeometry, innerButton)
    if (outer.positions.length) setQuads(this.glassGeometry, outer)
    if (backdrop.positions.length) setQuads(this.glassBackdropGeometry, backdrop)
    this.splitHasInterior = inner.positions.length > 0
    this.splitHasInteriorButton = innerButton.positions.length > 0
    this.splitHasOuter = outer.positions.length > 0
    this.splitHasBackdrop = backdrop.positions.length > 0
  }

  frame(delta) {
    const frameStart = performance.now()
    const bench = this.bench
    if (bench.skip || !this.programsReady) return
    if (bench.force) this.signature.force = true

    this.sceneTimer.poll()

    if (this.pendingSwap && --this.pendingSwap.frames <= 0) {
      this.frontLayer = this.pendingSwap.layer
      this.pendingSwap = null
      this.transitionProgress = 0
      this.applyLayers()
    }

    const state = this.state
    const now = Date.now()
    const effects = this.effects
    const isPlaying = state.isPlaying
    const progressMs = state.progressMs
    const visualQuality = state.visualQuality
    const reduceMotion = state.reduceMotion
    const bgUniforms = this.bgMaterial.uniforms
    const fgUniforms = this.fgMaterial.uniforms

    const perFrame = rate => 1 - Math.pow(1 - rate, delta * REFERENCE_FPS)

    if (progressMs !== this.lastSeenProgress) {
      this.localProgressUpdateTime = now
      this.lastSeenProgress = progressMs
      if (!isPlaying) this.interpolatedProgress = progressMs
    }
    this.interpolatedProgress = isPlaying ? progressMs + (now - this.localProgressUpdateTime) : progressMs

    const plan = this.plan
    const playhead = this.interpolatedProgress / 1000
    const at = plan ? directorAt(plan, playhead, this.directorCursor) : null
    const beat = stepFx(effects, plan, at, playhead, delta, isPlaying)
    const kickOut = reduceMotion || this.lightLevel < LIGHT_KICK_FLOOR ? 0 : beat.kick
    const pulseOut = reduceMotion ? 0 : effects.beatPulse

    const curve = this.lightCurve
    const lightTarget = !isPlaying ? 0 : curve
      ? curve[Math.min(curve.length - 1, Math.floor(this.interpolatedProgress / 1000 / LIGHT_CURVE_STEP_S))]
      : LIGHT_WITHOUT_ANALYSIS
    const lightRate = lightTarget > this.lightLevel ? LIGHT_ATTACK_RATE : LIGHT_RELEASE_RATE
    this.lightLevel = approach(this.lightLevel, lightTarget, lightRate, delta)
    if (this.lightLevel < 0.002 && lightTarget === 0) this.lightLevel = 0

    const { width, height, dpr, referenceDpr } = this.size
    const w = width * dpr
    const h = height * dpr
    const logicalWidth = width * referenceDpr
    const logicalHeight = height * referenceDpr

    fgUniforms.u_header_height.value = state.headerHeight / Math.max(height, 1)

    let minX = Infinity
    let minY = Infinity
    let maxX = -Infinity
    let maxY = -Infinity
    let hasVisiblePanels = false
    const regions = state.panelRegions
    const opacities = state.panelOpacities
    const panelCount = regions ? Math.min(regions.length, 7) : 0
    const packedRegions = fgUniforms.u_panel_regions.value
    const packedOpacities = fgUniforms.u_panel_opacities.value
    const packedIds = fgUniforms.u_panel_ids.value
    const opacityStep = perFrame(0.3)
    const fadeNow = performance.now()
    const chrome = 1 - state.chromeReveal
    const liftFloor = state.chromeLift / Math.max(height, 1)
    let packedCount = 0

    for (let i = 0; i < panelCount; i++) {
      const region = regions[i]
      const current = this.panelRegionVecs[i]
      const targetOpacity = opacities[i] || 0.0
      current.set(region.x, region.y, region.z, region.w)
      if (i === MOBILE_PANELS && liftFloor > 0 && current.y - current.w * 0.5 < liftFloor) {
        const top = current.y + current.w * 0.5
        current.y = (top + liftFloor) * 0.5
        current.w = Math.max(0, top - liftFloor)
      }
      this.panelOpacitiesLerp[i] += (targetOpacity - this.panelOpacitiesLerp[i]) * opacityStep
      const shown = i === PLAYER_PANEL ? this.panelOpacitiesLerp[i] * chrome : this.panelOpacitiesLerp[i]
      if (current.z > 0.01 && shown > 0.01) {
        hasVisiblePanels = true
        const halfWidth = current.z * 0.5
        const halfHeight = current.w * 0.5
        minX = Math.min(minX, current.x - halfWidth)
        minY = Math.min(minY, current.y - halfHeight)
        maxX = Math.max(maxX, current.x + halfWidth)
        maxY = Math.max(maxY, current.y + halfHeight)
      }
      if (current.z >= 0.01 && shown >= 0.01) {
        packedRegions[packedCount].copy(current)
        packedOpacities[packedCount] = shown
        packedIds[packedCount] = i
        packedCount++
      }
    }
    for (let i = packedCount; i < 7; i++) {
      packedRegions[i].set(0, 0, 0, 0)
      packedOpacities[i] = 0
      packedIds[i] = 0
    }
    fgUniforms.u_panel_count.value = packedCount
    if (hasVisiblePanels) {
      fgUniforms.u_panels_bounding_box.value.set(
        Math.max(0.0, minX - 0.02), Math.max(0.0, minY - 0.02),
        Math.min(1.0, maxX + 0.02), Math.min(1.0, maxY + 0.02)
      )
    } else {
      fgUniforms.u_panels_bounding_box.value.set(0, 0, 0, 0)
    }

    const rbPos = state.radioButtonPos
    if (rbPos && rbPos.radiusX > 0 && rbPos.radiusY > 0) {
      fgUniforms.u_radio_button_pos.value.set(rbPos.x, rbPos.y)
      fgUniforms.u_radio_button_radius.value.set(rbPos.radiusX, rbPos.radiusY)
    }

    const radioButton = state.radioButton
    fgUniforms.u_radio_button_state.value = stepFade(this.radioFade, radioButton.opacity, fadeNow)
    if (fgUniforms.u_radio_button_state.value > 0.01) hasVisiblePanels = true
    const buttonStep = perFrame(0.25)
    fgUniforms.u_radio_button_hover.value += ((radioButton.isHovered ? 1.0 : 0.0) - fgUniforms.u_radio_button_hover.value) * buttonStep
    fgUniforms.u_radio_button_pressed.value += ((radioButton.isPressed ? 1.0 : 0.0) - fgUniforms.u_radio_button_pressed.value) * buttonStep

    const radioProgress = state.radioProgress
    if (radioProgress) {
      let progressPercent = 0
      if (state.durationMs > 0) progressPercent = Math.min(100, Math.max(0, (progressMs / state.durationMs) * 100))
      this.smoothProgress += (progressPercent / 100 - this.smoothProgress) * perFrame(0.1)
      const stateInt = radioProgress.stateInt || 0
      fgUniforms.u_radio_progress.value = this.smoothProgress
      fgUniforms.u_radio_state_int.value = stateInt
      fgUniforms.u_radio_time.value += delta
      let c = radioProgress.currentVisualColor
      if (stateInt === 3 && state.speakerColor) c = state.speakerColor
      if (c) {
        this.scratchColor.set(c.r / 255, c.g / 255, c.b / 255)
        fgUniforms.u_visual_state_color.value.lerp(this.scratchColor, 5.0 * delta)
      }
    }

    const ambient = this.ambient
    const onAirColor = radioProgress?.onAirColor || null
    const speaking = radioProgress?.stateInt === 3
    const voiceTarget = speaking ? state.voiceTarget : 0
    ambient.voice = approach(ambient.voice, voiceTarget, voiceTarget > ambient.voice ? VOICE_ATTACK : VOICE_RELEASE, delta)
    if (ambient.voice < AMBIENT_FLOOR && voiceTarget === 0) ambient.voice = 0
    fgUniforms.u_voice_level.value = ambient.voice
    if (speaking && state.speakerColor) {
      const sc = state.speakerColor
      this.scratchColor.set(sc.r / 255, sc.g / 255, sc.b / 255)
      fgUniforms.u_voice_color.value.lerp(this.scratchColor, Math.min(1, VOICE_COLOR_RATE * delta))
    }

    const talkBreak = state.talkBreak
    const onAirTarget = talkBreak ? (talkBreak.paused ? ON_AIR_PAUSED : 1) : 0
    ambient.onAir = approach(ambient.onAir, onAirTarget, ON_AIR_RATE, delta)
    if (ambient.onAir < AMBIENT_FLOOR && onAirTarget === 0) ambient.onAir = 0
    let breathe = 1
    if (ambient.onAir > 0 && !talkBreak?.paused && !reduceMotion && state.levelIndex >= 1) {
      ambient.breatheTime += delta
      breathe = 0.8 + 0.2 * Math.sin(ambient.breatheTime * Math.PI * 2 / ON_AIR_BREATHE_SECONDS)
    }
    fgUniforms.u_on_air.value = ambient.onAir * breathe

    const crossfadeMs = state.crossfadeMs || 0
    const crossfadeTarget = crossfadeMs >= CROSSFADE_GLOW_MIN_MS ? CROSSFADE_GLOW : 0
    const crossfadeRate = crossfadeTarget > ambient.crossfade ? 4000 / Math.max(crossfadeMs, 1) : CROSSFADE_RELEASE
    ambient.crossfade = approach(ambient.crossfade, crossfadeTarget, crossfadeRate, delta)
    if (ambient.crossfade < AMBIENT_FLOOR && crossfadeTarget === 0) ambient.crossfade = 0
    const accent = state.accentRgb
    this.scratchColor.set(accent.r / 255, accent.g / 255, accent.b / 255)
    this.crossfadeColor.lerp(this.scratchColor, Math.min(1, VOICE_COLOR_RATE * delta))
    if (onAirColor) {
      this.scratchColor.set(onAirColor.r / 255, onAirColor.g / 255, onAirColor.b / 255)
      fgUniforms.u_on_air_color.value.lerp(this.scratchColor, Math.min(1, VOICE_COLOR_RATE * delta))
    }

    fgUniforms.u_glass_blur_factor.value = visualQuality === 'high' ? this.glassBlurFactor : 0
    fgUniforms.u_enable_refraction.value = visualQuality === 'high' ? 1.0 : 0.0
    fgUniforms.u_glass_taps.value = state.glassTaps
    fgUniforms.u_audio_pulse.value = effects.beatPulse
    fgUniforms.u_scroll_offset.value = state.scrollPosition

    const targetGradientColor = onAirColor || state.seedColor
    this.scratchColor.set(targetGradientColor.r / 255, targetGradientColor.g / 255, targetGradientColor.b / 255)
    fgUniforms.u_player_gradient_color.value.lerp(this.scratchColor, 2.0 * delta)
    const hasGradient = targetGradientColor.r > 0 || targetGradientColor.g > 0 || targetGradientColor.b > 0
    fgUniforms.u_player_gradient_intensity.value += ((hasGradient ? 1.0 : 0.0) - fgUniforms.u_player_gradient_intensity.value) * perFrame(0.05)

    const click = this.click
    if (click.active && visualQuality === 'high') {
      const decay = Math.max(0, 1 - (now - click.at) / CLICK_DECAY_MS)
      if (decay > 0) {
        const intensity = click.intensity * decay
        effects.glitchX += (Math.random() - 0.5) * intensity * 100.0
        effects.glitchY += (Math.random() - 0.5) * intensity * 100.0
        effects.chromatic += intensity * 40.0
        effects.rotation += (Math.random() - 0.5) * intensity * 8.0
        const targetBrightness = 0.6 + (intensity * 0.3)
        effects.brightness += (targetBrightness - effects.brightness) * perFrame(0.3)
      } else {
        click.active = false
      }
    }

    effects.blur *= 1 - perFrame(0.1)
    if (state.scrollVelocity > 0.01 && visualQuality === 'high') effects.blur += state.scrollVelocity * 50.0

    const gyro = state.gyro || NO_PARALLAX
    const mouse = state.mouse || NO_PARALLAX
    const hasGyro = Math.abs(gyro.parallaxX) > 0.001 || Math.abs(gyro.parallaxY) > 0.001
    const pX = hasGyro ? gyro.parallaxX * 40 : mouse.parallaxX * 40
    const pY = hasGyro ? gyro.parallaxY * 40 : mouse.parallaxY * 40

    if (this.transitionProgress < 1) this.transitionProgress = Math.min(1, this.transitionProgress + Math.min(delta, ARTWORK_FADE_MAX_STEP_S) / DURATION.theme)

    bgUniforms.u_time.value = (bgUniforms.u_time.value + delta) % 1000.0
    bgUniforms.u_transition.value = trackFadeEase(this.transitionProgress)
    if (reduceMotion) {
      bgUniforms.u_parallax.value.set(0, 0)
      bgUniforms.u_glitch.value.set(0, 0)
      bgUniforms.u_frame_offset.value.set(0, 0)
      bgUniforms.u_frame_scale.value = 1.0
      bgUniforms.u_scale.value = 1.0
      bgUniforms.u_rotation.value = 0.0
      bgUniforms.u_orbit.value.set(0, 0, 0)
    } else {
      bgUniforms.u_parallax.value.set(pX, pY)
      setCamera(bgUniforms, effects, -pX / Math.max(logicalWidth, 1), -pY / Math.max(logicalHeight, 1))
    }
    bgUniforms.u_brightness.value = effects.brightness
    bgUniforms.u_contrast.value = effects.contrast
    bgUniforms.u_saturation.value = effects.saturation
    bgUniforms.u_hue.value = effects.hue * Math.PI / 180
    setGrade(bgUniforms)
    bgUniforms.u_chromatic.value = visualQuality === 'high' && !reduceMotion ? effects.chromatic : 0
    applyFocus(bgUniforms, this.focus, this.depthGrid, true, delta, effects, visualQuality === 'high')
    bgUniforms.u_flicker.value = effects.flicker
    this.direct(at, reduceMotion, logicalWidth, logicalHeight, reduceMotion ? 0 : pX, reduceMotion ? 0 : pY)

    if (this.clips.meta.length) this.stepClips(plan, at, playhead, delta, isPlaying && !reduceMotion)
    const footageActive = isPlaying && this.footageLooks.has(this.look)

    const rtHeight = this.captureResolution
    const rtWidth = Math.floor(rtHeight * (w / h))
    if (Math.abs(this.captureTarget.width - rtWidth) > 1 || Math.abs(this.captureTarget.height - rtHeight) > 1) {
      this.captureTarget.setSize(rtWidth, rtHeight)
      this.signature.captureStale = true
    }

    const textTexture = this.textTexture
    if (textTexture) bgUniforms.u_text_texture.value = textTexture
    bgUniforms.u_text_empty.value = textTexture?.userData?.empty ? 1.0 : 0.0
    bgUniforms.u_canvas_resolution.value.set(logicalWidth, logicalHeight)

    const voiceLevel = fgUniforms.u_voice_level.value
    const onAirLevel = fgUniforms.u_on_air.value
    const crossfadeLevel = ambient.crossfade
    fgUniforms.u_glow_active.value = voiceLevel >= 0.001 || onAirLevel >= 0.001 || crossfadeLevel >= 0.001 ? 1.0 : 0.0
    const glowAnchorMix = Math.min(1, Math.max(0, fgUniforms.u_radio_button_state.value))
    const glowAnchor = fgUniforms.u_radio_button_pos.value
    fgUniforms.u_glow_center.value.set(
      0.5 + (Math.min(1, Math.max(0, glowAnchor.x)) - 0.5) * glowAnchorMix,
      0.55 + (Math.min(1, Math.max(0, glowAnchor.y)) - 0.55) * glowAnchorMix
    )
    fgUniforms.u_glow_scale.value.set(logicalWidth / Math.max(logicalHeight, 1) * GLOW_FALLOFF_SCALE, GLOW_FALLOFF_SCALE)
    fgUniforms.u_voice_glow.value.copy(fgUniforms.u_voice_color.value).multiplyScalar(voiceLevel * 0.45)
    fgUniforms.u_on_air_glow.value.copy(fgUniforms.u_on_air_color.value).multiplyScalar(onAirLevel)
      .addScaledVector(this.crossfadeColor, crossfadeLevel)
    const panelGlowMix = Math.min(1, Math.max(0, onAirLevel)) * 0.85
    const onAirTint = fgUniforms.u_on_air_color.value
    fgUniforms.u_panel_glow.value.set(
      0.6 + (onAirTint.x - 0.6) * panelGlowMix,
      0.7 + (onAirTint.y - 0.7) * panelGlowMix,
      0.9 + (onAirTint.z - 0.9) * panelGlowMix
    ).multiplyScalar(0.2 + 0.3 * onAirLevel)

    const signature = this.signature
    let sigLength = writeUniformSignature(signature.current, 0, bgUniforms, BG_SIGNATURE_UNIFORMS)
    signature.current[sigLength++] = Math.abs(effects.glitchX) > 20 ? bgUniforms.u_time.value : 0
    signature.current[sigLength++] = bgUniforms.u_parallax.value.x * PARALLAX_SIGNATURE_SCALE
    signature.current[sigLength++] = bgUniforms.u_parallax.value.y * PARALLAX_SIGNATURE_SCALE
    signature.current[sigLength++] = w
    signature.current[sigLength++] = h
    signature.current[sigLength++] = this.lookIndex[this.look]
    sigLength = writeUniformSignature(signature.current, sigLength, bgUniforms, this.lookSignatures[this.look])
    const captureLength = sigLength
    sigLength = writeUniformSignature(signature.current, sigLength, fgUniforms, FG_SIGNATURE_UNIFORMS)
    signature.current[sigLength++] = fgUniforms.u_radio_state_int.value === 4 ? fgUniforms.u_radio_time.value : 0

    const textures = signature.textures
    const textVersion = textTexture ? textTexture.version : -1
    const wantsRender = signature.force ||
      footageActive ||
      hasVisiblePanels !== signature.fgVisible ||
      textures[0] !== bgUniforms.u_texture.value ||
      textures[1] !== bgUniforms.u_texture_prev.value ||
      textures[2] !== bgUniforms.u_text_texture.value ||
      textures[3] !== fgUniforms.u_noise_texture.value ||
      textVersion !== signature.textVersion ||
      sigLength !== signature.length ||
      signatureChanged(signature.current, signature.previous, sigLength)

    const captureDirty = signature.force || signature.captureStale || footageActive ||
      textures[0] !== bgUniforms.u_texture.value ||
      textures[1] !== bgUniforms.u_texture_prev.value ||
      textures[2] !== bgUniforms.u_text_texture.value ||
      textVersion !== signature.textVersion ||
      captureLength !== signature.captureLength ||
      signatureChanged(signature.current, signature.captured, captureLength)

    let probe = null
    if (frameStart - this.probeAt >= PROBE_INTERVAL_MS && state.probeWanted) {
      this.probeAt = frameStart
      const u = bgUniforms
      const p = this.probeParams
      p.current = artworkPixels(u.u_texture.value?.image)
      p.previous = artworkPixels(u.u_texture_prev.value?.image)
      p.transition = u.u_transition.value
      p.width = rtWidth
      p.height = rtHeight
      p.texWidth = u.u_tex_resolution.value.x
      p.texHeight = u.u_tex_resolution.value.y
      p.parallaxX = u.u_parallax.value.x
      p.parallaxY = u.u_parallax.value.y
      p.glitchX = u.u_glitch.value.x
      p.glitchY = u.u_glitch.value.y
      p.frameScale = u.u_frame_scale.value
      p.frameOffsetX = u.u_frame_offset.value.x
      p.frameOffsetY = u.u_frame_offset.value.y
      p.scale = u.u_scale.value
      p.rotation = u.u_rotation.value
      p.contrast = u.u_contrast.value
      p.brightness = u.u_brightness.value
      p.saturation = u.u_saturation.value
      p.hue = u.u_hue.value
      p.flicker = u.u_flicker.value
      p.text = u.u_text_empty.value < 0.5 ? this.textProbe : null
      p.split = this.look === 'split' || this.look === 'splitFootage' ? u : null
      probe = sampleBackgroundProbe(p).slice()
    }

    if (bench.uniforms) {
      for (const name in bench.uniforms) {
        const target = bgUniforms[name] || fgUniforms[name]
        if (target) target.value = bench.uniforms[name]
      }
    }

    const needsRender = wantsRender && (signature.force || !state.renderPaused)
    const renderer = this.renderer
    const gl = this.gl

    if (needsRender) {
      this.sceneTimer.begin()
      signature.force = false
      signature.fgVisible = hasVisiblePanels
      signature.textVersion = textVersion
      signature.length = sigLength
      textures[0] = bgUniforms.u_texture.value
      textures[1] = bgUniforms.u_texture_prev.value
      textures[2] = bgUniforms.u_text_texture.value
      textures[3] = fgUniforms.u_noise_texture.value
      const swap = signature.previous
      signature.previous = signature.current
      signature.current = swap

      const timing = bench.timing
      if (hasVisiblePanels && captureDirty) {
        bgUniforms.u_canvas_resolution.value.set(rtWidth, rtHeight)
        bgUniforms.u_is_capture.value = 1.0
        if (timing) { gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, timing.pixel); timing.t = performance.now() }
        renderer.setRenderTarget(this.captureTarget)
        renderer.render(this.captureScene, this.captureCamera)
        if (timing) gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, timing.pixel)
        renderer.setRenderTarget(null)
        if (timing) { timing.capture = (timing.capture || 0) + performance.now() - timing.t; timing.captures = (timing.captures || 0) + 1 }
        bgUniforms.u_is_capture.value = 0.0
        bgUniforms.u_canvas_resolution.value.set(logicalWidth, logicalHeight)
        signature.captureStale = false
        signature.captureLength = captureLength
        signature.captured.set(signature.previous.subarray(0, captureLength))
      } else if (!hasVisiblePanels) {
        signature.captureStale = true
      }

      if (hasVisiblePanels) this.updateGlassSplit(packedRegions, packedOpacities, packedCount)
      this.glassMesh.visible = hasVisiblePanels && this.splitHasOuter
      this.glassInteriorMesh.visible = hasVisiblePanels && this.splitHasInterior
      this.glassButtonMesh.visible = hasVisiblePanels && this.splitHasInteriorButton
      this.glassBackdropMesh.visible = hasVisiblePanels && this.splitHasBackdrop
      this.backdropMesh.visible = !hasVisiblePanels
      if (!hasVisiblePanels) this.setBackdrop()

      if (timing) { gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, timing.pixel); timing.t = performance.now() }
      renderer.render(this.mainScene, this.mainCamera)
      if (timing) for (let i = 1; i < (timing.repeat || 1); i++) renderer.render(this.mainScene, this.mainCamera)
      for (let i = 0; i < bench.extra; i++) renderer.render(this.mainScene, this.mainCamera)
      if (timing) { gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, timing.pixel); timing.main = (timing.main || 0) + (performance.now() - timing.t) / (timing.repeat || 1); timing.frames = (timing.frames || 0) + 1 }
      this.sceneTimer.end()
      if (!this.splashSent) {
        this.splashSent = true
        this.emit({ type: 'ready' })
      }
    }

    let work = null
    if (state.statsActive) {
      addFrameWork('scene js', performance.now() - frameStart)
      addFrameWork('scene frames', 1)
      if (this.relayWork) work = takeFrameWork(1)
    }

    this.emit({
      type: 'tick',
      delta,
      wanted: wantsRender && !state.renderPaused,
      rendered: needsRender,
      kick: kickOut,
      pulse: pulseOut,
      level: this.lightLevel,
      target: lightTarget,
      glow: [
        fgUniforms.u_glow_active.value,
        fgUniforms.u_glow_center.value.x, fgUniforms.u_glow_center.value.y,
        fgUniforms.u_glow_scale.value.x, fgUniforms.u_glow_scale.value.y,
        fgUniforms.u_voice_glow.value.x, fgUniforms.u_voice_glow.value.y, fgUniforms.u_voice_glow.value.z,
        fgUniforms.u_on_air_glow.value.x, fgUniforms.u_on_air_glow.value.y, fgUniforms.u_on_air_glow.value.z,
      ],
      probe,
      work,
    })
  }
}
