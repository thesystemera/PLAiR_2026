import { logger } from './logger'
import { audioCacheDB, normalizeTrackMetadata } from './offlineStorage'
import { api } from './api'
import { canPlayCachedBlob, downloadFormat } from './mediaSupport'
import { PACK_SIZES, artPackUrl } from './mediaCache'

const isIOS = typeof navigator !== 'undefined' &&
  (/iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.userAgent.includes('Macintosh') && navigator.maxTouchPoints > 1))
const isFirefox = typeof navigator !== 'undefined' && /firefox/i.test(navigator.userAgent)
const MB = 1024 * 1024
const CACHE_CAP_BYTES = 2 * 1024 * MB
const CACHE_FLOOR_BYTES = 200 * MB
const CACHE_QUOTA_SHARE = 0.5
const FALLBACK_CACHE_BYTES = isIOS ? 500 * MB : CACHE_CAP_BYTES
const CLEANUP_THRESHOLD = 0.9
const TARGET_AFTER_CLEANUP = 0.75
const CHUNK_SIZE = 256 * 1024
const MAX_ACTIVE_STREAMS = 3
const MP3_BITRATE = '192k'
const TYPICAL_TRACK_BYTES = 8 * 1024 * 1024

class CacheManager {
  constructor() {
    this.initialized = false
    this.downloadQueue = new Map()
    this.activeStreams = new Map()
    this.listCache = null
    this.listPromise = null
    this.evictionGuard = null
    this.changeListeners = new Set()
    this.cacheLimit = FALLBACK_CACHE_BYTES
    this.persistRequested = false
  }

  get maxCacheSize() {
    return this.cacheLimit
  }

  async _sizeFromQuota() {
    try {
      const estimate = await navigator.storage?.estimate?.()
      const quota = estimate?.quota || 0
      if (!quota) return
      const share = Math.floor(quota * CACHE_QUOTA_SHARE)
      this.cacheLimit = Math.min(CACHE_CAP_BYTES, Math.max(Math.min(CACHE_FLOOR_BYTES, Math.floor(quota * 0.8)), share))
      logger.info(`[CacheManager] Download space ${(this.cacheLimit / MB).toFixed(0)} MB (browser quota ${(quota / MB).toFixed(0)} MB)`)
    } catch (error) {
      logger.warn('[CacheManager] Storage estimate unavailable:', error)
    }
  }

  async requestPersistence() {
    if (this.persistRequested || isFirefox || !navigator.storage?.persist) return
    this.persistRequested = true
    try {
      const already = await navigator.storage.persisted?.()
      const granted = already || await navigator.storage.persist()
      logger.info(`[CacheManager] Persistent storage ${granted ? 'granted' : 'not granted'}`)
    } catch (error) {
      logger.warn('[CacheManager] Persistent storage request failed:', error)
    }
  }

  get isAvailable() {
    return !audioCacheDB.unavailable
  }

  setEvictionGuard(guard) {
    this.evictionGuard = guard
  }

  onChange(listener) {
    this.changeListeners.add(listener)
    return () => this.changeListeners.delete(listener)
  }

  _invalidateList() {
    this.listCache = null
    this.listPromise = null
    this.changeListeners.forEach(listener => {
      try {
        listener()
      } catch (err) {
        logger.warn('[CacheManager] Change listener failed:', err)
      }
    })
  }

  async getCachedTrackList() {
    if (!this.initialized) await this.initialize()
    if (this.listCache) return this.listCache
    if (this.listPromise) return this.listPromise
    const promise = audioCacheDB.getAllTracks()
      .then(records => records.map(record => ({
        trackId: record.trackId,
        metadata: normalizeTrackMetadata(record.metadata, record.trackId),
        bitrate: record.bitrate,
        size: record.size || 0,
        addedAt: record.addedAt,
        lastAccessed: record.lastAccessed,
        playable: record.audioBlob instanceof Blob && record.audioBlob.size > 0 && canPlayCachedBlob(record.audioBlob),
        hasArtwork: PACK_SIZES.every(size => record.packBlobs?.[size] instanceof Blob),
        hasAudioFeatures: !!record.audioFeatures,
      })))
      .catch(error => {
        logger.error('[CacheManager] Error listing cached tracks:', error)
        return []
      })
    this.listPromise = promise
    const list = await promise
    if (this.listPromise === promise) {
      this.listCache = list
      this.listPromise = null
    }
    return list
  }

  async hasRoomFor(bytes = TYPICAL_TRACK_BYTES) {
    if (!this.initialized) await this.initialize()
    if (audioCacheDB.unavailable) return false
    const list = await this.getCachedTrackList()
    const used = list.reduce((sum, track) => sum + track.size, 0)
    return used + bytes <= this.cacheLimit * CLEANUP_THRESHOLD
  }

  async canMakeRoomFor(bytes = TYPICAL_TRACK_BYTES) {
    if (await this.hasRoomFor(bytes)) return true
    if (audioCacheDB.unavailable) return false
    let guarded = new Set()
    try {
      guarded = new Set(this.evictionGuard?.() || [])
    } catch {
      guarded = new Set()
    }
    const list = await this.getCachedTrackList()
    const evictable = list.filter(track => !guarded.has(track.trackId)).reduce((sum, track) => sum + track.size, 0)
    return evictable >= bytes
  }

  async initialize() {
    if (this.initialized) return
    if (this._initPromise) return this._initPromise

    this._initPromise = (async () => {
      try {
        await audioCacheDB.initialize()
        await this._sizeFromQuota()
        logger.info('[CacheManager] Initialized')

        const totalDownloaded = await audioCacheDB.getMetadata('totalDownloaded') || 0
        if (totalDownloaded === 0) {
          await audioCacheDB.setMetadata('totalDownloaded', 0)
          await audioCacheDB.setMetadata('sessionDownloaded', 0)
        }

        this.initialized = true
      } catch (error) {
        logger.error('[CacheManager] Initialization failed:', error)
        this.initialized = true
      } finally {
        this._initPromise = null
      }
    })()
    return this._initPromise
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
    const list = await this.getCachedTrackList()
    return list.some(track => track.trackId === trackId && track.playable)
  }

  async getAllCachedTracks() {
    if (!this.initialized) await this.initialize()

    try {
      const allTracks = await audioCacheDB.getAllTracks()

      return allTracks.map(cached => ({
        trackId: cached.trackId,
        audioBlob: cached.audioBlob,
        packBlobs: cached.packBlobs,
        metadata: normalizeTrackMetadata(cached.metadata, cached.trackId),
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
    if (existing && canPlayCachedBlob(existing.audioBlob)) {
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

    await this.ensureSpace(TYPICAL_TRACK_BYTES, [trackId])

    const format = downloadFormat()
    const url = format === 'mp3' ? api.getMp3StreamUrl(trackId, 'download') : api.getStreamUrl(trackId, bitrate, 'download')

    const result = await this.downloadChunked(url, { onProgress, signal })

    if (signal?.aborted) throw new DOMException('Download aborted', 'AbortError')
    if (!result.totalBytes || (result.contentLength > 0 && result.totalBytes < result.contentLength)) {
      throw new Error(`Incomplete download for ${trackId} (${result.totalBytes}/${result.contentLength} bytes)`)
    }

    const audioBlob = new Blob(result.chunks, { type: format === 'mp3' ? 'audio/mpeg' : 'audio/webm' })
    logger.info(`[CacheManager] Downloaded audio ${trackId}: ${(audioBlob.size / 1024 / 1024).toFixed(2)} MB`)

    await this._updateDataUsage(audioBlob.size)

    let packBlobs = null
    if (fullTrackData.has_artwork || fullTrackData.hasArtwork) {
      try {
        packBlobs = await this._downloadPacks(trackId, signal)
      } catch (err) {
        if (err.name !== 'AbortError') {
          logger.warn(`[CacheManager] Failed to download cover pack for ${trackId}:`, err)
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
      packBlobs,
      metadata: fullTrackData,
      audioFeatures,
      lyricTimestamps,
      bitrate: format === 'mp3' ? MP3_BITRATE : bitrate
    }

    const savedTrack = await audioCacheDB.saveTrack(trackId, trackData)
    this._invalidateList()

    logger.info(`[CacheManager] Cached track ${trackId} at ${bitrate} with complete metadata`)
    return savedTrack
  }

  async _downloadPacks(trackId, signal = null) {
    const blobs = await Promise.all(PACK_SIZES.map(async (size) => {
      const response = await fetch(artPackUrl(trackId, size), { signal })
      if (!response.ok) throw new Error(`Cover pack ${size} fetch failed: ${response.status}`)
      return await response.blob()
    }))
    return Object.fromEntries(PACK_SIZES.map((size, index) => [size, blobs[index]]))
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

  async ensureSpace(requiredSpace = 10 * 1024 * 1024, keepIds = []) {
    if (!this.initialized) await this.initialize()

    const list = await this.getCachedTrackList()
    const currentSize = list.reduce((sum, track) => sum + track.size, 0)
    const threshold = this.cacheLimit * CLEANUP_THRESHOLD

    if (currentSize + requiredSpace > threshold) {
      logger.info(`[CacheManager] Cache size (${(currentSize / 1024 / 1024).toFixed(2)} MB) approaching limit, cleaning up...`)
      await this.evictLRU(requiredSpace, keepIds)
    }
  }

  async evictLRU(requiredSpace = 0, keepIds = []) {
    if (!this.initialized) await this.initialize()

    const tracks = (await audioCacheDB.getTracksByLastAccessed()).reverse()
    const currentSize = tracks.reduce((sum, t) => sum + (t.size || 0), 0)
    const targetSize = Math.max(0, this.cacheLimit * TARGET_AFTER_CLEANUP - requiredSpace)
    const keep = new Set(keepIds)
    let guarded = new Set()
    try {
      guarded = new Set(this.evictionGuard?.() || [])
    } catch (err) {
      logger.warn('[CacheManager] Eviction guard failed:', err)
    }
    const ordered = [
      ...tracks.filter(track => !guarded.has(track.trackId)),
      ...tracks.filter(track => guarded.has(track.trackId)),
    ].filter(track => !keep.has(track.trackId))

    let freedSpace = 0
    let deletedCount = 0

    for (const track of ordered) {
      if (currentSize - freedSpace <= targetSize) {
        break
      }

      await audioCacheDB.deleteTrack(track.trackId)
      freedSpace += track.size || 0
      deletedCount++
    }

    if (deletedCount) this._invalidateList()
    logger.info(`[CacheManager] Evicted ${deletedCount} tracks, freed ${(freedSpace / 1024 / 1024).toFixed(2)} MB`)
  }

  async getStorageInfo() {
    if (!this.initialized) await this.initialize()

    const tracks = await this.getCachedTrackList()
    const usedBytes = tracks.reduce((sum, track) => sum + track.size, 0)

    let quota = this.cacheLimit
    let usage = usedBytes
    if (tracks.length) void this.requestPersistence()

    if (navigator.storage && navigator.storage.estimate) {
      try {
        const estimate = await navigator.storage.estimate()
        quota = estimate.quota || this.cacheLimit
        usage = estimate.usage || usedBytes
      } catch (error) {
        logger.warn('[CacheManager] Could not get storage estimate:', error)
      }
    }

    return {
      usedBytes,
      maxBytes: this.cacheLimit,
      usedPercentage: (usedBytes / this.cacheLimit) * 100,
      trackCount: tracks.length,
      offlineTrackCount: tracks.filter(track => track.playable).length,
      tracks,
      browserQuota: quota,
      browserUsage: usage
    }
  }

  async deleteTrack(trackId) {
    if (!this.initialized) await this.initialize()
    await audioCacheDB.deleteTrack(trackId)
    this._invalidateList()
    logger.info(`[CacheManager] Deleted cached track ${trackId}`)
  }

  async clearAllCache() {
    if (!this.initialized) await this.initialize()
    await audioCacheDB.clearAll()
    this._invalidateList()
    logger.info('[CacheManager] Cleared all cached tracks')
  }

  beginTrackStream(trackId, metadata) {
    if (audioCacheDB.unavailable) return
    this.activeStreams.delete(trackId)
    while (this.activeStreams.size >= MAX_ACTIVE_STREAMS) {
      this.activeStreams.delete(this.activeStreams.keys().next().value)
    }
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
      return false
    }
    this.activeStreams.delete(trackId)

    logger.info(`[CacheManager] Finalizing ${trackId}: ${(stream.totalSize / 1024 / 1024).toFixed(2)} MB`)

    try {
      await this.ensureSpace(stream.totalSize, [trackId])

      const audioBlob = new Blob(stream.chunks, { type: 'audio/webm' })
      await this._updateDataUsage(audioBlob.size)

      let packBlobs = null
      const metadata = stream.metadata
      if (metadata.has_artwork || metadata.hasArtwork) {
        try {
          packBlobs = await this._downloadPacks(trackId)
        } catch {
          // Cover pack download errors are intentionally suppressed
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
        packBlobs,
        metadata,
        audioFeatures,
        lyricTimestamps,
        bitrate: metadata.bitrate || '192k'
      }

      const saved = await audioCacheDB.saveTrack(trackId, trackData)
      this._invalidateList()
      if (!saved) return false
      logger.info(`[CacheManager] SAVED to IndexedDB: ${trackId} (${(audioBlob.size / 1024 / 1024).toFixed(2)} MB)`)
      return true
    } catch (error) {
      logger.error(`[CacheManager] Failed to finalize ${trackId}:`, error)
      return false
    }
  }
}

export const cacheManager = new CacheManager()