import { createContext, useContext, useEffect } from 'react'
import { useVoiceRecorder } from '../hooks/useVoiceRecorder'
import { useFFTProcessor } from '../hooks/useFFTProcessor'
import { triggerHaptic } from '../lib/haptics'
import { useUIState } from './UIStateContext'

const VoiceRecordingContext = createContext(null)

export function VoiceRecordingProvider({ children, mixerRef }) {
  const voiceRecorder = useVoiceRecorder()
  const { setMixerRef, toastError, reportEngineStatus } = useUIState()
  const errorToast = toastError

  useFFTProcessor(
    voiceRecorder.isRecording,
    voiceRecorder.analyser,
    'micFftData',
    { processingMode: 'frequency_bands' }
  )

  useEffect(() => {
    if (mixerRef) {
      setMixerRef(mixerRef)
    }
  }, [mixerRef, setMixerRef])

  useEffect(() => {
    if (voiceRecorder.recordingError) {
      errorToast(voiceRecorder.recordingError, 2000)
      triggerHaptic('error')
      voiceRecorder.clearError()
    }
  }, [voiceRecorder.recordingError, errorToast, voiceRecorder.clearError])

  useEffect(() => {
    reportEngineStatus({
      isMicRecording: voiceRecorder.isRecording
    })
  }, [voiceRecorder.isRecording, reportEngineStatus])

  return (
    <VoiceRecordingContext.Provider value={{
      ...voiceRecorder
    }}>
      {children}
    </VoiceRecordingContext.Provider>
  )
}

export function useVoiceRecording() {
  const context = useContext(VoiceRecordingContext)
  if (!context) {
    throw new Error('useVoiceRecording must be used within VoiceRecordingProvider')
  }
  return context
}