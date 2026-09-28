import { logger } from '../lib/logger'
import { useCallback, useContext, useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { safeStorage } from '../lib/safeStorage'
import { WebSocketContext } from '../contexts/WebSocketContext'
import { useUIState } from '../contexts/UIStateContext'

const DENIED_STORAGE_KEY = 'geolocationDenied'
const GUEST_PROMPT_DELAY_MS = 2500
const COORD_PRECISION = 10000
const PERMISSION_DENIED = 1
const ATTEMPTS = [
  { enableHighAccuracy: true, timeout: 30000, maximumAge: 300000 },
  { enableHighAccuracy: false, timeout: 15000, maximumAge: 600000 }
]

function roundCoord(value) {
  return Math.round(value * COORD_PRECISION) / COORD_PRECISION
}

function browserTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || ''
  } catch {
    return ''
  }
}

async function permissionState() {
  try {
    if (!navigator.permissions?.query) return 'unknown'
    const status = await navigator.permissions.query({ name: 'geolocation' })
    return status?.state || 'unknown'
  } catch {
    return 'unknown'
  }
}

function currentPosition(options) {
  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(resolve, reject, options)
  })
}

export function useGeolocation(isAuthenticated, options = {}) {
  const { periodicCheck = false, checkInterval = 30 * 60 * 1000 } = options
  const { send: wsSend, connected: wsConnected } = useContext(WebSocketContext) || {}
  const { engineState } = useUIState()
  const engaged = Boolean(engineState?.isMusicPlaying || engineState?.isAIProcessing ||
    engineState?.isMicRecording || engineState?.isDJSpeaking)

  const [location, setLocation] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [guestReady, setGuestReady] = useState(false)
  const [permission, setPermission] = useState(null)

  const authRef = useRef(isAuthenticated)
  const locationRef = useRef(null)
  const profileSentRef = useRef(null)
  const busyRef = useRef(false)
  const wsSendRef = useRef(wsSend)
  const wsConnectedRef = useRef(wsConnected)

  useEffect(() => {
    authRef.current = isAuthenticated
  }, [isAuthenticated])

  useEffect(() => {
    wsSendRef.current = wsSend
    wsConnectedRef.current = wsConnected
  }, [wsSend, wsConnected])

  const sendGuestLocation = useCallback((current) => {
    const send = wsSendRef.current
    if (!current || authRef.current || !send || !wsConnectedRef.current) return
    void send({
      type: 'listener_location',
      data: {
        latitude: current.latitude,
        longitude: current.longitude,
        accuracy_m: current.accuracy,
        timezone: current.timezone
      }
    })
  }, [])

  const fetchAddress = useCallback(async (latitude, longitude) => {
    const LOCATIONIQ_API_KEY = import.meta.env.VITE_LOCATIONIQ_API_KEY
    if (!LOCATIONIQ_API_KEY) {
      logger.warn('[Geolocation] VITE_LOCATIONIQ_API_KEY not set - skipping reverse geocoding')
      return 'Unknown Location'
    }

    const url = `https://us1.locationiq.com/v1/reverse?key=${LOCATIONIQ_API_KEY}&lat=${latitude}&lon=${longitude}&format=json&addressdetails=1`

    try {
      const response = await fetch(url)

      if (!response.ok) {
        logger.error(`[Geolocation] Geocoding failed: ${response.status}`)
        return 'Unknown Location'
      }

      const data = await response.json()
      const address = data?.address
      const parts = []

      if (address?.road) parts.push(address.road)
      if (address?.suburb) parts.push(address.suburb)
      else if (address?.neighbourhood) parts.push(address.neighbourhood)
      if (address?.city) parts.push(address.city)
      else if (address?.town) parts.push(address.town)
      else if (address?.village) parts.push(address.village)
      if (address?.state) parts.push(address.state)
      if (address?.country) parts.push(address.country)

      const fullAddress = parts.join(', ') || 'Unknown Location'
      logger.info('[Geolocation] Resolved address:', fullAddress)
      return fullAddress
    } catch (err) {
      logger.error('[Geolocation] Reverse geocoding failed:', err)
      return 'Unknown Location'
    }
  }, [])

  const saveProfileLocation = useCallback(async (current) => {
    const previous = profileSentRef.current
    if (previous && previous.latitude === current.latitude && previous.longitude === current.longitude &&
      previous.timezone === current.timezone) {
      logger.info('[Geolocation] No change detected, skipping backend update')
      return previous.address
    }
    const address = await fetchAddress(current.latitude, current.longitude)
    try {
      await api.updateUserProfile({
        location: address,
        latitude: String(current.latitude),
        longitude: String(current.longitude),
        timezone: current.timezone
      })
      profileSentRef.current = { ...current, address }
      logger.info('[Geolocation] Location/timezone updated:', address, current.timezone)
    } catch (apiErr) {
      logger.warn('[Geolocation] Failed to update profile, will retry later:', apiErr.message)
    }
    return address
  }, [fetchAddress])

  const grabLocation = useCallback(async () => {
    if (!navigator.geolocation) {
      logger.warn('[Geolocation] Not supported by browser')
      return
    }
    if (busyRef.current) return
    if (safeStorage.get(DENIED_STORAGE_KEY) && (await permissionState()) !== 'granted') {
      logger.info('[Geolocation] Location was declined earlier - not asking again')
      return
    }

    busyRef.current = true
    setLoading(true)
    setError(null)

    try {
      for (let i = 0; i < ATTEMPTS.length; i++) {
        try {
          const position = await currentPosition(ATTEMPTS[i])
          safeStorage.remove(DENIED_STORAGE_KEY)
          const current = {
            latitude: roundCoord(position.coords.latitude),
            longitude: roundCoord(position.coords.longitude),
            accuracy: Number.isFinite(position.coords.accuracy) ? Math.round(position.coords.accuracy) : null,
            timezone: browserTimezone()
          }
          locationRef.current = current

          if (authRef.current) {
            const address = await saveProfileLocation(current)
            setLocation({ ...current, address })
          } else {
            sendGuestLocation(current)
            setLocation(current)
          }
          return
        } catch (err) {
          if (err?.code === PERMISSION_DENIED) {
            safeStorage.set(DENIED_STORAGE_KEY, '1')
            setPermission('denied')
            setError(err.message || 'Location permission denied')
            logger.info('[Geolocation] Permission denied - will not ask again')
            return
          }
          if (i < ATTEMPTS.length - 1) {
            logger.warn(`[Geolocation] Attempt ${i + 1} failed, trying with lower accuracy:`, err?.message)
          } else {
            logger.warn('[Geolocation] All attempts failed, will retry later:', err?.message)
            setError(err?.message || 'Failed to get location')
          }
        }
      }
    } finally {
      busyRef.current = false
      setLoading(false)
    }
  }, [saveProfileLocation, sendGuestLocation])

  useEffect(() => {
    let cancelled = false
    void permissionState().then(state => {
      if (!cancelled) setPermission(state)
    })
    return () => { cancelled = true }
  }, [isAuthenticated])

  useEffect(() => {
    if (!isAuthenticated) return
    void grabLocation()
  }, [isAuthenticated, grabLocation])

  useEffect(() => {
    if (isAuthenticated || guestReady || permission === null) return
    if (permission === 'granted') {
      setGuestReady(true)
      return
    }
    if (permission === 'denied' || safeStorage.get(DENIED_STORAGE_KEY) || !engaged) return
    const timer = setTimeout(() => setGuestReady(true), GUEST_PROMPT_DELAY_MS)
    return () => clearTimeout(timer)
  }, [isAuthenticated, guestReady, permission, engaged])

  useEffect(() => {
    if (isAuthenticated || !guestReady) return
    void grabLocation()
  }, [isAuthenticated, guestReady, grabLocation])

  useEffect(() => {
    if (isAuthenticated || !wsConnected) return
    sendGuestLocation(locationRef.current)
  }, [isAuthenticated, wsConnected, sendGuestLocation])

  const active = isAuthenticated || guestReady

  useEffect(() => {
    if (!active || !periodicCheck) return

    const interval = setInterval(() => {
      logger.info('[Geolocation] Periodic check triggered')
      void grabLocation()
    }, checkInterval)

    return () => clearInterval(interval)
  }, [active, periodicCheck, checkInterval, grabLocation])

  return {
    location,
    loading,
    error,
    refreshLocation: grabLocation
  }
}
