import React, { useCallback } from 'react'
import ReactDOM from 'react-dom/client'
import { MotionConfig } from 'framer-motion'
import App from './App.jsx'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { PreferencesProvider } from './contexts/PreferencesContext'
import { WebSocketProvider } from './contexts/WebSocketContext'
import { DynamicThemeProvider } from './contexts/DynamicThemeContext'
import { GenerationQueueProvider } from './contexts/GenerationQueueContext'
import { NetworkProvider } from './contexts/NetworkContext'
import { StorageProvider } from './contexts/StorageContext'
import { PlaybackProvider } from './contexts/PlaybackContext'
import { PlaybackShoutoutProvider } from './contexts/PlaybackShoutoutContext'
import { UIStateProvider } from './contexts/UIStateContext'
import { QualityProvider } from './contexts/QualityContext'
import { ViewportProvider } from './contexts/ViewportContext'
import { logger } from './lib/logger'
import { installErrorReporter } from './lib/errorReporter'
import { AppErrorBoundary } from './components/AppErrorBoundary'
import { installPressFeedback } from './lib/microMotion'
import './index.css'

installErrorReporter()
installPressFeedback()

const PRODUCTION_MODE = import.meta.env.PROD
const CHUNK_RELOAD_KEY = 'plair_chunk_reload_at'
const CHUNK_RELOAD_GUARD_MS = 60000
if (PRODUCTION_MODE) {
  logger.info('[Main] Production mode')
}

window.addEventListener('unhandledrejection', (event) => {
  logger.error('[Global] Unhandled promise rejection:', event.reason)
  logger.error('[Global] Promise:', event.promise)
})

window.addEventListener('error', (event) => {
  logger.error('[Global] Uncaught error:', event.error || event.message)
  logger.error('[Global] Source:', event.filename, 'Line:', event.lineno, 'Column:', event.colno)

})

function readChunkReloadMark() {
  try {
    return Number(sessionStorage.getItem(CHUNK_RELOAD_KEY) || 0)
  } catch {
    return 0
  }
}

function writeChunkReloadMark() {
  try {
    sessionStorage.setItem(CHUNK_RELOAD_KEY, String(Date.now()))
    return true
  } catch {
    return false
  }
}

window.addEventListener('vite:preloadError', (event) => {
  if (Date.now() - readChunkReloadMark() < CHUNK_RELOAD_GUARD_MS || !writeChunkReloadMark()) return
  event.preventDefault()
  logger.warn('[Main] A newer version was deployed - reloading to pick it up')
  window.location.reload()
})

function WebSocketWrapper({ children }) {
  const { sessionKey, tokenRef, handleSessionInfo } = useAuth()
  const getToken = useCallback(() => tokenRef.current, [tokenRef])
  return (
    <WebSocketProvider sessionKey={sessionKey} getToken={getToken} onSessionInfo={handleSessionInfo}>
      {children}
    </WebSocketProvider>
  )
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <AppErrorBoundary>
  <MotionConfig reducedMotion="user">
  <UIStateProvider>
  <QualityProvider>
    <AuthProvider>
      <WebSocketWrapper>
        <ViewportProvider>
          <NetworkProvider>
            <StorageProvider>
              <PreferencesProvider>
                <DynamicThemeProvider>
                  <PlaybackProvider>
                    <PlaybackShoutoutProvider>
                      <GenerationQueueProvider>
                        <App />
                      </GenerationQueueProvider>
                    </PlaybackShoutoutProvider>
                  </PlaybackProvider>
                </DynamicThemeProvider>
              </PreferencesProvider>
            </StorageProvider>
          </NetworkProvider>
        </ViewportProvider>
      </WebSocketWrapper>
    </AuthProvider>
  </QualityProvider>
  </UIStateProvider>
  </MotionConfig>
  </AppErrorBoundary>,
)

function scheduleIdleWork(callback, delay) {
  if ('requestIdleCallback' in window) {
    window.requestIdleCallback(callback, { timeout: 4000 })
  } else {
    setTimeout(() => callback(null), delay)
  }
}

function warmInterfaceSounds() {
  const pending = Array.from(document.querySelectorAll('audio[data-deferred-preload]'))
  const warmNext = (deadline) => {
    do {
      const element = pending.shift()
      if (element && element.paused && element.readyState === 0) {
        element.preload = 'auto'
        element.load()
      }
    } while (pending.length > 0 && deadline && !deadline.didTimeout && deadline.timeRemaining() > 8)
    if (pending.length > 0) scheduleIdleWork(warmNext, 50)
  }
  scheduleIdleWork(warmNext, 2000)
}

window.addEventListener('load', warmInterfaceSounds, { once: true })

if ('serviceWorker' in navigator && PRODUCTION_MODE) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js')
      .then(registration => {
        logger.info('[ServiceWorker] Registered:', registration.scope)
        navigator.serviceWorker.ready
          .then(ready => ready.active?.postMessage({ type: 'PRECACHE' }))
          .catch(() => {})

        registration.addEventListener('updatefound', () => {
          const newWorker = registration.installing
          logger.info('[ServiceWorker] Update found')

          newWorker.addEventListener('statechange', () => {
            if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
              logger.info('[ServiceWorker] New version available')
            }
          })
        })
      })
      .catch(err => {
        logger.warn('[ServiceWorker] Registration failed:', err)
      })
  })
} else if ('serviceWorker' in navigator && !PRODUCTION_MODE) {
  navigator.serviceWorker.getRegistrations().then(registrations => {
    registrations.forEach(registration => {
      registration.unregister().catch(err => {
        logger.warn('[ServiceWorker] Unregister failed:', err)
      })
    })
  })
}