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
import {drawLyricFrame, lyricCanvasSize, lyricFrameAt, prepareLyricStyle} from '../lib/lyricStyle'
import {loadLyricFonts} from '../lib/lyricFonts'
import {usePlaybackActions} from '../contexts/PlaybackContext'
import {shareVideoRendering} from '../lib/shareVideoJob'
import {scrollChrome} from '../lib/scrollChrome'

const VOICE_GAIN = 2.2
const VOICE_STEADY = 0.4
const LYRIC_WIDTH = 1024
const LYRIC_HEIGHT = 512
const RENDERING_LABEL_SIZE = 0.16
const RENDERING_PERCENT_SIZE = 0.3
const RENDERING_TEXT_WIDTH = 0.84
const LYRIC_PROBE_DIVISOR = 16
const LYRIC_LEAD_MS = 16
const NO_PARALLAX = Object.freeze({ parallaxX: 0, parallaxY: 0 })
const CLIP_RATE_MIN = 0.0625
const CLIP_RATE_MAX = 16
const DEFAULT_CLIP_SECONDS = 30

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

function drawRenderingVideo(ctx, percent, width, height) {
  ctx.clearRect(0, 0, width, height)
  ctx.fillStyle = 'white'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  const unit = Math.min(width, height)
  const label = 'RENDERING VIDEO'
  ctx.font = `900 ${RENDERING_LABEL_SIZE * unit}px Inter, sans-serif`
  const labelSize = RENDERING_LABEL_SIZE * unit * Math.min(1, (width * RENDERING_TEXT_WIDTH) / ctx.measureText(label).width)
  ctx.font = `900 ${labelSize}px Inter, sans-serif`
  ctx.fillText(label, width / 2, height / 2 - labelSize * 0.9)
  ctx.font = `900 ${RENDERING_PERCENT_SIZE * unit}px Inter, sans-serif`
  ctx.fillText(`${percent}%`, width / 2, height / 2 + RENDERING_PERCENT_SIZE * unit * 0.55)
}

function createClipState() {
  return { videos: new Map(), callbacks: new Map(), sources: [], playing: new Set(), rates: new Map(), resting: false, hidden: false, frameTransfer: false }
}

function syncClipPlayback(clips) {
  for (const [index, video] of clips.videos) {
    const run = !clips.resting && !clips.hidden && clips.playing.has(index) && (clips.rates.get(index) ?? 1) > 0 && video.getAttribute('src')
    if (run) {
      if (video.paused) video.play().catch(() => {})
    } else if (!video.paused) {
      video.pause()
    }
  }
}

function clipVideo(clips, index, send) {
  const existing = clips.videos.get(index)
  if (existing) return existing
  const video = document.createElement('video')
  video.crossOrigin = 'anonymous'
  video.muted = true
  video.loop = true
  video.playsInline = true
  video.preload = 'auto'
  clips.videos.set(index, video)
  if (!clips.frameTransfer) {
    send({ type: 'clipFrame', index, video })
    return video
  }
  if (!video.requestVideoFrameCallback) return video
  const push = () => {
    if (clips.videos.get(index) !== video) return
    if (clips.playing.has(index) && video.readyState >= 2) {
      try {
        const frame = new VideoFrame(video)
        send({ type: 'clipFrame', index, frame }, [frame])
      } catch (error) {
        logger.debug('[AudioReactiveCanvas] Video frame skipped', error?.message)
      }
    }
    clips.callbacks.set(index, video.requestVideoFrameCallback(push))
  }
  clips.callbacks.set(index, video.requestVideoFrameCallback(push))
  return video
}

function cueClip(clips, index, at, send) {
  const source = clips.sources[index]
  if (!source) return
  const video = clipVideo(clips, index, send)
  if (!video.getAttribute('src')) {
    video.src = source.url
    video.load()
  }
  if (!Number.isFinite(at)) return
  if (video.readyState >= 1) video.currentTime = at
  else video.addEventListener('loadedmetadata', () => { video.currentTime = at }, { once: true })
}

function releaseClips(clips) {
  for (const [index, video] of clips.videos) {
    const callback = clips.callbacks.get(index)
    if (callback) video.cancelVideoFrameCallback(callback)
    releaseVideo(video)
  }
  clips.videos = new Map()
  clips.callbacks = new Map()
  clips.playing = new Set()
  clips.rates = new Map()
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
    visualsResting: shareVideoRendering(state.shareVideoState.status),
    videoPercent: Math.round(state.shareVideoState.progress * 100),
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
  const { audio } = usePlaybackActions()
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
  const [clips] = useState(createClipState)
  const lyricRef = useRef({ canvas: null, words: null, styled: null, key: null })
  const aspectRef = useRef(LYRIC_WIDTH / LYRIC_HEIGHT)
  const evalsRef = useRef(new Map())

  latestRef.current = { ui, getUIState, audio, visualQuality, glassTaps, reduceMotion, levelIndex, reportFrame, reportRenderer, onContextLostChange, getAccentRgb, getCategoryMetadata, interactionEffectsRef, canvasDpr, referenceDpr }

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
      case 'footage': {
        const before = clips.playing
        clips.playing = new Set(message.play.map(clip => clip.index))
        const keep = new Set([...clips.playing, ...message.next.map(clip => clip.index)])
        for (const { index, at } of message.play) cueClip(clips, index, before.has(index) ? null : at, send)
        for (const { index, at } of message.next) if (!before.has(index) && !clips.playing.has(index)) cueClip(clips, index, at, send)
        for (const [index, video] of clips.videos) if (!keep.has(index) && video.getAttribute('src')) releaseVideo(video)
        syncClipPlayback(clips)
        break
      }
      case 'clipRates': {
        for (const [index, rate] of message.rates) {
          clips.rates.set(index, rate)
          const video = clips.videos.get(index)
          if (video && rate > 0) video.playbackRate = Math.min(CLIP_RATE_MAX, Math.max(CLIP_RATE_MIN, rate))
        }
        syncClipPlayback(clips)
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
  }, [send, clips])

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
      aspectRef.current = rect.width / Math.max(1, rect.height)
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
    clips.hidden = !ui.isScreenVisible
    syncClipPlayback(clips)
  }, [send, clips, ui.isScreenVisible])

  useEffect(() => {
    const features = ui.audioFeatures || null
    const timing = ui.lyricTimestamps
    const matched = features && timing?.id === features.id ? timing : null
    const lines = matched?.lyrics?.map(line => ({ start: line.start, end: line.end, line: line.line, words: line.words?.map(word => word.start) })) || null
    send({ type: 'features', features, sections: matched?.timed_sections || null, lines })
  }, [send, ui.audioFeatures, ui.lyricTimestamps])

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
    lyric.key = null
    lyric.words = null
    lyric.styled = null
    send({ type: 'lyric', image: null, empty: true, probe: null })
    const timestamps = ui.lyricTimestamps
    if (!timestamps || timestamps.instrumental) return undefined
    const { words, wordData } = new TextRenderer().prepareLyricData(timestamps)
    if (words.length > 0) lyric.words = wordData
    const styled = prepareLyricStyle(timestamps)
    if (!styled) return undefined
    let cancelled = false
    loadLyricFonts(styled.uses, styled.text).then(ready => {
      if (cancelled) return
      if (!ready) {
        logger.warn('[AudioReactiveCanvas] Lyric fonts did not load; showing plain lyrics')
        return
      }
      lyric.styled = styled
      lyric.key = null
    })
    return () => { cancelled = true }
  }, [send, ui.lyricTimestamps])

  const visualsResting = ui.visualsResting
  const videoPercent = visualsResting ? ui.videoPercent : 0
  const isPlaying = ui.isPlaying && !visualsResting
  useEffect(() => {
    clips.resting = visualsResting
    syncClipPlayback(clips)
  }, [clips, visualsResting])

  useEffect(() => {
    if (!ui.isScreenVisible) return undefined
    const lyric = lyricRef.current
    const clockMs = () => {
      const latest = latestRef.current
      const heard = latest.getUIState().engineState?.isActiveDevice ? latest.audio?.getEngine()?.heardPositionMs() : null
      return (heard ?? latest.ui.engineRef?.current?.progress_ms ?? 0) + LYRIC_LEAD_MS
    }
    const update = () => {
      const seconds = clockMs() / 1000
      const aspect = aspectRef.current
      let key = null
      let draw = null
      let size = { width: LYRIC_WIDTH, height: LYRIC_HEIGHT }
      if (visualsResting) {
        size = lyricCanvasSize(aspect)
        key = `rendering:${videoPercent}:${aspect.toFixed(3)}`
        draw = (ctx, width, height) => drawRenderingVideo(ctx, videoPercent, width, height)
      } else if (lyric.styled) {
        size = lyricCanvasSize(aspect)
        const frame = lyricFrameAt(lyric.styled, seconds)
        key = frame ? `${frame.index}:${frame.count}:${aspect.toFixed(3)}` : 'none'
        if (frame) draw = (ctx, width, height) => drawLyricFrame(ctx, lyric.styled, frame, width, height, aspect)
      } else if (lyric.words) {
        const index = lyric.words.findIndex(word => seconds >= word.start && seconds <= word.end)
        const text = index >= 0 ? lyric.words[index].text : null
        key = `${index}`
        if (text) draw = (ctx, width, height) => renderLyricToCanvas(ctx, text, width, height)
      } else {
        key = 'none'
      }
      if (key === lyric.key) return
      lyric.key = key
      if (lyric.canvas.width !== size.width || lyric.canvas.height !== size.height) {
        lyric.canvas.width = size.width
        lyric.canvas.height = size.height
      }
      if (!draw) {
        lyric.canvas.getContext('2d').clearRect(0, 0, lyric.canvas.width, lyric.canvas.height)
        send({ type: 'lyric', image: null, empty: true, probe: null })
        return
      }
      draw(lyric.canvas.getContext('2d'), lyric.canvas.width, lyric.canvas.height)
      const probe = textPixels(draw, lyric.canvas.width, lyric.canvas.height, Math.round(lyric.canvas.width / LYRIC_PROBE_DIVISOR), Math.round(lyric.canvas.height / LYRIC_PROBE_DIVISOR))
      createImageBitmap(lyric.canvas).then(image => send({ type: 'lyric', image, empty: false, probe }, [image]))
    }
    update()
    if (!isPlaying) return undefined
    let frame = null
    const tick = () => {
      frame = requestAnimationFrame(tick)
      update()
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [send, isPlaying, visualsResting, videoPercent, ui.isScreenVisible, ui.lyricTimestamps])

  useEffect(() => {
    releaseClips(clips)
    clips.sources = videoClips || []
    clips.frameTransfer = channelRef.current?.thread === 'worker' && typeof VideoFrame !== 'undefined'
    send({ type: 'clips', clips: clips.sources.map(clip => ({ duration: clip.duration || DEFAULT_CLIP_SECONDS })) })
    return () => releaseClips(clips)
  }, [send, clips, videoClips])

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
          isPlaying: !!engine.is_playing && !latest.ui.visualsResting,
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
          headerReveal: scrollChrome.reveal(),
          chromeReveal: scrollChrome.chromeReveal(),
          chromeLift: scrollChrome.sceneLift(),
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
