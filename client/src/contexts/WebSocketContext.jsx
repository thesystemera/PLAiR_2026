import { logger } from '../lib/logger'
import { createContext, useContext, useEffect, useRef, useState, useCallback, useMemo } from 'react'
import { getSessionIds, deviceKind } from '../lib/session'
import { api } from '../lib/api'
import { reportClientEvent } from '../lib/errorReporter'
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
const WS_SUBPROTOCOL = 'plair.v1'
const WS_AUTH_PROTOCOL_PREFIX = 'auth.'
const HANDSHAKE_FAILURES_BEFORE_LEGACY = 2
const PING_INTERVAL_MS = 20000
const PONG_TIMEOUT_MS = 5000
const RECONNECT_BASE_MS = 1000
const RECONNECT_MAX_MS = 30000
const RECONNECT_MAX_ATTEMPTS = 10
const PING_MESSAGE = JSON.stringify({ type: 'ping', data: {} })
const OUTBOX_TTL_MS = {
  playback_command: 8 * 1000,
  track_transition: 30 * 60 * 1000,
  talk_break_ready: 60 * 1000,
  talk_break_failed: 60 * 1000,
  talk_break_start: 60 * 1000,
  talk_break_end: 5 * 60 * 1000,
  radio_mode_prefs: 5 * 60 * 1000,
}
const OFFLINE_DROPPED_TYPES = new Set([
  'playback_command',
  'track_transition',
  'talk_break_ready',
  'talk_break_failed',
  'talk_break_start',
  'talk_break_end',
])

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
  const probeRef = useRef(null)
  const subscribersRef = useRef({
    playback_state: [],
    preference_change: [],
    catalog_updated: [],
    playback_override: []
  })

  useEffect(() => {
    let isConnecting = false
    let isUnmounting = false
    let legacyAuth = false
    let handshakeFailures = 0
    let pongSupported = false
    let awaitingPongSince = 0
    let pongTimer = null

    const clearPongTimer = () => {
      if (pongTimer) {
        clearTimeout(pongTimer)
        pongTimer = null
      }
    }

    const dropZombie = (socket, reason) => {
      if (ws.current !== socket || socket.readyState !== WebSocket.OPEN) return
      logger.warn(`[WebSocket] No reply from server (${reason}) - reconnecting`)
      api.reportServerTrouble('ws_silent')
      reconnectAttemptRef.current = 0
      socket.close(4000, 'heartbeat timeout')
    }

    const ping = (reason) => {
      const socket = ws.current
      if (!socket || socket.readyState !== WebSocket.OPEN) return false
      try {
        socket.send(PING_MESSAGE)
      } catch {
        dropZombie(socket, reason)
        return false
      }
      if (!pongSupported) return true
      if (!awaitingPongSince) awaitingPongSince = Date.now()
      clearPongTimer()
      pongTimer = setTimeout(() => {
        pongTimer = null
        if (awaitingPongSince) dropZombie(socket, reason)
      }, PONG_TIMEOUT_MS)
      return true
    }
    probeRef.current = ping

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
          device_type: session.deviceType,
          device_kind: deviceKind()
        })

        const sentToken = getTokenRef.current?.() || null
        const useHandshakeAuth = !legacyAuth
        if (sentToken && !useHandshakeAuth) {
          params.set('token', sentToken)
        }

        const timezone = browserTimezone()
        if (timezone) {
          params.set('tz', timezone)
        }

        const wsUrl = `${wsProtocol}//${window.location.host}/ws/playback?${params.toString()}`
        const protocols = useHandshakeAuth
          ? (sentToken ? [WS_SUBPROTOCOL, `${WS_AUTH_PROTOCOL_PREFIX}${sentToken}`] : [WS_SUBPROTOCOL])
          : null
        const socket = protocols ? new WebSocket(wsUrl, protocols) : new WebSocket(wsUrl)
        ws.current = socket
        let opened = false

        socket.onopen = () => {
          if (ws.current !== socket) return
          opened = true
          handshakeFailures = 0
          awaitingPongSince = 0
          clearPongTimer()
          logger.info('[WebSocket] Connected')
          isConnecting = false
          reconnectAttemptRef.current = 0
          flushOutbox(socket)
          setConnected(true)
          ping('connect')
        }

        socket.onmessage = (event) => {
          if (ws.current !== socket) return
          awaitingPongSince = 0
          clearPongTimer()
          try {
            const message = JSON.parse(event.data)

            if (message.type === 'pong') {
              pongSupported = true
              return
            }

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
          awaitingPongSince = 0
          clearPongTimer()

          if (!opened && useHandshakeAuth && !isUnmounting) {
            fetch('/api/health', { cache: 'no-store' })
              .then(res => {
                if (!res.ok || legacyAuth) return
                handshakeFailures++
                if (handshakeFailures >= HANDSHAKE_FAILURES_BEFORE_LEGACY) {
                  legacyAuth = true
                  logger.warn('[WebSocket] Server did not accept the handshake protocol - using the legacy connection')
                }
              })
              .catch(() => {})
          }

          if (event.code === 4401 && sentToken) {
            onSessionInfoRef.current?.({ authenticated: false, token_rejected: true }, sentToken)
          }

          if (isUnmounting) {
            logger.info('[WebSocket] Close due to unmount, not reconnecting')
            return
          }

          if (event.code !== 1000 && event.code !== 4401) {
            api.reportServerTrouble('ws_closed')
            reportClientEvent('ws_close', `WebSocket closed with code ${event.code}${opened ? '' : ' before opening'}`)
          }

          if (!navigator.onLine) {
            logger.info('[WebSocket] Offline - waiting for network to reconnect')
            return
          }

          const ceiling = Math.min(RECONNECT_BASE_MS * Math.pow(2, reconnectAttemptRef.current), RECONNECT_MAX_MS)
          const delay = Math.round(ceiling * (0.5 + Math.random() * 0.5))
          reconnectAttemptRef.current = Math.min(reconnectAttemptRef.current + 1, RECONNECT_MAX_ATTEMPTS)

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

    const reviveOrCheck = (reason) => {
      const socket = ws.current
      if (!socket || socket.readyState === WebSocket.CLOSED || socket.readyState === WebSocket.CLOSING) {
        reconnectAttemptRef.current = 0
        connect()
      } else if (socket.readyState === WebSocket.OPEN) {
        ping(reason)
      }
    }

    const handleOnline = () => {
      logger.info('[WebSocket] Network online - attempting to connect')
      reviveOrCheck('online')
    }

    const handleVisible = () => {
      if (document.visibilityState === 'visible') reviveOrCheck('resume')
    }

    const handlePageShow = () => reviveOrCheck('pageshow')

    const pingInterval = setInterval(() => {
      if (document.visibilityState === 'visible') ping('interval')
    }, PING_INTERVAL_MS)

    const unsubscribeConnectivity = api.onConnectivity((event) => {
      if (event.type === 'lost') {
        const dropped = outboxRef.current.length
        outboxRef.current = outboxRef.current.filter(entry => !OFFLINE_DROPPED_TYPES.has(entry.data?.type))
        if (dropped !== outboxRef.current.length) logger.info('[WebSocket] Server unreachable - dropped queued playback commands')
      } else if (event.type === 'recovered') {
        const socket = ws.current
        if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return
        logger.info('[WebSocket] Server reachable again - reconnecting now')
        reconnectAttemptRef.current = 0
        connect()
      }
    })

    window.addEventListener('online', handleOnline)
    window.addEventListener('pageshow', handlePageShow)
    document.addEventListener('visibilitychange', handleVisible)

    return () => {
      isUnmounting = true
      connectRef.current = null
      probeRef.current = null
      clearInterval(pingInterval)
      clearPongTimer()
      unsubscribeConnectivity()
      window.removeEventListener('online', handleOnline)
      window.removeEventListener('pageshow', handlePageShow)
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

    if (uiState.audioState.offlineMode) return 'offline'

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
  }, [])

  const checkConnection = useCallback((reason = 'check') => {
    if (probeRef.current) return probeRef.current(reason)
    connectRef.current?.()
    return false
  }, [])

  const value = useMemo(() => ({
    connected,
    subscribe,
    emit,
    send,
    checkConnection
  }), [connected, subscribe, emit, send, checkConnection])

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
