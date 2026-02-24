import { logger } from '../lib/logger'
import { createContext, useContext, useEffect, useRef, useState, useCallback, useMemo } from 'react'
import { getSessionIds } from '../lib/session'
import { offlineBackend } from '../lib/offlineAPI'
import { uiState } from './UIStateContext'

export const WebSocketContext = createContext(null)

export function WebSocketProvider({ children, token }) {
  const ws = useRef(null)
  const [connected, setConnected] = useState(false)
  const reconnectTimeoutRef = useRef(null)
  const reconnectAttemptRef = useRef(0)
  const subscribersRef = useRef({
    playback_state: [],
    preference_change: [],
    catalog_updated: [],
    playback_override: []
  })

  useEffect(() => {
    let isConnecting = false
    let isUnmounting = false

    const connect = () => {
      if (!navigator.onLine) {
        logger.info('[WebSocket] Offline - skipping connection attempt')
        return
      }

      if (isConnecting || (ws.current && ws.current.readyState === WebSocket.OPEN)) {
        return
      }

      isConnecting = true

      try {
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
        const session = getSessionIds()
        const params = new URLSearchParams({
          guest_id: session.guestId,
          device_id: session.deviceId,
          device_name: session.deviceName,
          device_type: session.deviceType
        })

        if (token) {
          params.set('token', token)
        }

        const wsUrl = `${wsProtocol}//${window.location.host}/ws/playback?${params.toString()}`
        ws.current = new WebSocket(wsUrl)

        ws.current.onopen = () => {
          logger.info('[WebSocket] Connected')
          setConnected(true)
          isConnecting = false
          reconnectAttemptRef.current = 0
        }

        ws.current.onmessage = (event) => {
          try {
            const message = JSON.parse(event.data)

            const subscribers = subscribersRef.current[message.type] || []
            subscribers.forEach(callback => {
              try {
                callback(message.data)
              } catch (error) {
                logger.error('[WebSocket] Subscriber callback error:', error)
              }
            })
          } catch (error) {
            logger.error('[WebSocket] Failed to parse message:', error)
          }
        }

        ws.current.onerror = (error) => {
          logger.warn('[WebSocket] Error (will reconnect):', error)
          setConnected(false)
          isConnecting = false
        }

        ws.current.onclose = (event) => {
          logger.info(`[WebSocket] Disconnected (code: ${event.code}, reason: ${event.reason || 'none'})`)
          setConnected(false)
          isConnecting = false

          if (isUnmounting) {
            logger.info('[WebSocket] Close due to unmount, not reconnecting')
            return
          }

          if (!navigator.onLine) {
            logger.info('[WebSocket] Offline - waiting for network to reconnect')
            return
          }

          const baseDelay = 1000
          const maxDelay = 30000
          const maxAttempts = 10
          const delay = Math.min(baseDelay * Math.pow(2, reconnectAttemptRef.current), maxDelay)
          reconnectAttemptRef.current = Math.min(reconnectAttemptRef.current + 1, maxAttempts)

          logger.info(`[WebSocket] Reconnecting in ${delay}ms (attempt ${reconnectAttemptRef.current})`)

          if (reconnectTimeoutRef.current) {
            clearTimeout(reconnectTimeoutRef.current)
          }
          reconnectTimeoutRef.current = setTimeout(connect, delay)
        }
      } catch (error) {
        logger.error('[WebSocket] Failed to create connection:', error)
        isConnecting = false
      }
    }

    connect()

    const handleOnline = () => {
      logger.info('[WebSocket] Network online - attempting to connect')
      reconnectAttemptRef.current = 0
      connect()
    }

    window.addEventListener('online', handleOnline)

    return () => {
      isUnmounting = true
      window.removeEventListener('online', handleOnline)
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
      }
      if (ws.current) {
        if (ws.current.readyState === WebSocket.OPEN) {
          ws.current.close(1000, 'Component unmounting')
        } else if (ws.current.readyState === WebSocket.CONNECTING) {
          ws.current.onopen = null
          ws.current.onmessage = null
          ws.current.onerror = null
          ws.current.onclose = null
        }
      }
    }
  }, [token])

  const subscribe = useCallback((messageType, callback) => {
    if (!subscribersRef.current[messageType]) {
      subscribersRef.current[messageType] = []
    }
    subscribersRef.current[messageType].push(callback)

    return () => {
      subscribersRef.current[messageType] = subscribersRef.current[messageType].filter(
        cb => cb !== callback
      )
    }
  }, [])

  const emit = useCallback((messageType, data) => {
    const subscribers = subscribersRef.current[messageType] || []
    subscribers.forEach(callback => {
      try {
        callback(data)
      } catch (error) {
        logger.error('[WebSocket] Subscriber callback error:', error)
      }
    })
  }, [])

  const send = useCallback(async (data) => {
    if (ws.current && ws.current.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify(data))
      return
    }

    if (!uiState.audioState.isOnline) {
      logger.info('[WebSocket] Offline - simulating backend response for:', data.type)

      try {
        let response = null

        if (data.type === 'playback_command') {
          const cmd = data.data.command
          const trackId = data.data.track_id
          const positionMs = data.data.position_ms

          if (cmd === 'play' && trackId) {
            response = await offlineBackend['play'](trackId)
            if (response.state) {
              logger.info('[WebSocket] Emitting simulated playback_state:', response.state)
              emit('playback_state', response.state)
            }
          } else if (cmd === 'pause') {
            response = await offlineBackend['pause']()
          } else if (cmd === 'seek' && positionMs !== undefined) {
            response = await offlineBackend['seek'](positionMs)
          } else if (cmd === 'next') {
            response = await offlineBackend['next']()
            if (response.state) {
              logger.info('[WebSocket] Emitting simulated playback_state for next:', response.state)
              emit('playback_state', response.state)
            }
          } else if (cmd === 'previous') {
            response = await offlineBackend['previous']()
            if (response.state) {
              logger.info('[WebSocket] Emitting simulated playback_state for previous:', response.state)
              emit('playback_state', response.state)
            }
          }
        } else if (data.type === 'track_transition') {
          logger.info('[WebSocket] Track transition while offline - loading next cached track')
          response = await offlineBackend['play'](null)
          if (response.state) {
            logger.info('[WebSocket] Emitting simulated playback_state for next track:', response.state)
            emit('playback_state', response.state)
          }
        }

        if (response) {
          logger.info('[WebSocket] Offline simulation complete:', response)
        }
      } catch (err) {
        logger.error('[WebSocket] Offline simulation failed:', err)
      }
    } else {
      logger.warn('[WebSocket] Cannot send - socket not open and not offline')
    }
  }, [emit])

  const value = useMemo(() => ({
    connected,
    subscribe,
    emit,
    send
  }), [connected, subscribe, emit, send])

  return (
    <WebSocketContext.Provider value={value}>
      {children}
    </WebSocketContext.Provider>
  )
}

export function useWebSocketSubscribe(messageType, callback) {
  const context = useContext(WebSocketContext)
  if (!context) {
    throw new Error('useWebSocketSubscribe must be used within WebSocketProvider')
  }

  const callbackRef = useRef(callback)
  useEffect(() => {
    callbackRef.current = callback
  }, [callback])

  useEffect(() => {
    return context.subscribe(messageType, (...args) => callbackRef.current(...args))
  }, [messageType, context])

  return context.connected
}

export function useWebSocketEmit() {
  const context = useContext(WebSocketContext)
  if (!context) {
    throw new Error('useWebSocketEmit must be used within WebSocketProvider')
  }
  return context.emit
}