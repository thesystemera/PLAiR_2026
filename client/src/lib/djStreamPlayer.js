const DJ_STREAM_MIME = 'audio/webm; codecs="opus"'

const DJ_STREAM_TIMING = {
  firstAudioTimeoutMs: 30000,
  stallTimeoutMs: 45000,
  reconnectStallTimeoutMs: 8000,
  endedGraceMs: 8000,
  playStartTimeoutMs: 15000,
  pausedDropMs: 30000,
  handoffHoldMs: 1200,
  cancelFadeMs: 50,
  idleFadeMs: 150,
  watchdogIntervalMs: 1000,
  trimBehindSec: 30,
  trimKeepSec: 10,
  maxQueuedBytes: 32 * 1024 * 1024,
  maxColorEntries: 400,
  maxStaged: 2,
  stagedTtlMs: 15 * 60 * 1000,
}

function defaultEnv() {
  const g = typeof window !== 'undefined' ? window : globalThis
  return {
    MediaSource: g.MediaSource || g.ManagedMediaSource || null,
    isManaged: !g.MediaSource && !!g.ManagedMediaSource,
    createObjectURL: (obj) => URL.createObjectURL(obj),
    revokeObjectURL: (url) => URL.revokeObjectURL(url),
    now: () => (typeof performance !== 'undefined' ? performance.now() : Date.now()),
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    clearTimeout: (id) => clearTimeout(id),
    setInterval: (fn, ms) => setInterval(fn, ms),
    clearInterval: (id) => clearInterval(id),
  }
}

const noopLog = { info() {}, warn() {}, error() {} }

export function decodeBase64Chunk(base64) {
  const binary = atob(base64)
  const length = binary.length
  const bytes = new Uint8Array(length)
  for (let i = 0; i < length; i++) bytes[i] = binary.charCodeAt(i)
  return bytes
}

function bufferedEnd(sourceBuffer) {
  try {
    const ranges = sourceBuffer.buffered
    return ranges.length ? ranges.end(ranges.length - 1) : 0
  } catch {
    return 0
  }
}

function bufferedStart(sourceBuffer) {
  try {
    const ranges = sourceBuffer.buffered
    return ranges.length ? ranges.start(0) : 0
  } catch {
    return 0
  }
}

export class DJStreamPlayer {
  constructor({ element, getOutput = null, onSpeakingChange = null, onBlocked = null, onUnsupported = null, log = noopLog, env = null, timing = null }) {
    this.element = element
    this.getOutput = getOutput
    this.onSpeakingChange = onSpeakingChange
    this.onBlocked = onBlocked
    this.onUnsupported = onUnsupported
    this.log = log
    this.env = { ...defaultEnv(), ...(env || {}) }
    this.timing = { ...DJ_STREAM_TIMING, ...(timing || {}) }

    this.enabled = false
    this.hold = false
    this.holdReasons = new Set()
    this.onStagedEnd = null
    this.onStreamEnd = null
    this.destroyed = false
    this.streams = new Map()
    this.queue = []
    this.cur = null
    this.gen = 0
    this.speaking = false
    this.output = null
    this.colorSchedule = []
    this.lastDisconnectAt = -Infinity
    this.busyUntil = 0
    this._busyTimer = null
    this._handoffTimer = null
    this._watchdog = null
    this._unsupportedLogged = false
    this.stats = { started: 0, finished: 0, cancelled: 0, failed: 0, appended: 0, droppedChunks: 0, trimmed: 0 }

    this.supported = !!this.env.MediaSource && this._typeSupported()
    if (this.env.isManaged && element) {
      try { element.disableRemotePlayback = true } catch { /* read-only on some engines */ }
    }

    this._onPlay = () => this._handlePlay()
    this._onPlaying = () => {
      if (this.cur && !this.cur.stopping) this.cur.playing = true
    }
    this._onPause = () => this._handlePause()
    this._onEnded = () => this._handleEnded()
    this._onError = () => this._handleElementError()
    this._onTimeUpdate = () => this._checkTalkEnd()
    if (element) {
      element.addEventListener('play', this._onPlay)
      element.addEventListener('playing', this._onPlaying)
      element.addEventListener('pause', this._onPause)
      element.addEventListener('ended', this._onEnded)
      element.addEventListener('error', this._onError)
      element.addEventListener('timeupdate', this._onTimeUpdate)
    }
  }

  _typeSupported() {
    try {
      const MS = this.env.MediaSource
      return typeof MS.isTypeSupported !== 'function' || MS.isTypeSupported(DJ_STREAM_MIME)
    } catch {
      return false
    }
  }

  setEnabled(enabled) {
    const next = !!enabled
    if (next === this.enabled) return
    this.enabled = next
    if (!next) this.cancelAll('disabled')
  }

  setHold(hold) {
    this.setHoldReason('mic', hold)
  }

  setHoldReason(reason, on) {
    if (on) this.holdReasons.add(reason)
    else this.holdReasons.delete(reason)
    this._applyHold(this.holdReasons.size > 0)
  }

  _applyHold(hold) {
    const next = !!hold
    if (next === this.hold) return
    this.hold = next
    const cur = this.cur
    if (!cur || cur.stopping || !cur.playRequested) return
    if (next) {
      if (!this.element.paused) this.element.pause()
      return
    }
    if (this.element.paused && !this.element.ended) this._startElement(cur.gen)
  }

  notifyConnection(connected) {
    if (!connected) this.lastDisconnectAt = this.env.now()
  }

  accepts(streamId) {
    return this.enabled && this.streams.has(streamId)
  }

  handleStart(streamId, { staged = false } = {}) {
    if (!this.enabled || !streamId || this.destroyed) return
    if (!this.supported) {
      if (!this._unsupportedLogged) {
        this._unsupportedLogged = true
        this.log.warn('[DJ Stream] MediaSource WebM/Opus not supported - DJ voice unavailable on this browser')
        this.onUnsupported?.()
      }
      return
    }
    if (this.streams.has(streamId)) return
    this.streams.set(streamId, {
      id: streamId,
      chunks: [],
      bytes: 0,
      ended: false,
      createdAt: this.env.now(),
      lastChunkAt: this.env.now(),
      nextChunkNum: 0,
      staged,
      complete: false,
      durationS: null,
      talkEndS: null,
    })
    if (staged) {
      this._trimStaged(streamId)
      return
    }
    this.queue.push(streamId)
    this._enforceQueueBudget()
    this._kick()
  }

  handleChunk(streamId, bytes, intensities = null, chunkNum = null) {
    if (!this.enabled) return
    const stream = this.streams.get(streamId)
    if (!stream || stream.ended || !bytes || !bytes.byteLength) return

    if (Number.isInteger(chunkNum)) {
      if (chunkNum < stream.nextChunkNum) return
      if (stream.nextChunkNum === 0 && chunkNum > 0) {
        this.log.warn(`[DJ Stream] ${streamId.slice(0, 8)} joined mid-stream without its header - skipping it`)
        this._dropStream(streamId, 'missing-header')
        return
      }
      if (chunkNum > stream.nextChunkNum) {
        this.stats.droppedChunks += chunkNum - stream.nextChunkNum
        this.log.warn(`[DJ Stream] ${streamId.slice(0, 8)} missed ${chunkNum - stream.nextChunkNum} chunk(s)`)
      }
      stream.nextChunkNum = chunkNum + 1
    }

    stream.chunks.push({ data: bytes, intensities })
    stream.bytes += bytes.byteLength
    stream.lastChunkAt = this.env.now()
    if (this.cur?.id === streamId) this._pump(this.cur.gen)
    else this._enforceQueueBudget()
  }

  _dropStream(streamId, reason) {
    if (this.cur?.id === streamId) {
      this._fail(reason)
      return
    }
    const stream = this.streams.get(streamId)
    this.streams.delete(streamId)
    this.queue = this.queue.filter(id => id !== streamId)
    if (stream?.staged) {
      this.onStagedEnd?.(streamId, { complete: false, durationS: null, reason })
      this.onStreamEnd?.(streamId, reason)
    }
  }

  _trimStaged(keepId) {
    const now = this.env.now()
    const staged = []
    this.streams.forEach(stream => {
      if (stream.staged && stream.id !== keepId) staged.push(stream)
    })
    staged.sort((a, b) => a.createdAt - b.createdAt)
    const excess = staged.length + 1 - this.timing.maxStaged
    staged.forEach((stream, index) => {
      if (index < excess || now - stream.createdAt > this.timing.stagedTtlMs) this._dropStream(stream.id, 'staged-replaced')
    })
  }

  handleEnd(streamId, { complete = true, durationS = null, talkEndS = null } = {}) {
    const stream = this.streams.get(streamId)
    if (!stream || stream.ended) return
    stream.ended = true
    stream.talkEndS = Number.isFinite(talkEndS) && talkEndS > 0 ? talkEndS : null
    if (this.cur?.id === streamId) {
      this.cur.talkEndS = stream.talkEndS
      this._checkTalkEnd()
    }
    if (stream.staged) {
      stream.complete = complete !== false && stream.chunks.length > 0
      stream.durationS = Number.isFinite(durationS) && durationS > 0 ? durationS : null
      if (!stream.complete) this.streams.delete(streamId)
      this.onStagedEnd?.(streamId, { complete: stream.complete, durationS: stream.durationS, reason: stream.complete ? null : 'incomplete' })
      return
    }
    if (this.cur?.id === streamId) this._pump(this.cur.gen)
  }

  isStagedReady(streamId) {
    const stream = streamId ? this.streams.get(streamId) : null
    return !!stream && stream.staged && stream.ended && stream.complete && stream.chunks.length > 0
  }

  stagedDuration(streamId) {
    const stream = streamId ? this.streams.get(streamId) : null
    return stream?.durationS ?? null
  }

  release(streamId) {
    if (!this.enabled || this.destroyed || !this.isStagedReady(streamId)) return false
    const stream = this.streams.get(streamId)
    stream.staged = false
    stream.lastChunkAt = this.env.now()
    this.queue.unshift(streamId)
    this._kick()
    return true
  }

  isCurrent(streamId) {
    return !!streamId && this.cur?.id === streamId
  }

  getProgress(streamId) {
    const cur = this.cur
    if (!cur || cur.id !== streamId || !this.element) return null
    const elementDuration = this.element.duration
    const duration = cur.eos && Number.isFinite(elementDuration) && elementDuration > 0 ? elementDuration : cur.durationS
    if (!duration) return null
    return { elapsed: Math.min(duration, this.element.currentTime || 0), duration }
  }

  getRemaining(streamId) {
    const progress = this.getProgress(streamId)
    return progress ? Math.max(0, progress.duration - progress.elapsed) : null
  }

  cancelStream(streamId, reason = 'cancel') {
    if (!streamId) return
    if (this.cur?.id === streamId) {
      this.stats.cancelled++
      const continuing = this.enabled && this.queue.some(id => id !== streamId && this.streams.has(id))
      this._stopCurrent(reason, this.timing.cancelFadeMs, { continuing })
      if (continuing) this._kick()
      return
    }
    if (this.streams.has(streamId)) this._dropStream(streamId, reason)
  }

  cancelAll(reason = 'cancel') {
    const hadWork = !!this.cur || this.queue.length > 0
    const stagedIds = []
    this.streams.forEach(stream => {
      if (stream.staged) stagedIds.push(stream.id)
    })
    this.queue = []
    this.streams.clear()
    stagedIds.forEach(id => this.onStreamEnd?.(id, reason))
    this._clearHandoff()
    if (this.cur) {
      this.stats.cancelled++
      this._stopCurrent(reason, this.timing.cancelFadeMs, { continuing: false })
    } else {
      this._setSpeaking(false)
      this._deactivateOutput(this.timing.cancelFadeMs)
    }
    if (hadWork) this.log.info(`[DJ Stream] Stopped (${reason}) - queue cleared`)
  }

  getCurrentTime() {
    return this.element ? this.element.currentTime : 0
  }

  getIntensitiesAt(time) {
    const schedule = this.colorSchedule
    for (let i = schedule.length - 1; i >= 0; i--) {
      if (schedule[i].time <= time) return schedule[i].intensities
    }
    return null
  }

  getState() {
    return {
      enabled: this.enabled,
      speaking: this.speaking,
      current: this.cur ? { id: this.cur.id, playing: this.cur.playing, eos: this.cur.eos, hasData: this.cur.hasData } : null,
      queued: this.queue.slice(),
      streams: this.streams.size,
      busy: this.env.now() < this.busyUntil,
      stats: { ...this.stats },
    }
  }

  destroy() {
    if (this.destroyed) return
    this.onStagedEnd = null
    this.onStreamEnd = null
    this.cancelAll('destroyed')
    this.destroyed = true
    this.enabled = false
    this._stopWatchdog()
    if (this._busyTimer) this.env.clearTimeout(this._busyTimer)
    this._busyTimer = null
    if (this.element) {
      this.element.removeEventListener('play', this._onPlay)
      this.element.removeEventListener('playing', this._onPlaying)
      this.element.removeEventListener('pause', this._onPause)
      this.element.removeEventListener('ended', this._onEnded)
      this.element.removeEventListener('error', this._onError)
      this.element.removeEventListener('timeupdate', this._onTimeUpdate)
    }
    this._detachElement()
    this.onSpeakingChange = null
  }

  _enforceQueueBudget() {
    let total = 0
    this.streams.forEach(stream => { if (!stream.staged) total += stream.bytes })
    while (total > this.timing.maxQueuedBytes && this.queue.length > 0) {
      const dropped = this.queue.shift()
      const stream = this.streams.get(dropped)
      if (stream) total -= stream.bytes
      this.streams.delete(dropped)
      this.log.warn(`[DJ Stream] Dropped queued stream ${dropped.slice(0, 8)} (buffer budget)`)
    }
  }

  _setSpeaking(speaking) {
    if (this.speaking === speaking) return
    this.speaking = speaking
    this.onSpeakingChange?.(speaking)
  }

  _deactivateOutput(fadeMs) {
    if (this.output) this.output.deactivate(fadeMs / 1000)
  }

  _clearHandoff() {
    if (this._handoffTimer) {
      this.env.clearTimeout(this._handoffTimer)
      this._handoffTimer = null
    }
  }

  _isStale(gen) {
    return !this.cur || this.cur.gen !== gen || this.destroyed
  }

  _kick() {
    if (this.cur || !this.enabled || this.destroyed || this.queue.length === 0) return
    const wait = this.busyUntil - this.env.now()
    if (wait > 0) {
      if (!this._busyTimer) {
        this._busyTimer = this.env.setTimeout(() => {
          this._busyTimer = null
          this._kick()
        }, wait)
      }
      return
    }

    let stream = null
    while (this.queue.length > 0 && !stream) {
      const candidate = this.streams.get(this.queue.shift()) || null
      stream = candidate && !candidate.staged ? candidate : null
    }
    if (!stream) {
      if (!this.cur) {
        this._setSpeaking(false)
        this._deactivateOutput(this.timing.idleFadeMs)
      }
      return
    }
    this._begin(stream)
  }

  _begin(stream) {
    const gen = ++this.gen
    const MS = this.env.MediaSource
    let mediaSource
    try {
      mediaSource = new MS()
    } catch (error) {
      this.log.error('[DJ Stream] MediaSource creation failed:', error)
      this.streams.delete(stream.id)
      this.stats.failed++
      this._kick()
      return
    }

    const url = this.env.createObjectURL(mediaSource)
    this.cur = {
      id: stream.id,
      durationS: stream.durationS || null,
      talkEndS: stream.talkEndS || null,
      talkOver: false,
      gen,
      mediaSource,
      sourceBuffer: null,
      url,
      urlRevoked: false,
      hasData: false,
      eos: false,
      eosAt: 0,
      playRequested: false,
      playRequestedAt: 0,
      playing: false,
      externallyPaused: false,
      stopping: false,
      lastTime: 0,
      lastProgressAt: this.env.now(),
      startedAt: this.env.now(),
    }
    this.colorSchedule = []
    this.stats.started++

    this.cur.outputReady = Promise.resolve()
      .then(() => (this.getOutput ? this.getOutput() : null))
      .then(output => {
        if (output) this.output = output
      })
      .catch(error => {
        this.log.warn('[DJ Stream] Output chain unavailable:', error)
      })

    mediaSource.addEventListener('sourceopen', () => this._handleSourceOpen(gen), { once: true })
    this.element.src = url
    this._startWatchdog()
  }

  _handleSourceOpen(gen) {
    if (this._isStale(gen)) return
    const cur = this.cur
    this._revokeUrl(cur)
    try {
      const sourceBuffer = cur.mediaSource.addSourceBuffer(DJ_STREAM_MIME)
      sourceBuffer.mode = 'sequence'
      sourceBuffer.addEventListener('updateend', () => this._handleUpdateEnd(gen))
      sourceBuffer.addEventListener('error', () => {
        if (this._isStale(gen)) return
        this._fail('sourcebuffer-error')
      })
      cur.sourceBuffer = sourceBuffer
    } catch (error) {
      this.log.error('[DJ Stream] SourceBuffer setup failed:', error)
      this._fail('sourcebuffer-setup')
      return
    }
    this._pump(gen)
  }

  _handleUpdateEnd(gen) {
    if (this._isStale(gen)) return
    const cur = this.cur
    if (!cur.hasData && bufferedEnd(cur.sourceBuffer) > 0) cur.hasData = true
    if (cur.hasData && !cur.playRequested) this._play(gen)
    this._pump(gen)
  }

  _pump(gen) {
    if (this._isStale(gen)) return
    const cur = this.cur
    const sourceBuffer = cur.sourceBuffer
    if (!sourceBuffer || sourceBuffer.updating || cur.eos) return
    if (cur.mediaSource.readyState !== 'open') return
    const stream = this.streams.get(cur.id)
    if (!stream) return

    const now = this.element.currentTime || 0
    const start = bufferedStart(sourceBuffer)
    if (now - start > this.timing.trimBehindSec) {
      try {
        sourceBuffer.remove(0, now - this.timing.trimKeepSec)
        this.stats.trimmed++
        return
      } catch (error) {
        this.log.warn('[DJ Stream] Buffer trim failed:', error)
      }
    }

    if (stream.chunks.length > 0) {
      const chunk = stream.chunks[0]
      const time = bufferedEnd(sourceBuffer)
      try {
        sourceBuffer.appendBuffer(chunk.data)
      } catch (error) {
        if (error?.name === 'QuotaExceededError') {
          this._handleQuota(gen)
          return
        }
        this.log.error('[DJ Stream] appendBuffer failed:', error)
        this._fail('append-error')
        return
      }
      stream.chunks.shift()
      stream.bytes -= chunk.data.byteLength
      this.stats.appended++
      if (chunk.intensities) {
        this.colorSchedule.push({ time, intensities: chunk.intensities })
        if (this.colorSchedule.length > this.timing.maxColorEntries) {
          this.colorSchedule.splice(0, this.colorSchedule.length - this.timing.maxColorEntries)
        }
      }
      return
    }

    if (stream.ended) {
      if (!cur.hasData) {
        this._finish('empty')
        return
      }
      cur.eos = true
      cur.eosAt = this.env.now()
      try {
        cur.mediaSource.endOfStream()
      } catch (error) {
        this.log.warn('[DJ Stream] endOfStream failed:', error)
      }
      if (!cur.playRequested) this._play(gen)
    }
  }

  _handleQuota(gen) {
    const cur = this.cur
    const sourceBuffer = cur.sourceBuffer
    const now = this.element.currentTime || 0
    const start = bufferedStart(sourceBuffer)
    if (now - 0.2 - start > 0.1) {
      try {
        sourceBuffer.remove(start, now - 0.2)
        this.stats.trimmed++
        return
      } catch (error) {
        this.log.warn('[DJ Stream] Quota trim failed:', error)
      }
    }
    this.env.setTimeout(() => this._pump(gen), 500)
  }

  _play(gen) {
    if (this._isStale(gen)) return
    const cur = this.cur
    cur.playRequested = true
    cur.playRequestedAt = this.env.now()
    cur.outputReady.then(() => this._startElement(gen))
  }

  _startElement(gen) {
    if (this._isStale(gen)) return
    const cur = this.cur
    cur.playRequestedAt = this.env.now()
    if (this.hold) {
      cur.externallyPaused = true
      cur.pausedAt = this.env.now()
      return
    }
    if (this.output) this.output.activate()

    let playPromise
    try {
      playPromise = this.element.play()
    } catch (error) {
      this._handlePlayError(gen, error)
      return
    }
    if (playPromise && typeof playPromise.catch === 'function') {
      playPromise.catch(error => this._handlePlayError(gen, error))
    }
  }

  _handlePlayError(gen, error) {
    if (this._isStale(gen)) return
    if (error?.name === 'AbortError') return
    if (error?.name === 'NotAllowedError') {
      if (this.onBlocked && !this.cur?.blockedOnce) {
        this.cur.blockedOnce = true
        this.log.warn('[DJ Stream] Playback blocked - waiting for the next tap')
        this.onBlocked(() => this._startElement(gen))
        return
      }
      this.log.warn('[DJ Stream] Playback blocked until the listener interacts with the page')
      this._fail('autoplay-blocked')
      return
    }
    this.log.error('[DJ Stream] Playback failed:', error)
    this._fail('play-error')
  }

  _handlePlay() {
    const cur = this.cur
    if (!cur || cur.stopping) return
    cur.externallyPaused = false
    cur.lastProgressAt = this.env.now()
    this._clearHandoff()
    if (this.output && !this.output.isActive) this.output.activate()
    if (!cur.talkOver) this._setSpeaking(true)
  }

  _checkTalkEnd() {
    const cur = this.cur
    if (!cur || cur.stopping || cur.talkOver || !cur.talkEndS || !this.element) return
    if ((this.element.currentTime || 0) < cur.talkEndS) return
    cur.talkOver = true
    this._setSpeaking(false)
  }

  _handlePause() {
    const cur = this.cur
    if (!cur || cur.stopping || this.element.ended) return
    if (!cur.playRequested) return
    cur.externallyPaused = true
    cur.pausedAt = this.env.now()
    cur.playing = false
    this._setSpeaking(false)
    this._deactivateOutput(this.timing.cancelFadeMs)
  }

  _handleEnded() {
    const cur = this.cur
    if (!cur || cur.stopping) return
    this._finish('ended')
  }

  _handleElementError() {
    const cur = this.cur
    if (!cur || cur.stopping || !this.element.error) return
    this.log.error('[DJ Stream] Media element error:', this.element.error)
    this._fail('media-error')
  }

  _fail(reason) {
    this.stats.failed++
    this.log.warn(`[DJ Stream] Stream ${this.cur?.id?.slice(0, 8) || ''} dropped (${reason})`)
    this._finish(reason)
  }

  _finish(reason) {
    if (!this.cur) return
    this.stats.finished++
    const continuing = this.enabled && this.queue.some(id => this.streams.has(id))
    const fadeMs = reason === 'ended' ? this.timing.idleFadeMs : this.timing.cancelFadeMs
    this._stopCurrent(reason, fadeMs, { continuing })
    if (continuing) this._kick()
  }

  _stopCurrent(reason, fadeMs, { continuing }) {
    const cur = this.cur
    if (!cur) return
    cur.stopping = true
    this.gen++
    this.cur = null
    this.streams.delete(cur.id)
    this.colorSchedule = []
    this._revokeUrl(cur)

    const wasAudible = cur.playing || (cur.playRequested && !this.element.paused && !this.element.ended)
    const needsFade = wasAudible && reason !== 'ended' && fadeMs > 0

    if (continuing && !needsFade) {
      this._clearHandoff()
      this._handoffTimer = this.env.setTimeout(() => {
        this._handoffTimer = null
        if (!this.cur?.playing || this.element.paused) {
          this._setSpeaking(false)
          this._deactivateOutput(this.timing.idleFadeMs)
        }
      }, this.timing.handoffHoldMs)
    } else {
      this._setSpeaking(false)
      this._deactivateOutput(fadeMs)
    }

    if (needsFade) {
      this.busyUntil = this.env.now() + fadeMs
      const detachGen = this.gen
      this.env.setTimeout(() => {
        if (this.gen === detachGen && !this.cur) this._detachElement()
      }, fadeMs)
    } else {
      this._detachElement()
    }

    if (!this.cur && this.queue.length === 0) this._stopWatchdog()
    if (reason !== 'ended') this.log.info(`[DJ Stream] ${cur.id.slice(0, 8)} stopped (${reason})`)
    this.onStreamEnd?.(cur.id, reason)
  }

  _revokeUrl(cur) {
    if (cur.url && !cur.urlRevoked) {
      cur.urlRevoked = true
      try { this.env.revokeObjectURL(cur.url) } catch { /* already revoked */ }
    }
  }

  _detachElement() {
    const element = this.element
    if (!element) return
    try {
      if (!element.paused) element.pause()
      if (element.getAttribute && element.getAttribute('src') !== null) {
        element.removeAttribute('src')
        element.load()
      }
    } catch (error) {
      this.log.warn('[DJ Stream] Element reset failed:', error)
    }
  }

  _startWatchdog() {
    if (this._watchdog) return
    this._watchdog = this.env.setInterval(() => this._checkHealth(), this.timing.watchdogIntervalMs)
  }

  _stopWatchdog() {
    if (!this._watchdog) return
    this.env.clearInterval(this._watchdog)
    this._watchdog = null
  }

  _checkHealth() {
    const cur = this.cur
    if (!cur) {
      if (this.queue.length === 0) this._stopWatchdog()
      else this._kick()
      return
    }
    const now = this.env.now()
    const stream = this.streams.get(cur.id)
    const element = this.element
    const t = this.timing

    if (element.currentTime !== cur.lastTime) {
      cur.lastTime = element.currentTime
      cur.lastProgressAt = now
    }
    if (!element.paused && !element.ended && element.readyState > 2 && cur.playRequested) cur.playing = true

    if (cur.externallyPaused) {
      if (!this.hold && now - cur.pausedAt > t.pausedDropMs) this._fail('paused')
      return
    }

    if (!cur.hasData) {
      if (now - cur.startedAt > t.firstAudioTimeoutMs && (!stream || now - stream.lastChunkAt > t.firstAudioTimeoutMs / 2)) {
        this._fail('no-audio')
      }
      return
    }

    if (cur.playRequested && !this.speaking && element.paused && now - cur.playRequestedAt > t.playStartTimeoutMs) {
      this._fail('play-timeout')
      return
    }

    if (stream && !stream.ended) {
      const starved = !cur.sourceBuffer || bufferedEnd(cur.sourceBuffer) - element.currentTime < 0.25
      const limit = this.lastDisconnectAt > stream.lastChunkAt ? t.reconnectStallTimeoutMs : t.stallTimeoutMs
      if (starved && now - stream.lastChunkAt > limit) {
        this.log.warn(`[DJ Stream] ${cur.id.slice(0, 8)} stalled ${Math.round((now - stream.lastChunkAt) / 1000)}s without audio - closing it`)
        this.handleEnd(cur.id)
      }
      return
    }

    if (cur.eos && now - cur.lastProgressAt > t.endedGraceMs && now - cur.eosAt > t.endedGraceMs) {
      this._finish('ended-timeout')
    }
  }
}
