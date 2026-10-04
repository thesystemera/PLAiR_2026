import { logger } from './logger'
import { offlineBackend } from './offlineAPI'
import { API_BASE } from './api'

export const accountMethods = {
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
  },

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
  },

  async passkeyLoginOptions() {
    return this._routeRequest('passkeyLoginOptions', [], () => this._sendJson('POST', '/auth/passkey/login/options', {}))
  },

  async passkeyLogin(requestId, credential) {
    return this._routeRequest('passkeyLogin', [requestId, credential], () => this._sendJson('POST', '/auth/passkey/login', { request_id: requestId, credential }))
  },

  async passkeySignupOptions(username) {
    return this._routeRequest('passkeySignupOptions', [username], () => this._sendJson('POST', '/auth/passkey/signup/options', { username }))
  },

  async passkeySignup(requestId, credential) {
    return this._routeRequest('passkeySignup', [requestId, credential], () => this._sendJson('POST', '/auth/passkey/signup', { request_id: requestId, credential }))
  },

  async getPasskeys() {
    return this._routeRequest('getPasskeys', [], () => this._sendJson('GET', '/auth/passkeys'))
  },

  async addPasskeyOptions(auto = false) {
    return this._routeRequest('addPasskeyOptions', [auto], () => this._sendJson('POST', `/auth/passkeys/options${auto ? '?auto=true' : ''}`, {}))
  },

  async addPasskey(requestId, credential) {
    return this._routeRequest('addPasskey', [requestId, credential], () => this._sendJson('POST', '/auth/passkeys', { request_id: requestId, credential }))
  },

  async deletePasskey(passkeyId) {
    return this._routeRequest('deletePasskey', [passkeyId], () => this._sendJson('DELETE', `/auth/passkeys/${encodeURIComponent(passkeyId)}`))
  },

  async setPassword(password) {
    return this._routeRequest('setPassword', [password], () => this._sendJson('PUT', '/auth/password', { password }))
  },

  async startDeviceLink() {
    return this._routeRequest('startDeviceLink', [], () => this._sendJson('POST', '/auth/link/start', {}))
  },

  async pollDeviceLink(code, pollKey) {
    return this._routeRequest('pollDeviceLink', [code, pollKey], () => this._sendJson('POST', '/auth/link/poll', { code, poll_key: pollKey }))
  },

  async describeDeviceLink(code) {
    return this._routeRequest('describeDeviceLink', [code], () => this._sendJson('GET', `/auth/link/${encodeURIComponent(code)}`))
  },

  async approveDeviceLink(code) {
    return this._routeRequest('approveDeviceLink', [code], () => this._sendJson('POST', `/auth/link/${encodeURIComponent(code)}/approve`, {}))
  },

  async deleteAccount() {
    return this._routeRequest('deleteAccount', [], () => this._sendJson('DELETE', '/auth/account'))
  },

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
  },

  async getDeviceSettings() {
    return this._routeRequest('getDeviceSettings', [], async () => {
      const res = await this._fetch(`${API_BASE}/settings`, { headers: this.getHeaders() })
      if (!res.ok) throw new Error(`Failed to load settings (${res.status})`)
      return res.json()
    })
  },

  async saveDeviceSettings(changes) {
    return this._routeRequest('saveDeviceSettings', [changes], async () => {
      const res = await this._fetch(`${API_BASE}/settings`, {
        method: 'PUT',
        headers: this.getHeaders(),
        body: JSON.stringify({ settings: changes }),
      })
      if (!res.ok) throw new Error(`Failed to save settings (${res.status})`)
      return res.json()
    })
  },

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
  },

  async removePreference(type, id) {
    return this._routeRequest('removePreference', [type, id], async () => {
      const res = await this._fetch(`${API_BASE}/${type}s/${id}/preference`, {
        method: 'DELETE',
        headers: this.getHeaders(),
      })
      if (!res.ok) throw new Error('Failed to remove preference')
      return res.json()
    })
  },

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
  },

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
  },

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
  },

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
  },

  async deleteProfilePicture() {
    return this._routeRequest('deleteProfilePicture', [], async () => {
      const res = await this._fetch(`${API_BASE}/user/profile-picture`, {
        method: 'DELETE',
        headers: this.getHeaders(),
        credentials: 'include'
      })
      return { ok: res.ok }
    })
  },

  async _billingRequest(path, options, fallbackMessage) {
    const res = await this._fetch(`${API_BASE}/stripe/${path}`, { headers: this.getHeaders(), ...options })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      const err = new Error(data.detail || fallbackMessage)
      err.status = res.status
      throw err
    }
    return res.json()
  },

  async createStripeCheckout() {
    return this._routeRequest('createStripeCheckout', [], () =>
      this._billingRequest('create-checkout-session', { method: 'POST' }, 'Could not start checkout. Please try again.')
    )
  },

  async createBillingPortal() {
    return this._routeRequest('createBillingPortal', [], () =>
      this._billingRequest('portal', { method: 'POST' }, 'Could not open subscription management. Please try again.')
    )
  },

  async getBillingStatus(checkoutSessionId = null) {
    return this._routeRequest('getBillingStatus', [checkoutSessionId], () => {
      const query = checkoutSessionId ? `?${new URLSearchParams({ session_id: checkoutSessionId })}` : ''
      return this._billingRequest(`status${query}`, { method: 'GET' }, 'Could not load subscription status.')
    })
  },

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
  },

  async getUsageSummary(period = 'month', start = null, end = null) {
    return this._getUsage('getUsageSummary', '/admin/usage/summary', { period, start, end })
  },

  async getUsageUsers(period = 'month', start = null, end = null) {
    return this._getUsage('getUsageUsers', '/admin/usage/users', { period, start, end })
  },

  async getUsageSubject(subject, period = 'month') {
    return this._getUsage('getUsageSubject', `/admin/usage/user/${encodeURIComponent(subject)}`, { period })
  },

  async getMyUsage(period = 'month') {
    return this._getUsage('getMyUsage', '/usage/me', { period })
  },
}
