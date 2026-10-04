import { logger } from './logger'
import { safeStorage } from './safeStorage'
import { cacheManager } from './cacheManager'
import { api } from './api'
import { uiState, updateDownloadState, onAuthStateChange } from '../contexts/UIStateContext'
import { triggerStorageRefresh } from '../contexts/StorageContext'

const SLOW_CONNECTIONS = new Set(['slow-2g', '2g', '3g'])
const NO_ROOM_SLEEP_MS = 30 * 60 * 1000
const MIN_DOWNLOAD_MBPS = 1
const SLOW_BACKOFF_MS = 15 * 60 * 1000

class BackgroundDownloader {
  constructor() {
    this.isRunning = false
    this.downloadQueue = []
    this.abortController = null
    this.wakeSleep = null
    this.slowUntil = 0
    this.dailyDownloadedBytes = parseInt((() => { try { return localStorage.getItem('dailyDownloadedBytes') } catch { return null } })() || '0', 10)
    this.lastResetDate = (() => { try { return localStorage.getItem('lastResetDate') } catch { return null } })() || new Date().toDateString()

    this.resetDailyLimitIfNeeded()

    api.onConnectivity((event) => {
      if (event.type === 'recovered') this.wake()
    })

    onAuthStateChange((authState) => {
      if (authState.isAuthenticated && uiState.settingsState.backgroundDownloads && !this.isRunning) {
        logger.info('[BackgroundDownloader] Auto-starting after authentication')
        void this.start()
      }
    })

    setTimeout(() => {
      if (uiState.authState?.isAuthenticated && uiState.settingsState.backgroundDownloads && !this.isRunning) {
        logger.info('[BackgroundDownloader] Auto-starting (cached session)')
        void this.start()
      }
    }, 2000)
  }

  resetDailyLimitIfNeeded() {
    const today = new Date().toDateString()
    if (this.lastResetDate !== today) {
      logger.info('[BackgroundDownloader] New day - resetting daily download counter')
      this.dailyDownloadedBytes = 0
      this.lastResetDate = today
      safeStorage.set('dailyDownloadedBytes', '0')
      safeStorage.set('lastResetDate', today)
    }
  }

  async start() {
    if (this.isRunning) {
      logger.info('[BackgroundDownloader] Already running')
      return
    }

    logger.info('[BackgroundDownloader] Starting background download service')
    this.isRunning = true
    await this.run()
  }

  stop() {
    logger.info('[BackgroundDownloader] Stopping background download service')
    this.isRunning = false
    if (this.abortController) {
      this.abortController.abort()
      this.abortController = null
    }
  }

  toggle(enabled) {
    if (enabled) {
      void this.start()
    } else {
      this.stop()
    }
  }

  shouldDownload() {
    const { audioState, settingsState } = uiState

    if (!safeStorage.get('cached_user')) {
      return false
    }

    if (!settingsState.backgroundDownloads) {
      return false
    }

    if (!audioState.isOnline || audioState.offlineMode || !cacheManager.isAvailable) {
      return false
    }

    if (settingsState.dataSaverMode) {
      return false
    }

    if (Date.now() < this.slowUntil) {
      return false
    }

    const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection
    if (connection) {
      if (connection.saveData) return false
      if (connection.type === 'cellular') return false
      if (connection.effectiveType && SLOW_CONNECTIONS.has(connection.effectiveType)) return false
    }

    this.resetDailyLimitIfNeeded()

    if (this.dailyDownloadedBytes >= uiState.downloadState.dailyLimit) {
      logger.info('[BackgroundDownloader] Daily download limit reached')
      return false
    }

    return true
  }

  async buildPriorityQueue() {
    const queue = []

    try {
      const preferences = await api.getUserPreferences('track')

      const superLiked = preferences.super_likes || []
      const liked = preferences.likes || []

      for (const track of superLiked) {
        if (track?.id && !(await cacheManager.isCached(track.id))) {
          queue.push({ track, priority: 3 })
        }
      }

      for (const track of liked) {
        if (track?.id && !(await cacheManager.isCached(track.id))) {
          queue.push({ track, priority: 2 })
        }
      }

      logger.info(`[BackgroundDownloader] Built priority queue: ${superLiked.length} super-likes, ${liked.length} likes`)
    } catch (error) {
      logger.error('[BackgroundDownloader] Failed to build priority queue:', error)
    }

    queue.sort((a, b) => b.priority - a.priority)

    return queue
  }

  async run() {
    while (this.isRunning) {
      try {
        if (!this.shouldDownload()) {
          this.resetDailyLimitIfNeeded()

          if (this.dailyDownloadedBytes >= uiState.downloadState.dailyLimit) {
            const eightHours = 8 * 60 * 60 * 1000
            logger.info(`[BackgroundDownloader] Daily limit reached. Sleeping for 8 hours`)
            await this.sleep(eightHours)
            continue
          }

          await this.sleep(10000)
          continue
        }

        if (this.downloadQueue.length === 0) {
          this.downloadQueue = await this.buildPriorityQueue()

          if (this.downloadQueue.length === 0) {
            logger.info('[BackgroundDownloader] No tracks to download')
            await this.sleep(60000)
            continue
          }

          updateDownloadState({
            totalQueued: this.downloadQueue.length
          })
        }

        if (!(await cacheManager.canMakeRoomFor())) {
          logger.info('[BackgroundDownloader] Download space is full of liked tracks - pausing downloads')
          this.downloadQueue = []
          await this.sleep(NO_ROOM_SLEEP_MS)
          continue
        }

        const item = this.downloadQueue.shift()
        await this.downloadTrack(item.track)

        await this.sleep(2000)

      } catch (error) {
        logger.error('[BackgroundDownloader] Error in run loop:', error)
        await this.sleep(5000)
      }
    }
  }

  async downloadTrack(track) {
    if (!track || !track.id) return

    try {
      const trackId = track.id
      const title = track.generation_params?.title || track.title || 'Unknown'

      logger.info(`[BackgroundDownloader] Downloading track: ${title}`)

      updateDownloadState({
        isDownloading: true,
        currentTrackId: trackId,
        currentTrackTitle: title
      })

      const bitrate = '192k'

      this.abortController = new AbortController()
      const startedAt = performance.now()

      await cacheManager.downloadAndCacheTrack(
        trackId,
        track,
        bitrate,
        null,
        this.abortController.signal
      )

      const cached = await cacheManager.getCachedTrack(trackId)
      const downloadedBytes = cached?.audioBlob?.size || 0
      const seconds = (performance.now() - startedAt) / 1000
      const mbps = seconds > 0 ? (downloadedBytes * 8) / (seconds * 1024 * 1024) : Infinity
      if (downloadedBytes && mbps < MIN_DOWNLOAD_MBPS) {
        this.slowUntil = Date.now() + SLOW_BACKOFF_MS
        logger.info(`[BackgroundDownloader] Slow connection (${mbps.toFixed(2)} Mbps) - pausing downloads for 15 minutes`)
      }

      this.dailyDownloadedBytes += downloadedBytes
      safeStorage.set('dailyDownloadedBytes', String(this.dailyDownloadedBytes))

      updateDownloadState({
        isDownloading: false,
        currentTrackId: null,
        currentTrackTitle: null,
        downloadedCount: (uiState.downloadState.downloadedCount || 0) + 1,
        dailyDownloadedBytes: this.dailyDownloadedBytes
      })

      triggerStorageRefresh()

      logger.info(`[BackgroundDownloader] Successfully downloaded: ${title} (${(downloadedBytes / 1024 / 1024).toFixed(2)} MB)`)

    } catch (error) {
      if (error.name === 'AbortError') {
        logger.info('[BackgroundDownloader] Download aborted')
      } else {
        logger.error('[BackgroundDownloader] Failed to download track:', error)
      }

      updateDownloadState({
        isDownloading: false,
        currentTrackId: null,
        currentTrackTitle: null
      })
    } finally {
      this.abortController = null
    }
  }

  wake() {
    this.downloadQueue = []
    this.wakeSleep?.()
  }

  sleep(ms) {
    return new Promise(resolve => {
      const timer = setTimeout(() => {
        this.wakeSleep = null
        resolve()
      }, ms)
      this.wakeSleep = () => {
        clearTimeout(timer)
        this.wakeSleep = null
        resolve()
      }
    })
  }
}

export const backgroundDownloader = new BackgroundDownloader()