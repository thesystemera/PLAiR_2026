import { createContext, useContext, useState, useEffect, useRef } from 'react'
import { api } from '../lib/api'
import { logger } from '../lib/logger'
import { useUIState } from './UIStateContext'

const AuthContext = createContext(null)

export const useAuth = () => {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return context
}

export const AuthProvider = ({ children }) => {
  const { publishAuthState } = useUIState()
  const [user, setUser] = useState(null)
  const [token, setToken] = useState(null)
  const [loading, setLoading] = useState(true)
  const isValidatingRef = useRef(false)

  useEffect(() => {
    const savedToken = localStorage.getItem('token')

    const timeout = setTimeout(() => {
      logger.warn('[Auth] Token check timed out, continuing with cached state')
      setLoading(false)
    }, 5000)

    if (savedToken) {
      queueMicrotask(() => setToken(savedToken))
      api.setToken(savedToken)

      const cachedUser = localStorage.getItem('cached_user')
      if (cachedUser) {
        try {
          queueMicrotask(() => setUser(JSON.parse(cachedUser)))
        } catch {
          logger.warn('[Auth] Failed to parse cached user')
        }
      }

      isValidatingRef.current = true
      api.getMe()
        .then(data => {
          clearTimeout(timeout)
          setUser(data)
          localStorage.setItem('cached_user', JSON.stringify(data))
        })
        .catch((err) => {
          clearTimeout(timeout)

          if (err.status === 401 || err.message?.includes('401')) {
            logger.info('[Auth] Token invalid (401), logging out')
            localStorage.removeItem('token')
            localStorage.removeItem('cached_user')
            setToken(null)
            setUser(null)
          } else {
            logger.warn('[Auth] Token validation failed due to network, keeping session:', err.message)
          }
        })
        .finally(() => {
          isValidatingRef.current = false
          setLoading(false)
        })
    } else {
      clearTimeout(timeout)
      queueMicrotask(() => setLoading(false))
    }

    return () => clearTimeout(timeout)
  }, [])

  useEffect(() => {
    publishAuthState({
      isAuthenticated: !!user,
      user
    })
  }, [user, publishAuthState])

  const login = async (username, password) => {
    const data = await api.login(username, password)
    setUser(data.user)
    setToken(data.token)
    localStorage.setItem('token', data.token)
    localStorage.setItem('cached_user', JSON.stringify(data.user))
    api.setToken(data.token)

    try {
      const completeUser = await api.getMe()
      setUser(completeUser)
      localStorage.setItem('cached_user', JSON.stringify(completeUser))
    } catch (err) {
      logger.error('[Auth] Failed to fetch complete user data after login:', err)
    }

    return data
  }

  const register = async (username, password) => {
    const data = await api.register(username, password)
    setUser(data.user)
    setToken(data.token)
    localStorage.setItem('token', data.token)
    localStorage.setItem('cached_user', JSON.stringify(data.user))
    api.setToken(data.token)

    try {
      const completeUser = await api.getMe()
      setUser(completeUser)
      localStorage.setItem('cached_user', JSON.stringify(completeUser))
    } catch (err) {
      logger.error('[Auth] Failed to fetch complete user data after registration:', err)
    }

    return data
  }

  const logout = () => {
    setUser(null)
    setToken(null)
    localStorage.removeItem('token')
    localStorage.removeItem('cached_user')
    api.setToken(null)
  }

  const refreshUser = async () => {
    if (token) {
      try {
        const data = await api.getMe()
        setUser(data)
        localStorage.setItem('cached_user', JSON.stringify(data))
      } catch (err) {
        if (err.status === 401) {
          logout()
        } else {
          logger.warn('[Auth] Failed to refresh user (network):', err.message)
        }
      }
    }
  }

  const value = {
    user,
    token,
    loading,
    isAuthenticated: !!user,
    login,
    register,
    logout,
    refreshUser
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}