import { logger } from './logger'
import { safeStorage } from './safeStorage'
import { STORAGE_KEYS, readJson, cachedUserId, writeJson, getOfflinePreferences, saveOfflinePreferences, idOf } from './offlineAPI'

export const writesMethods = {
  rememberPreferences(type, data) {
    if (!data || (type !== 'track' && type !== 'shoutout')) return
    const snapshot = {}
    const now = Date.now()
    const add = (items, prefType) => (items || []).forEach(item => {
      const id = idOf(item)
      if (id) snapshot[id] = { type: prefType, timestamp: now }
    })
    add(data.likes, 'like')
    add(data.super_likes, 'super_like')
    add(data.bans, 'ban')
    const pending = readJson(STORAGE_KEYS.PENDING_PREFERENCES, []).filter(op => op.type === type)
    pending.forEach(op => {
      if (op.preferenceType) snapshot[op.id] = { type: op.preferenceType, timestamp: op.at }
      else delete snapshot[op.id]
    })
    saveOfflinePreferences(type, snapshot)
  },

  _queuePendingWrite(type, id, preferenceType) {
    const pending = readJson(STORAGE_KEYS.PENDING_PREFERENCES, []).filter(op => !(op.type === type && op.id === id))
    pending.push({ type, id, preferenceType: preferenceType || null, at: Date.now() })
    writeJson(STORAGE_KEYS.PENDING_PREFERENCES, pending.slice(-500))
  },

  takePendingPreferenceWrites() {
    const pending = readJson(STORAGE_KEYS.PENDING_PREFERENCES, [])
    if (pending.length) safeStorage.remove(STORAGE_KEYS.PENDING_PREFERENCES)
    return pending
  },

  restorePendingPreferenceWrites(ops) {
    const current = readJson(STORAGE_KEYS.PENDING_PREFERENCES, [])
    const keys = new Set(current.map(op => `${op.type}:${op.id}`))
    writeJson(STORAGE_KEYS.PENDING_PREFERENCES, [...ops.filter(op => !keys.has(`${op.type}:${op.id}`)), ...current].slice(-500))
  },

  async setPreference(type, id, preferenceType) {
    try {
      const preferences = getOfflinePreferences(type)
      preferences[id] = {
        type: preferenceType,
        timestamp: Date.now(),
      }
      saveOfflinePreferences(type, preferences)
      this._queuePendingWrite(type, id, preferenceType)

      logger.info(`[OfflineBackend] Set ${type} preference ${preferenceType} for ${id}`)

      const idKey = type === 'track' ? 'track_id' : 'shoutout_id'
      return {
        status: 'ok',
        [idKey]: id,
        preference_type: preferenceType,
        offline: true,
      }
    } catch (err) {
      logger.error(`[OfflineBackend] setPreference failed for ${type}:`, err)
      throw err
    }
  },

  async removePreference(type, id) {
    try {
      const preferences = getOfflinePreferences(type)
      delete preferences[id]
      saveOfflinePreferences(type, preferences)
      this._queuePendingWrite(type, id, null)

      logger.info(`[OfflineBackend] Removed ${type} preference for ${id}`)

      const idKey = type === 'track' ? 'track_id' : 'shoutout_id'
      return {
        status: 'ok',
        [idKey]: id,
        offline: true,
      }
    } catch (err) {
      logger.error(`[OfflineBackend] removePreference failed for ${type}:`, err)
      throw err
    }
  },

  async getUserPreferences(type) {
    try {
      const preferences = getOfflinePreferences(type)

      const grouped = {
        likes: [],
        super_likes: [],
        bans: []
      }

      Object.entries(preferences).forEach(([id, data]) => {
        if (data.type === 'like') {
          grouped.likes.push(id)
        } else if (data.type === 'super_like') {
          grouped.super_likes.push(id)
        } else if (data.type === 'ban') {
          grouped.bans.push(id)
        }
      })

      return grouped
    } catch (err) {
      logger.error(`[OfflineBackend] getUserPreferences failed for ${type}:`, err)
      return { likes: [], super_likes: [], bans: [] }
    }
  },

  queueProfileUpdate(updates) {
    const userId = cachedUserId()
    if (userId === null) throw new Error('Sign in to save settings')
    const pending = readJson(STORAGE_KEYS.PENDING_PROFILE, null)
    const base = pending && pending.userId === userId ? pending.updates : {}
    writeJson(STORAGE_KEYS.PENDING_PROFILE, { userId, updates: { ...base, ...updates } })
    logger.info('[OfflineBackend] Settings change queued, will sync when the server is back:', Object.keys(updates).join(', '))
  },

  pendingProfileUpdates(userId) {
    const pending = readJson(STORAGE_KEYS.PENDING_PROFILE, null)
    return pending && pending.userId === userId ? pending.updates : null
  },

  clearPendingProfileKeys(keys) {
    const pending = readJson(STORAGE_KEYS.PENDING_PROFILE, null)
    if (!pending) return
    const updates = Object.fromEntries(Object.entries(pending.updates || {}).filter(([key]) => !keys.includes(key)))
    if (Object.keys(updates).length) writeJson(STORAGE_KEYS.PENDING_PROFILE, { ...pending, updates })
    else safeStorage.remove(STORAGE_KEYS.PENDING_PROFILE)
  },

  takePendingProfileWrites() {
    const pending = readJson(STORAGE_KEYS.PENDING_PROFILE, null)
    if (pending) safeStorage.remove(STORAGE_KEYS.PENDING_PROFILE)
    return pending
  },

  restorePendingProfileWrites(entry) {
    const current = readJson(STORAGE_KEYS.PENDING_PROFILE, null)
    if (current && current.userId !== entry.userId) return
    writeJson(STORAGE_KEYS.PENDING_PROFILE, { userId: entry.userId, updates: { ...entry.updates, ...(current?.updates || {}) } })
  },

  async updateUserProfile(updates) {
    this.queueProfileUpdate(updates)
    return { status: 'queued', offline: true }
  },

  async getMe() {
    try {
      const cachedUser = safeStorage.get('cached_user')
      if (cachedUser) {
        const userData = JSON.parse(cachedUser)
        logger.info('[OfflineBackend] Returning cached user data')
        return userData
      }

      logger.warn('[OfflineBackend] No cached user data available')
      return {
        status: 'error',
        error: 'No cached user data available. Please log in when online.',
        offline: true
      }
    } catch (err) {
      logger.error('[OfflineBackend] getMe failed:', err)
      return {
        status: 'error',
        error: 'Authentication check requires an internet connection',
        offline: true
      }
    }
  },
}
