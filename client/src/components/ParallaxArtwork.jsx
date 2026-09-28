import { memo, useEffect, useRef, useState, useCallback } from 'react'
import { useUIState, useEnrichedArtwork } from '../contexts/UIStateContext'
import { useViewport } from '../contexts/ViewportContext'
import { useQuality } from '../contexts/QualityContext'
import { isSceneRenderingPaused } from '../lib/renderPause'
import { logger } from '../lib/logger'
import { CSS_TRANSITION } from '../lib/motion'

// ═══════════════════════════════════════════════════════════════════════════════
// PARALLAX TUNING - All tweakable values for the POM + trail suppression shader
// ═══════════════════════════════════════════════════════════════════════════════
//
// The shader has 3 stages:
//   1. POM ray march     → proper foreground-over-background occlusion
//   2. Trail detection   → identifies disoccluded streaks behind shifted foreground
//   3. Background fill   → replaces detected trails with nearby background color
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
//   Effect too subtle?     → Raise intensity prop in NowPlaying.jsx (currently 0.08)
// ═══════════════════════════════════════════════════════════════════════════════
const POM = {

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

// GLSL float formatter - ensures values like 1 become "1.0" for valid GLSL
const G = v => String(v).includes('.') ? String(v) : String(v) + '.0'

const vertexShader = `
  attribute vec2 a_position;
  attribute vec2 a_texCoord;
  varying vec2 v_texCoord;

  void main() {
    gl_Position = vec4(a_position, 0.0, 1.0);
    v_texCoord = a_texCoord;
  }
`

const fragmentShader = `
  precision highp float;

  uniform sampler2D u_color;
  uniform sampler2D u_depth;
  uniform vec2 u_gyro;
  uniform float u_intensity;
  uniform float u_zoom;
  uniform float u_steps;

  varying vec2 v_texCoord;

  void main() {
    vec2 displacement = u_gyro * u_intensity;
    float dispLen = length(displacement);

    float autoZoom = 1.0 + abs(u_intensity) * ${G(POM.ZOOM_FACTOR)};
    float finalZoom = u_zoom * autoZoom;
    vec2 uv = (v_texCoord - 0.5) / finalZoom + 0.5;

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

    vec4 pomColor = texture2D(u_color, clamp(testUV, 0.001, 0.999));

    float dispGate = smoothstep(${G(POM.DISP_GATE_MIN)}, ${G(POM.DISP_GATE_MAX)}, dispLen);
    if (dispGate <= 0.0) {
      gl_FragColor = pomColor;
      return;
    }

    float baseDepth = texture2D(u_depth, clamp(uv, 0.0, 1.0)).r;
    float foregroundness = smoothstep(${G(POM.FG_DEPTH_SOFT)}, ${G(POM.FG_DEPTH_HARD)}, baseDepth);
    if (foregroundness <= 0.0) {
      gl_FragColor = pomColor;
      return;
    }

    float texel = ${G(POM.EDGE_RADIUS)};
    float dL = texture2D(u_depth, clamp(testUV - vec2(texel, 0.0), 0.0, 1.0)).r;
    float dR = texture2D(u_depth, clamp(testUV + vec2(texel, 0.0), 0.0, 1.0)).r;
    float dU = texture2D(u_depth, clamp(testUV - vec2(0.0, texel), 0.0, 1.0)).r;
    float dD = texture2D(u_depth, clamp(testUV + vec2(0.0, texel), 0.0, 1.0)).r;
    float gradient = abs(dR - dL) + abs(dD - dU);

    float edgeness = smoothstep(${G(POM.EDGE_SOFT)}, ${G(POM.EDGE_HARD)}, gradient);
    float trailMask = edgeness * foregroundness * dispGate;
    if (trailMask <= 0.0) {
      gl_FragColor = pomColor;
      return;
    }

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

    gl_FragColor = mix(pomColor, fillColor, trailMask * ${G(POM.FILL_STRENGTH)});
  }
`

const POSITIONS = new Float32Array([
  -1, -1,  1, -1,  -1, 1,
  -1, 1,   1, -1,   1, 1
])

const PARALLAX_EPSILON = 1e-4
const REDRAW_SHIFT_PX = 0.1
const MIN_LINEAR_STEPS = 6
const FRAME_CAP_SLACK_MS = 4
const MIPMAP_BELOW_RATIO = 0.75

const isPowerOfTwo = value => value > 0 && (value & (value - 1)) === 0

const TEX_COORDS = new Float32Array([
  0, 1,  1, 1,  0, 0,
  0, 0,  1, 1,  1, 0
])

export const ParallaxArtwork = memo(function ParallaxArtwork({
  trackId,
  artworkUrl,
  alt = 'Album artwork',
  className = '',
  intensity = 0.15,
  zoom = 1.0,
  onLoad,
  onError,
  isActive = true
}) {
  const enrichedArtworkUrl = useEnrichedArtwork(trackId)

  const canvasRef = useRef(null)
  const glRef = useRef(null)
  const programRef = useRef(null)
  const colorTextureRef = useRef(null)
  const depthTextureRef = useRef(null)
  const animationFrameRef = useRef(null)
  const uniformsRef = useRef(null)
  const lastDrawRef = useRef(null)
  const colorTextureInfoRef = useRef(null)

  const [glReady, setGlReady] = useState(false)
  const [texturesReady, setTexturesReady] = useState(false)
  const [fallbackMode, setFallbackMode] = useState(false)
  const [contextLost, setContextLost] = useState(false)
  const [isVisible, setIsVisible] = useState(true)

  const { gyroscopeRef, mouseRef } = useUIState()
  const { isMobile } = useViewport()
  const { parallaxDpr, parallaxFpsCap, parallaxStepPx, reduceMotion, isTopTier } = useQuality()

  useEffect(() => {
    window.registerRAFSource?.('ParallaxArtwork')
  }, [])

  const handleContextLost = useCallback((event) => {
    event.preventDefault()
    logger.warn('[ParallaxArtwork] WebGL context lost')
    setContextLost(true)
    setGlReady(false)
    setTexturesReady(false)

    if (animationFrameRef.current) {
      cancelAnimationFrame(animationFrameRef.current)
      animationFrameRef.current = null
    }
  }, [])

  const handleContextRestored = useCallback(() => {
    logger.info('[ParallaxArtwork] WebGL context restored, attempting recovery')
    setContextLost(false)

    const canvas = canvasRef.current
    if (!canvas) return

    const gl = canvas.getContext('webgl', {
      alpha: false,
      antialias: false,
      depth: false,
      preserveDrawingBuffer: false
    })

    if (!gl) {
      logger.error('[ParallaxArtwork] Failed to restore WebGL context')
      setFallbackMode(true)
      return
    }

    glRef.current = gl

    try {
      const vertShader = gl.createShader(gl.VERTEX_SHADER)
      gl.shaderSource(vertShader, vertexShader)
      gl.compileShader(vertShader)

      if (!gl.getShaderParameter(vertShader, gl.COMPILE_STATUS)) {
        logger.error('[ParallaxArtwork] Vertex shader compilation failed on restore')
        setFallbackMode(true)
        return
      }

      const fragShader = gl.createShader(gl.FRAGMENT_SHADER)
      gl.shaderSource(fragShader, fragmentShader)
      gl.compileShader(fragShader)

      if (!gl.getShaderParameter(fragShader, gl.COMPILE_STATUS)) {
        logger.error('[ParallaxArtwork] Fragment shader compilation failed on restore')
        setFallbackMode(true)
        return
      }

      const program = gl.createProgram()
      gl.attachShader(program, vertShader)
      gl.attachShader(program, fragShader)
      gl.linkProgram(program)

      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        logger.error('[ParallaxArtwork] Program linking failed on restore')
        setFallbackMode(true)
        return
      }

      programRef.current = program
      gl.useProgram(program)

      const posBuffer = gl.createBuffer()
      gl.bindBuffer(gl.ARRAY_BUFFER, posBuffer)
      gl.bufferData(gl.ARRAY_BUFFER, POSITIONS, gl.STATIC_DRAW)

      const posLoc = gl.getAttribLocation(program, 'a_position')
      gl.enableVertexAttribArray(posLoc)
      gl.vertexAttribPointer(posLoc, 2, gl.FLOAT, false, 0, 0)

      const texBuffer = gl.createBuffer()
      gl.bindBuffer(gl.ARRAY_BUFFER, texBuffer)
      gl.bufferData(gl.ARRAY_BUFFER, TEX_COORDS, gl.STATIC_DRAW)

      const texLoc = gl.getAttribLocation(program, 'a_texCoord')
      gl.enableVertexAttribArray(texLoc)
      gl.vertexAttribPointer(texLoc, 2, gl.FLOAT, false, 0, 0)

      uniformsRef.current = {
        color: gl.getUniformLocation(program, 'u_color'),
        depth: gl.getUniformLocation(program, 'u_depth'),
        gyro: gl.getUniformLocation(program, 'u_gyro'),
        intensity: gl.getUniformLocation(program, 'u_intensity'),
        zoom: gl.getUniformLocation(program, 'u_zoom'),
        steps: gl.getUniformLocation(program, 'u_steps')
      }

      gl.uniform1i(uniformsRef.current.color, 0)
      gl.uniform1i(uniformsRef.current.depth, 1)

      setGlReady(true)
      logger.info('[ParallaxArtwork] WebGL context successfully restored')
    } catch (error) {
      logger.error('[ParallaxArtwork] Error during context restore:', error)
      setFallbackMode(true)
    }
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const gl = canvas.getContext('webgl', {
      alpha: false,
      antialias: false,
      depth: false,
      preserveDrawingBuffer: false
    })

    if (!gl) {
      logger.warn('[ParallaxArtwork] WebGL not available, using fallback')
      setFallbackMode(true)
      return
    }

    glRef.current = gl

    try {
      const vertShader = gl.createShader(gl.VERTEX_SHADER)
      gl.shaderSource(vertShader, vertexShader)
      gl.compileShader(vertShader)

      if (!gl.getShaderParameter(vertShader, gl.COMPILE_STATUS)) {
        logger.error('[ParallaxArtwork] Vertex shader compilation failed')
        setFallbackMode(true)
        return
      }

      const fragShader = gl.createShader(gl.FRAGMENT_SHADER)
      gl.shaderSource(fragShader, fragmentShader)
      gl.compileShader(fragShader)

      if (!gl.getShaderParameter(fragShader, gl.COMPILE_STATUS)) {
        logger.error('[ParallaxArtwork] Fragment shader compilation failed')
        setFallbackMode(true)
        return
      }

      const program = gl.createProgram()
      gl.attachShader(program, vertShader)
      gl.attachShader(program, fragShader)
      gl.linkProgram(program)

      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        logger.error('[ParallaxArtwork] Program linking failed')
        setFallbackMode(true)
        return
      }

      programRef.current = program
      gl.useProgram(program)

      const posBuffer = gl.createBuffer()
      gl.bindBuffer(gl.ARRAY_BUFFER, posBuffer)
      gl.bufferData(gl.ARRAY_BUFFER, POSITIONS, gl.STATIC_DRAW)

      const posLoc = gl.getAttribLocation(program, 'a_position')
      gl.enableVertexAttribArray(posLoc)
      gl.vertexAttribPointer(posLoc, 2, gl.FLOAT, false, 0, 0)

      const texBuffer = gl.createBuffer()
      gl.bindBuffer(gl.ARRAY_BUFFER, texBuffer)
      gl.bufferData(gl.ARRAY_BUFFER, TEX_COORDS, gl.STATIC_DRAW)

      const texLoc = gl.getAttribLocation(program, 'a_texCoord')
      gl.enableVertexAttribArray(texLoc)
      gl.vertexAttribPointer(texLoc, 2, gl.FLOAT, false, 0, 0)

      uniformsRef.current = {
        color: gl.getUniformLocation(program, 'u_color'),
        depth: gl.getUniformLocation(program, 'u_depth'),
        gyro: gl.getUniformLocation(program, 'u_gyro'),
        intensity: gl.getUniformLocation(program, 'u_intensity'),
        zoom: gl.getUniformLocation(program, 'u_zoom'),
        steps: gl.getUniformLocation(program, 'u_steps')
      }

      gl.uniform1i(uniformsRef.current.color, 0)
      gl.uniform1i(uniformsRef.current.depth, 1)

      canvas.addEventListener('webglcontextlost', handleContextLost)
      canvas.addEventListener('webglcontextrestored', handleContextRestored)

      setGlReady(true)
      logger.debug('[ParallaxArtwork] WebGL initialized successfully')
    } catch (error) {
      logger.error('[ParallaxArtwork] WebGL initialization error:', error)
      setFallbackMode(true)
      return
    }

    return () => {
      canvas.removeEventListener('webglcontextlost', handleContextLost)
      canvas.removeEventListener('webglcontextrestored', handleContextRestored)

      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current)
      }

      if (!gl.isContextLost()) {
        if (programRef.current) gl.deleteProgram(programRef.current)
        if (colorTextureRef.current) gl.deleteTexture(colorTextureRef.current)
        if (depthTextureRef.current) gl.deleteTexture(depthTextureRef.current)
        gl.getExtension('WEBGL_lose_context')?.loseContext()
      }
      programRef.current = null
      colorTextureRef.current = null
      depthTextureRef.current = null
      glRef.current = null
    }
  }, [handleContextLost, handleContextRestored])

  useEffect(() => {
    if (!isMobile) {
      setIsVisible(true)
      return
    }

    const canvas = canvasRef.current
    if (!canvas) return

    const observer = new IntersectionObserver(
      ([entry]) => {
        setIsVisible(entry.isIntersecting)
        if (entry.isIntersecting) {
          logger.debug('[ParallaxArtwork] Canvas visible, resuming render')
        } else {
          logger.debug('[ParallaxArtwork] Canvas hidden, pausing render')
        }
      },
      { threshold: 0.01 }
    )

    observer.observe(canvas)
    return () => observer.disconnect()
  }, [isMobile])

  useEffect(() => {
    if (!glReady || !trackId || fallbackMode || contextLost) return

    const gl = glRef.current
    if (!gl || gl.isContextLost()) {
      logger.warn('[ParallaxArtwork] Cannot load textures - context lost')
      return
    }

    setTexturesReady(false)

    let cancelled = false
    const image = new Image()
    image.crossOrigin = 'anonymous'

    image.decoding = 'async'
    image.onload = async () => {
      try {
        await image.decode?.()
      } catch {
        logger.debug('[ParallaxArtwork] Async decode unavailable, drawing directly')
      }
      if (cancelled) {
        logger.debug('[ParallaxArtwork] Image load cancelled (track changed)')
        return
      }

      if (!gl || gl.isContextLost()) {
        logger.warn('[ParallaxArtwork] Context lost before texture creation')
        setFallbackMode(true)
        return
      }

      try {
        const halfWidth = Math.floor(image.width / 2)
        const height = image.height

        const splitCanvas = document.createElement('canvas')
        splitCanvas.width = halfWidth
        splitCanvas.height = height
        const ctx = splitCanvas.getContext('2d')

        ctx.drawImage(image, 0, 0, halfWidth, height, 0, 0, halfWidth, height)

        const newColorTexture = gl.createTexture()
        gl.activeTexture(gl.TEXTURE0)
        gl.bindTexture(gl.TEXTURE_2D, newColorTexture)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, splitCanvas)
        const canMipmap = isPowerOfTwo(halfWidth) && isPowerOfTwo(height)
        if (canMipmap) gl.generateMipmap(gl.TEXTURE_2D)
        colorTextureInfoRef.current = { width: halfWidth, canMipmap, filter: gl.LINEAR }

        ctx.drawImage(image, halfWidth, 0, halfWidth, height, 0, 0, halfWidth, height)

        const newDepthTexture = gl.createTexture()
        gl.activeTexture(gl.TEXTURE1)
        gl.bindTexture(gl.TEXTURE_2D, newDepthTexture)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, splitCanvas)

        if (colorTextureRef.current && !gl.isContextLost()) {
          gl.deleteTexture(colorTextureRef.current)
        }
        if (depthTextureRef.current && !gl.isContextLost()) {
          gl.deleteTexture(depthTextureRef.current)
        }

        colorTextureRef.current = newColorTexture
        depthTextureRef.current = newDepthTexture

        setTexturesReady(true)
        if (onLoad) onLoad()
      } catch (error) {
        logger.error('[ParallaxArtwork] Error creating textures:', error)
        setFallbackMode(true)
        if (onError) onError()
      }
    }

    image.onerror = () => {
      if (cancelled) return
      logger.error('[ParallaxArtwork] Failed to load artwork image')
      setFallbackMode(true)
      if (onError) onError()
    }

    const enrichedIsBlob = enrichedArtworkUrl?.startsWith('blob:')
    const enrichedIsPlaceholder = enrichedArtworkUrl?.startsWith('data:image/svg')
    const regularIsBlob = artworkUrl?.startsWith('blob:')

    if (enrichedIsBlob) {
      image.src = enrichedArtworkUrl
    } else if (enrichedIsPlaceholder) {
      image.src = enrichedArtworkUrl
    } else if (regularIsBlob) {
      image.src = artworkUrl
    } else if (artworkUrl) {
      image.src = artworkUrl
    } else {
      logger.warn('[ParallaxArtwork] No artwork source available')
      setFallbackMode(true)
    }

    return () => {
      cancelled = true
    }
  }, [glReady, trackId, enrichedArtworkUrl, artworkUrl, fallbackMode, contextLost, onLoad, onError])

  useEffect(() => {
    if (!glReady || !texturesReady || fallbackMode || contextLost || !isVisible || !isActive) return

    const gl = glRef.current
    const canvas = canvasRef.current
    const uniforms = uniformsRef.current

    if (!gl || !canvas || !uniforms) {
      logger.warn('[ParallaxArtwork] Missing required refs for render loop')
      return
    }

    lastDrawRef.current = null
    let errorCheckPending = true
    const minFrameMs = parallaxFpsCap > 0 ? 1000 / parallaxFpsCap - FRAME_CAP_SLACK_MS : 0

    const render = (timestamp) => {
      try {
        if (!gl || !canvas || !uniforms) {
          return
        }

        if (gl.isContextLost()) {
          logger.warn('[ParallaxArtwork] Context lost during render')
          return
        }

        if (!colorTextureRef.current || !depthTextureRef.current) {
          window.__rafDebug?.sources && (window.__rafDebug.sources['ParallaxArtwork'] = (window.__rafDebug.sources['ParallaxArtwork'] || 0) + 1)
          animationFrameRef.current = requestAnimationFrame(render)
          return
        }

        const gyro = gyroscopeRef?.current || { parallaxX: 0, parallaxY: 0 }
        const mouse = mouseRef?.current || { parallaxX: 0, parallaxY: 0 }

        const hasGyro = Math.abs(gyro.parallaxX) > 0.001 || Math.abs(gyro.parallaxY) > 0.001
        const parallaxX = reduceMotion ? 0 : hasGyro ? gyro.parallaxX : mouse.parallaxX
        const parallaxY = reduceMotion ? 0 : hasGyro ? gyro.parallaxY : mouse.parallaxY

        const effectiveZoom = zoom * (1 + Math.abs(intensity) * POM.ZOOM_FACTOR)
        const pixelsPerUnit = Math.abs(intensity) * Math.max(canvas.width, canvas.height) * effectiveZoom
        const epsilon = Math.max(PARALLAX_EPSILON, pixelsPerUnit > 0 ? REDRAW_SHIFT_PX / (0.5 * pixelsPerUnit) : PARALLAX_EPSILON)

        const last = lastDrawRef.current
        const unchanged = last &&
          last.color === colorTextureRef.current &&
          last.width === canvas.width &&
          last.height === canvas.height &&
          Math.abs(last.x - parallaxX) < epsilon &&
          Math.abs(last.y - parallaxY) < epsilon

        const paused = last && timestamp !== undefined && isSceneRenderingPaused(timestamp)
        const throttled = paused || (last && minFrameMs > 0 && timestamp !== undefined && timestamp - last.time < minFrameMs)

        if (!unchanged && !throttled) {
          const travelPx = Math.hypot(parallaxX, parallaxY) * pixelsPerUnit
          const steps = parallaxStepPx > 0
            ? Math.min(POM.LINEAR_STEPS, Math.max(MIN_LINEAR_STEPS, Math.ceil(travelPx / parallaxStepPx)))
            : POM.LINEAR_STEPS

          gl.viewport(0, 0, canvas.width, canvas.height)

          gl.activeTexture(gl.TEXTURE0)
          gl.bindTexture(gl.TEXTURE_2D, colorTextureRef.current)
          const colorInfo = colorTextureInfoRef.current
          if (colorInfo) {
            const minify = !isTopTier && colorInfo.canMipmap && canvas.width < colorInfo.width * MIPMAP_BELOW_RATIO
            const filter = minify ? gl.LINEAR_MIPMAP_LINEAR : gl.LINEAR
            if (colorInfo.filter !== filter) {
              gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter)
              colorInfo.filter = filter
            }
          }
          gl.activeTexture(gl.TEXTURE1)
          gl.bindTexture(gl.TEXTURE_2D, depthTextureRef.current)

          gl.uniform2f(uniforms.gyro, parallaxX, parallaxY)
          gl.uniform1f(uniforms.intensity, intensity)
          gl.uniform1f(uniforms.zoom, zoom)
          gl.uniform1f(uniforms.steps, steps)

          gl.clearColor(0, 0, 0, 1)
          gl.clear(gl.COLOR_BUFFER_BIT)
          gl.drawArrays(gl.TRIANGLES, 0, 6)

          if (errorCheckPending) {
            errorCheckPending = false
            const error = gl.getError()
            if (error !== gl.NO_ERROR) {
              logger.error(`[ParallaxArtwork] WebGL error during render: ${error}`)
              setFallbackMode(true)
              return
            }
          }

          lastDrawRef.current = {
            color: colorTextureRef.current,
            width: canvas.width,
            height: canvas.height,
            x: parallaxX,
            y: parallaxY,
            time: timestamp ?? performance.now(),
          }
        }

        window.__rafDebug?.sources && (window.__rafDebug.sources['ParallaxArtwork'] = (window.__rafDebug.sources['ParallaxArtwork'] || 0) + 1)
        animationFrameRef.current = requestAnimationFrame(render)
      } catch (error) {
        logger.error('[ParallaxArtwork] Render loop error:', error)
        setFallbackMode(true)
      }
    }

    render()

    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current)
      }
    }
  }, [glReady, texturesReady, intensity, zoom, fallbackMode, contextLost, gyroscopeRef, mouseRef, isVisible, isActive, parallaxFpsCap, parallaxStepPx, reduceMotion, isTopTier])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const resizeObserver = new ResizeObserver(entries => {
      for (const entry of entries) {
        const deviceDpr = window.devicePixelRatio || 1
        const dpr = Math.min(deviceDpr, parallaxDpr)

        let width, height
        if (entry.devicePixelContentBoxSize && dpr === deviceDpr) {
          width = entry.devicePixelContentBoxSize[0].inlineSize
          height = entry.devicePixelContentBoxSize[0].blockSize
        } else {
          width = Math.round(entry.contentRect.width * dpr)
          height = Math.round(entry.contentRect.height * dpr)
        }

        if (canvas.width !== width || canvas.height !== height) {
          canvas.width = width
          canvas.height = height
        }
      }
    })

    resizeObserver.observe(canvas)
    return () => resizeObserver.disconnect()
  }, [parallaxDpr])

  const useStandardArtwork = fallbackMode || contextLost || !isVisible || !isActive || !texturesReady

  if (fallbackMode || contextLost) {
    return (
      <img
        src={artworkUrl}
        alt={alt}
        decoding="async"
        className={className}
        onLoad={onLoad}
        onError={onError}
        style={{ width: '100%', height: '100%', objectFit: 'cover' }}
      />
    )
  }

  return (
    <div className={className} style={{ position: 'relative', width: '100%', height: '100%' }}>
      <img
        src={artworkUrl}
        alt={alt}
        decoding="async"
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          objectFit: 'cover'
        }}
        onLoad={!texturesReady ? onLoad : undefined}
      />
      <canvas
        ref={canvasRef}
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          opacity: useStandardArtwork ? 0 : 1,
          transition: CSS_TRANSITION.fadeOpacity
        }}
      />
    </div>
  )
})