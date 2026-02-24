import React from 'react'
import ReactDOM from 'react-dom/client'
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
import { ViewportProvider } from './contexts/ViewportContext'
import { logger } from './lib/logger'
import './index.css'

const PRODUCTION_MODE = import.meta.env.PROD
if (PRODUCTION_MODE) {
  logger.info('[Main] Production mode')
}

window.addEventListener('unhandledrejection', (event) => {
  logger.error('[Global] Unhandled promise rejection:', event.reason)
  logger.error('[Global] Promise:', event.promise)

  event.preventDefault()
})

window.addEventListener('error', (event) => {
  logger.error('[Global] Uncaught error:', event.error || event.message)
  logger.error('[Global] Source:', event.filename, 'Line:', event.lineno, 'Column:', event.colno)

})

function WebSocketWrapper({ children }) {
  const { token } = useAuth()
  return <WebSocketProvider token={token}>{children}</WebSocketProvider>
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <UIStateProvider>
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
  </UIStateProvider>,
)

if ('serviceWorker' in navigator && PRODUCTION_MODE) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js')
      .then(registration => {
        logger.info('[ServiceWorker] Registered:', registration.scope)

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