const WEBM_OPUS = 'audio/webm; codecs="opus"'

function safely(fn, fallback) {
  try {
    return fn()
  } catch {
    return fallback
  }
}

function detect() {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return { mseWebm: false, webmAudio: false, managedMediaSource: false, ios: false, standalone: false }
  }
  const MediaSourceCtor = window.MediaSource
  const mseWebm = safely(() => !!MediaSourceCtor && typeof MediaSourceCtor.isTypeSupported === 'function' && MediaSourceCtor.isTypeSupported(WEBM_OPUS), false)
  const webmAudio = safely(() => !!document.createElement('audio').canPlayType?.(WEBM_OPUS), false)
  const ua = navigator.userAgent || ''
  const ios = /iPad|iPhone|iPod/.test(ua) || (ua.includes('Macintosh') && navigator.maxTouchPoints > 1)
  const standalone = safely(() => window.matchMedia('(display-mode: standalone)').matches, false) || navigator.standalone === true
  return { mseWebm, webmAudio, managedMediaSource: !!window.ManagedMediaSource, ios, standalone }
}

export const mediaSupport = detect()

export function usesProgressiveStreaming() {
  return !mediaSupport.mseWebm
}

export function downloadFormat() {
  return mediaSupport.webmAudio ? 'webm' : 'mp3'
}

export function canPlayCachedBlob(blob) {
  const type = blob?.type || ''
  if (!type || type.includes('webm')) return mediaSupport.webmAudio
  return true
}
