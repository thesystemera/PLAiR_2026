import { createContext, useContext, useEffect } from 'react'
import { useVoiceRecorder } from '../hooks/useVoiceRecorder'
import { useFFTProcessor } from '../hooks/useFFTProcessor'
import { triggerHaptic } from '../lib/haptics'
import { useUISelector } from './UIStateContext'

const VoiceRecordingContext = createContext(null)

export function VoiceRecordingProvider({ children, mixerRef }) {
  const voiceRecorder = useVoiceRecorder()
  const {
    setMixerRef,
    toastError,
    reportEngineStatus,
  } = useUISelector(state => ({
    setMixerRef: state.setMixerRef,
    toastError: state.toastError,
    reportEngineStatus: state.reportEngineStatus,
  }))
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

  const { recordingError, clearError } = voiceRecorder

  useEffect(() => {
    if (recordingError) {
      errorToast(recordingError, 2000)
      triggerHaptic('error')
      clearError()
    }
  }, [recordingError, errorToast, clearError])

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