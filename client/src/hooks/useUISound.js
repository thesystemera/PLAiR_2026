import { useMemo } from 'react'
import { uiState } from '../contexts/UIStateContext'
import { UI_SOUNDS } from '../lib/uiSounds'

const BUTTON_FEEDBACK = new Set(['recordPress', 'recordRelease'])

function playSound(key) {
  if (uiState.settingsState.notificationsMuted && !BUTTON_FEEDBACK.has(key)) return
  window.audioEngine?.playUiSound(UI_SOUNDS[key])
}

export function useUISound() {
  return useMemo(() => ({
    playRecordPress: () => playSound('recordPress'),
    playRecordRelease: () => playSound('recordRelease'),
    playBroadcast: () => playSound('broadcast'),
    playTxt: () => playSound('txt'),
    playTranscript: () => playSound('transcript'),
    playBot: () => playSound('bot'),
    playCommand: () => playSound('command'),
    playError: () => playSound('error'),
    playWarning: () => playSound('warning'),
    playInfo: () => playSound('info'),
  }), [])
}
