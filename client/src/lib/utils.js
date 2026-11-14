export function formatDuration(ms) {
  const seconds = Math.floor((ms || 0) / 1000)
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

const SHORT_DATE = new Intl.DateTimeFormat('en-US', { month: 'short', year: 'numeric' })

export function formatDateShort(dateStr) {
  if (!dateStr) return 'Unknown'
  const date = new Date(dateStr)
  return Number.isNaN(date.getTime()) ? date.toLocaleDateString('en-US', { month: 'short', year: 'numeric' }) : SHORT_DATE.format(date)
}

const TIME_AGO_UNITS = [[31536000, 'y'], [2592000, 'mo'], [604800, 'w'], [86400, 'd'], [3600, 'h'], [60, 'm']]

export function formatTimeAgo(dateStr) {
  const time = new Date(dateStr).getTime()
  if (!Number.isFinite(time)) return ''
  const seconds = Math.max(0, Math.round((Date.now() - time) / 1000))
  const unit = TIME_AGO_UNITS.find(([size]) => seconds >= size)
  return unit ? `${Math.floor(seconds / unit[0])}${unit[1]} ago` : 'just now'
}

export function formatTimeLeft(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return ''
  if (seconds < 60) return `${Math.round(seconds)} s left`
  return `about ${Math.round(seconds / 60)} min left`
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

export function trackTitle(track) {
  return track?.generation_params?.title || track?.title || track?.track_info?.title || null
}

function trackArtist(track) {
  return track?.generation_params?.artist_name || track?.track_info?.artist || track?.artist_name || track?.artist || track?.derived_tags?.inspired_artist || null
}

export function artistCredit(track) {
  const artist = trackArtist(track)
  if (!artist) return null
  return track.is_ai_generated === false ? artist : `Inspired by ${artist}`
}

export function artistByline(track) {
  const artist = trackArtist(track)
  if (!artist) return ''
  return track.is_ai_generated === false ? `by ${artist}` : `inspired by ${artist}`
}
