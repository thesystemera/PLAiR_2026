import { logger } from './logger'
import { safeStorage } from './safeStorage'
import { getSessionIds } from './session'
import { offlineBackend } from './offlineAPI'
import { uiState } from '../contexts/UIStateContext'

const API_BASE = '/api'
const STREAM_BITRATES = new Set(['128k', '192k', '256k'])

class API {
  constructor() {
    this.token = null
    this.authRejectedListeners = new Set()
    this.connectivityListeners = new Set()
    this.syncingOfflineWrites = null
  }

  onConnectivity(listener) {
    this.connectivityListeners.add(listener)
    return () => this.connectivityListeners.delete(listener)
  }

  _notifyConnectivity(event) {
    this.connectivityListeners.forEach(listener => {
      try {
        listener(event)
      } catch (err) {
        logger.error('[API] Connectivity listener failed:', err)
      }
    })
  }

  reportServerTrouble(source) {
    this._notifyConnectivity({ type: 'trouble', source })
  }

  reportServerRecovered() {
    this._notifyConnectivity({ type: 'recovered' })
  }

  reportServerLost() {
    this._notifyConnectivity({ type: 'lost' })
  }

  async _replayProfileWrites(entry) {
    const currentUser = (() => {
      try {
        return JSON.parse(safeStorage.get('cached_user') || 'null')
      } catch {
        return null
      }
    })()
    if (!this.token || currentUser?.id !== entry.userId) return 0
    const { audio_quality: audioQuality, ...profileUpdates } = entry.updates
    try {
      if (audioQuality !== undefined) {
        const res = await this._fetch(`${API_BASE}/auth/audio-quality`, {
          method: 'PUT',
          headers: this.getHeaders(),
          body: JSON.stringify({ audio_quality: audioQuality }),
        })
        if (!res.ok) throw new Error(`audio quality sync failed (${res.status})`)
      }
      if (Object.keys(profileUpdates).length) {
        const res = await this._fetch(`${API_BASE}/user/profile`, {
          method: 'PUT',
          headers: this.getHeaders(),
          body: JSON.stringify(profileUpdates),
        })
        if (!res.ok) throw new Error(`profile sync failed (${res.status})`)
      }
      logger.info('[API] Synced offline settings changes:', Object.keys(entry.updates).join(', '))
      return 1
    } catch (err) {
      logger.warn('[API] Offline settings sync failed, will retry:', err.message)
      offlineBackend.restorePendingProfileWrites(entry)
      return 0
    }
  }

  async syncOfflineWrites() {
    if (this.syncingOfflineWrites) return this.syncingOfflineWrites
    const pending = offlineBackend.takePendingPreferenceWrites()
    const pendingProfile = offlineBackend.takePendingProfileWrites()
    if (!pending.length && !pendingProfile) return 0
    this.syncingOfflineWrites = (async () => {
      const failed = []
      for (const op of pending) {
        try {
          const url = `${API_BASE}/${op.type}s/${op.id}/preference`
          const res = op.preferenceType
            ? await this._fetch(url, { method: 'POST', headers: this.getHeaders(), body: JSON.stringify({ preference_type: op.preferenceType }) })
            : await this._fetch(url, { method: 'DELETE', headers: this.getHeaders() })
          if (!res.ok && res.status >= 500) failed.push(op)
        } catch {
          failed.push(op)
        }
      }
      if (failed.length) offlineBackend.restorePendingPreferenceWrites(failed)
      if (pending.length) logger.info(`[API] Synced ${pending.length - failed.length}/${pending.length} offline preference change(s)`)
      const profileSynced = pendingProfile ? await this._replayProfileWrites(pendingProfile) : 0
      return pending.length - failed.length + profileSynced
    })().finally(() => {
      this.syncingOfflineWrites = null
    })
    return this.syncingOfflineWrites
  }

  setToken(token) {
    this.token = token
  }

  onAuthRejected(listener) {
    this.authRejectedListeners.add(listener)
    return () => this.authRejectedListeners.delete(listener)
  }

  _notifyAuthRejected(token, url) {
    this.authRejectedListeners.forEach(listener => {
      try {
        listener({ token, url })
      } catch (err) {
        logger.error('[API] Auth rejection listener failed:', err)
      }
    })
  }

  async _fetch(url, options = {}) {
    let res
    try {
      res = await fetch(url, options)
    } catch (error) {
      if (error?.name !== 'AbortError') this.reportServerTrouble('network')
      throw error
    }
    if (res.status === 502 || res.status === 503 || res.status === 504) {
      this.reportServerTrouble(`http_${res.status}`)
    }
    if (res.status === 401) {
      const auth = options.headers?.Authorization
      const sentToken = typeof auth === 'string' && auth.startsWith('Bearer ') ? auth.slice(7) : null
      if (sentToken && sentToken === this.token) this._notifyAuthRejected(sentToken, url)
    }
    return res
  }

  async checkSession(token = this.token) {
    const res = await fetch(`${API_BASE}/auth/me`, {
      headers: { ...this.getHeaders(false), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      cache: 'no-store',
    })
    if (!res.ok) {
      const error = new Error(`Not authenticated (${res.status})`)
      error.status = res.status
      throw error
    }
    return res.json()
  }

  async refreshToken() {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: 'POST',
      headers: this.getHeaders(),
    })
    if (!res.ok) {
      const error = new Error(`Token refresh failed (${res.status})`)
      error.status = res.status
      throw error
    }
    return res.json()
  }

  getHeaders(includeAuth = true) {
    const headers = { 'Content-Type': 'application/json' }

    if (includeAuth && this.token) {
      headers['Authorization'] = `Bearer ${this.token}`
    }

    const session = getSessionIds()
    headers['X-Guest-ID'] = session.guestId
    headers['X-Device-ID'] = session.deviceId
    headers['X-Device-Name'] = session.deviceName
    headers['X-Device-Type'] = session.deviceType

    return headers
  }

  async put(url, data) {
    const res = await this._fetch(url, {
      method: 'PUT',
      headers: this.getHeaders(),
      body: JSON.stringify(data)
    })
    if (!res.ok) {
      const error = await res.json().catch(() => ({ detail: 'Request failed' }))
      throw new Error(error.detail || 'Request failed')
    }
    return res.json()
  }

  async _routeRequest(methodName, args, onlineFn) {
    const { isOnline, isServerAvailable } = uiState.audioState
    const hasOfflineSupport = typeof offlineBackend[methodName] === 'function'

    // Use offline backend if: (no internet OR server unavailable) AND method supports it
    if (hasOfflineSupport && (!isOnline || !isServerAvailable)) {
      const mode = uiState.audioState.connectionMode
      logger.info(`[API] 🔌 ${mode} - routing ${methodName} to offlineBackend`)
      return offlineBackend[methodName](...args)
    }
    return onlineFn()
  }

  async register(username, password) {
    return this._routeRequest('register', [username, password], async () => {
      logger.info('Registering user:', username)
      const res = await this._fetch(`${API_BASE}/auth/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      logger.info('Register response status:', res.status)
      if (!res.ok) {
        const error = await res.text()
        logger.error('Registration failed:', error)
        throw new Error(error || 'Registration failed')
      }
      return res.json()
    })
  }

  async login(username, password) {
    return this._routeRequest('login', [username, password], async () => {
      logger.info('Logging in user:', username)
      const res = await this._fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      logger.info('Login response status:', res.status)
      if (!res.ok) {
        const error = await res.text()
        logger.error('Login failed:', error)
        throw new Error(error || 'Invalid credentials')
      }
      return res.json()
    })
  }

  async getMe() {
    return this._routeRequest('getMe', [], async () => {
      const res = await this._fetch(`${API_BASE}/auth/me`, {
        headers: this.getHeaders(),
      })
      if (!res.ok) {
        const error = new Error(`Not authenticated (${res.status})`)
        error.status = res.status
        throw error
      }

      return res.json()
    })
  }

  async updateAudioQuality(audioQuality) {
    logger.info('[API] Updating audio quality to:', audioQuality)
    return this._routeRequest('updateAudioQuality', [audioQuality], async () => {
      const res = await this._fetch(`${API_BASE}/auth/audio-quality`, {
        method: 'PUT',
        headers: this.getHeaders(),
        body: JSON.stringify({ audio_quality: audioQuality }),
      })
      if (!res.ok) throw new Error('Failed to update audio quality')
      offlineBackend.clearPendingProfileKeys(['audio_quality'])
      const data = await res.json()
      logger.info('[API] Audio quality update response:', data)
      return data
    })
  }

  async setPreference(type, id, preferenceType) {
    return this._routeRequest('setPreference', [type, id, preferenceType], async () => {
      const res = await this._fetch(`${API_BASE}/${type}s/${id}/preference`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify({ preference_type: preferenceType }),
      })
      if (!res.ok) throw new Error('Failed to set preference')
      return res.json()
    })
  }

  async removePreference(type, id) {
    return this._routeRequest('removePreference', [type, id], async () => {
      const res = await this._fetch(`${API_BASE}/${type}s/${id}/preference`, {
        method: 'DELETE',
        headers: this.getHeaders(),
      })
      if (!res.ok) throw new Error('Failed to remove preference')
      return res.json()
    })
  }

  async getUserPreferences(type) {
    return this._routeRequest('getUserPreferences', [type], async () => {
      const endpoint = type === 'track' ? '/user/preferences' : `/user/${type}-preferences`
      const res = await this._fetch(`${API_BASE}${endpoint}`, {
        headers: this.getHeaders(),
      })
      if (!res.ok) throw new Error('Failed to get preferences')
      const data = await res.json()
      offlineBackend.rememberPreferences(type, data)
      return data
    })
  }

  async updateUserProfile(updates) {
    return this._routeRequest('updateUserProfile', [updates], async () => {
      const res = await this._fetch(`${API_BASE}/user/profile`, {
        method: 'PUT',
        headers: this.getHeaders(),
        body: JSON.stringify(updates),
      })
      if (!res.ok) throw new Error('Failed to update user profile')
      offlineBackend.clearPendingProfileKeys(Object.keys(updates))
      return res.json()
    })
  }

  async getTracks(skip = 0, limit = 100, sortBy = 'created_at', order = 'desc', genre = null) {
    return this._routeRequest('getTracks', [skip, limit, sortBy, order, genre], async () => {
      logger.info('[API] 🌐 ONLINE MODE - Fetching tracks from server')
      let url = `${API_BASE}/catalog/tracks?skip=${skip}&limit=${limit}&sort_by=${sortBy}&order=${order}`
      if (genre) {
        url += `&genre=${encodeURIComponent(genre)}`
      }
      const res = await this._fetch(url, {
        headers: this.getHeaders(),
      })
      return res.json()
    })
  }

  async getStats(genre = null) {
    return this._routeRequest('getStats', [genre], async () => {
      let url = `${API_BASE}/catalog/stats`
      if (genre) {
        url += `?genre=${encodeURIComponent(genre)}`
      }
      const res = await this._fetch(url)
      return res.json()
    })
  }

  async getGenres() {
    return this._routeRequest('getGenres', [], async () => {
      const res = await this._fetch(`${API_BASE}/catalog/genres`)
      return res.json()
    })
  }

  async getTrack(trackId) {
    return this._routeRequest('getTrack', [trackId], async () => {
      const res = await this._fetch(`${API_BASE}/track/${trackId}`)
      if (!res.ok) {
        throw new Error(`Track not found: ${trackId}`)
      }
      return res.json()
    })
  }

  async play(trackId = null) {
    return this._routeRequest('play', [trackId], async () => {
      const res = await this._fetch(`${API_BASE}/playback/play`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify({ track_id: trackId }),
      })
      return res.json()
    })
  }

  async addToQueue(trackIds) {
    return this._routeRequest('addToQueue', [trackIds], async () => {
      const res = await this._fetch(`${API_BASE}/queue/add`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify({ track_ids: trackIds }),
      })
      return res.json()
    })
  }

  async removeFromQueue(trackId) {
    return this._routeRequest('removeFromQueue', [trackId], async () => {
      const res = await this._fetch(`${API_BASE}/queue/remove/${trackId}`, {
        method: 'DELETE',
        headers: this.getHeaders(),
      })
      return res.json()
    })
  }

  async seedRadio(category = 'all', trackId = null) {
    return this._routeRequest('seedRadio', [category, trackId], async () => {
      const res = await this._fetch(`${API_BASE}/queue/seed`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify({ category, track_id: trackId })
      })
      return res.json()
    })
  }

  async searchSemantic(query, nResults = 50, useAiAnalysis = false) {
    return this._routeRequest('searchSemantic', [query, nResults], async () => {
      const mode = useAiAnalysis ? '🤖 AI-powered' : '⚡ Fast keyword'
      logger.info(`[API] 🌐 ONLINE MODE - ${mode} semantic search for "${query}"`)
      const res = await this._fetch(`${API_BASE}/search/semantic`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify({ query, n_results: nResults, use_ai_analysis: useAiAnalysis }),
      })
      return res.json()
    })
  }

  async getShoutout(shoutoutId) {
    return this._routeRequest('getShoutout', [shoutoutId], async () => {
      logger.info(`[API] 📝 Fetching shoutout ${shoutoutId}`)
      const res = await this._fetch(`${API_BASE}/user_content/shoutouts/${shoutoutId}`, {
        method: 'GET',
        headers: this.getHeaders(),
      })
      if (!res.ok) {
        if (res.status === 404) return null
        throw new Error(`Failed to fetch shoutout: ${res.statusText}`)
      }
      return res.json()
    })
  }

  async deleteShoutout(shoutoutId) {
    return this._routeRequest('deleteShoutout', [shoutoutId], async () => {
      logger.info(`[API] 🗑️ Deleting shoutout ${shoutoutId}`)
      const res = await this._fetch(`${API_BASE}/user_content/shoutouts/${shoutoutId}`, {
        method: 'DELETE',
        headers: this.getHeaders(),
        credentials: 'include',
      })
      return res.ok
    })
  }

  async searchShoutouts(query, nResults = 20, useAiAnalysis = false) {
    return this._routeRequest('searchShoutouts', [query, nResults], async () => {
      const mode = useAiAnalysis ? '🤖 AI-powered' : '⚡ Fast keyword'
      logger.info(`[API] 🔍 ${mode} shoutout search for "${query}"`)
      const res = await this._fetch(`${API_BASE}/user_content/shoutouts/search`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify({ query, n_results: nResults, use_ai_analysis: useAiAnalysis }),
      })
      return res.json()
    })
  }

  async getShoutoutReplies(shoutoutId, sortBy = 'popularity') {
    return this._routeRequest('getShoutoutReplies', [shoutoutId, sortBy], async () => {
      logger.info(`[API] 💬 Fetching replies for shoutout ${shoutoutId}`)
      const res = await this._fetch(`${API_BASE}/user_content/shoutouts/${shoutoutId}/replies?sort_by=${sortBy}`, {
        method: 'GET',
        headers: this.getHeaders(),
      })
      if (!res.ok) {
        if (res.status === 404) return { replies: [], count: 0 }
        throw new Error(`Failed to fetch replies: ${res.statusText}`)
      }
      return res.json()
    })
  }

  async uploadShoutoutReply(parentId, audioBase64) {
    return this._routeRequest('uploadShoutoutReply', [parentId, audioBase64], async () => {
      logger.info(`[API] 🎤 Uploading reply directly to shoutout ${parentId}`)
      const res = await this._fetch(`${API_BASE}/user_content/shoutouts/${parentId}/reply/upload`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify({ audio: audioBase64 }),
      })
      if (!res.ok) {
        const errorData = await res.json().catch(() => ({ detail: 'Failed to upload reply' }))
        throw new Error(errorData.detail || 'Failed to upload reply')
      }
      return res.json()
    })
  }

  async typeShoutoutReply(parentId, text) {
    return this._routeRequest('typeShoutoutReply', [parentId, text], async () => {
      logger.info(`[API] ⌨️ Posting typed reply to shoutout ${parentId}`)
      const res = await this._fetch(`${API_BASE}/user_content/shoutouts/${parentId}/reply/text`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify({ text }),
      })
      if (!res.ok) {
        const errorData = await res.json().catch(() => ({ detail: 'Failed to post reply' }))
        throw new Error(errorData.detail || 'Failed to post reply')
      }
      return res.json()
    })
  }

  async getMyCommunityPosts() {
    return this._routeRequest('getMyCommunityPosts', [], async () => {
      const res = await this._fetch(`${API_BASE}/user/community`, {
        method: 'GET',
        headers: this.getHeaders(),
      })
      if (!res.ok) throw new Error(`Failed to fetch your posts: ${res.statusText}`)
      return res.json()
    })
  }

  async getTrackReviews(trackId) {
    return this._routeRequest('getTrackReviews', [trackId], async () => {
      const res = await this._fetch(`${API_BASE}/tracks/${encodeURIComponent(trackId)}/reviews`, {
        method: 'GET',
        headers: this.getHeaders(),
      })
      if (!res.ok) {
        if (res.status === 404) return { reviews: [], count: 0, track_id: trackId }
        throw new Error(`Failed to fetch reviews: ${res.statusText}`)
      }
      return res.json()
    })
  }

  async uploadTrackReview(trackId, audioBase64) {
    return this._routeRequest('uploadTrackReview', [trackId, audioBase64], async () => {
      logger.info(`[API] 🎤 Uploading review for track ${trackId}`)
      const res = await this._fetch(`${API_BASE}/tracks/${encodeURIComponent(trackId)}/reviews/upload`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify({ audio: audioBase64 }),
      })
      if (!res.ok) {
        const errorData = await res.json().catch(() => ({ detail: 'Failed to upload review' }))
        throw new Error(errorData.detail || 'Failed to upload review')
      }
      return res.json()
    })
  }

  async typeTrackReview(trackId, text) {
    return this._routeRequest('typeTrackReview', [trackId, text], async () => {
      logger.info(`[API] ⌨️ Posting typed review for track ${trackId}`)
      const res = await this._fetch(`${API_BASE}/tracks/${encodeURIComponent(trackId)}/reviews/text`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify({ text }),
      })
      if (!res.ok) {
        const errorData = await res.json().catch(() => ({ detail: 'Failed to post review' }))
        throw new Error(errorData.detail || 'Failed to post review')
      }
      return res.json()
    })
  }

  async generate({ type = 'new', userRequest = null, sourceTrackId = null, batchCount = 3 }) {
    return this._routeRequest('generate', [{ type, userRequest, sourceTrackId, batchCount }], async () => {
      const body = {
        generation_type: type,
        batch_count: batchCount
      }

      if (type === 'new') {
        body.user_request = userRequest
      } else if (type === 'similar') {
        body.source_track_id = sourceTrackId
      }

      const res = await this._fetch(`${API_BASE}/generate`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const errorData = await res.json().catch(() => ({ detail: 'Generation failed' }))
        const err = new Error(errorData.detail || 'Generation failed')
        err.status = res.status
        throw err
      }
      return res.json()
    })
  }

  async cancelGenerationJob(jobId) {
    return this._routeRequest('cancelGenerationJob', [jobId], async () => {
      const res = await this._fetch(`${API_BASE}/generation-jobs/${jobId}`, {
        method: 'DELETE',
        headers: this.getHeaders(),
      })
      if (!res.ok) throw new Error('Failed to cancel generation job')
      return res.json()
    })
  }

  async getGenerationJobs() {
    return this._routeRequest('getGenerationJobs', [], async () => {
      const res = await this._fetch(`${API_BASE}/generation-jobs`, {
        headers: this.getHeaders(),
      })
      if (!res.ok) throw new Error('Failed to get generation jobs')
      return res.json()
    })
  }

  async getDevices() {
    return this._routeRequest('getDevices', [], async () => {
      const res = await this._fetch(`${API_BASE}/devices`, {
        headers: this.getHeaders(),
      })
      if (!res.ok) throw new Error('Failed to get devices')
      return res.json()
    })
  }

  async activateDevice(deviceId = null) {
    return this._routeRequest('activateDevice', [deviceId], async () => {
      const res = await this._fetch(`${API_BASE}/devices/activate`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify({ device_id: deviceId }),
      })
      if (!res.ok) throw new Error('Failed to activate device')
      return res.json()
    })
  }

  async renameDevice(deviceId, newName) {
    return this._routeRequest('renameDevice', [deviceId, newName], async () => {
      const res = await this._fetch(`${API_BASE}/devices/${deviceId}/name`, {
        method: 'PUT',
        headers: this.getHeaders(),
        body: JSON.stringify({ new_name: newName }),
      })
      if (!res.ok) throw new Error('Failed to rename device')
      return res.json()
    })
  }

  async removeDevice(deviceId) {
    return this._routeRequest('removeDevice', [deviceId], async () => {
      const res = await this._fetch(`${API_BASE}/devices/${deviceId}`, {
        method: 'DELETE',
        headers: this.getHeaders(),
      })
      if (!res.ok) throw new Error('Failed to remove device')
      return res.json()
    })
  }

  getStreamUrl(trackId, bitrate = null, purpose = 'play') {
    const baseUrl = `/api/stream/${trackId}/webm`
    const session = getSessionIds()
    const params = new URLSearchParams({
      guest_id: session.guestId,
      device_id: session.deviceId,
      purpose
    })

    if (STREAM_BITRATES.has(bitrate)) {
      params.set('bitrate', bitrate)
    }

    return `${baseUrl}?${params.toString()}`
  }

  getMp3StreamUrl(trackId, purpose = 'play') {
    const session = getSessionIds()
    const params = new URLSearchParams({
      guest_id: session.guestId,
      device_id: session.deviceId,
      purpose
    })
    return `/api/stream/${trackId}?${params.toString()}`
  }

  getRenderAudioUrl(trackId) {
    const baseUrl = `/api/stream/${trackId}`
    const session = getSessionIds()
    const params = new URLSearchParams({
      guest_id: session.guestId,
      device_id: session.deviceId,
      render: '1',
      t: String(Date.now())
    })

    return `${baseUrl}?${params.toString()}`
  }

  getArtworkUrl(trackId) {
    if (window.__artworkBlobCache && window.__artworkBlobCache[trackId]) {
      return window.__artworkBlobCache[trackId]
    }
    return `/api/artwork/${trackId}`
  }

  async getAudioFeatures(trackId) {
    return this._routeRequest('getAudioFeatures', [trackId], async () => {
      const res = await this._fetch(`${API_BASE}/audio-features/${trackId}`, {
        headers: this.getHeaders(false),
      })
      if (!res.ok) {
        throw new Error(`Failed to get audio features: ${res.status}`)
      }
      return res.json()
    })
  }

  async getLyricTimestamps(trackId) {
    return this._routeRequest('getLyricTimestamps', [trackId], async () => {
      const res = await this._fetch(`${API_BASE}/lyric-timestamps/${trackId}`, {
        headers: this.getHeaders(false),
      })
      if (!res.ok) {
        return null
      }
      return res.json()
    })
  }

  async getVideoClips(trackId) {
    return this._routeRequest('getVideoClips', [trackId], async () => {
      const res = await this._fetch(`${API_BASE}/video-clips/${trackId}`, {
        headers: this.getHeaders(),
      })
      if (!res.ok) {
        return { clips: [], reason: 'not_found' }
      }
      return res.json()
    })
  }

  async updateUsername(username) {
    return this._routeRequest('updateUsername', [username], async () => {
      const res = await this._fetch(`${API_BASE}/auth/update-username`, {
        method: 'POST',
        headers: this.getHeaders(true),
        body: JSON.stringify({ username }),
      })
      if (!res.ok) throw new Error('Failed to update username')
      return res.json()
    })
  }

  async deleteConversationHistory() {
    return this._routeRequest('deleteConversationHistory', [], async () => {
      const res = await this._fetch(`${API_BASE}/manage_user_data`, {
        method: 'POST',
        headers: this.getHeaders(true),
        body: JSON.stringify({ action: 'delete_conversations' }),
      })
      if (!res.ok) throw new Error('Failed to delete conversation history')
      return res.json()
    })
  }

  async resetPersona() {
    return this._routeRequest('resetPersona', [], async () => {
      const res = await this._fetch(`${API_BASE}/manage_user_data`, {
        method: 'POST',
        headers: this.getHeaders(true),
        body: JSON.stringify({ action: 'reset_persona' }),
      })
      if (!res.ok) throw new Error('Failed to reset persona')
      return res.json()
    })
  }

  async djTalk({ audio, text, context = 'generic_talk', voice_name = null }) {
    return this._routeRequest('djTalk', [{ audio, text, context, voice_name }], async () => {
      const res = await this._fetch(`${API_BASE}/dj/talk`, {
        method: 'POST',
        headers: this.getHeaders(true),
        body: JSON.stringify({
          audio,
          text,
          context,
          voice_name
        }),
      })
      if (!res.ok) {
        throw new Error(`Failed to contact DJ: ${res.status}`)
      }
      return res.json()
    })
  }

  async getConversationHistory(limit = 3) {
    return this._routeRequest('getConversationHistory', [limit], async () => {
      const res = await this._fetch(`${API_BASE}/conversation?limit=${limit}`, {
        method: 'GET',
        headers: this.getHeaders(true)
      })
      if (!res.ok) {
        throw new Error(`Failed to fetch conversation history: ${res.status}`)
      }
      return res.json()
    })
  }

  async getTimeline(hours = 6, kinds = null, limit = 150) {
    return this._routeRequest('getTimeline', [hours, kinds, limit], async () => {
      const kindParam = kinds?.length ? `&kinds=${encodeURIComponent(kinds.join(','))}` : ''
      const res = await this._fetch(`${API_BASE}/timeline?hours=${encodeURIComponent(hours)}&limit=${limit}${kindParam}`, {
        method: 'GET',
        headers: this.getHeaders(true)
      })
      if (!res.ok) throw new Error(`Failed to fetch the timeline: ${res.status}`)
      return res.json()
    })
  }

  async getTimelineEntry(entryId) {
    return this._routeRequest('getTimelineEntry', [entryId], async () => {
      const res = await this._fetch(`${API_BASE}/timeline/entry?id=${encodeURIComponent(entryId)}`, {
        method: 'GET',
        headers: this.getHeaders(true)
      })
      if (!res.ok) throw new Error(`Failed to fetch the timeline entry: ${res.status}`)
      return res.json()
    })
  }

  async validateCache(autoCleanup = false) {
    return offlineBackend.validateCache(autoCleanup)
  }

  async getUserUploads() {
    return this._routeRequest('getUserUploads', [], async () => {
      const res = await this._fetch(`${API_BASE}/user/music/tracks`, {
        headers: this.getHeaders(true)
      })
      if (!res.ok) {
        throw new Error(`Failed to fetch user uploads: ${res.status}`)
      }
      return res.json()
    })
  }

  async getShoutoutStats(category = null) {
    return this._routeRequest('getShoutoutStats', [category], async () => {
      let url = `${API_BASE}/user_content/stats`
      if (category) {
        url += `?category=${encodeURIComponent(category)}`
      }
      const res = await this._fetch(url)
      return res.json()
    })
  }

  async trackShoutoutPlay(data) {
    return this._routeRequest('trackShoutoutPlay', [data], async () => {
      await this._fetch(`${API_BASE}/analytics/shoutout/play`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify(data)
      })
      return { ok: true }
    })
  }

  async getTrackAnalytics(trackId) {
    return this._routeRequest('getTrackAnalytics', [trackId], async () => {
      const res = await this._fetch(`${API_BASE}/analytics/track/${trackId}`)
      if (!res.ok) return null
      return res.json()
    })
  }

  async getShoutoutAnalytics(shoutoutId) {
    return this._routeRequest('getShoutoutAnalytics', [shoutoutId], async () => {
      const res = await this._fetch(`${API_BASE}/analytics/shoutout/${shoutoutId}`)
      if (!res.ok) return null
      return res.json()
    })
  }

  async transcribe(audioBlob) {
    return this._routeRequest('transcribe', [audioBlob], async () => {
      const formData = new FormData()
      formData.append('audio', audioBlob, 'recording.webm')
      const headers = this.getHeaders()
      delete headers['Content-Type']
      const res = await this._fetch(`${API_BASE}/transcribe`, {
        method: 'POST',
        headers,
        body: formData
      })
      if (!res.ok) throw new Error('Transcription failed')
      return res.json()
    })
  }

  async deleteUserTrack(trackId) {
    return this._routeRequest('deleteUserTrack', [trackId], async () => {
      const res = await this._fetch(`${API_BASE}/user/music/tracks/${trackId}`, {
        method: 'DELETE',
        headers: this.getHeaders()
      })
      return { ok: res.ok }
    })
  }

  async uploadProfilePicture(file) {
    return this._routeRequest('uploadProfilePicture', [file], async () => {
      const formData = new FormData()
      formData.append('file', file)
      const headers = {}
      const auth = this.getHeaders().Authorization
      if (auth) headers.Authorization = auth
      const res = await this._fetch(`${API_BASE}/user/profile-picture`, {
        method: 'POST',
        headers,
        body: formData,
        credentials: 'include'
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || 'Upload failed')
      }
      return res.json()
    })
  }

  async deleteProfilePicture() {
    return this._routeRequest('deleteProfilePicture', [], async () => {
      const res = await this._fetch(`${API_BASE}/user/profile-picture`, {
        method: 'DELETE',
        headers: this.getHeaders(),
        credentials: 'include'
      })
      return { ok: res.ok }
    })
  }

  async _billingRequest(path, options, fallbackMessage) {
    const res = await this._fetch(`${API_BASE}/stripe/${path}`, { headers: this.getHeaders(), ...options })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      const err = new Error(data.detail || fallbackMessage)
      err.status = res.status
      throw err
    }
    return res.json()
  }

  async createStripeCheckout() {
    return this._routeRequest('createStripeCheckout', [], () =>
      this._billingRequest('create-checkout-session', { method: 'POST' }, 'Could not start checkout. Please try again.')
    )
  }

  async createBillingPortal() {
    return this._routeRequest('createBillingPortal', [], () =>
      this._billingRequest('portal', { method: 'POST' }, 'Could not open subscription management. Please try again.')
    )
  }

  async getBillingStatus(checkoutSessionId = null) {
    return this._routeRequest('getBillingStatus', [checkoutSessionId], () => {
      const query = checkoutSessionId ? `?${new URLSearchParams({ session_id: checkoutSessionId })}` : ''
      return this._billingRequest(`status${query}`, { method: 'GET' }, 'Could not load subscription status.')
    })
  }

  async uploadTrackArtwork(trackId, file) {
    return this._routeRequest('uploadTrackArtwork', [trackId, file], async () => {
      const formData = new FormData()
      formData.append('file', file)
      const headers = this.getHeaders()
      delete headers['Content-Type']
      const res = await this._fetch(`${API_BASE}/user/music/tracks/${trackId}/artwork`, {
        method: 'POST',
        headers,
        body: formData
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || 'Upload failed')
      }
      return res.json()
    })
  }

  async uploadShareVideo({ trackId, title, artist, blob }) {
    return this._routeRequest('uploadShareVideo', [trackId, title, artist], async () => {
      const formData = new FormData()
      formData.append('file', blob, `${trackId}_plair_share.mp4`)
      formData.append('track_id', trackId)
      formData.append('title', title || '')
      formData.append('artist', artist || '')

      const headers = this.getHeaders()
      delete headers['Content-Type']

      const res = await this._fetch(`${API_BASE}/share/video`, {
        method: 'POST',
        headers,
        body: formData
      })

      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || 'Failed to publish share video')
      }

      return res.json()
    })
  }

  async _jsonRequest(path, method, body, fallbackMessage) {
    const res = await this._fetch(`${API_BASE}${path}`, {
      method,
      headers: this.getHeaders(),
      ...(body === undefined ? {} : { body: JSON.stringify(body) })
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      const detail = Array.isArray(data.detail)
        ? data.detail.map(d => d?.msg).filter(Boolean).join('. ')
        : data.detail
      const err = new Error(detail || fallbackMessage)
      err.status = res.status
      throw err
    }
    return res.json()
  }

  async getUploadSetup() {
    return this._routeRequest('getUploadSetup', [], () =>
      this._jsonRequest('/user/music/setup', 'GET', undefined, 'Could not load your artists')
    )
  }

  async createArtist(data) {
    return this._routeRequest('createArtist', [data], () =>
      this._jsonRequest('/artists', 'POST', data, 'Could not add the artist')
    )
  }

  async updateArtist(artistId, data) {
    return this._routeRequest('updateArtist', [artistId, data], () =>
      this._jsonRequest(`/artists/${encodeURIComponent(artistId)}`, 'PUT', data, 'Could not save the artist')
    )
  }

  async deleteArtist(artistId) {
    return this._routeRequest('deleteArtist', [artistId], () =>
      this._jsonRequest(`/artists/${encodeURIComponent(artistId)}`, 'DELETE', undefined, 'Could not delete the artist')
    )
  }

  async getArtist(artistId) {
    return this._routeRequest('getArtist', [artistId], () =>
      this._jsonRequest(`/artists/${encodeURIComponent(artistId)}`, 'GET', undefined, 'Could not load the artist')
    )
  }

  async updateUserTrack(trackId, updates) {
    return this._routeRequest('updateUserTrack', [trackId, updates], () =>
      this._jsonRequest(`/user/music/tracks/${encodeURIComponent(trackId)}`, 'PUT', updates, 'Could not save the change')
    )
  }

  _sendUpload(url, formData, { onProgress, signal } = {}) {
    return new Promise((resolve, reject) => {
      const abortError = () => {
        const err = new Error('Upload cancelled')
        err.name = 'AbortError'
        return err
      }
      if (signal?.aborted) {
        reject(abortError())
        return
      }
      const headers = this.getHeaders()
      delete headers['Content-Type']
      const xhr = new XMLHttpRequest()
      const onAbortSignal = () => xhr.abort()
      const cleanup = () => signal?.removeEventListener('abort', onAbortSignal)
      xhr.open('POST', url)
      Object.entries(headers).forEach(([name, value]) => {
        if (value !== undefined && value !== null) xhr.setRequestHeader(name, String(value))
      })
      if (onProgress) {
        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable && event.total > 0) onProgress(Math.min(event.loaded / event.total, 1))
        }
      }
      xhr.onload = () => {
        cleanup()
        const status = xhr.status
        let data = {}
        try {
          data = xhr.responseText ? JSON.parse(xhr.responseText) : {}
        } catch {
          data = {}
        }
        if (status === 502 || status === 503 || status === 504) this.reportServerTrouble(`http_${status}`)
        if (status === 401 && headers.Authorization === `Bearer ${this.token}` && this.token) {
          this._notifyAuthRejected(this.token, url)
        }
        if (status >= 200 && status < 300) {
          resolve(data)
          return
        }
        const detail = status === 413
          ? 'Upload is larger than the server currently accepts. Video uploads support up to 10GB once the proxy limit is active.'
          : (typeof data.detail === 'string' ? data.detail : 'Upload failed')
        const err = new Error(detail)
        err.status = status
        reject(err)
      }
      xhr.onerror = () => {
        cleanup()
        this.reportServerTrouble('network')
        reject(new Error('Upload failed - check your connection and try again'))
      }
      xhr.onabort = () => {
        cleanup()
        reject(abortError())
      }
      signal?.addEventListener('abort', onAbortSignal)
      xhr.send(formData)
    })
  }

  async uploadMusic(file, uploadId = null, { artistProfileId = null, enableUpscaling = null, rightsConfirmed = false, onProgress = null, signal = null } = {}) {
    return this._routeRequest('uploadMusic', [file, uploadId, { artistProfileId, enableUpscaling, rightsConfirmed }], async () => {
      const sizeMb = file.size / (1024 * 1024)
      logger.info(`[API] Uploading media: ${file.name} (${sizeMb.toFixed(1)}MB, ${file.type || 'unknown type'})`)
      const formData = new FormData()
      formData.append('file', file)
      if (uploadId) formData.append('upload_id', uploadId)
      if (artistProfileId !== null && artistProfileId !== undefined) formData.append('artist_profile_id', String(artistProfileId))
      if (enableUpscaling !== null && enableUpscaling !== undefined) formData.append('enable_upscaling', enableUpscaling ? 'true' : 'false')
      if (rightsConfirmed) formData.append('rights_confirmed', 'true')
      return this._sendUpload(`${API_BASE}/user/music/upload`, formData, { onProgress, signal })
    })
  }

  async listUploadJobs() {
    return this._routeRequest('listUploadJobs', [], () =>
      this._jsonRequest('/user/music/uploads', 'GET', undefined, 'Could not load your uploads')
    )
  }

  async getUploadJob(uploadId) {
    return this._routeRequest('getUploadJob', [uploadId], () =>
      this._jsonRequest(`/user/music/uploads/${encodeURIComponent(uploadId)}`, 'GET', undefined, 'Could not check the upload')
    )
  }

  async cancelUploadJob(uploadId) {
    return this._routeRequest('cancelUploadJob', [uploadId], () =>
      this._jsonRequest(`/user/music/uploads/${encodeURIComponent(uploadId)}`, 'DELETE', undefined, 'Could not cancel the upload')
    )
  }

  async _getUsage(methodName, path, params = {}) {
    return this._routeRequest(methodName, [path, params], async () => {
      const query = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== ''))
      const res = await this._fetch(`${API_BASE}${path}${query.toString() ? `?${query}` : ''}`, {
        headers: this.getHeaders(),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || 'Failed to load usage stats')
      }
      return res.json()
    })
  }

  async getUsageSummary(period = 'month', start = null, end = null) {
    return this._getUsage('getUsageSummary', '/admin/usage/summary', { period, start, end })
  }

  async getUsageUsers(period = 'month', start = null, end = null) {
    return this._getUsage('getUsageUsers', '/admin/usage/users', { period, start, end })
  }

  async getUsageSubject(subject, period = 'month') {
    return this._getUsage('getUsageSubject', `/admin/usage/user/${encodeURIComponent(subject)}`, { period })
  }

  async getMyUsage(period = 'month') {
    return this._getUsage('getMyUsage', '/usage/me', { period })
  }

  async getRadioMode() {
    return this._routeRequest('getRadioMode', [], async () => {
      const res = await this._fetch(`${API_BASE}/radio-mode`, { headers: this.getHeaders() })
      if (!res.ok) throw new Error('Failed to load Radio Mode settings')
      return res.json()
    })
  }

  async updateRadioMode(updates) {
    return this._routeRequest('updateRadioMode', [updates], async () => {
      const res = await this._fetch(`${API_BASE}/radio-mode`, {
        method: 'PUT',
        headers: this.getHeaders(),
        body: JSON.stringify(updates),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || 'Failed to save Radio Mode settings')
      }
      return res.json()
    })
  }

  async fetchMusicBed(url) {
    if (typeof url !== 'string' || !url.startsWith(`${API_BASE}/music-beds/`)) {
      throw new Error('Invalid music bed url')
    }
    return this._routeRequest('fetchMusicBed', [url], async () => {
      const res = await this._fetch(url)
      if (!res.ok) throw new Error(`Music bed request failed (${res.status})`)
      return res.arrayBuffer()
    })
  }
}

export const api = new API()
