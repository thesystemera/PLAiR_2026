import { logger } from './logger'
import { safeStorage } from './safeStorage'
import { cacheManager } from './cacheManager'
import { getDeviceId } from './session'

const QUEUE_SIZE = 11
const TARGET_INDEX = 5
const RECENT_LIMIT = 30
const MIN_FAVORITES_POOL = 3

const OFFLINE_MESSAGES = {
  voiceSearch: 'Voice search needs a connection to PLAiR. You can still type to search your downloads.',
  shoutouts: 'Shoutouts need a connection to PLAiR. They will be back when you are online.',
  reply: 'Replies need a connection to PLAiR. Please try again when you are back online.',
  review: 'Reviews need a connection to PLAiR. Please try again when you are back online.',
  deleteShoutout: 'Deleting a shoutout needs a connection to PLAiR. Please try again when you are back online.',
  radioMode: 'Radio Mode needs a connection to PLAiR. Your change was not saved.',
}

const STORAGE_KEYS = {
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

function readJson(key, fallback) {
  try {
    const data = safeStorage.get(key)
    return data ? JSON.parse(data) : fallback
  } catch (err) {
    logger.error(`[OfflineBackend] Failed to read ${key}:`, err)
    return fallback
  }
}

function cachedUserId() {
  try {
    const cached = safeStorage.get('cached_user')
    return cached ? JSON.parse(cached)?.id ?? null : null
  } catch {
    return null
  }
}

function writeJson(key, value) {
  try {
    safeStorage.set(key, JSON.stringify(value))
  } catch (err) {
    logger.error(`[OfflineBackend] Failed to save ${key}:`, err)
  }
}

function preferenceKey(type) {
  return type === 'track' ? STORAGE_KEYS.TRACK_PREFERENCES : STORAGE_KEYS.SHOUTOUT_PREFERENCES
}

function getOfflinePreferences(type = 'track') {
  return readJson(preferenceKey(type), {})
}

function saveOfflinePreferences(type = 'track', preferences) {
  writeJson(preferenceKey(type), preferences)
}

function idOf(item) {
  return typeof item === 'string' ? item : item?.id
}

function queueItem(track) {
  return {
    id: track.id,
    title: track.title || track.generation_params?.title || 'Untitled',
    artist_name: track.artist_name ?? track.generation_params?.artist_name ?? null,
    style: track.style || track.generation_params?.style || '',
    duration_ms: track.duration_ms || track.track_info?.duration || 0,
    has_artwork: !!track.has_artwork
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

  _validateCachedTrack(cached) {
    const issues = []

    if (!cached.trackId) {
      issues.push('Missing trackId')
    }

    if (!cached.metadata) {
      issues.push('Missing metadata')
    } else if (!cached.metadata.id) {
      issues.push('Missing metadata.id')
    }

    if (!cached.audioBlob) {
      issues.push('Missing audioBlob - track cannot play')
    } else if (!(cached.audioBlob instanceof Blob)) {
      issues.push('audioBlob is not a valid Blob')
    } else if (cached.audioBlob.size === 0) {
      issues.push('audioBlob is empty')
    }

    if (cached.artworkBlob && !(cached.artworkBlob instanceof Blob)) {
      issues.push('artworkBlob is not a valid Blob')
    }

    return {
      isValid: issues.length === 0,
      issues
    }
  }

  async validateCache(autoCleanup = false) {
    try {
      const allTracks = await cacheManager.getAllCachedTracks()
      const report = {
        total: allTracks.length,
        valid: 0,
        invalid: 0,
        issues: [],
        cleaned: 0
      }

      const corruptTrackIds = []

      for (const cached of allTracks) {
        const validation = this._validateCachedTrack(cached)

        if (validation.isValid) {
          report.valid++
        } else {
          report.invalid++
          report.issues.push({
            trackId: cached.trackId,
            trackTitle: cached.metadata?.title || 'Unknown',
            problems: validation.issues
          })
          corruptTrackIds.push(cached.trackId)
        }
      }

      if (autoCleanup && corruptTrackIds.length > 0) {
        logger.warn(`[OfflineBackend] Auto-cleanup: Removing ${corruptTrackIds.length} corrupt cache entries`)

        for (const trackId of corruptTrackIds) {
          try {
            await cacheManager.deleteTrack(trackId)
            report.cleaned++
          } catch (err) {
            logger.error(`[OfflineBackend] Failed to delete corrupt track ${trackId}:`, err)
          }
        }
      }

      if (report.invalid > 0) {
        logger.warn(`[OfflineBackend] Cache validation: ${report.valid} valid, ${report.invalid} invalid tracks`)
        logger.warn('[OfflineBackend] Invalid tracks:', report.issues)
      } else {
        logger.info(`[OfflineBackend] Cache validation: All ${report.total} tracks are valid`)
      }

      return report
    } catch (err) {
      logger.error('[OfflineBackend] validateCache failed:', err)
      return { total: 0, valid: 0, invalid: 0, issues: [], cleaned: 0, error: err.message }
    }
  }

  async getTracks(skip = 0, limit = 100, sortBy = 'created_at', order = 'desc', genre = null) {
    try {
      let tracks = await this._libraryTracks()

      if (genre) {
        const genreLower = genre.toLowerCase()
        tracks = tracks.filter(track => (track.derived_tags?.primary_genre || '').toLowerCase() === genreLower)
      }

      const valueOf = (track) => {
        switch (sortBy) {
          case 'created_at':
            return new Date(track.created_at || track._addedAt || 0).getTime()
          case 'title':
            return (track.title || '').toLowerCase()
          case 'genre':
            return (track.derived_tags?.primary_genre || '').toLowerCase()
          case 'play_count':
            return track.play_count || 0
          default:
            return 0
        }
      }

      const sorted = [...tracks].sort((a, b) => {
        const aVal = valueOf(a)
        const bVal = valueOf(b)
        if (aVal < bVal) return order === 'asc' ? -1 : 1
        if (aVal > bVal) return order === 'asc' ? 1 : -1
        return 0
      })

      const paginated = sorted.slice(skip, skip + limit)

      return {
        tracks: paginated,
        total_tracks: sorted.length,
        skip,
        limit,
        offline: true
      }
    } catch (err) {
      logger.error('[OfflineBackend] getTracks failed:', err)
      return { tracks: [], total_tracks: 0, skip, limit, offline: true }
    }
  }

  async getStats(genre = null) {
    try {
      const list = await cacheManager.getCachedTrackList()
      const genreLower = genre ? genre.toLowerCase() : null
      const entries = list.filter(entry => entry.playable &&
        (!genreLower || (entry.metadata?.derived_tags?.primary_genre || '').toLowerCase() === genreLower))

      let totalDuration = 0
      let instrumentalCount = 0
      let vocalCount = 0
      let totalSize = 0

      entries.forEach(entry => {
        const metadata = entry.metadata || {}
        totalDuration += metadata.duration_ms || 0
        totalSize += entry.size || 0
        if (metadata.generation_params?.instrumental) instrumentalCount++
        else vocalCount++
      })

      const formatDuration = (ms) => {
        const seconds = Math.floor(ms / 1000)
        const m = Math.floor(seconds / 60)
        const s = seconds % 60
        const h = Math.floor(m / 60)
        const mins = m % 60
        return h > 0 ? `${h}h ${mins}m` : `${mins}m ${s}s`
      }

      return {
        total_tracks: entries.length,
        total_duration_ms: totalDuration,
        total_duration_formatted: formatDuration(totalDuration),
        instrumental_count: instrumentalCount,
        vocal_count: vocalCount,
        total_size_bytes: totalSize,
        cached_offline: true
      }
    } catch (err) {
      logger.error('[OfflineBackend] getStats failed:', err)
      return {
        total_tracks: 0,
        total_duration_ms: 0,
        total_duration_formatted: '0m 0s',
        instrumental_count: 0,
        vocal_count: 0,
        total_size_bytes: 0,
        cached_offline: true
      }
    }
  }

  async getGenres() {
    try {
      const tracks = await this._libraryTracks()
      const genreCounts = {}
      const subGenresMap = {}

      tracks.forEach(track => {
        const derivedTags = track.derived_tags || {}
        const primaryGenre = derivedTags.primary_genre
        if (!primaryGenre) return
        genreCounts[primaryGenre] = (genreCounts[primaryGenre] || 0) + 1
        if (!subGenresMap[primaryGenre]) subGenresMap[primaryGenre] = new Set()
        ;(derivedTags.secondary_genres || []).forEach(sg => {
          if (sg) subGenresMap[primaryGenre].add(sg)
        })
      })

      const genres = Object.entries(genreCounts)
        .map(([genre, count]) => ({
          genre,
          count,
          sub_genres: Array.from(subGenresMap[genre] || []).sort()
        }))
        .sort((a, b) => b.count - a.count)

      return {
        genres,
        offline: true
      }
    } catch (err) {
      logger.error('[OfflineBackend] getGenres failed:', err)
      return { genres: [], offline: true }
    }
  }

  async searchSemantic(query, nResults = 100) {
    try {
      const tracks = await this._libraryTracks()

      if (!query || query.trim() === '') {
        return { results: tracks.slice(0, nResults), count: tracks.length, offline: true }
      }

      const terms = query.toLowerCase().split(/\s+/).filter(Boolean)
      const haystack = (track) => [
        track.title,
        track.artist_name,
        track.style,
        track.generation_params?.user_request,
        track.derived_tags?.primary_genre,
        ...(track.derived_tags?.secondary_genres || []),
        ...(track.derived_tags?.mood_keywords || []),
        ...(track.tags || []),
        typeof track.lyrics === 'string' ? track.lyrics : ''
      ].filter(Boolean).join(' ').toLowerCase()

      const matches = tracks.filter(track => {
        const text = haystack(track)
        return terms.every(term => text.includes(term))
      })

      logger.info(`[OfflineBackend] Search "${query}": ${matches.length} matches (offline)`)

      const results = matches.slice(0, nResults)
      return {
        results,
        count: results.length,
        offline: true
      }
    } catch (err) {
      logger.error('[OfflineBackend] searchSemantic failed:', err)
      return { results: [], count: 0, offline: true }
    }
  }

  async searchShoutouts(_query, _nResults = 20) {
    return {
      results: [],
      count: 0,
      message: 'Shoutouts need a connection',
      offline: true
    }
  }

  async getShoutout() {
    throw new Error(OFFLINE_MESSAGES.shoutouts)
  }

  async getShoutoutStats() {
    throw new Error(OFFLINE_MESSAGES.shoutouts)
  }

  async getShoutoutReplies() {
    return { replies: [], offline: true }
  }

  async uploadShoutoutReply() {
    throw new Error(OFFLINE_MESSAGES.reply)
  }

  async typeShoutoutReply() {
    throw new Error(OFFLINE_MESSAGES.reply)
  }

  async getMyCommunityPosts() {
    return { shoutouts: [], replies: [], reviews: [], offline: true }
  }

  async getTrackReviews(trackId) {
    return { reviews: [], count: 0, track_id: trackId, offline: true }
  }

  async uploadTrackReview() {
    throw new Error(OFFLINE_MESSAGES.review)
  }

  async typeTrackReview() {
    throw new Error(OFFLINE_MESSAGES.review)
  }

  async deleteShoutout() {
    throw new Error(OFFLINE_MESSAGES.deleteShoutout)
  }

  async getRadioMode() {
    throw new Error(OFFLINE_MESSAGES.radioMode)
  }

  async updateRadioMode() {
    throw new Error(OFFLINE_MESSAGES.radioMode)
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

  rememberPreferences(type, data) {
    if (!data || (type !== 'track' && type !== 'shoutout')) return
    const snapshot = {}
    const now = Date.now()
    const add = (items, prefType) => (items || []).forEach(item => {
      const id = idOf(item)
      if (id) snapshot[id] = { type: prefType, timestamp: now }
    })
    add(data.likes, 'like')
    add(data.super_likes, 'super_like')
    add(data.bans, 'ban')
    const pending = readJson(STORAGE_KEYS.PENDING_PREFERENCES, []).filter(op => op.type === type)
    pending.forEach(op => {
      if (op.preferenceType) snapshot[op.id] = { type: op.preferenceType, timestamp: op.at }
      else delete snapshot[op.id]
    })
    saveOfflinePreferences(type, snapshot)
  }

  _queuePendingWrite(type, id, preferenceType) {
    const pending = readJson(STORAGE_KEYS.PENDING_PREFERENCES, []).filter(op => !(op.type === type && op.id === id))
    pending.push({ type, id, preferenceType: preferenceType || null, at: Date.now() })
    writeJson(STORAGE_KEYS.PENDING_PREFERENCES, pending.slice(-500))
  }

  takePendingPreferenceWrites() {
    const pending = readJson(STORAGE_KEYS.PENDING_PREFERENCES, [])
    if (pending.length) safeStorage.remove(STORAGE_KEYS.PENDING_PREFERENCES)
    return pending
  }

  restorePendingPreferenceWrites(ops) {
    const current = readJson(STORAGE_KEYS.PENDING_PREFERENCES, [])
    const keys = new Set(current.map(op => `${op.type}:${op.id}`))
    writeJson(STORAGE_KEYS.PENDING_PREFERENCES, [...ops.filter(op => !keys.has(`${op.type}:${op.id}`)), ...current].slice(-500))
  }

  async setPreference(type, id, preferenceType) {
    try {
      const preferences = getOfflinePreferences(type)
      preferences[id] = {
        type: preferenceType,
        timestamp: Date.now(),
      }
      saveOfflinePreferences(type, preferences)
      this._queuePendingWrite(type, id, preferenceType)

      logger.info(`[OfflineBackend] Set ${type} preference ${preferenceType} for ${id}`)

      const idKey = type === 'track' ? 'track_id' : 'shoutout_id'
      return {
        status: 'ok',
        [idKey]: id,
        preference_type: preferenceType,
        offline: true,
      }
    } catch (err) {
      logger.error(`[OfflineBackend] setPreference failed for ${type}:`, err)
      throw err
    }
  }

  async removePreference(type, id) {
    try {
      const preferences = getOfflinePreferences(type)
      delete preferences[id]
      saveOfflinePreferences(type, preferences)
      this._queuePendingWrite(type, id, null)

      logger.info(`[OfflineBackend] Removed ${type} preference for ${id}`)

      const idKey = type === 'track' ? 'track_id' : 'shoutout_id'
      return {
        status: 'ok',
        [idKey]: id,
        offline: true,
      }
    } catch (err) {
      logger.error(`[OfflineBackend] removePreference failed for ${type}:`, err)
      throw err
    }
  }

  async getUserPreferences(type) {
    try {
      const preferences = getOfflinePreferences(type)

      const grouped = {
        likes: [],
        super_likes: [],
        bans: []
      }

      Object.entries(preferences).forEach(([id, data]) => {
        if (data.type === 'like') {
          grouped.likes.push(id)
        } else if (data.type === 'super_like') {
          grouped.super_likes.push(id)
        } else if (data.type === 'ban') {
          grouped.bans.push(id)
        }
      })

      return grouped
    } catch (err) {
      logger.error(`[OfflineBackend] getUserPreferences failed for ${type}:`, err)
      return { likes: [], super_likes: [], bans: [] }
    }
  }

  async getAudioFeatures(trackId) {
    try {
      const cached = await cacheManager.getCachedTrack(trackId)
      if (!cached || !cached.audioFeatures) {
        throw new Error('Audio features not cached for this track')
      }

      logger.info(`[OfflineBackend] Audio features loaded from cache: ${trackId}`)
      return cached.audioFeatures
    } catch (err) {
      logger.error(`[OfflineBackend] getAudioFeatures(${trackId}) failed:`, err)
      throw err
    }
  }

  async getLyricTimestamps(trackId) {
    try {
      const cached = await cacheManager.getCachedTrack(trackId)
      if (!cached || !cached.metadata?.lyric_timestamps) {
        return null
      }

      logger.info(`[OfflineBackend] Lyric timestamps loaded from cache: ${trackId}`)
      return cached.metadata.lyric_timestamps
    } catch (err) {
      logger.error(`[OfflineBackend] getLyricTimestamps(${trackId}) failed:`, err)
      return null
    }
  }

  async getVideoClips(trackId) {
    logger.info(`[OfflineBackend] Video clips not available offline for ${trackId}`)
    return { clips: [], reason: 'offline', offline: true }
  }

  async getUserUploads() {
    logger.info('[OfflineBackend] User uploads not available offline')
    return { tracks: [], offline: true }
  }

  async trackShoutoutPlay() {
    return { ok: true, offline: true }
  }

  async getTrackAnalytics() {
    return null
  }

  async getShoutoutAnalytics() {
    return null
  }

  async transcribe() {
    throw new Error(OFFLINE_MESSAGES.voiceSearch)
  }

  async deleteUserTrack() {
    throw new Error('Deleting tracks requires an internet connection')
  }

  async uploadProfilePicture() {
    throw new Error('Uploading profile picture requires an internet connection')
  }

  async deleteProfilePicture() {
    throw new Error('Deleting profile picture requires an internet connection')
  }

  async createStripeCheckout() {
    throw new Error('Payment requires an internet connection')
  }

  async createBillingPortal() {
    throw new Error('Managing your subscription requires an internet connection')
  }

  async getBillingStatus() {
    throw new Error('Subscription status requires an internet connection')
  }

  async uploadTrackArtwork() {
    throw new Error('Uploading artwork requires an internet connection')
  }

  async uploadMusic() {
    throw new Error('Uploading music requires an internet connection')
  }

  async getUploadSetup() {
    return { artists: [], last_artist_profile_id: null, upload_enhance: false, offline: true }
  }

  async createArtist() {
    throw new Error('Adding an artist requires an internet connection')
  }

  async updateArtist() {
    throw new Error('Editing an artist requires an internet connection')
  }

  async deleteArtist() {
    throw new Error('Deleting an artist requires an internet connection')
  }

  async updateUserTrack() {
    throw new Error('Editing a track requires an internet connection')
  }

  queueProfileUpdate(updates) {
    const userId = cachedUserId()
    if (userId === null) throw new Error('Sign in to save settings')
    const pending = readJson(STORAGE_KEYS.PENDING_PROFILE, null)
    const base = pending && pending.userId === userId ? pending.updates : {}
    writeJson(STORAGE_KEYS.PENDING_PROFILE, { userId, updates: { ...base, ...updates } })
    logger.info('[OfflineBackend] Settings change queued, will sync when the server is back:', Object.keys(updates).join(', '))
  }

  pendingProfileUpdates(userId) {
    const pending = readJson(STORAGE_KEYS.PENDING_PROFILE, null)
    return pending && pending.userId === userId ? pending.updates : null
  }

  clearPendingProfileKeys(keys) {
    const pending = readJson(STORAGE_KEYS.PENDING_PROFILE, null)
    if (!pending) return
    const updates = Object.fromEntries(Object.entries(pending.updates || {}).filter(([key]) => !keys.includes(key)))
    if (Object.keys(updates).length) writeJson(STORAGE_KEYS.PENDING_PROFILE, { ...pending, updates })
    else safeStorage.remove(STORAGE_KEYS.PENDING_PROFILE)
  }

  takePendingProfileWrites() {
    const pending = readJson(STORAGE_KEYS.PENDING_PROFILE, null)
    if (pending) safeStorage.remove(STORAGE_KEYS.PENDING_PROFILE)
    return pending
  }

  restorePendingProfileWrites(entry) {
    const current = readJson(STORAGE_KEYS.PENDING_PROFILE, null)
    if (current && current.userId !== entry.userId) return
    writeJson(STORAGE_KEYS.PENDING_PROFILE, { userId: entry.userId, updates: { ...entry.updates, ...(current?.updates || {}) } })
  }

  async updateAudioQuality(audioQuality) {
    this.queueProfileUpdate({ audio_quality: audioQuality })
    return { status: 'queued', audio_quality: audioQuality, offline: true }
  }

  async updateUserProfile(updates) {
    this.queueProfileUpdate(updates)
    return { status: 'queued', offline: true }
  }

  async updateUsername(_username) {
    logger.warn('[OfflineBackend] Username updates require an internet connection')
    return {
      status: 'error',
      error: 'Username updates require an internet connection',
      offline: true
    }
  }

  async register(_username, _password) {
    throw new Error('Creating an account needs a connection to PLAiR. Please try again when you are back online.')
  }

  async login(_username, _password) {
    throw new Error('Signing in needs a connection to PLAiR. Please try again when you are back online.')
  }

  async getMe() {
    try {
      const cachedUser = safeStorage.get('cached_user')
      if (cachedUser) {
        const userData = JSON.parse(cachedUser)
        logger.info('[OfflineBackend] Returning cached user data')
        return userData
      }

      logger.warn('[OfflineBackend] No cached user data available')
      return {
        status: 'error',
        error: 'No cached user data available. Please log in when online.',
        offline: true
      }
    } catch (err) {
      logger.error('[OfflineBackend] getMe failed:', err)
      return {
        status: 'error',
        error: 'Authentication check requires an internet connection',
        offline: true
      }
    }
  }

  async generate(_params) {
    logger.warn('[OfflineBackend] Music generation requires an internet connection')
    return {
      status: 'error',
      error: '🎵 Music generation requires an internet connection',
      offline: true
    }
  }

  async cancelGenerationJob(_jobId) {
    logger.warn('[OfflineBackend] Generation job cancellation requires an internet connection')
    return {
      status: 'error',
      error: 'Generation job cancellation requires an internet connection',
      offline: true
    }
  }

  async getGenerationJobs() {
    logger.info('[OfflineBackend] Generation jobs not available offline')
    return {
      jobs: [],
      message: 'Generation jobs require an internet connection',
      offline: true
    }
  }

  async djTalk(_params) {
    const offlineResponses = [
      "We're off air while PLAiR is offline, so we can't hear you right now. Your downloads keep playing, and we'll be back the moment the connection is.",
      "The studio line is down for a bit. Keep enjoying your downloads, and talk to us again when you're back online.",
      "No signal to the studio right now. The music keeps going from your downloads, and we'll pick up the chat when PLAiR is reachable again.",
    ]

    const randomResponse = offlineResponses[Math.floor(Math.random() * offlineResponses.length)]

    logger.info('[OfflineBackend] DJ chat attempted offline:', randomResponse)

    return {
      response: randomResponse,
      offline: true,
      sentiment: 'apologetic',
    }
  }

  async getConversationHistory(_limit = 3) {
    // Return empty history offline
    return {
      conversations: [],
      offline: true,
    }
  }

  async deleteConversationHistory() {
    logger.warn('[OfflineBackend] Conversation management requires an internet connection')
    return {
      status: 'error',
      error: 'Conversation management requires an internet connection',
      offline: true
    }
  }

  async resetPersona() {
    logger.warn('[OfflineBackend] Persona reset requires an internet connection')
    return {
      status: 'error',
      error: 'Persona reset requires an internet connection',
      offline: true
    }
  }

  async getDevices() {
    logger.info('[OfflineBackend] Device management not available offline')
    return {
      devices: [],
      message: 'Device management requires an internet connection',
      offline: true
    }
  }

  async activateDevice(_deviceId) {
    logger.warn('[OfflineBackend] Device activation requires an internet connection')
    return {
      status: 'error',
      error: 'Device activation requires an internet connection',
      offline: true
    }
  }

  async renameDevice(_deviceId, _newName) {
    logger.warn('[OfflineBackend] Device management requires an internet connection')
    return {
      status: 'error',
      error: 'Device management requires an internet connection',
      offline: true
    }
  }

  async removeDevice(_deviceId) {
    logger.warn('[OfflineBackend] Device management requires an internet connection')
    return {
      status: 'error',
      error: 'Device management requires an internet connection',
      offline: true
    }
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

export const offlineBackend = new OfflineBackend()