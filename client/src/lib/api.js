import { logger } from './logger'
import { getSessionIds } from './session'
import { offlineBackend } from './offlineAPI'
import { uiState } from '../contexts/UIStateContext'

const API_BASE = '/api'

class API {
  constructor() {
    this.token = null
  }

  setToken(token) {
    this.token = token
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
    const res = await fetch(url, {
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
      const res = await fetch(`${API_BASE}/auth/register`, {
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
      const res = await fetch(`${API_BASE}/auth/login`, {
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
      const res = await fetch(`${API_BASE}/auth/me`, {
        headers: this.getHeaders(),
      })
      if (!res.ok) throw new Error('Not authenticated')

      const userData = await res.json()

      try {
        localStorage.setItem('cached_user', JSON.stringify(userData))
      } catch (err) {
        logger.error('[API] Failed to cache user data:', err)
      }

      return userData
    })
  }

  async updateAudioQuality(audioQuality) {
    logger.info('[API] Updating audio quality to:', audioQuality)
    return this._routeRequest('updateAudioQuality', [audioQuality], async () => {
      const res = await fetch(`${API_BASE}/auth/audio-quality`, {
        method: 'PUT',
        headers: this.getHeaders(),
        body: JSON.stringify({ audio_quality: audioQuality }),
      })
      if (!res.ok) throw new Error('Failed to update audio quality')
      const data = await res.json()
      logger.info('[API] Audio quality update response:', data)
      return data
    })
  }

  async setPreference(type, id, preferenceType) {
    return this._routeRequest('setPreference', [type, id, preferenceType], async () => {
      const res = await fetch(`${API_BASE}/${type}s/${id}/preference`, {
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
      const res = await fetch(`${API_BASE}/${type}s/${id}/preference`, {
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
      const res = await fetch(`${API_BASE}${endpoint}`, {
        headers: this.getHeaders(),
      })
      if (!res.ok) throw new Error('Failed to get preferences')
      return res.json()
    })
  }

  async updateUserProfile(updates) {
    return this._routeRequest('updateUserProfile', [updates], async () => {
      const res = await fetch(`${API_BASE}/user/profile`, {
        method: 'PUT',
        headers: this.getHeaders(),
        body: JSON.stringify(updates),
      })
      if (!res.ok) throw new Error('Failed to update user profile')
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
      const res = await fetch(url, {
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
      const res = await fetch(url)
      return res.json()
    })
  }

  async getGenres() {
    return this._routeRequest('getGenres', [], async () => {
      const res = await fetch(`${API_BASE}/catalog/genres`)
      return res.json()
    })
  }

  async getTrack(trackId) {
    return this._routeRequest('getTrack', [trackId], async () => {
      const res = await fetch(`${API_BASE}/track/${trackId}`)
      if (!res.ok) {
        throw new Error(`Track not found: ${trackId}`)
      }
      return res.json()
    })
  }

  async play(trackId = null) {
    return this._routeRequest('play', [trackId], async () => {
      const res = await fetch(`${API_BASE}/playback/play`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify({ track_id: trackId }),
      })
      return res.json()
    })
  }

  async addToQueue(trackIds) {
    return this._routeRequest('addToQueue', [trackIds], async () => {
      const res = await fetch(`${API_BASE}/queue/add`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify({ track_ids: trackIds }),
      })
      return res.json()
    })
  }

  async removeFromQueue(trackId) {
    return this._routeRequest('removeFromQueue', [trackId], async () => {
      const res = await fetch(`${API_BASE}/queue/remove/${trackId}`, {
        method: 'DELETE',
        headers: this.getHeaders(),
      })
      return res.json()
    })
  }

  async seedRadio(category = 'all', trackId = null) {
    return this._routeRequest('seedRadio', [category, trackId], async () => {
      const res = await fetch(`${API_BASE}/queue/seed`, {
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
      const res = await fetch(`${API_BASE}/search/semantic`, {
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
      const res = await fetch(`${API_BASE}/user_content/shoutouts/${shoutoutId}`, {
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

  async searchShoutouts(query, nResults = 20, useAiAnalysis = false) {
    return this._routeRequest('searchShoutouts', [query, nResults], async () => {
      const mode = useAiAnalysis ? '🤖 AI-powered' : '⚡ Fast keyword'
      logger.info(`[API] 🔍 ${mode} shoutout search for "${query}"`)
      const res = await fetch(`${API_BASE}/user_content/shoutouts/search`, {
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
      const res = await fetch(`${API_BASE}/user_content/shoutouts/${shoutoutId}/replies?sort_by=${sortBy}`, {
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
      const res = await fetch(`${API_BASE}/user_content/shoutouts/${parentId}/reply/upload`, {
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

      const res = await fetch(`${API_BASE}/generate`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const errorData = await res.json().catch(() => ({ detail: 'Generation failed' }))
        throw new Error(errorData.detail || 'Generation failed')
      }
      return res.json()
    })
  }

  async cancelGenerationJob(jobId) {
    return this._routeRequest('cancelGenerationJob', [jobId], async () => {
      const res = await fetch(`${API_BASE}/generation-jobs/${jobId}`, {
        method: 'DELETE',
        headers: this.getHeaders(),
      })
      if (!res.ok) throw new Error('Failed to cancel generation job')
      return res.json()
    })
  }

  async getGenerationJobs() {
    return this._routeRequest('getGenerationJobs', [], async () => {
      const res = await fetch(`${API_BASE}/generation-jobs`, {
        headers: this.getHeaders(),
      })
      if (!res.ok) throw new Error('Failed to get generation jobs')
      return res.json()
    })
  }

  async getDevices() {
    return this._routeRequest('getDevices', [], async () => {
      const res = await fetch(`${API_BASE}/devices`, {
        headers: this.getHeaders(),
      })
      if (!res.ok) throw new Error('Failed to get devices')
      return res.json()
    })
  }

  async activateDevice(deviceId = null) {
    return this._routeRequest('activateDevice', [deviceId], async () => {
      const res = await fetch(`${API_BASE}/devices/activate`, {
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
      const res = await fetch(`${API_BASE}/devices/${deviceId}/name`, {
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
      const res = await fetch(`${API_BASE}/devices/${deviceId}`, {
        method: 'DELETE',
        headers: this.getHeaders(),
      })
      if (!res.ok) throw new Error('Failed to remove device')
      return res.json()
    })
  }

  getStreamUrl(trackId) {
    const baseUrl = `/api/stream/${trackId}/webm`
    const session = getSessionIds()
    const params = new URLSearchParams({
      guest_id: session.guestId,
      device_id: session.deviceId
    })

    if (this.token) {
      params.set('token', this.token)
    }

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
      const res = await fetch(`${API_BASE}/audio-features/${trackId}`, {
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
      const res = await fetch(`${API_BASE}/lyric-timestamps/${trackId}`, {
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
      const res = await fetch(`${API_BASE}/video-clips/${trackId}`, {
        headers: this.getHeaders(false),
      })
      if (!res.ok) {
        return { clips: [], reason: 'not_found' }
      }
      return res.json()
    })
  }

  async updateUsername(username) {
    return this._routeRequest('updateUsername', [username], async () => {
      const res = await fetch(`${API_BASE}/auth/update-username`, {
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
      const res = await fetch(`${API_BASE}/manage_user_data`, {
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
      const res = await fetch(`${API_BASE}/manage_user_data`, {
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
      const res = await fetch(`${API_BASE}/dj/talk`, {
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
      const res = await fetch(`${API_BASE}/conversation?limit=${limit}`, {
        method: 'GET',
        headers: this.getHeaders(true)
      })
      if (!res.ok) {
        throw new Error(`Failed to fetch conversation history: ${res.status}`)
      }
      return res.json()
    })
  }

  async validateCache(autoCleanup = false) {
    return offlineBackend.validateCache(autoCleanup)
  }

  async getUserUploads() {
    return this._routeRequest('getUserUploads', [], async () => {
      const res = await fetch(`${API_BASE}/user/music/tracks`, {
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
      const res = await fetch(url)
      return res.json()
    })
  }

  async trackShoutoutPlay(data) {
    return this._routeRequest('trackShoutoutPlay', [data], async () => {
      await fetch(`${API_BASE}/analytics/shoutout/play`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      })
      return { ok: true }
    })
  }

  async getTrackAnalytics(trackId) {
    return this._routeRequest('getTrackAnalytics', [trackId], async () => {
      const res = await fetch(`${API_BASE}/analytics/track/${trackId}`)
      if (!res.ok) return null
      return res.json()
    })
  }

  async getShoutoutAnalytics(shoutoutId) {
    return this._routeRequest('getShoutoutAnalytics', [shoutoutId], async () => {
      const res = await fetch(`${API_BASE}/analytics/shoutout/${shoutoutId}`)
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
      const res = await fetch(`${API_BASE}/transcribe`, {
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
      const res = await fetch(`${API_BASE}/user/music/tracks/${trackId}`, {
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
      const res = await fetch(`${API_BASE}/user/profile-picture`, {
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
      const res = await fetch(`${API_BASE}/user/profile-picture`, {
        method: 'DELETE',
        headers: this.getHeaders(),
        credentials: 'include'
      })
      return { ok: res.ok }
    })
  }

  async createStripeCheckout() {
    return this._routeRequest('createStripeCheckout', [], async () => {
      const res = await fetch(`${API_BASE}/stripe/create-checkout-session`, {
        method: 'POST',
        headers: this.getHeaders()
      })
      if (!res.ok) throw new Error('Failed to create session')
      return res.json()
    })
  }

  async getStripeKey() {
    return this._routeRequest('getStripeKey', [], async () => {
      const res = await fetch(`${API_BASE}/stripe/stripe_key`)
      return res.json()
    })
  }

  async uploadTrackArtwork(trackId, file) {
    return this._routeRequest('uploadTrackArtwork', [trackId, file], async () => {
      const formData = new FormData()
      formData.append('file', file)
      const headers = this.getHeaders()
      delete headers['Content-Type']
      const res = await fetch(`${API_BASE}/user/music/tracks/${trackId}/artwork`, {
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

  async uploadMusic(file) {
    return this._routeRequest('uploadMusic', [file], async () => {
      const formData = new FormData()
      formData.append('file', file)
      const headers = this.getHeaders()
      delete headers['Content-Type']
      const res = await fetch(`${API_BASE}/user/music/upload`, {
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
}

export const api = new API()