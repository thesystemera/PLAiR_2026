import { logger } from './logger'
import { safeStorage } from './safeStorage'
const INTERACTION_KEY = 'hadUserInteraction'
const RESUMABLE_STATES = new Set(['suspended', 'interrupted'])

class AudioInteractionManagerClass {
  constructor() {
    this.audioContexts = new Set()
    this.gestureCallbacks = new Set()
    this.initialized = false
    this.sessionInteraction = false
  }

  onUserGesture(callback) {
    this.gestureCallbacks.add(callback)
    return () => this.gestureCallbacks.delete(callback)
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

  init() {
    if (this.initialized) return

    const handler = this.handleInteraction.bind(this)
    const events = ['click', 'touchstart', 'touchend', 'keydown']
    events.forEach(eventType => {
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
    this.runGestureCallbacks(event)
    if (event?.type !== 'touchstart') this.sessionInteraction = true

    if (safeStorage.get(INTERACTION_KEY) !== 'true') {
      safeStorage.set(INTERACTION_KEY, 'true')
      logger.info('[Audio] 🔓 User interaction captured - Audio unlocked')
    }
    this.resumeAudioContexts()
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
