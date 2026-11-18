import { api } from './api'
import { logger } from './logger'
import { videoClipCache } from './mediaCache'

const SLOW_CONNECTIONS = new Set(['slow-2g', '2g', '3g'])
const FAVOURITE_SHARE = 0.4
const FAVOURITES_DELAY_MS = 60 * 1000

let chain = Promise.resolve()
let pinned = new Set()
let favouritesStarted = false

videoClipCache.addPinnedChecker(file => pinned.has(file))

function clipFile(url) {
  return typeof url === 'string' ? url.split('/video-clip-file/')[1] || null : null
}

export function cachedClipUrl(url) {
  const file = clipFile(url)
  if (file && videoClipCache.isMissing(file)) return null
  return (file && videoClipCache.getMemory(file)) || url
}

function networkAllows(state, background) {
  const { audioState, settingsState } = state
  if (!audioState.isOnline || audioState.offlineMode || settingsState.dataSaverMode) return false
  const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection
  if (connection?.saveData) return false
  if (connection?.effectiveType && SLOW_CONNECTIONS.has(connection.effectiveType)) return false
  return !(background && connection?.type === 'cellular')
}

export function pinClips(lists) {
  pinned = new Set(lists.flat().map(clip => clipFile(clip?.url)).filter(Boolean))
}

export function warmClips(clips, state) {
  const files = clips.map(clip => clipFile(clip?.url)).filter(Boolean)
  const run = chain.then(async () => {
    const missing = []
    for (const file of files) {
      if (!networkAllows(state, false)) break
      if (!(await videoClipCache.getMedia(file)) && videoClipCache.isMissing(file)) missing.push(file)
    }
    return missing
  })
  chain = run.catch(() => [])
  return run
}

function shuffled(items) {
  const copy = [...items]
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[copy[i], copy[j]] = [copy[j], copy[i]]
  }
  return copy
}

export function warmFavouriteClips(state) {
  if (favouritesStarted) return
  favouritesStarted = true
  setTimeout(async () => {
    try {
      if (!networkAllows(state, true)) return
      const preferences = await api.getUserPreferences('track')
      let budget = Math.floor(videoClipCache.config.maxItems * FAVOURITE_SHARE)
      let songs = 0
      for (const track of shuffled(preferences?.super_likes || [])) {
        if (budget <= 0 || !networkAllows(state, true)) break
        const data = await api.getVideoClips(track.id, { prefetch: true })
        const files = (data?.clips || []).map(clip => clipFile(clip.url)).filter(Boolean).slice(0, budget)
        for (const file of files) {
          if (!networkAllows(state, true)) return
          await videoClipCache.getMedia(file)
        }
        budget -= files.length
        if (files.length) songs++
      }
      logger.info(`[VideoClipCache] Super likes warmed: ${songs} songs`)
    } catch (error) {
      logger.debug('[VideoClipCache] Super likes not warmed', error?.message)
    }
  }, FAVOURITES_DELAY_MS)
}
