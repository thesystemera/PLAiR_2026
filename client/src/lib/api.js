import { logger } from './logger'
import { safeStorage } from './safeStorage'
import { deviceKind, getSessionIds } from './session'
import { offlineBackend } from './offlineAPI'
import { uiState } from '../contexts/UIStateContext'
import { accountMethods } from './apiAccount'
import { musicMethods } from './apiMusic'
import { communityMethods } from './apiCommunity'
import { stationMethods } from './apiStation'

export const API_BASE = '/api'
export const STREAM_BITRATES = new Set(['128k', '192k', '256k'])

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
    const profileUpdates = entry.updates
    try {
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
    headers['X-Device-Kind'] = deviceKind()

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

  async _sendJson(method, path, body) {
    const res = await this._fetch(`${API_BASE}${path}`, {
      method,
      headers: this.getHeaders(),
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
    if (!res.ok) {
      const detail = await res.json().then(data => data?.detail).catch(() => null)
      const error = new Error(typeof detail === 'string' ? detail : 'Request failed')
      error.status = res.status
      throw error
    }
    return res.json()
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

}

Object.assign(API.prototype, accountMethods, musicMethods, communityMethods, stationMethods)

export const api = new API()
