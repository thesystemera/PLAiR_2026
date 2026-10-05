import { logger } from './logger'
import { safeStorage } from './safeStorage'
import { cacheManager } from './cacheManager'

function isSlowConnection() {
  const connection = typeof navigator !== 'undefined' ? navigator.connection : null
  if (!connection) return false
  return !!connection.saveData || ['slow-2g', '2g', '3g'].includes(connection.effectiveType)
}

const PACK_SIZES = [256, 512, 768]

function pickPackSize() {
  if (typeof window === 'undefined') return 512
  if (isSlowConnection()) return PACK_SIZES[0]
  const dpr = Math.min(window.devicePixelRatio || 1, 3)
  const width = Math.max(window.innerWidth || 0, window.screen?.width || 0) || 1024
  const height = Math.max(window.innerHeight || 0, window.screen?.height || 0) || 768
  const phoneLandscape = width > height && height < 640
  const cardCss = width < 1024 ? width / (phoneLandscape ? 4 : 2) : 320
  const needed = cardCss * dpr * 0.85
  return PACK_SIZES.find(size => size >= needed) || PACK_SIZES[PACK_SIZES.length - 1]
}

export const ART_PACK_SIZE = pickPackSize()

export function artPackUrl(id) {
  return `/api/artwork/${id}/pack/${ART_PACK_SIZE}?v=${NORMAL_MAP_VERSION}`
}
export const PROFILE_PACK_SIZE = 512
const NORMAL_MAP_VERSION = 3

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
  },
  art_pack: {
    cacheName: 'art-pack-cache-v1',
    maxItems: 1500,
    maxMemoryItems: 300,
    expiryMs: 14 * 24 * 60 * 60 * 1000,
    metadataKey: 'art-pack-metadata',
    getUrl: artPackUrl,
    logPrefix: '[ArtPackCache]',
    memoryOnly: true
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
  profile_pack: {
    cacheName: 'profile-pack-cache-v1',
    maxItems: 500,
    expiryMs: 7 * 24 * 60 * 60 * 1000,
    metadataKey: 'profile-pack-metadata',
    getUrl: (id) => `/api/user/${id}/profile-picture/pack?v=${NORMAL_MAP_VERSION}`,
    logPrefix: '[ProfilePackCache]',
    memoryOnly: true
  }
}

const MAX_MEMORY_ITEMS = 200
const OFFLINE_STORE_TYPES = new Set(['artwork', 'enriched_artwork', 'art_pack'])
const OFFLINE_BLOB_FIELDS = { artwork: 'artworkBlob', enriched_artwork: 'enrichedArtworkBlob', art_pack: 'packBlob' }
const openCaches = new Map()
const METADATA_SAVE_DELAY_MS = 1000

const blobsByUrl = new Map()
const MEMORY_KEY_PREFIX = 'mem:'
let memoryKeyCount = 0

export function blobForUrl(url) {
  return blobsByUrl.get(url) || null
}

export function isMemoryKey(url) {
  return typeof url === 'string' && url.startsWith(MEMORY_KEY_PREFIX)
}

function createBlobUrl(blob, type, id, memoryOnly) {
  const url = memoryOnly ? `${MEMORY_KEY_PREFIX}${type}:${id}:${++memoryKeyCount}` : URL.createObjectURL(blob)
  blobsByUrl.set(url, blob)
  return url
}

function revokeBlobUrl(url) {
  if (typeof url !== 'string') return
  blobsByUrl.delete(url)
  if (url.startsWith('blob:')) URL.revokeObjectURL(url)
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
      const blob = cached?.[OFFLINE_BLOB_FIELDS[this.type]]
      if (!blob) return null
      const blobUrl = createBlobUrl(blob, this.type, id, this.config.memoryOnly)
      this._setMemory(id, blobUrl)
      this.updateMetadata(id, blob.size)
      return blobUrl
    } catch (err) {
      logger.debug(`${this.config.logPrefix} IndexedDB check failed:`, err)
      return null
    }
  }

  async _fetchNetwork(id, cache, mediaUrl, signal) {
    const response = await fetch(mediaUrl, { signal })
    if (response.ok) {
      if (cache) {
        cache.put(mediaUrl, response.clone())
          .then(() => this.evictIfNeeded(cache))
          .catch(err => logger.debug(`${this.config.logPrefix} Cache write failed:`, err))
      }
      return response
    }
    logger.warn(`${this.config.logPrefix} ❌ Fetch failed:`, id, response.status)
    return null
  }

  async _fetchAndCache(id, signal) {
    try {
      const cache = await this._openCache()
      const mediaUrl = this.config.getUrl(id)
      const hasOfflineStore = OFFLINE_STORE_TYPES.has(this.type)

      let response = cache ? await cache.match(mediaUrl) : null

      if (!response && hasOfflineStore) {
        const offlineUrl = await this._fromOfflineStore(id)
        if (offlineUrl) return offlineUrl
      }

      if (!response) response = await this._fetchNetwork(id, cache, mediaUrl, signal)
      if (!response) return null

      const blob = await response.blob()
      const blobUrl = createBlobUrl(blob, this.type, id, this.config.memoryOnly)

      this._setMemory(id, blobUrl)
      this.updateMetadata(id, blob.size)

      return blobUrl
    } catch (err) {
      if (err?.name !== 'AbortError') logger.error(`${this.config.logPrefix} ❌ Error fetching media:`, id, err)
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
export const enrichedArtworkCache = new MediaCache('enriched_artwork')
export const profilePictureCache = new MediaCache('profile_picture')
export const artPackCache = new MediaCache('art_pack')
export const normalFullCache = new MediaCache('normal_full')
export const profilePackCache = new MediaCache('profile_pack')