export function formatDuration(ms) {
  const seconds = Math.floor((ms || 0) / 1000)
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

export function isHumanTrack(track) {
  return track?.is_human === true || track?.is_ai_generated === false
}

export function formatDateShort(dateStr) {
  if (!dateStr) return 'Unknown'
  const date = new Date(dateStr)
  return date.toLocaleDateString('en-US', { month: 'short', year: 'numeric' })
}

const TIME_AGO_UNITS = [[31536000, 'y'], [2592000, 'mo'], [604800, 'w'], [86400, 'd'], [3600, 'h'], [60, 'm']]

export function formatTimeAgo(dateStr) {
  const time = new Date(dateStr).getTime()
  if (!Number.isFinite(time)) return ''
  const seconds = Math.max(0, Math.round((Date.now() - time) / 1000))
  const unit = TIME_AGO_UNITS.find(([size]) => seconds >= size)
  return unit ? `${Math.floor(seconds / unit[0])}${unit[1]} ago` : 'just now'
}

export function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onloadend = () => resolve(String(reader.result).split(',')[1])
    reader.onerror = reject
    reader.readAsDataURL(blob)
  })
}

export function isWebGL2Available() {
  try {
    const gl = document.createElement('canvas').getContext('webgl2')
    if (!gl) return false
    gl.getExtension('WEBGL_lose_context')?.loseContext()
    return true
  } catch {
    return false
  }
}
