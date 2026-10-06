import { logger } from './logger'
import { getSessionIds } from './session'
import { offlineBackend } from './offlineAPI'
import { API_BASE, STREAM_BITRATES } from './api'

export const musicMethods = {
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
  },

  async getStats(genre = null) {
    return this._routeRequest('getStats', [genre], async () => {
      let url = `${API_BASE}/catalog/stats`
      if (genre) {
        url += `?genre=${encodeURIComponent(genre)}`
      }
      const res = await this._fetch(url)
      return res.json()
    })
  },

  async getGenres() {
    return this._routeRequest('getGenres', [], async () => {
      const res = await this._fetch(`${API_BASE}/catalog/genres`)
      return res.json()
    })
  },

  async getTrack(trackId) {
    return this._routeRequest('getTrack', [trackId], async () => {
      const res = await this._fetch(`${API_BASE}/track/${trackId}`)
      if (!res.ok) {
        throw new Error(`Track not found: ${trackId}`)
      }
      return res.json()
    })
  },

  async play(trackId = null) {
    return this._routeRequest('play', [trackId], async () => {
      const res = await this._fetch(`${API_BASE}/playback/play`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify({ track_id: trackId }),
      })
      return res.json()
    })
  },

  async addToQueue(trackIds) {
    return this._routeRequest('addToQueue', [trackIds], async () => {
      const res = await this._fetch(`${API_BASE}/queue/add`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify({ track_ids: trackIds }),
      })
      return res.json()
    })
  },

  async removeFromQueue(trackId) {
    return this._routeRequest('removeFromQueue', [trackId], async () => {
      const res = await this._fetch(`${API_BASE}/queue/remove/${trackId}`, {
        method: 'DELETE',
        headers: this.getHeaders(),
      })
      return res.json()
    })
  },

  async seedRadio(category = 'all', trackId = null) {
    return this._routeRequest('seedRadio', [category, trackId], async () => {
      const res = await this._fetch(`${API_BASE}/queue/seed`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify({ category, track_id: trackId })
      })
      return res.json()
    })
  },

  async searchSemantic(query, nResults = 50, useAiAnalysis = false, fillQueue = false) {
    return this._routeRequest('searchSemantic', [query, nResults], async () => {
      const mode = useAiAnalysis ? '🤖 AI-powered' : '⚡ Fast keyword'
      logger.info(`[API] 🌐 ONLINE MODE - ${mode} semantic search for "${query}"`)
      const res = await this._fetch(`${API_BASE}/search/semantic`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify({ query, n_results: nResults, use_ai_analysis: useAiAnalysis, queue: fillQueue }),
      })
      return res.json()
    })
  },

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
  },

  async cancelGenerationJob(jobId) {
    return this._routeRequest('cancelGenerationJob', [jobId], async () => {
      const res = await this._fetch(`${API_BASE}/generation-jobs/${jobId}`, {
        method: 'DELETE',
        headers: this.getHeaders(),
      })
      if (!res.ok) throw new Error('Failed to cancel generation job')
      return res.json()
    })
  },

  async getGenerationJobs() {
    return this._routeRequest('getGenerationJobs', [], async () => {
      const res = await this._fetch(`${API_BASE}/generation-jobs`, {
        headers: this.getHeaders(),
      })
      if (!res.ok) throw new Error('Failed to get generation jobs')
      return res.json()
    })
  },

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
  },

  getMp3StreamUrl(trackId, purpose = 'play') {
    const session = getSessionIds()
    const params = new URLSearchParams({
      guest_id: session.guestId,
      device_id: session.deviceId,
      purpose
    })
    return `/api/stream/${trackId}?${params.toString()}`
  },

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
  },

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
  },

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
  },

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
  },

  async validateCache(autoCleanup = false) {
    return offlineBackend.validateCache(autoCleanup)
  },

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
  },

  async getTrackAnalytics(trackId) {
    return this._routeRequest('getTrackAnalytics', [trackId], async () => {
      const res = await this._fetch(`${API_BASE}/analytics/track/${trackId}`)
      if (!res.ok) return null
      return res.json()
    })
  },

  async deleteUserTrack(trackId) {
    return this._routeRequest('deleteUserTrack', [trackId], async () => {
      const res = await this._fetch(`${API_BASE}/user/music/tracks/${trackId}`, {
        method: 'DELETE',
        headers: this.getHeaders()
      })
      return { ok: res.ok }
    })
  },

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
  },

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
  },

  async getUploadSetup() {
    return this._routeRequest('getUploadSetup', [], () =>
      this._jsonRequest('/user/music/setup', 'GET', undefined, 'Could not load your artists')
    )
  },

  async createArtist(data) {
    return this._routeRequest('createArtist', [data], () =>
      this._jsonRequest('/artists', 'POST', data, 'Could not add the artist')
    )
  },

  async updateArtist(artistId, data) {
    return this._routeRequest('updateArtist', [artistId, data], () =>
      this._jsonRequest(`/artists/${encodeURIComponent(artistId)}`, 'PUT', data, 'Could not save the artist')
    )
  },

  async deleteArtist(artistId) {
    return this._routeRequest('deleteArtist', [artistId], () =>
      this._jsonRequest(`/artists/${encodeURIComponent(artistId)}`, 'DELETE', undefined, 'Could not delete the artist')
    )
  },

  async getArtist(artistId) {
    return this._routeRequest('getArtist', [artistId], () =>
      this._jsonRequest(`/artists/${encodeURIComponent(artistId)}`, 'GET', undefined, 'Could not load the artist')
    )
  },

  async updateUserTrack(trackId, updates) {
    return this._routeRequest('updateUserTrack', [trackId, updates], () =>
      this._jsonRequest(`/user/music/tracks/${encodeURIComponent(trackId)}`, 'PUT', updates, 'Could not save the change')
    )
  },

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
  },

  async listUploadJobs() {
    return this._routeRequest('listUploadJobs', [], () =>
      this._jsonRequest('/user/music/uploads', 'GET', undefined, 'Could not load your uploads')
    )
  },

  async getUploadJob(uploadId) {
    return this._routeRequest('getUploadJob', [uploadId], () =>
      this._jsonRequest(`/user/music/uploads/${encodeURIComponent(uploadId)}`, 'GET', undefined, 'Could not check the upload')
    )
  },

  async cancelUploadJob(uploadId) {
    return this._routeRequest('cancelUploadJob', [uploadId], () =>
      this._jsonRequest(`/user/music/uploads/${encodeURIComponent(uploadId)}`, 'DELETE', undefined, 'Could not cancel the upload')
    )
  },
}
