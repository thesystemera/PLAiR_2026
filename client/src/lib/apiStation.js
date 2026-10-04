import { API_BASE } from './api'

export const stationMethods = {
  async getDevices() {
    return this._routeRequest('getDevices', [], async () => {
      const res = await this._fetch(`${API_BASE}/devices`, {
        headers: this.getHeaders(),
      })
      if (!res.ok) throw new Error('Failed to get devices')
      return res.json()
    })
  },

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
  },

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
  },

  async removeDevice(deviceId) {
    return this._routeRequest('removeDevice', [deviceId], async () => {
      const res = await this._fetch(`${API_BASE}/devices/${deviceId}`, {
        method: 'DELETE',
        headers: this.getHeaders(),
      })
      if (!res.ok) throw new Error('Failed to remove device')
      return res.json()
    })
  },

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
  },

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
  },

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
  },

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
  },

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
  },

  async getTimelineEntry(entryId) {
    return this._routeRequest('getTimelineEntry', [entryId], async () => {
      const res = await this._fetch(`${API_BASE}/timeline/entry?id=${encodeURIComponent(entryId)}`, {
        method: 'GET',
        headers: this.getHeaders(true)
      })
      if (!res.ok) throw new Error(`Failed to fetch the timeline entry: ${res.status}`)
      return res.json()
    })
  },

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
  },

  async getRadioMode() {
    return this._routeRequest('getRadioMode', [], async () => {
      const res = await this._fetch(`${API_BASE}/radio-mode`, { headers: this.getHeaders() })
      if (!res.ok) throw new Error('Failed to load Radio Mode settings')
      return res.json()
    })
  },

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
  },

  async fetchMusicBed(url) {
    if (typeof url !== 'string' || !url.startsWith(`${API_BASE}/music-beds/`)) {
      throw new Error('Invalid music bed url')
    }
    return this._routeRequest('fetchMusicBed', [url], async () => {
      const res = await this._fetch(url)
      if (!res.ok) throw new Error(`Music bed request failed (${res.status})`)
      return res.arrayBuffer()
    })
  },
}
