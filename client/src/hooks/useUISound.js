import { useRef, useEffect } from 'react'
import { useAudio } from './useAudio.js'

export function useUISound() {
  const { engineRef } = useAudio()

  const elementsRef = useRef({
    recordPress: [],
    recordRelease: [],
    broadcast: null,
    txt: null,
    transcript: null,
    bot: null,
    command: null,
    error: null,
    warning: null,
    info: null,
    stop: null,
    trackChange: null,
    trackError: null
  })

  const connectedSourcesRef = useRef(new WeakSet())

  useEffect(() => {
    const elements = elementsRef.current
    if (!document.getElementById('recordButtonPress1')) return

    elements.recordPress = [
      document.getElementById('recordButtonPress1'),
      document.getElementById('recordButtonPress2'),
      document.getElementById('recordButtonPress3')
    ]

    elements.recordRelease = [
      document.getElementById('recordButtonRelease1'),
      document.getElementById('recordButtonRelease2'),
      document.getElementById('recordButtonRelease3')
    ]

    elements.broadcast = document.getElementById('broadcastSound')
    elements.txt = document.getElementById('txtSound')
    elements.transcript = document.getElementById('transcriptSound')
    elements.bot = document.getElementById('botSound')
    elements.command = document.getElementById('commandSound')
    elements.error = document.getElementById('errorSound')
    elements.warning = document.getElementById('warningSound')
    elements.info = document.getElementById('infoSound')
    elements.stop = document.getElementById('stopSound')
    elements.trackChange = document.getElementById('spotifyTrackChangeSound')
    elements.trackError = document.getElementById('spotifyErrorSound')

    const audioEngine = engineRef.current

    if (audioEngine?.context && audioEngine?.uiSoundsGain) {
      const allElements = [
        ...elements.recordPress,
        ...elements.recordRelease,
        elements.broadcast,
        elements.txt,
        elements.transcript,
        elements.bot,
        elements.command,
        elements.error,
        elements.warning,
        elements.info,
        elements.stop,
        elements.trackChange,
        elements.trackError
      ].filter(Boolean)

      allElements.forEach(element => {
        if (!connectedSourcesRef.current.has(element)) {
          try {
            const source = audioEngine.context.createMediaElementSource(element)
            source.connect(audioEngine.uiSoundsGain)
            connectedSourcesRef.current.add(element)
          } catch {
            // Audio connection errors are intentionally suppressed
          }
        }
      })
    }
  }, [engineRef])

  const playElement = (element, volume = 1.0) => {
    if (!element) return

    element.volume = volume
    element.currentTime = 0

    element.play().catch(() => {})
  }

  const playRandom = (elements, volume = 1.0) => {
    if (!elements || elements.length === 0) return
    const element = elements[Math.floor(Math.random() * elements.length)]
    if (element) playElement(element, volume)
  }

  return {
    playRecordPress: (volume = 0.8) => playRandom(elementsRef.current.recordPress, volume),
    playRecordRelease: (volume = 0.8) => playRandom(elementsRef.current.recordRelease, volume),
    playBroadcast: (volume = 0.7) => playElement(elementsRef.current.broadcast, volume),
    playTxt: (volume = 0.7) => playElement(elementsRef.current.txt, volume),
    playTranscript: (volume = 0.7) => playElement(elementsRef.current.transcript, volume),
    playBot: (volume = 0.7) => playElement(elementsRef.current.bot, volume),
    playCommand: (volume = 0.7) => playElement(elementsRef.current.command, volume),
    playError: (volume = 0.8) => playElement(elementsRef.current.error, volume),
    playWarning: (volume = 0.8) => playElement(elementsRef.current.warning, volume),
    playInfo: (volume = 0.7) => playElement(elementsRef.current.info, volume),
    playTrackChange: (volume = 0.6) => playElement(elementsRef.current.trackChange, volume),
  }
}