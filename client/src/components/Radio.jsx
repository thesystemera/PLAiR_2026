import { logger } from '../lib/logger'
import { useState, useRef, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion } from 'framer-motion'

import { LayoutGrid, MessageCircle, Radio as RadioIcon, Globe, Heart, AlertTriangle, History } from 'lucide-react'
import { useRadioButton, uiState, useUISelector } from '../contexts/UIStateContext'
import { usePlaybackActions } from '../contexts/PlaybackContext'
import { useUISound } from '../hooks/useUISound'
import { api } from '../lib/api'
import { blobToBase64 } from '../lib/utils'
import { useWebSocketEmit } from '../contexts/WebSocketContext'
import { useViewport } from '../contexts/ViewportContext'
import { Conversation } from './Conversation'
import { ListenerTimeline, TimelineFilters } from './ListenerTimeline'
import { DJTextComposer } from './DJTextComposer'
import { GestureGuide } from './GestureGuide'
import { InteractiveEngagementButton } from './InteractiveEngagementButton'
import { registerKeyboardRecordingCallback } from './KeyboardControls'
import { PanelHeader, TextRadioIcon, PANEL_CONFIG, PANEL_IDS } from './Panel'
import { Scroller } from './Scroller'
import { FadeSwap } from './Motion'
import { PRESETS } from '../lib/motion'
import { useDynamicTheme } from '../contexts/DynamicThemeContext'
import { PANEL } from '../lib/themeManager'
import { insetBottom, insetBottomAboveTabs } from '../lib/playerInset'
import { usePlayerInset } from '../hooks/usePlayerInset'

const RADIO_SIDE_WIDTH = 'min(18rem, 40vw)'

function useRadioAnchor(scrollerRef, anchorRef) {
  const [anchor, setAnchor] = useState({ offset: 0, scale: 1, size: 0 })

  useEffect(() => {
    const button = anchorRef.current?.querySelector('[data-shader-element="radio-button"]')

    const measure = () => {
      const scroller = scrollerRef.current
      const anchorNode = anchorRef.current
      if (!scroller || !anchorNode || !button) return
      const scrollerRect = scroller.getBoundingClientRect()
      const anchorRect = anchorNode.getBoundingClientRect()
      const offset = (anchorRect.top + anchorRect.height / 2) - (scrollerRect.top + scrollerRect.height / 2)
      const size = button.offsetWidth
      const fit = Math.min(anchorRect.width, anchorRect.height) - 16
      const scale = size > 0 ? Math.max(0.5, Math.min(1, fit / size)) : 1
      setAnchor(prev => (Math.abs(prev.offset - offset) < 0.5 && Math.abs(prev.scale - scale) < 0.005 && Math.abs(prev.size - size) < 0.5) ? prev : { offset, scale, size })
    }

    measure()
    const resizeObserver = new ResizeObserver(measure)
    if (scrollerRef.current) resizeObserver.observe(scrollerRef.current)
    if (anchorRef.current) resizeObserver.observe(anchorRef.current)
    if (button) resizeObserver.observe(button)
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

  const { radioButtonOpacity: buttonOpacity, radioButtonForegroundOpacity: buttonForegroundOpacity, radioButtonInteraction: buttonInteraction } = useRadioButton()
  const { reportEngineStatus, isMusicPlaying, mobilePanel, isFullscreenVisuals, radioInput, toggleRadioInput } = useUISelector(state => ({
    reportEngineStatus: state.reportEngineStatus,
    isMusicPlaying: state.engineState.isMusicPlaying,
    mobilePanel: state.interfaceState.currentMobilePanel,
    isFullscreenVisuals: state.interfaceState.isFullscreenVisuals,
    radioInput: state.settingsState.radioInput,
    toggleRadioInput: state.toggleRadioInput,
  }))
  const isTextInput = radioInput === 'text'

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
  const anchorInset = usePlayerInset(isPhoneLandscape ? insetBottom : insetBottomAboveTabs)
  const attachAnchor = useCallback((node) => {
    anchorRef.current = node
    anchorInset(node)
  }, [anchorInset])
  const { offset: maskOffset, scale: anchorScale, size: buttonSize } = useRadioAnchor(scrollerRef, anchorRef)

  const { toastError, toastInfo } = useUISelector(state => ({ toastError: state.toastError, toastInfo: state.toastInfo }))
  const errorToast = toastError
  const uiSound = useUISound(window.audioEngine)
  const emitWebSocketEvent = useWebSocketEmit()


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
    let audio = null
    try {
      audio = await blobToBase64(audioBlob)
    } catch (err) {
      logger.error('[Radio] Failed to process audio:', err)
    }
    if (!audio || audio === 'null') {
      errorToast('Failed to process audio', 3000)
      return
    }
    void talkToDJ({ audio }, '🎤 Voice message (not sent, you are offline)')
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

  const isPanelActive = !isFullscreenVisuals && (!isMobile || mobilePanel === 2)
  const showButtonMask = buttonOpacity > 0.5 && !isPhoneLandscape
  const besideButton = isPhoneLandscape && !isTextInput

  const buttonScale = buttonInteraction?.scale || 1
  const buttonVisualRadius = (buttonSize * buttonScale * anchorScale) / 2
  const maskInnerRadius = buttonVisualRadius * 0.92
  const maskOuterRadius = buttonVisualRadius * 1.12

  return (
    <div className="h-full w-full relative overflow-hidden flex flex-col">
      <GestureGuide />

      <PanelHeader title={<h2 className="text-lg md:text-xl font-bold">Radio</h2>}>
        <div className="flex items-center gap-2 min-w-0 ml-3">
          <div className="flex flex-wrap justify-end gap-1 min-w-0">
            <FadeSwap swapKey={showTimeline ? 'timeline' : 'conversation'} preset={PRESETS.fade} className="flex flex-wrap justify-end gap-1">
              {showTimeline ? <TimelineFilters value={timelineFilter} onChange={setTimelineFilter} /> : ['all', 'interactive', 'announcer', 'external', 'shoutouts', 'system'].map(filter => {
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
            </FadeSwap>
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
          </div>
          {!isMobile && (
            <button
              type="button"
              onClick={toggleRadioInput}
              aria-label={isTextInput ? 'Talk to the DJs' : 'Type to the DJs'}
              title={isTextInput ? 'Talk to the DJs' : 'Type to the DJs'}
              className="ui-press flex-shrink-0 w-9 h-9 bg-gray-700 text-white rounded-full hover:bg-gray-600 transition flex items-center justify-center"
            >
              <FadeSwap swapKey={radioInput} preset={PRESETS.iconSwap} mode="wait">
                {isTextInput ? PANEL_CONFIG[PANEL_IDS.RADIO].mobileIcon('w-4 h-4') : <TextRadioIcon className="w-4 h-4" />}
              </FadeSwap>
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
            {showTimeline && (
              <div key={timelineFilter} className="ui-fade-in">
                <ListenerTimeline kind={timelineFilter} scrollRef={conversationScrollRef} />
              </div>
            )}
            <div className={showTimeline ? 'hidden' : 'ui-fade-in'}>
              <Conversation
                isOpen={true}
                messageFilter={messageFilter}
                onFilterCounts={handleFilterCounts}
                shouldAutoScroll={isPanelActive && !showTimeline}
                scrollRef={conversationScrollRef}
              />
            </div>
          </div>
        </Scroller>
      </div>

      <AnimatePresence initial={false}>
        {isTextInput && (
          <motion.div key="composer" className="w-full max-w-3xl mx-auto" {...PRESETS.fade}>
            <DJTextComposer onSend={sendTextToDJ} />
          </motion.div>
        )}
      </AnimatePresence>

      {createPortal(
        <div
          ref={attachAnchor}
          className="fixed flex items-center justify-center pointer-events-none"
          style={isPhoneLandscape ? {
            top: `calc(var(--safe-top) + ${PANEL.headerHeight}px)`,
            right: 'var(--safe-right)',
            width: RADIO_SIDE_WIDTH,
            zIndex: 50
          } : {
            top: isMobile ? `calc(var(--safe-top) + ${PANEL.headerHeight}px)` : `${PANEL.headerHeight}px`,
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