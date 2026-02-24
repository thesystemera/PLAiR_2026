import * as THREE from 'three'
import { Muxer, ArrayBufferTarget } from 'mp4-muxer'
import {
  backgroundVertexShader,
  backgroundFragmentShader,
  createInitialEffects,
  buildVisualCueMap,
  calculateFrameEffects,
  processLyricTimestamps,
  getLyricAt,
  renderLyricToCanvas,
} from '../components/AudioReactiveCanvas'

function seededRandom(seed) {
  let s = seed
  return () => { s = Math.sin(s * 9999) * 10000; return s - Math.floor(s) }
}

export async function renderVideo({
  artworkUrl,
  audioUrl,
  audioFeatures,
  lyricTimestamps,
  videoClips = [],
  durationMs,
  width = 720,
  height = 1280,
  fps = 30,
  onProgress,
  onStatus
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
    clipVideos: [],
  }

  try {
    const canvas = new OffscreenCanvas(width, height)
    const gl = canvas.getContext('webgl2', {
      preserveDrawingBuffer: true,
      antialias: false,
      alpha: false,
      powerPreference: 'high-performance',
      desynchronized: true,
    })

    if (!gl) {
      throw new Error('WebGL2 not supported')
    }

    const renderer = new THREE.WebGLRenderer({
      canvas,
      context: gl,
      antialias: false,
      alpha: false,
      powerPreference: 'high-performance',
    })
    renderer.setSize(width, height, false)
    renderer.setPixelRatio(1)
    resources.renderer = renderer

  const scene = new THREE.Scene()
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1)

  const flipCanvas = new OffscreenCanvas(width, height)
  const flipCtx = flipCanvas.getContext('2d')

  const lyricCanvas = new OffscreenCanvas(1024, 512)
  const lyricCtx = lyricCanvas.getContext('2d')
  const lyricTexture = new THREE.CanvasTexture(lyricCanvas)
  lyricTexture.flipY = false
  resources.lyricTexture = lyricTexture

  onStatus?.('Loading artwork...')
  const artworkTexture = await loadTexture(artworkUrl)
  resources.artworkTexture = artworkTexture
  const texWidth = artworkTexture.image.width
  const texHeight = artworkTexture.image.height

  const transparentPixel = new THREE.DataTexture(
    new Uint8Array([0, 0, 0, 0]), 1, 1, THREE.RGBAFormat
  )
  resources.transparentPixel = transparentPixel

  const material = new THREE.ShaderMaterial({
    vertexShader: backgroundVertexShader,
    fragmentShader: backgroundFragmentShader,
    uniforms: {
      u_texture: { value: artworkTexture },
      u_texture_prev: { value: artworkTexture },
      u_depth_map: { value: transparentPixel },
      u_text_texture: { value: lyricTexture },
      u_video_clip: { value: transparentPixel },
      u_video_clip_blend: { value: 0.0 },
      u_transition: { value: 1.0 },
      u_tex_resolution: { value: new THREE.Vector2(texWidth, texHeight) },
      u_canvas_resolution: { value: new THREE.Vector2(width, height) },
      u_parallax: { value: new THREE.Vector2(0, 0) },
      u_glitch: { value: new THREE.Vector2(0, 0) },
      u_time: { value: 0.0 },
      u_scale: { value: 1.0 },
      u_frame_offset: { value: new THREE.Vector2(0, 0) },
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

  const geometry = new THREE.PlaneGeometry(2, 2)
  resources.geometry = geometry
  const mesh = new THREE.Mesh(geometry, material)
  scene.add(mesh)
  resources.material = material

  onStatus?.('Loading audio...')
  const audioResponse = await fetch(audioUrl)
  const audioArrayBuffer = await audioResponse.arrayBuffer()

  onStatus?.('Decoding audio...')
  const audioCtx = new OfflineAudioContext(2, 44100 * Math.ceil(durationMs / 1000), 44100)
  const audioData = await audioCtx.decodeAudioData(audioArrayBuffer.slice(0))

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
      sampleRate: 44100,
    },
    firstTimestampBehavior: 'offset',
    fastStart: 'in-memory',
  })

  const videoEncoder = new VideoEncoder({
    output: (chunk, meta) => muxer.addVideoChunk(chunk, meta),
    error: (e) => console.error('VideoEncoder error:', e),
  })
  resources.videoEncoder = videoEncoder

  videoEncoder.configure({
    codec: 'avc1.64001f',
    width,
    height,
    bitrate: 1_000_000,
    framerate: fps,
  })

  const audioEncoder = new AudioEncoder({
    output: (chunk, meta) => muxer.addAudioChunk(chunk, meta),
    error: (e) => console.error('AudioEncoder error:', e),
  })
  resources.audioEncoder = audioEncoder

  audioEncoder.configure({
    codec: 'mp4a.40.2',
    numberOfChannels: 2,
    sampleRate: 44100,
    bitrate: 128000,
  })

  onStatus?.('Encoding audio...')
  const left = audioData.getChannelData(0)
  const right = audioData.numberOfChannels > 1 ? audioData.getChannelData(1) : left
  const samplesPerChunk = 1024

  for (let i = 0; i < left.length; i += samplesPerChunk) {
    const chunkSize = Math.min(samplesPerChunk, left.length - i)
    const planarData = new Float32Array(chunkSize * 2)
    for (let j = 0; j < chunkSize; j++) {
      planarData[j] = left[i + j]
      planarData[chunkSize + j] = right[i + j]
    }

    const audioFrame = new AudioData({
      format: 'f32-planar',
      sampleRate: 44100,
      numberOfFrames: chunkSize,
      numberOfChannels: 2,
      timestamp: (i / 44100) * 1_000_000,
      data: planarData,
    })
    audioEncoder.encode(audioFrame)
    audioFrame.close()
  }

  const effects = createInitialEffects()

  const beats = audioFeatures?.beats || []
  const visualCueMap = buildVisualCueMap(beats)

  if (videoClips.length > 0) {
    onStatus?.('Loading video clips...')
    for (const clip of videoClips) {
      try {
        const video = document.createElement('video')
        video.crossOrigin = 'anonymous'
        video.muted = true
        video.preload = 'auto'
        video.src = clip.url

        await new Promise((resolve, reject) => {
          video.onloadeddata = resolve
          video.onerror = reject
          video.load()
        })

        const clipTexture = new THREE.VideoTexture(video)
        clipTexture.minFilter = THREE.LinearFilter
        clipTexture.magFilter = THREE.LinearFilter
        clipTexture.flipY = false
        resources.clipTextures.push(clipTexture)
        resources.clipVideos.push(video)
      } catch (e) {
        console.warn('Failed to load video clip:', clip.url, e)
      }
    }
    onStatus?.(`Loaded ${resources.clipTextures.length} video clips`)
  }

  let currentClipIndex = 0
  let videoClipBlend = 0
  const clipVirtualTime = new Array(resources.clipVideos.length).fill(0)

  const trackingState = {
    lastSegmentIndex: 0,
    lastBeatIndex: 0,
    tempoTime: 0,
    energyHistory: [],
  }
  const rand = seededRandom(42)

  const lyricData = processLyricTimestamps(lyricTimestamps)
  let lyricIndex = 0
  let lastLyricText = null

  const delta = 1 / fps

  onStatus?.('Rendering frames...')

  for (let frame = 0; frame < totalFrames; frame++) {
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

      const clipVideo = resources.clipVideos[currentClipIndex]
      if (clipVideo && clipVideo.duration) {
        const seekTime = clipVirtualTime[currentClipIndex] % clipVideo.duration

        if (Math.abs(clipVideo.currentTime - seekTime) > 0.001) {
          clipVideo.currentTime = seekTime

          await new Promise(resolve => {
            const onSeeked = () => {
              clipVideo.removeEventListener('seeked', onSeeked)
              resolve()
            }
            clipVideo.addEventListener('seeked', onSeeked)
          })
        }
      }

      material.uniforms.u_video_clip.value = resources.clipTextures[currentClipIndex]
      material.uniforms.u_video_clip_blend.value = videoClipBlend
      resources.clipTextures[currentClipIndex].needsUpdate = true
    }

    const currentLyric = getLyricAt(lyricData, timeMs, lyricIndex)
    if (currentLyric !== lastLyricText || frame === 0) {
      renderLyricToCanvas(lyricCtx, currentLyric, 1024, 512)

      lyricCtx.fillStyle = 'rgba(255,255,255,0.6)'
      lyricCtx.font = '600 36px Inter, Arial, sans-serif'
      lyricCtx.textAlign = 'center'
      lyricCtx.textBaseline = 'middle'
      lyricCtx.fillText('PLAIR.live', 512, 480)

      lyricTexture.needsUpdate = true
      lastLyricText = currentLyric
    }

    renderer.render(scene, camera)

    flipCtx.save()
    flipCtx.translate(0, height)
    flipCtx.scale(1, -1)
    flipCtx.drawImage(canvas, 0, 0)
    flipCtx.restore()

    const bitmap = await createImageBitmap(flipCanvas)
    const videoFrame = new VideoFrame(bitmap, {
      timestamp: (frame / fps) * 1_000_000,
      duration: (1 / fps) * 1_000_000,
    })
    videoEncoder.encode(videoFrame, { keyFrame: frame % 30 === 0 })
    videoFrame.close()
    bitmap.close()

    onProgress?.(frame / totalFrames)

    if (frame % 10 === 0) {
      await new Promise(r => setTimeout(r, 0))
    }
  }

  onStatus?.('Finishing video encoding...')
  await videoEncoder.flush()

  onStatus?.('Finishing audio encoding...')
  await audioEncoder.flush()

  onStatus?.('Building MP4 file...')
  muxer.finalize()

  onProgress?.(1)

  const { buffer } = muxer.target
  return new Blob([buffer], { type: 'video/mp4' })

  } finally {
    onStatus?.('Cleaning up...')

    try {
      if (resources.videoEncoder) {
        resources.videoEncoder.close()
      }
    } catch (e) { /* ignore */ }

    try {
      if (resources.audioEncoder) {
        resources.audioEncoder.close()
      }
    } catch (e) { /* ignore */ }

    try {
      if (resources.renderer) {
        resources.renderer.dispose()
        resources.renderer.forceContextLoss()
      }
    } catch (e) { /* ignore */ }

    try {
      if (resources.geometry) resources.geometry.dispose()
    } catch (e) { /* ignore */ }

    try {
      if (resources.material) resources.material.dispose()
    } catch (e) { /* ignore */ }

    try {
      if (resources.artworkTexture) resources.artworkTexture.dispose()
    } catch (e) { /* ignore */ }

    try {
      if (resources.lyricTexture) resources.lyricTexture.dispose()
    } catch (e) { /* ignore */ }

    try {
      if (resources.transparentPixel) resources.transparentPixel.dispose()
    } catch (e) { /* ignore */ }

    // Cleanup video clips
    for (const tex of resources.clipTextures) {
      try { tex.dispose() } catch (e) { /* ignore */ }
    }
    for (const video of resources.clipVideos) {
      try {
        video.pause()
        video.src = ''
        video.load()
      } catch (e) { /* ignore */ }
    }

    await new Promise(r => setTimeout(r, 100))
  }
}

async function loadTexture(url) {
  const response = await fetch(url)
  const blob = await response.blob()
  const bitmap = await createImageBitmap(blob)
  const texture = new THREE.CanvasTexture(bitmap)
  texture.image = bitmap
  texture.flipY = false
  return texture
}