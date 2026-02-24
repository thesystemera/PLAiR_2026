const LOG_LEVELS = {
  CRITICAL: 0,
  INFO: 1,
  DEBUG: 2,
}

const config = {
  level: import.meta.env.DEV ? LOG_LEVELS.DEBUG : LOG_LEVELS.INFO,
  enabled: true,
}

export const logger = {
  critical: (...args) => {
    if (config.enabled && config.level >= LOG_LEVELS.CRITICAL) {
      console.error('[CRITICAL]', ...args)
    }
  },

  info: (...args) => {
    if (config.enabled && config.level >= LOG_LEVELS.INFO) {
      console.log('[INFO]', ...args)
    }
  },

  debug: (...args) => {
    if (config.enabled && config.level >= LOG_LEVELS.DEBUG) {
      console.log('[DEBUG]', ...args)
    }
  },

  warn: (...args) => {
    if (config.enabled && config.level >= LOG_LEVELS.INFO) {
      console.warn('[WARN]', ...args)
    }
  },

  error: (...args) => {
    if (config.enabled && config.level >= LOG_LEVELS.CRITICAL) {
      console.error('[ERROR]', ...args)
    }
  },
}

export { LOG_LEVELS }