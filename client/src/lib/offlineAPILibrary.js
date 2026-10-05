import { logger } from './logger'
import { cacheManager } from './cacheManager'

export const libraryMethods = {
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

    for (const [size, blob] of Object.entries(cached.packBlobs || {})) {
      if (!(blob instanceof Blob)) issues.push(`packBlobs.${size} is not a valid Blob`)
    }

    return {
      isValid: issues.length === 0,
      issues
    }
  },

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
  },

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
  },

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
  },

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
  },

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
  },

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
  },

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
  },
}
