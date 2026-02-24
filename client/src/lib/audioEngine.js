import {logger} from './logger'
import {AudioInteractionManager} from './audioInteractionManager'

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
      A: { element: null, source: null, gain: null, mediaSource: null, sourceBuffer: null, chunks: [], fetchController: null, isComplete: false, trackId: null, metadata: null, blobUrl: null, isFadingOut: false },
      B: { element: null, source: null, gain: null, mediaSource: null, sourceBuffer: null, chunks: [], fetchController: null, isComplete: false, trackId: null, metadata: null, blobUrl: null, isFadingOut: false }
    }

    this.sfxElement = null
    this.sfxSource = null

    this.currentTrackId = null
    this.nextTrackId = null

    this.onCrossfadeStart = null
    this.onCrossfadeStateChange = null
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
    //   _vinylRapidRampUp    - Play: 0.1 → 1.0 (motor starting)
    //   _vinylRapidRampDown  - Pause: 1.0 → 0.1 (motor stopping)
    //   _vinylSeekRampDown   - Seek: 1.0 → 0.3 (needle lifting)
    //   _vinylSeekRampUp     - Seek: 0.3 → 1.0 (needle dropping)
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
    this.timeUpdateElement = null
    this.crossfadeScheduled = false

    this.initialized = false
    this.isUnlockGestureSetup = false
    this.boundUnlockAudio = this.unlockAudio.bind(this)
    this.onUnlockedCallback = null
    this.pendingCleanupTimers = []

    this._isLoadingAnyTrack = false
    this._currentLoadAbortController = null

    this.vinylWobbleActive = false
  }

  setActiveDevice(isActive) {
    const wasActive = this.isActiveDevice
    this.isActiveDevice = isActive

    if (wasActive && !isActive) {
      logger.info('[AudioEngine] 🔕 Device deactivated - stopping audio immediately')
      this.stopImmediately()
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

  _enforceActiveDevice(operation = 'operation') {
    if (!this.isActiveDevice) {
      logger.warn(`[AudioEngine] ⛔ Blocked ${operation}: Not active device`)
      return false
    }
    return true
  }

  async initialize() {
    if (this.initialized) return

    const AudioContext = window.AudioContext || window['webkitAudioContext']
    this.context = new AudioContext({
      latencyHint: 'playback',
      sampleRate: 48000
    })

    AudioInteractionManager.audioContexts.add(this.context)

    const preferredSpeaker = localStorage.getItem('preferredSpeakerId')
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

    this.masterGain.gain.value = 1.122
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

    const handleWaiting = () => {
      if (slot.element.getAttribute('data-blob-url') === 'true') return
      this._reportStarvation(true)
    }

    const handlePlaying = () => {
      this._reportStarvation(false)
    }

    const handleStalled = () => {
      if (slot.element.getAttribute('data-blob-url') === 'true') return
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

    return Promise.resolve()
  }

  getCurrentTrackId() {
    return this.currentTrackId
  }

  getNextTrackId() {
    return this.nextTrackId
  }

  async _cleanupNextSlot(next) {
    if (next.fetchController) {
      next.fetchController.abort()
      next.fetchController = null
    }

    if (next.mediaSource) {
      try {
        if (next.sourceBuffer && next.sourceBuffer.updating) {
          await new Promise(resolve => {
            next.sourceBuffer.addEventListener('updateend', resolve, { once: true })
          })
        }

        if (next.mediaSource.readyState === 'open') {
          next.mediaSource.endOfStream()
        }
      } catch (e) {
        logger.warn('[AudioEngine] Error closing previous MediaSource:', e)
      }
      next.mediaSource = null
      next.sourceBuffer = null
    }

    if (next.blobUrl) {
      URL.revokeObjectURL(next.blobUrl)
      next.blobUrl = null
    }

    next.chunks = []
    next.isComplete = false
    next.isFadingOut = false // Reset state
  }

  async _loadBlobToElement(element, url, slot, shouldRejectOnError = false) {
    element.removeAttribute('crossOrigin')
    element.setAttribute('data-blob-url', 'true')
    element.preload = 'auto'
    element.src = url
    element.load()

    if (url.startsWith('blob:')) {
      slot.blobUrl = url
    }

    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        cleanup()
        logger.warn('[Audio] Blob load timeout, proceeding')
        resolve()
      }, 8000)

      const cleanup = () => {
        element.removeEventListener('canplay', onCanPlay)
        element.removeEventListener('error', onError)
        clearTimeout(timeout)
      }

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

      element.addEventListener('canplay', onCanPlay, { once: true })
      element.addEventListener('error', onError, { once: true })
    })
  }

  async _setupMediaSourceStream(element, url, trackId, slot, waitForCanPlay = true) {
    element.crossOrigin = 'anonymous'
    element.removeAttribute('data-blob-url')
    element.preload = 'metadata'

    return new Promise((resolve, reject) => {
      const mediaSource = new MediaSource()
      slot.mediaSource = mediaSource

      let resolved = false

      const maybeResolve = () => {
        if (!waitForCanPlay || resolved) return
        resolved = true
        element.removeEventListener('canplay', onCanPlay)
        element.removeEventListener('error', onError)
        resolve()
      }

      const onCanPlay = () => maybeResolve()
      const onError = (e) => {
        if (resolved) return
        resolved = true
        element.removeEventListener('canplay', onCanPlay)
        element.removeEventListener('error', onError)
        if (waitForCanPlay) {
          reject(e)
        } else {
          resolve()
        }
      }

      if (waitForCanPlay) {
        element.addEventListener('canplay', onCanPlay, { once: true })
        element.addEventListener('error', onError, { once: true })
      } else {
        element.addEventListener('canplay', onCanPlay)
        element.addEventListener('error', onError)
      }

      mediaSource.addEventListener('sourceopen', async () => {
        try {
          if (slot.mediaSource !== mediaSource) return

          const sourceBuffer = mediaSource.addSourceBuffer('audio/webm; codecs="opus"')
          slot.sourceBuffer = sourceBuffer

          const controller = new AbortController()
          slot.fetchController = controller

          this._streamToBuffer(url, sourceBuffer, slot.chunks, controller.signal, slot)
            .then(async (isComplete) => {
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
            resolve()
          } else if (element.readyState >= 2) {
            maybeResolve()
          }
        } catch (err) {
          logger.error('[Audio] MediaSource setup error:', err)
          reject(err)
        }
      })

      mediaSource.addEventListener('error', (e) => reject(e))
      const blobUrl = URL.createObjectURL(mediaSource)
      slot.blobUrl = blobUrl
      element.src = blobUrl
    })
  }

  _setupAutoCrossfade() {
    if (this.timeUpdateHandler && this.timeUpdateElement) {
      this.timeUpdateElement.removeEventListener('timeupdate', this.timeUpdateHandler)
    }

    this.crossfadeScheduled = false
    this.nextTrackWaitingLogged = false
    this.timeUpdateHandler = () => {
      const element = this.currentSlot.element
      const currentTrack = this.currentSlot.metadata
      const metadataDurationMs = currentTrack?.duration_ms || 0

      if (!metadataDurationMs) {
        return
      }

      if (this.crossfadeScheduled) {
        return
      }

      if (!this.nextTrackId) {
        return
      }

      if (!this.nextSlot.element.src || this.nextSlot.element.readyState < 2) {
        if (!this.nextTrackWaitingLogged) {
          logger.warn(`[Audio] ⏳ Waiting for next track to load (readyState: ${this.nextSlot.element.readyState})`)
          this.nextTrackWaitingLogged = true
        }

        const crossfadeHint = currentTrack?.crossfade_hint
        let actualStartFromEnd = this.crossfadeDuration
        if (crossfadeHint) {
          actualStartFromEnd = metadataDurationMs - crossfadeHint.optimal_start_ms
        }
        const currentTimeMs = element.currentTime * 1000
        const timeRemaining = metadataDurationMs - currentTimeMs

        if (timeRemaining <= actualStartFromEnd + 2000 && !this.vinylWobbleActive) {
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

      const crossfadeHint = currentTrack?.crossfade_hint
      let actualStartFromEnd = this.crossfadeDuration
      let actualDuration = this.crossfadeDuration

      if (crossfadeHint) {
        actualStartFromEnd = metadataDurationMs - crossfadeHint.optimal_start_ms
        actualDuration = crossfadeHint.duration_ms
      }

      const duration = metadataDurationMs
      const currentTime = element.currentTime * 1000
      const timeRemaining = duration - currentTime

      if (timeRemaining <= actualStartFromEnd) {
        this.crossfadeScheduled = true

        const oldTrackId = this.currentTrackId
        const newTrackId = this.nextTrackId

        logger.info(
          `[Audio] 🔀 AUTO-CROSSFADE: ${oldTrackId} -> ${newTrackId} (${actualDuration}ms)`
        )

        this.currentTrackId = newTrackId
        this.nextTrackId = null

        this._executeCrossfade(actualDuration, true, true).then(() => {
          this._setupAutoCrossfade()
        })

        if (this.onCrossfadeStart) {
          logger.info(`[Audio] 📡 Calling onCrossfadeStart callback`)
          this.onCrossfadeStart(oldTrackId, newTrackId, actualDuration, {
            planned_start_from_end_ms: actualStartFromEnd,
            planned_duration_ms: actualDuration,
            actual_start_from_end_ms: timeRemaining,
            actual_duration_ms: actualDuration,
            used_backend_hint: !!crossfadeHint,
            backend_confidence: crossfadeHint?.confidence || null
          })
        } else {
          logger.error(`[Audio] ❌ CALLBACK MISSING! onCrossfadeStart is null`)
        }
      }
    }

    this.timeUpdateElement = this.currentSlot.element
    this.timeUpdateElement.addEventListener('timeupdate', this.timeUpdateHandler)
    logger.info(`[Audio] 🎬 Auto-crossfade monitoring active (nextTrackId: ${this.nextTrackId ? this.nextTrackId.slice(0,8) : 'none'})`)
  }

async loadTrack(trackId, url, options = {}) {
    if (!this._enforceActiveDevice('loadTrack')) return
    if (this.nextTrackId === trackId) return // Already loading

    if (options.useCrossfade) {
      this.nextTrackId = trackId
    } else {
      this.currentTrackId = trackId
    }

    const { isBlobUrl = false, shouldPlay = true, fadeTimeMs = 3000, useCrossfade = true, metadata = {} } = options

    await this.ensureContext()

    if (this._isLoadingAnyTrack && this._currentLoadAbortController) {
      logger.warn(`[Audio] 🔄 Cancelling previous load, starting new load for ${trackId.slice(0, 8)}`)
      this._currentLoadAbortController.abort()
    }

    this._isLoadingAnyTrack = true
    this._currentLoadAbortController = new AbortController()
    const targetSlot = useCrossfade ? this.nextSlot : this.currentSlot
    const slotName = targetSlot === this.slots.A ? 'A' : 'B'
    const abortSignal = this._currentLoadAbortController.signal

    const handleAbort = () => {
      logger.info(`[Audio] 🚫 Load cancelled for ${trackId.slice(0, 8)}`)
    }

    try {
      if (useCrossfade) {
        logger.info(`[Audio] 📀 LOAD & SWAP: ${trackId} into Slot ${slotName}`)
      } else {
        logger.info(`[Audio] 💿 RE-LOAD: ${trackId} into Slot ${slotName}`)
      }

      if (abortSignal.aborted) { handleAbort(); return }

      await this._cleanupNextSlot(targetSlot)

      if (abortSignal.aborted) { handleAbort(); return }

      targetSlot.trackId = trackId
      targetSlot.metadata = metadata

      if (isBlobUrl) {
        await this._loadBlobToElement(targetSlot.element, url, targetSlot, true)
      } else {
        await this._setupMediaSourceStream(targetSlot.element, url, trackId, targetSlot, true)
      }

      if (abortSignal.aborted) { handleAbort(); return }

      if (useCrossfade) {
        this.playVinylScratch()
      }

      await this._executeCrossfade(fadeTimeMs, shouldPlay, useCrossfade, 'user')

      if (abortSignal.aborted) { handleAbort(); return }

      this.currentTrackId = trackId
      this.nextTrackId = null
      this._setupAutoCrossfade()

      logger.info(`[Audio] ✅ PLAYING: ${trackId}`)
    } catch (error) {
      if (error.name === 'AbortError') {
        handleAbort()
      } else {
        logger.error('[Audio] Load failed:', error)
        throw error
      }
    } finally {
      this._isLoadingAnyTrack = false
      this._currentLoadAbortController = null
    }
  }

  async preloadTrack(trackId, url, options = {}) {
    if (!this._enforceActiveDevice('preloadTrack')) return

    if (this.nextTrackId === trackId) return

    if (this.nextSlot.isFadingOut) {
        logger.warn(`[Audio] ✋ Skipping preload for ${trackId}: Target slot is busy fading out`)
        return
    }

    this.nextTrackId = trackId

    const {
      isBlobUrl = false,
      metadata = {}
    } = options

    await this.ensureContext()

    const next = this.nextSlot
    const nextSlotName = next === this.slots.A ? 'A' : 'B'

    logger.info(`[Audio] ⚡ PRELOAD: ${trackId} into Slot ${nextSlotName}`)

    await this._cleanupNextSlot(next)
    next.trackId = trackId
    next.metadata = metadata

    if (isBlobUrl) {
      await this._loadBlobToElement(next.element, url, next, false)
    } else {
      await this._setupMediaSourceStream(next.element, url, trackId, next, false)
    }

    logger.info(`[Audio] 🔋 PRELOAD COMPLETE: ${trackId}`)
  }

  async _streamToBuffer(url, sourceBuffer, chunkArray, signal, slot) {
    const CHUNK_SIZE = 512 * 1024
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

    await this.ensureContext()

    if (this.currentSlot.element.src) {
      const element = this.currentSlot.element
      const now = this.context.currentTime
      const rampSec = this.vinylRapidDuration / 1000

      this.currentSlot.gain.gain.cancelScheduledValues(now)
      this.currentSlot.gain.gain.setValueAtTime(0, now)
      this.currentSlot.gain.gain.linearRampToValueAtTime(1, now + rampSec)

      element.playbackRate = 0.1
      this._vinylRapidRampUp(element, this.vinylRapidDuration)

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
      const element = this.currentSlot.element
      const now = this.context.currentTime
      const rampSec = this.vinylRapidDuration / 1000

      this.currentSlot.gain.gain.cancelScheduledValues(now)
      this.currentSlot.gain.gain.setValueAtTime(this.currentSlot.gain.gain.value, now)
      this.currentSlot.gain.gain.linearRampToValueAtTime(0, now + rampSec)

      this._vinylRapidRampDown(element, this.vinylRapidDuration)

      setTimeout(() => {
        if (this.currentSlot?.element) {
          this.currentSlot.element.pause()
          this.currentSlot.element.playbackRate = 1.0
          this.currentSlot.gain.gain.cancelScheduledValues(0)
          this.currentSlot.gain.gain.value = 0
        }
      }, this.vinylRapidDuration + 10)
    }
  }

  _vinylRapidRampUp(element, durationMs) {
    this._vinylAnimate(element, 0.1, 1.0, durationMs)
  }

  _vinylRapidRampDown(element, durationMs) {
    this._vinylAnimate(element, 1.0, 0.1, durationMs)
  }

  _vinylSlowRampUp(element, durationMs) {
    this._vinylAnimate(element, 0.85, 1.0, durationMs)
  }

  _vinylSeekRampDown(element, durationMs) {
    this._vinylAnimate(element, 1.0, 0.3, durationMs)
  }

  _vinylSeekRampUp(element, durationMs) {
    this._vinylAnimate(element, 0.3, 1.0, durationMs)
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
        window.__rafDebug?.sources && (window.__rafDebug.sources['AudioEngine-ramp'] = (window.__rafDebug.sources['AudioEngine-ramp'] || 0) + 1)
        requestAnimationFrame(animate)
      }
    }
    window.__rafDebug?.sources && (window.__rafDebug.sources['AudioEngine-ramp'] = (window.__rafDebug.sources['AudioEngine-ramp'] || 0) + 1)
    requestAnimationFrame(animate)
  }

  _vinylAnimate(element, from, to, durationMs) {
    const startTime = performance.now()
    const animate = () => {
      const elapsed = performance.now() - startTime
      const progress = Math.min(elapsed / durationMs, 1)
      const eased = 1 - Math.pow(1 - progress, 2)
      element.playbackRate = from + (to - from) * eased
      if (progress < 1) {
        window.__rafDebug?.sources && (window.__rafDebug.sources['AudioEngine-vinyl'] = (window.__rafDebug.sources['AudioEngine-vinyl'] || 0) + 1)
        requestAnimationFrame(animate)
      }
    }
    window.__rafDebug?.sources && (window.__rafDebug.sources['AudioEngine-vinyl'] = (window.__rafDebug.sources['AudioEngine-vinyl'] || 0) + 1)
    requestAnimationFrame(animate)
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

      window.__rafDebug?.sources && (window.__rafDebug.sources['AudioEngine-wobble'] = (window.__rafDebug.sources['AudioEngine-wobble'] || 0) + 1)
      requestAnimationFrame(wobble)
    }
    window.__rafDebug?.sources && (window.__rafDebug.sources['AudioEngine-wobble'] = (window.__rafDebug.sources['AudioEngine-wobble'] || 0) + 1)
    requestAnimationFrame(wobble)
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

    if (this.currentSlot?.fetchController) {
      this.currentSlot.fetchController.abort()
      logger.info('[AudioEngine] Aborted current slot fetch')
    }

    if (this.nextSlot?.fetchController) {
      this.nextSlot.fetchController.abort()
      logger.info('[AudioEngine] Aborted next slot fetch')
    }

    const trackId = getCurrentTrackId()
    if (!trackId) return { switched: false }

    const cached = await getCachedVersion(trackId)
    if (!cached) {
      logger.info('[AudioEngine] No cached version available - buffer will starve')
      return { switched: false }
    }

    const currentElement = this.getCurrentElement()
    const currentTime = currentElement?.currentTime || 0
    const wasPlaying = currentElement && !currentElement.paused

    const blobUrl = URL.createObjectURL(cached.audioBlob)

    await this.loadTrack(trackId, blobUrl, {
      isBlobUrl: true,
      shouldPlay: wasPlaying,
      fadeTimeMs: 0,
      useCrossfade: false,
      metadata: cached.metadata
    })

    if (currentTime > 0) {
      await this.seek(currentTime)
    }

    logger.info(`[AudioEngine] ✅ Switched to cached ${cached.bitrate} version`)
    return { switched: true, bitrate: cached.bitrate }
  }

  _scheduleSlotCleanup(slot, actualFadeTime) {
    const cleanupTimer = setTimeout(() => {
      slot.isFadingOut = false

      // Publish crossfade end to UIState
      if (this.onCrossfadeStateChange) {
        this.onCrossfadeStateChange(false)
      }

      if (slot.element && slot !== this.nextSlot) {
        slot.element.pause()
        slot.element.playbackRate = 1.0
        slot.element.currentTime = 0
        slot.element.src = ''
        slot.gain.gain.cancelScheduledValues(0)
        slot.gain.gain.value = 0
      }
    }, actualFadeTime * 1000 + 100)
    this.pendingCleanupTimers.push(cleanupTimer)
  }

  async crossfade(fadeTimeMs = 50, shouldPlay = true, useCrossfade = true, transitionType = 'user') {
    if (!this._enforceActiveDevice('crossfade')) return

    await this._executeCrossfade(fadeTimeMs, shouldPlay, useCrossfade, transitionType)

    this.currentTrackId = this.currentSlot.trackId
    this.nextTrackId = null

    this._setupAutoCrossfade()
  }

  async _executeCrossfade(fadeTimeMs = 50, shouldPlay = true, useCrossfade = true, transitionType = 'natural') {
    await this.ensureContext()

    this._stopVinylWobble()

    this.pendingCleanupTimers.forEach(timer => clearTimeout(timer))
    this.pendingCleanupTimers = []

    const current = this.currentSlot

    if (!useCrossfade) {
      const actualFadeTime = fadeTimeMs === 0 ? 0.015 : fadeTimeMs / 1000
      const now = this.context.currentTime

      current.gain.gain.cancelScheduledValues(now)
      current.gain.gain.setValueAtTime(0, now)

      if (shouldPlay) {
        current.gain.gain.linearRampToValueAtTime(1, now + actualFadeTime)
        try {
          await current.element.play()
        } catch {
          // Play errors (e.g., autoplay policy) are intentionally suppressed
        }
      } else {
        current.gain.gain.value = 1
      }
      return
    }

    const next = this.nextSlot

    if (!next.element.src) {
        logger.warn('[Audio] ⚠️ Crossfade skipped: Next slot empty. Attempting direct play.')
        return this._executeCrossfade(fadeTimeMs, shouldPlay, false)
    }

    // Publish crossfade start to UIState (only for actual A/B crossfades)
    if (this.onCrossfadeStateChange) {
      this.onCrossfadeStateChange(true)
    }

    const actualFadeTime = fadeTimeMs === 0 ? 0.015 : fadeTimeMs / 1000
    const now = this.context.currentTime

    current.isFadingOut = true

    current.gain.gain.cancelScheduledValues(now)
    current.gain.gain.setValueAtTime(current.gain.gain.value, now)
    current.gain.gain.linearRampToValueAtTime(0, now + actualFadeTime)

    if (transitionType === 'user' && current.element && !current.element.paused) {
      this._vinylSlowRampDown(current.element, actualFadeTime * 1000)
    }

    next.gain.gain.cancelScheduledValues(now)
    next.gain.gain.setValueAtTime(0, now)

    if (shouldPlay) {
      next.gain.gain.linearRampToValueAtTime(1, now + actualFadeTime)
      if (transitionType === 'user') {
        next.element.playbackRate = 0.85
        this._vinylSlowRampUp(next.element, actualFadeTime * 1000 * 0.5)
      }
      try {
        await next.element.play()
      } catch {
        // Play errors (e.g., autoplay policy) are intentionally suppressed
      }
    } else {
      next.gain.gain.value = 1
    }

    this._scheduleSlotCleanup(current, actualFadeTime)

    this.currentSlot = next
    this.nextSlot = current

    logger.info(`[Audio] 🔄 SWAPPED SLOTS: New Current is Slot ${this.currentSlot === this.slots.A ? 'A' : 'B'}`)
  }

  seek(timeSeconds) {
    if (!this._enforceActiveDevice('seek')) return Promise.resolve()

    if (!this.currentSlot?.element) return Promise.resolve()

    const element = this.currentSlot.element
    const wasPlaying = !element.paused

    if (element.readyState < 2) {
      return new Promise((resolve) => {
        const onLoadedMetadata = () => {
          element.currentTime = timeSeconds
          resolve()
        }
        element.addEventListener('loadedmetadata', onLoadedMetadata, { once: true })
      })
    }

    this._vinylSeekRampDown(element, this.vinylSeekDuration)

    return new Promise((resolve) => {
      setTimeout(() => {
        try {
          element.currentTime = timeSeconds
        } catch (err) {
          logger.error('[AudioEngine] Seek failed:', err)
        }

        if (wasPlaying) {
          this._vinylSeekRampUp(element, this.vinylSeekDuration)
        } else {
          element.playbackRate = 1.0
        }
        resolve()
      }, this.vinylSeekDuration)
    })
  }

  setVolume(value) {
    if (this.masterGain) {
      this.masterGain.gain.value = Math.max(0, Math.min(1.122, value * 1.122))
    }
  }

  getVolume() {
    if (this.masterGain) {
      return this.masterGain.gain.value / 1.122
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

    this._stopVinylWobble()

    if (this._boundDeviceChangeHandler) {
      window.removeEventListener('audio-output-device-change', this._boundDeviceChangeHandler)
    }

    if (this.timeUpdateHandler && this.timeUpdateElement) {
      this.timeUpdateElement.removeEventListener('timeupdate', this.timeUpdateHandler)
    }

    if (this.slots.A.element) {
        this.slots.A.element.pause()
        this.slots.A.element.src = ''
    }

    if (this.slots.B.element) {
        this.slots.B.element.pause()
        this.slots.B.element.src = ''
    }

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