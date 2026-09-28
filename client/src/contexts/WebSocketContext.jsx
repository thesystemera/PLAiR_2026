import { logger } from '../lib/logger'
import { createContext, useContext, useEffect, useRef, useState, useCallback, useMemo } from 'react'
import { getSessionIds } from '../lib/session'
import { offlineBackend } from '../lib/offlineAPI'
import { uiState } from './UIStateContext'

export const WebSocketContext = createContext(null)

function browserTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || ''
  } catch {
    return ''
  }
}

const OUTBOX_LIMIT = 32
const OUTBOX_TTL_MS = {
  playback_command: 60 * 1000,
  track_transition: 30 * 60 * 1000,
  talk_break_ready: 60 * 1000,
  talk_break_failed: 60 * 1000,
  talk_break_start: 60 * 1000,
  talk_break_end: 5 * 60 * 1000,
  radio_mode_prefs: 5 * 60 * 1000,
}

export function WebSocketProvider({ children, sessionKey = 'guest', getToken, onSessionInfo }) {
  const ws = useRef(null)
  const getTokenRef = useRef(getToken)
  const onSessionInfoRef = useRef(onSessionInfo)
  useEffect(() => { getTokenRef.current = getToken }, [getToken])
  useEffect(() => { onSessionInfoRef.current = onSessionInfo }, [onSessionInfo])
  const [connected, setConnected] = useState(false)
  const reconnectTimeoutRef = useRef(null)
  const reconnectAttemptRef = useRef(0)
  const outboxRef = useRef([])
  const connectRef = useRef(null)
  const subscribersRef = useRef({
    playback_state: [],
    preference_change: [],
    catalog_updated: [],
    playback_override: []
  })

  useEffect(() => {
    let isConnecting = false
    let isUnmounting = false

    const flushOutbox = (socket) => {
      const now = Date.now()
      const pending = outboxRef.current
      outboxRef.current = []
      let flushed = 0
      for (const entry of pending) {
        if (now - entry.queuedAt > (OUTBOX_TTL_MS[entry.data.type] || 0)) continue
        try {
          socket.send(JSON.stringify(entry.data))
          flushed++
        } catch (error) {
          logger.warn('[WebSocket] Failed to flush queued message:', error)
        }
      }
      if (flushed) logger.info(`[WebSocket] Flushed ${flushed} queued message(s)`)
    }

    const connect = () => {
      if (isUnmounting) return

      if (!navigator.onLine) {
        logger.info('[WebSocket] Offline - skipping connection attempt')
        return
      }

      if (isConnecting || (ws.current && (ws.current.readyState === WebSocket.OPEN || ws.current.readyState === WebSocket.CONNECTING))) {
        return
      }

      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
        reconnectTimeoutRef.current = null
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

        const sentToken = getTokenRef.current?.() || null
        if (sentToken) {
          params.set('token', sentToken)
        }

        const timezone = browserTimezone()
        if (timezone) {
          params.set('tz', timezone)
        }

        const wsUrl = `${wsProtocol}//${window.location.host}/ws/playback?${params.toString()}`
        const socket = new WebSocket(wsUrl)
        ws.current = socket

        socket.onopen = () => {
          if (ws.current !== socket) return
          logger.info('[WebSocket] Connected')
          isConnecting = false
          reconnectAttemptRef.current = 0
          flushOutbox(socket)
          setConnected(true)
        }

        socket.onmessage = (event) => {
          if (ws.current !== socket) return
          try {
            const message = JSON.parse(event.data)

            if (message.type === 'session_info') {
              try {
                onSessionInfoRef.current?.(message.data, sentToken)
              } catch (error) {
                logger.error('[WebSocket] Session info handler error:', error)
              }
            }

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

        socket.onerror = (error) => {
          if (ws.current !== socket) return
          logger.warn('[WebSocket] Error (will reconnect):', error)
          isConnecting = false
        }

        socket.onclose = (event) => {
          if (ws.current !== socket) return
          logger.info(`[WebSocket] Disconnected (code: ${event.code}, reason: ${event.reason || 'none'})`)
          setConnected(false)
          isConnecting = false

          if (event.code === 4401 && sentToken) {
            onSessionInfoRef.current?.({ authenticated: false, token_rejected: true }, sentToken)
          }

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

    connectRef.current = connect
    connect()

    const handleOnline = () => {
      logger.info('[WebSocket] Network online - attempting to connect')
      reconnectAttemptRef.current = 0
      connect()
    }

    const handleVisible = () => {
      if (document.visibilityState !== 'visible') return
      const socket = ws.current
      if (!socket || socket.readyState === WebSocket.CLOSED || socket.readyState === WebSocket.CLOSING) {
        reconnectAttemptRef.current = 0
        connect()
      }
    }

    window.addEventListener('online', handleOnline)
    document.addEventListener('visibilitychange', handleVisible)

    return () => {
      isUnmounting = true
      connectRef.current = null
      window.removeEventListener('online', handleOnline)
      document.removeEventListener('visibilitychange', handleVisible)
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
        reconnectTimeoutRef.current = null
      }
      const socket = ws.current
      ws.current = null
      if (socket) {
        socket.onopen = null
        socket.onmessage = null
        socket.onerror = null
        socket.onclose = null
        if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
          socket.close(1000, 'Component unmounting')
        }
      }
      setConnected(false)
    }
  }, [sessionKey])

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
      return 'sent'
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
      return 'offline'
    }

    if (OUTBOX_TTL_MS[data?.type]) {
      const outbox = outboxRef.current
      outbox.push({ data, queuedAt: Date.now() })
      if (outbox.length > OUTBOX_LIMIT) outbox.splice(0, outbox.length - OUTBOX_LIMIT)
      logger.info(`[WebSocket] Socket not open - queued ${data.type} until reconnect`)
      if (connectRef.current) connectRef.current()
      return 'queued'
    }

    logger.warn('[WebSocket] Cannot send - socket not open and not offline')
    return 'dropped'
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

  const { subscribe } = context
  useEffect(() => {
    return subscribe(messageType, (...args) => callbackRef.current(...args))
  }, [messageType, subscribe])

  return context.connected
}

export function useWebSocketEmit() {
  const context = useContext(WebSocketContext)
  if (!context) {
    throw new Error('useWebSocketEmit must be used within WebSocketProvider')
  }
  return context.emit
}
