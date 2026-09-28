import { logger } from '../lib/logger'
import { createContext, useContext, useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { cacheManager } from '../lib/cacheManager'
import { useUISelector } from './UIStateContext'
import { useNetwork } from './NetworkContext'

const StorageContext = createContext(null)

let refreshStorageCallback = null

export function setStorageRefreshCallback(callback) {
  refreshStorageCallback = callback
}

export function triggerStorageRefresh() {
  if (refreshStorageCallback) {
    refreshStorageCallback()
  }
}

export function StorageProvider({ children }) {
  const {
    toastSuccess,
    toastError,
    audioState,
  } = useUISelector(state => ({
    toastSuccess: state.toastSuccess,
    toastError: state.toastError,
    audioState: state.audioState,
  }))
  const success = toastSuccess
  const showError = toastError
  const { networkQuality } = useNetwork()

  const [storageInfo, setStorageInfo] = useState({
    usedBytes: 0,
    maxBytes: 2 * 1024 * 1024 * 1024,
    usedPercentage: 0,
    trackCount: 0,
    tracks: [],
    browserQuota: 0,
    browserUsage: 0
  })
  const [dataUsage, setDataUsage] = useState({
    totalDownloaded: 0,
    sessionDownloaded: 0
  })
  const [loading, setLoading] = useState(true)
  const updateTimeoutRef = useRef(null)

  const refreshStorageInfo = useCallback(async () => {
    try {
      const info = await cacheManager.getStorageInfo()
      setStorageInfo(info)
    } catch (err) {
      logger.error('[Storage] Failed to refresh storage info:', err)
    }
  }, [])

  const refreshDataUsage = useCallback(async () => {
    try {
      const usage = await cacheManager.getDataUsage()
      setDataUsage(usage)
    } catch (err) {
      logger.error('[Storage] Failed to refresh data usage:', err)
    }
  }, [])

  useEffect(() => {
    const init = async () => {
      try {
        await cacheManager.initialize()
        await refreshStorageInfo()
        await refreshDataUsage()
        setLoading(false)
      } catch (err) {
        logger.error('[Storage] Initialization failed:', err)
        setLoading(false)
      }
    }
    void init()
  }, [refreshStorageInfo, refreshDataUsage])

  const scheduleStorageUpdate = useCallback(() => {
    if (updateTimeoutRef.current) {
      clearTimeout(updateTimeoutRef.current)
    }
    updateTimeoutRef.current = setTimeout(() => {
      void refreshStorageInfo()
      void refreshDataUsage()
    }, 500)
  }, [refreshStorageInfo, refreshDataUsage])

  useEffect(() => {
    setStorageRefreshCallback(scheduleStorageUpdate)
    const unsubscribe = cacheManager.onChange(scheduleStorageUpdate)
    return () => {
      setStorageRefreshCallback(null)
      unsubscribe()
    }
  }, [scheduleStorageUpdate])

  const cacheTrack = useCallback(async (trackId, trackInfo = {}, bitrate = '192k') => {
    try {
      await cacheManager.downloadAndCacheTrack(trackId, trackInfo, bitrate)
      scheduleStorageUpdate()
      success('Track cached for offline use')
      return true
    } catch (err) {
      logger.error('[Storage] Failed to cache track:', err)
      showError('Failed to cache track')
      return false
    }
  }, [success, showError, scheduleStorageUpdate])

  const isCached = useCallback(async (trackId) => {
    return await cacheManager.isCached(trackId)
  }, [])

  const getCachedTrack = useCallback(async (trackId) => {
    return await cacheManager.getCachedTrack(trackId)
  }, [])

  const deleteTrack = useCallback(async (trackId) => {
    try {
      await cacheManager.deleteTrack(trackId)
      await refreshStorageInfo()
      success('Track removed from cache')
    } catch (err) {
      logger.error('[Storage] Failed to delete track:', err)
      showError('Failed to delete track')
    }
  }, [success, showError, refreshStorageInfo])

  const clearAllCache = useCallback(async () => {
    try {
      await cacheManager.clearAllCache()

      if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
        navigator.serviceWorker.controller.postMessage({ type: 'CLEAR_AUDIO_CACHE' })
      }

      await refreshStorageInfo()
      success('All cached tracks cleared')
    } catch (err) {
      logger.error('[Storage] Failed to clear cache:', err)
      showError('Failed to clear cache')
    }
  }, [success, showError, refreshStorageInfo])

  const resetSessionUsage = useCallback(async () => {
    try {
      await cacheManager.resetSessionUsage()
      await refreshDataUsage()
    } catch (err) {
      logger.error('[Storage] Failed to reset session usage:', err)
    }
  }, [refreshDataUsage])

  const isOnline = audioState.isOnline

  const value = useMemo(() => ({
    storageInfo,
    dataUsage,
    refreshStorageInfo,
    refreshDataUsage,
    loading,

    isOnline,
    networkQuality,

    cacheTrack,
    isCached,
    getCachedTrack,
    deleteTrack,
    clearAllCache,

    resetSessionUsage,
  }), [
    storageInfo, dataUsage, refreshStorageInfo, refreshDataUsage, loading, isOnline, networkQuality,
    cacheTrack, isCached, getCachedTrack, deleteTrack, clearAllCache, resetSessionUsage,
  ])

  return (
    <StorageContext.Provider value={value}>
      {children}
    </StorageContext.Provider>
  )
}

export function useStorage() {
  const context = useContext(StorageContext)
  if (!context) {
    throw new Error('useStorage must be used within StorageProvider')
  }
  return context
}