import { logger } from './logger'
import { api } from './api'

const FADE_IN_MS = 1800
const FADE_OUT_MS = 2500
const DEFAULT_GAIN = 0.35
const MAX_CACHED = 2

function canPlayOpus() {
  try {
    const probe = document.createElement('audio')
    return typeof probe.canPlayType === 'function' && probe.canPlayType('audio/ogg; codecs="opus"') !== ''
  } catch {
    return false
  }
}

function attempt(fn) {
  try {
    fn()
    return true
  } catch {
    return false
  }
}

function clampGain(value) {
  const gain = Number(value)
  if (!Number.isFinite(gain)) return DEFAULT_GAIN
  return Math.max(0, Math.min(1, gain))
}

export class MusicBed {
  constructor(getEngine) {
    this.getEngine = getEngine
    this.buffers = new Map()
    this.loading = new Map()
    this.source = null
    this.gain = null
    this.level = 0
    this.paused = false
  }

  _engine() {
    const engine = this.getEngine?.()
    if (!engine?.context || !engine.musicGain || engine.context.state === 'closed') return null
    return engine
  }

  prepare(bed) {
    if (!bed?.id) return Promise.resolve(null)
    if (this.buffers.has(bed.id)) return Promise.resolve(this.buffers.get(bed.id))
    if (this.loading.has(bed.id)) return this.loading.get(bed.id)
    const engine = this._engine()
    if (!engine) return Promise.resolve(null)

    const urls = [canPlayOpus() ? bed.url_opus : null, bed.url].filter(Boolean)
    const promise = (async () => {
      for (const url of urls) {
        try {
          const data = await api.fetchMusicBed(url)
          const buffer = await engine.context.decodeAudioData(data)
          this.buffers.set(bed.id, buffer)
          while (this.buffers.size > MAX_CACHED) this.buffers.delete(this.buffers.keys().next().value)
          return buffer
        } catch (error) {
          logger.warn(`[MusicBed] ${bed.id} unavailable from ${url}:`, error?.message || error)
        }
      }
      return null
    })().finally(() => this.loading.delete(bed.id))
    this.loading.set(bed.id, promise)
    return promise
  }

  isReady(bed) {
    return !!bed?.id && this.buffers.has(bed.id)
  }

  start(bed, { fadeInMs = FADE_IN_MS } = {}) {
    const engine = this._engine()
    const buffer = bed?.id ? this.buffers.get(bed.id) : null
    if (!engine || !buffer) return false
    this.stop(0)

    const context = engine.context
    const source = context.createBufferSource()
    source.buffer = buffer
    const loopStart = Number(bed.loop_start_s) || 0
    const loopEnd = Math.min(Number(bed.loop_end_s) || 0, buffer.duration)
    if (loopEnd - loopStart > 1) {
      source.loop = true
      source.loopStart = loopStart
      source.loopEnd = loopEnd
    }
    const gain = context.createGain()
    gain.gain.value = 0
    source.connect(gain)
    gain.connect(engine.musicGain)
    source.onended = () => {
      if (this.source === source) {
        this.source = null
        this.gain = null
      }
      this._disconnect(source, gain)
    }

    this.level = clampGain(bed.gain)
    this.paused = false
    this.source = source
    this.gain = gain
    source.start()
    engine._rampGain(gain, 0, this.level, Math.max(0, fadeInMs) / 1000)
    return true
  }

  pause(fadeMs = 300) {
    const engine = this._engine()
    if (!engine || !this.gain || this.paused) return
    this.paused = true
    engine._rampGain(this.gain, this.gain.gain.value, 0, fadeMs / 1000)
  }

  resume(fadeMs = 600) {
    const engine = this._engine()
    if (!engine || !this.gain || !this.paused) return
    this.paused = false
    engine._rampGain(this.gain, this.gain.gain.value, this.level, fadeMs / 1000)
  }

  stop(fadeMs = FADE_OUT_MS) {
    const source = this.source
    const gain = this.gain
    this.source = null
    this.gain = null
    this.paused = false
    if (!source) return
    const engine = this._engine()
    if (engine && gain && fadeMs > 0) {
      engine._rampGain(gain, gain.gain.value, 0, fadeMs / 1000)
      if (!attempt(() => source.stop(engine.context.currentTime + fadeMs / 1000 + 0.05))) this._disconnect(source, gain)
      return
    }
    if (!attempt(() => source.stop())) this._disconnect(source, gain)
  }

  _disconnect(source, gain) {
    attempt(() => source.disconnect())
    attempt(() => gain.disconnect())
  }

  release(bedId) {
    if (bedId) this.buffers.delete(bedId)
  }

  destroy() {
    this.stop(0)
    this.buffers.clear()
    this.loading.clear()
  }
}
