import { decodePack } from './packImage'
import {
  CanvasTexture,
  DataTexture,
  DoubleSide,
  LinearFilter,
  LinearMipmapLinearFilter,
  Matrix3,
  MirroredRepeatWrapping,
  Mesh,
  OrthographicCamera,
  PlaneGeometry,
  RGBAFormat,
  Scene,
  ShaderMaterial,
  Vector2,
  Vector3,
  VideoFrameTexture,
  WebGLRenderer,
} from 'three'
import { Muxer, StreamTarget } from 'mp4-muxer'
import { backgroundVertexShader, backgroundFragmentShader, lookShaders } from './sceneShaders'
import { FOOTAGE_IDS, LOOK_IDS, createLookContext, directLook, lookById, lookUniforms, readDepth, setLookUniforms } from './scene/looks'
import { createDirectorCursor, directorAt, planShots } from './scene/director'
import { analysisPixels, applyFocus, createFocus, detailGrid, interestPoints } from './scene/focus'
import { createFx, setCamera, stepFx } from './scene/beatFx'
import { setGrade } from './scene/grade'
import { createSoftener } from './scene/softPyramid'
import { MAX_SLOTS, createFootageState, planFootage, setFootageUniforms, stepFootage } from './scene/footage'
import { coverPalette } from './scene/palette'
import {
  processLyricTimestamps,
  getLyricAt,
  renderLyricToCanvas,
} from './sceneEffects'
import { drawLyricFrame, lyricCanvasSize, lyricFrameAt, prepareLyricStyle } from './lyricStyle'
import { loadLyricFonts } from './lyricFonts'
import { NV12_COLOR_SPACE, createFrameReadback, frameReadbackSupported } from './videoFrameReadback'
import { openClip } from './clipReader'

const VIDEO_QUEUE_FRAMES = 8
const KEY_FRAME_SECONDS = 2
const AUDIO_QUEUE_CHUNKS = 4
const AUDIO_CHUNK_SECONDS = 1
const AUDIO_FRAME_SAMPLES = 1024
const AUDIO_CHUNK_MARGIN = 16
const PROGRESS_INTERVAL_MS = 250
const ENCODER_WAIT_LIMIT_MS = 50
const CLIP_FRAME_S = 1 / 30
const CLIP_IDLE_SECONDS = 2

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

function readFrame(gl, width, height) {
  const pixels = new Uint8Array(width * height * 4)
  gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels)
  return pixels
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

async function audioConfigFor(sampleRate, bitrate) {
  for (const candidate of [bitrate, 192_000, 128_000]) {
    const config = { codec: 'mp4a.40.2', numberOfChannels: 2, sampleRate, bitrate: candidate }
    const { supported } = await AudioEncoder.isConfigSupported(config).catch(() => ({ supported: false }))
    if (supported) return config
  }
  return { codec: 'mp4a.40.2', numberOfChannels: 2, sampleRate, bitrate: 128_000 }
}

async function openClips(urls) {
  const opened = await Promise.all(urls.map(url => openClip(url).catch(() => null)))
  return opened.filter(Boolean)
}

function writePiece(pieces, position, data) {
  const blob = new Blob([data])
  const end = position + blob.size
  const kept = []
  for (const piece of pieces) {
    const pieceEnd = piece.position + piece.blob.size
    if (pieceEnd <= position || piece.position >= end) {
      kept.push(piece)
      continue
    }
    if (piece.position < position) kept.push({ position: piece.position, blob: piece.blob.slice(0, position - piece.position) })
    if (pieceEnd > end) kept.push({ position: end, blob: piece.blob.slice(end - piece.position) })
  }
  kept.push({ position, blob })
  kept.sort((a, b) => a.position - b.position)
  return kept
}

function fileFromPieces(pieces) {
  const parts = []
  let end = 0
  for (const piece of pieces) {
    if (piece.position > end) parts.push(new Uint8Array(piece.position - end))
    parts.push(piece.blob)
    end = piece.position + piece.blob.size
  }
  return new Blob(parts, { type: 'video/mp4' })
}

export async function renderVideo({
  artworkBlob,
  pcm,
  audioFeatures,
  lyricTimestamps,
  clipUrls = [],
  durationMs,
  width,
  height,
  fps,
  codec,
  videoBitrate,
  audioBitrate,
  bench = {},
  onProgress,
  onStatus,
}) {
  onStatus?.('Initializing renderer...')

  const resources = {
    renderer: null,
    geometry: null,
    material: null,
    lookMaterials: [],
    artworkTexture: null,
    depthTexture: null,
    soft: null,
    lyricTexture: null,
    transparentPixel: null,
    videoEncoder: null,
    audioEncoder: null,
    clipTextures: [],
    clipReaders: [],
    readback: null,
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
  const { texture: artworkTexture, depth, palette } = await loadTextures(artworkBlob)
  resources.artworkTexture = artworkTexture
  resources.depthTexture = depth?.texture || null
  const softener = createSoftener(renderer)
  const soft = softener.soften(artworkTexture)
  softener.dispose()
  resources.soft = soft
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
      u_texture_near: { value: soft.near.texture },
      u_texture_far: { value: soft.far.texture },
      u_texture_prev_far: { value: soft.far.texture },
      u_depth_map: { value: depth?.texture || transparentPixel },
      u_orbit: { value: new Vector3(0, 0, 0) },
      u_text_texture: { value: lyricTexture },
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
    }
  })

  if (bench.flatShader) {
    material.fragmentShader = backgroundFragmentShader.slice(0, backgroundFragmentShader.lastIndexOf('void main()')) + 'void main() { gl_FragColor = vec4(vUv, 0.5, 1.0); }'
  }

  const geometry = new PlaneGeometry(2, 2).scale(1, -1, 1)
  resources.geometry = geometry
  const mesh = new Mesh(geometry, material)
  scene.add(mesh)
  resources.material = material
  const materials = { cover: material }
  if (clipUrls.length) onStatus?.('Loading video clips...')
  const readers = clipUrls.length ? await openClips(clipUrls) : []
  resources.clipReaders = readers
  const clips = readers.map(reader => ({ duration: reader.duration }))
  for (const id of clips.length ? [...LOOK_IDS, ...FOOTAGE_IDS] : LOOK_IDS) {
    if (id === 'cover') continue
    const shaders = lookShaders(id)
    materials[id] = new ShaderMaterial({ vertexShader: shaders.vertex, fragmentShader: shaders.background, side: DoubleSide, uniforms: material.uniforms })
    resources.lookMaterials.push(materials[id])
  }
  const basePlan = bench.flatShader ? null : planShots(audioFeatures, { sections: lyricTimestamps?.timed_sections, lines: lyricTimestamps?.lyrics })
  const plan = clips.length ? planFootage(basePlan, clips) : basePlan
  const directorCursor = createDirectorCursor()
  const focus = createFocus()
  const lookContext = Object.assign(createLookContext(focus.frame, false), { aspect: width / height, bands: depth?.bands || null, grid: depth?.grid || null })

  onStatus?.('Setting up encoder...')
  const totalFrames = Math.ceil((durationMs / 1000) * fps)

  let pieces = []
  const muxer = new Muxer({
    target: new StreamTarget({
      onData: (data, position) => { pieces = writePiece(pieces, position, data) },
      chunked: true,
    }),
    video: {
      codec: 'avc',
      width,
      height,
      frameRate: fps,
    },
    audio: {
      codec: 'aac',
      numberOfChannels: 2,
      sampleRate,
    },
    firstTimestampBehavior: 'offset',
    fastStart: {
      expectedVideoChunks: totalFrames,
      expectedAudioChunks: Math.ceil(pcm.left.length / AUDIO_FRAME_SAMPLES) + AUDIO_CHUNK_MARGIN,
    },
  })

  let videoEncoderError = null
  const videoEncoder = new VideoEncoder({
    output: (chunk, meta) => muxer.addVideoChunk(chunk, meta),
    error: (e) => {
      videoEncoderError = e
    },
  })
  resources.videoEncoder = videoEncoder

  const encoderConfig = { codec, width, height, bitrate: videoBitrate, framerate: fps, bitrateMode: 'constant', ...bench.encoder }
  const constantBitrate = await VideoEncoder.isConfigSupported(encoderConfig).then(result => result.supported, () => false)
  if (!constantBitrate) delete encoderConfig.bitrateMode
  const supported = constantBitrate || await VideoEncoder.isConfigSupported(encoderConfig).then(result => result.supported, () => false)
  if (!supported) throw new Error(`This device can't encode ${height > width ? width : height}p video at ${fps} fps`)
  videoEncoder.configure(encoderConfig)
  const keyFrameEvery = bench.keyFrameEvery || Math.round(fps * KEY_FRAME_SECONDS)
  const syncPixel = new Uint8Array(4)

  let audioEncoderError = null
  const audioEncoder = new AudioEncoder({
    output: (chunk, meta) => muxer.addAudioChunk(chunk, meta),
    error: (e) => {
      audioEncoderError = e
    },
  })
  resources.audioEncoder = audioEncoder

  const audioConfig = await audioConfigFor(sampleRate, audioBitrate)
  audioEncoder.configure(audioConfig)

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

  const effects = createFx()
  effects.interest = depth?.interest || null

  for (let index = 0; index < clips.length; index++) {
    const clipTexture = new VideoFrameTexture()
    clipTexture.generateMipmaps = true
    clipTexture.minFilter = LinearMipmapLinearFilter
    clipTexture.magFilter = LinearFilter
    clipTexture.flipY = false
    clipTexture.wrapS = MirroredRepeatWrapping
    clipTexture.wrapT = MirroredRepeatWrapping
    resources.clipTextures.push(clipTexture)
  }
  const footage = createFootageState()
  const slotTextures = new Array(MAX_SLOTS).fill(null)

  const lyricData = processLyricTimestamps(lyricTimestamps)
  let lyricIndex = 0
  let lastLyricText = null

  const delta = 1 / fps
  const frameDuration = Math.round((1 / fps) * 1_000_000)
  const readback = !bench.canvasFrames && frameReadbackSupported(width, height) ? createFrameReadback(renderer, width, height, bench.batchFrames) : null
  resources.readback = readback
  const encodeFrame = (source, index, init) => {
    const videoFrame = new VideoFrame(source, { timestamp: Math.round((index / fps) * 1_000_000), duration: frameDuration, ...init })
    videoEncoder.encode(videoFrame, { keyFrame: index % keyFrameEvery === 0 })
    videoFrame.close()
  }
  const encodeBatch = async () => {
    for (const { index, bytes } of readback.read()) {
      if (!bench.noEncode) encodeFrame(bytes, index, { format: 'NV12', codedWidth: width, codedHeight: height, colorSpace: NV12_COLOR_SPACE })
      await waitForEncoderQueue(videoEncoder, VIDEO_QUEUE_FRAMES, videoError)
    }
  }

  let sample = null
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

    const at = plan ? directorAt(plan, currentTime, directorCursor) : null
    stepFx(effects, plan, at, currentTime, delta, true)
    effects.blur *= Math.exp(-delta * 6)
    const effectsDone = performance.now()

    material.uniforms.u_time.value = currentTime
    setCamera(material.uniforms, effects)
    material.uniforms.u_brightness.value = effects.brightness
    material.uniforms.u_contrast.value = effects.contrast
    material.uniforms.u_saturation.value = effects.saturation
    material.uniforms.u_hue.value = (effects.hue * Math.PI) / 180
    setGrade(material.uniforms)
    material.uniforms.u_chromatic.value = effects.chromatic
    material.uniforms.u_flicker.value = effects.flicker
    applyFocus(material.uniforms, focus, depth?.grid || null, false, delta, effects, true)

    let look = 'cover'
    let textures = null
    if (at) {
      const shot = at.shot
      if (clips.length) {
        stepFootage(footage, plan, at, currentTime, delta, effects, clips, true)
        if (shot.footage) textures = await footageFrames(shot.footage, footage, resources.clipTextures, slotTextures, readers, frame)
        releaseIdleClips(readers, frame - fps * CLIP_IDLE_SECONDS)
      }
      const showable = id => !(lookById(id).needsDepth && !lookContext.bands)
      look = textures && showable(shot.footage.look) ? shot.footage.look : shot.look
      if (!showable(look)) look = 'cover'
      lookContext.footage = !!textures && look === shot.footage.look
      if (look !== 'cover') {
        directLook(lookContext, plan, at, effects, currentTime)
        lookContext.hue = material.uniforms.u_hue.value
        lookContext.rotation = material.uniforms.u_rotation.value
        setLookUniforms(material.uniforms, shot, lookContext, look)
        if (lookContext.footage) setFootageUniforms(material.uniforms, shot, lookContext, textures, palette)
      }
    }
    mesh.material = materials[look]
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

    if (readback) {
      renderer.setRenderTarget(readback.target)
      renderer.render(scene, camera)
      if (frame === bench.sampleFrame) sample = readFrame(gl, width, height)
      readback.submit(frame)
    } else {
      renderer.render(scene, camera)
      if (frame === bench.sampleFrame) sample = readFrame(gl, width, height)
      if (bench.noEncode) gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, syncPixel)
      else encodeFrame(canvas, frame)
    }
    const drawDone = performance.now()

    if (readback) {
      if (readback.full()) await encodeBatch()
    } else {
      await waitForEncoderQueue(videoEncoder, VIDEO_QUEUE_FRAMES, videoError)
    }

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
  if (readback && readback.waiting() > 0) await encodeBatch()
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
    frames_source: readback ? `readback, ${readback.batch} a read` : 'canvas',
    format: `${width}x${height} ${fps} fps, ${(encoderConfig.bitrate / 1e6).toFixed(1)} Mbps video, ${audioConfig.bitrate / 1000} kbps audio`,
    constantBitrate,
    clipsLoaded: readers.length,
    clipsWanted: clipUrls.length,
    sample,
  }

  if (bench.noEncode) return { blob: new Blob([]), stats }

  onStatus?.('Building MP4 file...')
  muxer.finalize()
  const blob = fileFromPieces(pieces)

  onProgress?.(1)

  return { blob, stats }

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
      resources.readback?.dispose()
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
      for (const lookMaterial of resources.lookMaterials) lookMaterial.dispose()
    } catch { /* ignore */ }

    try {
      if (resources.artworkTexture) resources.artworkTexture.dispose()
      if (resources.depthTexture) resources.depthTexture.dispose()
      resources.soft?.dispose()
    } catch { /* ignore */ }

    try {
      if (resources.lyricTexture) resources.lyricTexture.dispose()
    } catch { /* ignore */ }

    try {
      if (resources.transparentPixel) resources.transparentPixel.dispose()
    } catch { /* ignore */ }

    for (const tex of resources.clipTextures) {
      try {
        tex.image?.close?.()
        tex.dispose()
      } catch { /* ignore */ }
    }

    for (const reader of resources.clipReaders) {
      try { reader.close() } catch { /* ignore */ }
    }
  }
}

function releaseIdleClips(readers, before) {
  for (const reader of readers) {
    if (reader.decoder && reader.usedAt < before) reader.close()
  }
}

async function footageFrames(shotFootage, state, clipTextures, slots, readers, index) {
  for (let k = 0; k < MAX_SLOTS; k++) {
    const clip = shotFootage.clips[k]
    if (clip === undefined) {
      slots[k] = null
      continue
    }
    const texture = clipTextures[clip]
    const time = state.times[k]
    readers[clip].usedAt = index
    const fresh = Math.abs(time - (texture.userData.time ?? -1)) < CLIP_FRAME_S
    const frame = fresh ? null : await readers[clip].frameAt(time)
    if (frame) {
      texture.userData.time = time
      const previous = texture.image
      texture.userData.width = frame.displayWidth
      texture.userData.height = frame.displayHeight
      texture.setFrame(frame)
      if (previous !== frame) previous?.close?.()
    }
    if (!(texture.version > 0)) return null
    slots[k] = texture
  }
  return slots
}

async function loadTextures(packBlob) {
  const { color, map } = await decodePack(packBlob, { map: true })
  const texture = new CanvasTexture(color)
  texture.image = color
  texture.flipY = false
  texture.wrapS = MirroredRepeatWrapping
  texture.wrapT = MirroredRepeatWrapping
  const small = analysisPixels(color, (w, h) => new OffscreenCanvas(w, h))
  const palette = coverPalette(small.data)
  const depth = map ? readDepth(map, (w, h) => new OffscreenCanvas(w, h)) : null
  if (!depth?.bands) return { texture, depth: null, palette }
  const depthTexture = new CanvasTexture(depth.canvas)
  depthTexture.flipY = false
  depthTexture.wrapS = MirroredRepeatWrapping
  depthTexture.wrapT = MirroredRepeatWrapping
  depthTexture.minFilter = LinearMipmapLinearFilter
  const detail = detailGrid(small.data, small.width, small.height)
  return { texture, depth: { texture: depthTexture, bands: depth.bands, grid: depth.grid, interest: interestPoints(depth.grid, detail, false) }, palette }
}
