import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react'
import { useUIState } from './UIStateContext'
import { logger } from '../lib/logger'

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

// Server health check configuration
const SERVER_HEALTH = {
  CHECK_INTERVAL_MS: 10000,      // Check every 10s when uncertain
  FAST_CHECK_INTERVAL_MS: 5000,  // Check every 5s when recovering
  TIMEOUT_MS: 5000,              // Request timeout
  RECOVERY_THRESHOLD: 2,         // Consecutive successes to consider recovered
}

export function NetworkProvider({ children }) {
  const { publishAudioState } = useUIState()
  const [isOnline, setIsOnline] = useState(navigator.onLine)
  const [isServerAvailable, setIsServerAvailable] = useState(navigator.onLine)
  const [networkQuality, setNetworkQuality] = useState('good')
  const [connectionMode, setConnectionMode] = useState('full') // 'full' | 'degraded' | 'offline'

  const [detectedBitrate, setDetectedBitrate] = useState('192k')
  const [detectedSpeed, setDetectedSpeed] = useState(0)
  const [detectionMethod, setDetectionMethod] = useState(null)
  const [isDetecting, setIsDetecting] = useState(false)

  const lastDetectionTimeRef = useRef(0)
  const detectionCooldown = 60000
  const lastPublishedRef = useRef({ quality: null, bitrate: null, isOnline: null, isServerAvailable: null })
  const healthCheckTimeoutRef = useRef(null)
  const consecutiveSuccessesRef = useRef(0)
  const isCheckingRef = useRef(false)

  // Derived connection mode based on network and server state
  const deriveConnectionMode = useCallback((online, serverAvailable) => {
    if (!online) return 'offline'
    if (!serverAvailable) return 'degraded'
    return 'full'
  }, [])

  // Check server health via HTTP
  const checkServerHealth = useCallback(async () => {
    if (!navigator.onLine || isCheckingRef.current) {
      return isServerAvailable
    }

    isCheckingRef.current = true

    try {
      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), SERVER_HEALTH.TIMEOUT_MS)

      const response = await fetch('/api/health', {
        method: 'GET',
        cache: 'no-store',
        signal: controller.signal
      })

      clearTimeout(timeoutId)

      if (response.ok) {
        consecutiveSuccessesRef.current += 1
        if (!isServerAvailable && consecutiveSuccessesRef.current >= SERVER_HEALTH.RECOVERY_THRESHOLD) {
          logger.info('[Network] Server health check passed - entering full mode')
          setIsServerAvailable(true)
        }
        return true
      } else {
        consecutiveSuccessesRef.current = 0
        if (isServerAvailable) {
          logger.warn('[Network] Server health check failed - entering degraded mode')
          setIsServerAvailable(false)
        }
        return false
      }
    } catch (error) {
      consecutiveSuccessesRef.current = 0
      if (isServerAvailable) {
        logger.warn('[Network] Server health check error - entering degraded mode', error)
        setIsServerAvailable(false)
      }
      return false
    } finally {
      isCheckingRef.current = false
    }
  }, [isServerAvailable])

  // Handle online/offline events
  useEffect(() => {
    const handleOnline = () => {
      logger.info('[Network] Online event fired')
      setIsOnline(true)
      // Trigger immediate health check when coming back online
      checkServerHealth()
      publishAudioState({ isOnline: true })
    }

    const handleOffline = () => {
      logger.info('[Network] Offline event fired')
      setIsOnline(false)
      setIsServerAvailable(false)
      consecutiveSuccessesRef.current = 0
      publishAudioState({ isOnline: false, isServerAvailable: false })
    }

    window.addEventListener('online', handleOnline)
    window.addEventListener('offline', handleOffline)

    return () => {
      window.removeEventListener('online', handleOnline)
      window.removeEventListener('offline', handleOffline)
    }
  }, [publishAudioState, checkServerHealth])

  // Health check polling when online but server availability uncertain
  useEffect(() => {
    if (!isOnline) {
      // Clear any pending checks when going offline
      if (healthCheckTimeoutRef.current) {
        clearTimeout(healthCheckTimeoutRef.current)
        healthCheckTimeoutRef.current = null
      }
      return
    }

    const scheduleCheck = (delay) => {
      healthCheckTimeoutRef.current = setTimeout(async () => {
        await checkServerHealth()
        // Schedule next check based on current state
        const nextDelay = isServerAvailable
          ? SERVER_HEALTH.CHECK_INTERVAL_MS
          : SERVER_HEALTH.FAST_CHECK_INTERVAL_MS
        scheduleCheck(nextDelay)
      }, delay)
    }

    // Start polling
    scheduleCheck(SERVER_HEALTH.CHECK_INTERVAL_MS)

    return () => {
      if (healthCheckTimeoutRef.current) {
        clearTimeout(healthCheckTimeoutRef.current)
      }
    }
  }, [isOnline, isServerAvailable, checkServerHealth])

  // Update connection mode when dependencies change
  useEffect(() => {
    const newMode = deriveConnectionMode(isOnline, isServerAvailable)
    if (newMode !== connectionMode) {
      logger.info(`[Network] Connection mode changed: ${connectionMode} → ${newMode}`)
      setConnectionMode(newMode)
      publishAudioState({
        connectionMode: newMode,
        isServerAvailable: isServerAvailable
      })
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

  const value = {
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
  }

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
