import { useState, useEffect } from 'react'
import { logger } from '../lib/logger'

export function useDeviceSelector() {
  const [devices, setDevices] = useState({ microphones: [], speakers: [] })
  const [selectedMicrophone, setSelectedMicrophone] = useState('')
  const [selectedSpeaker, setSelectedSpeaker] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    void loadDevices()
    loadPreferences()
  }, [])

  const loadDevices = async () => {
    try {
      setLoading(true)
      const devices = await navigator.mediaDevices.enumerateDevices()

      const microphones = devices
        .filter(device => device.kind === 'audioinput')
        .map(device => ({
          id: device.deviceId,
          label: device.label || `Microphone ${device.deviceId.slice(0, 5)}`
        }))

      const speakers = devices
        .filter(device => device.kind === 'audiooutput')
        .map(device => ({
          id: device.deviceId,
          label: device.label || `Speaker ${device.deviceId.slice(0, 5)}`
        }))

      setDevices({ microphones, speakers })
    } catch (error) {
      logger.error('Failed to enumerate devices:', error)
    } finally {
      setLoading(false)
    }
  }

  const loadPreferences = () => {
    const preferredMic = localStorage.getItem('preferredMicrophoneId')
    const preferredSpeaker = localStorage.getItem('preferredSpeakerId')

    if (preferredMic) setSelectedMicrophone(preferredMic)
    if (preferredSpeaker) setSelectedSpeaker(preferredSpeaker)
  }

  const selectMicrophone = (deviceId) => {
    setSelectedMicrophone(deviceId)
    localStorage.setItem('preferredMicrophoneId', deviceId)
  }

  const selectSpeaker = async (deviceId) => {
    setSelectedSpeaker(deviceId)
    localStorage.setItem('preferredSpeakerId', deviceId)

    return new Promise((resolve, reject) => {
      const requestId = Math.random().toString(36).slice(2)

      const handleResponse = (e) => {
        if (e.detail.requestId !== requestId) return
        window.removeEventListener('audio-output-device-response', handleResponse)

        if (e.detail.success) {
          resolve()
        } else {
          reject(new Error(e.detail.error || 'Failed to set output device'))
        }
      }

      window.addEventListener('audio-output-device-response', handleResponse)

      window.dispatchEvent(new CustomEvent('audio-output-device-change', {
        detail: { deviceId, requestId }
      }))

      setTimeout(() => {
        window.removeEventListener('audio-output-device-response', handleResponse)
        resolve()
      }, 2000)
    })
  }

  const requestPermissions = async () => {
    try {
      await navigator.mediaDevices.getUserMedia({ audio: true })
      await loadDevices()
    } catch (error) {
      logger.error('Failed to get media permissions:', error)
    }
  }

  return {
    devices,
    selectedMicrophone,
    selectedSpeaker,
    loading,
    selectMicrophone,
    selectSpeaker,
    requestPermissions,
    refreshDevices: loadDevices
  }
}