const LOG_LEVELS = {
  CRITICAL: 0,
  INFO: 1,
  DEBUG: 2,
}

const config = {
  level: import.meta.env.DEV ? LOG_LEVELS.DEBUG : LOG_LEVELS.INFO,
  enabled: true,
}

let sink = null

export function setLogSink(fn) {
  sink = typeof fn === 'function' ? fn : null
}

function forward(level, args) {
  if (!sink) return
  try {
    sink(level, args)
  } catch {
    sink = null
  }
}

export const logger = {
  critical: (...args) => {
    forward('error', args)
    if (config.enabled && config.level >= LOG_LEVELS.CRITICAL) {
      console.error('[CRITICAL]', ...args)
    }
  },

  info: (...args) => {
    forward('info', args)
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
    forward('warn', args)
    if (config.enabled && config.level >= LOG_LEVELS.INFO) {
      console.warn('[WARN]', ...args)
    }
  },

  error: (...args) => {
    forward('error', args)
    if (config.enabled && config.level >= LOG_LEVELS.CRITICAL) {
      console.error('[ERROR]', ...args)
    }
  },
}

