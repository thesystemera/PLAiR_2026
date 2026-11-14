import { api } from './api'
import { logger } from './logger'
import { FULL_PACK_SIZE, blobForUrl, packCache } from './mediaCache'
import { artistCredit } from './utils'

const VIDEO_WIDTH = 720
const VIDEO_HEIGHT = 1280
const VIDEO_FPS = 30
const AUDIO_SAMPLE_RATE = 44100
const CLIP_SEEK_TOLERANCE_S = 0.001
const ETA_WINDOW_MS = 20000
const ETA_MIN_WINDOW_MS = 4000
const ETA_STEP_S = 5

export const SHARE_JOB_ID = 'share-video'

export const shareVideoBusy = status => status === 'preparing' || status === 'rendering' || status === 'publishing'
export const shareVideoRendering = status => status === 'rendering'

export function shareJobView({ status, title, stage, progress, etaSeconds, error }) {
  if (status === 'idle') return null
  return {
    job_id: SHARE_JOB_ID,
    job_type: 'share_video',
    status: status === 'complete' ? 'completed' : status === 'error' ? 'failed' : 'processing',
    title,
    current_stage: stage,
    progress_percent: Math.round(progress * 100),
    eta_seconds: shareVideoRendering(status) ? etaSeconds : null,
    error,
  }
}

let publish = null
let run = null
let video = { blob: null, url: null }
let lastPercent = -1
const bench = { options: null, last: null }

function secondsLeft(samples, progress, now) {
  samples.push({ at: now, progress })
  while (samples.length > 2 && now - samples[0].at > ETA_WINDOW_MS) samples.shift()
  const first = samples[0]
  if (now - first.at < ETA_MIN_WINDOW_MS || progress <= first.progress) return null
  const perMs = (progress - first.progress) / (now - first.at)
  return Math.max(ETA_STEP_S, Math.round((1 - progress) / perMs / 1000 / ETA_STEP_S) * ETA_STEP_S)
}

function report(updates) {
  publish?.(updates)
}

function releaseVideo() {
  if (video.url) URL.revokeObjectURL(video.url)
  video = { blob: null, url: null }
}

function endRun(current) {
  current.worker?.terminate()
  current.worker = null
  for (const clip of current.clips) {
    clip.pause()
    clip.removeAttribute('src')
    clip.load()
  }
  current.clips = []
  if (run === current) run = null
}

async function decodeAudio(trackId, durationMs) {
  const audioUrl = `${window.location.origin}${api.getRenderAudioUrl(trackId)}`
  const response = await fetch(audioUrl, { cache: 'no-store' })
  if (!response.ok) throw new Error(`Unable to load audio for video (${response.status})`)

  const contentType = response.headers.get('content-type') || 'unknown'
  const bytes = await response.arrayBuffer()
  if (!bytes.byteLength) throw new Error('Unable to load audio for video: empty audio response')

  const size = bytes.byteLength
  const context = new OfflineAudioContext(2, AUDIO_SAMPLE_RATE * Math.ceil(durationMs / 1000), AUDIO_SAMPLE_RATE)
  let decoded
  try {
    decoded = await context.decodeAudioData(bytes)
  } catch (error) {
    logger.error('[ShareVideo] Audio decode failed:', { url: audioUrl, contentType, bytes: size, error })
    throw new Error(`Unable to decode audio data (${contentType})`)
  }
  const left = decoded.getChannelData(0).slice()
  const right = decoded.numberOfChannels > 1 ? decoded.getChannelData(1).slice() : left.slice()
  return { left, right, sampleRate: decoded.sampleRate }
}

async function loadClips(clipData) {
  const clips = []
  for (const source of clipData?.clips || []) {
    const clip = document.createElement('video')
    clip.crossOrigin = 'anonymous'
    clip.muted = true
    clip.preload = 'auto'
    clip.src = `${window.location.origin}${source.url}`
    try {
      await new Promise((resolve, reject) => {
        clip.onloadeddata = resolve
        clip.onerror = reject
        clip.load()
      })
      clips.push(clip)
    } catch (error) {
      logger.warn('[ShareVideo] Failed to load video clip:', source.url, error)
    }
  }
  return clips
}

async function clipFrameAt(clip, time) {
  if (Math.abs(clip.currentTime - time) > CLIP_SEEK_TOLERANCE_S) {
    clip.currentTime = time
    await new Promise(resolve => clip.addEventListener('seeked', resolve, { once: true }))
  }
  return new VideoFrame(clip)
}

function renderInWorker(current, say, job, transfer) {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('./shareVideoWorker.js', import.meta.url), { type: 'module' })
    current.worker = worker
    current.abort = reject
    const samples = []
    worker.onmessage = ({ data }) => {
      if (data.type === 'progress') {
        const etaSeconds = secondsLeft(samples, data.progress, performance.now())
        const percent = Math.round(data.progress * 100)
        if (percent !== lastPercent) {
          lastPercent = percent
          say({ progress: percent / 100, etaSeconds })
        }
      } else if (data.type === 'stage') {
        say({ stage: data.stage })
      } else if (data.type === 'clip') {
        const answer = frame => worker.postMessage({ type: 'clipFrame', id: data.id, frame }, frame ? [frame] : [])
        clipFrameAt(current.clips[data.index], data.time).then(answer, () => answer(null))
      } else if (data.type === 'done') {
        resolve(data)
      } else if (data.type === 'error') {
        reject(new Error(data.message))
      }
    }
    worker.onerror = event => reject(new Error(event.message || 'The video renderer stopped unexpectedly'))
    worker.postMessage({ type: 'start', job }, transfer)
  })
}

async function start(track, { includeClips = false } = {}) {
  if (!track?.id || run) return
  const current = { worker: null, clips: [], abort: null }
  const live = () => run === current
  const say = updates => { if (live()) report(updates) }
  run = current
  releaseVideo()
  lastPercent = -1

  const trackId = track.id
  const title = track.generation_params?.title || 'Untitled'
  report(null)
  say({ status: 'preparing', track, title, stage: 'Fetching track data...' })

  try {
    const [audioFeatures, lyricTimestamps, clipData] = await Promise.all([
      api.getAudioFeatures(trackId).catch(() => null),
      api.getLyricTimestamps(trackId).catch(() => null),
      includeClips ? api.getVideoClips(trackId).catch(() => null) : null,
    ])
    if (!live()) return

    const packUrl = track.has_artwork ? await packCache(FULL_PACK_SIZE).getMedia(trackId) : null
    const artworkBlob = packUrl ? blobForUrl(packUrl) : null
    if (!live()) return
    if (!artworkBlob) throw new Error('Track has no artwork')

    say({ stage: 'Loading audio...' })
    const pcm = await decodeAudio(trackId, track.duration_ms)
    if (!live()) return

    let clipsNote = null
    if (includeClips) {
      say({ stage: 'Loading video clips...' })
      const clips = await loadClips(clipData)
      current.clips = clips
      if (!live()) return endRun(current)
      const wanted = clipData?.clips?.length || 0
      if (!clips.length) clipsNote = 'No video clips could be added, so the video was made without them.'
      else if (clips.length < wanted) clipsNote = "Some video clips couldn't be loaded."
    }

    const options = bench.options || {}
    const durationMs = options.seconds ? Math.min(track.duration_ms, options.seconds * 1000) : track.duration_ms
    if (durationMs < track.duration_ms) {
      const samples = Math.ceil(durationMs / 1000 * pcm.sampleRate)
      pcm.left = pcm.left.slice(0, samples)
      pcm.right = pcm.right.slice(0, samples)
    }
    say({ status: options.keepVisuals ? 'preparing' : 'rendering', stage: 'Rendering frames...' })
    const { buffer, stats } = await renderInWorker(current, say, {
      artworkBlob,
      pcm,
      audioFeatures,
      lyricTimestamps,
      clipDurations: current.clips.map(clip => clip.duration || 0),
      durationMs,
      width: VIDEO_WIDTH,
      height: VIDEO_HEIGHT,
      fps: VIDEO_FPS,
      bench: options,
    }, [pcm.left.buffer, pcm.right.buffer])
    stats.megabitsPerSecond = +(buffer.byteLength * 8 / (durationMs / 1000) / 1e6).toFixed(2)
    bench.last = stats
    logger.info(`[ShareVideo] ${stats.frames} frames in ${stats.seconds} s (${stats.speed}x realtime), ${stats.megabitsPerSecond} Mbps, ms per frame:`, stats.msPerFrame)

    const blob = new Blob([buffer], { type: 'video/mp4' })
    video = { blob, url: URL.createObjectURL(blob) }
    say({ status: 'publishing', stage: 'Publishing video to PLAiR...', progress: 1, etaSeconds: null, url: video.url, sizeBytes: blob.size })

    let publishedUrl = null
    let publishNote = null
    if (!bench.options && !api.token) {
      publishNote = 'Video rendered. Sign in to publish a hosted PLAiR link, or use Share Video to send the MP4 directly.'
    } else if (!bench.options) {
      try {
        const published = await api.uploadShareVideo({ trackId, title, artist: artistCredit(track) || 'Unknown Artist', blob })
        publishedUrl = published.page_url
      } catch (error) {
        logger.warn('[ShareVideo] Hosted publish failed, the MP4 stays available on this device:', error)
        publishNote = `Video rendered, but the hosted link failed (${error?.message || 'unknown error'}). Use Share Video to send the MP4 directly.`
      }
    }
    const note = [publishNote, clipsNote].filter(Boolean).join(' ') || null
    say({ status: 'complete', stage: 'Video ready', publishedUrl, note })
  } catch (error) {
    if (!live()) return
    logger.error('[ShareVideo] Render failed:', error)
    say({ status: 'error', stage: '', error: error.message || 'Failed to generate video' })
  } finally {
    endRun(current)
  }
}

function cancel() {
  const current = run
  if (!current) return
  endRun(current)
  current.abort?.(new Error('cancelled'))
  releaseVideo()
  report(null)
}

function clear() {
  if (run) return
  releaseVideo()
  report(null)
}

if (typeof window !== 'undefined') {
  window.__plairShare = {
    set: options => { bench.options = options || null },
    last: () => bench.last,
    clear: () => clear(),
  }
}

export const shareVideoJob = {
  connect(publisher) { publish = publisher },
  start,
  cancel,
  clear,
  blob: () => video.blob,
}
