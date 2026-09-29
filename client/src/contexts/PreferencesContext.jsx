import { logger } from '../lib/logger'
import { createContext, useContext, useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { api } from '../lib/api'
import { safeStorage } from '../lib/safeStorage'
import { backgroundDownloader } from '../lib/backgroundDownloader'
import { useAuth } from './AuthContext'
import { useWebSocketSubscribe, WebSocketContext } from './WebSocketContext'
import { useUIActions, uiState, useUISelector } from './UIStateContext'

const PreferencesContext = createContext(null)

const RADIO_MODE_STORAGE_KEY = 'radioMode'
const RADIO_MODE_TOGGLES = ['enabled', 'news', 'city', 'local', 'community', 'features', 'stings', 'reviews']
export const DEFAULT_RADIO_MODE = Object.freeze({
  enabled: false,
  news: true,
  city: true,
  local: true,
  community: true,
  features: true,
  stings: true,
  reviews: true,
  feature_interval_min: 20,
})
const DEFAULT_RADIO_OPTIONS = Object.freeze({ feature_intervals_min: [15, 20, 30], stings_outside_radio_mode: true })

function normalizeRadioMode(raw, intervals = DEFAULT_RADIO_OPTIONS.feature_intervals_min) {
  const next = { ...DEFAULT_RADIO_MODE }
  if (!raw || typeof raw !== 'object') return next
  RADIO_MODE_TOGGLES.forEach(key => {
    if (typeof raw[key] === 'boolean') next[key] = raw[key]
  })
  const interval = Number(raw.feature_interval_min)
  if (Number.isFinite(interval) && intervals.length) {
    next.feature_interval_min = intervals.reduce((best, value) => (
      Math.abs(value - interval) < Math.abs(best - interval) ? value : best
    ), intervals[0])
  }
  return next
}

function loadGuestRadioMode() {
  try {
    return normalizeRadioMode(JSON.parse(safeStorage.get(RADIO_MODE_STORAGE_KEY) || 'null'))
  } catch {
    return { ...DEFAULT_RADIO_MODE }
  }
}

const accountRadioModeKey = (userId) => `${RADIO_MODE_STORAGE_KEY}:user:${userId}`

function loadAccountRadioMode(userId) {
  try {
    const cached = safeStorage.get(accountRadioModeKey(userId))
    return cached ? normalizeRadioMode(JSON.parse(cached)) : { ...DEFAULT_RADIO_MODE }
  } catch {
    return { ...DEFAULT_RADIO_MODE }
  }
}

function saveAccountRadioMode(userId, settings) {
  if (userId != null) safeStorage.set(accountRadioModeKey(userId), JSON.stringify(settings))
}

function loadRadioModeFor(user) {
  return user?.id != null ? loadAccountRadioMode(user.id) : loadGuestRadioMode()
}

export function PreferencesProvider({ children }) {
  const { isAuthenticated, user } = useAuth()
  const { toastSuccess, toastError } = useUIActions()
  const { settingsState } = useUISelector(state => ({ settingsState: state.settingsState }))
  const { send: wsSend, connected: wsConnected } = useContext(WebSocketContext) || {}
  const success = toastSuccess
  const error = toastError

  const [radioMode, setRadioModeState] = useState(() => loadRadioModeFor(user))
  const [radioOptions, setRadioOptions] = useState(DEFAULT_RADIO_OPTIONS)
  const [radioModeSaving, setRadioModeSaving] = useState(false)
  const radioModeRef = useRef(radioMode)
  const radioModeRequestRef = useRef(0)
  const radioModeInflightRef = useRef(0)
  const userIdRef = useRef(user?.id ?? null)
  useEffect(() => { radioModeRef.current = radioMode }, [radioMode])
  useEffect(() => { userIdRef.current = user?.id ?? null }, [user?.id])
  const ttsMuted = !!settingsState.ttsMuted

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
  const opsRef = useRef(new Map())
  const inflightByTypeRef = useRef({ track: 0, shoutout: 0 })

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

  useEffect(() => {
    let cancelled = false
    if (!isAuthenticated) {
      setRadioModeState(loadGuestRadioMode())
      return
    }
    const userId = user?.id
    setRadioModeState(loadAccountRadioMode(userId))
    const requestAtStart = radioModeRequestRef.current
    api.getRadioMode()
      .then(data => {
        if (cancelled || !data?.settings) return
        const intervals = data.options?.feature_intervals_min?.length ? data.options.feature_intervals_min : DEFAULT_RADIO_OPTIONS.feature_intervals_min
        setRadioOptions({
          feature_intervals_min: intervals,
          stings_outside_radio_mode: data.options?.stings_outside_radio_mode !== false
        })
        if (radioModeRequestRef.current !== requestAtStart) return
        const saved = normalizeRadioMode(data.settings, intervals)
        saveAccountRadioMode(userId, saved)
        setRadioModeState(saved)
      })
      .catch(err => logger.warn('[Preferences] Radio Mode settings unavailable:', err))
    return () => { cancelled = true }
  }, [isAuthenticated, user?.id])

  useWebSocketSubscribe('radio_mode_updated', useCallback((data) => {
    if (!data?.settings || radioModeInflightRef.current > 0) return
    const saved = normalizeRadioMode(data.settings, radioOptions.feature_intervals_min)
    saveAccountRadioMode(userIdRef.current, saved)
    setRadioModeState(saved)
  }, [radioOptions]))

  useEffect(() => {
    if (isAuthenticated || !wsConnected || !wsSend) return
    void wsSend({
      type: 'radio_mode_prefs',
      data: { ...radioMode, enabled: radioMode.enabled && !ttsMuted }
    })
  }, [isAuthenticated, wsConnected, wsSend, radioMode, ttsMuted])

  const updateRadioMode = useCallback(async (patch) => {
    const previous = radioModeRef.current
    const next = normalizeRadioMode({ ...previous, ...patch }, radioOptions.feature_intervals_min)
    setRadioModeState(next)
    if (!isAuthenticated) {
      safeStorage.set(RADIO_MODE_STORAGE_KEY, JSON.stringify(next))
      return next
    }
    const requestId = ++radioModeRequestRef.current
    radioModeInflightRef.current += 1
    setRadioModeSaving(true)
    try {
      const data = await api.updateRadioMode(patch)
      const saved = normalizeRadioMode(data?.settings || next, radioOptions.feature_intervals_min)
      if (requestId === radioModeRequestRef.current) {
        saveAccountRadioMode(userIdRef.current, saved)
        setRadioModeState(saved)
      }
      return saved
    } catch (err) {
      logger.error('[Preferences] Failed to save Radio Mode settings:', err)
      if (requestId === radioModeRequestRef.current) setRadioModeState(previous)
      error(uiState.audioState.offlineMode ? err.message : 'Could not save Radio Mode settings')
      return previous
    } finally {
      radioModeInflightRef.current -= 1
      if (radioModeInflightRef.current === 0) setRadioModeSaving(false)
    }
  }, [isAuthenticated, radioOptions, error])

  const handlePreferenceChange = useCallback((data) => {
    if (isAuthenticated && user && data.user_id === user.id) {
      logger.info('Preference changed via WebSocket, refreshing:', data)
      if (!inflightByTypeRef.current.track) void loadPreferences('track')
      if (!inflightByTypeRef.current.shoutout) void loadPreferences('shoutout')
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

  const setItemPending = useCallback((type, id, pending) => {
    setPendingByType(prev => {
      const current = prev[type] || new Set()
      if (current.has(id) === pending) return prev
      const nextSet = new Set(current)
      if (pending) nextSet.add(id)
      else nextSet.delete(id)
      return { ...prev, [type]: nextSet }
    })
  }, [])

  const runPreferenceOp = useCallback(async (type, id, preferenceType) => {
    const key = `${type}:${id}`
    const entry = opsRef.current.get(key) || { chain: Promise.resolve(), version: 0 }
    const version = entry.version + 1
    entry.version = version
    opsRef.current.set(key, entry)

    if (preferenceType) updatePreferenceOptimistic(type, id, preferenceType)
    else removePreferenceOptimistic(type, id)
    setItemPending(type, id, true)
    inflightByTypeRef.current[type] = (inflightByTypeRef.current[type] || 0) + 1

    const run = entry.chain.then(async () => {
      if (entry.version !== version) return false
      if (preferenceType) await api.setPreference(type, id, preferenceType)
      else await api.removePreference(type, id)
      if (type === 'track' && (preferenceType === 'like' || preferenceType === 'super_like')) backgroundDownloader.wake()
      return true
    })
    entry.chain = run.catch(() => {})

    let failed = false
    try {
      const sent = await run
      if (sent && entry.version === version) {
        const label = type === 'track' ? 'Track' : 'Shoutout'
        const messages = {
          like: `${label} liked!`,
          super_like: `${label} super liked!`,
          ban: `${label} banned`
        }
        success(preferenceType ? (messages[preferenceType] || 'Preference updated') : 'Preference removed')
      }
    } catch (err) {
      failed = true
      logger.error(`Failed to update ${type} preference:`, err)
      if (entry.version === version) error('Failed to update preference. Please try again.')
    } finally {
      inflightByTypeRef.current[type] -= 1
      if (entry.version === version) {
        opsRef.current.delete(key)
        setItemPending(type, id, false)
      }
      if (inflightByTypeRef.current[type] === 0 || failed) {
        await loadPreferences(type)
      }
    }
  }, [updatePreferenceOptimistic, removePreferenceOptimistic, setItemPending, loadPreferences, success, error])

  const setPreference = useCallback((type, id, preferenceType) => {
    return runPreferenceOp(type, id, preferenceType)
  }, [runPreferenceOp])

  const removePreference = useCallback((type, id) => {
    return runPreferenceOp(type, id, null)
  }, [runPreferenceOp])

  const isPending = useCallback((type, id) => {
    return pendingByType[type]?.has(id) || false
  }, [pendingByType])

  const getPreferences = useCallback((type) => {
    return preferencesByType[type] || { likes: [], super_likes: [], bans: [] }
  }, [preferencesByType])

  const isLoading = useCallback((type) => {
    return loadingByType[type] || false
  }, [loadingByType])

  const value = useMemo(() => ({
    getPreference,
    setPreference,
    removePreference,
    isPending,
    loadPreferences,
    getPreferences,
    isLoading,
    radioMode,
    radioOptions,
    radioModeSaving,
    updateRadioMode
  }), [getPreference, setPreference, removePreference, isPending, loadPreferences, getPreferences, isLoading,
    radioMode, radioOptions, radioModeSaving, updateRadioMode])

  return (
    <PreferencesContext.Provider value={value}>
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