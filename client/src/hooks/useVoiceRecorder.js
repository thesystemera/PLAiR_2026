import { logger } from '../lib/logger'
import { safeStorage } from '../lib/safeStorage'
import { useRef, useState, useEffect, useCallback } from 'react'

const SPEECH_BITRATE = { opus: 32000, aac: 64000 }

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

const setAudioSessionType = (type) => {
  if (!navigator.audioSession) return
  try {
    if (navigator.audioSession.type !== type) navigator.audioSession.type = type
  } catch (error) {
    logger.warn('[VoiceRecorder] Could not set the audio session type:', error)
  }
}

const releaseStream = (stream) => {
  if (!stream) return
  stream.getTracks().forEach(track => {
    try {
      track.stop()
      stream.removeTrack(track)
    } catch { /* track already gone */ }
  })
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

    releaseStream(streamRef.current)
    streamRef.current = null

    if (audioContext.current && audioContext.current.state !== 'closed') {
      try {
        audioContext.current.close().catch(() => {})
        audioContext.current = null
      } catch {
        audioContext.current = null
      }
    }

    setAudioSessionType('playback')

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
        channelCount: 1
      }

      const preferredMicrophoneId = safeStorage.get('preferredMicrophoneId')
      if (preferredMicrophoneId) {
        audioConstraints.deviceId = { exact: preferredMicrophoneId }
      }

      if (!audioContext.current || audioContext.current.state === 'closed') {
        audioContext.current = new (window.AudioContext || window.webkitAudioContext)()
      }
      const analysisContext = audioContext.current
      if (analysisContext.state !== 'running') analysisContext.resume().catch(() => {})

      setAudioSessionType('play-and-record')

      let stream
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints })
      } catch (error) {
        const staleDevice = audioConstraints.deviceId && (error?.name === 'OverconstrainedError' || error?.name === 'NotFoundError')
        if (!staleDevice) throw error
        safeStorage.remove('preferredMicrophoneId')
        delete audioConstraints.deviceId
        stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints })
      }

      if (!isStarting.current || audioContext.current !== analysisContext) {
        releaseStream(stream)
        setAudioSessionType('playback')
        return false
      }

      streamRef.current = stream

      const audioSource = audioContext.current.createMediaStreamSource(stream)
      analyserRef.current = audioContext.current.createAnalyser()
      analyserRef.current.fftSize = 1024
      audioSource.connect(analyserRef.current)

      setAnalyser(analyserRef.current)

      audioChunks.current = []

      const mimeType = getSupportedAudioMimeType()
      recordingMimeType.current = mimeType || 'audio/webm;codecs=opus'

      const recorderOptions = {
        audioBitsPerSecond: mimeType === 'audio/mp4' ? SPEECH_BITRATE.aac : SPEECH_BITRATE.opus,
        ...(mimeType ? { mimeType } : {})
      }
      const recorder = new MediaRecorder(stream, recorderOptions)
      mediaRecorder.current = recorder

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0 && mediaRecorder.current === recorder) {
          audioChunks.current.push(event.data)
        }
      }

      recorder.start(100)

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

      const recorder = mediaRecorder.current
      let settled = false
      const finish = () => {
        if (settled) return
        settled = true
        clearTimeout(fallback)
        const audioBlob = new Blob(audioChunks.current, { type: recordingMimeType.current })
        cleanup()
        resolve(audioBlob)
      }
      const fallback = setTimeout(finish, 1500)

      recorder.requestData()
      recorder.onstop = finish
      recorder.stop()

      releaseStream(streamRef.current)
      streamRef.current = null
      setAudioSessionType('playback')
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