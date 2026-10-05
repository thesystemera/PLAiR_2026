import {
  BufferGeometry,
  CanvasTexture,
  ClampToEdgeWrapping,
  ColorManagement,
  DataTexture,
  Float32BufferAttribute,
  LinearFilter,
  LinearSRGBColorSpace,
  MathUtils,
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
import { backgroundVertexShader, backgroundFragmentShader, sceneFragmentShader, sceneGlassFragmentShader, backdropFragmentShader } from './sceneShaders'
import { lowerBound, numericAscending, pushEnergySample } from './sceneEffects'
import { artworkPixels, sampleBackgroundProbe } from './backgroundProbe'
import { addFrameWork, createGpuTimer, takeFrameWork, watchFrameStats } from './frameStats'

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
const SIGNATURE_SIZE = 160
const PARALLAX_SIGNATURE_SCALE = SIGNATURE_EPSILON / 0.05
const UNDERLAY_LEVEL = 0x0a / 255 * 0.5
const ARTWORK_SIZE = 512
const CLICK_DECAY_MS = 500
const ARTWORK_SWAP_FRAMES = 2
const PANEL_CORNER_RADIUS = 0.015
const OPAQUE_PANEL_OPACITY = 0.9995
const MAX_SPLIT_RECTS = 7
export const LYRIC_WIDTH = 1024
export const LYRIC_HEIGHT = 512

const BG_SIGNATURE_UNIFORMS = [
  'u_video_clip_blend', 'u_transition', 'u_tex_resolution', 'u_glitch', 'u_scale',
  'u_frame_offset', 'u_frame_scale', 'u_rotation', 'u_brightness', 'u_contrast', 'u_saturation',
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

async function loadArtworkTexture(url, targetSize) {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`artwork ${response.status}`)
  const bitmap = await createImageBitmap(await response.blob())
  const shrink = targetSize && (bitmap.width > targetSize || bitmap.height > targetSize)
  const canvas = makeCanvas(shrink ? targetSize : bitmap.width, shrink ? targetSize : bitmap.height)
  canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height)
  bitmap.close?.()
  const texture = new CanvasTexture(canvas)
  texture.wrapS = ClampToEdgeWrapping
  texture.wrapT = ClampToEdgeWrapping
  texture.generateMipmaps = true
  texture.needsUpdate = true
  return texture
}

function opaquePanelRects(regions, opacities, count, out) {
  let n = 0
  for (let i = 0; i < count; i++) {
    const region = regions[i]
    if (region.z < 0.01 || opacities[i] < OPAQUE_PANEL_OPACITY) continue
    const halfWidth = region.z * 0.5 - PANEL_CORNER_RADIUS
    const halfHeight = region.w * 0.5 - PANEL_CORNER_RADIUS
    if (halfWidth <= 0 || halfHeight <= 0) continue
    out[n * 4] = Math.max(0, region.x - halfWidth)
    out[n * 4 + 1] = Math.max(0, region.y - halfHeight)
    out[n * 4 + 2] = Math.min(1, region.x + halfWidth)
    out[n * 4 + 3] = Math.min(1, region.y + halfHeight)
    if (out[n * 4] < out[n * 4 + 2] && out[n * 4 + 1] < out[n * 4 + 3]) n++
  }
  return n
}

function pushQuad(positions, uvs, x0, y0, x1, y1) {
  const corners = [x0, y0, x1, y0, x1, y1, x0, y0, x1, y1, x0, y1]
  for (let i = 0; i < corners.length; i += 2) {
    positions.push(corners[i] * 2 - 1, corners[i + 1] * 2 - 1, 0)
    uvs.push(corners[i], corners[i + 1])
  }
}

function splitScreen(rects, count) {
  const xs = [0, 1]
  const ys = [0, 1]
  for (let i = 0; i < count; i++) {
    xs.push(rects[i * 4], rects[i * 4 + 2])
    ys.push(rects[i * 4 + 1], rects[i * 4 + 3])
  }
  const sortUnique = values => [...new Set(values)].sort((a, b) => a - b)
  const columns = sortUnique(xs)
  const rows = sortUnique(ys)
  const inner = { positions: [], uvs: [] }
  const outer = { positions: [], uvs: [] }
  for (let j = 0; j < rows.length - 1; j++) {
    const y0 = rows[j]
    const y1 = rows[j + 1]
    const cy = (y0 + y1) * 0.5
    let runStart = 0
    let runInside = null
    for (let i = 0; i <= columns.length - 1; i++) {
      let inside = null
      if (i < columns.length - 1) {
        const cx = (columns[i] + columns[i + 1]) * 0.5
        inside = false
        for (let k = 0; k < count; k++) {
          if (cx > rects[k * 4] && cx < rects[k * 4 + 2] && cy > rects[k * 4 + 1] && cy < rects[k * 4 + 3]) { inside = true; break }
        }
      }
      if (inside !== runInside) {
        if (runInside !== null) {
          const target = runInside ? inner : outer
          pushQuad(target.positions, target.uvs, columns[runStart], y0, columns[i], y1)
        }
        runStart = i
        runInside = inside
      }
    }
  }
  return { inner, outer }
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
  constructor({ canvas, captureResolution = 128, glassBlurFactor = 1, emit }) {
    this.emit = emit
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

    this.effects = {
      chromatic: 0, glitchX: 0, glitchY: 0, rotation: 0, brightness: 0.6, saturation: 1, contrast: 1,
      scale: 1.0, hue: 0, blur: 0, flicker: 1, currentEnergy: 0, macroEnergy: 0.5,
      frameOffset: new Vector2(0, 0), targetFrameOffset: new Vector2(0, 0), frameScale: 1.0, targetFrameScale: 1.0, beatPulse: 0.0,
    }
    this.beatState = { threshold: 0.65, intensity: 0, onBeat: false }
    this.energyHistory = []
    this.energyScratch = []
    this.energyStamps = []
    this.signature = {
      current: new Float64Array(SIGNATURE_SIZE), previous: new Float64Array(SIGNATURE_SIZE), length: 0,
      textures: [null, null, null, null], textVersion: -1, fgVisible: false, force: true, captureStale: true,
      captureLength: 0, captured: new Float64Array(SIGNATURE_SIZE),
    }
    this.audioFeatures = null
    this.visualCueMap = new Map()
    this.lastBeatIndex = 0
    this.lastSegmentIndex = 0
    this.tempoTime = 0
    this.interpolatedProgress = 0
    this.lastSeenProgress = 0
    this.localProgressUpdateTime = Date.now()
    this.scratchColor = new Vector3()
    this.ambient = { voice: 0, onAir: 0, breatheTime: 0, crossfade: 0 }
    this.crossfadeColor = new Vector3()
    this.smoothProgress = 0
    this.panelOpacitiesLerp = [0, 0, 0, 0, 0, 0, 0]
    this.panelRegionVecs = Array.from({ length: 7 }, () => new Vector4())
    this.probeAt = 0
    this.probeParams = {}
    this.lightCurve = null
    this.lightLevel = 0
    this.lastKickBeat = -1
    this.click = { intensity: 0, at: 0, active: false }
    this.transitionProgress = 1
    this.layers = { A: this.transparentPixel, B: this.transparentPixel }
    this.frontLayer = 'A'
    this.artworkUrl = null
    this.artworkSize = undefined
    this.artworkTokens = { A: 0, B: 0 }
    this.pendingSwap = null
    this.clips = { textures: [], currentIndex: 0, blend: 0, count: 0, rate: 0 }

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
        u_depth_map: { value: this.transparentPixel },
        u_text_texture: { value: this.transparentPixel },
        u_text_empty: { value: 0.0 },
        u_video_clip: { value: this.transparentPixel },
        u_video_clip_blend: { value: 0.0 },
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
        u_max_blur: { value: 0.0 },
        u_chromatic: { value: 0.0 },
        u_flicker: { value: 1.0 },
        u_has_depth_map: { value: 0.0 },
        u_focal_depth: { value: 0.5 },
        u_focal_range: { value: 0.1 },
        u_is_capture: { value: 0.0 },
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
    this.glassGeometry = new BufferGeometry()
    this.glassInteriorGeometry = new BufferGeometry()
    setQuads(this.glassGeometry, splitScreen(null, 0).outer)
    setQuads(this.glassInteriorGeometry, splitScreen(null, 0).outer)
    this.splitRects = new Float64Array(MAX_SPLIT_RECTS * 4)
    this.splitScratch = new Float64Array(MAX_SPLIT_RECTS * 4)
    this.splitCount = -1
    this.splitHasInterior = false
    this.splitHasOuter = true

    this.captureMesh = new Mesh(this.geometry, this.bgMaterial)
    this.glassMesh = new Mesh(this.glassGeometry, this.fgMaterial)
    this.glassInteriorMesh = new Mesh(this.glassInteriorGeometry, this.glassInteriorMaterial)
    this.backdropMesh = new Mesh(this.geometry, this.backdropMaterial)
    for (const mesh of [this.captureMesh, this.glassMesh, this.glassInteriorMesh, this.backdropMesh]) mesh.frustumCulled = false
    this.captureScene.add(this.captureMesh)
    this.mainScene.add(this.glassMesh)
    this.mainScene.add(this.glassInteriorMesh)
    this.mainScene.add(this.backdropMesh)
    for (const node of [this.mainScene, this.captureScene, this.mainCamera, this.captureCamera]) {
      node.updateMatrixWorld(true)
      node.matrixWorldAutoUpdate = false
    }

    Promise.all([
      this.renderer.compileAsync(this.captureScene, this.captureCamera),
      this.renderer.compileAsync(this.mainScene, this.mainCamera),
    ]).then(() => { this.programsReady = true })

    let rendererName = ''
    try {
      const info = this.gl.getExtension('WEBGL_debug_renderer_info')
      rendererName = String(this.gl.getParameter(info ? info.UNMASKED_RENDERER_WEBGL : this.gl.RENDERER) || '')
    } catch {
      rendererName = ''
    }
    this.emit({ type: 'renderer', name: rendererName })
  }

  handle(message) {
    switch (message.type) {
      case 'size': this.setSize(message); break
      case 'state': Object.assign(this.state, message.state); this.updateStats(); break
      case 'features': this.setFeatures(message.features); break
      case 'artwork': this.setArtwork(message.url, message.fullscreen); break
      case 'lyric': this.setLyric(message); break
      case 'clips': this.setClipCount(message.count); break
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

  setFeatures(features) {
    this.audioFeatures = features || null
    this.lightCurve = buildLightCurve(features)
    if (!features) return
    this.lastBeatIndex = 0
    this.lastSegmentIndex = 0
    const { loudness_segments: segments, beats } = features
    if (!segments || segments.length === 0 || !beats || beats.length === 0) return
    const cueMap = new Map()
    let beatCounter = 0
    beats.forEach((beatTime, index) => {
      const cues = new Set()
      if (index % 16 === 0) cues.add('CAMERA_CUT')
      beatCounter++
      if (beatCounter % 2 === 0) cues.add('SMALL_ROTATION')
      if (cues.size > 0) cueMap.set(beatTime, cues)
    })
    this.visualCueMap = cueMap
    this.tempoTime = 0
  }

  setArtwork(url, fullscreen) {
    if (!url) return
    const targetSize = fullscreen ? null : ARTWORK_SIZE
    const isNew = url !== this.artworkUrl
    if (!isNew && targetSize === this.artworkSize) return
    const layer = isNew ? (this.frontLayer === 'A' ? 'B' : 'A') : this.frontLayer
    const token = ++this.artworkTokens[layer]
    this.artworkUrl = url
    this.artworkSize = targetSize
    loadArtworkTexture(url, targetSize).then(texture => {
      if (this.disposed || this.artworkTokens[layer] !== token) {
        texture.dispose()
        return
      }
      const old = this.layers[layer]
      this.layers[layer] = texture
      if (old !== this.transparentPixel) old.dispose()
      if (isNew) this.pendingSwap = { layer, frames: ARTWORK_SWAP_FRAMES }
      else this.applyLayers()
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
    u.u_has_depth_map.value = 0.0
  }

  setLyric({ image, empty, probe }) {
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

  setClipCount(count) {
    for (const texture of this.clips.textures) texture.dispose()
    this.clips = { textures: [], currentIndex: 0, blend: 0, count, rate: 0, sources: [] }
  }

  setClipFrame({ index, frame, video }) {
    const clips = this.clips
    let texture = clips.textures[index]
    if (!texture) {
      texture = video ? new VideoTexture(video) : new VideoFrameTexture()
      texture.minFilter = LinearFilter
      texture.magFilter = LinearFilter
      clips.textures[index] = texture
    }
    if (frame) texture.setFrame(frame)
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
    this.stop()
    this.statsStop?.()
    for (const texture of [this.layers.A, this.layers.B, this.textTexture, this.noiseTexture, ...this.clips.textures]) {
      if (texture && texture !== this.transparentPixel) texture.dispose()
    }
    this.captureTarget.dispose()
    this.bgMaterial.dispose()
    this.fgMaterial.dispose()
    this.backdropMaterial.dispose()
    this.glassInteriorMaterial.dispose()
    this.glassGeometry.dispose()
    this.glassInteriorGeometry.dispose()
    this.geometry.dispose()
    this.sceneTimer.dispose()
    this.renderer.dispose()
  }

  updateGlassSplit(regions, opacities, count) {
    const rects = this.splitScratch
    const n = opaquePanelRects(regions, opacities, count, rects)
    let changed = n !== this.splitCount
    for (let i = 0; !changed && i < n * 4; i++) changed = rects[i] !== this.splitRects[i]
    if (!changed) return
    this.splitCount = n
    this.splitRects.set(rects)
    const { inner, outer } = splitScreen(rects, n)
    if (inner.positions.length) setQuads(this.glassInteriorGeometry, inner)
    if (outer.positions.length) setQuads(this.glassGeometry, outer)
    this.splitHasInterior = inner.positions.length > 0
    this.splitHasOuter = outer.positions.length > 0
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

    const features = this.audioFeatures
    const tempo = features?.tempo || 120
    const beatDurationMs = (60 / tempo) * 1000
    const tempoSyncedDecay = Math.min(1.0, (delta * 1000) / (beatDurationMs * 1.5))
    const fastDecay = Math.min(1.0, (delta * 1000) / (beatDurationMs * 0.8))

    let kickOut = 0
    let pulseOut = 0
    if (isPlaying && features?.loudness_segments) {
      const currentTime = this.interpolatedProgress / 1000
      const segments = features.loudness_segments
      const beats = features.beats || []

      let segIdx = this.lastSegmentIndex
      while (segIdx < segments.length - 1 && segments[segIdx + 1].start <= currentTime) segIdx++
      this.lastSegmentIndex = segIdx
      const currentSegment = segments[segIdx]

      if (currentSegment) {
        const minL = features.min_loudness || -60
        const peakL = features.peak_loudness || -1
        const rawEnergy = Math.max(0, Math.min(1, (currentSegment.loudness - minL) / (peakL - minL)))
        effects.currentEnergy = rawEnergy

        const beatState = this.beatState
        beatState.threshold = pushEnergySample(this.energyHistory, this.energyStamps, this.energyScratch, rawEnergy, now)
        beatState.intensity = Math.max(0, (rawEnergy - beatState.threshold) / (1.0 - beatState.threshold))

        let onBeat = false
        let beatHit = -1
        for (let i = this.lastBeatIndex; i < beats.length; i++) {
          const beatTime = beats[i]
          if (beatTime > currentTime + 0.08) break
          if (Math.abs(beatTime - currentTime) < 0.08) {
            onBeat = true
            beatHit = beatTime
            this.lastBeatIndex = Math.max(0, i - 1)
            const cues = this.visualCueMap.get(beatTime)
            if (cues) {
              if (cues.has('CAMERA_CUT')) {
                const magnitude = 0.3 + (rawEnergy * 0.4)
                if (Math.random() < 0.2) {
                  effects.targetFrameOffset.set(0, 0)
                  effects.targetFrameScale = 1.0
                } else {
                  effects.targetFrameScale = 1.0 + (Math.random() * magnitude)
                  effects.targetFrameOffset.set((Math.random() - 0.5) * magnitude * 0.5, (Math.random() - 0.5) * magnitude * 0.5)
                }
                effects.frameOffset.copy(effects.targetFrameOffset)
                effects.frameScale = effects.targetFrameScale
                if (this.clips.count > 0) {
                  this.clips.currentIndex = (this.clips.currentIndex + 1) % this.clips.count
                  this.emit({ type: 'clipIndex', index: this.clips.currentIndex })
                }
              }
              if (cues.has('SMALL_ROTATION')) effects.rotation += (Math.random() - 0.5) * 10.0 * rawEnergy
            }
            break
          }
        }
        beatState.onBeat = onBeat && rawEnergy > beatState.threshold && beatState.intensity > 0.4

        if (beatState.onBeat) {
          effects.glitchX = (Math.random() - 0.5) * beatState.intensity * 150.0
          effects.glitchY = (Math.random() - 0.5) * beatState.intensity * 150.0
          if (beatState.intensity > 0.5 && visualQuality === 'high') {
            effects.hue += beatState.intensity * 30.0
            effects.chromatic = beatState.intensity * 80.0
            effects.blur += beatState.intensity * 25.0
          }
        }

        const kick = onBeat && beatHit !== this.lastKickBeat && rawEnergy > beatState.threshold
        if (kick) this.lastKickBeat = beatHit

        if (!beatState.onBeat) {
          effects.glitchX *= (1.0 - fastDecay)
          effects.glitchY *= (1.0 - fastDecay)
        }

        effects.hue *= (1.0 - tempoSyncedDecay)
        effects.chromatic *= (1.0 - fastDecay)
        effects.rotation *= (1.0 - fastDecay)
        effects.brightness += ((0.25 + (rawEnergy * 0.5)) - effects.brightness) * perFrame(0.1)
        effects.saturation += ((0.8 + (rawEnergy * 0.4)) - effects.saturation) * perFrame(0.1)
        effects.contrast += ((0.9 + (rawEnergy * 0.2)) - effects.contrast) * perFrame(0.1)

        const halfSpeedBps = tempo / 120.0
        this.tempoTime += delta
        const breathing = (Math.sin(this.tempoTime * halfSpeedBps * Math.PI * 2.0) + 1.0) / 2.0

        effects.beatPulse = breathing * (0.2 + rawEnergy * 0.8)
        effects.flicker += ((1.0 - (breathing * rawEnergy * 0.2)) - effects.flicker) * perFrame(0.2)
        effects.scale += ((1.0 + (breathing * rawEnergy * 0.1)) - effects.scale) * perFrame(0.05)

        const lightUp = this.lightLevel >= LIGHT_KICK_FLOOR
        kickOut = reduceMotion || !kick || !lightUp ? 0 : Math.min(1, 0.5 + beatState.intensity * 0.5)
        pulseOut = reduceMotion ? 0 : effects.beatPulse
      }
    } else {
      effects.chromatic *= (1.0 - fastDecay)
      effects.glitchX *= (1.0 - fastDecay)
      effects.glitchY *= (1.0 - fastDecay)
      effects.rotation += (0 - effects.rotation) * perFrame(0.1)
      effects.brightness += (0.6 - effects.brightness) * perFrame(0.05)
      effects.saturation += (1 - effects.saturation) * perFrame(0.05)
      effects.contrast += (1 - effects.contrast) * perFrame(0.05)
      effects.scale += (1.0 - effects.scale) * perFrame(0.05)
      effects.flicker += (1.0 - effects.flicker) * perFrame(0.1)
      effects.hue += (0 - effects.hue) * perFrame(0.1)
      effects.targetFrameOffset.set(0, 0)
      effects.targetFrameScale = 1.0
      effects.frameOffset.copy(effects.targetFrameOffset)
      effects.frameScale = effects.targetFrameScale
      effects.beatPulse = 0.0
      this.beatState.onBeat = false
    }

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
    let packedCount = 0

    for (let i = 0; i < panelCount; i++) {
      const region = regions[i]
      const current = this.panelRegionVecs[i]
      const targetOpacity = opacities[i] || 0.0
      current.set(region.x, region.y, region.z, region.w)
      this.panelOpacitiesLerp[i] += (targetOpacity - this.panelOpacitiesLerp[i]) * opacityStep
      if (current.z > 0.01 && this.panelOpacitiesLerp[i] > 0.01) {
        hasVisiblePanels = true
        const halfWidth = current.z * 0.5
        const halfHeight = current.w * 0.5
        minX = Math.min(minX, current.x - halfWidth)
        minY = Math.min(minY, current.y - halfHeight)
        maxX = Math.max(maxX, current.x + halfWidth)
        maxY = Math.max(maxY, current.y + halfHeight)
      }
      if (current.z >= 0.01 && this.panelOpacitiesLerp[i] >= 0.01) {
        packedRegions[packedCount].copy(current)
        packedOpacities[packedCount] = this.panelOpacitiesLerp[i]
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
    fgUniforms.u_radio_button_state.value += (radioButton.opacity - fgUniforms.u_radio_button_state.value) * perFrame(0.15)
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

    if (this.transitionProgress < 1) this.transitionProgress = Math.min(1, this.transitionProgress + delta / 0.7)

    bgUniforms.u_time.value = (bgUniforms.u_time.value + delta) % 1000.0
    bgUniforms.u_transition.value = this.transitionProgress
    if (reduceMotion) {
      bgUniforms.u_parallax.value.set(0, 0)
      bgUniforms.u_glitch.value.set(0, 0)
      bgUniforms.u_frame_offset.value.set(0, 0)
      bgUniforms.u_frame_scale.value = 1.0
      bgUniforms.u_scale.value = 1.0
      bgUniforms.u_rotation.value = 0.0
    } else {
      bgUniforms.u_parallax.value.set(pX, pY)
      bgUniforms.u_glitch.value.set(effects.glitchX, effects.glitchY)
      bgUniforms.u_frame_offset.value.set(effects.frameOffset.x, effects.frameOffset.y)
      bgUniforms.u_frame_scale.value = effects.frameScale
      bgUniforms.u_scale.value = effects.scale || 1.0
      bgUniforms.u_rotation.value = effects.rotation * Math.PI / 180
    }
    bgUniforms.u_brightness.value = effects.brightness
    bgUniforms.u_contrast.value = effects.contrast
    bgUniforms.u_saturation.value = effects.saturation
    bgUniforms.u_hue.value = effects.hue * Math.PI / 180
    bgUniforms.u_max_blur.value = visualQuality === 'high' ? effects.blur : 0
    bgUniforms.u_chromatic.value = visualQuality === 'high' && !reduceMotion ? effects.chromatic : 0
    bgUniforms.u_has_depth_map.value = 0.0
    bgUniforms.u_flicker.value = effects.flicker

    const clips = this.clips
    if (clips.count > 0 && visualQuality === 'high') {
      const currentEnergy = effects.currentEnergy || 0
      const targetBlend = isPlaying ? 0.15 + (currentEnergy * 0.55) : 0
      clips.blend += (targetBlend - clips.blend) * perFrame(targetBlend > clips.blend ? 0.15 : 0.08)
      const rate = 0.6 + (currentEnergy * 1.2)
      if (Math.abs(rate - clips.rate) > 0.01) {
        clips.rate = rate
        this.emit({ type: 'clipRate', rate })
      }
      const texture = clips.textures[clips.currentIndex]
      if (texture) {
        bgUniforms.u_video_clip.value = texture
        bgUniforms.u_video_clip_blend.value = clips.blend
      }
    } else {
      bgUniforms.u_video_clip_blend.value = 0
    }

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
    const captureLength = sigLength
    sigLength = writeUniformSignature(signature.current, sigLength, fgUniforms, FG_SIGNATURE_UNIFORMS)
    signature.current[sigLength++] = fgUniforms.u_radio_state_int.value === 4 ? fgUniforms.u_radio_time.value : 0

    const textures = signature.textures
    const textVersion = textTexture ? textTexture.version : -1
    const videoActive = bgUniforms.u_video_clip_blend.value > 0.01
    const wantsRender = signature.force ||
      videoActive ||
      hasVisiblePanels !== signature.fgVisible ||
      textures[0] !== bgUniforms.u_texture.value ||
      textures[1] !== bgUniforms.u_texture_prev.value ||
      textures[2] !== bgUniforms.u_text_texture.value ||
      textures[3] !== fgUniforms.u_noise_texture.value ||
      textVersion !== signature.textVersion ||
      sigLength !== signature.length ||
      signatureChanged(signature.current, signature.previous, sigLength)

    const captureDirty = signature.force || signature.captureStale || videoActive ||
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
      this.backdropMesh.visible = !hasVisiblePanels

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
      work = takeFrameWork(1)
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
