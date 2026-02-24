import { useState, useEffect } from 'react'
import { useAuth } from '../contexts/AuthContext'

const STORAGE_KEY = 'gesture_guide_seen'

export function GestureGuide() {
  const { isAuthenticated, loading } = useAuth()
  const [shouldShow, setShouldShow] = useState(false)

  useEffect(() => {
    if (loading) return

    if (isAuthenticated) {
      queueMicrotask(() => setShouldShow(false))
      return
    }

    const hasSeenBefore = sessionStorage.getItem(STORAGE_KEY)

    if (!hasSeenBefore) {
      queueMicrotask(() => setShouldShow(true))
      sessionStorage.setItem(STORAGE_KEY, 'true')

      const hideTimer = setTimeout(() => {
        setShouldShow(false)
      }, 10000)

      return () => clearTimeout(hideTimer)
    }
  }, [isAuthenticated, loading])

  if (!shouldShow) return null

  const gestures = [
    { direction: 'up', text: 'Play', delay: 0 },
    { direction: 'down', text: 'Pause', delay: 2.5 },
    { direction: 'left', text: 'Previous', delay: 5 },
    { direction: 'right', text: 'Next', delay: 7.5 }
  ]

  return (
    <>
      <style>{`
        @keyframes swipeUp {
          0% { transform: translateY(150px); opacity: 0; }
          15% { transform: translateY(150px); opacity: 1; }
          85% { transform: translateY(-150px); opacity: 1; }
          100% { transform: translateY(-150px); opacity: 0; }
        }
        @keyframes swipeDown {
          0% { transform: translateY(-150px); opacity: 0; }
          15% { transform: translateY(-150px); opacity: 1; }
          85% { transform: translateY(150px); opacity: 1; }
          100% { transform: translateY(150px); opacity: 0; }
        }
        @keyframes swipeLeft {
          0% { transform: translateX(-150px); opacity: 0; }
          15% { transform: translateX(-150px); opacity: 1; }
          85% { transform: translateX(150px); opacity: 1; }
          100% { transform: translateX(150px); opacity: 0; }
        }
        @keyframes swipeRight {
          0% { transform: translateX(150px); opacity: 0; }
          15% { transform: translateX(150px); opacity: 1; }
          85% { transform: translateX(-150px); opacity: 1; }
          100% { transform: translateX(-150px); opacity: 0; }
        }
        @keyframes textSwipeUp {
          0% { transform: translateY(80px); opacity: 0; }
          15% { transform: translateY(80px); opacity: 1; }
          85% { transform: translateY(-80px); opacity: 1; }
          100% { transform: translateY(-80px); opacity: 0; }
        }
        @keyframes textSwipeDown {
          0% { transform: translateY(-80px); opacity: 0; }
          15% { transform: translateY(-80px); opacity: 1; }
          85% { transform: translateY(80px); opacity: 1; }
          100% { transform: translateY(80px); opacity: 0; }
        }
        @keyframes textSwipeLeft {
          0% { transform: translateX(-80px); opacity: 0; }
          15% { transform: translateX(-80px); opacity: 1; }
          85% { transform: translateX(80px); opacity: 1; }
          100% { transform: translateX(80px); opacity: 0; }
        }
        @keyframes textSwipeRight {
          0% { transform: translateX(80px); opacity: 0; }
          15% { transform: translateX(80px); opacity: 1; }
          85% { transform: translateX(-80px); opacity: 1; }
          100% { transform: translateX(-80px); opacity: 0; }
        }
      `}</style>

      <div style={{
        position: 'fixed',
        top: '50%',
        left: '50%',
        transform: 'translate(-50%, -50%)',
        width: '300px',
        height: '300px',
        zIndex: 10000,
        pointerEvents: 'none',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        willChange: 'auto'
      }}>
        {gestures.map((gesture) => (
          <div
            key={gesture.direction}
            style={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center'
            }}
          >
            <i
              className="fas fa-hand-pointer"
              style={{
                position: 'absolute',
                fontSize: '36px',
                color: '#ffffff',
                filter: 'drop-shadow(0 0 12px rgba(255,255,255,0.6))',
                animation: `swipe${gesture.direction.charAt(0).toUpperCase() + gesture.direction.slice(1)} 2s ease-in-out forwards`,
                animationDelay: `${gesture.delay}s`,
                willChange: 'transform, opacity'
              }}
            />
            <div
              style={{
                position: 'absolute',
                color: '#ffffff',
                fontSize: '20px',
                fontWeight: '600',
                textShadow: '0 2px 8px rgba(0,0,0,0.7)',
                letterSpacing: '0.5px',
                animation: `textSwipe${gesture.direction.charAt(0).toUpperCase() + gesture.direction.slice(1)} 2s ease-in-out forwards`,
                animationDelay: `${gesture.delay}s`,
                willChange: 'transform, opacity'
              }}
            >
              {gesture.text}
            </div>
          </div>
        ))}
      </div>
    </>
  )
}