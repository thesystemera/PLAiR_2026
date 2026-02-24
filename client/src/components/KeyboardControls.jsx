import { useEffect, useRef } from 'react'
import { usePlayback } from '../contexts/PlaybackContext'
import { useVoiceRecording } from '../contexts/VoiceRecordingContext'
import { useUIState } from '../contexts/UIStateContext'
import { useUISound } from '../hooks/useUISound'

let _keyboardRecordingCallback = null

export function registerKeyboardRecordingCallback(cb) {
  _keyboardRecordingCallback = cb
}

export function KeyboardControls({
  showLogin = false,
  showRegister = false,
  onCloseLogin = null,
  onCloseRegister = null
}) {
  const playback = usePlayback()
  const { isRecording, startRecording, stopRecording } = useVoiceRecording()
  const { interfaceState, reportInterfaceState } = useUIState()
  const uiSound = useUISound()

  const stateRef = useRef({})
  stateRef.current = {
    playback,
    isRecording,
    startRecording,
    stopRecording,
    uiSound,
    showLogin,
    showRegister,
    onCloseLogin,
    onCloseRegister,
    isFullscreenVisuals: interfaceState.isFullscreenVisuals,
    reportInterfaceState,
  }

  const mouseRef = useRef({ x: 0, y: 0 })

  useEffect(() => {
    const trackMouse = (e) => {
      mouseRef.current.x = e.clientX
      mouseRef.current.y = e.clientY
    }

    const findScrollerAtCursor = () => {
      const el = document.elementFromPoint(mouseRef.current.x, mouseRef.current.y)
      return el?.closest('[data-scroller]') || null
    }

    const isInputFocused = (e) => {
      const tag = e.target.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
      if (e.target.closest('[contenteditable="true"]')) return true
      return false
    }

    const handleKeyEvent = async (e, isKeyDown) => {
      if (isInputFocused(e)) return

      const s = stateRef.current

      if (e.code === 'Space' || e.code === 'KeyP') {
        e.preventDefault()
        if (isKeyDown && !s.isRecording) {
          s.uiSound?.playRecordPress()
          await s.startRecording('radio')
        } else if (!isKeyDown && s.isRecording) {
          s.uiSound?.playRecordRelease()
          const blob = await s.stopRecording()
          if (blob?.size > 0) _keyboardRecordingCallback?.(blob)
        }
        return
      }

      if (!isKeyDown) return

      switch (e.key) {
        case 'k':
        case 'K':
          e.preventDefault()
          s.playback?.togglePlay()
          break

        case 'j':
        case 'J':
          e.preventDefault()
          s.playback?.previous()
          break

        case 'l':
        case 'L':
          e.preventDefault()
          s.playback?.next()
          break

        case 'm':
        case 'M':
          e.preventDefault()
          if (s.playback?.audio?.setMuted) {
            const el = s.playback.audio.getCurrentElement()
            if (el) s.playback.audio.setMuted(!el.muted)
          }
          break

        case '=':
        case '+':
          e.preventDefault()
          if (s.playback?.audio?.getVolume && s.playback?.audio?.setVolume) {
            s.playback.audio.setVolume(Math.min(1, s.playback.audio.getVolume() + 0.1))
          }
          break

        case '-':
        case '_':
          e.preventDefault()
          if (s.playback?.audio?.getVolume && s.playback?.audio?.setVolume) {
            s.playback.audio.setVolume(Math.max(0, s.playback.audio.getVolume() - 0.1))
          }
          break

        case 'ArrowLeft': {
          e.preventDefault()
          const el = s.playback?.audio?.getCurrentElement()
          if (el && s.playback?.seek) {
            s.playback.seek(Math.max(0, el.currentTime * 1000 - 5000))
          }
          break
        }

        case 'ArrowRight': {
          e.preventDefault()
          const el = s.playback?.audio?.getCurrentElement()
          if (el && s.playback?.seek) {
            const durationMs = (el.duration || Infinity) * 1000
            s.playback.seek(Math.min(durationMs, el.currentTime * 1000 + 5000))
          }
          break
        }

        case 'f':
        case 'F':
          e.preventDefault()
          s.reportInterfaceState({
            isFullscreenVisuals: !s.isFullscreenVisuals,
            showUIControls: !s.isFullscreenVisuals
          })
          break

        case 'Escape':
          e.preventDefault()
          if (s.isFullscreenVisuals) {
            s.reportInterfaceState({ isFullscreenVisuals: false, showUIControls: false })
          } else {
            if (s.showLogin) s.onCloseLogin?.()
            if (s.showRegister) s.onCloseRegister?.()
          }
          break

        case 'PageUp': {
          e.preventDefault()
          const scroller = findScrollerAtCursor()
          if (scroller) scroller.scrollBy({ top: -scroller.clientHeight * 0.8, behavior: e.repeat ? 'instant' : 'smooth' })
          break
        }

        case 'PageDown': {
          e.preventDefault()
          const scroller = findScrollerAtCursor()
          if (scroller) scroller.scrollBy({ top: scroller.clientHeight * 0.8, behavior: e.repeat ? 'instant' : 'smooth' })
          break
        }

        case 'Home': {
          e.preventDefault()
          const scroller = findScrollerAtCursor()
          if (scroller) scroller.scrollTo({ top: 0, behavior: 'smooth' })
          break
        }

        case 'End': {
          e.preventDefault()
          const scroller = findScrollerAtCursor()
          if (scroller) scroller.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' })
          break
        }
      }
    }

    const handleKeyDown = (e) => handleKeyEvent(e, true)
    const handleKeyUp = (e) => handleKeyEvent(e, false)

    document.addEventListener('mousemove', trackMouse, { passive: true })
    document.addEventListener('keydown', handleKeyDown)
    document.addEventListener('keyup', handleKeyUp)

    return () => {
      document.removeEventListener('mousemove', trackMouse)
      document.removeEventListener('keydown', handleKeyDown)
      document.removeEventListener('keyup', handleKeyUp)
    }
  }, [])

  return null
}
