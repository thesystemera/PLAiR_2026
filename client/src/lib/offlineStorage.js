import { logger } from './logger'
import { CacheValidator } from './cacheValidator'
const DB_NAME = 'plair-audio-cache'
const DB_VERSION = 2
const TRACK_STORE = 'tracks'
const METADATA_STORE = 'metadata'

class AudioCacheDB {
  constructor() {
    this.db = null
  }

  async initialize() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION)

      request.onerror = () => {
        logger.error('[IndexedDB] Failed to open database:', request.error)
        reject(request.error)
      }

      request.onsuccess = () => {
        this.db = request.result
        logger.info('[IndexedDB] Database opened successfully')
        resolve(this.db)
      }

      request.onupgradeneeded = (event) => {
        const db = event.target.result
        const oldVersion = event.oldVersion

        logger.info(`[IndexedDB] Upgrading database from v${oldVersion} to v${DB_VERSION}`)

        if (oldVersion < 2) {
          if (db.objectStoreNames.contains(TRACK_STORE)) {
            db.deleteObjectStore(TRACK_STORE)
            logger.info('[IndexedDB] Cleared old track store for schema upgrade')
          }
        }

        if (!db.objectStoreNames.contains(TRACK_STORE)) {
          const trackStore = db.createObjectStore(TRACK_STORE, { keyPath: 'trackId' })
          trackStore.createIndex('lastAccessed', 'lastAccessed', { unique: false })
          trackStore.createIndex('addedAt', 'addedAt', { unique: false })
          trackStore.createIndex('size', 'size', { unique: false })
          logger.info('[IndexedDB] Created tracks object store with v2 schema')
        }

        if (!db.objectStoreNames.contains(METADATA_STORE)) {
          db.createObjectStore(METADATA_STORE, { keyPath: 'key' })
          logger.info('[IndexedDB] Created metadata object store')
        }
      }
    })
  }

  async saveTrack(trackId, trackData) {
    if (!this.db) {
      await this.initialize()
    }

    const validation = CacheValidator.validateTrackData(trackId, trackData)

    if (!validation.isValid) {
      const errorMsg = `Validation failed: ${validation.errors.join(', ')}`
      logger.error(`[IndexedDB] Cannot save track ${trackId}: ${errorMsg}`)
      throw new Error(`Cannot save track ${trackId}: ${errorMsg}`)
    }

    CacheValidator.logValidationResult(trackId, trackData, validation)

    return new Promise((resolve, reject) => {
      const transaction = this.db.transaction([TRACK_STORE], 'readwrite')
      const store = transaction.objectStore(TRACK_STORE)

      const metadata = trackData.metadata || {}

      if (trackData.lyricTimestamps && !metadata.lyric_timestamps) {
        metadata.lyric_timestamps = trackData.lyricTimestamps
      }

      const record = {
        trackId,
        audioBlob: trackData.audioBlob,
        artworkBlob: trackData.artworkBlob || null,
        enrichedArtworkBlob: trackData.enrichedArtworkBlob || null,
        metadata,
        audioFeatures: trackData.audioFeatures || null,
        bitrate: trackData.bitrate || '192k',
        size: trackData.audioBlob.size,
        addedAt: Date.now(),
        lastAccessed: Date.now(),
      }

      const request = store.put(record)

      request.onsuccess = () => {
        resolve(record)
      }

      request.onerror = () => {
        logger.error(`[IndexedDB] Failed to save track ${trackId}:`, request.error)
        reject(request.error)
      }
    })
  }

  async getTrack(trackId) {
    if (!this.db) {
      await this.initialize()
    }

    return new Promise((resolve, reject) => {
      const transaction = this.db.transaction([TRACK_STORE], 'readwrite')
      const store = transaction.objectStore(TRACK_STORE)
      const request = store.get(trackId)

      request.onsuccess = () => {
        const track = request.result
        if (track) {
          const updateRequest = store.put({
            ...track,
            lastAccessed: Date.now()
          })

          updateRequest.onsuccess = () => {
            resolve({
              id: trackId,
              ...track.metadata,
              audioBlob: track.audioBlob,
              artworkBlob: track.artworkBlob,
              enrichedArtworkBlob: track.enrichedArtworkBlob,
              audioFeatures: track.audioFeatures,
              bitrate: track.bitrate || '192k',
              _isCached: true,
              _cacheSize: track.size,
              _cacheAddedAt: track.addedAt,
              _cacheLastAccessed: track.lastAccessed,
            })
          }

          updateRequest.onerror = () => {
            resolve({
              id: trackId,
              ...track.metadata,
              audioBlob: track.audioBlob,
              artworkBlob: track.artworkBlob,
              enrichedArtworkBlob: track.enrichedArtworkBlob,
              audioFeatures: track.audioFeatures,
              bitrate: track.bitrate || '192k',
              _isCached: true,
              _cacheSize: track.size,
              _cacheAddedAt: track.addedAt,
              _cacheLastAccessed: track.lastAccessed,
            })
          }
        } else {
          resolve(null)
        }
      }

      request.onerror = () => {
        logger.error(`[IndexedDB] Failed to get track ${trackId}:`, request.error)
        reject(request.error)
      }
    })
  }

  async deleteTrack(trackId) {
    if (!this.db) {
      await this.initialize()
    }

    return new Promise((resolve, reject) => {
      const transaction = this.db.transaction([TRACK_STORE], 'readwrite')
      const store = transaction.objectStore(TRACK_STORE)
      const request = store.delete(trackId)

      request.onsuccess = () => {
        resolve()
      }

      request.onerror = () => {
        logger.error(`[IndexedDB] Failed to delete track ${trackId}:`, request.error)
        reject(request.error)
      }
    })
  }

  async getAllTracks() {
    if (!this.db) {
      await this.initialize()
    }

    return new Promise((resolve, reject) => {
      const transaction = this.db.transaction([TRACK_STORE], 'readonly')
      const store = transaction.objectStore(TRACK_STORE)
      const request = store.getAll()

      request.onsuccess = () => {
        resolve(request.result || [])
      }

      request.onerror = () => {
        logger.error('[IndexedDB] Failed to get all tracks:', request.error)
        reject(request.error)
      }
    })
  }

  async getTracksByLastAccessed() {
    if (!this.db) {
      await this.initialize()
    }

    return new Promise((resolve, reject) => {
      const transaction = this.db.transaction([TRACK_STORE], 'readonly')
      const store = transaction.objectStore(TRACK_STORE)
      const index = store.index('lastAccessed')
      const request = index.openCursor(null, 'prev')
      const results = []

      request.onsuccess = (event) => {
        const cursor = event.target.result
        if (cursor) {
          results.push(cursor.value)
          cursor.continue()
        } else {
          resolve(results)
        }
      }

      request.onerror = () => {
        logger.error('[IndexedDB] Failed to get tracks by last accessed:', request.error)
        reject(request.error)
      }
    })
  }

  async getTotalSize() {
    if (!this.db) {
      await this.initialize()
    }

    const tracks = await this.getAllTracks()
    return tracks.reduce((total, track) => total + (track.size || 0), 0)
  }

  async clearAll() {
    if (!this.db) {
      await this.initialize()
    }

    return new Promise((resolve, reject) => {
      const transaction = this.db.transaction([TRACK_STORE], 'readwrite')
      const store = transaction.objectStore(TRACK_STORE)
      const request = store.clear()

      request.onsuccess = () => {
        logger.info('[IndexedDB] Cleared all tracks')
        resolve()
      }

      request.onerror = () => {
        logger.error('[IndexedDB] Failed to clear tracks:', request.error)
        reject(request.error)
      }
    })
  }

  async setMetadata(key, value) {
    if (!this.db) {
      await this.initialize()
    }

    return new Promise((resolve, reject) => {
      const transaction = this.db.transaction([METADATA_STORE], 'readwrite')
      const store = transaction.objectStore(METADATA_STORE)
      const request = store.put({ key, value })

      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error)
    })
  }

  async getMetadata(key) {
    if (!this.db) {
      await this.initialize()
    }

    return new Promise((resolve, reject) => {
      const transaction = this.db.transaction([METADATA_STORE], 'readonly')
      const store = transaction.objectStore(METADATA_STORE)
      const request = store.get(key)

      request.onsuccess = () => {
        const result = request.result
        resolve(result ? result.value : null)
      }
      request.onerror = () => reject(request.error)
    })
  }
}

export const audioCacheDB = new AudioCacheDB()