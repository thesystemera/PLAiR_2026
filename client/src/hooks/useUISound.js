import { useMemo } from 'react'

const SOUND_IDS = {
  recordPress: ['recordButtonPress1', 'recordButtonPress2', 'recordButtonPress3'],
  recordRelease: ['recordButtonRelease1', 'recordButtonRelease2', 'recordButtonRelease3'],
  broadcast: 'broadcastSound',
  txt: 'txtSound',
  transcript: 'transcriptSound',
  bot: 'botSound',
  command: 'commandSound',
  error: 'errorSound',
  warning: 'warningSound',
  info: 'infoSound',
  trackChange: 'spotifyTrackChangeSound',
}

function getSound(key) {
  const id = SOUND_IDS[key]
  if (Array.isArray(id)) {
    const pick = id[Math.floor(Math.random() * id.length)]
    return document.getElementById(pick)
  }
  return document.getElementById(id)
}

function playSound(key, volume) {
  const element = getSound(key)
  if (!element) return
  try {
    element.volume = volume
    element.currentTime = 0
  } catch {
    return
  }
  const result = element.play()
  if (result && typeof result.catch === 'function') result.catch(() => {})
}

export function useUISound() {
  return useMemo(() => ({
    playRecordPress: (volume = 0.8) => playSound('recordPress', volume),
    playRecordRelease: (volume = 0.8) => playSound('recordRelease', volume),
    playBroadcast: (volume = 0.7) => playSound('broadcast', volume),
    playTxt: (volume = 0.7) => playSound('txt', volume),
    playTranscript: (volume = 0.7) => playSound('transcript', volume),
    playBot: (volume = 0.7) => playSound('bot', volume),
    playCommand: (volume = 0.7) => playSound('command', volume),
    playError: (volume = 0.8) => playSound('error', volume),
    playWarning: (volume = 0.8) => playSound('warning', volume),
    playInfo: (volume = 0.7) => playSound('info', volume),
    playTrackChange: (volume = 0.6) => playSound('trackChange', volume),
  }), [])
}
