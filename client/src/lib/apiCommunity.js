import { logger } from './logger'
import { API_BASE } from './api'

export const communityMethods = {
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
  },

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
  },

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
  },

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
  },

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
  },

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
  },

  async getMyCommunityPosts() {
    return this._routeRequest('getMyCommunityPosts', [], async () => {
      const res = await this._fetch(`${API_BASE}/user/community`, {
        method: 'GET',
        headers: this.getHeaders(),
      })
      if (!res.ok) throw new Error(`Failed to fetch your posts: ${res.statusText}`)
      return res.json()
    })
  },

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
  },

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
  },

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
  },

  async getShoutoutStats(category = null) {
    return this._routeRequest('getShoutoutStats', [category], async () => {
      let url = `${API_BASE}/user_content/stats`
      if (category) {
        url += `?category=${encodeURIComponent(category)}`
      }
      const res = await this._fetch(url)
      return res.json()
    })
  },

  async trackShoutoutPlay(data) {
    return this._routeRequest('trackShoutoutPlay', [data], async () => {
      await this._fetch(`${API_BASE}/analytics/shoutout/play`, {
        method: 'POST',
        headers: this.getHeaders(),
        body: JSON.stringify(data)
      })
      return { ok: true }
    })
  },

  async getShoutoutAnalytics(shoutoutId) {
    return this._routeRequest('getShoutoutAnalytics', [shoutoutId], async () => {
      const res = await this._fetch(`${API_BASE}/analytics/shoutout/${shoutoutId}`)
      if (!res.ok) return null
      return res.json()
    })
  },
}
