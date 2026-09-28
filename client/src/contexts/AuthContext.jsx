import { createContext, useContext, useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { api } from '../lib/api'
import { logger } from '../lib/logger'
import { safeStorage } from '../lib/safeStorage'
import { useUIState } from './UIStateContext'

const AuthContext = createContext(null)

const INITIAL_CHECK_TIMEOUT_MS = 5000
const REVALIDATE_TIMEOUT_MS = 8000
const RESUME_REVALIDATE_MS = 5 * 60 * 1000
const HTTP_401_REVALIDATE_MS = 30 * 1000
const REFRESH_BEFORE_EXPIRY_S = 4 * 24 * 60 * 60
const EXPIRED_MESSAGE = 'Your sign-in has expired. Please log in again.'

export const useAuth = () => {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return context
}

function decodeTokenClaims(token) {
  if (!token || typeof token !== 'string') return null
  const part = token.split('.')[1]
  if (!part) return null
  try {
    const normalized = part.replace(/-/g, '+').replace(/_/g, '/')
    const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4)
    const claims = JSON.parse(atob(padded))
    return claims && typeof claims === 'object' ? claims : null
  } catch {
    return null
  }
}

function sessionKeyFor(token) {
  if (!token) return 'guest'
  const sub = decodeTokenClaims(token)?.sub
  return `user:${sub ?? token.slice(-16)}`
}

function readCachedUser() {
  const cached = safeStorage.get('cached_user')
  if (!cached) return null
  try {
    return JSON.parse(cached)
  } catch {
    logger.warn('[Auth] Failed to parse cached user')
    return null
  }
}

function readInitialAuth() {
  const token = safeStorage.get('token') || null
  api.setToken(token)
  return { token, user: token ? readCachedUser() : null }
}

function withTimeout(promise, ms) {
  let timer = null
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      const error = new Error('Session check timed out')
      error.timeout = true
      reject(error)
    }, ms)
  })
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer))
}

const isAuthFailure = (err) => err?.status === 401 || err?.status === 403

export const AuthProvider = ({ children }) => {
  const { publishAuthState, toastWarning } = useUIState()
  const [initial] = useState(readInitialAuth)
  const [user, setUser] = useState(initial.user)
  const [token, setToken] = useState(initial.token)
  const [loading, setLoading] = useState(!!initial.token)
  const [sessionExpiredCount, setSessionExpiredCount] = useState(0)
  const tokenRef = useRef(initial.token)
  const validationRef = useRef(null)
  const lastValidatedRef = useRef(0)
  const refreshingRef = useRef(false)
  const toastWarningRef = useRef(toastWarning)

  useEffect(() => { toastWarningRef.current = toastWarning }, [toastWarning])

  const applyToken = useCallback((nextToken) => {
    tokenRef.current = nextToken
    api.setToken(nextToken)
    setToken(nextToken)
    if (nextToken) safeStorage.set('token', nextToken)
    else safeStorage.remove('token')
  }, [])

  const acceptUser = useCallback((data) => {
    setUser(data)
    safeStorage.set('cached_user', JSON.stringify(data))
  }, [])

  const clearSession = useCallback(() => {
    validationRef.current = null
    applyToken(null)
    safeStorage.remove('cached_user')
    setUser(null)
  }, [applyToken])

  const expireSession = useCallback((reason) => {
    if (!tokenRef.current) return
    logger.info(`[Auth] Session no longer valid (${reason}), signing out`)
    clearSession()
    setSessionExpiredCount(count => count + 1)
    toastWarningRef.current?.(EXPIRED_MESSAGE, 6000, 'top', 'auth-expired')
  }, [clearSession])

  const maybeRefreshToken = useCallback(async () => {
    const current = tokenRef.current
    const exp = decodeTokenClaims(current)?.exp
    if (!current || !exp || refreshingRef.current) return
    if (exp - Date.now() / 1000 > REFRESH_BEFORE_EXPIRY_S) return
    refreshingRef.current = true
    try {
      const data = await api.refreshToken()
      if (data?.token && tokenRef.current === current && sessionKeyFor(data.token) === sessionKeyFor(current)) {
        applyToken(data.token)
        logger.info('[Auth] Session token refreshed')
      }
    } catch (err) {
      logger.warn('[Auth] Token refresh skipped:', err.message)
    } finally {
      refreshingRef.current = false
    }
  }, [applyToken])

  const revalidate = useCallback((reason) => {
    const current = tokenRef.current
    if (!current) return Promise.resolve(false)
    if (reason !== 'refresh' && validationRef.current?.token === current) {
      if (reason === 'server_rejected') validationRef.current.serverRejected = true
      return validationRef.current.promise
    }

    const entry = { token: current, promise: null, serverRejected: reason === 'server_rejected' }
    const promise = withTimeout(api.checkSession(current), REVALIDATE_TIMEOUT_MS)
      .then(data => {
        if (tokenRef.current !== current) return false
        lastValidatedRef.current = Date.now()
        acceptUser(data)
        void maybeRefreshToken()
        return true
      })
      .catch(err => {
        if (tokenRef.current !== current) return false
        if (isAuthFailure(err) || entry.serverRejected) {
          expireSession(isAuthFailure(err) ? `${reason}, HTTP ${err.status}` : `server rejected, ${err.message}`)
          return false
        }
        logger.warn(`[Auth] Session check (${reason}) failed, keeping session:`, err.message)
        return true
      })
      .finally(() => {
        if (validationRef.current?.promise === promise) validationRef.current = null
      })
    entry.promise = promise
    validationRef.current = entry
    return promise
  }, [acceptUser, expireSession, maybeRefreshToken])

  useEffect(() => {
    const savedToken = tokenRef.current
    if (!savedToken) return undefined

    let settled = false
    const timeout = setTimeout(() => {
      if (settled) return
      logger.warn('[Auth] Token check timed out, continuing with cached state')
      setLoading(false)
    }, INITIAL_CHECK_TIMEOUT_MS)

    api.getMe()
      .then(data => {
        if (tokenRef.current !== savedToken) return
        if (data && data.offline) return
        if (data && data.id !== undefined) {
          lastValidatedRef.current = Date.now()
          acceptUser(data)
          void maybeRefreshToken()
        }
      })
      .catch(err => {
        if (tokenRef.current !== savedToken) return
        if (isAuthFailure(err) || err.message?.includes('401')) {
          expireSession(`startup, HTTP ${err.status || 401}`)
        } else {
          logger.warn('[Auth] Token validation failed due to network, keeping session:', err.message)
        }
      })
      .finally(() => {
        settled = true
        clearTimeout(timeout)
        setLoading(false)
      })

    return () => clearTimeout(timeout)
  }, [acceptUser, expireSession, maybeRefreshToken])

  useEffect(() => {
    return api.onAuthRejected(({ token: rejected }) => {
      if (!rejected || rejected !== tokenRef.current) return
      if (Date.now() - lastValidatedRef.current < HTTP_401_REVALIDATE_MS) return
      void revalidate('http_401')
    })
  }, [revalidate])

  useEffect(() => {
    const handleVisible = () => {
      if (document.visibilityState !== 'visible' || !tokenRef.current) return
      if (Date.now() - lastValidatedRef.current < RESUME_REVALIDATE_MS) return
      void revalidate('resume')
    }
    document.addEventListener('visibilitychange', handleVisible)
    return () => document.removeEventListener('visibilitychange', handleVisible)
  }, [revalidate])

  useEffect(() => {
    publishAuthState({
      isAuthenticated: !!user,
      user
    })
  }, [user, publishAuthState])

  const handleSessionInfo = useCallback((info, sentToken) => {
    if (!info || (sentToken || null) !== (tokenRef.current || null)) return
    if (info.token_rejected && tokenRef.current) {
      logger.warn('[Auth] Server rejected this session token on the live connection')
      void revalidate('server_rejected')
    } else if (info.authenticated && user && info.user_id != null && String(info.user_id) !== String(user.id)) {
      void revalidate('identity_mismatch')
    }
  }, [revalidate, user])

  const startSession = useCallback(async (data) => {
    validationRef.current = null
    setUser(data.user)
    safeStorage.set('cached_user', JSON.stringify(data.user))
    applyToken(data.token)
    lastValidatedRef.current = Date.now()

    try {
      const completeUser = await api.getMe()
      if (completeUser && completeUser.id !== undefined && tokenRef.current === data.token) acceptUser(completeUser)
    } catch (err) {
      logger.error('[Auth] Failed to fetch complete user data after sign-in:', err)
    }
    return data
  }, [acceptUser, applyToken])

  const login = useCallback(async (username, password) => {
    const data = await api.login(username, password)
    return startSession(data)
  }, [startSession])

  const register = useCallback(async (username, password) => {
    const data = await api.register(username, password)
    return startSession(data)
  }, [startSession])

  const logout = useCallback(() => {
    clearSession()
  }, [clearSession])

  const refreshUser = useCallback(async () => {
    if (tokenRef.current) await revalidate('refresh')
  }, [revalidate])

  const sessionKey = useMemo(() => sessionKeyFor(token), [token])

  const value = useMemo(() => ({
    user,
    token,
    tokenRef,
    sessionKey,
    loading,
    isAuthenticated: !!user,
    sessionExpiredCount,
    login,
    register,
    logout,
    refreshUser,
    handleSessionInfo
  }), [user, token, sessionKey, loading, sessionExpiredCount, login, register, logout, refreshUser, handleSessionInfo])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
