import {memo, useCallback, useEffect, useRef, useState} from 'react'
import {TextRenderer} from '../lib/textRenderer'
import { useVideoClips, useUISelector, useUIStateGetter } from '../contexts/UIStateContext'
import {PANEL, useDynamicTheme, useThemeArtwork} from '../contexts/DynamicThemeContext'
import {useDepthMap} from '../hooks/useDepthMap'
import {FULL_PACK_SIZE, SCENE_PACK_SIZE, blobForUrl, packCache} from '../lib/mediaCache'
import {VisualErrorBoundary} from './VisualErrorBoundary'
import {isWebGL2Available} from '../lib/utils'
import {logger} from '../lib/logger'
import {safeStorage} from '../lib/safeStorage'
import {REFERENCE_SCENE_DPR, useQuality} from '../contexts/QualityContext'
import {isSceneRenderingPaused} from '../lib/renderPause'
import {splashReady} from '../lib/splash'
import {lightProbeWanted, publishBeat, publishLightLevel, publishLightProbe, setLightGlow} from '../lib/lightProbe'
import {textPixels} from '../lib/backgroundProbe'
import {addFrameWork, frameStatsActive} from '../lib/frameStats'
import {renderLyricToCanvas} from '../lib/sceneEffects'

const VOICE_GAIN = 2.2
const VOICE_STEADY = 0.4
const LYRIC_WIDTH = 1024
const LYRIC_HEIGHT = 512
const LYRIC_PROBE_WIDTH = 64
const LYRIC_PROBE_HEIGHT = 32
const NO_PARALLAX = Object.freeze({ parallaxX: 0, parallaxY: 0 })

function averageLevel(data) {
  if (!data || !data.length) return 0
  let sum = 0
  for (let i = 0; i < data.length; i++) sum += data[i]
  return Math.min(1, (sum / data.length / 255) * VOICE_GAIN)
}

function sceneWorkerSupported() {
  if (safeStorage.get('plair_scene_thread') !== 'worker') return false
  if (typeof Worker === 'undefined' || typeof OffscreenCanvas === 'undefined') return false
  if (typeof HTMLCanvasElement === 'undefined' || !('transferControlToOffscreen' in HTMLCanvasElement.prototype)) return false
  try {
    return !!new OffscreenCanvas(1, 1).getContext('webgl2')
  } catch {
    return false
  }
}

function hexToRgb(hex) {
  const value = hex.replace('#', '')
  return { r: parseInt(value.substring(0, 2), 16), g: parseInt(value.substring(2, 4), 16), b: parseInt(value.substring(4, 6), 16) }
}

function pickColor(color) {
  return color ? { r: color.r, g: color.g, b: color.b } : null
}

function releaseVideo(video) {
  video.pause()
  video.removeAttribute('src')
  video.load()
}

function syncVideoClipPlayback(clipState) {
  const count = clipState.videos.length
  if (count === 0) return
  const nextIndex = (clipState.currentIndex + 1) % count
  clipState.videos.forEach((video, index) => {
    if (index === clipState.currentIndex || index === nextIndex) {
      if (!video.getAttribute('src')) {
        video.src = video.dataset.clipUrl
        video.load()
      }
      if (video.paused) video.play().catch(() => {})
    } else if (!video.paused) {
      video.pause()
    }
  })
}

const AudioReactiveScene = memo(function AudioReactiveScene({
  captureResolution = 128,
  glassBlurFactor = 1.0,
  onContextLostChange,
}) {
  const ui = useUISelector(state => ({
    audioFeatures: state.audioFeatures,
    lyricTimestamps: state.lyricTimestamps,
    currentTrackId: state.engineState.currentTrack?.id,
    currentTrackHasArtwork: state.engineState.currentTrack?.has_artwork,
    isPlaying: !!state.engineState.is_playing,
    isOfflineRendering: state.isOfflineRendering,
    isScreenVisible: state.isScreenVisible,
    panelRegionsRef: state.shaderPanelRegions,
    panelOpacitiesRef: state.shaderPanelOpacities,
    radioButtonPosRef: state.shaderRadioButtonPos,
    radioButtonRef: state.radioButtonRef,
    radioProgressData: state.radioProgressData,
    speakerColorRef: state.speakerColorRef,
    djFftDataRef: state.djFftDataRef,
    gyroscopeRef: state.gyroscopeRef,
    mouseRef: state.mouseRef,
    interfaceRef: state.interfaceRef,
    engineRef: state.engineRef,
    activeSeedMode: state.radioState.activeSeedMode,
    isFullscreen: state.interfaceState?.isFullscreenVisuals ?? false,
  }))
  const trackId = ui.currentTrackId
  const hasArtwork = !!trackId && ui.currentTrackHasArtwork !== false
  const getUIState = useUIStateGetter()
  const { url: artworkPack } = useDepthMap(packCache(ui.isFullscreen ? FULL_PACK_SIZE : SCENE_PACK_SIZE), trackId, hasArtwork)
  const { interactionEffectsRef, getCategoryMetadata, getAccentRgb } = useDynamicTheme()
  const { sceneDpr, level: visualQuality, glassTaps, reduceMotion, levelIndex, reportFrame, reportRenderer } = useQuality()
  const deviceDpr = window.devicePixelRatio || 1
  const referenceDpr = Math.min(deviceDpr, visualQuality === 'high' ? REFERENCE_SCENE_DPR : 1.0)
  const canvasDpr = Math.min(deviceDpr, sceneDpr)
  const videoClips = useVideoClips(trackId)

  const canvasRef = useRef(null)
  const channelRef = useRef(null)
  const latestRef = useRef(null)
  const glowRef = useRef(new Float32Array(11))
  const clipsRef = useRef({ videos: [], currentIndex: 0 })
  const lyricRef = useRef({ canvas: null, words: null, index: -1 })
  const evalsRef = useRef(new Map())

  latestRef.current = { ui, getUIState, visualQuality, glassTaps, reduceMotion, levelIndex, reportFrame, reportRenderer, onContextLostChange, getAccentRgb, getCategoryMetadata, interactionEffectsRef, canvasDpr, referenceDpr }

  const send = useCallback((message, transfer) => channelRef.current?.send(message, transfer), [])

  const onSceneMessage = useCallback((message) => {
    const latest = latestRef.current
    switch (message.type) {
      case 'tick': {
        latest.reportFrame(message.delta, message.wanted)
        publishBeat(message.kick, message.pulse)
        publishLightLevel(message.level, message.target)
        glowRef.current.set(message.glow)
        if (message.probe) publishLightProbe(message.probe)
        if (message.work) {
          for (const name in message.work) {
            if (name !== 'gpuTimers') addFrameWork(name, message.work[name].avg)
          }
        }
        break
      }
      case 'ready': splashReady('scene'); break
      case 'renderer': latest.reportRenderer(message.name); break
      case 'context': latest.onContextLostChange(message.lost); break
      case 'clipIndex': {
        const clips = clipsRef.current
        clips.currentIndex = message.index
        syncVideoClipPlayback(clips)
        break
      }
      case 'clipRate': {
        const video = clipsRef.current.videos[clipsRef.current.currentIndex]
        if (video && video.readyState >= 2) video.playbackRate = message.rate
        break
      }
      case 'warn': logger.warn(`[AudioReactiveCanvas] ${message.message}`); break
      case 'evalResult': {
        const resolve = evalsRef.current.get(message.id)
        evalsRef.current.delete(message.id)
        resolve?.(message.value)
        break
      }
      default: break
    }
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return undefined
    let closed = false
    if (sceneWorkerSupported()) {
      const offscreen = canvas.transferControlToOffscreen()
      const worker = new Worker(new URL('../lib/sceneWorker.js', import.meta.url), { type: 'module' })
      worker.onmessage = event => onSceneMessage(event.data)
      worker.onerror = event => logger.error('[AudioReactiveCanvas] Scene worker failed', event.message)
      worker.postMessage({ type: 'init', canvas: offscreen, captureResolution, glassBlurFactor }, [offscreen])
      channelRef.current = { thread: 'worker', send: (message, transfer) => worker.postMessage(message, transfer || []), close: () => worker.terminate() }
    } else {
      const pending = []
      channelRef.current = { thread: 'main', send: message => pending.push(message), close: () => { closed = true } }
      import('../lib/sceneRenderer').then(({ SceneRenderer }) => {
        if (closed) return
        const scene = new SceneRenderer({ canvas, captureResolution, glassBlurFactor, emit: onSceneMessage })
        channelRef.current = { thread: 'main', send: message => scene.handle(message), close: () => scene.dispose() }
        for (const message of pending) scene.handle(message)
      })
    }
    logger.info(`[AudioReactiveCanvas] Scene renders on the ${channelRef.current.thread} thread`)
    return () => {
      channelRef.current?.close()
      channelRef.current = null
    }
  }, [captureResolution, glassBlurFactor, onSceneMessage])

  useEffect(() => {
    const glow = glowRef.current
    const out = [0, 0, 0]
    const glowAt = (x, y) => {
      if (glow[0] < 0.5) {
        out[0] = 0; out[1] = 0; out[2] = 0
        return out
      }
      const dx = (x - glow[1]) * glow[3]
      const dy = (y - glow[2]) * glow[4]
      const voice = Math.pow(2, -(dx * dx + dy * dy))
      const t = Math.min(1, Math.max(0, (Math.max(Math.abs(x - 0.5), Math.abs(y - 0.5)) - 0.225) / (0.5 - 0.225)))
      const air = 0.06 + 0.2 * (t * t * (3 - 2 * t))
      out[0] = glow[8] * air + glow[5] * voice
      out[1] = glow[9] * air + glow[6] * voice
      out[2] = glow[10] * air + glow[7] * voice
      return out
    }
    return setLightGlow(glowAt)
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return undefined
    const report = () => {
      const rect = canvas.getBoundingClientRect()
      const latest = latestRef.current
      send({ type: 'size', width: rect.width, height: rect.height, dpr: latest.canvasDpr, referenceDpr: latest.referenceDpr })
    }
    report()
    const observer = new ResizeObserver(report)
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [send, canvasDpr, referenceDpr])

  useEffect(() => {
    send({ type: 'visible', visible: !!ui.isScreenVisible })
  }, [send, ui.isScreenVisible])

  useEffect(() => {
    send({ type: 'features', features: ui.audioFeatures || null })
  }, [send, ui.audioFeatures])

  useEffect(() => {
    const blob = artworkPack ? blobForUrl(artworkPack) : null
    if (blob) send({ type: 'artwork', key: artworkPack, blob })
  }, [send, artworkPack])

  useEffect(() => {
    const lyric = lyricRef.current
    if (!lyric.canvas) {
      lyric.canvas = document.createElement('canvas')
      lyric.canvas.width = LYRIC_WIDTH
      lyric.canvas.height = LYRIC_HEIGHT
    }
    lyric.index = -1
    lyric.words = null
    send({ type: 'lyric', image: null, empty: true, probe: null })
    const timestamps = ui.lyricTimestamps
    if (!timestamps || timestamps.instrumental) return
    const { words, wordData } = new TextRenderer().prepareLyricData(timestamps)
    if (words.length > 0) lyric.words = wordData
  }, [send, ui.lyricTimestamps])

  const isPlaying = ui.isPlaying
  useEffect(() => {
    if (!ui.isScreenVisible) return undefined
    const lyric = lyricRef.current
    const update = () => {
      const words = lyric.words
      if (!words || words.length === 0) return
      const seconds = (latestRef.current.ui.engineRef?.current?.progress_ms || 0) / 1000
      const index = words.findIndex(word => seconds >= word.start && seconds <= word.end)
      if (index === lyric.index) return
      lyric.index = index
      const text = index >= 0 ? words[index].text : null
      renderLyricToCanvas(lyric.canvas.getContext('2d'), text, lyric.canvas.width, lyric.canvas.height)
      if (!text) {
        send({ type: 'lyric', image: null, empty: true, probe: null })
        return
      }
      const probe = textPixels((probeCtx, width, height) => renderLyricToCanvas(probeCtx, text, width, height), lyric.canvas.width, lyric.canvas.height, LYRIC_PROBE_WIDTH, LYRIC_PROBE_HEIGHT)
      createImageBitmap(lyric.canvas).then(image => send({ type: 'lyric', image, empty: false, probe }, [image]))
    }
    update()
    if (!isPlaying) return undefined
    const interval = setInterval(update, 100)
    return () => clearInterval(interval)
  }, [send, isPlaying, ui.isScreenVisible, ui.lyricTimestamps])

  useEffect(() => {
    const clips = clipsRef.current
    clips.videos.forEach(releaseVideo)
    clips.videos = []
    clips.currentIndex = 0
    const sources = videoClips || []
    send({ type: 'clips', count: sources.length })
    if (sources.length === 0) return undefined
    const frameTransfer = channelRef.current?.thread === 'worker' && typeof VideoFrame !== 'undefined'
    const callbacks = []
    sources.forEach((clip, index) => {
      const video = document.createElement('video')
      video.crossOrigin = 'anonymous'
      video.muted = true
      video.loop = true
      video.playsInline = true
      video.preload = 'auto'
      video.dataset.clipUrl = clip.url
      clips.videos.push(video)
      if (!frameTransfer || !video.requestVideoFrameCallback) {
        if (!frameTransfer) send({ type: 'clipFrame', index, video })
        return
      }
      const pushFrame = () => {
        if (!clips.videos.includes(video)) return
        if (index === clips.currentIndex && video.readyState >= 2) {
          try {
            const frame = new VideoFrame(video)
            send({ type: 'clipFrame', index, frame }, [frame])
          } catch (error) {
            logger.debug('[AudioReactiveCanvas] Video frame skipped', error?.message)
          }
        }
        callbacks[index] = video.requestVideoFrameCallback(pushFrame)
      }
      callbacks[index] = video.requestVideoFrameCallback(pushFrame)
    })
    syncVideoClipPlayback(clips)
    return () => {
      clips.videos.forEach((video, index) => { if (callbacks[index]) video.cancelVideoFrameCallback(callbacks[index]) })
      clips.videos.forEach(releaseVideo)
      clips.videos = []
    }
  }, [send, videoClips])

  useEffect(() => {
    let frame = null
    let lastClick = 0
    const tick = () => {
      frame = requestAnimationFrame(tick)
      const latest = latestRef.current
      const state = latest.ui
      const engine = latest.getUIState().engineState || {}
      const now = performance.now()
      const click = latest.interactionEffectsRef?.current?.click
      if (click?.active && click.timestamp !== lastClick) {
        lastClick = click.timestamp
        send({ type: 'click', intensity: click.intensity, age: now - click.timestamp })
      }
      const radioButton = state.radioButtonRef?.current
      const progress = state.radioProgressData
      const seedMeta = state.activeSeedMode ? latest.getCategoryMetadata(state.activeSeedMode) : null
      send({
        type: 'state',
        state: {
          isPlaying: !!engine.is_playing,
          progressMs: state.engineRef?.current?.progress_ms || 0,
          durationMs: engine.currentTrack?.duration_ms || 0,
          talkBreak: engine.talkBreak ? { paused: !!engine.talkBreak.paused } : null,
          crossfadeMs: engine.crossfadeMs || 0,
          scrollPosition: state.interfaceRef?.current?.scrollPosition || 0,
          scrollVelocity: state.interfaceRef?.current?.scrollVelocity || 0,
          panelRegions: state.panelRegionsRef?.current || [],
          panelOpacities: state.panelOpacitiesRef?.current || [],
          radioButtonPos: state.radioButtonPosRef?.current || null,
          radioButton: { opacity: radioButton?.opacity || 0, isHovered: !!radioButton?.isHovered, isPressed: !!radioButton?.isPressed },
          radioProgress: progress ? { stateInt: progress.stateInt || 0, currentVisualColor: pickColor(progress.currentVisualColor), onAirColor: pickColor(progress.onAirColor) } : null,
          speakerColor: pickColor(state.speakerColorRef?.current),
          voiceTarget: latest.reduceMotion ? VOICE_STEADY : averageLevel(state.djFftDataRef?.current),
          gyro: state.gyroscopeRef?.current || NO_PARALLAX,
          mouse: state.mouseRef?.current || NO_PARALLAX,
          accentRgb: pickColor(latest.getAccentRgb()),
          seedColor: seedMeta?.color ? hexToRgb(seedMeta.color) : { r: 0, g: 0, b: 0 },
          visualQuality: latest.visualQuality,
          glassTaps: latest.glassTaps,
          reduceMotion: latest.reduceMotion,
          levelIndex: latest.levelIndex,
          headerHeight: PANEL.headerHeight,
          probeWanted: lightProbeWanted(now),
          renderPaused: isSceneRenderingPaused(now),
          statsActive: frameStatsActive(),
        },
      })
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [send])

  useEffect(() => {
    let nextId = 0
    window.__plairScene = {
      set: bench => send({ type: 'bench', bench }),
      eval: code => new Promise(resolve => {
        const id = ++nextId
        evalsRef.current.set(id, resolve)
        send({ type: 'eval', id, code })
      }),
      thread: () => channelRef.current?.thread,
    }
    return () => { delete window.__plairScene }
  }, [send])

  if (ui.isOfflineRendering) return null

  return (
    <>
      <div className="absolute inset-0 bg-black/50 pointer-events-none z-0" />
      <canvas
        ref={canvasRef}
        style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', pointerEvents: 'none', zIndex: 0, display: 'block' }}
      />
    </>
  )
})

const VisualFallback = memo(function VisualFallback() {
  const currentArtwork = useThemeArtwork()

  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none z-0">
      {currentArtwork && (
        <div
          className="absolute -inset-16 bg-cover bg-center opacity-60"
          style={{ backgroundImage: `url(${currentArtwork.imageUrl})`, filter: 'blur(48px)' }}
        />
      )}
      <div className="absolute inset-0 bg-black/50" />
    </div>
  )
})

export const AudioReactiveCanvas = memo(function AudioReactiveCanvas(props) {
  const [webglAvailable] = useState(isWebGL2Available)
  const [contextLost, setContextLost] = useState(false)

  useEffect(() => {
    if (!webglAvailable) logger.warn('[AudioReactiveCanvas] WebGL2 unavailable, using static fallback')
  }, [webglAvailable])

  if (!webglAvailable) return <VisualFallback />

  return (
    <VisualErrorBoundary name="AudioReactiveCanvas" fallback={<VisualFallback />}>
      <AudioReactiveScene {...props} onContextLostChange={setContextLost} />
      {contextLost && <VisualFallback />}
    </VisualErrorBoundary>
  )
})
