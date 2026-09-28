import { logger } from './logger'
import { CacheValidator } from './cacheValidator'
const DB_NAME = 'plair-audio-cache'
const DB_VERSION = 2
const TRACK_STORE = 'tracks'
const METADATA_STORE = 'metadata'
const LAST_ACCESSED_TOUCH_INTERVAL_MS = 60 * 60 * 1000
const OPEN_TIMEOUT_MS = 5000
const TRANSIENT_METADATA_KEYS = ['fromCache', 'crossfade_hint', 'announcer_hint', 'audioBlob', 'artworkBlob', 'enrichedArtworkBlob', 'audioFeatures']

export function normalizeTrackMetadata(metadata, trackId = null) {
  const meta = metadata && typeof metadata === 'object' ? { ...metadata } : {}
  TRANSIENT_METADATA_KEYS.forEach(key => { delete meta[key] })
  const params = meta.generation_params && typeof meta.generation_params === 'object' ? meta.generation_params : {}
  const info = meta.track_info && typeof meta.track_info === 'object' ? meta.track_info : {}
  const title = meta.title || params.title || 'Untitled'
  const artistName = meta.artist_name ?? params.artist_name ?? null
  const style = meta.style || params.style || ''
  const durationMs = meta.duration_ms || info.duration || 0
  return {
    ...meta,
    id: meta.id || trackId,
    title,
    artist_name: artistName,
    style,
    duration_ms: durationMs,
    generation_params: { ...params, title: params.title || title, artist_name: params.artist_name ?? artistName, style: params.style || style },
    track_info: { ...info, duration: info.duration || durationMs },
  }
}

class AudioCacheDB {
  constructor() {
    this.db = null
    this.unavailable = false
    this._openPromise = null
  }

  async initialize() {
    if (this.unavailable) return null
    if (this.db) return this.db
    if (this._openPromise) return this._openPromise
    this._openPromise = this._open().finally(() => {
      this._openPromise = null
    })
    return this._openPromise
  }

  _markUnavailable(reason) {
    if (!this.unavailable) logger.warn(`[IndexedDB] Offline library unavailable (${reason}) - continuing without it`)
    this.unavailable = true
  }

  _open() {
    return new Promise((resolve) => {
      let settled = false
      let timer = null
      const finish = (db) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        resolve(db)
      }
      timer = setTimeout(() => {
        this._markUnavailable('open timed out')
        finish(null)
      }, OPEN_TIMEOUT_MS)

      try {
        if (typeof indexedDB === 'undefined' || !indexedDB) {
          this._markUnavailable('not supported')
          finish(null)
          return
        }
        const request = indexedDB.open(DB_NAME, DB_VERSION)

        request.onerror = (event) => {
          event?.preventDefault?.()
          logger.error('[IndexedDB] Failed to open database:', request.error)
          this._markUnavailable(request.error?.name || 'open failed')
          finish(null)
        }

        request.onblocked = () => {
          logger.warn('[IndexedDB] Database upgrade blocked by another tab')
        }

        request.onsuccess = () => {
          const db = request.result
          db.onversionchange = () => {
            db.close()
            if (this.db === db) this.db = null
          }
          db.onclose = () => {
            if (this.db === db) this.db = null
          }
          if (settled) {
            db.close()
            return
          }
          this.db = db
          logger.info('[IndexedDB] Database opened successfully')
          finish(db)
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
      } catch (error) {
        this._markUnavailable(error?.name || 'open threw')
        finish(null)
      }
    })
  }

  async _transaction(stores, mode = 'readonly') {
    for (let attempt = 0; attempt < 2; attempt++) {
      const db = this.db || await this.initialize()
      if (!db) return null
      try {
        return db.transaction(stores, mode)
      } catch (error) {
        if (error?.name !== 'InvalidStateError' || attempt > 0) throw error
        logger.warn('[IndexedDB] Connection was closed, reopening')
        if (this.db === db) this.db = null
      }
    }
    return null
  }

  _run(request, label) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result)
      request.onerror = (event) => {
        event.preventDefault()
        logger.error(`[IndexedDB] ${label} failed:`, request.error)
        reject(request.error)
      }
    })
  }

  async saveTrack(trackId, trackData) {
    const validation = CacheValidator.validateTrackData(trackId, trackData)

    if (!validation.isValid) {
      const errorMsg = `Validation failed: ${validation.errors.join(', ')}`
      logger.error(`[IndexedDB] Cannot save track ${trackId}: ${errorMsg}`)
      throw new Error(`Cannot save track ${trackId}: ${errorMsg}`)
    }

    const transaction = await this._transaction([TRACK_STORE], 'readwrite')
    if (!transaction) return null

    CacheValidator.logValidationResult(trackId, trackData, validation)

    const metadata = normalizeTrackMetadata(trackData.metadata, trackId)
    if (trackData.lyricTimestamps && !metadata.lyric_timestamps) {
      metadata.lyric_timestamps = trackData.lyricTimestamps
    }

    const now = Date.now()
    const record = {
      trackId,
      audioBlob: trackData.audioBlob,
      artworkBlob: trackData.artworkBlob || null,
      enrichedArtworkBlob: trackData.enrichedArtworkBlob || null,
      metadata,
      audioFeatures: trackData.audioFeatures || null,
      bitrate: trackData.bitrate || '192k',
      size: trackData.audioBlob.size + (trackData.artworkBlob?.size || 0) + (trackData.enrichedArtworkBlob?.size || 0),
      addedAt: now,
      lastAccessed: now,
    }

    return new Promise((resolve, reject) => {
      const request = transaction.objectStore(TRACK_STORE).put(record)
      transaction.oncomplete = () => resolve(record)
      transaction.onabort = () => {
        const error = transaction.error || request.error
        logger.error(`[IndexedDB] Failed to save track ${trackId}:`, error)
        reject(error || new Error('Transaction aborted'))
      }
    })
  }

  async getTrack(trackId) {
    const transaction = await this._transaction([TRACK_STORE], 'readonly')
    if (!transaction) return null
    const track = await this._run(transaction.objectStore(TRACK_STORE).get(trackId), `Get track ${trackId}`)
    if (!track) return null
    this._touchTrack(track)
    return {
      ...normalizeTrackMetadata(track.metadata, trackId),
      id: trackId,
      audioBlob: track.audioBlob,
      artworkBlob: track.artworkBlob,
      enrichedArtworkBlob: track.enrichedArtworkBlob,
      audioFeatures: track.audioFeatures,
      bitrate: track.bitrate || '192k',
      _isCached: true,
      _cacheSize: track.size,
      _cacheAddedAt: track.addedAt,
      _cacheLastAccessed: track.lastAccessed,
    }
  }

  _touchTrack(track) {
    const now = Date.now()
    if (track.lastAccessed && now - track.lastAccessed < LAST_ACCESSED_TOUCH_INTERVAL_MS) return
    this._transaction([TRACK_STORE], 'readwrite').then(transaction => {
      if (!transaction) return
      const request = transaction.objectStore(TRACK_STORE).put({ ...track, lastAccessed: now })
      request.onerror = (event) => event.preventDefault()
    }).catch(error => logger.warn('[IndexedDB] Failed to update lastAccessed:', error))
  }

  async deleteTrack(trackId) {
    const transaction = await this._transaction([TRACK_STORE], 'readwrite')
    if (!transaction) return
    await this._run(transaction.objectStore(TRACK_STORE).delete(trackId), `Delete track ${trackId}`)
  }

  async getAllTracks() {
    const transaction = await this._transaction([TRACK_STORE], 'readonly')
    if (!transaction) return []
    const result = await this._run(transaction.objectStore(TRACK_STORE).getAll(), 'Get all tracks')
    return result || []
  }

  async getTracksByLastAccessed() {
    const transaction = await this._transaction([TRACK_STORE], 'readonly')
    if (!transaction) return []

    return new Promise((resolve, reject) => {
      const request = transaction.objectStore(TRACK_STORE).index('lastAccessed').openCursor(null, 'prev')
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

      request.onerror = (event) => {
        event.preventDefault()
        logger.error('[IndexedDB] Failed to get tracks by last accessed:', request.error)
        reject(request.error)
      }
    })
  }

  async getTotalSize() {
    const tracks = await this.getAllTracks()
    return tracks.reduce((total, track) => total + (track.size || 0), 0)
  }

  async clearAll() {
    const transaction = await this._transaction([TRACK_STORE], 'readwrite')
    if (!transaction) return
    await this._run(transaction.objectStore(TRACK_STORE).clear(), 'Clear tracks')
    logger.info('[IndexedDB] Cleared all tracks')
  }

  async setMetadata(key, value) {
    const transaction = await this._transaction([METADATA_STORE], 'readwrite')
    if (!transaction) return
    await this._run(transaction.objectStore(METADATA_STORE).put({ key, value }), `Set metadata ${key}`)
  }

  async getMetadata(key) {
    const transaction = await this._transaction([METADATA_STORE], 'readonly')
    if (!transaction) return null
    const result = await this._run(transaction.objectStore(METADATA_STORE).get(key), `Get metadata ${key}`)
    return result ? result.value : null
  }
}

export const audioCacheDB = new AudioCacheDB()
