import { logger } from './logger'
import { audioCacheDB } from './offlineStorage'
import { api } from './api'

const MAX_CACHE_SIZE = 2 * 1024 * 1024 * 1024
const CLEANUP_THRESHOLD = 0.9
const TARGET_AFTER_CLEANUP = 0.75
const CHUNK_SIZE = 256 * 1024

class CacheManager {
  constructor() {
    this.initialized = false
    this.downloadQueue = new Map()
    this.activeStreams = new Map()
  }

  async initialize() {
    if (this.initialized) return

    try {
      await audioCacheDB.initialize()
      logger.info('[CacheManager] Initialized')

      const totalDownloaded = await audioCacheDB.getMetadata('totalDownloaded') || 0
      if (totalDownloaded === 0) {
        await audioCacheDB.setMetadata('totalDownloaded', 0)
        await audioCacheDB.setMetadata('sessionDownloaded', 0)
      }

      this.initialized = true
    } catch (error) {
      logger.error('[CacheManager] Initialization failed:', error)
      throw error
    }
  }

  async getCachedTrack(trackId) {
    if (!this.initialized) await this.initialize()

    try {
      const cached = await audioCacheDB.getTrack(trackId)
      return cached || null
    } catch (error) {
      logger.error(`[CacheManager] Error getting cached track ${trackId}:`, error)
      return null
    }
  }

  async isCached(trackId) {
    if (!this.initialized) await this.initialize()
    const track = await audioCacheDB.getTrack(trackId)
    return !!track
  }

  async getAllCachedTracks() {
    if (!this.initialized) await this.initialize()

    try {
      const allTracks = await audioCacheDB.getAllTracks()

      return allTracks.map(cached => ({
        trackId: cached.trackId,
        audioBlob: cached.audioBlob,
        artworkBlob: cached.artworkBlob,
        enrichedArtworkBlob: cached.enrichedArtworkBlob,
        metadata: cached.metadata,
        audioFeatures: cached.audioFeatures,
        bitrate: cached.bitrate,
        size: cached.size,
        addedAt: cached.addedAt,
        lastAccessed: cached.lastAccessed
      }))
    } catch (error) {
      logger.error('[CacheManager] Error getting all cached tracks:', error)
      return []
    }
  }

  async downloadChunked(url, options = {}) {
    const {
      onChunk = null,
      onProgress = null,
      signal = null,
      chunkSize = CHUNK_SIZE
    } = options

    const chunks = []
    let contentLength = 0
    let bytesDownloaded = 0

    try {
      const headResponse = await fetch(url, { method: 'HEAD', signal })
      if (headResponse.ok) {
        contentLength = parseInt(headResponse.headers.get('content-length') || '0')
      }

      while (bytesDownloaded < contentLength || contentLength === 0) {
        if (signal?.aborted) break

        const start = bytesDownloaded
        const end = contentLength > 0
          ? Math.min(bytesDownloaded + chunkSize - 1, contentLength - 1)
          : bytesDownloaded + chunkSize - 1

        const response = await fetch(url, {
          signal,
          headers: { 'Range': `bytes=${start}-${end}` }
        })

        if (!response.ok && response.status !== 206) {
          if (bytesDownloaded === 0) {
            throw new Error(`HTTP error! status: ${response.status}`)
          }
          break
        }

        const chunk = await response.arrayBuffer()
        const value = new Uint8Array(chunk)

        let shouldContinue = true
        if (onChunk) {
          shouldContinue = await onChunk(value, bytesDownloaded, contentLength)
          if (shouldContinue === false) {
            continue
          }
        }

        if (!onChunk) {
          chunks.push(value)
        }

        bytesDownloaded += value.byteLength

        if (onProgress && contentLength > 0) {
          const progress = (bytesDownloaded / contentLength) * 100
          onProgress(progress)
        }

        if (contentLength === 0 && value.byteLength < chunkSize) {
          break
        }

        if (bytesDownloaded >= contentLength && contentLength > 0) {
          break
        }
      }

      return {
        chunks,
        totalBytes: bytesDownloaded,
        contentLength
      }
    } catch (error) {
      if (error.name !== 'AbortError') {
        logger.error('[CacheManager] Chunked download failed:', error)
      }
      throw error
    }
  }

  async downloadAndCacheTrack(trackId, fullTrackData = {}, bitrate = '192k', onProgress = null, signal = null) {
    if (!this.initialized) await this.initialize()

    if (this.downloadQueue.has(trackId)) {
      logger.info(`[CacheManager] Already downloading ${trackId}, waiting...`)
      return this.downloadQueue.get(trackId)
    }

    const existing = await this.getCachedTrack(trackId)
    if (existing) {
      const existingQuality = this._getBitrateValue(existing.bitrate)
      const requestedQuality = this._getBitrateValue(bitrate)

      if (existingQuality >= requestedQuality) {
        logger.info(`[CacheManager] Track ${trackId} already cached at ${existing.bitrate} (>= ${bitrate})`)
        return existing
      } else {
        logger.info(`[CacheManager] Upgrading ${trackId} from ${existing.bitrate} to ${bitrate}`)
      }
    }

    const downloadPromise = this._downloadTrack(trackId, fullTrackData, bitrate, onProgress, signal)
    this.downloadQueue.set(trackId, downloadPromise)

    try {
      return await downloadPromise
    } finally {
      this.downloadQueue.delete(trackId)
    }
  }

  _getBitrateValue(bitrate) {
    if (bitrate === 'auto' || bitrate === '256k') return 256
    if (bitrate === '192k') return 192
    if (bitrate === '128k') return 128
    return 192
  }

  async _downloadTrack(trackId, fullTrackData, bitrate, onProgress, signal) {
    logger.info(`[CacheManager] Starting download for ${trackId} at ${bitrate}`)

    await this.ensureSpace()

    const url = api.getStreamUrl(trackId)

    const result = await this.downloadChunked(url, { onProgress, signal })

    const audioBlob = new Blob(result.chunks, { type: 'audio/webm' })
    logger.info(`[CacheManager] Downloaded audio ${trackId}: ${(audioBlob.size / 1024 / 1024).toFixed(2)} MB`)

    await this._updateDataUsage(audioBlob.size)

    let artworkBlob = null
    let enrichedArtworkBlob = null
    if (fullTrackData.has_artwork || fullTrackData.hasArtwork) {
      try {
        artworkBlob = await this._downloadArtwork(trackId, signal)
        logger.info(`[CacheManager] Downloaded artwork for ${trackId}`)
      } catch (err) {
        if (err.name !== 'AbortError') {
          logger.warn(`[CacheManager] Failed to download artwork for ${trackId}:`, err)
        }
      }

      try {
        enrichedArtworkBlob = await this._downloadEnrichedArtwork(trackId, signal)
        logger.info(`[CacheManager] Downloaded enriched artwork for ${trackId}`)
      } catch (err) {
        if (err.name !== 'AbortError') {
          logger.warn(`[CacheManager] Failed to download enriched artwork for ${trackId}:`, err)
        }
      }
    }

    let audioFeatures = null
    let lyricTimestamps = null

    try {
      const [features, lyrics] = await Promise.all([
        api.getAudioFeatures(trackId).catch(err => {
          if (err.name !== 'AbortError') {
            logger.warn(`[CacheManager] Failed to download audio features for ${trackId}:`, err)
          }
          return null
        }),
        api.getLyricTimestamps(trackId).catch(err => {
          if (err.name !== 'AbortError') {
            logger.warn(`[CacheManager] Failed to download lyric timestamps for ${trackId}:`, err)
          }
          return null
        })
      ])

      audioFeatures = features
      lyricTimestamps = lyrics

      if (audioFeatures) {
        logger.info(`[CacheManager] Downloaded audio features for ${trackId}`)
      }
      if (lyricTimestamps) {
        logger.info(`[CacheManager] Downloaded lyric timestamps for ${trackId}`)
      }
    } catch (err) {
      if (err.name !== 'AbortError') {
        logger.warn(`[CacheManager] Failed to download metadata for ${trackId}:`, err)
      }
    }

    const trackData = {
      audioBlob,
      artworkBlob,
      enrichedArtworkBlob,
      metadata: fullTrackData,
      audioFeatures,
      lyricTimestamps,
      bitrate
    }

    const savedTrack = await audioCacheDB.saveTrack(trackId, trackData)

    logger.info(`[CacheManager] Cached track ${trackId} at ${bitrate} with complete metadata`)
    return savedTrack
  }

  async _downloadArtwork(trackId, signal = null) {
    const artworkUrl = api.getArtworkUrl(trackId)
    const response = await fetch(artworkUrl, { signal })

    if (!response.ok) {
      throw new Error(`Artwork fetch failed: ${response.status}`)
    }

    return await response.blob()
  }

  async _downloadEnrichedArtwork(trackId, signal = null) {
    const enrichedUrl = `/api/artwork/${trackId}/enriched`
    const response = await fetch(enrichedUrl, { signal })

    if (!response.ok) {
      throw new Error(`Enriched artwork fetch failed: ${response.status}`)
    }

    return await response.blob()
  }

  async _updateDataUsage(bytes) {
    try {
      const totalDownloaded = (await audioCacheDB.getMetadata('totalDownloaded')) || 0
      const sessionDownloaded = (await audioCacheDB.getMetadata('sessionDownloaded')) || 0

      await audioCacheDB.setMetadata('totalDownloaded', totalDownloaded + bytes)
      await audioCacheDB.setMetadata('sessionDownloaded', sessionDownloaded + bytes)
    } catch (error) {
      logger.error('[CacheManager] Failed to update data usage:', error)
    }
  }

  async getDataUsage() {
    if (!this.initialized) await this.initialize()

    const totalDownloaded = (await audioCacheDB.getMetadata('totalDownloaded')) || 0
    const sessionDownloaded = (await audioCacheDB.getMetadata('sessionDownloaded')) || 0

    return { totalDownloaded, sessionDownloaded }
  }

  async resetSessionUsage() {
    if (!this.initialized) await this.initialize()
    await audioCacheDB.setMetadata('sessionDownloaded', 0)
  }

  async ensureSpace(requiredSpace = 10 * 1024 * 1024) {
    if (!this.initialized) await this.initialize()

    const currentSize = await audioCacheDB.getTotalSize()
    const threshold = MAX_CACHE_SIZE * CLEANUP_THRESHOLD

    if (currentSize + requiredSpace > threshold) {
      logger.info(`[CacheManager] Cache size (${(currentSize / 1024 / 1024).toFixed(2)} MB) approaching limit, cleaning up...`)
      await this.evictLRU()
    }
  }

  async evictLRU() {
    if (!this.initialized) await this.initialize()

    const tracks = await audioCacheDB.getTracksByLastAccessed()
    const currentSize = tracks.reduce((sum, t) => sum + t.size, 0)
    const targetSize = MAX_CACHE_SIZE * TARGET_AFTER_CLEANUP

    let freedSpace = 0
    let deletedCount = 0

    for (const track of tracks.reverse()) {
      if (currentSize - freedSpace <= targetSize) {
        break
      }

      await audioCacheDB.deleteTrack(track.trackId)
      freedSpace += track.size
      deletedCount++
    }

    logger.info(`[CacheManager] Evicted ${deletedCount} tracks, freed ${(freedSpace / 1024 / 1024).toFixed(2)} MB`)
  }

  async getStorageInfo() {
    if (!this.initialized) await this.initialize()

    const tracks = await audioCacheDB.getAllTracks()
    const usedBytes = tracks.reduce((sum, track) => sum + track.size, 0)

    let quota = MAX_CACHE_SIZE
    let usage = usedBytes

    if (navigator.storage && navigator.storage.estimate) {
      try {
        const estimate = await navigator.storage.estimate()
        quota = estimate.quota || MAX_CACHE_SIZE
        usage = estimate.usage || usedBytes
      } catch (error) {
        logger.warn('[CacheManager] Could not get storage estimate:', error)
      }
    }

    return {
      usedBytes,
      maxBytes: MAX_CACHE_SIZE,
      usedPercentage: (usedBytes / MAX_CACHE_SIZE) * 100,
      trackCount: tracks.length,
      tracks,
      browserQuota: quota,
      browserUsage: usage
    }
  }

  async deleteTrack(trackId) {
    if (!this.initialized) await this.initialize()
    await audioCacheDB.deleteTrack(trackId)
    logger.info(`[CacheManager] Deleted cached track ${trackId}`)
  }

  async clearAllCache() {
    if (!this.initialized) await this.initialize()
    await audioCacheDB.clearAll()
    logger.info('[CacheManager] Cleared all cached tracks')
  }

  beginTrackStream(trackId, metadata) {
    this.activeStreams.set(trackId, {
      chunks: [],
      metadata,
      startTime: Date.now(),
      totalSize: 0
    })
  }

  addStreamChunk(trackId, chunk) {
    const stream = this.activeStreams.get(trackId)
    if (!stream) return

    stream.chunks.push(chunk)
    stream.totalSize += chunk.byteLength
  }

  async finalizeStream(trackId, isOnline = true) {
    const stream = this.activeStreams.get(trackId)
    if (!stream) {
      logger.warn(`[CacheManager] No active stream to finalize for ${trackId}`)
      return
    }

    logger.info(`[CacheManager] Finalizing ${trackId}: ${(stream.totalSize / 1024 / 1024).toFixed(2)} MB`)

    try {
      await this.ensureSpace(stream.totalSize)

      const audioBlob = new Blob(stream.chunks, { type: 'audio/webm' })
      await this._updateDataUsage(audioBlob.size)

      let artworkBlob = null
      let enrichedArtworkBlob = null
      const metadata = stream.metadata
      if (metadata.has_artwork || metadata.hasArtwork) {
        try {
          artworkBlob = await this._downloadArtwork(trackId)
        } catch {
          // Artwork download errors are intentionally suppressed
        }

        try {
          enrichedArtworkBlob = await this._downloadEnrichedArtwork(trackId)
        } catch {
          // Enriched artwork download errors are intentionally suppressed
        }
      }

      let audioFeatures = null
      let lyricTimestamps = null

      if (isOnline) {
        try {
          audioFeatures = await api.getAudioFeatures(trackId)
        } catch {
          // Audio features fetch errors are intentionally suppressed
        }

        try {
          lyricTimestamps = await api.getLyricTimestamps(trackId)
        } catch {
          // Lyric timestamps fetch errors are intentionally suppressed
        }
      }

      const trackData = {
        audioBlob,
        artworkBlob,
        enrichedArtworkBlob,
        metadata,
        audioFeatures,
        lyricTimestamps,
        bitrate: metadata.bitrate || '192k'
      }

      await audioCacheDB.saveTrack(trackId, trackData)
      logger.info(`[CacheManager] SAVED to IndexedDB: ${trackId} (${(audioBlob.size / 1024 / 1024).toFixed(2)} MB)`)
    } catch (error) {
      logger.error(`[CacheManager] Failed to finalize ${trackId}:`, error)
    } finally {
      this.activeStreams.delete(trackId)
    }
  }
}

export const cacheManager = new CacheManager()