import { logger } from './logger'
const INTERACTION_KEY = 'hadUserInteraction'

class AudioInteractionManagerClass {
  constructor() {
    this.audioContexts = new Set()
    this.initialized = false
  }

  init() {
    if (this.initialized) return

    const events = ['click', 'touchstart', 'touchend', 'keydown']
    events.forEach(eventType => {
      document.addEventListener(eventType, this.handleInteraction.bind(this), {
        capture: true,
        passive: true
      })
    })

    this.initialized = true
    logger.info('[Audio] 🛡️ Interaction Manager active')
  }

  handleInteraction() {
    if (localStorage.getItem(INTERACTION_KEY) === 'true') {
      this.resumeAudioContexts()
      return
    }

    localStorage.setItem(INTERACTION_KEY, 'true')
    logger.info('[Audio] 🔓 User interaction captured - Audio unlocked')
    this.resumeAudioContexts()
  }

  hasInteraction() {
    return localStorage.getItem(INTERACTION_KEY) === 'true'
  }

  resumeAudioContexts() {
    this.audioContexts.forEach(ctx => {
      if (ctx.state === 'suspended') {
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