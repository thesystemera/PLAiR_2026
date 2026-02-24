import { logger } from './logger'
import { cacheManager } from './cacheManager'
import { getDeviceId } from './session'

const QUEUE_SIZE = 11
const TARGET_INDEX = 5

const STORAGE_KEYS = {
  TRACK_PREFERENCES: 'offline_track_preferences',
  SHOUTOUT_PREFERENCES: 'offline_shoutout_preferences',
  AUDIO_QUALITY: 'offline_audio_quality',
}

function getOfflinePreferences(type = 'track') {
  try {
    const key = type === 'track' ? STORAGE_KEYS.TRACK_PREFERENCES : STORAGE_KEYS.SHOUTOUT_PREFERENCES
    const data = localStorage.getItem(key)
    return data ? JSON.parse(data) : {}
  } catch (err) {
    logger.error(`[OfflineBackend] Failed to load ${type} preferences:`, err)
    return {}
  }
}

function saveOfflinePreferences(type = 'track', preferences) {
  try {
    const key = type === 'track' ? STORAGE_KEYS.TRACK_PREFERENCES : STORAGE_KEYS.SHOUTOUT_PREFERENCES
    localStorage.setItem(key, JSON.stringify(preferences))
  } catch (err) {
    logger.error(`[OfflineBackend] Failed to save ${type} preferences:`, err)
  }
}

class OfflineBackend {
  constructor() {
    this.queue = []
    this.currentIndex = TARGET_INDEX
    this.history = []
  }

  async getPlaybackState() {
    try {
      logger.info('[OfflineBackend] Getting initial playback state for offline cold boot')

      const cachedTracks = await cacheManager.getCachedTrackList()

      if (!cachedTracks || cachedTracks.length === 0) {
        logger.info('[OfflineBackend] No cached tracks available')
        return null
      }

      const preferences = getOfflinePreferences('track')
      const likedTrackIds = new Set(
        Object.entries(preferences)
          .filter(([, data]) => data.type === 'like' || data.type === 'super_like')
          .map(([id]) => id)
      )

      let sortedTracks = cachedTracks.map(t => ({
        ...t.metadata,
        isLiked: likedTrackIds.has(t.trackId)
      }))

      sortedTracks.sort((a, b) => {
        if (a.isLiked && !b.isLiked) return -1
        if (!a.isLiked && b.isLiked) return 1
        return 0
      })

      const shuffled = [...sortedTracks].sort(() => Math.random() - 0.5)
      this.queue = shuffled.slice(0, QUEUE_SIZE)
      this.currentIndex = 0

      const currentTrack = this.queue[0]
      if (!currentTrack) {
        logger.warn('[OfflineBackend] Failed to select initial track')
        return null
      }

      logger.info(`[OfflineBackend] Initialized with ${this.queue.length} tracks, starting with: ${currentTrack.title}`)

      return {
        current_track: currentTrack,
        queue: this.queue,
        current_index: this.currentIndex,
        is_playing: false,
        progress_ms: 0,
        active_device_id: getDeviceId(),
        radio_mode: 'favorites'
      }
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
    } else {

      if (!cached.metadata.id) {
        issues.push('Missing metadata.id')
      }
    }

    if (!cached.audioBlob) {
      issues.push('Missing audioBlob - track cannot play')
    } else if (!(cached.audioBlob instanceof Blob)) {
      issues.push('audioBlob is not a valid Blob')
    }

    if (cached.metadata?.has_artwork && !cached.artworkBlob) {
      issues.push('Metadata claims has_artwork but artworkBlob is missing')
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
            trackTitle: cached.metadata?.generation_params?.title || 'Unknown',
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

  _simplifyTrackForQueue(track) {
    const params = track.generation_params || {}
    const trackInfo = track.track_info || {}

    return {
      id: track.id,
      title: params.title || 'Unknown',
      artist_name: params.artist_name,
      style: params.style || '',
      duration_ms: trackInfo.duration || track.duration_ms || 0,
      has_artwork: track.has_artwork || false
    }
  }

  async getTracks(skip = 0, limit = 100, sortBy = 'created_at', order = 'desc', genre = null) {
    try {
      const allTracks = await cacheManager.getAllCachedTracks()

      if (!allTracks || allTracks.length === 0) {
        return { tracks: [], total: 0, skip, limit }
      }

      let invalidCount = 0

      let tracks = allTracks
        .map(cached => {
          const validation = this._validateCachedTrack(cached)

          if (!validation.isValid) {
            invalidCount++
            logger.warn(`[OfflineBackend] Skipping invalid cache entry ${cached.trackId}:`, validation.issues)
            return null
          }

          return {
            ...cached.metadata,
            has_artwork: !!cached.artworkBlob
          }
        })
        .filter(Boolean)

      if (genre) {
        const genreLower = genre.toLowerCase()
        tracks = tracks.filter(track => {
          const primaryGenre = track.derived_tags?.primary_genre || ''
          return primaryGenre.toLowerCase() === genreLower
        })
        logger.info(`[OfflineBackend] Filtered by genre "${genre}": ${tracks.length} tracks`)
      }

      if (invalidCount > 0) {
        logger.warn(`[OfflineBackend] Found ${invalidCount} invalid cache entries. Consider running validateCache(true) to clean up.`)
      }

      const sorted = [...tracks].sort((a, b) => {
        let aVal, bVal

        switch (sortBy) {
          case 'created_at':
            aVal = new Date(a.created_at || 0).getTime()
            bVal = new Date(b.created_at || 0).getTime()
            break
          case 'title':
            aVal = (a.title || '').toLowerCase()
            bVal = (b.title || '').toLowerCase()
            break
          case 'play_count':
            aVal = a.play_count || 0
            bVal = b.play_count || 0
            break
          default:
            return 0
        }

        if (aVal < bVal) return order === 'asc' ? -1 : 1
        if (aVal > bVal) return order === 'asc' ? 1 : -1
        return 0
      })

      const paginated = sorted.slice(skip, skip + limit)

      logger.info(`[OfflineBackend] getTracks: ${paginated.length}/${sorted.length} tracks (offline cache)`)

      return {
        tracks: paginated,
        total_tracks: sorted.length,  // Match backend key name
        skip,
        limit,
      }
    } catch (err) {
      logger.error('[OfflineBackend] getTracks failed:', err)
      return { tracks: [], total_tracks: 0, skip, limit }
    }
  }

  async getStats() {
    try {
      const allTracks = await cacheManager.getAllCachedTracks()

      let totalDuration = 0
      let instrumentalCount = 0
      let vocalCount = 0
      let totalSize = 0

      allTracks.forEach(cached => {
        const metadata = cached.metadata || {}
        const trackInfo = metadata.track_info || {}
        const genParams = metadata.generation_params || {}

        totalDuration += trackInfo.duration || 0
        totalSize += cached.size || 0

        if (genParams.instrumental) {
          instrumentalCount++
        } else {
          vocalCount++
        }
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
        total_tracks: allTracks.length,
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
      const allTracks = await cacheManager.getAllCachedTracks()
      const genreCounts = {}
      const subGenresMap = {}

      allTracks.forEach(cached => {
        const derivedTags = cached.metadata?.derived_tags || {}
        const primaryGenre = derivedTags.primary_genre
        const secondaryGenres = derivedTags.secondary_genres || []

        if (primaryGenre) {
          genreCounts[primaryGenre] = (genreCounts[primaryGenre] || 0) + 1

          if (!subGenresMap[primaryGenre]) {
            subGenresMap[primaryGenre] = new Set()
          }

          secondaryGenres.forEach(sg => {
            if (sg) subGenresMap[primaryGenre].add(sg)
          })
        }
      })

      const genres = Object.entries(genreCounts)
        .map(([genre, count]) => ({
          genre,
          count,
          sub_genres: Array.from(subGenresMap[genre] || []).sort()
        }))
        .sort((a, b) => b.count - a.count)

      logger.info(`[OfflineBackend] Found ${genres.length} genres in cached tracks`)

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
      const allTracks = await cacheManager.getAllCachedTracks()

      const tracks = allTracks
        .map(cached => {
          const validation = this._validateCachedTrack(cached)
          if (!validation.isValid) {
            return null
          }

          return {
            ...cached.metadata,
            has_artwork: !!cached.artworkBlob,
            has_enriched_artwork: !!cached.enrichedArtworkBlob
          }
        })
        .filter(Boolean)

      if (!query || query.trim() === '') {
        return { results: tracks.slice(0, nResults), count: tracks.length }
      }

      const lowerQuery = query.toLowerCase()

      const matches = tracks.filter(track => {
        const params = track.generation_params || {}
        if (params.title?.toLowerCase().includes(lowerQuery)) return true
        if (params.style?.toLowerCase().includes(lowerQuery)) return true
        if (track.tags?.some(tag => tag.toLowerCase().includes(lowerQuery))) return true
        if (track.lyrics?.toLowerCase().includes(lowerQuery)) return true
        return !!params.user_request?.toLowerCase().includes(lowerQuery);


      })

      logger.info(`[OfflineBackend] Search "${query}": ${matches.length} matches (offline)`)

      const results = matches.slice(0, nResults)
      return {
        results,
        count: results.length,
      }
    } catch (err) {
      logger.error('[OfflineBackend] searchSemantic failed:', err)
      return { results: [], count: 0 }
    }
  }

  async searchShoutouts(_query, _nResults = 20) {
    logger.info('[OfflineBackend] searchShoutouts: Shoutouts not cached yet (future feature)')

    return {
      results: [],
      count: 0,
      message: 'Shoutout search requires an internet connection',
      offline: true
    }
  }

  _scoreTrackSimilarity(seedTrack, candidateTrack) {
    let score = 0

    const seedParams = seedTrack.generation_params || {}
    const candidateParams = candidateTrack.generation_params || {}

    const seedStyle = seedParams.style?.toLowerCase() || ''
    const candidateStyle = candidateParams.style?.toLowerCase() || ''

    if (seedStyle && candidateStyle) {
      if (seedStyle === candidateStyle) {
        score += 0.5
      } else if (seedStyle.includes(candidateStyle) || candidateStyle.includes(seedStyle)) {
        score += 0.3
      }
    }

    const seedTags = seedTrack.tags || []
    const candidateTags = candidateTrack.tags || []
    if (seedTags.length > 0 && candidateTags.length > 0) {
      const seedTagsLower = seedTags.map(t => t.toLowerCase())
      const candidateTagsLower = candidateTags.map(t => t.toLowerCase())
      const overlap = seedTagsLower.filter(t => candidateTagsLower.includes(t)).length
      const maxTags = Math.max(seedTags.length, candidateTags.length)
      score += (overlap / maxTags) * 0.3
    }

    const seedTitle = seedParams.title?.toLowerCase() || ''
    const candidateTitle = candidateParams.title?.toLowerCase() || ''
    if (seedTitle && candidateTitle) {
      const seedWords = seedTitle.split(/\s+/)
      const candidateWords = candidateTitle.split(/\s+/)
      const commonWords = seedWords.filter(w => w.length > 3 && candidateWords.includes(w)).length
      if (commonWords > 0) {
        score += commonWords * 0.05
      }
    }

    return score
  }

  _getOfflineRecommendations(seedTrack, allTracks, count, excludeIds = new Set()) {
    const preferences = getOfflinePreferences()
    const likedIds = new Set()
    const superLikedIds = new Set()
    const bannedIds = new Set()

    Object.entries(preferences).forEach(([trackId, data]) => {
      if (data.type === 'like') likedIds.add(trackId)
      else if (data.type === 'super_like') superLikedIds.add(trackId)
      else if (data.type === 'ban') bannedIds.add(trackId)
    })

    const scored = allTracks
      .filter(track => {
        if (!track.id || track.id === seedTrack.id) return false
        if (excludeIds.has(track.id)) return false
        return !bannedIds.has(track.id);

      })
      .map(track => {
        let score = this._scoreTrackSimilarity(seedTrack, track)

        if (superLikedIds.has(track.id)) {
          score += 0.3
        } else if (likedIds.has(track.id)) {
          score += 0.15
        }

        return { track, score }
      })

    scored.sort((a, b) => b.score - a.score)

    return scored.slice(0, count).map(item => item.track)
  }

  async play(trackId = null) {
    logger.info(`[OfflineBackend] play(${trackId}) - building smart offline queue`)

    try {
      const allTracks = await cacheManager.getAllCachedTracks()

      const tracks = allTracks
        .map(cached => {
          const validation = this._validateCachedTrack(cached)
          if (!validation.isValid) {
            return null
          }

          return {
            ...cached.metadata,
            has_artwork: !!cached.artworkBlob,
            has_enriched_artwork: !!cached.enrichedArtworkBlob
          }
        })
        .filter(Boolean)

      if (tracks.length === 0) {
        return { status: 'error', offline: true, error: 'No cached tracks available' }
      }

      let currentTrack = trackId ? tracks.find(t => t.id === trackId) : tracks[0]
      if (!currentTrack) {
        currentTrack = tracks[0]
      }

      const existingIdx = this.queue.findIndex(t => t.id === currentTrack.id)

      if (existingIdx !== -1) {
        logger.info(`[OfflineBackend] Track already in queue at index ${existingIdx}, shifting to target`)
        this.currentIndex = existingIdx
        this._shiftQueueToTarget()

        await this._autoFillQueue()
      } else {
        const queueTracks = []
        const excludeIds = new Set([currentTrack.id])

        const recommendations = this._getOfflineRecommendations(
          currentTrack,
          tracks,
          QUEUE_SIZE - 1,
          excludeIds
        )

        const beforeCurrent = recommendations.slice(0, TARGET_INDEX)
        const afterCurrent = recommendations.slice(TARGET_INDEX)

        queueTracks.push(...beforeCurrent)
        queueTracks.push(currentTrack)
        queueTracks.push(...afterCurrent)

        if (queueTracks.length < QUEUE_SIZE) {
          const remaining = tracks.filter(t => !excludeIds.has(t.id) && !queueTracks.some(q => q.id === t.id))
          const shuffled = remaining.sort(() => Math.random() - 0.5)
          queueTracks.push(...shuffled.slice(0, QUEUE_SIZE - queueTracks.length))
        }

        this.queue = queueTracks
        this.currentIndex = TARGET_INDEX

        logger.info(`[OfflineBackend] Built new queue: ${this.queue.length} tracks at index ${TARGET_INDEX}`)
      }

      const simplifiedQueue = this.queue.map(t => this._simplifyTrackForQueue(t))
      const currentTrackData = this.queue[this.currentIndex]

      return {
        status: 'playing',
        state: {
          current_track: currentTrackData,
          queue: simplifiedQueue,
          current_index: this.currentIndex,
          is_playing: true,
          progress_ms: 0,
          active_device_id: getDeviceId()
        },
        offline: true
      }
    } catch (err) {
      logger.error('[OfflineBackend] play() failed:', err)
      return { status: 'error', offline: true, error: err.message }
    }
  }

  _shiftQueueToTarget() {
    while (this.currentIndex > TARGET_INDEX && this.queue.length > 0) {
      const removed = this.queue.shift()
      this.history.push(removed)
      this.currentIndex--

      if (this.history.length > 50) {
        this.history.shift()
      }
    }
  }

  async _autoFillQueue() {
    if (this.queue.length >= QUEUE_SIZE) {
      return
    }

    try {
      const allTracks = await cacheManager.getAllCachedTracks()
      const tracks = allTracks
        .map(cached => {
          const validation = this._validateCachedTrack(cached)
          if (!validation.isValid) return null
          return { ...cached.metadata, has_artwork: !!cached.artworkBlob }
        })
        .filter(Boolean)

      const currentTrack = this.queue[this.currentIndex]
      if (!currentTrack) return

      const existingIds = new Set([...this.queue.map(t => t.id), ...this.history.slice(-20).map(t => t.id)])
      const needed = QUEUE_SIZE - this.queue.length

      const recommendations = this._getOfflineRecommendations(
        currentTrack,
        tracks,
        needed,
        existingIds
      )

      this.queue.push(...recommendations)

      if (this.queue.length < QUEUE_SIZE) {
        const remaining = tracks.filter(t => !existingIds.has(t.id) && !this.queue.some(q => q.id === t.id))
        const shuffled = remaining.sort(() => Math.random() - 0.5)
        this.queue.push(...shuffled.slice(0, QUEUE_SIZE - this.queue.length))
      }

      logger.info(`[OfflineBackend] Auto-filled queue: ${this.queue.length} tracks`)
    } catch (err) {
      logger.error('[OfflineBackend] Auto-fill failed:', err)
    }
  }

  async next() {
    logger.info('[OfflineBackend] next() called')

    if (this.queue.length === 0) {
      return { status: 'error', offline: true, error: 'Queue is empty' }
    }

    if (this.currentIndex + 1 < this.queue.length) {
      this.currentIndex++
    } else {
      logger.warn('[OfflineBackend] Already at end of queue')
      return { status: 'error', offline: true, error: 'No next track' }
    }

    this._shiftQueueToTarget()

    await this._autoFillQueue()

    const simplifiedQueue = this.queue.map(t => this._simplifyTrackForQueue(t))
    const currentTrackData = this.queue[this.currentIndex]

    logger.info(`[OfflineBackend] Next: now at index ${this.currentIndex}, queue size ${this.queue.length}`)

    return {
      status: 'playing',
      state: {
        current_track: currentTrackData,
        queue: simplifiedQueue,
        current_index: this.currentIndex,
        is_playing: true,
        progress_ms: 0,
        active_device_id: getDeviceId()
      },
      offline: true
    }
  }

  async previous() {
    logger.info('[OfflineBackend] previous() called')

    if (this.history.length === 0) {
      logger.warn('[OfflineBackend] No history available')
      return { status: 'error', offline: true, error: 'No previous track' }
    }

    const prevTrack = this.history.pop()
    this.queue.unshift(prevTrack)
    this.currentIndex++

    logger.info(`[OfflineBackend] Previous: restored from history, now at index ${this.currentIndex}`)

    const simplifiedQueue = this.queue.map(t => this._simplifyTrackForQueue(t))
    const currentTrackData = this.queue[this.currentIndex]

    return {
      status: 'playing',
      state: {
        current_track: currentTrackData,
        queue: simplifiedQueue,
        current_index: this.currentIndex,
        is_playing: true,
        progress_ms: 0,
        active_device_id: getDeviceId()
      },
      offline: true
    }
  }

  async addToQueue(_trackIds) {
    logger.warn('[OfflineBackend] addToQueue not fully supported in offline mode - queue managed by PlaybackContext')

    return {
      status: 'ok',
      message: 'Queue changes not persisted in offline mode',
      offline: true,
    }
  }

  async removeFromQueue(_trackId) {
    logger.warn('[OfflineBackend] removeFromQueue not fully supported in offline mode - queue managed by PlaybackContext')

    return {
      status: 'ok',
      message: 'Queue changes not persisted in offline mode',
      offline: true,
    }
  }

  async seedRadio(category = 'all', trackId = null) {
    try {
      logger.info(`[OfflineBackend] seedRadio(category: ${category}, trackId: ${trackId})`)

      const allTracks = await cacheManager.getAllCachedTracks()

      const tracks = allTracks
        .map(cached => {
          const validation = this._validateCachedTrack(cached)
          if (!validation.isValid) {
            return null
          }

          return {
            ...cached.metadata,
            has_artwork: !!cached.artworkBlob,
            has_enriched_artwork: !!cached.enrichedArtworkBlob
          }
        })
        .filter(Boolean)

      let currentTrack = null

      if (trackId) {
        const seedTrack = tracks.find(t => t.id === trackId)
        if (seedTrack) {
          currentTrack = seedTrack
          const excludeIds = new Set([seedTrack.id])

          const recommendations = this._getOfflineRecommendations(
            seedTrack,
            tracks,
            QUEUE_SIZE - 1,
            excludeIds
          )

          const beforeCurrent = recommendations.slice(0, TARGET_INDEX)
          const afterCurrent = recommendations.slice(TARGET_INDEX)

          const queueTracks = []
          queueTracks.push(...beforeCurrent)
          queueTracks.push(currentTrack)
          queueTracks.push(...afterCurrent)

          if (queueTracks.length < QUEUE_SIZE) {
            const remaining = tracks.filter(t => !excludeIds.has(t.id) && !queueTracks.some(q => q.id === t.id))
            const shuffled = remaining.sort(() => Math.random() - 0.5)
            queueTracks.push(...shuffled.slice(0, QUEUE_SIZE - queueTracks.length))
          }

          this.queue = queueTracks
          this.currentIndex = TARGET_INDEX
          this.history = []

          logger.info(`[OfflineBackend] Seeded radio: ${this.queue.length} tracks at index ${TARGET_INDEX} (category: ${category})`)
        }
      }

      const simplifiedQueue = this.queue.map(t => this._simplifyTrackForQueue(t))
      const currentTrackData = this.queue[this.currentIndex]

      return {
        status: 'seeded',
        activeSeedMode: category,
        state: {
          current_track: currentTrackData,
          queue: simplifiedQueue,
          current_index: this.currentIndex,
          is_playing: true,
          progress_ms: 0,
          active_device_id: getDeviceId()
        },
        offline: true
      }
    } catch (err) {
      logger.error('[OfflineBackend] seedRadio failed:', err)
      throw err
    }
  }

  async setPreference(type, id, preferenceType) {
    try {
      const preferences = getOfflinePreferences(type)
      preferences[id] = {
        type: preferenceType,
        timestamp: Date.now(),
      }
      saveOfflinePreferences(type, preferences)

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
    throw new Error('Transcription requires an internet connection')
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

  async getStripeKey() {
    throw new Error('Payment requires an internet connection')
  }

  async uploadTrackArtwork() {
    throw new Error('Uploading artwork requires an internet connection')
  }

  async uploadMusic() {
    throw new Error('Uploading music requires an internet connection')
  }

  async updateAudioQuality(audioQuality) {
    try {
      localStorage.setItem(STORAGE_KEYS.AUDIO_QUALITY, audioQuality)
      logger.info(`[OfflineBackend] Audio quality set to ${audioQuality} (will sync when online)`)

      return {
        status: 'ok',
        audio_quality: audioQuality,
        offline: true,
      }
    } catch (err) {
      logger.error('[OfflineBackend] updateAudioQuality failed:', err)
      throw err
    }
  }

  async updateUserProfile(_updates) {
    logger.warn('[OfflineBackend] Profile updates require an internet connection')
    return {
      status: 'error',
      error: 'Profile updates require an internet connection',
      offline: true
    }
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
    logger.warn('[OfflineBackend] Registration requires an internet connection')
    return {
      status: 'error',
      error: 'Registration requires an internet connection. Please connect to the internet to create an account.',
      offline: true
    }
  }

  async login(username, password) {
    logger.warn('[OfflineBackend] Login requires an internet connection')
    return {
      status: 'error',
      error: 'Login requires an internet connection. Please connect to the internet to sign in.',
      offline: true
    }
  }

  async getMe() {
    try {
      const cachedUser = localStorage.getItem('cached_user')
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

  async generate({ type, userRequest, sourceTrackId, batchCount }) {
    logger.warn('[OfflineBackend] Music generation requires an internet connection')
    return {
      status: 'error',
      error: '🎵 Music generation requires an internet connection',
      offline: true
    }
  }

  async cancelGenerationJob(jobId) {
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

  async djTalk({ audio, text, context, voice_name }) {
    const offlineResponses = [
      "Sorry buddy, I'm currently offline! 😅 Hit me up when you're back on the grid.",
      "Yo! I'm in airplane mode right now. Can't chat but the beats are still playing! ✈️",
      "DJ is off the air at the moment. We'll be back after these messages... (when you're online)",
      "🎧 *static noises* Sorry, the signal's down. The music keeps spinning though!",
      "No internet, no DJ chat. But hey, at least the tracks are cached! 💾",
    ]

    const randomResponse = offlineResponses[Math.floor(Math.random() * offlineResponses.length)]

    logger.info('[OfflineBackend] DJ chat attempted offline:', randomResponse)

    return {
      response: randomResponse,
      offline: true,
      sentiment: 'apologetic',
    }
  }

  async getConversationHistory(limit = 3) {
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

  async activateDevice(deviceId) {
    logger.warn('[OfflineBackend] Device activation requires an internet connection')
    return {
      status: 'error',
      error: 'Device activation requires an internet connection',
      offline: true
    }
  }

  async renameDevice(deviceId, newName) {
    logger.warn('[OfflineBackend] Device management requires an internet connection')
    return {
      status: 'error',
      error: 'Device management requires an internet connection',
      offline: true
    }
  }

  async removeDevice(deviceId) {
    logger.warn('[OfflineBackend] Device management requires an internet connection')
    return {
      status: 'error',
      error: 'Device management requires an internet connection',
      offline: true
    }
  }

  getHeaders(includeAuth = true) {
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