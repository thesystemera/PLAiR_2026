import { decodePack } from './packImage'
import {
  CanvasTexture,
  DataTexture,
  DoubleSide,
  LinearFilter,
  Mesh,
  OrthographicCamera,
  PlaneGeometry,
  RGBAFormat,
  Scene,
  ShaderMaterial,
  Vector2,
  VideoFrameTexture,
  WebGLRenderer,
} from 'three'
import { Muxer, ArrayBufferTarget } from 'mp4-muxer'
import { backgroundVertexShader, backgroundFragmentShader } from './sceneShaders'
import {
  createInitialEffects,
  buildVisualCueMap,
  calculateFrameEffects,
  processLyricTimestamps,
  getLyricAt,
  renderLyricToCanvas,
} from './sceneEffects'
import { drawLyricFrame, lyricCanvasSize, lyricFrameAt, prepareLyricStyle } from './lyricStyle'
import { loadLyricFonts } from './lyricFonts'

function seededRandom(seed) {
  let s = seed
  return () => { s = Math.sin(s * 9999) * 10000; return s - Math.floor(s) }
}

const VIDEO_QUEUE_FRAMES = 4
const AUDIO_QUEUE_CHUNKS = 4
const AUDIO_CHUNK_SECONDS = 1
const PROGRESS_INTERVAL_MS = 250
const ENCODER_WAIT_LIMIT_MS = 50

function encoderDequeued(encoder) {
  return new Promise(resolve => {
    const done = () => {
      clearTimeout(timer)
      encoder.removeEventListener('dequeue', done)
      resolve()
    }
    const timer = setTimeout(done, ENCODER_WAIT_LIMIT_MS)
    encoder.addEventListener('dequeue', done)
  })
}

async function waitForEncoderQueue(encoder, maxQueueSize, getError) {
  while (encoder.encodeQueueSize > maxQueueSize) {
    const error = getError()
    if (error) {
      throw error
    }
    await encoderDequeued(encoder)
  }
}

async function encodeAudio(audioEncoder, pcm, getError) {
  const { left, right, sampleRate } = pcm
  const samplesPerChunk = sampleRate * AUDIO_CHUNK_SECONDS

  for (let i = 0; i < left.length; i += samplesPerChunk) {
    const error = getError()
    if (error) {
      throw error
    }

    const chunkSize = Math.min(samplesPerChunk, left.length - i)
    const planarData = new Float32Array(chunkSize * 2)
    planarData.set(left.subarray(i, i + chunkSize), 0)
    planarData.set(right.subarray(i, i + chunkSize), chunkSize)

    const audioFrame = new AudioData({
      format: 'f32-planar',
      sampleRate,
      numberOfFrames: chunkSize,
      numberOfChannels: 2,
      timestamp: Math.round((i / sampleRate) * 1_000_000),
      data: planarData,
    })
    audioEncoder.encode(audioFrame)
    audioFrame.close()

    await waitForEncoderQueue(audioEncoder, AUDIO_QUEUE_CHUNKS, getError)
  }

  try {
    await audioEncoder.flush()
  } catch (err) {
    throw getError() || new Error(`Audio encoding failed: ${err.message || 'Encoding error'}`)
  }
}

export async function renderVideo({
  artworkBlob,
  pcm,
  audioFeatures,
  lyricTimestamps,
  clipDurations = [],
  requestClipFrame,
  durationMs,
  width = 720,
  height = 1280,
  fps = 30,
  onProgress,
  onStatus,
}) {
  onStatus?.('Initializing renderer...')

  const resources = {
    renderer: null,
    geometry: null,
    material: null,
    artworkTexture: null,
    lyricTexture: null,
    transparentPixel: null,
    videoEncoder: null,
    audioEncoder: null,
    clipTextures: [],
    clipFrame: null,
  }
  const spent = { effects: 0, clips: 0, lyrics: 0, draw: 0, encode: 0 }
  const sampleRate = pcm.sampleRate

  try {
    const canvas = new OffscreenCanvas(width, height)
    const gl = canvas.getContext('webgl2', {
      antialias: false,
      alpha: false,
      powerPreference: 'high-performance',
    })

    if (!gl) {
      throw new Error('WebGL2 not supported')
    }

    const renderer = new WebGLRenderer({
      canvas,
      context: gl,
      antialias: false,
      alpha: false,
      powerPreference: 'high-performance',
    })
    renderer.setSize(width, height, false)
    renderer.setPixelRatio(1)
    resources.renderer = renderer

  const scene = new Scene()
  const camera = new OrthographicCamera(-1, 1, 1, -1, 0, 1)

  const styledLyrics = prepareLyricStyle(lyricTimestamps)
  const lyricsStyled = !!styledLyrics && await loadLyricFonts(styledLyrics.uses, styledLyrics.text)
  const lyricSize = lyricsStyled ? lyricCanvasSize(width / height) : { width: 1024, height: 512 }
  const lyricCanvas = new OffscreenCanvas(lyricSize.width, lyricSize.height)
  const lyricCtx = lyricCanvas.getContext('2d')
  const lyricTexture = new CanvasTexture(lyricCanvas)
  lyricTexture.flipY = false
  resources.lyricTexture = lyricTexture

  onStatus?.('Loading artwork...')
  const artworkTexture = await loadTexture(artworkBlob)
  resources.artworkTexture = artworkTexture
  const texWidth = artworkTexture.image.width
  const texHeight = artworkTexture.image.height

  const transparentPixel = new DataTexture(
    new Uint8Array([0, 0, 0, 0]), 1, 1, RGBAFormat
  )
  resources.transparentPixel = transparentPixel

  const material = new ShaderMaterial({
    vertexShader: backgroundVertexShader,
    fragmentShader: backgroundFragmentShader,
    side: DoubleSide,
    uniforms: {
      u_texture: { value: artworkTexture },
      u_texture_prev: { value: artworkTexture },
      u_depth_map: { value: transparentPixel },
      u_text_texture: { value: lyricTexture },
      u_video_clip: { value: transparentPixel },
      u_video_clip_blend: { value: 0.0 },
      u_transition: { value: 1.0 },
      u_tex_resolution: { value: new Vector2(texWidth, texHeight) },
      u_canvas_resolution: { value: new Vector2(width, height) },
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
    }
  })

  const geometry = new PlaneGeometry(2, 2).scale(1, -1, 1)
  resources.geometry = geometry
  const mesh = new Mesh(geometry, material)
  scene.add(mesh)
  resources.material = material

  onStatus?.('Setting up encoder...')
  const totalFrames = Math.ceil((durationMs / 1000) * fps)

  const muxer = new Muxer({
    target: new ArrayBufferTarget(),
    video: {
      codec: 'avc',
      width,
      height,
    },
    audio: {
      codec: 'aac',
      numberOfChannels: 2,
      sampleRate,
    },
    firstTimestampBehavior: 'offset',
    fastStart: 'in-memory',
  })

  let videoEncoderError = null
  const videoEncoder = new VideoEncoder({
    output: (chunk, meta) => muxer.addVideoChunk(chunk, meta),
    error: (e) => {
      videoEncoderError = e
    },
  })
  resources.videoEncoder = videoEncoder

  videoEncoder.configure({
    codec: 'avc1.64001f',
    width,
    height,
    bitrate: 1_000_000,
    framerate: fps,
  })

  let audioEncoderError = null
  const audioEncoder = new AudioEncoder({
    output: (chunk, meta) => muxer.addAudioChunk(chunk, meta),
    error: (e) => {
      audioEncoderError = e
    },
  })
  resources.audioEncoder = audioEncoder

  audioEncoder.configure({
    codec: 'mp4a.40.2',
    numberOfChannels: 2,
    sampleRate,
    bitrate: 128000,
  })

  const audioError = () => (
    audioEncoderError
      ? new Error(`Audio encoding failed: ${audioEncoderError.message || audioEncoderError.name || 'Encoding error'}`)
      : null
  )
  const videoError = () => (
    videoEncoderError
      ? new Error(`Video encoding failed: ${videoEncoderError.message || videoEncoderError.name || 'Encoding error'}`)
      : null
  )

  let audioFailure = null
  const audioDone = encodeAudio(audioEncoder, pcm, audioError)
  audioDone.catch(err => { audioFailure = err })
  pcm = null

  const effects = createInitialEffects()

  const beats = audioFeatures?.beats || []
  const visualCueMap = buildVisualCueMap(beats)

  for (let index = 0; index < clipDurations.length; index++) {
    const clipTexture = new VideoFrameTexture()
    clipTexture.minFilter = LinearFilter
    clipTexture.magFilter = LinearFilter
    clipTexture.flipY = false
    resources.clipTextures.push(clipTexture)
  }

  let currentClipIndex = 0
  let videoClipBlend = 0
  const clipVirtualTime = new Array(clipDurations.length).fill(0)

  const trackingState = {
    lastSegmentIndex: 0,
    lastBeatIndex: 0,
    tempoTime: 0,
    energyHistory: [],
    energyStamps: [],
    energyScratch: [],
  }
  const rand = seededRandom(42)

  const lyricData = processLyricTimestamps(lyricTimestamps)
  let lyricIndex = 0
  let lastLyricText = null

  const delta = 1 / fps
  const frameDuration = Math.round((1 / fps) * 1_000_000)
  const renderStart = performance.now()
  let lastProgressAt = renderStart

  onStatus?.('Rendering frames...')

  for (let frame = 0; frame < totalFrames; frame++) {
    if (audioFailure) {
      throw audioFailure
    }

    const frameStart = performance.now()
    const timeMs = (frame / fps) * 1000
    const currentTime = timeMs / 1000

    const rawEnergy = calculateFrameEffects({
      effects,
      audioFeatures,
      currentTimeSeconds: currentTime,
      delta,
      visualCueMap,
      trackingState,
      random: rand,
      onCameraCut: () => {
        if (resources.clipTextures.length > 0) {
          currentClipIndex = (currentClipIndex + 1) % resources.clipTextures.length
        }
      },
    })

    effects.blur *= 0.9
    const effectsDone = performance.now()

    material.uniforms.u_time.value = currentTime
    material.uniforms.u_glitch.value.set(effects.glitchX, effects.glitchY)
    material.uniforms.u_frame_offset.value.set(effects.frameOffsetX, effects.frameOffsetY)
    material.uniforms.u_frame_scale.value = effects.frameScale
    material.uniforms.u_scale.value = effects.scale
    material.uniforms.u_rotation.value = (effects.rotation * Math.PI) / 180
    material.uniforms.u_brightness.value = effects.brightness
    material.uniforms.u_contrast.value = effects.contrast
    material.uniforms.u_saturation.value = effects.saturation
    material.uniforms.u_hue.value = (effects.hue * Math.PI) / 180
    material.uniforms.u_max_blur.value = effects.blur
    material.uniforms.u_chromatic.value = effects.chromatic
    material.uniforms.u_flicker.value = effects.flicker

    if (resources.clipTextures.length > 0) {
      const targetBlend = 0.15 + (rawEnergy * 0.55)
      const blendSpeed = targetBlend > videoClipBlend ? 0.15 : 0.08
      videoClipBlend += (targetBlend - videoClipBlend) * blendSpeed

      const playbackRate = 0.6 + (rawEnergy * 1.2)
      clipVirtualTime[currentClipIndex] += delta * playbackRate

      const clipDuration = clipDurations[currentClipIndex]
      if (clipDuration) {
        const clipFrame = await requestClipFrame(currentClipIndex, clipVirtualTime[currentClipIndex] % clipDuration)
        if (clipFrame) {
          resources.clipFrame?.close()
          resources.clipFrame = clipFrame
          resources.clipTextures[currentClipIndex].setFrame(clipFrame)
        }
      }

      material.uniforms.u_video_clip.value = resources.clipTextures[currentClipIndex]
      material.uniforms.u_video_clip_blend.value = resources.clipFrame ? videoClipBlend : 0
    }
    const clipsDone = performance.now()

    const lyricFrame = lyricsStyled ? lyricFrameAt(styledLyrics, timeMs / 1000) : null
    const currentLyric = lyricsStyled ? (lyricFrame && `${lyricFrame.index}:${lyricFrame.count}`) : getLyricAt(lyricData, timeMs, lyricIndex)
    if (currentLyric !== lastLyricText || frame === 0) {
      if (lyricsStyled) drawLyricFrame(lyricCtx, styledLyrics, lyricFrame, lyricSize.width, lyricSize.height, width / height)
      else renderLyricToCanvas(lyricCtx, currentLyric, lyricSize.width, lyricSize.height)

      lyricCtx.fillStyle = 'rgba(255,255,255,0.6)'
      lyricCtx.font = `600 ${Math.round(36 * lyricSize.height / 512)}px Inter, Arial, sans-serif`
      lyricCtx.textAlign = 'center'
      lyricCtx.textBaseline = 'middle'
      lyricCtx.fillText('PLAIR.live', lyricSize.width / 2, lyricSize.height * 480 / 512)

      lyricTexture.needsUpdate = true
      lastLyricText = currentLyric
    }
    const lyricsDone = performance.now()

    renderer.render(scene, camera)

    const videoFrame = new VideoFrame(canvas, {
      timestamp: Math.round((frame / fps) * 1_000_000),
      duration: frameDuration,
    })
    videoEncoder.encode(videoFrame, { keyFrame: frame % 30 === 0 })
    videoFrame.close()
    const drawDone = performance.now()

    await waitForEncoderQueue(videoEncoder, VIDEO_QUEUE_FRAMES, videoError)

    const now = performance.now()
    spent.effects += effectsDone - frameStart
    spent.clips += clipsDone - effectsDone
    spent.lyrics += lyricsDone - clipsDone
    spent.draw += drawDone - lyricsDone
    spent.encode += now - drawDone
    if (now - lastProgressAt >= PROGRESS_INTERVAL_MS) {
      lastProgressAt = now
      onProgress?.(frame / totalFrames)
      await new Promise(r => setTimeout(r, 0))
    }
  }

  onStatus?.('Finishing video encoding...')
  try {
    await videoEncoder.flush()
  } catch (err) {
    throw videoError() || new Error(`Video encoding failed: ${err.message || 'Encoding error'}`)
  }

  onStatus?.('Finishing audio encoding...')
  await audioDone

  const renderSeconds = (performance.now() - renderStart) / 1000
  const stats = {
    frames: totalFrames,
    seconds: +renderSeconds.toFixed(1),
    speed: +(durationMs / 1000 / renderSeconds).toFixed(2),
    msPerFrame: Object.fromEntries(Object.entries(spent).map(([stage, ms]) => [stage, +(ms / totalFrames).toFixed(2)])),
  }

  onStatus?.('Building MP4 file...')
  muxer.finalize()

  onProgress?.(1)

  return { buffer: muxer.target.buffer, stats }

  } finally {
    try {
      if (resources.videoEncoder) {
        resources.videoEncoder.close()
      }
    } catch { /* ignore */ }

    try {
      if (resources.audioEncoder) {
        resources.audioEncoder.close()
      }
    } catch { /* ignore */ }

    try {
      if (resources.renderer) {
        resources.renderer.dispose()
        resources.renderer.forceContextLoss()
      }
    } catch { /* ignore */ }

    try {
      if (resources.geometry) resources.geometry.dispose()
    } catch { /* ignore */ }

    try {
      if (resources.material) resources.material.dispose()
    } catch { /* ignore */ }

    try {
      if (resources.artworkTexture) resources.artworkTexture.dispose()
    } catch { /* ignore */ }

    try {
      if (resources.lyricTexture) resources.lyricTexture.dispose()
    } catch { /* ignore */ }

    try {
      if (resources.transparentPixel) resources.transparentPixel.dispose()
    } catch { /* ignore */ }

    for (const tex of resources.clipTextures) {
      try { tex.dispose() } catch { /* ignore */ }
    }
    try {
      resources.clipFrame?.close()
    } catch { /* ignore */ }
  }
}

async function loadTexture(packBlob) {
  const { color } = await decodePack(packBlob, { map: false })
  const texture = new CanvasTexture(color)
  texture.image = color
  texture.flipY = false
  return texture
}
