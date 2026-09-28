import {logger} from './logger'
import {safeStorage} from './safeStorage'
import {AudioInteractionManager} from './audioInteractionManager'

const MASTER_GAIN_HEADROOM = 1.0
const STREAM_CANPLAY_TIMEOUT_MS = 20000
const SEEK_METADATA_TIMEOUT_MS = 8000
const SOURCE_RESOLVE_TIMEOUT_MS = 10000

function createSlot() {
  return {
    element: null,
    source: null,
    gain: null,
    mediaSource: null,
    sourceBuffer: null,
    chunks: [],
    fetchController: null,
    isComplete: false,
    trackId: null,
    metadata: null,
    blobUrl: null,
    isFadingOut: false,
    loaded: false,
    token: 0,
  }
}

function abortError() {
  const error = new Error('Aborted')
  error.name = 'AbortError'
  return error
}

function isAbortError(error) {
  return error?.name === 'AbortError'
}

function throwIfAborted(signal) {
  if (signal?.aborted) throw abortError()
}

export class AudioEngine {
  constructor() {
    this.context = null
    this.masterGain = null
    this.musicGain = null
    this.uiSoundsGain = null

    this.musicAnalyser = null

    this.currentSlot = null
    this.nextSlot = null

    this.isActiveDevice = false

    this.slots = {
      A: createSlot(),
      B: createSlot()
    }

    this.sfxElement = null
    this.sfxSource = null

    this.currentTrackId = null
    this.nextTrackId = null

    this.onCrossfadeStart = null
    this.onCrossfadeStateChange = null
    this.onHoldReached = null
    this.onChunkReceived = null
    this.onStreamComplete = null
    this.onBufferStatusChange = null

    // ===========================================
    // VINYL TURNTABLE SYSTEM
    // ===========================================
    // Simulates analog turntable behavior via unified pitch+gain control.
    // All transitions marry playbackRate (pitch) with gain (volume) ramps.
    //
    // Durations (ms):
    //   crossfadeDuration    - Default crossfade (overridden by backend hints)
    //   vinylRapidDuration   - Play/pause motor spin up/down
    //   vinylSeekDuration    - Seek ramp down/up
    //
    // Methods:
    //   _vinylAnimate(el, from, to, ms) - Unified pitch ramp
    //   _vinylSlowRampDown(el, ms)      - Eased ramp with wobble (crossfade out)
    //   _vinylSlowRampUp     - Incoming track spin-up during crossfade
    //   _vinylSlowRampDown   - Outgoing track slowdown with wobble
    //   _startVinylWobble    - Pitch wobble while waiting for next track
    //   _stopVinylWobble     - Stop wobble when next track ready
    //   _vinylAnimate        - Shared eased playbackRate animation
    //   playVinylScratch     - Needle scratch sound on track change
    //
    // Backend Integration:
    //   crossfade_hint.duration_ms drives both gain AND vinyl durations.
    //   Future: per-track hints for DJ-controlled turntable effects.
    // ===========================================
    this.crossfadeDuration = 3000
    this.vinylRapidDuration = 150
    this.vinylSeekDuration = 80
    this.timeUpdateHandler = null
    this.endedHandler = null
    this.timeUpdateElement = null
    this.crossfadeScheduled = false

    this.initialized = false
    this._initPromise = null
    this.isUnlockGestureSetup = false
    this.boundUnlockAudio = this.unlockAudio.bind(this)
    this.onUnlockedCallback = null
    this.pendingCleanups = []

    this._opChain = Promise.resolve()
    this._opGen = 0
    this._latestPreemptGen = 0
    this._pendingOps = 0
    this._op = null
    this._slotToken = 0
    this._pauseTimer = null
    this._vinylTimers = new Set()

    this.vinylWobbleActive = false
    this._hold = null
  }

  _scheduleAudioTick(callback) {
    const timer = setTimeout(() => {
      this._vinylTimers.delete(timer)
      callback()
    }, 16)
    this._vinylTimers.add(timer)
    return timer
  }

  setActiveDevice(isActive) {
    const wasActive = this.isActiveDevice
    this.isActiveDevice = isActive

    if (wasActive && !isActive) {
      logger.info('[AudioEngine] 🔕 Device deactivated - releasing audio')
      this._releaseAll()
    } else if (!wasActive && isActive) {
      logger.info('[AudioEngine] 🔔 Device activated - audio playback enabled')
    }
  }

  async setOutputDevice(deviceId) {
    if (!this.context) {
      logger.warn('[AudioEngine] Cannot set output device - context not initialized')
      return
    }

    if (typeof this.context.setSinkId !== 'function') {
      logger.warn('[AudioEngine] setSinkId not supported in this browser')
      return
    }

    try {
      await this.context.setSinkId(deviceId || '')
      logger.info(`[AudioEngine] 🔊 Output device set to: ${deviceId || 'default'}`)
    } catch (err) {
      logger.error('[AudioEngine] Failed to set output device:', err)
      throw err
    }
  }

  _rampGain(gainNode, from, to, durationSec) {
    const now = this.context.currentTime
    gainNode.gain.cancelScheduledValues(now)
    gainNode.gain.setValueAtTime(from, now)
    gainNode.gain.linearRampToValueAtTime(to, now + durationSec)
  }

  _enforceActiveDevice(operation = 'operation') {
    if (!this.isActiveDevice) {
      logger.warn(`[AudioEngine] ⛔ Blocked ${operation}: Not active device`)
      return false
    }
    return true
  }

  async initialize() {
    if (this.initialized) return
    if (!this._initPromise) {
      this._initPromise = this._initialize().finally(() => {
        this._initPromise = null
      })
    }
    return this._initPromise
  }

  async _initialize() {
    const AudioContext = window.AudioContext || window['webkitAudioContext']
    this.context = new AudioContext({
      latencyHint: 'playback'
    })

    AudioInteractionManager.audioContexts.add(this.context)

    const preferredSpeaker = safeStorage.get('preferredSpeakerId')
    if (preferredSpeaker) {
      await this.setOutputDevice(preferredSpeaker)
    }

    this._boundDeviceChangeHandler = async (e) => {
      const { deviceId, requestId } = e.detail
      try {
        await this.setOutputDevice(deviceId)
        if (requestId) {
          window.dispatchEvent(new CustomEvent('audio-output-device-response', {
            detail: { requestId, success: true }
          }))
        }
      } catch (err) {
        if (requestId) {
          window.dispatchEvent(new CustomEvent('audio-output-device-response', {
            detail: { requestId, success: false, error: err.message }
          }))
        }
      }
    }
    window.addEventListener('audio-output-device-change', this._boundDeviceChangeHandler)

    this.masterGain = this.context.createGain()
    this.musicGain = this.context.createGain()
    this.uiSoundsGain = this.context.createGain()

    this.musicAnalyser = this.context.createAnalyser()
    this.musicAnalyser.fftSize = 2048
    this.musicAnalyser.smoothingTimeConstant = 0.8

    this.musicGain.connect(this.musicAnalyser)
    this.musicAnalyser.connect(this.masterGain)

    this.uiSoundsGain.connect(this.masterGain)

    this.masterGain.connect(this.context.destination)

    this.masterGain.gain.value = MASTER_GAIN_HEADROOM
    this.musicGain.gain.value = 1.0
    this.uiSoundsGain.gain.value = 1.0

    this.slots.A.element = new Audio()
    this.slots.B.element = new Audio()

    this.slots.A.element.preload = 'metadata'
    this.slots.B.element.preload = 'metadata'

    this.slots.A.element.preservesPitch = false
    this.slots.B.element.preservesPitch = false

    this._attachBufferMonitors(this.slots.A)
    this._attachBufferMonitors(this.slots.B)

    this.slots.A.source = this.context.createMediaElementSource(this.slots.A.element)
    this.slots.B.source = this.context.createMediaElementSource(this.slots.B.element)

    this.slots.A.gain = this.context.createGain()
    this.slots.B.gain = this.context.createGain()

    this.slots.A.source.connect(this.slots.A.gain)
    this.slots.B.source.connect(this.slots.B.gain)

    this.slots.A.gain.connect(this.musicGain)
    this.slots.B.gain.connect(this.musicGain)

    this.slots.A.gain.gain.value = 1.0
    this.slots.B.gain.gain.value = 0.0

    this.sfxElement = new Audio()
    this.sfxElement.crossOrigin = 'anonymous'
    this.sfxSource = this.context.createMediaElementSource(this.sfxElement)
    this.sfxSource.connect(this.uiSoundsGain)

    this.currentSlot = this.slots.A
    this.nextSlot = this.slots.B

    this.initialized = true
  }

  _attachBufferMonitors(slot) {
    if (!slot || !slot.element) return

    const isCurrent = () => this.currentSlot === slot

    const handleWaiting = () => {
      if (!isCurrent() || slot.element.getAttribute('data-blob-url') === 'true') return
      this._reportStarvation(true)
    }

    const handlePlaying = () => {
      if (!isCurrent()) return
      this._reportStarvation(false)
    }

    const handleStalled = () => {
      if (!isCurrent() || slot.element.getAttribute('data-blob-url') === 'true') return
      this._reportStarvation(true)
    }

    slot.element.addEventListener('waiting', handleWaiting)
    slot.element.addEventListener('playing', handlePlaying)
    slot.element.addEventListener('stalled', handleStalled)
  }

  _reportStarvation(isStarving) {
    if (this.onBufferStatusChange) {
      this.onBufferStatusChange(isStarving)
    }
  }

  async playSfx(url, onEnded) {
    await this.ensureContext()

    if (!this.sfxElement.paused) {
      this.sfxElement.pause()
      this.sfxElement.currentTime = 0
    }

    this.sfxElement.src = url

    const endHandler = () => {
      this.sfxElement.removeEventListener('ended', endHandler)
      this.sfxElement.removeEventListener('error', errorHandler)
      if (onEnded) onEnded()
    }

    const errorHandler = (e) => {
      logger.error('[AudioEngine] SFX play failed', e)
      this.sfxElement.removeEventListener('ended', endHandler)
      this.sfxElement.removeEventListener('error', errorHandler)
      if (onEnded) onEnded()
    }

    this.sfxElement.addEventListener('ended', endHandler)
    this.sfxElement.addEventListener('error', errorHandler)

    try {
      await this.sfxElement.play()
    } catch (e) {
      errorHandler(e)
    }
  }

  stopSfx() {
    if (this.sfxElement) {
      this.sfxElement.pause()
      this.sfxElement.currentTime = 0
    }
  }

  async unlockAudio() {
    if (!this.context || this.context.state !== 'suspended') {
      this.removeUnlockGestures()
      return
    }
    try {
      await this.context.resume()
      if (this.context.state === 'running') {
        logger.info('AudioContext resumed successfully after user gesture.')
        this.removeUnlockGestures()
        if (this.onUnlockedCallback) {
          this.onUnlockedCallback()
          this.onUnlockedCallback = null
        }
      }
    } catch (e) {
      logger.error('Failed to resume AudioContext:', e)
    }
  }

  setupUnlockGesture() {
    if (this.isUnlockGestureSetup || !this.context || this.context.state !== 'suspended') {
      return
    }

    this.isUnlockGestureSetup = true
    logger.info('AudioEngine: Setting up user gesture listeners to unlock audio...')
    ;['click', 'mousedown', 'keydown', 'touchstart'].forEach(eventName => {
      document.body.addEventListener(eventName, this.boundUnlockAudio, { once: true, capture: true })
    })
  }

  removeUnlockGestures() {
    if (!this.isUnlockGestureSetup) return

    this.isUnlockGestureSetup = false
    logger.info('AudioEngine: Removing user gesture listeners.')
    ;['click', 'mousedown', 'keydown', 'touchstart'].forEach(eventName => {
      document.body.removeEventListener(eventName, this.boundUnlockAudio, { capture: true })
    })
  }

  async ensureContext() {
    if (!this.initialized) {
      await this.initialize()
    }

    if (this.context.state === 'suspended') {
      try {
        await this.context.resume()
        logger.info('[AudioEngine] Audio context resumed successfully')
      } catch {
        this.setupUnlockGesture()
        logger.info('[AudioEngine] Context suspended, waiting for user gesture')
      }
    }
  }

  getCurrentTrackId() {
    return this.currentTrackId
  }

  getNextTrackId() {
    return this.nextTrackId
  }

  hasCurrentSource() {
    const slot = this.currentSlot
    return !!slot && slot.loaded && !!slot.trackId && slot.trackId === this.currentTrackId
  }

  isNextReady(trackId) {
    const next = this.nextSlot
    return !!next && next.loaded && !next.isFadingOut && next.trackId === trackId && this.nextTrackId === trackId
  }

  isTransportBusy() {
    return this._pendingOps > 0
  }

  setCrossfadeHint(trackId, hint) {
    const slot = this.currentSlot
    if (slot && slot.trackId === trackId && slot.metadata) {
      slot.metadata.crossfade_hint = hint
    }
  }

  _enqueue(kind, trackId, fn) {
    const gen = ++this._opGen
    const preempt = kind === 'switch' || kind === 'reload'
    if (preempt) {
      this._latestPreemptGen = gen
      const inFlight = this._op
      const sameTrack = kind === 'switch' && inFlight?.trackId === trackId &&
        (inFlight.kind === 'preload' || inFlight.kind === 'switch')
      if (inFlight && !sameTrack) {
        inFlight.controller.abort()
      }
    }
    this._pendingOps++

    const run = async () => {
      try {
        const superseded = preempt ? this._latestPreemptGen !== gen : this._opGen !== gen
        if (superseded) return { status: 'superseded' }
        const controller = new AbortController()
        const op = { kind, trackId, controller, gen }
        this._op = op
        try {
          return await fn(controller.signal)
        } catch (error) {
          if (isAbortError(error) || controller.signal.aborted) {
            logger.info(`[Audio] 🚫 ${kind} superseded for ${trackId ? trackId.slice(0, 8) : 'none'}`)
            return { status: 'superseded' }
          }
          throw error
        } finally {
          if (this._op === op) this._op = null
        }
      } finally {
        this._pendingOps--
      }
    }

    const result = this._opChain.then(run, run)
    this._opChain = result.catch(() => {})
    return result
  }

  _abortAllOps() {
    this._opGen++
    this._latestPreemptGen = this._opGen
    if (this._op) this._op.controller.abort()
  }

  _awaitAbortable(promise, signal, onLateValue = null) {
    if (!signal) return promise
    if (signal.aborted) {
      if (onLateValue) promise.then(onLateValue, () => {})
      return Promise.reject(abortError())
    }
    return new Promise((resolve, reject) => {
      const onAbort = () => {
        if (onLateValue) promise.then(onLateValue, () => {})
        reject(abortError())
      }
      signal.addEventListener('abort', onAbort, { once: true })
      promise.then(
        (value) => {
          signal.removeEventListener('abort', onAbort)
          if (signal.aborted) {
            if (onLateValue) onLateValue(value)
            return
          }
          resolve(value)
        },
        (error) => {
          signal.removeEventListener('abort', onAbort)
          reject(error)
        }
      )
    })
  }

  _withTimeout(value, timeoutMs) {
    const promise = Promise.resolve(value)
    let timer = null
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Source lookup timed out')), timeoutMs)
    })
    return Promise.race([promise, timeout]).finally(() => clearTimeout(timer))
  }

  _cancelSlotCleanup(slot) {
    let hadPending = false
    this.pendingCleanups = this.pendingCleanups.filter(entry => {
      if (entry.slot !== slot) return true
      clearTimeout(entry.timer)
      hadPending = true
      return false
    })
    if (hadPending) {
      slot.isFadingOut = false
      if (this.onCrossfadeStateChange) this.onCrossfadeStateChange(false)
    }
  }

  _flushPendingCleanups() {
    const pending = this.pendingCleanups
    if (!pending.length) return
    this.pendingCleanups = []
    pending.forEach(entry => {
      clearTimeout(entry.timer)
      entry.slot.isFadingOut = false
      if (entry.slot !== this.currentSlot && entry.slot.token === entry.token) {
        this._resetSlot(entry.slot)
      }
    })
    if (this.onCrossfadeStateChange) this.onCrossfadeStateChange(false)
  }

  _resetSlot(slot) {
    if (!slot) return
    this._cancelSlotCleanup(slot)

    if (slot.fetchController) {
      slot.fetchController.abort()
      slot.fetchController = null
    }

    slot.mediaSource = null
    slot.sourceBuffer = null

    const element = slot.element
    if (element) {
      try {
        element.pause()
        element.playbackRate = 1.0
        if (element.getAttribute('src') !== null) {
          element.removeAttribute('src')
          element.load()
        }
      } catch (e) {
        logger.warn('[AudioEngine] Slot reset error:', e)
      }
    }

    if (slot.blobUrl) {
      URL.revokeObjectURL(slot.blobUrl)
      slot.blobUrl = null
    }

    if (slot.gain && this.context) {
      slot.gain.gain.cancelScheduledValues(0)
      slot.gain.gain.value = 0
    }

    slot.chunks = []
    slot.isComplete = false
    slot.isFadingOut = false
    slot.loaded = false
    slot.trackId = null
    slot.metadata = null
    slot.token = ++this._slotToken
  }

  _releaseAll() {
    this._hold = null
    this._abortAllOps()
    this._stopVinylWobble()
    if (this._pauseTimer) {
      clearTimeout(this._pauseTimer)
      this._pauseTimer = null
    }
    this._detachAutoCrossfade()
    const hadPending = this.pendingCleanups.length > 0
    this.pendingCleanups.forEach(entry => clearTimeout(entry.timer))
    this.pendingCleanups = []
    this._resetSlot(this.slots.A)
    this._resetSlot(this.slots.B)
    this.currentTrackId = null
    this.nextTrackId = null
    this.crossfadeScheduled = false
    if (hadPending && this.onCrossfadeStateChange) this.onCrossfadeStateChange(false)
  }

  _loadBlobToElement(element, url, slot, shouldRejectOnError = false, signal = null) {
    element.removeAttribute('crossOrigin')
    element.setAttribute('data-blob-url', 'true')
    element.preload = 'auto'
    element.src = url
    element.load()

    if (url.startsWith('blob:')) {
      slot.blobUrl = url
    }

    return new Promise((resolve, reject) => {
      const cleanup = () => {
        element.removeEventListener('canplay', onCanPlay)
        element.removeEventListener('error', onError)
        signal?.removeEventListener('abort', onAbort)
        clearTimeout(timeout)
      }

      const timeout = setTimeout(() => {
        cleanup()
        logger.warn('[Audio] Blob load timeout, proceeding')
        resolve()
      }, 8000)

      const onCanPlay = () => {
        cleanup()
        resolve()
      }

      const onError = (e) => {
        cleanup()
        if (shouldRejectOnError) {
          logger.error('[Audio] Blob load error:', e)
          reject(e)
        } else {
          resolve()
        }
      }

      const onAbort = () => {
        cleanup()
        reject(abortError())
      }

      element.addEventListener('canplay', onCanPlay, { once: true })
      element.addEventListener('error', onError, { once: true })
      if (signal) {
        if (signal.aborted) {
          onAbort()
          return
        }
        signal.addEventListener('abort', onAbort, { once: true })
      }
    })
  }

  _setupMediaSourceStream(element, url, trackId, slot, waitForCanPlay = true, signal = null) {
    element.crossOrigin = 'anonymous'
    element.removeAttribute('data-blob-url')
    element.preload = 'metadata'

    return new Promise((resolve, reject) => {
      const mediaSource = new MediaSource()
      slot.mediaSource = mediaSource
      const slotToken = slot.token

      let settled = false
      let timeout = null

      const cleanup = () => {
        element.removeEventListener('canplay', onCanPlay)
        element.removeEventListener('error', onError)
        signal?.removeEventListener('abort', onAbort)
        if (timeout) clearTimeout(timeout)
      }

      const finish = (error) => {
        if (settled) return
        settled = true
        cleanup()
        if (error) reject(error)
        else resolve()
      }

      const onCanPlay = () => {
        if (waitForCanPlay) finish()
      }
      const onError = (e) => {
        if (waitForCanPlay) finish(e?.target?.error || new Error('Media element error'))
        else finish()
      }
      const onAbort = () => finish(abortError())

      element.addEventListener('canplay', onCanPlay)
      element.addEventListener('error', onError)
      if (signal) {
        if (signal.aborted) {
          finish(abortError())
          return
        }
        signal.addEventListener('abort', onAbort, { once: true })
      }
      if (waitForCanPlay) {
        timeout = setTimeout(() => finish(new Error('Stream start timed out')), STREAM_CANPLAY_TIMEOUT_MS)
      }

      mediaSource.addEventListener('sourceopen', () => {
        try {
          if (slot.mediaSource !== mediaSource || slot.token !== slotToken || (settled && waitForCanPlay)) return

          const sourceBuffer = mediaSource.addSourceBuffer('audio/webm; codecs="opus"')
          slot.sourceBuffer = sourceBuffer

          const controller = new AbortController()
          slot.fetchController = controller

          this._streamToBuffer(url, sourceBuffer, slot.chunks, controller.signal, slot)
            .then(async (isComplete) => {
              if (slot.token !== slotToken) return
              slot.isComplete = isComplete

              while (sourceBuffer.updating) {
                await new Promise(resolve => sourceBuffer.addEventListener('updateend', resolve, { once: true }))
              }

              if (isComplete && mediaSource.readyState === 'open') {
                mediaSource.endOfStream()
              }

              if (isComplete && this.onStreamComplete) {
                this.onStreamComplete(trackId, slot.chunks)
              }
            })
            .catch((err) => {
              if (err.name !== 'AbortError') {
                logger.error(`[Audio] Stream error:`, err)
              }
            })

          if (!waitForCanPlay) {
            finish()
          } else if (element.readyState >= 2) {
            finish()
          }
        } catch (err) {
          logger.error('[Audio] MediaSource setup error:', err)
          finish(err)
        }
      })

      mediaSource.addEventListener('error', (e) => finish(e))
      const blobUrl = URL.createObjectURL(mediaSource)
      slot.blobUrl = blobUrl
      element.src = blobUrl
    })
  }

  async _loadIntoSlot(slot, trackId, source, metadata, signal, { waitForCanPlay = true, rejectOnError = true } = {}) {
    this._resetSlot(slot)
    const token = slot.token
    slot.trackId = trackId
    slot.metadata = metadata || {}
    if (slot === this.nextSlot) this.nextTrackId = trackId

    try {
      if (source.isBlobUrl) {
        await this._loadBlobToElement(slot.element, source.url, slot, rejectOnError, signal)
      } else {
        await this._setupMediaSourceStream(slot.element, source.url, trackId, slot, waitForCanPlay, signal)
      }
      throwIfAborted(signal)
      if (slot.token !== token) throw abortError()
      slot.loaded = true
    } catch (error) {
      if (slot.token === token) {
        this._resetSlot(slot)
        if (this.nextSlot === slot && this.nextTrackId === trackId) this.nextTrackId = null
      }
      throw error
    }
  }

  _detachAutoCrossfade() {
    if (this.timeUpdateElement) {
      if (this.timeUpdateHandler) this.timeUpdateElement.removeEventListener('timeupdate', this.timeUpdateHandler)
      if (this.endedHandler) this.timeUpdateElement.removeEventListener('ended', this.endedHandler)
    }
    this.timeUpdateElement = null
  }

  _crossfadePlan(currentTrack, metadataDurationMs) {
    const crossfadeHint = currentTrack?.crossfade_hint
    let startFromEnd = this.crossfadeDuration
    let duration = this.crossfadeDuration
    if (crossfadeHint) {
      startFromEnd = metadataDurationMs - crossfadeHint.optimal_start_ms
      duration = crossfadeHint.duration_ms
    }
    return { crossfadeHint, startFromEnd, duration }
  }

  _holdOnCurrent() {
    return !!this._hold && this._hold.trackId === this.currentTrackId
  }

  _reachHold() {
    const hold = this._hold
    if (!hold || hold.reached || hold.trackId !== this.currentTrackId) return
    hold.reached = true
    if (this.vinylWobbleActive) this._stopVinylWobble()
    logger.info(`[Audio] 🎙️ Holding after ${hold.trackId.slice(0, 8)} for a talk break`)
    this.onHoldReached?.(hold.trackId)
  }

  talkUpPointMs(maxMs = 12000, minMs = 800) {
    const slot = this.currentSlot
    const durationMs = slot?.metadata?.duration_ms || 0
    if (!durationMs) return minMs
    const { startFromEnd } = this._crossfadePlan(slot.metadata, durationMs)
    return Math.max(minMs, Math.min(maxMs, Number.isFinite(startFromEnd) ? startFromEnd : minMs))
  }

  armHold(trackId, { talkUpMs = 1000, minLeadMs = 1500 } = {}) {
    if (!this.isActiveDevice || !trackId || this.currentTrackId !== trackId || !this.hasCurrentSource()) return false
    if (this._hold?.trackId === trackId) return true
    if (this.crossfadeScheduled || this.isTransportBusy()) return false
    const element = this.currentSlot.element
    const durationMs = this.currentSlot.metadata?.duration_ms ||
      (Number.isFinite(element?.duration) ? element.duration * 1000 : 0)
    if (!element || !durationMs || element.ended) return false
    const remainingMs = durationMs - element.currentTime * 1000
    if (remainingMs < talkUpMs + minLeadMs) return false
    this._hold = { trackId, talkUpMs, reached: false }
    if (this.vinylWobbleActive) this._stopVinylWobble()
    return true
  }

  clearHold() {
    this._hold = null
  }

  isHolding(trackId = null) {
    return !!this._hold && (!trackId || this._hold.trackId === trackId)
  }

  isHoldReached(trackId = null) {
    return !!this._hold?.reached && (!trackId || this._hold.trackId === trackId)
  }

  resumeFromHold({ fadeMs = 1500 } = {}) {
    const hold = this._hold
    this._hold = null
    if (!hold || !this.isActiveDevice || this.currentTrackId !== hold.trackId || this.crossfadeScheduled) return false
    if (!this.nextTrackId || !this.nextSlot.loaded) {
      this._checkEndedAdvance()
      return false
    }
    const durationMs = Math.max(50, fadeMs)
    this._startAutoCrossfade(durationMs, {
      planned_start_from_end_ms: 0,
      planned_duration_ms: durationMs,
      actual_start_from_end_ms: 0,
      actual_duration_ms: durationMs,
      used_backend_hint: false,
      backend_confidence: null,
      talk_break: true
    })
    return true
  }

  _setupAutoCrossfade() {
    this._detachAutoCrossfade()

    this.crossfadeScheduled = false
    this.nextTrackWaitingLogged = false
    this.timeUpdateHandler = () => {
      const element = this.currentSlot.element
      const currentTrack = this.currentSlot.metadata
      const metadataDurationMs = currentTrack?.duration_ms || 0

      if (this._holdOnCurrent()) {
        if (!this._hold.reached && metadataDurationMs &&
          metadataDurationMs - element.currentTime * 1000 <= this._hold.talkUpMs) {
          this._reachHold()
        }
        return
      }

      if (!metadataDurationMs || this.crossfadeScheduled || !this.nextTrackId || this.isTransportBusy()) {
        return
      }

      const { crossfadeHint, startFromEnd, duration } = this._crossfadePlan(currentTrack, metadataDurationMs)
      const currentTimeMs = element.currentTime * 1000
      const timeRemaining = metadataDurationMs - currentTimeMs

      if (!this.nextSlot.loaded || this.nextSlot.element.readyState < 2) {
        if (!this.nextTrackWaitingLogged) {
          logger.warn(`[Audio] ⏳ Waiting for next track to load (readyState: ${this.nextSlot.element.readyState})`)
          this.nextTrackWaitingLogged = true
        }

        if (timeRemaining <= startFromEnd + 2000 && !this.vinylWobbleActive) {
          this.vinylWobbleActive = true
          this._startVinylWobble(element)
          logger.info('[Audio] 🎚️ Vinyl wobble started - next track not ready')
        }
        return
      }

      if (this.vinylWobbleActive) {
        this._stopVinylWobble()
        logger.info('[Audio] 🎚️ Vinyl wobble stopped - next track ready')
      }

      if (timeRemaining <= startFromEnd) {
        this._startAutoCrossfade(duration, {
          planned_start_from_end_ms: startFromEnd,
          planned_duration_ms: duration,
          actual_start_from_end_ms: timeRemaining,
          actual_duration_ms: duration,
          used_backend_hint: !!crossfadeHint,
          backend_confidence: crossfadeHint?.confidence || null
        })
      }
    }

    this.endedHandler = () => {
      if (this._holdOnCurrent()) {
        this._reachHold()
        return
      }
      if (this.crossfadeScheduled || !this.nextTrackId || this.isTransportBusy()) return
      if (!this.nextSlot.loaded) return
      this._startAutoCrossfade(50, {
        planned_start_from_end_ms: 0,
        planned_duration_ms: 50,
        actual_start_from_end_ms: 0,
        actual_duration_ms: 50,
        used_backend_hint: false,
        backend_confidence: null
      })
    }

    this.timeUpdateElement = this.currentSlot.element
    this.timeUpdateElement.addEventListener('timeupdate', this.timeUpdateHandler)
    this.timeUpdateElement.addEventListener('ended', this.endedHandler)
  }

  _startAutoCrossfade(durationMs, info) {
    this.crossfadeScheduled = true
    const oldTrackId = this.currentTrackId
    const newTrackId = this.nextTrackId

    void this._enqueue('auto', newTrackId, async () => {
      if (!this.isActiveDevice || this.currentTrackId !== oldTrackId || this.nextTrackId !== newTrackId || !this.nextSlot.loaded) {
        this.crossfadeScheduled = false
        return { status: 'skipped' }
      }

      logger.info(`[Audio] 🔀 AUTO-CROSSFADE: ${oldTrackId} -> ${newTrackId} (${durationMs}ms)`)
      const playing = this._swapToNext(durationMs, true, 'natural')

      if (this.onCrossfadeStart) {
        this.onCrossfadeStart(oldTrackId, newTrackId, durationMs, info)
      } else {
        logger.error(`[Audio] ❌ CALLBACK MISSING! onCrossfadeStart is null`)
      }

      await playing
      return { status: 'crossfaded' }
    }).then(result => {
      if (result?.status === 'superseded') this.crossfadeScheduled = false
    }).catch(err => {
      this.crossfadeScheduled = false
      logger.error('[Audio] Auto-crossfade failed:', err)
    })
  }

  _checkEndedAdvance() {
    const element = this.currentSlot?.element
    if (element?.ended && this.endedHandler) this.endedHandler()
  }

  switchTo(trackId, options = {}) {
    const {
      resolveSource,
      metadata = {},
      fadeTimeMs = 50,
      preloadedFadeMs = 50,
      shouldPlay = false,
      scratch = true
    } = options

    if (!this._enforceActiveDevice('switchTo')) return Promise.resolve({ status: 'inactive' })

    return this._enqueue('switch', trackId, async (signal) => {
      await this.ensureContext()
      throwIfAborted(signal)

      if (this.currentTrackId === trackId && this.hasCurrentSource()) {
        return { status: 'current' }
      }

      if (this.isNextReady(trackId)) {
        logger.info(`[Audio] ⏭️ SWITCH to preloaded ${trackId.slice(0, 8)}`)
        await this._swapToNext(preloadedFadeMs, shouldPlay, 'user')
        return { status: 'crossfaded' }
      }

      const discard = (value) => {
        if (value?.isBlobUrl && typeof value.url === 'string' && value.url.startsWith('blob:')) {
          URL.revokeObjectURL(value.url)
        }
      }
      const source = await this._awaitAbortable(this._withTimeout(resolveSource?.(), SOURCE_RESOLVE_TIMEOUT_MS), signal, discard)
      if (!source) return { status: 'unavailable' }

      const slotName = this.nextSlot === this.slots.A ? 'A' : 'B'
      logger.info(`[Audio] 📀 LOAD & SWAP: ${trackId} into Slot ${slotName}`)

      await this._loadIntoSlot(this.nextSlot, trackId, source, { ...metadata, bitrate: source.bitrate ?? metadata.bitrate, fromCache: source.fromCache ?? metadata.fromCache }, signal, { waitForCanPlay: true })
      throwIfAborted(signal)

      if (scratch) this.playVinylScratch()
      await this._swapToNext(fadeTimeMs, shouldPlay, 'user')
      logger.info(`[Audio] ✅ SWITCHED: ${trackId}`)
      return { status: 'loaded' }
    })
  }

  reloadCurrent(trackId, source, options = {}) {
    const { metadata = {}, positionSeconds = 0, shouldPlay = false } = options

    if (!this._enforceActiveDevice('reloadCurrent')) {
      if (source?.isBlobUrl && source.url?.startsWith('blob:')) URL.revokeObjectURL(source.url)
      return Promise.resolve({ status: 'inactive' })
    }

    return this._enqueue('reload', trackId, async (signal) => {
      await this.ensureContext()
      throwIfAborted(signal)

      logger.info(`[Audio] 💿 RE-LOAD: ${trackId}`)
      await this._loadIntoSlot(this.nextSlot, trackId, source, metadata, signal, { waitForCanPlay: true })
      throwIfAborted(signal)

      await this._swapToNext(0, false, 'natural')
      if (positionSeconds > 0) {
        await this.seek(positionSeconds)
      }
      throwIfAborted(signal)
      if (shouldPlay) await this.play()
      return { status: 'loaded' }
    })
  }

  _discardUnusedBlobUrl(url, isBlobUrl) {
    if (isBlobUrl && typeof url === 'string' && url.startsWith('blob:')) {
      URL.revokeObjectURL(url)
    }
  }

  preloadTrack(trackId, options = {}) {
    const { resolveSource, metadata = {} } = options

    if (!this._enforceActiveDevice('preloadTrack') || this.nextTrackId === trackId || this.currentTrackId === trackId) {
      return Promise.resolve({ status: 'skipped' })
    }

    return this._enqueue('preload', trackId, async (signal) => {
      if (!this.isActiveDevice) return { status: 'inactive' }
      const next = this.nextSlot
      if (next.isFadingOut) {
        logger.warn(`[Audio] ✋ Skipping preload for ${trackId}: Target slot is busy fading out`)
        return { status: 'busy' }
      }
      if (this.currentTrackId === trackId || (this.nextTrackId === trackId && next.loaded)) {
        return { status: 'skipped' }
      }

      await this.ensureContext()
      throwIfAborted(signal)

      const discard = (value) => {
        if (value?.isBlobUrl && typeof value.url === 'string' && value.url.startsWith('blob:')) {
          URL.revokeObjectURL(value.url)
        }
      }
      const source = await this._awaitAbortable(this._withTimeout(resolveSource?.(), SOURCE_RESOLVE_TIMEOUT_MS), signal, discard)
      if (!source) return { status: 'unavailable' }

      const nextSlotName = next === this.slots.A ? 'A' : 'B'
      logger.info(`[Audio] ⚡ PRELOAD: ${trackId} into Slot ${nextSlotName}`)

      await this._loadIntoSlot(next, trackId, source, { ...metadata, bitrate: source.bitrate, fromCache: source.fromCache }, signal, { waitForCanPlay: false, rejectOnError: false })

      logger.info(`[Audio] 🔋 PRELOAD COMPLETE: ${trackId}`)
      setTimeout(() => this._checkEndedAdvance(), 0)
      return { status: 'preloaded' }
    })
  }

  async _streamToBuffer(url, sourceBuffer, chunkArray, signal, slot) {
    const CHUNK_SIZE = 2 * 1024 * 1024
    const BUFFER_LOW_WATERMARK = 10
    const BUFFER_HIGH_WATERMARK = 30
    const INITIAL_BUFFER_TARGET = 2
    const CHECK_INTERVAL = 1000
    const PRELOAD_TARGET = 30

    const headResponse = await fetch(url, { method: 'HEAD', signal })
    const contentLength = parseInt(headResponse.headers.get('content-length') || '0')

    let bytesDownloaded = 0
    let isStreamComplete = false
    let hasInitialBuffer = false
    let isBuffering = true

    while (bytesDownloaded < contentLength && !signal.aborted) {
      if (!hasInitialBuffer) {
        try {
          if (sourceBuffer.buffered.length > 0) {
            const bufferedEnd = sourceBuffer.buffered.end(0)
            if (bufferedEnd >= INITIAL_BUFFER_TARGET) {
              hasInitialBuffer = true
            }
          }
        } catch {
          break
        }
      }

      if (hasInitialBuffer) {
        try {
          if (sourceBuffer.buffered.length > 0) {
            const bufferedEnd = sourceBuffer.buffered.end(0)
            const currentTime = slot.element.currentTime
            const isActivelyPlaying = slot === this.currentSlot && currentTime > 0

            let bufferAhead = isActivelyPlaying ? (bufferedEnd - currentTime) : bufferedEnd

            if (isActivelyPlaying) {
              if (isBuffering) {
                if (bufferAhead >= BUFFER_HIGH_WATERMARK) {
                  isBuffering = false
                  await new Promise(resolve => setTimeout(resolve, CHECK_INTERVAL))
                  if (signal.aborted) break
                  continue
                }
              } else {
                if (bufferAhead < BUFFER_LOW_WATERMARK) {
                  isBuffering = true
                } else {
                  await new Promise(resolve => setTimeout(resolve, CHECK_INTERVAL))
                  if (signal.aborted) break
                  continue
                }
              }
            } else {
              if (bufferAhead >= PRELOAD_TARGET) {
                isBuffering = false
                await new Promise(resolve => setTimeout(resolve, CHECK_INTERVAL))
                if (signal.aborted) break
                continue
              } else {
                isBuffering = true
              }
            }
          } else if (!isBuffering) {
            isBuffering = true
          }
        } catch {
          break
        }
      }

      const start = bytesDownloaded
      const end = Math.min(bytesDownloaded + CHUNK_SIZE - 1, contentLength - 1)

      const response = await fetch(url, {
        signal,
        headers: { 'Range': `bytes=${start}-${end}` }
      })

      if (!response.ok && response.status !== 206) throw new Error('Network error')

      const chunk = await response.arrayBuffer()
      const value = new Uint8Array(chunk)
      bytesDownloaded += value.byteLength

      try {
        if (slot.sourceBuffer !== sourceBuffer) break

        while (sourceBuffer.updating) {
          await new Promise(resolve => sourceBuffer.addEventListener('updateend', resolve, { once: true }))
        }

        if (slot.sourceBuffer !== sourceBuffer) break

        sourceBuffer.appendBuffer(value)
        chunkArray.push(value)

        if (this.onChunkReceived) {
          this.onChunkReceived(slot.trackId, value)
        }
      } catch {
        break
      }

      if (bytesDownloaded >= contentLength) {
        isStreamComplete = true
        break
      }
    }

    return isStreamComplete
  }

  async play() {
    if (!this._enforceActiveDevice('play')) return
    if (this.isHoldReached(this.currentTrackId)) return

    await this.ensureContext()

    if (this._pauseTimer) {
      clearTimeout(this._pauseTimer)
      this._pauseTimer = null
    }

    if (this.currentSlot.loaded && this.currentSlot.element.getAttribute('src')) {
      const element = this.currentSlot.element
      const rampSec = this.vinylRapidDuration / 1000

      this._rampGain(this.currentSlot.gain, 0, 1, rampSec)

      element.playbackRate = 0.1
      this._vinylAnimate(element, 0.1, 1.0, this.vinylRapidDuration)

      try {
        await element.play()
      } catch (err) {
        if (err.name !== 'AbortError') logger.error('[AudioEngine] Play failed:', err)
      }
    }
  }

  pause() {
    if (!this._enforceActiveDevice('pause')) return

    if (this.currentSlot?.element && this.context) {
      const slot = this.currentSlot
      const element = slot.element
      const rampSec = this.vinylRapidDuration / 1000

      this._rampGain(slot.gain, slot.gain.gain.value, 0, rampSec)

      this._vinylAnimate(element, 1.0, 0.1, this.vinylRapidDuration)

      if (this._pauseTimer) clearTimeout(this._pauseTimer)
      this._pauseTimer = setTimeout(() => {
        this._pauseTimer = null
        element.pause()
        element.playbackRate = 1.0
        slot.gain.gain.cancelScheduledValues(0)
        slot.gain.gain.value = 0
      }, this.vinylRapidDuration + 10)
    }
  }

  isPlaying() {
    const element = this.currentSlot?.element
    return !!element && !element.paused && !this._pauseTimer
  }

  _vinylSlowRampDown(element, durationMs) {
    const startTime = performance.now()
    const startRate = element.playbackRate
    const animate = () => {
      const elapsed = performance.now() - startTime
      const progress = Math.min(elapsed / durationMs, 1)
      const eased = Math.pow(progress, 1.5)
      const wobble = Math.sin(progress * Math.PI * 3) * 0.03 * (1 - progress)
      element.playbackRate = Math.max(0.1, startRate * (1 - eased * 0.85) + wobble)
      if (progress < 1 && !element.paused) {
        this._scheduleAudioTick(animate)
      }
    }
    this._scheduleAudioTick(animate)
  }

  _vinylAnimate(element, from, to, durationMs) {
    const startTime = performance.now()
    const animate = () => {
      const elapsed = performance.now() - startTime
      const progress = Math.min(elapsed / durationMs, 1)
      const eased = 1 - Math.pow(1 - progress, 2)
      element.playbackRate = from + (to - from) * eased
      if (progress < 1) {
        this._scheduleAudioTick(animate)
      }
    }
    this._scheduleAudioTick(animate)
  }

  _startVinylWobble(element) {
    if (!element) return

    this.vinylWobbleActive = true
    let phase = 0

    const wobble = () => {
      if (!this.vinylWobbleActive) return

      phase += 0.15
      const wobbleAmount = 0.03 + Math.sin(phase * 2) * 0.02
      element.playbackRate = 1.0 - wobbleAmount + Math.sin(phase) * wobbleAmount

      this._scheduleAudioTick(wobble)
    }
    this._scheduleAudioTick(wobble)
  }

  _stopVinylWobble() {
    this.vinylWobbleActive = false
    if (this.currentSlot?.element) {
      this.currentSlot.element.playbackRate = 1.0
    }
  }

  playVinylScratch() {
    const scratchUrl = '/audio/interface/spotify_track_change.mp3'
    this.playSfx(scratchUrl)
  }

  stopImmediately() {
    this._stopVinylWobble()

    if (this.currentSlot?.element && this.context) {
      this.currentSlot.gain.gain.cancelScheduledValues(0)
      this.currentSlot.gain.gain.value = 0
      this.currentSlot.element.pause()
    }
  }

  async handleOfflineTransition(getCurrentTrackId, getCachedVersion) {
    logger.info('[AudioEngine] 🔌 Handling offline transition')

    if (this.nextSlot?.fetchController) {
      this.nextSlot.fetchController.abort()
      logger.info('[AudioEngine] Aborted next slot fetch')
    }

    const trackId = getCurrentTrackId()
    if (!trackId || trackId !== this.currentTrackId) return { switched: false }

    const cached = await getCachedVersion(trackId)
    if (!cached) {
      logger.info('[AudioEngine] No cached version available - buffer will starve')
      return { switched: false }
    }

    const currentElement = this.getCurrentElement()
    const currentTime = currentElement?.currentTime || 0
    const wasPlaying = currentElement && !currentElement.paused

    const blobUrl = URL.createObjectURL(cached.audioBlob)

    const result = await this.reloadCurrent(trackId, { url: blobUrl, isBlobUrl: true, bitrate: cached.bitrate, fromCache: true }, {
      metadata: { ...(cached.metadata || {}), bitrate: cached.bitrate, fromCache: true },
      positionSeconds: currentTime,
      shouldPlay: wasPlaying
    })

    if (result?.status !== 'loaded') return { switched: false }

    logger.info(`[AudioEngine] ✅ Switched to cached ${cached.bitrate} version`)
    return { switched: true, bitrate: cached.bitrate }
  }

  _scheduleSlotCleanup(slot, actualFadeTime) {
    const token = slot.token
    const entry = { slot, token, timer: null }
    entry.timer = setTimeout(() => {
      this.pendingCleanups = this.pendingCleanups.filter(e => e !== entry)
      slot.isFadingOut = false

      if (slot !== this.currentSlot && slot.token === token) {
        this._resetSlot(slot)
      }

      if (this.onCrossfadeStateChange) {
        this.onCrossfadeStateChange(false)
      }
    }, actualFadeTime * 1000 + 100)
    this.pendingCleanups.push(entry)
  }

  async _swapToNext(fadeTimeMs = 50, shouldPlay = true, transitionType = 'natural') {
    this._hold = null
    this._flushPendingCleanups()
    this._stopVinylWobble()

    const current = this.currentSlot
    const next = this.nextSlot
    const actualFadeTime = fadeTimeMs === 0 ? 0.015 : fadeTimeMs / 1000

    if (this.onCrossfadeStateChange) {
      this.onCrossfadeStateChange(true)
    }

    current.isFadingOut = true
    if (current.gain) {
      this._rampGain(current.gain, current.gain.gain.value, 0, actualFadeTime)
    }

    if (transitionType === 'user' && current.element && !current.element.paused) {
      this._vinylSlowRampDown(current.element, actualFadeTime * 1000)
    }

    this.currentSlot = next
    this.nextSlot = current
    this.currentTrackId = next.trackId
    this.nextTrackId = null

    this._setupAutoCrossfade()
    this._scheduleSlotCleanup(current, actualFadeTime)

    logger.info(`[Audio] 🔄 SWAPPED SLOTS: New Current is Slot ${this.currentSlot === this.slots.A ? 'A' : 'B'} (${this.currentTrackId?.slice(0, 8)})`)

    if (shouldPlay) {
      this._rampGain(next.gain, 0, 1, actualFadeTime)
      if (transitionType === 'user') {
        next.element.playbackRate = 0.85
        this._vinylAnimate(next.element, 0.85, 1.0, actualFadeTime * 1000 * 0.5)
      }
      try {
        await next.element.play()
      } catch (err) {
        if (err?.name !== 'AbortError') logger.warn('[AudioEngine] Play after swap blocked:', err?.message || err)
      }
    } else {
      this._rampGain(next.gain, 0, 1, 0)
    }
  }

  seek(timeSeconds) {
    if (!this._enforceActiveDevice('seek')) return Promise.resolve()

    if (!this.currentSlot?.element || !Number.isFinite(timeSeconds)) return Promise.resolve()

    const element = this.currentSlot.element
    const wasPlaying = !element.paused

    if (element.readyState < 1) {
      return new Promise((resolve) => {
        const done = () => {
          clearTimeout(timer)
          element.removeEventListener('loadedmetadata', onLoadedMetadata)
          resolve()
        }
        const onLoadedMetadata = () => {
          try {
            element.currentTime = timeSeconds
          } catch (err) {
            logger.error('[AudioEngine] Seek failed:', err)
          }
          done()
        }
        const timer = setTimeout(done, SEEK_METADATA_TIMEOUT_MS)
        element.addEventListener('loadedmetadata', onLoadedMetadata, { once: true })
      })
    }

    this._vinylAnimate(element, 1.0, 0.3, this.vinylSeekDuration)

    return new Promise((resolve) => {
      setTimeout(() => {
        try {
          element.currentTime = timeSeconds
        } catch (err) {
          logger.error('[AudioEngine] Seek failed:', err)
        }

        if (wasPlaying) {
          this._vinylAnimate(element, 0.3, 1.0, this.vinylSeekDuration)
        } else {
          element.playbackRate = 1.0
        }
        resolve()
      }, this.vinylSeekDuration)
    })
  }

  setVolume(value) {
    if (this.masterGain) {
      this.masterGain.gain.value = Math.max(0, Math.min(MASTER_GAIN_HEADROOM, value * MASTER_GAIN_HEADROOM))
    }
  }

  getVolume() {
    if (this.masterGain) {
      return this.masterGain.gain.value / MASTER_GAIN_HEADROOM
    }
    return 1
  }

  setMuted(muted) {
    if (this.currentSlot?.element) {
      this.currentSlot.element.muted = muted
    }
  }

  getCurrentElement() {
    return this.currentSlot?.element
  }

  destroy() {
    this.removeUnlockGestures()

    this._abortAllOps()
    this._stopVinylWobble()
    this._vinylTimers.forEach(timer => clearTimeout(timer))
    this._vinylTimers.clear()
    if (this._pauseTimer) clearTimeout(this._pauseTimer)
    this.pendingCleanups.forEach(entry => clearTimeout(entry.timer))
    this.pendingCleanups = []

    if (this._boundDeviceChangeHandler) {
      window.removeEventListener('audio-output-device-change', this._boundDeviceChangeHandler)
    }

    this._detachAutoCrossfade()

    if (this.slots.A.element) this._resetSlot(this.slots.A)
    if (this.slots.B.element) this._resetSlot(this.slots.B)

    if (this.sfxElement) {
        this.sfxElement.pause()
        this.sfxElement.src = ''
    }

    if (this.context && this.context.state !== 'closed') {
      this.context.close()
    }

    this.initialized = false
  }
}
