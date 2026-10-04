import { logger } from './logger'
import { safeStorage } from './safeStorage'
import { cacheManager } from './cacheManager'
import { getDeviceId } from './session'
import { libraryMethods } from './offlineAPILibrary'
import { writesMethods } from './offlineAPIWrites'
import { unavailableMethods } from './offlineAPIUnavailable'

const QUEUE_SIZE = 11
const TARGET_INDEX = 5
const RECENT_LIMIT = 30
const MIN_FAVORITES_POOL = 3

export const OFFLINE_MESSAGES = {
  voiceSearch: 'Voice search needs a connection to PLAiR. You can still type to search your downloads.',
  shoutouts: 'Shoutouts need a connection to PLAiR. They will be back when you are online.',
  reply: 'Replies need a connection to PLAiR. Please try again when you are back online.',
  review: 'Reviews need a connection to PLAiR. Please try again when you are back online.',
  deleteShoutout: 'Deleting a shoutout needs a connection to PLAiR. Please try again when you are back online.',
  radioMode: 'Radio Mode needs a connection to PLAiR. Your change was not saved.',
}

export const STORAGE_KEYS = {
  TRACK_PREFERENCES: 'offline_track_preferences',
  SHOUTOUT_PREFERENCES: 'offline_shoutout_preferences',
  PENDING_PREFERENCES: 'offline_pending_preferences',
  PENDING_PROFILE: 'offline_pending_profile',
  LAST_SEED_MODE: 'lastSeedMode',
}

const SEED_MATCHERS = {
  primary_genre: (seed, track) => sameText(seed.derived_tags?.primary_genre, track.derived_tags?.primary_genre) ? 1 : 0,
  secondary_genres: (seed, track) => overlap(seed.derived_tags?.secondary_genres, track.derived_tags?.secondary_genres),
  mood: (seed, track) => overlap(seed.derived_tags?.mood_keywords, track.derived_tags?.mood_keywords),
  primary_artist: (seed, track) => (sameText(seed.artist_name, track.artist_name) || sameText(seed.derived_tags?.inspired_artist, track.derived_tags?.inspired_artist)) ? 1 : 0,
  similar_artists: (seed, track) => Math.max(
    overlap(seed.derived_tags?.similar_artists, track.derived_tags?.similar_artists),
    sameText(seed.derived_tags?.inspired_artist, track.derived_tags?.inspired_artist) ? 1 : 0
  ),
  style: (seed, track) => overlap(words(seed.style), words(track.style)),
  vocal: (seed, track) => overlap(seed.derived_tags?.vocal_style_keywords, track.derived_tags?.vocal_style_keywords),
}

function sameText(a, b) {
  return typeof a === 'string' && typeof b === 'string' && a.trim() !== '' && a.trim().toLowerCase() === b.trim().toLowerCase()
}

function words(text) {
  return typeof text === 'string' ? text.toLowerCase().split(/[\s,/]+/).filter(w => w.length > 2) : []
}

function overlap(a, b) {
  if (!Array.isArray(a) || !Array.isArray(b) || !a.length || !b.length) return 0
  const left = new Set(a.map(v => String(v).toLowerCase()))
  const shared = b.filter(v => left.has(String(v).toLowerCase())).length
  return shared / Math.max(a.length, b.length)
}

export function readJson(key, fallback) {
  try {
    const data = safeStorage.get(key)
    return data ? JSON.parse(data) : fallback
  } catch (err) {
    logger.error(`[OfflineBackend] Failed to read ${key}:`, err)
    return fallback
  }
}

export function cachedUserId() {
  try {
    const cached = safeStorage.get('cached_user')
    return cached ? JSON.parse(cached)?.id ?? null : null
  } catch {
    return null
  }
}

export function writeJson(key, value) {
  try {
    safeStorage.set(key, JSON.stringify(value))
  } catch (err) {
    logger.error(`[OfflineBackend] Failed to save ${key}:`, err)
  }
}

function preferenceKey(type) {
  return type === 'track' ? STORAGE_KEYS.TRACK_PREFERENCES : STORAGE_KEYS.SHOUTOUT_PREFERENCES
}

export function getOfflinePreferences(type = 'track') {
  return readJson(preferenceKey(type), {})
}

export function saveOfflinePreferences(type = 'track', preferences) {
  writeJson(preferenceKey(type), preferences)
}

export function idOf(item) {
  return typeof item === 'string' ? item : item?.id
}

function queueItem(track) {
  return {
    id: track.id,
    title: track.title || track.generation_params?.title || 'Untitled',
    artist_name: track.artist_name ?? track.generation_params?.artist_name ?? null,
    style: track.style || track.generation_params?.style || '',
    duration_ms: track.duration_ms || track.track_info?.duration || 0,
    has_artwork: !!track.has_artwork,
  }
}

class OfflineBackend {
  constructor() {
    this.queue = []
    this.currentIndex = 0
    this.history = []
    this.recent = []
    this.radioMode = null
    cacheManager.setEvictionGuard(() => this.likedTrackIds())
  }

  likedTrackIds() {
    return Object.entries(getOfflinePreferences('track'))
      .filter(([, data]) => data.type === 'like' || data.type === 'super_like')
      .map(([id]) => id)
  }

  async _libraryTracks() {
    const list = await cacheManager.getCachedTrackList()
    return list
      .filter(entry => entry.playable && entry.metadata?.id)
      .map(entry => ({
        ...entry.metadata,
        has_artwork: entry.hasArtwork || !!entry.metadata.has_artwork,
        has_enriched_artwork: entry.hasEnrichedArtwork,
        _addedAt: entry.addedAt || 0
      }))
  }

  async getLibraryCount() {
    const tracks = await this._libraryTracks()
    return tracks.length
  }

  _state({ isPlaying = true, progressMs = 0 } = {}) {
    const current = this.queue[this.currentIndex] || null
    return {
      current_track: current ? { ...current } : null,
      queue: this.queue.map(queueItem),
      history: [],
      current_index: this.currentIndex,
      is_playing: isPlaying,
      progress_ms: progressMs,
      active_device_id: getDeviceId(),
      activeSeedMode: this.radioMode,
      offline: true
    }
  }

  _response(options = {}) {
    return { status: 'playing', state: this._state(options), offline: true }
  }

  _remember(trackId) {
    if (!trackId) return
    this.recent = [...this.recent.filter(id => id !== trackId), trackId].slice(-RECENT_LIMIT)
  }

  _preferenceSets() {
    const liked = new Set()
    const superLiked = new Set()
    const banned = new Set()
    Object.entries(getOfflinePreferences('track')).forEach(([trackId, data]) => {
      if (data.type === 'like') liked.add(trackId)
      else if (data.type === 'super_like') superLiked.add(trackId)
      else if (data.type === 'ban') banned.add(trackId)
    })
    return { liked, superLiked, banned }
  }

  _scoreTrackSimilarity(seedTrack, candidateTrack) {
    let score = 0
    const seedStyle = (seedTrack.style || '').toLowerCase()
    const candidateStyle = (candidateTrack.style || '').toLowerCase()

    if (seedStyle && candidateStyle) {
      if (seedStyle === candidateStyle) score += 0.5
      else if (seedStyle.includes(candidateStyle) || candidateStyle.includes(seedStyle)) score += 0.3
      else score += overlap(words(seedStyle), words(candidateStyle)) * 0.3
    }

    if (sameText(seedTrack.derived_tags?.primary_genre, candidateTrack.derived_tags?.primary_genre)) score += 0.3
    score += overlap(seedTrack.derived_tags?.mood_keywords, candidateTrack.derived_tags?.mood_keywords) * 0.2
    score += overlap(seedTrack.tags, candidateTrack.tags) * 0.3
    return score
  }

  _pickUpcoming(seedTrack, tracks, count, excludeIds = new Set()) {
    if (count <= 0) return []
    const mode = this.radioMode
    const { liked, superLiked, banned } = this._preferenceSets()
    const recent = new Set(this.recent)
    let pool = tracks.filter(track => track.id && !excludeIds.has(track.id) && !banned.has(track.id))
    if (mode === 'favorites') {
      const favorites = pool.filter(track => liked.has(track.id) || superLiked.has(track.id))
      if (favorites.length >= Math.min(MIN_FAVORITES_POOL, count)) pool = favorites
    }
    const fresh = pool.filter(track => !recent.has(track.id))
    if (fresh.length >= Math.min(count, pool.length)) pool = fresh.length ? fresh : pool

    const matcher = SEED_MATCHERS[mode]
    const weighted = pool.map(track => {
      let weight = 1
      if (superLiked.has(track.id)) weight += 2
      else if (liked.has(track.id)) weight += 1
      if (seedTrack) {
        weight += matcher ? matcher(seedTrack, track) * 4 : this._scoreTrackSimilarity(seedTrack, track)
      }
      if (recent.has(track.id)) weight *= 0.2
      return { track, weight: Math.max(weight, 0.05) }
    })

    const picked = []
    let lastArtist = (seedTrack?.artist_name || '').toLowerCase()
    while (picked.length < count && weighted.length) {
      const sameArtistOnly = weighted.every(entry => (entry.track.artist_name || '').toLowerCase() === lastArtist)
      const candidates = weighted.filter(entry => sameArtistOnly || !lastArtist ||
        (entry.track.artist_name || '').toLowerCase() !== lastArtist)
      const total = candidates.reduce((sum, entry) => sum + entry.weight, 0)
      let roll = Math.random() * total
      let chosen = candidates[candidates.length - 1]
      for (const entry of candidates) {
        roll -= entry.weight
        if (roll <= 0) {
          chosen = entry
          break
        }
      }
      picked.push(chosen.track)
      weighted.splice(weighted.indexOf(chosen), 1)
      lastArtist = (chosen.track.artist_name || '').toLowerCase()
    }
    return picked
  }

  _trimHistory() {
    while (this.currentIndex > TARGET_INDEX && this.queue.length > 0) {
      this.history.push(this.queue.shift())
      this.currentIndex--
      if (this.history.length > 50) this.history.shift()
    }
  }

  _restoreHistory() {
    while (this.currentIndex < TARGET_INDEX && this.history.length) {
      this.queue.unshift(this.history.pop())
      this.currentIndex++
    }
  }

  async _fillQueue(tracks = null) {
    const needed = QUEUE_SIZE - this.queue.length
    const current = this.queue[this.currentIndex]
    if (needed <= 0 || !current) return
    const library = tracks || await this._libraryTracks()
    const exclude = new Set(this.queue.map(t => t.id))
    const lastQueued = this.queue[this.queue.length - 1]
    this.queue.push(...this._pickUpcoming(lastQueued || current, library, needed, exclude))
  }

  _setRadioMode(mode) {
    this.radioMode = mode ?? (safeStorage.get(STORAGE_KEYS.LAST_SEED_MODE) || null)
  }

  async startLocalSession({ currentTrack = null, radioMode, isPlaying = false, progressMs = 0 } = {}) {
    this._setRadioMode(radioMode)
    const tracks = await this._libraryTracks()
    const libraryIds = new Set(tracks.map(t => t.id))
    const current = currentTrack?.id
      ? (tracks.find(t => t.id === currentTrack.id) || currentTrack)
      : null
    const keepHistory = this.history.filter(t => libraryIds.has(t.id)).slice(-TARGET_INDEX)

    if (!current) {
      if (!tracks.length) {
        this.queue = []
        this.currentIndex = 0
        return null
      }
      const [first] = this._pickUpcoming(null, tracks, 1)
      this.queue = [first]
    } else {
      this.queue = [current]
    }
    this.history = []
    this.queue = [...keepHistory, ...this.queue]
    this.currentIndex = keepHistory.length
    this._remember(this.queue[this.currentIndex].id)
    await this._fillQueue(tracks)
    logger.info(`[OfflineBackend] Local session: ${tracks.length} downloaded tracks, queue ${this.queue.length}, mode ${this.radioMode || 'shuffle'}`)
    return this._state({ isPlaying, progressMs })
  }

  async getPlaybackState() {
    try {
      return await this.startLocalSession({ isPlaying: false })
    } catch (err) {
      logger.error('[OfflineBackend] getPlaybackState failed:', err)
      return null
    }
  }

  async play(trackId = null) {
    try {
      const tracks = await this._libraryTracks()
      if (!trackId) {
        if (this.queue[this.currentIndex]) return this._response()
        const state = await this.startLocalSession({ isPlaying: true })
        return state ? { status: 'playing', state, offline: true } : { status: 'error', offline: true, error: 'No downloaded tracks available' }
      }

      const existingIdx = this.queue.findIndex(t => t.id === trackId)
      if (existingIdx !== -1) {
        this.currentIndex = existingIdx
      } else {
        const track = tracks.find(t => t.id === trackId)
        if (!track) return { status: 'error', offline: true, error: 'Track is not downloaded' }
        if (!this.queue.length) {
          this.queue = [track]
          this.currentIndex = 0
        } else {
          this.queue.splice(this.currentIndex + 1, 0, track)
          this.currentIndex++
        }
      }

      this._remember(trackId)
      this._trimHistory()
      this.queue = this.queue.slice(0, this.currentIndex + 1).concat(
        this.queue.slice(this.currentIndex + 1).filter(t => t.id !== trackId)
      )
      await this._fillQueue(tracks)
      return this._response()
    } catch (err) {
      logger.error('[OfflineBackend] play() failed:', err)
      return { status: 'error', offline: true, error: err.message }
    }
  }

  async advanceTo(trackId) {
    const index = this.queue.findIndex((t, i) => i > this.currentIndex && t.id === trackId)
    if (index === -1) return this.play(trackId)
    this.currentIndex = index
    this._remember(trackId)
    this._trimHistory()
    await this._fillQueue()
    return this._response()
  }

  async next() {
    if (!this.queue.length) {
      const state = await this.startLocalSession({ isPlaying: true })
      return state ? { status: 'playing', state, offline: true } : { status: 'error', offline: true, error: 'No downloaded tracks available' }
    }
    if (this.currentIndex + 1 >= this.queue.length) await this._fillQueue()
    if (this.currentIndex + 1 >= this.queue.length) {
      return { status: 'error', offline: true, error: 'No next track' }
    }
    this.currentIndex++
    this._remember(this.queue[this.currentIndex].id)
    this._trimHistory()
    await this._fillQueue()
    return this._response()
  }

  async previous() {
    if (this.currentIndex > 0) {
      this.currentIndex--
    } else if (this.history.length) {
      this.queue.unshift(this.history.pop())
    } else {
      return { status: 'error', offline: true, error: 'No previous track' }
    }
    this._restoreHistory()
    this.queue = this.queue.slice(0, QUEUE_SIZE + TARGET_INDEX)
    return this._response()
  }

  async addToQueue(trackIds = []) {
    const tracks = await this._libraryTracks()
    const byId = new Map(tracks.map(t => [t.id, t]))
    const added = []
    let insertAt = this.currentIndex + 1
    for (const id of trackIds) {
      const track = byId.get(id)
      if (!track || this.queue.some(t => t.id === id)) continue
      this.queue.splice(insertAt++, 0, track)
      added.push(id)
    }
    return { status: 'ok', added, offline: true, state: this._state() }
  }

  async removeFromQueue(trackId) {
    const index = this.queue.findIndex((t, i) => i !== this.currentIndex && t.id === trackId)
    if (index !== -1) {
      this.queue.splice(index, 1)
      if (index < this.currentIndex) this.currentIndex--
      await this._fillQueue()
    }
    return { status: 'ok', offline: true, state: this._state() }
  }

  async seedRadio(category = 'all', trackId = null) {
    this.radioMode = category
    const tracks = await this._libraryTracks()
    const current = this.queue[this.currentIndex]
    const seed = (trackId && (tracks.find(t => t.id === trackId) || (current?.id === trackId ? current : null))) || current
    if (!seed) {
      const state = await this.startLocalSession({ radioMode: category, isPlaying: true })
      return { status: 'seeded', activeSeedMode: category, state, offline: true }
    }
    if (seed.id !== current?.id) {
      this.queue = [...this.queue.slice(0, this.currentIndex + 1), seed]
      this.currentIndex++
      this._remember(seed.id)
      this._trimHistory()
    } else {
      this.queue = this.queue.slice(0, this.currentIndex + 1)
    }
    await this._fillQueue(tracks)
    logger.info(`[OfflineBackend] Seeded local radio (${category}) from ${seed.title}`)
    return { status: 'seeded', activeSeedMode: category, state: this._state(), offline: true }
  }

  getHeaders(_includeAuth = true) {
    return {
      'Content-Type': 'application/json',
      'X-Offline-Mode': 'true',
    }
  }

  setToken(token) {
    this.token = token
  }
}

Object.assign(OfflineBackend.prototype, libraryMethods, writesMethods, unavailableMethods)

export const offlineBackend = new OfflineBackend()