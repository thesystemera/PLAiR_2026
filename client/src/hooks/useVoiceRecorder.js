import { logger } from '../lib/logger'
import { useRef, useState, useEffect, useCallback } from 'react'

const getSupportedAudioMimeType = () => {
  const webmOpus = 'audio/webm;codecs=opus'
  if (MediaRecorder.isTypeSupported(webmOpus)) {
    logger.info('[VoiceRecorder] Using WebM/Opus')
    return webmOpus
  }

  const mp4 = 'audio/mp4'
  if (MediaRecorder.isTypeSupported(mp4)) {
    logger.info('[VoiceRecorder] WebM/Opus not supported, using MP4/AAC')
    return mp4
  }

  logger.warn('[VoiceRecorder] No supported mime types, using browser default')
  return null
}

export const useVoiceRecorder = () => {
  const [isRecording, setIsRecording] = useState(false)
  const [analyser, setAnalyser] = useState(null)
  const [recordingError, setRecordingError] = useState(null)
  const [recordingSource, setRecordingSource] = useState(null)

  const mediaRecorder = useRef(null)
  const streamRef = useRef(null)
  const audioContext = useRef(null)
  const analyserRef = useRef(null)
  const audioChunks = useRef([])
  const recordingStartTime = useRef(0)
  const isStarting = useRef(false)
  const recordingMimeType = useRef('audio/webm;codecs=opus')

  const cleanup = useCallback(() => {
    isStarting.current = false

    if (mediaRecorder.current) {
      try {
        if (mediaRecorder.current.state === 'recording') {
          mediaRecorder.current.stop()
        }
        mediaRecorder.current.ondataavailable = null
        mediaRecorder.current.onstop = null
        mediaRecorder.current.onerror = null
        mediaRecorder.current = null
      } catch {
        mediaRecorder.current = null
      }
    }

    if (analyserRef.current) {
      try {
        analyserRef.current.disconnect()
        analyserRef.current = null
        setAnalyser(null)
      } catch {
        analyserRef.current = null
      }
    }

    if (streamRef.current) {
      try {
        const tracks = streamRef.current.getTracks()
        tracks.forEach(track => {
          track.stop()
          streamRef.current.removeTrack(track)
        })
        streamRef.current = null
      } catch {
        streamRef.current = null
      }
    }

    if (audioContext.current && audioContext.current.state !== 'closed') {
      try {
        audioContext.current.close().catch(() => {})
        audioContext.current = null
      } catch {
        audioContext.current = null
      }
    }

    audioChunks.current = []
    recordingStartTime.current = 0
    setIsRecording(false)
    setRecordingSource(null)
  }, [])

  const startRecording = useCallback(async (source = null) => {
    if (isStarting.current || isRecording) {
      return false
    }

    isStarting.current = true
    setRecordingError(null)
    setRecordingSource(source)

    try {
      if (mediaRecorder.current && mediaRecorder.current.state !== 'inactive') {
        cleanup()
        await new Promise(resolve => setTimeout(resolve, 100))
      }

      isStarting.current = true

      const audioConstraints = {
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
        sampleRate: 16000,
        channelCount: 1
      }

      const preferredMicrophoneId = localStorage.getItem('preferredMicrophoneId')
      if (preferredMicrophoneId) {
        audioConstraints.deviceId = { exact: preferredMicrophoneId }
      }

      const stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints })

      if (!isStarting.current) {
        stream.getTracks().forEach(track => track.stop())
        return false
      }

      streamRef.current = stream

      audioContext.current = new (window.AudioContext || window.webkitAudioContext)()
      const audioSource = audioContext.current.createMediaStreamSource(stream)
      analyserRef.current = audioContext.current.createAnalyser()
      analyserRef.current.fftSize = 1024
      audioSource.connect(analyserRef.current)

      setAnalyser(analyserRef.current)

      audioChunks.current = []

      const mimeType = getSupportedAudioMimeType()
      recordingMimeType.current = mimeType || 'audio/webm;codecs=opus'

      const recorderOptions = mimeType ? { mimeType } : {}
      mediaRecorder.current = new MediaRecorder(stream, recorderOptions)

      mediaRecorder.current.ondataavailable = (event) => {
        if (event.data.size > 0 && recordingStartTime.current > 0) {
          audioChunks.current.push(event.data)
        }
      }

      mediaRecorder.current.start(100)

      recordingStartTime.current = Date.now()
      setIsRecording(true)
      isStarting.current = false

      return true
    } catch (error) {
      console.warn('Recording start failed or aborted', error)
      cleanup()
      return false
    }
  }, [cleanup, isRecording])

  const stopRecording = useCallback(() => {
    return new Promise((resolve) => {
      const recordingDuration = recordingStartTime.current > 0
        ? Date.now() - recordingStartTime.current
        : 0

      setIsRecording(false)

      const wasValidRecording = recordingStartTime.current > 0
      recordingStartTime.current = 0

      if (recordingDuration < 1000) {
        setRecordingError('Hold button for at least 1 second while speaking')
        cleanup()
        resolve(null)
        return
      }

      if (!wasValidRecording || !mediaRecorder.current || mediaRecorder.current.state === 'inactive') {
        cleanup()
        resolve(null)
        return
      }

      mediaRecorder.current.requestData()

      mediaRecorder.current.onstop = () => {
        const audioBlob = new Blob(audioChunks.current, { type: recordingMimeType.current })

        cleanup()
        resolve(audioBlob)
      }

      mediaRecorder.current.stop()
    })
  }, [cleanup])

  const abortRecording = useCallback(() => {
    cleanup()
    setRecordingError(null)
    setIsRecording(false)
    setRecordingSource(null)
  }, [cleanup])

  useEffect(() => {
    return () => {
      cleanup()
    }
  }, [cleanup])

  const clearError = useCallback(() => {
    setRecordingError(null)
  }, [])

  return {
    isRecording,
    recordingError,
    recordingSource,
    startRecording,
    stopRecording,
    abortRecording,
    clearError,
    analyser
  }
}