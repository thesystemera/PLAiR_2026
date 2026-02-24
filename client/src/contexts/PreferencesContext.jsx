import { logger } from '../lib/logger'
import { createContext, useContext, useState, useEffect, useCallback, useMemo } from 'react'
import { api } from '../lib/api'
import { useAuth } from './AuthContext'
import { useWebSocketSubscribe } from './WebSocketContext'
import { useUIState } from './UIStateContext'

const PreferencesContext = createContext(null)

export function PreferencesProvider({ children }) {
  const { isAuthenticated, user } = useAuth()
  const { toastSuccess, toastError } = useUIState()
  const success = toastSuccess
  const error = toastError

  const [preferencesByType, setPreferencesByType] = useState({
    track: {
      likes: [],
      super_likes: [],
      bans: []
    },
    shoutout: {
      super_likes: [],
      likes: [],
      bans: []
    }
  })

  const [loadingByType, setLoadingByType] = useState({
    track: false,
    shoutout: false
  })

  const [pendingByType, setPendingByType] = useState({
    track: new Set(),
    shoutout: new Set()
  })

  const preferenceMaps = useMemo(() => {
    const buildMaps = (prefs) => {
      const likesMap = new Map()
      const superLikesMap = new Map()
      const bansMap = new Map()

      prefs.likes?.forEach(item => {
        const id = typeof item === 'string' ? item : item.id
        likesMap.set(id, 'like')
      })

      prefs.super_likes?.forEach(item => {
        const id = typeof item === 'string' ? item : item.id
        superLikesMap.set(id, 'super_like')
      })

      prefs.bans?.forEach(item => {
        const id = typeof item === 'string' ? item : item.id
        bansMap.set(id, 'ban')
      })

      return { likesMap, superLikesMap, bansMap }
    }

    return {
      track: buildMaps(preferencesByType.track),
      shoutout: buildMaps(preferencesByType.shoutout)
    }
  }, [preferencesByType])

  const getPreference = useCallback((type, id) => {
    const maps = preferenceMaps[type]
    if (!maps) return null

    if (maps.likesMap.has(id)) return 'like'
    if (maps.superLikesMap.has(id)) return 'super_like'
    if (maps.bansMap.has(id)) return 'ban'
    return null
  }, [preferenceMaps])

  const loadPreferences = useCallback(async (type) => {
    setLoadingByType(prev => ({ ...prev, [type]: true }))
    try {
      const data = await api.getUserPreferences(type)

      setPreferencesByType(prev => ({
        ...prev,
        [type]: data
      }))
    } catch (err) {
      logger.error(`Failed to load ${type} preferences:`, err)
    } finally {
      setLoadingByType(prev => ({ ...prev, [type]: false }))
    }
  }, [])

  useEffect(() => {
    if (isAuthenticated) {
      void loadPreferences('track')
      void loadPreferences('shoutout')
    } else {
      setPreferencesByType({
        track: { likes: [], super_likes: [], bans: [] },
        shoutout: { super_likes: [], likes: [], bans: [] }
      })
    }
  }, [isAuthenticated, loadPreferences])

  const handlePreferenceChange = useCallback((data) => {
    if (isAuthenticated && user && data.user_id === user.id) {
      logger.info('Preference changed via WebSocket, refreshing:', data)
      void loadPreferences('track')
      void loadPreferences('shoutout')
    }
  }, [isAuthenticated, user, loadPreferences])

  useWebSocketSubscribe('preference_change', handlePreferenceChange)

  const updatePreferenceOptimistic = useCallback((type, id, preferenceType) => {
    setPreferencesByType(prev => {
      const currentPrefs = prev[type]
      const filterOut = (arr) => arr.filter(item =>
        typeof item === 'string' ? item !== id : item.id !== id
      )

      const newPrefs = {
        likes: filterOut(currentPrefs.likes),
        super_likes: filterOut(currentPrefs.super_likes),
        bans: filterOut(currentPrefs.bans)
      }

      if (preferenceType === 'like') {
        newPrefs.likes.push({ id })
      } else if (preferenceType === 'super_like') {
        newPrefs.super_likes.push({ id })
      } else if (preferenceType === 'ban') {
        newPrefs.bans.push({ id })
      }

      return {
        ...prev,
        [type]: newPrefs
      }
    })
  }, [])

  const removePreferenceOptimistic = useCallback((type, id) => {
    setPreferencesByType(prev => {
      const currentPrefs = prev[type]
      const filterOut = (arr) => arr.filter(item =>
        typeof item === 'string' ? item !== id : item.id !== id
      )

      return {
        ...prev,
        [type]: {
          likes: filterOut(currentPrefs.likes),
          super_likes: filterOut(currentPrefs.super_likes),
          bans: filterOut(currentPrefs.bans)
        }
      }
    })
  }, [])

  const setPreference = useCallback(async (type, id, preferenceType) => {
    setPendingByType(prev => ({
      ...prev,
      [type]: new Set(prev[type]).add(id)
    }))

    updatePreferenceOptimistic(type, id, preferenceType)

    try {
      await api.setPreference(type, id, preferenceType)
      await loadPreferences(type)

      const messages = {
        like: `${type === 'track' ? 'Track' : 'Shoutout'} liked!`,
        super_like: `${type === 'track' ? 'Track' : 'Shoutout'} super liked!`,
        ban: `${type === 'track' ? 'Track' : 'Shoutout'} banned`
      }
      success(messages[preferenceType] || 'Preference updated')
    } catch (err) {
      logger.error(`Failed to set ${type} preference:`, err)
      error('Failed to update preference. Please try again.')
      await loadPreferences(type)
    } finally {
      setPendingByType(prev => {
        const next = { ...prev }
        const newSet = new Set(prev[type])
        newSet.delete(id)
        next[type] = newSet
        return next
      })
    }
  }, [updatePreferenceOptimistic, loadPreferences, success, error])

  const removePreference = useCallback(async (type, id) => {
    setPendingByType(prev => ({
      ...prev,
      [type]: new Set(prev[type]).add(id)
    }))

    removePreferenceOptimistic(type, id)

    try {
      await api.removePreference(type, id)
      await loadPreferences(type)
      success('Preference removed')
    } catch (err) {
      logger.error(`Failed to remove ${type} preference:`, err)
      error('Failed to update preference. Please try again.')
      await loadPreferences(type)
    } finally {
      setPendingByType(prev => {
        const next = { ...prev }
        const newSet = new Set(prev[type])
        newSet.delete(id)
        next[type] = newSet
        return next
      })
    }
  }, [removePreferenceOptimistic, loadPreferences, success, error])

  const isPending = useCallback((type, id) => {
    return pendingByType[type]?.has(id) || false
  }, [pendingByType])

  const getPreferences = useCallback((type) => {
    return preferencesByType[type] || { likes: [], super_likes: [], bans: [] }
  }, [preferencesByType])

  const isLoading = useCallback((type) => {
    return loadingByType[type] || false
  }, [loadingByType])

  return (
    <PreferencesContext.Provider
      value={{
        getPreference,
        setPreference,
        removePreference,
        isPending,
        loadPreferences,
        getPreferences,
        isLoading
      }}
    >
      {children}
    </PreferencesContext.Provider>
  )
}

export function usePreferences() {
  const context = useContext(PreferencesContext)
  if (!context) {
    throw new Error('usePreferences must be used within PreferencesProvider')
  }
  return context
}