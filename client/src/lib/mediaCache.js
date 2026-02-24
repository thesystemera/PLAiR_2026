import { logger } from './logger'
import { cacheManager } from './cacheManager'

const CACHE_CONFIGS = {
  artwork: {
    cacheName: 'artwork-cache-v1',
    maxItems: 1000,
    expiryMs: 7 * 24 * 60 * 60 * 1000, // 7 days
    metadataKey: 'artwork-metadata',
    getUrl: (id) => `/api/artwork/${id}`,
    logPrefix: '[ArtworkCache]'
  },
  enriched_artwork: {
    cacheName: 'enriched-artwork-cache-v1',
    maxItems: 1000,
    expiryMs: 7 * 24 * 60 * 60 * 1000, // 7 days
    metadataKey: 'enriched-artwork-metadata',
    getUrl: (id) => `/api/artwork/${id}/enriched`,
    logPrefix: '[EnrichedArtworkCache]'
  },
  profile_picture: {
    cacheName: 'profile-picture-cache-v1',
    maxItems: 500,
    expiryMs: 7 * 24 * 60 * 60 * 1000, // 7 days
    metadataKey: 'profile-picture-metadata',
    getUrl: (id) => `/api/user/${id}/profile-picture`,
    logPrefix: '[ProfilePictureCache]'
  }
}

class MediaCache {
  constructor(type) {
    if (!CACHE_CONFIGS[type]) {
      throw new Error(`Unknown media type: ${type}`)
    }

    this.type = type
    this.config = CACHE_CONFIGS[type]
    this.memoryCache = new Map()
    this.inFlightRequests = new Map()
    this.metadata = new Map()
    this.initialized = false
  }

  async initialize() {
    if (this.initialized) return

    try {
      const metaStr = localStorage.getItem(this.config.metadataKey)
      if (metaStr) {
        const parsed = JSON.parse(metaStr)
        Object.entries(parsed).forEach(([id, data]) => {
          this.metadata.set(id, data)
        })
      }

      await this.cleanExpired()

      this.initialized = true
      logger.info(`${this.config.logPrefix} Initialized with`, this.metadata.size, 'cached items')
    } catch (err) {
      logger.error(`${this.config.logPrefix} Init failed:`, err)
      this.initialized = true
    }
  }

  getMemory(id) {
    if (this.memoryCache.has(id)) {
      this.touch(id)
      return this.memoryCache.get(id)
    }
    return null
  }

  async getMedia(id) {
    if (!this.initialized) await this.initialize()
    if (!id) return null

    if (this.memoryCache.has(id)) {
      this.touch(id)
      return this.memoryCache.get(id)
    }

    if (this.inFlightRequests.has(id)) {
      return this.inFlightRequests.get(id)
    }

    const fetchPromise = this._fetchAndCache(id)
    this.inFlightRequests.set(id, fetchPromise)

    try {
      return await fetchPromise
    } finally {
      this.inFlightRequests.delete(id)
    }
  }

  async _fetchAndCache(id) {
    try {
      const cache = await caches.open(this.config.cacheName)
      const mediaUrl = this.config.getUrl(id)

      let response = await cache.match(mediaUrl)

      if (!response) {
        if (this.type === 'artwork' || this.type === 'enriched_artwork') {
          try {
            const cached = await cacheManager.getCachedTrack(id)

            if (mediaUrl.includes('/enriched') && cached?.enrichedArtworkBlob) {
              const oldBlobUrl = this.memoryCache.get(id)
              if (oldBlobUrl) URL.revokeObjectURL(oldBlobUrl)

              const blobUrl = URL.createObjectURL(cached.enrichedArtworkBlob)
              this.memoryCache.set(id, blobUrl)
              this.updateMetadata(id, cached.enrichedArtworkBlob.size)
              return blobUrl
            }

            if (!mediaUrl.includes('/enriched') && cached?.artworkBlob) {
              const oldBlobUrl = this.memoryCache.get(id)
              if (oldBlobUrl) URL.revokeObjectURL(oldBlobUrl)

              const blobUrl = URL.createObjectURL(cached.artworkBlob)
              this.memoryCache.set(id, blobUrl)
              this.updateMetadata(id, cached.artworkBlob.size)
              return blobUrl
            }
          } catch (err) {
            logger.debug(`${this.config.logPrefix} IndexedDB check failed:`, err)
          }
        }

        response = await fetch(mediaUrl)

        if (response.ok) {
          await cache.put(mediaUrl, response.clone())
          await this.evictIfNeeded(cache)
        } else {
          logger.warn(`${this.config.logPrefix} ❌ Fetch failed:`, id, response.status)
          return null
        }
      }

      const blob = await response.blob()

      const oldBlobUrl = this.memoryCache.get(id)
      if (oldBlobUrl) URL.revokeObjectURL(oldBlobUrl)

      const blobUrl = URL.createObjectURL(blob)

      this.memoryCache.set(id, blobUrl)
      this.updateMetadata(id, blob.size)

      return blobUrl
    } catch (err) {
      logger.error(`${this.config.logPrefix} ❌ Error fetching media:`, id, err)
      return null
    }
  }

  updateMetadata(id, size) {
    this.metadata.set(id, {
      timestamp: Date.now(),
      size: size || 0
    })
    this.saveMetadata()
  }

  touch(id) {
    const meta = this.metadata.get(id)
    if (meta) {
      meta.timestamp = Date.now()
      this.saveMetadata()
    }
  }

  saveMetadata() {
    try {
      const obj = Object.fromEntries(this.metadata)
      localStorage.setItem(this.config.metadataKey, JSON.stringify(obj))
    } catch (err) {
      logger.warn(`${this.config.logPrefix} Failed to save metadata:`, err)
    }
  }

  async evictIfNeeded(cache) {
    if (this.metadata.size <= this.config.maxItems) return

    logger.info(`${this.config.logPrefix} 🗑️  Evicting old items...`)

    const entries = Array.from(this.metadata.entries())
      .sort((a, b) => a[1].timestamp - b[1].timestamp)

    const toEvict = entries.slice(0, entries.length - this.config.maxItems)

    for (const [id] of toEvict) {
      await cache.delete(this.config.getUrl(id))
      this.memoryCache.delete(id)
      this.metadata.delete(id)
    }

    this.saveMetadata()
    logger.info(`${this.config.logPrefix} ✅ Evicted`, toEvict.length, 'items')
  }

  async cleanExpired() {
    const now = Date.now()
    const cache = await caches.open(this.config.cacheName)
    let cleaned = 0

    for (const [id, meta] of this.metadata.entries()) {
      if (now - meta.timestamp > this.config.expiryMs) {
        await cache.delete(this.config.getUrl(id))
        this.memoryCache.delete(id)
        this.metadata.delete(id)
        cleaned++
      }
    }

    if (cleaned > 0) {
      this.saveMetadata()
      logger.info(`${this.config.logPrefix} 🧹 Cleaned`, cleaned, 'expired items')
    }
  }

  async invalidate(id) {
    if (!id) return

    try {
      const cache = await caches.open(this.config.cacheName)
      await cache.delete(this.config.getUrl(id))

      this.memoryCache.delete(id)
      this.metadata.delete(id)
      this.saveMetadata()

      logger.info(`${this.config.logPrefix} 🔄 Invalidated:`, id)
    } catch (err) {
      logger.error(`${this.config.logPrefix} ❌ Failed to invalidate:`, id, err)
    }
  }
}

export const artworkCache = new MediaCache('artwork')
export const enrichedArtworkCache = new MediaCache('enriched_artwork')
export const profilePictureCache = new MediaCache('profile_picture')

export { MediaCache }