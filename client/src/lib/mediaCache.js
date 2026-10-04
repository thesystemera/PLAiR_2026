import { logger } from './logger'
import { safeStorage } from './safeStorage'
import { cacheManager } from './cacheManager'

const THUMB_SIZES = [256, 512, 768]

function isSlowConnection() {
  const connection = typeof navigator !== 'undefined' ? navigator.connection : null
  if (!connection) return false
  return !!connection.saveData || ['slow-2g', '2g', '3g'].includes(connection.effectiveType)
}

function pickThumbSize() {
  if (typeof window === 'undefined') return 512
  if (isSlowConnection()) return THUMB_SIZES[0]
  const dpr = Math.min(window.devicePixelRatio || 1, 3)
  const width = window.innerWidth || 1024
  const height = window.innerHeight || 768
  const phoneLandscape = width > height && height < 640
  const cardCss = width < 1024 ? width / (phoneLandscape ? 4 : 2) : 320
  const needed = cardCss * dpr * 0.85
  return THUMB_SIZES.find(size => size >= needed) || THUMB_SIZES[THUMB_SIZES.length - 1]
}

const ARTWORK_THUMB_SIZE = pickThumbSize()
const NORMAL_MAP_VERSION = 2

const CACHE_CONFIGS = {
  artwork: {
    cacheName: 'artwork-cache-v1',
    maxItems: 1000,
    expiryMs: 7 * 24 * 60 * 60 * 1000, // 7 days
    metadataKey: 'artwork-metadata',
    getUrl: (id) => `/api/artwork/${id}`,
    logPrefix: '[ArtworkCache]'
  },
  artwork_thumb: {
    cacheName: 'artwork-thumb-cache-v1',
    maxItems: 1500,
    maxMemoryItems: 400,
    expiryMs: 14 * 24 * 60 * 60 * 1000,
    metadataKey: 'artwork-thumb-metadata',
    getUrl: (id) => `/api/artwork/${id}/thumb/${ARTWORK_THUMB_SIZE}`,
    fallbackType: 'artwork',
    networkFirst: true,
    logPrefix: '[ArtworkThumbCache]'
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
  },
  depth_thumb: {
    cacheName: 'depth-thumb-cache-v1',
    maxItems: 1500,
    maxMemoryItems: 300,
    expiryMs: 14 * 24 * 60 * 60 * 1000,
    metadataKey: 'depth-thumb-metadata',
    getUrl: (id) => `/api/artwork/${id}/depth/thumb/${ARTWORK_THUMB_SIZE}`,
    logPrefix: '[DepthThumbCache]'
  },
  normal_thumb: {
    cacheName: 'normal-thumb-cache-v1',
    maxItems: 1500,
    maxMemoryItems: 300,
    expiryMs: 14 * 24 * 60 * 60 * 1000,
    metadataKey: 'normal-thumb-metadata',
    getUrl: (id) => `/api/artwork/${id}/normal/thumb/${ARTWORK_THUMB_SIZE}?v=${NORMAL_MAP_VERSION}`,
    logPrefix: '[NormalThumbCache]'
  },
  normal_full: {
    cacheName: 'normal-full-cache-v1',
    maxItems: 300,
    maxMemoryItems: 20,
    expiryMs: 14 * 24 * 60 * 60 * 1000,
    metadataKey: 'normal-full-metadata',
    getUrl: (id) => `/api/artwork/${id}/normal?v=${NORMAL_MAP_VERSION}`,
    logPrefix: '[NormalCache]'
  },
  profile_normal: {
    cacheName: 'profile-normal-cache-v1',
    maxItems: 500,
    expiryMs: 7 * 24 * 60 * 60 * 1000,
    metadataKey: 'profile-normal-metadata',
    getUrl: (id) => `/api/user/${id}/profile-picture/normal?v=${NORMAL_MAP_VERSION}`,
    logPrefix: '[ProfileNormalCache]'
  },
  profile_depth: {
    cacheName: 'profile-depth-cache-v1',
    maxItems: 500,
    expiryMs: 7 * 24 * 60 * 60 * 1000,
    metadataKey: 'profile-depth-metadata',
    getUrl: (id) => `/api/user/${id}/profile-picture/depth`,
    logPrefix: '[ProfileDepthCache]'
  }
}

const MAX_MEMORY_ITEMS = 200
const OFFLINE_STORE_TYPES = new Set(['artwork', 'artwork_thumb', 'enriched_artwork'])
const openCaches = new Map()
const METADATA_SAVE_DELAY_MS = 1000

function revokeBlobUrl(url) {
  if (typeof url === 'string' && url.startsWith('blob:')) {
    URL.revokeObjectURL(url)
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
    this.listeners = new Set()
    this.isPinned = null
    this.saveTimer = null
    this.maxMemoryItems = this.config.maxMemoryItems || MAX_MEMORY_ITEMS
    this.fallbackOnly = false

    if (typeof window !== 'undefined') {
      window.addEventListener('pagehide', () => this.flushMetadata())
    }
  }

  subscribe(listener) {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  setPinnedChecker(checker) {
    this.isPinned = checker
  }

  _emit(id) {
    this.listeners.forEach(listener => {
      try {
        listener(id)
      } catch (err) {
        logger.warn(`${this.config.logPrefix} Listener failed:`, err)
      }
    })
  }

  peekMemory(id) {
    return this.memoryCache.get(id) || null
  }

  _setMemory(id, url) {
    const previous = this.memoryCache.get(id)
    this.memoryCache.delete(id)
    this.memoryCache.set(id, url)
    if (previous !== url) {
      if (previous) revokeBlobUrl(previous)
      this._emit(id)
    }
    this._trimMemory()
  }

  releaseMemory(id) {
    const url = this.memoryCache.get(id)
    if (url === undefined) return
    this.memoryCache.delete(id)
    revokeBlobUrl(url)
    this._emit(id)
  }

  _trimMemory() {
    if (this.memoryCache.size <= this.maxMemoryItems) return
    for (const id of this.memoryCache.keys()) {
      if (this.memoryCache.size <= this.maxMemoryItems) break
      if (this.isPinned?.(id)) continue
      this.releaseMemory(id)
    }
  }

  async initialize() {
    if (this.initialized) return

    try {
      const metaStr = safeStorage.get(this.config.metadataKey)
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
      const url = this.memoryCache.get(id)
      this.memoryCache.delete(id)
      this.memoryCache.set(id, url)
      this.touch(id)
      return url
    }
    return null
  }

  async getMedia(id, { signal } = {}) {
    if (!this.initialized) await this.initialize()
    if (!id) return null

    if (this.memoryCache.has(id)) {
      return this.getMemory(id)
    }

    if (this.inFlightRequests.has(id)) {
      return this.inFlightRequests.get(id)
    }

    const fetchPromise = this._fetchAndCache(id, signal)
    this.inFlightRequests.set(id, fetchPromise)

    try {
      return await fetchPromise
    } finally {
      this.inFlightRequests.delete(id)
    }
  }

  _openCache(name = this.config.cacheName) {
    if (!openCaches.has(name)) {
      const pending = typeof caches === 'undefined'
        ? Promise.resolve(null)
        : caches.open(name).catch(() => null)
      openCaches.set(name, pending)
    }
    return openCaches.get(name)
  }

  async _fromOfflineStore(id) {
    try {
      const cached = await cacheManager.getCachedTrack(id)
      const blob = this.type === 'enriched_artwork' ? cached?.enrichedArtworkBlob : cached?.artworkBlob
      if (!blob) return null
      const blobUrl = URL.createObjectURL(blob)
      this._setMemory(id, blobUrl)
      this.updateMetadata(id, blob.size)
      return blobUrl
    } catch (err) {
      logger.debug(`${this.config.logPrefix} IndexedDB check failed:`, err)
      return null
    }
  }

  async _fetchNetwork(id, cache, mediaUrl, signal) {
    const response = this.fallbackOnly ? null : await fetch(mediaUrl, { signal })
    if (response?.ok) {
      if (cache) {
        cache.put(mediaUrl, response.clone())
          .then(() => this.evictIfNeeded(cache))
          .catch(err => logger.debug(`${this.config.logPrefix} Cache write failed:`, err))
      }
      return response
    }
    if (!this.config.fallbackType) {
      logger.warn(`${this.config.logPrefix} ❌ Fetch failed:`, id, response?.status)
      return null
    }
    const fallback = await this._fetchFallback(id, signal)
    if (fallback && response?.status === 404) this.fallbackOnly = true
    if (!fallback) logger.warn(`${this.config.logPrefix} ❌ Fetch failed:`, id, response?.status)
    return fallback
  }

  async _fetchAndCache(id, signal) {
    try {
      const cache = await this._openCache()
      const mediaUrl = this.config.getUrl(id)
      const hasOfflineStore = OFFLINE_STORE_TYPES.has(this.type)

      let response = cache ? await cache.match(mediaUrl) : null

      if (!response && hasOfflineStore && !this.config.networkFirst) {
        const offlineUrl = await this._fromOfflineStore(id)
        if (offlineUrl) return offlineUrl
      }

      if (!response) {
        try {
          response = await this._fetchNetwork(id, cache, mediaUrl, signal)
        } catch (err) {
          if (err?.name === 'AbortError' || !hasOfflineStore || !this.config.networkFirst) throw err
          response = null
        }
      }

      if (!response && hasOfflineStore && this.config.networkFirst) {
        return await this._fromOfflineStore(id)
      }

      if (!response) return null

      const blob = await response.blob()
      const blobUrl = URL.createObjectURL(blob)

      this._setMemory(id, blobUrl)
      this.updateMetadata(id, blob.size)

      return blobUrl
    } catch (err) {
      if (err?.name !== 'AbortError') logger.error(`${this.config.logPrefix} ❌ Error fetching media:`, id, err)
      return null
    }
  }

  async _fetchFallback(id, signal) {
    const fallback = CACHE_CONFIGS[this.config.fallbackType]
    const url = fallback.getUrl(id)
    const cache = await this._openCache(fallback.cacheName)
    const cached = cache ? await cache.match(url) : null
    if (cached) return cached
    const response = await fetch(url, { signal })
    if (!response.ok) return null
    if (cache) {
      cache.put(url, response.clone()).catch(err => logger.debug(`${this.config.logPrefix} Cache write failed:`, err))
    }
    return response
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
    if (this.saveTimer) return
    this.saveTimer = setTimeout(() => this.flushMetadata(), METADATA_SAVE_DELAY_MS)
  }

  flushMetadata() {
    if (this.saveTimer) {
      clearTimeout(this.saveTimer)
      this.saveTimer = null
    }
    try {
      const obj = Object.fromEntries(this.metadata)
      safeStorage.set(this.config.metadataKey, JSON.stringify(obj))
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
      if (this.isPinned?.(id)) continue
      await cache.delete(this.config.getUrl(id))
      this.releaseMemory(id)
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
        this.releaseMemory(id)
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

      this.metadata.delete(id)
      this.releaseMemory(id)
      this.saveMetadata()

      logger.info(`${this.config.logPrefix} 🔄 Invalidated:`, id)
    } catch (err) {
      logger.error(`${this.config.logPrefix} ❌ Failed to invalidate:`, id, err)
    }
  }
}

export const artworkCache = new MediaCache('artwork')
export const artworkThumbCache = new MediaCache('artwork_thumb')
export const enrichedArtworkCache = new MediaCache('enriched_artwork')
export const profilePictureCache = new MediaCache('profile_picture')
export const depthThumbCache = new MediaCache('depth_thumb')
export const profileDepthCache = new MediaCache('profile_depth')
export const normalThumbCache = new MediaCache('normal_thumb')
export const normalFullCache = new MediaCache('normal_full')
export const profileNormalCache = new MediaCache('profile_normal')