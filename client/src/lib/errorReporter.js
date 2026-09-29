import { setLogSink } from './logger'
import { mediaSupport } from './mediaSupport'
import { getDeviceId } from './session'

const ENDPOINT = '/api/client-log'
const MAX_BREADCRUMBS = 200
const SENT_BREADCRUMBS = 30
const MAX_REPORTS_PER_SESSION = 20
const MAX_BATCH = 10
const FLUSH_DELAY_MS = 5000
const RETRY_DELAY_MS = 30000
const MAX_TEXT = 300
const MAX_MESSAGE = 1000
const MAX_STACK = 4000

const JWT_PATTERN = /eyJ[\w-]{6,}\.[\w-]{6,}\.[\w-]{6,}/g
const EMAIL_PATTERN = /[\w.+-]+@[\w-]+\.[\w.-]+/g
const SECRET_PARAM_PATTERN = /((?:token|access_token|apikey|api_key|key)=)[^&\s"']+/gi

const state = {
  installed: false,
  sid: makeSessionId(),
  breadcrumbs: [],
  queue: [],
  reported: 0,
  fingerprints: new Set(),
  timer: null,
}

function makeSessionId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return `s${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`
}

function scrub(text) {
  return String(text)
    .replace(JWT_PATTERN, '[token]')
    .replace(SECRET_PARAM_PATTERN, '$1[redacted]')
    .replace(EMAIL_PATTERN, '[email]')
}

function describe(value) {
  if (value instanceof Error) return `${value.name}: ${value.message}`
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return String(value)
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value).slice(0, 200)
    } catch {
      return '[object]'
    }
  }
  return String(value)
}

function joinArgs(args) {
  return scrub(args.map(describe).join(' '))
}

function buildId() {
  const script = document.querySelector('script[type="module"][src*="/assets/index-"]')
  const match = script?.getAttribute('src')?.match(/index-([\w-]+)\.js/)
  return match ? match[1] : 'dev'
}

function capabilities() {
  const engine = window.audioEngine
  return {
    mseWebm: mediaSupport.mseWebm,
    webmAudio: mediaSupport.webmAudio,
    managedMediaSource: mediaSupport.managedMediaSource,
    ios: mediaSupport.ios,
    audioContext: engine?.context?.state || 'none',
    online: navigator.onLine,
    visible: document.visibilityState,
  }
}

function breadcrumb(line) {
  state.breadcrumbs.push(`${new Date().toISOString().slice(11, 23)} ${scrub(line).slice(0, MAX_TEXT)}`)
  if (state.breadcrumbs.length > MAX_BREADCRUMBS) state.breadcrumbs.splice(0, state.breadcrumbs.length - MAX_BREADCRUMBS)
}

export function reportClientEvent(kind, message, { stack = null, extra = null } = {}) {
  if (!state.installed || state.reported >= MAX_REPORTS_PER_SESSION) return
  const text = scrub(message || '').slice(0, MAX_MESSAGE)
  const fingerprint = `${kind}:${text.slice(0, 120)}`
  if (state.fingerprints.has(fingerprint)) return
  state.fingerprints.add(fingerprint)
  state.reported++
  state.queue.push({
    kind: String(kind).slice(0, 32),
    message: text,
    stack: stack ? scrub(stack).slice(0, MAX_STACK) : null,
    at: Date.now(),
    extra,
  })
  scheduleFlush()
}

function scheduleFlush(delay = FLUSH_DELAY_MS) {
  if (state.timer) return
  state.timer = setTimeout(() => {
    state.timer = null
    void flush(false)
  }, delay)
}

function payload(entries) {
  return JSON.stringify({
    sid: state.sid,
    device: (getDeviceId() || '').slice(0, 8),
    build: buildId(),
    ua: navigator.userAgent.slice(0, 300),
    standalone: mediaSupport.standalone,
    caps: capabilities(),
    path: window.location.pathname.slice(0, 200),
    breadcrumbs: state.breadcrumbs.slice(-SENT_BREADCRUMBS),
    entries,
  })
}

async function flush(useBeacon) {
  if (!state.queue.length) return
  const entries = state.queue.splice(0, MAX_BATCH)
  const body = payload(entries)
  if (useBeacon && typeof navigator.sendBeacon === 'function') {
    const sent = navigator.sendBeacon(ENDPOINT, new Blob([body], { type: 'text/plain' }))
    if (!sent) state.queue.unshift(...entries)
    return
  }
  let failed = false
  try {
    const res = await fetch(ENDPOINT, { method: 'POST', body, keepalive: true, headers: { 'Content-Type': 'text/plain' } })
    failed = res.status >= 500
  } catch {
    failed = true
  }
  if (failed) state.queue.unshift(...entries)
  if (state.queue.length) scheduleFlush(failed ? RETRY_DELAY_MS : FLUSH_DELAY_MS)
}

export function installErrorReporter() {
  if (state.installed || typeof window === 'undefined') return
  state.installed = true

  setLogSink((level, args) => {
    const line = joinArgs(args)
    breadcrumb(`${level.toUpperCase()} ${line}`)
    if (level === 'error') {
      const error = args.find(arg => arg instanceof Error)
      reportClientEvent('error', line, { stack: error?.stack || null })
    }
  })

  window.addEventListener('error', (event) => {
    const error = event.error
    reportClientEvent('error', error ? `${error.name}: ${error.message}` : String(event.message || 'Script error'), {
      stack: error?.stack || `${event.filename || ''}:${event.lineno || 0}:${event.colno || 0}`,
    })
  })

  window.addEventListener('unhandledrejection', (event) => {
    const reason = event.reason
    reportClientEvent('unhandledrejection', describe(reason), { stack: reason?.stack || null })
  })

  const flushNow = () => void flush(true)
  window.addEventListener('pagehide', flushNow)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushNow()
  })
  window.addEventListener('online', () => void flush(false))
}
