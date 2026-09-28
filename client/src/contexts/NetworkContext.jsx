import { createContext, useContext, useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useUIState } from './UIStateContext'
import { logger } from '../lib/logger'
import { api } from '../lib/api'
import { reportClientEvent } from '../lib/errorReporter'

const NetworkContext = createContext(null)

export const BITRATE_OPTIONS = ['auto', '128k', '192k', '256k']

const NETWORK_THRESHOLDS = {
  EXCELLENT: 2.0,
  GOOD: 1.0,
  FAIR: 0.5,
}

const BITRATE_RECOMMENDATIONS = {
  excellent: '256k',
  good: '192k',
  fair: '128k',
  poor: '128k',
}

const CONNECTION_TYPE_BITRATES = {
  '4g': '256k',
  '3g': '192k',
  '2g': '128k',
  'slow-2g': '128k',
  'wifi': '256k',
  'ethernet': '256k',
}

const SERVER_HEALTH = {
  CHECK_INTERVAL_MS: 10000,
  HIDDEN_CHECK_MS: 60000,
  SUSPECT_CHECK_MS: 1500,
  RECOVERY_CHECK_MS: 3000,
  TIMEOUT_MS: 4000,
  FAILURE_THRESHOLD: 2,
  RECOVERY_THRESHOLD: 2,
  FLAP_WINDOW_MS: 2 * 60 * 1000,
  MAX_FLAP_PENALTY: 3,
  TROUBLE_DEBOUNCE_MS: 1000,
  OFFLINE_EVENT_CONFIRM_MS: 2000,
}

export function NetworkProvider({ children }) {
  const { publishAudioState } = useUIState()
  const [isOnline, setIsOnline] = useState(navigator.onLine)
  const [isServerAvailable, setIsServerAvailable] = useState(navigator.onLine)
  const [networkQuality, setNetworkQuality] = useState('good')
  const [connectionMode, setConnectionMode] = useState(navigator.onLine ? 'full' : 'offline')

  const [detectedBitrate, setDetectedBitrate] = useState('192k')
  const [detectedSpeed, setDetectedSpeed] = useState(0)
  const [detectionMethod, setDetectionMethod] = useState(null)
  const [isDetecting, setIsDetecting] = useState(false)

  const lastDetectionTimeRef = useRef(0)
  const detectionCooldown = 60000
  const lastPublishedRef = useRef({ quality: null, bitrate: null, isOnline: null, isServerAvailable: null })
  const healthTimerRef = useRef(null)
  const serverAvailableRef = useRef(navigator.onLine)
  const failuresRef = useRef(0)
  const successesRef = useRef(0)
  const flapPenaltyRef = useRef(0)
  const lastRecoveryAtRef = useRef(0)
  const lastProbeAtRef = useRef(0)
  const probeRef = useRef(null)
  const offlineConfirmTimerRef = useRef(null)
  const scheduleHealthRef = useRef(null)
  const connectionModeRef = useRef(connectionMode)

  const deriveConnectionMode = useCallback((online, serverAvailable) => {
    if (!online) return 'offline'
    if (!serverAvailable) return 'degraded'
    return 'full'
  }, [])

  const markServerAvailable = useCallback((available) => {
    if (serverAvailableRef.current === available) return
    serverAvailableRef.current = available
    const now = Date.now()
    if (available) {
      lastRecoveryAtRef.current = now
      logger.info('[Network] Server reachable again - leaving degraded mode')
    } else {
      flapPenaltyRef.current = now - lastRecoveryAtRef.current < SERVER_HEALTH.FLAP_WINDOW_MS
        ? Math.min(flapPenaltyRef.current + 1, SERVER_HEALTH.MAX_FLAP_PENALTY)
        : 0
      logger.warn('[Network] Server unreachable (confirmed) - entering degraded mode')
      reportClientEvent('server_unreachable', `Server unreachable (browser ${navigator.onLine ? 'online' : 'offline'})`)
    }
    setIsServerAvailable(available)
  }, [])

  const recordProbe = useCallback((ok) => {
    if (ok) {
      failuresRef.current = 0
      successesRef.current += 1
      const needed = SERVER_HEALTH.RECOVERY_THRESHOLD + 2 * flapPenaltyRef.current
      if (!serverAvailableRef.current && successesRef.current >= needed) markServerAvailable(true)
    } else {
      successesRef.current = 0
      failuresRef.current += 1
      if (serverAvailableRef.current && failuresRef.current >= SERVER_HEALTH.FAILURE_THRESHOLD) markServerAvailable(false)
    }
  }, [markServerAvailable])

  const checkServerHealth = useCallback(async () => {
    if (!navigator.onLine) return false
    if (probeRef.current) return probeRef.current

    const probe = (async () => {
      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), SERVER_HEALTH.TIMEOUT_MS)
      try {
        const response = await fetch('/api/health', { method: 'GET', cache: 'no-store', signal: controller.signal })
        return response.ok
      } catch {
        return false
      } finally {
        clearTimeout(timeoutId)
      }
    })()

    probeRef.current = probe
    lastProbeAtRef.current = Date.now()
    try {
      const ok = await probe
      if (navigator.onLine) recordProbe(ok)
      return ok
    } finally {
      probeRef.current = null
      scheduleHealthRef.current?.()
    }
  }, [recordProbe])

  const scheduleHealthCheck = useCallback((delayOverride = null) => {
    if (healthTimerRef.current) {
      clearTimeout(healthTimerRef.current)
      healthTimerRef.current = null
    }
    if (!navigator.onLine) return
    const baseDelay = delayOverride ?? (!serverAvailableRef.current
      ? SERVER_HEALTH.RECOVERY_CHECK_MS
      : (failuresRef.current > 0 ? SERVER_HEALTH.SUSPECT_CHECK_MS : SERVER_HEALTH.CHECK_INTERVAL_MS))
    const delay = delayOverride === null && document.visibilityState === 'hidden' && serverAvailableRef.current && !failuresRef.current
      ? Math.max(baseDelay, SERVER_HEALTH.HIDDEN_CHECK_MS)
      : baseDelay
    healthTimerRef.current = setTimeout(() => {
      healthTimerRef.current = null
      void checkServerHealth()
    }, delay)
  }, [checkServerHealth])

  useEffect(() => { scheduleHealthRef.current = () => scheduleHealthCheck() }, [scheduleHealthCheck])

  useEffect(() => {
    void checkServerHealth()
    const handleVisible = () => {
      if (document.visibilityState === 'visible') scheduleHealthCheck(0)
    }
    document.addEventListener('visibilitychange', handleVisible)
    const unsubscribe = api.onConnectivity((event) => {
      if (event.type !== 'trouble' || probeRef.current) return
      if (Date.now() - lastProbeAtRef.current < SERVER_HEALTH.TROUBLE_DEBOUNCE_MS) return
      scheduleHealthCheck(0)
    })
    return () => {
      document.removeEventListener('visibilitychange', handleVisible)
      unsubscribe()
      if (healthTimerRef.current) {
        clearTimeout(healthTimerRef.current)
        healthTimerRef.current = null
      }
    }
  }, [checkServerHealth, scheduleHealthCheck])

  useEffect(() => {
    const handleOnline = () => {
      logger.info('[Network] Online event fired')
      if (offlineConfirmTimerRef.current) {
        clearTimeout(offlineConfirmTimerRef.current)
        offlineConfirmTimerRef.current = null
      }
      setIsOnline(true)
      scheduleHealthCheck(0)
    }

    const handleOffline = () => {
      logger.info('[Network] Offline event fired')
      if (offlineConfirmTimerRef.current) return
      offlineConfirmTimerRef.current = setTimeout(() => {
        offlineConfirmTimerRef.current = null
        if (navigator.onLine) return
        setIsOnline(false)
        failuresRef.current = SERVER_HEALTH.FAILURE_THRESHOLD
        successesRef.current = 0
        markServerAvailable(false)
        if (healthTimerRef.current) {
          clearTimeout(healthTimerRef.current)
          healthTimerRef.current = null
        }
      }, SERVER_HEALTH.OFFLINE_EVENT_CONFIRM_MS)
    }

    window.addEventListener('online', handleOnline)
    window.addEventListener('offline', handleOffline)

    return () => {
      window.removeEventListener('online', handleOnline)
      window.removeEventListener('offline', handleOffline)
      if (offlineConfirmTimerRef.current) {
        clearTimeout(offlineConfirmTimerRef.current)
        offlineConfirmTimerRef.current = null
      }
    }
  }, [markServerAvailable, scheduleHealthCheck])

  useEffect(() => {
    const newMode = deriveConnectionMode(isOnline, isServerAvailable)
    const previousMode = connectionModeRef.current
    connectionModeRef.current = newMode
    if (newMode !== connectionMode) setConnectionMode(newMode)
    publishAudioState({
      isOnline,
      connectionMode: newMode,
      isServerAvailable,
      offlineMode: newMode !== 'full'
    })
    if (newMode === previousMode) return
    logger.info(`[Network] Connection mode changed: ${previousMode} → ${newMode}`)
    if (newMode === 'full') {
      api.reportServerRecovered()
      void api.syncOfflineWrites()
    } else if (previousMode === 'full') {
      api.reportServerLost()
    }
  }, [isOnline, isServerAvailable, connectionMode, deriveConnectionMode, publishAudioState])

  const getQualityFromBitrate = useCallback((bitrate) => {
    switch (bitrate) {
      case '256k': return 'excellent'
      case '192k': return 'good'
      case '128k': return 'fair'
      default: return 'good'
    }
  }, [])

  const getQualityFromSpeed = useCallback((speedMbps) => {
    if (speedMbps >= NETWORK_THRESHOLDS.EXCELLENT) return 'excellent'
    if (speedMbps >= NETWORK_THRESHOLDS.GOOD) return 'good'
    if (speedMbps >= NETWORK_THRESHOLDS.FAIR) return 'fair'
    return 'poor'
  }, [])

  useEffect(() => {
    let quality
    let bitrate = '192k'

    if (!navigator.connection) {
      quality = 'good'
    } else {
      const effectiveType = navigator.connection.effectiveType
      bitrate = CONNECTION_TYPE_BITRATES[effectiveType] || '192k'
      quality = getQualityFromBitrate(bitrate)
    }

    const lastPub = lastPublishedRef.current

    if (
      quality === lastPub.quality &&
      bitrate === lastPub.bitrate &&
      isOnline === lastPub.isOnline &&
      isServerAvailable === lastPub.isServerAvailable
    ) {
      return
    }

    lastPublishedRef.current = { quality, bitrate, isOnline, isServerAvailable }
    setNetworkQuality(quality)
    setDetectedBitrate(bitrate)
    setDetectionMethod('connection-api')

    publishAudioState({
      networkQuality: quality,
      detectedBitrate: bitrate,
      isOnline,
      isServerAvailable
    })
  }, [getQualityFromBitrate, isOnline, isServerAvailable, publishAudioState])

  useEffect(() => {
    if (!navigator.connection) return

    const handleConnectionChange = () => {
      setDetectionMethod('connection-api')
    }

    navigator.connection.addEventListener('change', handleConnectionChange)
    return () => navigator.connection.removeEventListener('change', handleConnectionChange)
  }, [])

  const acknowledgeOfflineMode = useCallback(() => {
    logger.info('[Network] Offline mode acknowledged')
  }, [])

  const checkOfflineMode = useCallback(() => {
    return !navigator.onLine
  }, [])

  const measureDownloadSpeed = useCallback(async () => {
    try {
      const testUrl = '/api/health'
      const startTime = performance.now()
      const response = await fetch(testUrl, { cache: 'no-store' })
      await response.text()
      const endTime = performance.now()

      const duration = (endTime - startTime) / 1000
      const sizeBytes = parseInt(response.headers.get('content-length') || '1024', 10)
      const sizeMegabits = (sizeBytes * 8) / (1024 * 1024)
      const speedMbps = sizeMegabits / duration

      if (sizeBytes < 10000) {
        return await measureStreamSpeed()
      }

      return speedMbps
    } catch (_error) {
      logger.warn('[Network] Download speed test failed, trying stream method', _error)
      return await measureStreamSpeed()
    }
  }, [])

  const measureStreamSpeed = useCallback(async () => {
    try {
      const testSize = 100 * 1024
      const startTime = performance.now()

      const response = await fetch('/api/health', {
        headers: {
          'Range': `bytes=0-${testSize - 1}`,
        },
      })

      const blob = await response.blob()
      const endTime = performance.now()

      const duration = (endTime - startTime) / 1000
      const sizeBytes = blob.size
      const sizeMegabits = (sizeBytes * 8) / (1024 * 1024)
      const speedMbps = sizeMegabits / duration

      return Math.max(speedMbps, 0.5)
    } catch (error) {
      logger.warn('[Network] Stream speed test failed, using fallback', error)
      return 1.0
    }
  }, [])

  const detectNetworkQuality = useCallback(async () => {
    const now = Date.now()

    if (now - lastDetectionTimeRef.current < detectionCooldown && detectionMethod) {
      return {
        quality: networkQuality,
        bitrate: detectedBitrate,
        speed: detectedSpeed,
        method: detectionMethod
      }
    }

    if (isDetecting) {
      return {
        quality: networkQuality,
        bitrate: detectedBitrate,
        speed: detectedSpeed,
        method: detectionMethod
      }
    }

    setIsDetecting(true)

    try {
      if (navigator.connection && navigator.connection.effectiveType) {
        const conn = navigator.connection
        const bitrate = CONNECTION_TYPE_BITRATES[conn.effectiveType] || '192k'
        const quality = getQualityFromBitrate(bitrate)

        const result = {
          quality,
          bitrate,
          speed: conn.downlink || 0,
          method: 'connection-api',
          rtt: conn.rtt,
          effectiveType: conn.effectiveType
        }

        setNetworkQuality(quality)
        setDetectedBitrate(bitrate)
        setDetectedSpeed(conn.downlink || 0)
        setDetectionMethod('connection-api')
        lastDetectionTimeRef.current = now

        publishAudioState({
          networkQuality: quality,
          detectedBitrate: bitrate,
          detectedSpeed: conn.downlink || 0,
          isOnline
        })

        return result
      }

      const speed = await measureDownloadSpeed()
      const quality = getQualityFromSpeed(speed)
      const bitrate = BITRATE_RECOMMENDATIONS[quality]

      const result = {
        quality,
        bitrate,
        speed,
        method: 'speed-test'
      }

      setNetworkQuality(quality)
      setDetectedBitrate(bitrate)
      setDetectedSpeed(speed)
      setDetectionMethod('speed-test')
      lastDetectionTimeRef.current = now

      publishAudioState({
        networkQuality: quality,
        detectedBitrate: bitrate,
        detectedSpeed: speed,
        isOnline
      })

      return result
    } catch {
      const fallback = {
        quality: 'good',
        bitrate: '192k',
        speed: 0,
        method: 'fallback'
      }

      setNetworkQuality('good')
      setDetectedBitrate('192k')
      setDetectedSpeed(0)
      setDetectionMethod('fallback')

      return fallback
    } finally {
      setIsDetecting(false)
    }
  }, [
    networkQuality,
    detectedBitrate,
    detectedSpeed,
    detectionMethod,
    isDetecting,
    getQualityFromBitrate,
    getQualityFromSpeed,
    measureDownloadSpeed,
    publishAudioState,
    isOnline
  ])

  const getEffectiveBitrate = useCallback((userPreference = 'auto', isAuthenticated = false) => {
    if (userPreference !== 'auto') {
      return userPreference
    }

    const detected = detectedBitrate || '192k'

    if (!isAuthenticated && detected === '256k') {
      return '192k'
    }

    return detected
  }, [detectedBitrate])

  const value = useMemo(() => ({
    isOnline,
    isServerAvailable,
    connectionMode,
    networkQuality,
    detectedBitrate,
    detectedSpeed,
    detectionMethod,
    isDetecting,
    acknowledgeOfflineMode,
    checkOfflineMode,
    detectNetworkQuality,
    getEffectiveBitrate,
    checkServerHealth,
  }), [
    isOnline, isServerAvailable, connectionMode, networkQuality, detectedBitrate, detectedSpeed,
    detectionMethod, isDetecting, acknowledgeOfflineMode, checkOfflineMode, detectNetworkQuality,
    getEffectiveBitrate, checkServerHealth,
  ])

  return (
    <NetworkContext.Provider value={value}>
      {children}
    </NetworkContext.Provider>
  )
}

export function useNetwork() {
  const context = useContext(NetworkContext)
  if (!context) {
    throw new Error('useNetwork must be used within NetworkProvider')
  }
  return context
}
