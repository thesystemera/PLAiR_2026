import { logger } from '../lib/logger'
import { useState, useRef, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'

function useMaskOffset(scrollerRef, playerHeight) {
  const [offset, setOffset] = useState(0)

  useEffect(() => {
    const calculateOffset = () => {
      if (!scrollerRef.current) return
      
      const scrollerRect = scrollerRef.current.getBoundingClientRect()
      const viewportHeight = window.innerHeight
      
      // Button container spans from header to player+64
      const buttonContainerTop = PANEL.headerHeight
      const buttonContainerBottom = viewportHeight - playerHeight - 64
      const buttonCenterY = (buttonContainerTop + buttonContainerBottom) / 2
      
      // Where is that relative to the scroller?
      const buttonCenterRelativeToScroller = buttonCenterY - scrollerRect.top
      const scrollerCenter = scrollerRect.height / 2
      
      setOffset(buttonCenterRelativeToScroller - scrollerCenter)
    }

    calculateOffset()
    
    const resizeObserver = new ResizeObserver(calculateOffset)
    if (scrollerRef.current) {
      resizeObserver.observe(scrollerRef.current)
    }
    window.addEventListener('resize', calculateOffset)
    
    return () => {
      resizeObserver.disconnect()
      window.removeEventListener('resize', calculateOffset)
    }
  }, [scrollerRef, playerHeight])

  return offset
}
import { LayoutGrid, MessageCircle, Radio as RadioIcon, Globe, Heart, AlertTriangle } from 'lucide-react'
import { useDJAudioStream } from '../hooks/useDJAudioStream'
import { useRadioUI, useUIState } from '../contexts/UIStateContext'
import { usePlayback } from '../contexts/PlaybackContext'
import { useUISound } from '../hooks/useUISound'
import { api } from '../lib/api'
import { useWebSocketEmit } from '../contexts/WebSocketContext'
import { useViewport } from '../contexts/ViewportContext'
import { Conversation } from './Conversation'
import { GestureGuide } from './GestureGuide'
import { InteractiveEngagementButton } from './InteractiveEngagementButton'
import { registerKeyboardRecordingCallback } from './KeyboardControls'
import { PanelHeader } from './Panel'
import { Scroller } from './Scroller'
import { useDynamicTheme } from '../contexts/DynamicThemeContext'
import { PANEL } from '../lib/themeManager'

export function Radio({ mobilePanel = 2, playerHeight = 80 }) {
  const playback = usePlayback()
  const { isMobile } = useViewport()
  const { getFilterAllActive, getFilterInactive } = useDynamicTheme()

  const { reportEngineStatus, reportInterfaceState, buttonOpacity, buttonForegroundOpacity, engineState, buttonInteraction } = useRadioUI()
  const { interfaceState } = useUIState()

  const [messageFilter, setMessageFilter] = useState('all')
  const [filterCounts, setFilterCounts] = useState({
    all: 0,
    interactive: 0,
    announcer: 0,
    external: 0,
    shoutouts: 0,
    system: 0
  })

  const audioElementRef = useRef(null)
  const conversationScrollRef = useRef(null)
  const scrollerRef = useRef(null)
  const maskOffset = useMaskOffset(scrollerRef, playerHeight)

  const { toastError } = useUIState()
  const errorToast = toastError
  const uiSound = useUISound(window.audioEngine)
  const emitWebSocketEvent = useWebSocketEmit()

  useDJAudioStream(audioElementRef)

  const isMusicPlaying = engineState.isMusicPlaying

  useEffect(() => {
    if (isMobile) {
      reportInterfaceState({
        currentMobilePanel: mobilePanel
      })
    }
  }, [isMobile, mobilePanel, reportInterfaceState])

  const sendToDJ = useCallback(async (audioBlob) => {
    if (!audioBlob) return
    try {
      reportEngineStatus({ isAIProcessing: true })
      const reader = new FileReader()
      reader.onloadend = async () => {
        const base64Audio = reader.result.split(',')[1]
        try {
          const response = await api.djTalk({ audio: base64Audio, text: null, context: 'generic_talk' })
          if (response?.offline && response?.response) {
            emitWebSocketEvent('transcription_complete', { text: '🎤 [Voice message - transcription unavailable offline]' })
            setTimeout(() => emitWebSocketEvent('conversation_update', { bot_response: response.response }), 300)
          }
        } catch (err) {
          logger.error('[Radio] Failed to contact DJs:', err)
          errorToast('Failed to contact DJs', 3000)
        } finally {
          reportEngineStatus({ isAIProcessing: false })
        }
      }
      reader.readAsDataURL(audioBlob)
    } catch (err) {
      logger.error('[Radio] Failed to process audio:', err)
      errorToast('Failed to process audio', 3000)
      reportEngineStatus({ isAIProcessing: false })
    }
  }, [reportEngineStatus, errorToast, emitWebSocketEvent])

  useEffect(() => {
    registerKeyboardRecordingCallback(sendToDJ)
    return () => registerKeyboardRecordingCallback(null)
  }, [sendToDJ])

  const handleSwipeLeft = useCallback(() => { void playback?.next?.() }, [playback])
  const handleSwipeRight = useCallback(() => { void playback?.previous?.() }, [playback])
  const handleSwipeUp = useCallback(() => { if (!isMusicPlaying) void playback?.togglePlay?.() }, [isMusicPlaying, playback])
  const handleSwipeDown = useCallback(() => { if (isMusicPlaying) void playback?.togglePlay?.() }, [isMusicPlaying, playback])

  const handleFilterCounts = useCallback((counts) => {
    setFilterCounts(counts)
  }, [])

  const isPanelActive = !interfaceState.isFullscreenVisuals && (!isMobile || mobilePanel === 2)
  const showButtonMask = buttonOpacity > 0.5
  
  // Button is w-60 h-60 = 240px, mask ratios are fractions of button size
  const buttonScale = buttonInteraction?.scale || 1
  const buttonVisualRadius = (240 * buttonScale) / 2
  // Mask ratios: inner hole ~92% of button radius, outer fade ~112% of button radius
  const maskInnerRadius = buttonVisualRadius * 0.92
  const maskOuterRadius = buttonVisualRadius * 1.12

  return (
    <div className="h-full w-full relative overflow-hidden flex flex-col">
      <audio ref={audioElementRef} style={{ display: 'none' }} />
      <GestureGuide />

      <PanelHeader title="Radio">
        <div className="flex gap-2">
           {['all', 'interactive', 'announcer', 'external', 'shoutouts', 'system'].map(filter => {
             const hasConversations = filterCounts[filter] > 0
             const isDisabled = !hasConversations
             return (
               <button
                 key={filter}
                 onClick={() => !isDisabled && setMessageFilter(filter)}
                 disabled={isDisabled}
                 className="p-2 rounded transition-colors border"
                 style={
                   isDisabled
                     ? { backgroundColor: 'rgba(128, 128, 128, 0.1)', borderColor: 'rgba(128, 128, 128, 0.2)', color: 'rgba(128, 128, 128, 0.4)', cursor: 'not-allowed' }
                     : messageFilter === filter ? getFilterAllActive() : getFilterInactive()
                 }
               >
                 {filter === 'all' && <LayoutGrid size={16} />}
                 {filter === 'interactive' && <MessageCircle size={16} />}
                 {filter === 'announcer' && <RadioIcon size={16} />}
                 {filter === 'external' && <Globe size={16} />}
                 {filter === 'shoutouts' && <Heart size={16} />}
                 {filter === 'system' && <AlertTriangle size={16} />}
               </button>
             )
           })}
        </div>
      </PanelHeader>

      <div ref={scrollerRef} className="flex-1 overflow-hidden relative">
        <Scroller
          ref={conversationScrollRef}
          className="h-full overflow-x-hidden"
          style={{
            scrollbarWidth: 'thin',
            scrollbarColor: buttonOpacity < 1 ? 'rgba(136, 136, 136, 0.5) transparent' : 'transparent transparent',
            maskImage: showButtonMask
              ? `radial-gradient(circle at 50% calc(50% + ${maskOffset}px), transparent ${maskInnerRadius}px, black ${maskOuterRadius}px)`
              : 'none',
            WebkitMaskImage: showButtonMask
              ? `radial-gradient(circle at 50% calc(50% + ${maskOffset}px), transparent ${maskInnerRadius}px, black ${maskOuterRadius}px)`
              : 'none',
            transition: 'mask-image 0.5s ease, -webkit-mask-image 0.5s ease'
          }}
        >
          <div className="w-full max-w-3xl mx-auto px-4 pt-2">
            <Conversation
              isOpen={true}
              messageFilter={messageFilter}
              onFilterCounts={handleFilterCounts}
              shouldAutoScroll={isPanelActive}
            />
          </div>
        </Scroller>
      </div>

      {createPortal(
        <div
          className="fixed inset-x-0 flex items-center justify-center pointer-events-none"
          style={{
            top: `${PANEL.headerHeight}px`,
            bottom: `${playerHeight + 64}px`,
            zIndex: 50
          }}
        >
          <div 
            className="transition-opacity duration-500"
            style={{ 
              opacity: buttonForegroundOpacity,
              pointerEvents: buttonForegroundOpacity > 0.9 ? 'auto' : 'none'
            }}
          >
            <InteractiveEngagementButton
              buttonType="radio"
              onSwipeLeft={handleSwipeLeft}
              onSwipeRight={handleSwipeRight}
              onSwipeUp={handleSwipeUp}
              onSwipeDown={handleSwipeDown}
              uiSound={uiSound}
              onRecordingComplete={sendToDJ}
              audioElementRef={audioElementRef}
            />
          </div>
        </div>,
        document.body
      )}
    </div>
  )
}