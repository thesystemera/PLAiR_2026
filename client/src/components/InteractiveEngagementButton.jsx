import { useEffect, useRef, useState, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Mic, Loader } from 'lucide-react'
import { triggerHaptic } from '../lib/haptics'
import { useRadioUI } from '../contexts/UIStateContext'
import { useVoiceRecording } from '../contexts/VoiceRecordingContext'
import { useDynamicTheme } from '../contexts/DynamicThemeContext'

const numPoints = 32

export function InteractiveEngagementButton({
  onSwipeLeft = null,
  onSwipeRight = null,
  onSwipeUp = null,
  onSwipeDown = null,
  onRecordingComplete = null,
  audioElementRef = null,
  swipeThreshold = 50,
  uiSound = null,
  buttonType = 'radio',
  title = 'Hold to record voice'
}) {
  const { visualState, updateButtonInteraction, visualColorData, djFftDataRef, micFftDataRef, speakerColorRef } = useRadioUI()
  const { isRecording, recordingSource: activeRecordingSource, startRecording, stopRecording, abortRecording } = useVoiceRecording()
  const { getRadioButtonBaseRgb } = useDynamicTheme()

  const [isHovered, setIsHovered] = useState(false)
  const [isPressed, setIsPressed] = useState(false)

  useEffect(() => {
    const currentScale = isPressed
      ? sizeConfig.scale.recording
      : isHovered
        ? sizeConfig.scale.hover
        : sizeConfig.scale.normal
    if (buttonType === 'radio') {
      updateButtonInteraction({ isHovered, isPressed, scale: currentScale })
    }
  }, [isPressed, isHovered, buttonType, updateButtonInteraction])

  useEffect(() => {
    if (visualState === 4) {
      setIsPressed(false)
      setIsHovered(false)
    }
  }, [visualState])

  const wasPlayingBeforeRecordRef = useRef(false)

  useEffect(() => {
    if (!audioElementRef?.current) return
    const isOwn = isRecording && activeRecordingSource === buttonType

    if (isOwn) {
      wasPlayingBeforeRecordRef.current = !audioElementRef.current.paused
      if (!audioElementRef.current.paused) audioElementRef.current.pause()
    } else if (wasPlayingBeforeRecordRef.current) {
      audioElementRef.current.play().catch(() => {})
      wasPlayingBeforeRecordRef.current = false
    }
  }, [isRecording, activeRecordingSource, audioElementRef, buttonType])

  const interactionStateRef = useRef({
    startTime: 0,
    startX: 0,
    startY: 0,
    endX: 0,
    endY: 0,
    isMouseDown: false,
    isTouchDown: false,
    isSwipeAction: false
  })

  const canvasRef = useRef(null)
  const animationFrameRef = useRef(null)
  const isRunningRef = useRef(false)
  const currentBlobColorRef = useRef({ r: 147, g: 51, b: 234, a: 0.6 })
  const pulsePhaseRef = useRef(0)

  const sizeConfig = buttonType === 'search' ? {
    containerSize: 'w-10 h-10',
    scale: { normal: 1, hover: 1.1, recording: 1.15 },
    iconSize: 'w-5 h-5',
    baseRadius: 15,
    maxRadius: 30
  } : {
    containerSize: 'w-60 h-60',
    scale: { normal: 1, hover: 1.08, recording: 0.92 },
    iconSize: 'w-24 h-24',
    baseRadius: 50,
    maxRadius: 100
  }

  // Register RAF source for debugging
  useEffect(() => {
    window.registerRAFSource?.('EngagementBtn')
  }, [])

  // Canvas blob animation - only run RAF when actually animating
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    // Determine if we need animation
    const isRecordingOwn = isRecording && activeRecordingSource === buttonType
    const needsAudioAnimation = buttonType === 'radio' && (visualState === 1 || visualState === 3)
    const needsPulseAnimation = visualState === 4
    const isHoveredOrPressed = isHovered || isPressed

    // Skip RAF entirely if button is idle (visualState 0 or 5, not hovered, not recording)
    if (!isRecordingOwn && !needsAudioAnimation && !needsPulseAnimation && !isHoveredOrPressed) {
      // Cancel any existing RAF
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current)
        animationFrameRef.current = null
        isRunningRef.current = false
      }
      
      // Draw one static frame
      const ctx = canvas.getContext('2d')
      const dpr = window.devicePixelRatio || 1
      const logicalSize = buttonType === 'search' ? 80 : 240
      canvas.width = logicalSize * dpr
      canvas.height = logicalSize * dpr
      ctx.scale(dpr, dpr)
      const centerX = logicalSize / 2
      const centerY = logicalSize / 2

      ctx.clearRect(0, 0, logicalSize, logicalSize)

      let ctxColor = visualColorData?.currentVisualColor || getRadioButtonBaseRgb()
      const targetAlpha = visualState === 5 ? 0.8 : 0.9
      const baseRadius = sizeConfig.baseRadius

      ctx.beginPath()
      for (let i = 0; i <= numPoints; i++) {
        const angle = (i / numPoints) * Math.PI * 2 - Math.PI / 2
        const x = centerX + Math.cos(angle) * baseRadius
        const y = centerY + Math.sin(angle) * baseRadius
        if (i === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      }
      ctx.closePath()
      ctx.fillStyle = `rgba(${ctxColor.r}, ${ctxColor.g}, ${ctxColor.b}, ${targetAlpha})`
      ctx.shadowColor = `rgba(${ctxColor.r}, ${ctxColor.g}, ${ctxColor.b}, ${targetAlpha})`
      ctx.shadowBlur = buttonType === 'search' ? 5 : 25
      ctx.fill()

      return
    }
    
    // Already running? Don't start another loop
    if (isRunningRef.current) return
    isRunningRef.current = true

    // Animation is needed - start RAF loop
    const ctx = canvas.getContext('2d')
    const dpr = window.devicePixelRatio || 1
    const logicalSize = buttonType === 'search' ? 80 : 240
    canvas.width = logicalSize * dpr
    canvas.height = logicalSize * dpr
    ctx.scale(dpr, dpr)
    const centerX = logicalSize / 2
    const centerY = logicalSize / 2

    const renderBlob = () => {
      let currentFFT = new Array(32).fill(0)

      if (isRecording && activeRecordingSource === buttonType) {
        currentFFT = micFftDataRef.current
      } else if (buttonType === 'radio' && visualState === 3) {
        currentFFT = djFftDataRef.current
      }

      ctx.clearRect(0, 0, logicalSize, logicalSize)

      let ctxColor = visualColorData?.currentVisualColor || getRadioButtonBaseRgb()
      if (visualState === 3 && speakerColorRef?.current) {
        ctxColor = speakerColorRef.current
      }

      let targetAlpha = 0.9
      if (visualState === 1) targetAlpha = 1.0
      if (visualState === 4) targetAlpha = 1.0
      if (visualState === 5) targetAlpha = 0.8
      if (isHovered && !isPressed) targetAlpha = Math.min(1.0, targetAlpha + 0.1)

      const targetColor = { r: ctxColor.r, g: ctxColor.g, b: ctxColor.b, a: targetAlpha }

      let breathingOffset = 0
      if (visualState === 4) {
        pulsePhaseRef.current += 0.08
        breathingOffset = Math.sin(pulsePhaseRef.current) * (buttonType === 'search' ? 2 : 5)
      } else {
        pulsePhaseRef.current = 0
      }

      const baseRadius = sizeConfig.baseRadius + breathingOffset
      const maxRadius = sizeConfig.maxRadius

      const currentColor = currentBlobColorRef.current
      const isInteractionActive = isPressed || visualState === 4 || visualState === 1
      const lerpSpeed = isInteractionActive ? 0.3 : 0.1

      currentColor.r += (targetColor.r - currentColor.r) * lerpSpeed
      currentColor.g += (targetColor.g - currentColor.g) * lerpSpeed
      currentColor.b += (targetColor.b - currentColor.b) * lerpSpeed
      currentColor.a += (targetColor.a - currentColor.a) * lerpSpeed

      const stateColor = `rgba(${Math.round(currentColor.r)}, ${Math.round(currentColor.g)}, ${Math.round(currentColor.b)}, ${currentColor.a})`

      ctx.beginPath()
      const shouldReactToAudio = visualState === 1 || visualState === 3

      for (let i = 0; i <= numPoints; i++) {
        const angle = (i / numPoints) * Math.PI * 2 - Math.PI / 2
        const amplitude = shouldReactToAudio ? currentFFT[i % numPoints] : 0
        const normalizedAmplitude = amplitude / 255
        const radius = baseRadius + normalizedAmplitude * (maxRadius - baseRadius)
        const x = centerX + Math.cos(angle) * radius
        const y = centerY + Math.sin(angle) * radius
        if (i === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      }
      ctx.closePath()
      ctx.fillStyle = stateColor

      const avgFFT = shouldReactToAudio ? currentFFT.reduce((sum, val) => sum + val, 0) / numPoints : 0
      const dynamicGlow = (avgFFT || 0) * 0.5
      const processingGlow = visualState === 4 ? 15 : 0
      const hoverGlow = isHovered && !isPressed ? 10 : 0

      ctx.shadowColor = stateColor
      ctx.shadowBlur = (buttonType === 'search' ? 5 : 25) + processingGlow + dynamicGlow + hoverGlow
      ctx.fill()

      window.__rafDebug?.sources && (window.__rafDebug.sources['EngagementBtn'] = (window.__rafDebug.sources['EngagementBtn'] || 0) + 1)
      
      // Check if we should continue animating
      const shouldContinue =
        (isRecording && activeRecordingSource === buttonType) ||
        (buttonType === 'radio' && (visualState === 1 || visualState === 3)) ||
        (visualState === 4) ||
        isHovered ||
        isPressed
      
      if (shouldContinue) {
        animationFrameRef.current = requestAnimationFrame(renderBlob)
      } else {
        isRunningRef.current = false
        animationFrameRef.current = null
      }
    }

    renderBlob()
    return () => {
      isRunningRef.current = false
      if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current)
    }
  }, [visualState, sizeConfig, buttonType, isRecording, activeRecordingSource, micFftDataRef, djFftDataRef, isPressed, isHovered, visualColorData, speakerColorRef, getRadioButtonBaseRgb])

  const getSwipeDirection = useCallback((startX, startY, endX, endY, threshold) => {
    const diffX = startX - endX
    const diffY = startY - endY
    if (Math.abs(diffX) > Math.abs(diffY)) {
      if (Math.abs(diffX) > threshold) return diffX > 0 ? 'left' : 'right'
    } else {
      if (Math.abs(diffY) > threshold) return diffY > 0 ? 'up' : 'down'
    }
    return null
  }, [])

  const handleSwipeMove = useCallback((currentX, currentY) => {
    if (interactionStateRef.current.isSwipeAction) return

    if (Date.now() - interactionStateRef.current.startTime > 500) return

    const { startX, startY } = interactionStateRef.current
    const swipeDirection = getSwipeDirection(startX, startY, currentX, currentY, swipeThreshold)

    if (swipeDirection) {
      interactionStateRef.current.isSwipeAction = true

      if (abortRecording) {
        abortRecording()
      } else {
        stopRecording()
      }

      switch (swipeDirection) {
        case 'left': if (onSwipeLeft) onSwipeLeft(); break
        case 'right': if (onSwipeRight) onSwipeRight(); break
        case 'up': if (onSwipeUp) onSwipeUp(); break
        case 'down': if (onSwipeDown) onSwipeDown(); break
      }
    }
  }, [getSwipeDirection, swipeThreshold, stopRecording, abortRecording, onSwipeLeft, onSwipeRight, onSwipeUp, onSwipeDown])

  const resetInteractionState = useCallback(() => {
    interactionStateRef.current.isMouseDown = false
    interactionStateRef.current.isTouchDown = false
    interactionStateRef.current.isSwipeAction = false
    interactionStateRef.current.startTime = 0
  }, [])

  const handleMouseEnter = useCallback(() => setIsHovered(true), [])

  const handleMouseDown = useCallback((e) => {
    if (e.button !== 0) return
    e.preventDefault()
    setIsPressed(true)

    interactionStateRef.current.isMouseDown = true
    interactionStateRef.current.startTime = Date.now()
    interactionStateRef.current.startX = e.clientX
    interactionStateRef.current.startY = e.clientY
    interactionStateRef.current.endX = e.clientX
    interactionStateRef.current.endY = e.clientY
    interactionStateRef.current.isSwipeAction = false

    if (uiSound) uiSound.playRecordPress()
    triggerHaptic(buttonType === 'search' ? 'record' : 'strong')

    void startRecording(buttonType)
  }, [uiSound, buttonType, startRecording])

  const handleMouseMove = useCallback((e) => {
    if (!interactionStateRef.current.isMouseDown) return
    interactionStateRef.current.endX = e.clientX
    interactionStateRef.current.endY = e.clientY
    handleSwipeMove(e.clientX, e.clientY)
  }, [handleSwipeMove])

  const handleMouseUp = useCallback(async (e) => {
    e.preventDefault()
    setIsPressed(false)
    if (!interactionStateRef.current.isMouseDown) return

    if (!interactionStateRef.current.isSwipeAction) {
      if (uiSound) uiSound.playRecordRelease()
      triggerHaptic(buttonType === 'search' ? 'light' : 'medium')
      const audioBlob = await stopRecording()
      if (audioBlob && audioBlob.size > 0 && onRecordingComplete) {
        await onRecordingComplete(audioBlob)
      }
    }
    resetInteractionState()
  }, [uiSound, buttonType, stopRecording, onRecordingComplete, resetInteractionState])

  const handleMouseLeave = useCallback(async () => {
    setIsHovered(false)
    setIsPressed(false)
    if (interactionStateRef.current.isMouseDown) {
       if (!interactionStateRef.current.isSwipeAction) {
          const audioBlob = await stopRecording()
          if (audioBlob && audioBlob.size > 0 && onRecordingComplete) {
            await onRecordingComplete(audioBlob)
          }
       }
    }
    resetInteractionState()
  }, [stopRecording, onRecordingComplete, resetInteractionState])

  const handleTouchStart = useCallback((e) => {
    setIsPressed(true)
    const touch = e.touches[0]

    interactionStateRef.current.isTouchDown = true
    interactionStateRef.current.startTime = Date.now()
    interactionStateRef.current.startX = touch.clientX
    interactionStateRef.current.startY = touch.clientY
    interactionStateRef.current.endX = touch.clientX
    interactionStateRef.current.endY = touch.clientY
    interactionStateRef.current.isSwipeAction = false

    if (uiSound) uiSound.playRecordPress()
    triggerHaptic(buttonType === 'search' ? 'record' : 'strong')

    void startRecording(buttonType)
  }, [uiSound, buttonType, startRecording])

  const handleTouchMove = useCallback((e) => {
    const touch = e.touches[0]
    interactionStateRef.current.endX = touch.clientX
    interactionStateRef.current.endY = touch.clientY
    handleSwipeMove(touch.clientX, touch.clientY)
  }, [handleSwipeMove])

  const handleTouchEnd = useCallback(async () => {
    setIsPressed(false)
    if (!interactionStateRef.current.isTouchDown) return

    if (!interactionStateRef.current.isSwipeAction) {
      if (uiSound) uiSound.playRecordRelease()
      triggerHaptic(buttonType === 'search' ? 'light' : 'medium')
      const audioBlob = await stopRecording()
      if (audioBlob && audioBlob.size > 0 && onRecordingComplete) {
        await onRecordingComplete(audioBlob)
      }
    }
    resetInteractionState()
  }, [uiSound, buttonType, stopRecording, onRecordingComplete, resetInteractionState])

  const handleTouchCancel = useCallback(async () => {
    setIsPressed(false)
    interactionStateRef.current.isTouchDown = false
    await stopRecording()
    resetInteractionState()
  }, [stopRecording, resetInteractionState])

  const containerClasses = `${sizeConfig.containerSize} flex items-center justify-center`

  return (
      <button
        type="button"
        data-shader-element={buttonType === 'radio' ? "radio-button" : undefined}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseLeave}
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
        onTouchCancel={handleTouchCancel}
        onMouseEnter={handleMouseEnter}
        onContextMenu={(e) => e.preventDefault()}
        disabled={visualState === 4}
        className={`relative rounded-full transition-transform ${containerClasses}`}
        style={{
          transform: isPressed
            ? `scale(${sizeConfig.scale.recording})`
            : isHovered
              ? `scale(${sizeConfig.scale.hover})`
              : `scale(${sizeConfig.scale.normal})`,
          transition: 'transform 0.15s cubic-bezier(0.34, 1.56, 0.64, 1)',
          background: buttonType === 'search' ? 'rgba(0,0,0,0.2)' : 'transparent',
          WebkitTapHighlightColor: 'transparent',
          touchAction: 'none'
        }}
        title={title}
      >
        <canvas
          ref={canvasRef}
          className="absolute inset-0 w-full h-full pointer-events-none rounded-full"
          style={{ zIndex: 1 }}
        />
        <AnimatePresence mode="wait">
          {visualState === 4 ? (
            <motion.div
              key="transcribing"
              initial={{ opacity: 0, scale: 0.5 }}
              animate={{ opacity: 1, scale: 1, rotate: 360 }}
              exit={{ opacity: 0, scale: 0.5 }}
              transition={{ rotate: { duration: 1, repeat: Infinity, ease: 'linear' } }}
              className="relative z-10"
            >
              <Loader className={`${sizeConfig.iconSize} text-white pointer-events-none`} />
            </motion.div>
          ) : (
            <motion.div
              key="mic-icon"
              initial={{ opacity: 0, scale: 0.5 }}
              animate={{
                opacity: 1,
                scale: isHovered ? 1.1 : 1
              }}
              exit={{ opacity: 0, scale: 0.5 }}
              transition={{ scale: { duration: 0.2 } }}
              className="relative z-10"
            >
              <Mic className={`${sizeConfig.iconSize} text-white pointer-events-none`} />
            </motion.div>
          )}
        </AnimatePresence>
      </button>
  )
}