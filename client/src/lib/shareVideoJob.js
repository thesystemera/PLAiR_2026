import { api } from './api'
import { logger } from './logger'
import { FULL_PACK_SIZE, blobForUrl, packCache } from './mediaCache'
import { artistCredit } from './utils'

const AUDIO_SAMPLE_RATE = 44100
const AUDIO_BITRATE = 256_000
const HOSTED_VIDEO_MAX_BYTES = 500 * 1024 * 1024
const SIZE_HEADROOM = 0.95
const ETA_WINDOW_MS = 20000
const ETA_MIN_WINDOW_MS = 4000
const ETA_STEP_S = 5

export const SHARE_JOB_ID = 'share-video'

export const SHARE_QUALITIES = {
  '1080p60': { lines: 1080, fps: 60, videoBitrate: 12_000_000, codec: 'avc1.4d002a' },
  '720p60': { lines: 720, fps: 60, videoBitrate: 7_500_000, codec: 'avc1.4d0020' },
  '720p30': { lines: 720, fps: 30, videoBitrate: 5_000_000, codec: 'avc1.4d001f' },
}

const SHARE_ORIENTATIONS = ['portrait', 'landscape']

function shareVideoFormat(quality, orientation, durationMs = 0) {
  const preset = SHARE_QUALITIES[quality] || SHARE_QUALITIES['1080p60']
  const long = Math.round(preset.lines * 16 / 9)
  const [width, height] = orientation === 'landscape' ? [long, preset.lines] : [preset.lines, long]
  const seconds = durationMs / 1000
  const fits = seconds > 0 ? Math.floor(HOSTED_VIDEO_MAX_BYTES * 8 * SIZE_HEADROOM / seconds) - AUDIO_BITRATE : preset.videoBitrate
  return { width, height, fps: preset.fps, codec: preset.codec, videoBitrate: Math.min(preset.videoBitrate, fits), audioBitrate: AUDIO_BITRATE }
}

let supportCheck = null

export function shareVideoSupport() {
  if (supportCheck) return supportCheck
  supportCheck = (async () => {
    const support = {}
    for (const quality of Object.keys(SHARE_QUALITIES)) {
      for (const orientation of SHARE_ORIENTATIONS) {
        const { width, height, fps, codec, videoBitrate } = shareVideoFormat(quality, orientation)
        const config = { codec, width, height, bitrate: videoBitrate, framerate: fps }
        const ask = extra => (typeof VideoEncoder === 'undefined'
          ? Promise.resolve(false)
          : VideoEncoder.isConfigSupported({ ...config, ...extra }).then(result => result.supported, () => false))
        const hardware = await ask({ hardwareAcceleration: 'prefer-hardware' })
        support[`${quality}:${orientation}`] = { supported: hardware || await ask({}), hardware }
      }
    }
    return support
  })()
  return supportCheck
}

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
  if (run === current) run = null
}

async function decodeAudio(trackId, durationMs) {
  const response = await api.fetchRenderAudio(trackId)
  const audioUrl = response.url
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

async function start(track, { includeClips = false, quality, orientation } = {}) {
  if (!track?.id || run) return
  const current = { worker: null, abort: null }
  const live = () => run === current
  const say = updates => { if (live()) report(updates) }
  run = current
  releaseVideo()
  lastPercent = -1

  const trackId = track.id
  const title = track.generation_params?.title || 'Untitled'
  report(null)
  say({ status: 'preparing', track, title, orientation: orientation === 'landscape' ? 'landscape' : 'portrait', stage: 'Fetching track data...' })

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

    const options = bench.options || {}
    const durationMs = options.seconds ? Math.min(track.duration_ms, options.seconds * 1000) : track.duration_ms
    if (durationMs < track.duration_ms) {
      const samples = Math.ceil(durationMs / 1000 * pcm.sampleRate)
      pcm.left = pcm.left.slice(0, samples)
      pcm.right = pcm.right.slice(0, samples)
    }
    const clipUrls = options.clipUrls || (clipData?.clips || []).map(source => `${window.location.origin}${source.url}`)
    const format = shareVideoFormat(options.quality || quality, options.orientation || orientation, durationMs)
    say({ status: options.keepVisuals ? 'preparing' : 'rendering', stage: 'Rendering frames...' })
    const { blob, stats } = await renderInWorker(current, say, {
      artworkBlob,
      pcm,
      audioFeatures,
      lyricTimestamps,
      clipUrls,
      durationMs,
      ...format,
      bench: options,
    }, [pcm.left.buffer, pcm.right.buffer])
    stats.megabitsPerSecond = +(blob.size * 8 / (durationMs / 1000) / 1e6).toFixed(2)
    bench.last = stats
    logger.info(`[ShareVideo] ${stats.format}: ${stats.frames} frames in ${stats.seconds} s (${stats.speed}x realtime), ${stats.megabitsPerSecond} Mbps, ms per frame:`, stats.msPerFrame)

    let clipsNote = null
    if (includeClips && stats.clipsWanted === 0) clipsNote = 'This song has no video clips yet, so the video was made without them.'
    else if (includeClips && stats.clipsLoaded === 0) clipsNote = 'No video clips could be added, so the video was made without them.'
    else if (stats.clipsLoaded < stats.clipsWanted) clipsNote = "Some video clips couldn't be loaded."

    video = { blob, url: URL.createObjectURL(blob) }
    say({ status: 'publishing', stage: 'Publishing video to PLAiR...', progress: 1, etaSeconds: null, url: video.url, sizeBytes: blob.size })

    let publishedUrl = null
    let publishNote = null
    if (!bench.options && !api.token) {
      publishNote = 'Video rendered. Sign in to publish a hosted PLAiR link, or use Share Video to send the MP4 directly.'
    } else if (!bench.options) {
      try {
        const published = await api.uploadShareVideo({ trackId, title, artist: artistCredit(track) || 'Unknown Artist', blob, width: format.width, height: format.height })
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
    file: () => video.blob,
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
