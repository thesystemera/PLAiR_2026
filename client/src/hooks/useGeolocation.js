import { logger } from '../lib/logger'
import { useState, useEffect } from 'react'
import { api } from '../lib/api'

export function useGeolocation(isAuthenticated, options = {}) {
  const { periodicCheck = false, checkInterval = 30 * 60 * 1000 } = options
  const [location, setLocation] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!isAuthenticated) return

    void grabLocation()
  }, [isAuthenticated])

  useEffect(() => {
    if (!isAuthenticated || !periodicCheck) return

    const interval = setInterval(() => {
      logger.info('[Geolocation] Periodic check triggered')
      void grabLocation()
    }, checkInterval)

    return () => clearInterval(interval)
  }, [isAuthenticated, periodicCheck, checkInterval])

  const grabLocation = async () => {
    if (!isAuthenticated) {
      logger.info('[Geolocation] Skipping - user not authenticated')
      return
    }

    if (!navigator.geolocation) {
      logger.warn('[Geolocation] Not supported by browser')
      return
    }

    setLoading(true)
    setError(null)

    const attempts = [
      { enableHighAccuracy: true, timeout: 30000, maximumAge: 300000 },
      { enableHighAccuracy: false, timeout: 15000, maximumAge: 600000 }
    ]

    for (let i = 0; i < attempts.length; i++) {
      try {
        const position = await new Promise((resolve, reject) => {
          navigator.geolocation.getCurrentPosition(resolve, reject, attempts[i])
        })

        const latitude = position.coords.latitude.toString()
        const longitude = position.coords.longitude.toString()

        const address = await fetchAddress(latitude, longitude)
        const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone

        const hasChanged = !location ||
          location.latitude !== latitude ||
          location.longitude !== longitude ||
          location.timezone !== timezone

        if (hasChanged) {
          try {
            await api.updateUserProfile({
              location: address,
              latitude,
              longitude,
              timezone
            })
            logger.info('[Geolocation] Location/timezone updated:', address, timezone)
          } catch (apiErr) {
            logger.warn('[Geolocation] Failed to update profile, will retry later:', apiErr.message)
          }
        } else {
          logger.info('[Geolocation] No change detected, skipping backend update')
        }

        setLocation({
          latitude,
          longitude,
          address,
          timezone
        })
        setLoading(false)
        return // Success - exit
      } catch (err) {
        if (i < attempts.length - 1) {
          logger.warn(`[Geolocation] Attempt ${i + 1} failed, trying with lower accuracy:`, err.message)
        } else {
          logger.warn('[Geolocation] All attempts failed, will retry later:', err.message)
          setError(err.message || 'Failed to get location')
        }
      }
    }

    setLoading(false)
  }

  const fetchAddress = async (latitude, longitude) => {
    const LOCATIONIQ_API_KEY = import.meta.env.VITE_LOCATIONIQ_API_KEY || 'pk.658685f33dd314e341f22f40e3a7df0f'

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
  }

  return {
    location,
    loading,
    error,
    refreshLocation: grabLocation
  }
}