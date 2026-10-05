import { logger } from '../lib/logger'
import { useState, useRef, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'

import { LayoutGrid, MessageCircle, Radio as RadioIcon, Globe, Heart, AlertTriangle, History } from 'lucide-react'
import { useRadioUI, uiState, useUISelector } from '../contexts/UIStateContext'
import { usePlaybackActions } from '../contexts/PlaybackContext'
import { useUISound } from '../hooks/useUISound'
import { api } from '../lib/api'
import { useWebSocketEmit } from '../contexts/WebSocketContext'
import { useViewport } from '../contexts/ViewportContext'
import { Conversation } from './Conversation'
import { ListenerTimeline, TimelineFilters } from './ListenerTimeline'
import { DJTextComposer } from './DJTextComposer'
import { GestureGuide } from './GestureGuide'
import { InteractiveEngagementButton } from './InteractiveEngagementButton'
import { registerKeyboardRecordingCallback } from './KeyboardControls'
import { PanelHeader, TextRadioIcon } from './Panel'
import { Scroller } from './Scroller'
import { useDynamicTheme } from '../contexts/DynamicThemeContext'
import { PANEL } from '../lib/themeManager'

const RADIO_BUTTON_SIZE = 240
const RADIO_SIDE_WIDTH = 'min(18rem, 40vw)'

function useRadioAnchor(scrollerRef, anchorRef) {
  const [anchor, setAnchor] = useState({ offset: 0, scale: 1 })

  useEffect(() => {
    const measure = () => {
      const scroller = scrollerRef.current
      const anchorNode = anchorRef.current
      if (!scroller || !anchorNode) return
      const scrollerRect = scroller.getBoundingClientRect()
      const anchorRect = anchorNode.getBoundingClientRect()
      const offset = (anchorRect.top + anchorRect.height / 2) - (scrollerRect.top + scrollerRect.height / 2)
      const fit = Math.min(anchorRect.width, anchorRect.height) - 16
      const scale = Math.max(0.5, Math.min(1, fit / RADIO_BUTTON_SIZE))
      setAnchor(prev => (Math.abs(prev.offset - offset) < 0.5 && Math.abs(prev.scale - scale) < 0.005) ? prev : { offset, scale })
    }

    measure()
    const resizeObserver = new ResizeObserver(measure)
    if (scrollerRef.current) resizeObserver.observe(scrollerRef.current)
    if (anchorRef.current) resizeObserver.observe(anchorRef.current)
    window.addEventListener('resize', measure)

    return () => {
      resizeObserver.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [scrollerRef, anchorRef])

  return anchor
}

export function Radio() {
  const playback = usePlaybackActions()
  const { isMobile, isPhoneLandscape } = useViewport()
  const { getFilterAllActive, getFilterInactive } = useDynamicTheme()

  const { reportEngineStatus, buttonOpacity, buttonForegroundOpacity, engineState, buttonInteraction } = useRadioUI()
  const { interfaceState, radioInput, toggleRadioInput } = useUISelector(state => ({ interfaceState: state.interfaceState, radioInput: state.settingsState.radioInput, toggleRadioInput: state.toggleRadioInput }))
  const mobilePanel = interfaceState.currentMobilePanel
  const isTextInput = radioInput === 'text'
  const playerHeight = interfaceState.playerHeight

  const [messageFilter, setMessageFilter] = useState('all')
  const [showTimeline, setShowTimeline] = useState(false)
  const [timelineFilter, setTimelineFilter] = useState('all')
  const [filterCounts, setFilterCounts] = useState({
    all: 0,
    interactive: 0,
    announcer: 0,
    external: 0,
    shoutouts: 0,
    system: 0
  })

  const conversationScrollRef = useRef(null)
  const scrollerRef = useRef(null)
  const anchorRef = useRef(null)
  const { offset: maskOffset, scale: anchorScale } = useRadioAnchor(scrollerRef, anchorRef)

  const { toastError, toastInfo } = useUISelector(state => ({ toastError: state.toastError, toastInfo: state.toastInfo }))
  const errorToast = toastError
  const uiSound = useUISound(window.audioEngine)
  const emitWebSocketEvent = useWebSocketEmit()

  const isMusicPlaying = engineState.isMusicPlaying

  const talkToDJ = useCallback(async ({ audio = null, text = null }, offlineLabel) => {
    reportEngineStatus({ isAIProcessing: true })
    try {
      const response = await api.djTalk({ audio, text, context: 'generic_talk' })
      if (response?.offline && response?.response) {
        emitWebSocketEvent('transcription_complete', { text: offlineLabel })
        setTimeout(() => emitWebSocketEvent('conversation_update', { bot_response: response.response }), 300)
      }
      return true
    } catch (err) {
      logger.error('[Radio] Failed to contact DJs:', err)
      if (uiState.audioState.offlineMode) toastInfo("The DJs can't hear you while PLAiR is offline. Your music keeps playing.", 4000)
      else errorToast('Failed to contact DJs', 3000)
      return false
    } finally {
      reportEngineStatus({ isAIProcessing: false })
    }
  }, [reportEngineStatus, errorToast, toastInfo, emitWebSocketEvent])

  const sendToDJ = useCallback(async (audioBlob) => {
    if (!audioBlob) return
    try {
      const reader = new FileReader()
      reader.onloadend = () => {
        void talkToDJ({ audio: reader.result.split(',')[1] }, '🎤 Voice message (not sent, you are offline)')
      }
      reader.readAsDataURL(audioBlob)
    } catch (err) {
      logger.error('[Radio] Failed to process audio:', err)
      errorToast('Failed to process audio', 3000)
    }
  }, [talkToDJ, errorToast])

  const sendTextToDJ = useCallback((text) => talkToDJ({ text }, `💬 ${text} (not sent, you are offline)`), [talkToDJ])

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
  const showButtonMask = buttonOpacity > 0.5 && !isPhoneLandscape
  const besideButton = isPhoneLandscape && !isTextInput

  const buttonScale = buttonInteraction?.scale || 1
  const buttonVisualRadius = (RADIO_BUTTON_SIZE * buttonScale * anchorScale) / 2
  const maskInnerRadius = buttonVisualRadius * 0.92
  const maskOuterRadius = buttonVisualRadius * 1.12

  return (
    <div className="h-full w-full relative overflow-hidden flex flex-col">
      <GestureGuide />

      <PanelHeader title={<h2 className="text-lg md:text-xl font-bold">Radio</h2>}>
        <div className="flex flex-wrap justify-end gap-1 min-w-0 ml-3">
           {showTimeline && <TimelineFilters value={timelineFilter} onChange={setTimelineFilter} />}
           {!showTimeline && ['all', 'interactive', 'announcer', 'external', 'shoutouts', 'system'].map(filter => {
             const hasConversations = filterCounts[filter] > 0
             const isDisabled = !hasConversations
             return (
               <button
                 key={filter}
                 onClick={() => !isDisabled && setMessageFilter(filter)}
                 disabled={isDisabled}
                 aria-label={`Show ${filter} messages`}
                 title={`Show ${filter} messages`}
                 className="ui-press p-1.5 rounded transition-colors border"
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
          <button
            onClick={() => setShowTimeline(value => !value)}
            aria-pressed={showTimeline}
            aria-label="Timeline"
            title={showTimeline ? 'Timeline on: back to the conversation' : 'Timeline: everything that aired for you'}
            className="ui-press p-1.5 rounded transition-colors border ml-1"
            style={showTimeline ? getFilterAllActive() : getFilterInactive()}
          >
            <History size={16} />
          </button>
          {!isMobile && (
            <button
              onClick={toggleRadioInput}
              aria-pressed={isTextInput}
              aria-label="Text Radio"
              title={isTextInput ? 'Text Radio on: back to talking' : 'Text Radio: type to the DJs'}
              className="ui-press p-1.5 rounded transition-colors border ml-1"
              style={isTextInput ? getFilterAllActive() : getFilterInactive()}
            >
              <TextRadioIcon className="w-4 h-4" />
            </button>
          )}
        </div>
      </PanelHeader>

      <div ref={scrollerRef} className="flex-1 overflow-hidden relative">
        <Scroller
          ref={conversationScrollRef}
          className="h-full overflow-x-hidden"
          mask={showButtonMask
            ? `radial-gradient(circle at 50% calc(50% + ${maskOffset}px), transparent ${maskInnerRadius}px, black ${maskOuterRadius}px)`
            : null}
          style={{
            scrollbarWidth: 'thin',
            scrollbarColor: buttonOpacity < 1 ? 'rgba(136, 136, 136, 0.5) transparent' : 'transparent transparent',
          }}
        >
          <div
            className={besideButton ? 'w-full px-4 pt-2' : 'w-full max-w-3xl mx-auto px-4 pt-2'}
            style={besideButton ? { paddingRight: `calc(${RADIO_SIDE_WIDTH} + 0.5rem)` } : undefined}
          >
            {showTimeline && <ListenerTimeline key={timelineFilter} kind={timelineFilter} />}
            <div className={showTimeline ? 'hidden' : undefined}>
              <Conversation
                isOpen={true}
                messageFilter={messageFilter}
                onFilterCounts={handleFilterCounts}
                shouldAutoScroll={isPanelActive && !showTimeline}
              />
            </div>
          </div>
        </Scroller>
      </div>

      {isTextInput && (
        <div className="w-full max-w-3xl mx-auto">
          <DJTextComposer onSend={sendTextToDJ} />
        </div>
      )}

      {createPortal(
        <div
          ref={anchorRef}
          className="fixed flex items-center justify-center pointer-events-none"
          style={isPhoneLandscape ? {
            top: `calc(var(--safe-top) + ${PANEL.headerHeight}px)`,
            bottom: `${playerHeight}px`,
            right: 'var(--safe-right)',
            width: RADIO_SIDE_WIDTH,
            zIndex: 50
          } : {
            top: isMobile ? `calc(var(--safe-top) + ${PANEL.headerHeight}px)` : `${PANEL.headerHeight}px`,
            bottom: `${playerHeight + 64}px`,
            left: 0,
            right: 0,
            zIndex: 50
          }}
        >
          <div
            className="transition-opacity duration-fade"
            style={{
              opacity: buttonForegroundOpacity,
              transform: anchorScale < 1 ? `scale(${anchorScale})` : undefined,
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
            />
          </div>
        </div>,
        document.body
      )}
    </div>
  )
}