import { useEffect, useRef, useCallback, useState } from 'react'
import { useWebSocketSubscribe } from '../contexts/WebSocketContext'
import { useUIState } from '../contexts/UIStateContext'
import { useFFTProcessor } from './useFFTProcessor'
import { AudioInteractionManager } from '../lib/audioInteractionManager'
import { DJBroadcastChain } from '../lib/djBroadcastChain'
import { logger } from '../lib/logger'

const INITIALIZATION_TIMEOUT = 30000

const SPEAKER_COLORS = {
  terry: { r: 0, g: 128, b: 255 },
  shaquille: { r: 255, g: 0, b: 128 },
  computer: { r: 0, g: 255, b: 0 }
}

function calculateBlendedColor(speakerIntensities) {
  const activeColors = []

  for (const [speaker, intensity] of Object.entries(speakerIntensities)) {
    if (typeof intensity === 'number') {
      const baseColor = SPEAKER_COLORS[speaker]
      if (baseColor) {
        let rawIntensity = 1 - intensity
        rawIntensity = Math.max(0, rawIntensity)
        const colorIntensity = Math.pow(rawIntensity, 3)

        const adjustedIntensity = speaker === 'computer' ? colorIntensity * 0.8 : colorIntensity

        if (adjustedIntensity > 0.05) {
          activeColors.push({
            r: baseColor.r * adjustedIntensity,
            g: baseColor.g * adjustedIntensity,
            b: baseColor.b * adjustedIntensity
          })
        }
      }
    }
  }

  if (activeColors.length === 0) return { r: 147, g: 51, b: 234 }

  return activeColors.reduce((blend, color) => ({
    r: Math.max(blend.r, color.r),
    g: Math.max(blend.g, color.g),
    b: Math.max(blend.b, color.b)
  }), { r: 0, g: 0, b: 0 })
}

export function useDJAudioStream(audioElementRef) {
  const { reportEngineStatus, speakerColorRef, engineState, settingsState } = useUIState()
  const isActiveDevice = engineState.isActiveDevice

  const streamBuffersRef = useRef(new Map())
  const streamQueueRef = useRef([])
  const isDJSpeakingRef = useRef(false)
  const [isDJSpeaking, setIsDJSpeaking] = useState(false)
  const [analyser, setAnalyser] = useState(null)

  const playNextStreamRef = useRef(null)

  useFFTProcessor(isDJSpeaking, analyser, 'djFftData', { processingMode: 'frequency_bands' })

  // Register RAF source for debugging
  useEffect(() => {
    window.registerRAFSource?.('DJAudioStream')
  }, [])

  const colorScheduleRef = useRef([])
  const lastAppliedColorRef = useRef({ r: 147, g: 51, b: 234 })

  const mediaSourceRef = useRef(null)
  const sourceBufferRef = useRef(null)
  const currentStreamIdRef = useRef(null)
  const [currentStreamId, setCurrentStreamId] = useState(null)
  const playNextStreamTimeoutRef = useRef(null)
  const currentEventListenersRef = useRef({ play: null, ended: null, pause: null })

  const broadcastChainRef = useRef(null)
  const isAudioSetupRef = useRef(false)
  const isInitializingRef = useRef(false)

  const setupAudioProcessing = useCallback((audioElement) => {
    if (isAudioSetupRef.current) return true
    if (!window.audioEngine?.context) {
      logger.warn('[DJ Stream] AudioEngine not ready')
      return false
    }

    const chain = new DJBroadcastChain(
      window.audioEngine.context,
      audioElement
    )
    const analyserNode = chain.setup()

    broadcastChainRef.current = chain
    setAnalyser(analyserNode)
    isAudioSetupRef.current = true

    return true
  }, [])

  const getScheduledTime = useCallback(() => {
    if (!audioElementRef.current || !sourceBufferRef.current) return 0

    try {
      return audioElementRef.current.currentTime +
        (sourceBufferRef.current.buffered.length > 0
          ? sourceBufferRef.current.buffered.end(sourceBufferRef.current.buffered.length - 1) - audioElementRef.current.currentTime
          : 0)
    } catch {
      return audioElementRef.current.currentTime
    }
  }, [audioElementRef])

  const processNextChunk = useCallback(() => {
    if (!sourceBufferRef.current?.updating && currentStreamIdRef.current) {
      const buffer = streamBuffersRef.current.get(currentStreamIdRef.current)
      if (!buffer || buffer.length === 0) return

      const nextChunk = buffer.shift()

      try {
        if (nextChunk === 'END') {
          if (mediaSourceRef.current?.readyState === 'open') {
            mediaSourceRef.current.endOfStream()
          }
        } else {
          if (!sourceBufferRef.current) {
            logger.warn(`[DJ Stream] SourceBuffer destroyed, dropping chunk`)
            return
          }
          sourceBufferRef.current.appendBuffer(nextChunk.audio)

          if (nextChunk.colorUpdate) {
            colorScheduleRef.current.push({
              intensities: nextChunk.colorUpdate.intensities,
              time: nextChunk.colorUpdate.time
            })

            if (colorScheduleRef.current.length > 50) {
              const now = audioElementRef.current?.currentTime || 0
              colorScheduleRef.current = colorScheduleRef.current.filter(c => c.time > now - 2.0)
            }
          }
        }
      } catch (error) {
        logger.error('[DJ Stream] Chunk processing error:', error)
      }
    }
  }, [audioElementRef])

  const getBufferedDuration = useCallback(() => {
    if (!sourceBufferRef.current || sourceBufferRef.current.buffered.length === 0) {
      return 0
    }
    const bufferedEnd = sourceBufferRef.current.buffered.end(sourceBufferRef.current.buffered.length - 1)
    const currentTime = audioElementRef.current?.currentTime || 0
    return Math.max(0, bufferedEnd - currentTime)
  }, [audioElementRef])

  const removeEventListeners = useCallback(() => {
    const audioElement = audioElementRef.current
    if (!audioElement) return

    if (currentEventListenersRef.current.play) {
      audioElement.removeEventListener('play', currentEventListenersRef.current.play)
      currentEventListenersRef.current.play = null
    }
    if (currentEventListenersRef.current.ended) {
      audioElement.removeEventListener('ended', currentEventListenersRef.current.ended)
      currentEventListenersRef.current.ended = null
    }
    if (currentEventListenersRef.current.pause) {
      audioElement.removeEventListener('pause', currentEventListenersRef.current.pause)
      currentEventListenersRef.current.pause = null
    }
  }, [audioElementRef])

  const cleanupStream = useCallback((streamId, { destroyMediaSource = false } = {}) => {
    if (streamId) {
      streamBuffersRef.current.delete(streamId)
    }

    colorScheduleRef.current = []
    removeEventListeners()

    if (sourceBufferRef.current && mediaSourceRef.current) {
      try {
        if (
          mediaSourceRef.current.readyState === 'open' &&
          mediaSourceRef.current.sourceBuffers.length > 0
        ) {
          mediaSourceRef.current.removeSourceBuffer(sourceBufferRef.current)
        }
      } catch (error) {
        logger.warn('[DJ Stream] SourceBuffer removal failed:', error)
      }
      sourceBufferRef.current = null
    }

    if (destroyMediaSource && mediaSourceRef.current && audioElementRef.current) {
      URL.revokeObjectURL(audioElementRef.current.src)
      mediaSourceRef.current = null
    }
  }, [audioElementRef, removeEventListeners])

  const triggerNextStream = useCallback(() => {
    if (playNextStreamTimeoutRef.current) {
      clearTimeout(playNextStreamTimeoutRef.current)
    }
    playNextStreamTimeoutRef.current = setTimeout(() => {
      if (playNextStreamRef.current) {
        void playNextStreamRef.current()
      }
    }, 100)
  }, [])

  const handleStreamError = useCallback((streamId) => {
    logger.error(`[DJ Stream] Error for stream ${streamId}`)
    isDJSpeakingRef.current = false
    setIsDJSpeaking(false)
    isInitializingRef.current = false

    const noMoreStreams = streamQueueRef.current.length === 0
    cleanupStream(streamId, { destroyMediaSource: noMoreStreams })

    if (streamId === currentStreamIdRef.current) {
      currentStreamIdRef.current = null
      setCurrentStreamId(null)
      triggerNextStream()
    }
  }, [cleanupStream, triggerNextStream])

  const playNextStream = useCallback(async () => {
    if (streamQueueRef.current.length === 0) return
    if (isDJSpeakingRef.current || isInitializingRef.current) return

    isInitializingRef.current = true
    const nextStreamId = streamQueueRef.current.shift()
    currentStreamIdRef.current = nextStreamId

    if (window.audioEngine?.context?.state === 'suspended') {
      await window.audioEngine.context.resume()
    }

    const initializationTimeout = setTimeout(() => {
      logger.error(`[DJ Stream] Initialization timeout ${nextStreamId}`)
      handleStreamError(nextStreamId)
    }, INITIALIZATION_TIMEOUT)

    try {
      if (mediaSourceRef.current) {
        if (mediaSourceRef.current.readyState === 'open') {
          mediaSourceRef.current.endOfStream()
        }
        mediaSourceRef.current = null
      }

      mediaSourceRef.current = new MediaSource()
      audioElementRef.current.src = URL.createObjectURL(mediaSourceRef.current)

      if (!broadcastChainRef.current || !analyser) {
        const success = setupAudioProcessing(audioElementRef.current)
        if (!success) {
          logger.error('[DJ Stream] Setup failed - AudioEngine not ready')
          isInitializingRef.current = false
          handleStreamError(nextStreamId)
          return
        }
      }

      removeEventListeners()

      currentEventListenersRef.current.play = () => {
        isDJSpeakingRef.current = true
        setIsDJSpeaking(true)
        if (broadcastChainRef.current && window.audioEngine?.masterGain) {
          broadcastChainRef.current.connect(window.audioEngine.masterGain)
        }
      }
      audioElementRef.current.addEventListener('play', currentEventListenersRef.current.play)

      currentEventListenersRef.current.pause = () => {
        if (broadcastChainRef.current) {
          broadcastChainRef.current.disconnect()
        }
        isDJSpeakingRef.current = false
        setIsDJSpeaking(false)
      }
      audioElementRef.current.addEventListener('pause', currentEventListenersRef.current.pause)

      currentEventListenersRef.current.ended = () => {
        if (broadcastChainRef.current) {
          broadcastChainRef.current.disconnect()
        }

        const endedStreamId = currentStreamIdRef.current
        const noMoreStreams = streamQueueRef.current.length === 0

        isDJSpeakingRef.current = false
        setIsDJSpeaking(false)

        cleanupStream(endedStreamId, { destroyMediaSource: noMoreStreams })
        currentStreamIdRef.current = null
      setCurrentStreamId(null)

        triggerNextStream()
      }
      audioElementRef.current.addEventListener('ended', currentEventListenersRef.current.ended)

      await new Promise((resolve, reject) => {
        const setupSourceBuffer = () => {
          try {
            sourceBufferRef.current = mediaSourceRef.current.addSourceBuffer('audio/webm; codecs=opus')
            sourceBufferRef.current.mode = 'sequence'

            const onUpdateEnd = () => { processNextChunk() }
            sourceBufferRef.current.addEventListener('updateend', onUpdateEnd)

            const onFirstBuffer = () => {
              if (
                getBufferedDuration() > 0 ||
                streamBuffersRef.current.get(nextStreamId)?.includes('END')
              ) {
                clearTimeout(initializationTimeout)
                sourceBufferRef.current.removeEventListener('updateend', onFirstBuffer)
                resolve()
              }
            }
            sourceBufferRef.current.addEventListener('updateend', onFirstBuffer)
            processNextChunk()
          } catch (error) {
            reject(error)
          }
        }

        if (mediaSourceRef.current.readyState === 'open') {
          setupSourceBuffer()
        } else {
          mediaSourceRef.current.addEventListener('sourceopen', setupSourceBuffer, { once: true })
        }
      })

      isInitializingRef.current = false

      if (settingsState.ttsMuted) {
        logger.info('[DJ Stream] TTS is muted, skipping playback')
        handleStreamError(nextStreamId)
        return
      }

      if (audioElementRef.current.paused) {
        await audioElementRef.current.play()
      } else {
        isDJSpeakingRef.current = true
        setIsDJSpeaking(true)
      }
    } catch (error) {
      logger.error('[DJ Stream] Playback failed:', error)
      isInitializingRef.current = false
      clearTimeout(initializationTimeout)
      handleStreamError(nextStreamId)
    }
  }, [audioElementRef, analyser, processNextChunk, getBufferedDuration, setupAudioProcessing, removeEventListeners, cleanupStream, handleStreamError, settingsState.ttsMuted, triggerNextStream])

  useEffect(() => {
    playNextStreamRef.current = playNextStream
  }, [playNextStream])

  useWebSocketSubscribe('tts_stream_start', useCallback((data) => {
    if (!isActiveDevice || !data?.unique_id) return
    streamBuffersRef.current.set(data.unique_id, [])
    streamQueueRef.current.push(data.unique_id)

    if (!isDJSpeakingRef.current) {
      if (AudioInteractionManager.hasInteraction()) {
        void playNextStream()
      }
    }
  }, [isActiveDevice, playNextStream]))

  useWebSocketSubscribe('tts_stream_audio_chunk', useCallback((data) => {
    if (!isActiveDevice) return
    if (!currentStreamIdRef.current && !streamQueueRef.current.includes(data.unique_id)) return
    if (!streamBuffersRef.current.has(data.unique_id)) streamBuffersRef.current.set(data.unique_id, [])

    try {
      const byteCharacters = atob(data.chunk)
      const byteArray = Uint8Array.from(byteCharacters, (char) => char.charCodeAt(0))
      streamBuffersRef.current.get(data.unique_id).push({
        audio: byteArray,
        colorUpdate: {
          time: getScheduledTime(),
          intensities: data.speaker_intensities
        }
      })
      if (currentStreamIdRef.current === data.unique_id) processNextChunk()
    } catch (_err) {
      logger.error('Failed to process TTS chunk:', _err)
    }
  }, [isActiveDevice, getScheduledTime, processNextChunk]))

  useWebSocketSubscribe('tts_stream_end', useCallback((data) => {
    if (!isActiveDevice) return
    if (!currentStreamIdRef.current && !streamQueueRef.current.includes(data.unique_id)) return
    if (streamBuffersRef.current.has(data.unique_id)) {
      streamBuffersRef.current.get(data.unique_id).push('END')
      if (data.unique_id === currentStreamIdRef.current) processNextChunk()
    }
  }, [isActiveDevice, processNextChunk]))

  useEffect(() => {
    if (!isDJSpeaking) return

    const colorAnimationFrameRef = { current: null }

    const processColor = () => {
      if (isDJSpeakingRef.current && audioElementRef.current && speakerColorRef) {
        const currentTime = audioElementRef.current.currentTime

        let activeUpdate = null
        for (let i = colorScheduleRef.current.length - 1; i >= 0; i--) {
            if (colorScheduleRef.current[i].time <= currentTime) {
                activeUpdate = colorScheduleRef.current[i]
                break
            }
        }

        if (activeUpdate) {
            const color = calculateBlendedColor(activeUpdate.intensities)
            const last = lastAppliedColorRef.current

            if (color.r !== last.r || color.g !== last.g || color.b !== last.b) {
                speakerColorRef.current = color
                lastAppliedColorRef.current = color

                const root = document.documentElement
                root.style.setProperty('--dj-speaking-glow', `rgba(${Math.round(color.r)}, ${Math.round(color.g)}, ${Math.round(color.b)}, 0.8)`)
            }
        }
      }

      if (isDJSpeakingRef.current) {
        window.__rafDebug?.sources && (window.__rafDebug.sources['DJAudioStream'] = (window.__rafDebug.sources['DJAudioStream'] || 0) + 1)
        colorAnimationFrameRef.current = requestAnimationFrame(processColor)
      }
    }

    processColor()

    return () => {
      if (colorAnimationFrameRef.current) cancelAnimationFrame(colorAnimationFrameRef.current)
    }
  }, [speakerColorRef, audioElementRef, isDJSpeaking])

  useEffect(() => { reportEngineStatus({ isDJSpeaking }) }, [isDJSpeaking, reportEngineStatus])

  return {
    isDJSpeaking,
    currentStreamId,
    analyser,
    audioContext: window.audioEngine?.context || null
  }
}