import { logger } from './logger'
import { safeStorage } from './safeStorage'
const INTERACTION_KEY = 'hadUserInteraction'
const RESUMABLE_STATES = new Set(['suspended', 'interrupted'])
const GESTURE_EVENTS = ['pointerup', 'click', 'touchstart', 'touchend', 'keydown']
const SILENT_SAMPLE_RATE = 8000
const SILENT_SAMPLES = 400

let silentUrl = null

function silentWavUrl() {
  if (silentUrl) return silentUrl
  const buffer = new ArrayBuffer(44 + SILENT_SAMPLES)
  const view = new DataView(buffer)
  const text = (offset, value) => [...value].forEach((ch, i) => view.setUint8(offset + i, ch.charCodeAt(0)))
  text(0, 'RIFF')
  view.setUint32(4, 36 + SILENT_SAMPLES, true)
  text(8, 'WAVE')
  text(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, SILENT_SAMPLE_RATE, true)
  view.setUint32(28, SILENT_SAMPLE_RATE, true)
  view.setUint16(32, 1, true)
  view.setUint16(34, 8, true)
  text(36, 'data')
  view.setUint32(40, SILENT_SAMPLES, true)
  for (let i = 0; i < SILENT_SAMPLES; i++) view.setUint8(44 + i, 128)
  silentUrl = URL.createObjectURL(new Blob([buffer], { type: 'audio/wav' }))
  return silentUrl
}

class AudioInteractionManagerClass {
  constructor() {
    this.audioContexts = new Set()
    this.gestureCallbacks = new Set()
    this.gestureHooks = new Set()
    this.mediaElements = new Set()
    this.unlockedElements = new WeakSet()
    this.initialized = false
    this.sessionInteraction = false
  }

  onUserGesture(callback) {
    this.gestureCallbacks.add(callback)
    return () => this.gestureCallbacks.delete(callback)
  }

  addGestureHook(hook) {
    this.gestureHooks.add(hook)
    return () => this.gestureHooks.delete(hook)
  }

  registerMediaElement(element) {
    if (!element) return () => {}
    this.mediaElements.add(element)
    return () => this.mediaElements.delete(element)
  }

  isUnlocked(element) {
    return !!element && this.unlockedElements.has(element)
  }

  markUnlocked(element) {
    if (element) this.unlockedElements.add(element)
  }

  unlockIdleElement(element) {
    if (!element || this.unlockedElements.has(element) || !element.paused || element.getAttribute('src')) return
    const silent = silentWavUrl()
    element.src = silent
    const attempt = element.play()
    if (!attempt || typeof attempt.then !== 'function') return
    attempt
      .then(() => {
        this.unlockedElements.add(element)
        if (element.getAttribute('src') !== silent) return
        element.pause()
        element.removeAttribute('src')
        element.load()
      })
      .catch(() => {
        if (element.getAttribute('src') !== silent) return
        element.removeAttribute('src')
        element.load()
      })
  }

  runGestureCallbacks(event) {
    if (!this.gestureCallbacks.size || event?.type === 'touchstart') return
    const callbacks = Array.from(this.gestureCallbacks)
    this.gestureCallbacks.clear()
    callbacks.forEach(callback => {
      try {
        callback()
      } catch (error) {
        logger.warn('[Audio] ⚠️ Gesture callback failed:', error)
      }
    })
  }

  runGestureHooks() {
    this.gestureHooks.forEach(hook => {
      try {
        hook()
      } catch (error) {
        logger.warn('[Audio] ⚠️ Gesture hook failed:', error)
      }
    })
    this.mediaElements.forEach(element => {
      try {
        this.unlockIdleElement(element)
      } catch (error) {
        logger.warn('[Audio] ⚠️ Media unlock failed:', error)
      }
    })
  }

  init() {
    if (this.initialized) return

    const handler = this.handleInteraction.bind(this)
    GESTURE_EVENTS.forEach(eventType => {
      document.addEventListener(eventType, handler, {
        capture: true,
        passive: true
      })
    })

    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && this.hasSessionInteraction()) {
        this.resumeAudioContexts()
      }
    })
    window.addEventListener('pageshow', () => {
      if (this.hasSessionInteraction()) {
        this.resumeAudioContexts()
      }
    })

    this.initialized = true
    logger.info('[Audio] 🛡️ Interaction Manager active')
  }

  handleInteraction(event) {
    const activating = event?.type !== 'touchstart' && event?.isTrusted !== false
    if (activating) {
      this.sessionInteraction = true
      this.resumeAudioContexts()
      this.runGestureHooks()
    }
    this.runGestureCallbacks(event)

    if (activating && safeStorage.get(INTERACTION_KEY) !== 'true') {
      safeStorage.set(INTERACTION_KEY, 'true')
      logger.info('[Audio] 🔓 User interaction captured - Audio unlocked')
    }
  }

  hasInteraction() {
    return safeStorage.get(INTERACTION_KEY) === 'true'
  }

  hasSessionInteraction() {
    const activation = typeof navigator !== 'undefined' ? navigator.userActivation : null
    if (activation && typeof activation.hasBeenActive === 'boolean') return activation.hasBeenActive
    return this.sessionInteraction
  }

  resumeAudioContexts() {
    this.audioContexts.forEach(ctx => {
      if (ctx.state === 'closed') {
        this.audioContexts.delete(ctx)
        return
      }
      if (RESUMABLE_STATES.has(ctx.state)) {
        ctx.resume()
          .then(() => {
            logger.info('[Audio] 🔊 AudioContext resumed')
          })
          .catch(error => {
            logger.warn('[Audio] ⚠️ AudioContext resume failed:', error)
          })
      }
    })
  }
}

export const AudioInteractionManager = new AudioInteractionManagerClass()

if (typeof window !== 'undefined') {
  AudioInteractionManager.init()
}
