import { memo, useEffect, useRef, useCallback, useState } from 'react'
import { useWebSocketSubscribe } from '../contexts/WebSocketContext'
import { useUISelector } from '../contexts/UIStateContext'
import { usePlaybackActions } from '../contexts/PlaybackContext'
import { useFFTProcessor } from './useFFTProcessor'
import { DJBroadcastChain } from '../lib/djBroadcastChain'
import { DJStreamPlayer, decodeBase64Chunk } from '../lib/djStreamPlayer'
import { logger } from '../lib/logger'
import { AudioInteractionManager } from '../lib/audioInteractionManager'

const DEFAULT_COLOR = { r: 147, g: 51, b: 234 }

const SPEAKER_COLORS = {
  jess: { r: 0, g: 128, b: 255 },
  leo: { r: 255, g: 0, b: 128 },
  computer: { r: 0, g: 255, b: 0 },
  station: { r: 255, g: 176, b: 0 }
}

function calculateBlendedColor(speakerIntensities) {
  let r = 0
  let g = 0
  let b = 0
  let active = false

  for (const speaker in speakerIntensities) {
    const intensity = speakerIntensities[speaker]
    const baseColor = SPEAKER_COLORS[speaker]
    if (typeof intensity !== 'number' || !baseColor) continue

    const colorIntensity = Math.pow(Math.max(0, 1 - intensity), 3)
    const adjustedIntensity = speaker === 'computer' ? colorIntensity * 0.8 : colorIntensity
    if (adjustedIntensity <= 0.05) continue

    active = true
    r = Math.max(r, baseColor.r * adjustedIntensity)
    g = Math.max(g, baseColor.g * adjustedIntensity)
    b = Math.max(b, baseColor.b * adjustedIntensity)
  }

  return active ? { r, g, b } : DEFAULT_COLOR
}

function createVoiceElement() {
  const element = new Audio()
  element.preload = 'auto'
  element.setAttribute('playsinline', '')
  return element
}

function useDJAudioStream() {
  const {
    reportEngineStatus,
    speakerColorRef,
    isActiveDevice,
    isMicRecording,
    settingsState,
    toastInfo,
  } = useUISelector(state => ({
    reportEngineStatus: state.reportEngineStatus,
    speakerColorRef: state.speakerColorRef,
    isActiveDevice: state.engineState.isActiveDevice,
    isMicRecording: state.engineState.isMicRecording,
    settingsState: state.settingsState,
    toastInfo: state.toastInfo,
  }))
  const toastInfoRef = useRef(toastInfo)
  useEffect(() => { toastInfoRef.current = toastInfo }, [toastInfo])
  const { audio, talkBreak } = usePlaybackActions()
  const ttsMuted = settingsState.ttsMuted

  const [isDJSpeaking, setIsDJSpeaking] = useState(false)
  const [analyser, setAnalyser] = useState(null)
  const playerRef = useRef(null)
  const lastAppliedColorRef = useRef(DEFAULT_COLOR)

  useFFTProcessor(isDJSpeaking, analyser, 'djFftData', { processingMode: 'frequency_bands' })

  useEffect(() => {
    window.registerRAFSource?.('DJAudioStream')
  }, [])

  useEffect(() => {
    const element = createVoiceElement()
    let chain = null
    let disposed = false

    const getOutput = async () => {
      if (chain?.isUsable()) return chain
      const ready = await audio.initializeAudio()
      if (disposed || !ready) return null
      const engine = audio.getEngine()
      if (!engine?.context || engine.context.state === 'closed') return null
      if (chain) {
        if (chain.isUsable()) return chain
        logger.warn('[DJ Stream] Audio engine was recreated - DJ output chain is stale')
        return null
      }
      chain = new DJBroadcastChain(engine, element)
      setAnalyser(chain.setup())
      if (engine.context.state !== 'running') {
        engine.context.resume().catch(() => {})
      }
      return chain
    }

    const unregisterElement = AudioInteractionManager.registerMediaElement(element)
    let unsupportedShown = false
    const player = new DJStreamPlayer({
      element,
      getOutput,
      onSpeakingChange: (speaking) => {
        if (!disposed) setIsDJSpeaking(speaking)
      },
      onBlocked: (retry) => AudioInteractionManager.onUserGesture(retry),
      onUnsupported: () => {
        if (unsupportedShown || disposed) return
        unsupportedShown = true
        toastInfoRef.current?.("The DJ's voice can't play in this browser yet. Your music keeps playing.", 5000, 'top', 'dj-unsupported')
      },
      log: logger
    })
    playerRef.current = player
    talkBreak?.attachPlayer(player)

    return () => {
      disposed = true
      unregisterElement()
      talkBreak?.detachPlayer(player)
      player.destroy()
      chain?.destroy()
      if (playerRef.current === player) playerRef.current = null
      setAnalyser(null)
      setIsDJSpeaking(false)
    }
  }, [audio, talkBreak])

  useEffect(() => {
    playerRef.current?.setEnabled(isActiveDevice && !ttsMuted)
  }, [isActiveDevice, ttsMuted, audio])

  useEffect(() => {
    playerRef.current?.setHold(isMicRecording)
  }, [isMicRecording, audio])

  const connected = useWebSocketSubscribe('tts_stream_start', useCallback((data) => {
    if (data?.unique_id) playerRef.current?.handleStart(data.unique_id, { staged: data.tts_type === 'radio_segment' })
  }, []))

  useWebSocketSubscribe('tts_stream_audio_chunk', useCallback((data) => {
    const player = playerRef.current
    if (!player || !data?.chunk || !player.accepts(data.unique_id)) return
    try {
      player.handleChunk(data.unique_id, decodeBase64Chunk(data.chunk), data.speaker_intensities || null, data.chunk_num)
    } catch (error) {
      logger.error('[DJ Stream] Failed to decode TTS chunk:', error)
    }
  }, []))

  useWebSocketSubscribe('tts_stream_end', useCallback((data) => {
    if (data?.unique_id) {
      playerRef.current?.handleEnd(data.unique_id, {
        complete: data.complete !== false,
        durationS: typeof data.duration_s === 'number' ? data.duration_s : null,
        talkEndS: typeof data.talk_end_s === 'number' ? data.talk_end_s : null
      })
    }
  }, []))

  useWebSocketSubscribe('tts_stream_cancel', useCallback(() => {
    playerRef.current?.cancelAll('cancel')
  }, []))

  useEffect(() => {
    playerRef.current?.notifyConnection(connected)
  }, [connected])

  useEffect(() => {
    if (!isDJSpeaking || !speakerColorRef) return

    let frameId = null

    const processColor = () => {
      const player = playerRef.current
      if (player) {
        const intensities = player.getIntensitiesAt(player.getCurrentTime())
        if (intensities) {
          const color = calculateBlendedColor(intensities)
          const last = lastAppliedColorRef.current
          if (color.r !== last.r || color.g !== last.g || color.b !== last.b) {
            speakerColorRef.current = color
            lastAppliedColorRef.current = color
          }
        }
      }

      window.__rafDebug?.sources && (window.__rafDebug.sources['DJAudioStream'] = (window.__rafDebug.sources['DJAudioStream'] || 0) + 1)
      frameId = requestAnimationFrame(processColor)
    }

    processColor()

    return () => {
      if (frameId) cancelAnimationFrame(frameId)
    }
  }, [speakerColorRef, isDJSpeaking])

  useEffect(() => { reportEngineStatus({ isDJSpeaking }) }, [isDJSpeaking, reportEngineStatus])

  useEffect(() => () => reportEngineStatus({ isDJSpeaking: false }), [reportEngineStatus])

  return {
    isDJSpeaking,
    analyser,
    audioContext: audio.getEngine()?.context || null
  }
}

export const DJVoiceEngine = memo(function DJVoiceEngine() {
  useDJAudioStream()
  return null
})
