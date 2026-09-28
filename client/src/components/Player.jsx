import { Play, Pause, SkipForward, SkipBack, Volume2, VolumeX, HardDrive, Bell} from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { memo, useState, useCallback, useEffect, useRef, useMemo } from 'react'
import { formatDuration } from '../lib/utils'
import { triggerHaptic } from '../lib/haptics'
import { useDynamicTheme } from '../contexts/DynamicThemeContext'
import { useDevicePicker, DevicePickerButton, DevicePickerBanner, DevicePickerPanel } from './DevicePicker'
import { useBitratePicker, BitratePickerButton, BitratePickerPanel } from './BitratePicker'
import { GenerationQueuePanel } from './GenerationQueuePanel'
import { OnAirBadge } from './OnAirBadge'
import { useArtwork, useUISelector } from '../contexts/UIStateContext'
import { usePlaybackActions } from '../contexts/PlaybackContext'
import { useAuth } from '../contexts/AuthContext'
import { useViewport } from '../contexts/ViewportContext'
import { api } from '../lib/api'
import { saveGuestSettings } from '../lib/accountSettings'
import { CSS_TRANSITION, MOTION, PRESETS } from '../lib/motion'
import { artPop, nudge } from '../lib/microMotion'

const PLAYED_WINDOW_STYLE = {
  transition: CSS_TRANSITION.progress
}

const PLAYED_CONTENT_STYLE = {
  transition: CSS_TRANSITION.progress
}

const progressTranslate = (offsetPercent, width) => (
  width > 0 ? `translateX(${offsetPercent * width / 100}px)` : `translateX(${offsetPercent}%)`
)

const PROGRESS_STYLE_WRITERS = {
  window: (node, percent, width) => { node.style.transform = progressTranslate(percent - 100, width) },
  content: (node, percent, width) => { node.style.transform = progressTranslate(100 - percent, width) },
  playhead: (node, percent, width) => { node.style.transform = progressTranslate(percent - 100, width) },
  bar: (node, percent) => { node.style.transform = `translateX(${percent - 100}%)` },
}

const PROGRESS_NODE_KEYS = Object.keys(PROGRESS_STYLE_WRITERS)

function hslToRgb(h, s, l) {
  let r, g, b

  if (s === 0) {
    r = g = b = l
  } else {
    const hue2rgb = (p, q, t) => {
      if (t < 0) t += 1
      if (t > 1) t -= 1
      if (t < 1 / 6) return p + (q - p) * 6 * t
      if (t < 1 / 2) return q
      if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6
      return p
    }

    const q = l < 0.5 ? l * (1 + s) : l + s - l * s
    const p = 2 * l - q
    r = hue2rgb(p, q, h + 1 / 3)
    g = hue2rgb(p, q, h)
    b = hue2rgb(p, q, h - 1 / 3)
  }

  return { r: Math.round(r * 255), g: Math.round(g * 255), b: Math.round(b * 255) }
}

function getSectionModifiers(sectionType) {
  switch (sectionType) {
    case 'chorus':
      return { hueShift: 60 / 360, saturation: 1.4, lightness: 1.2 }
    case 'bridge':
      return { hueShift: -30 / 360, saturation: 0.8, lightness: 0.9 }
    case 'intro':
    case 'outro':
      return { hueShift: 0, saturation: 0.6, lightness: 0.7 }
    case 'verse':
    default:
      return { hueShift: 0, saturation: 1.0, lightness: 1.0 }
  }
}

function getDynamicSegmentColor(normalizedLoudness, trackEnergy, trackValence, sectionType) {
  const modifiers = getSectionModifiers(sectionType)

  const baseHue = (trackValence * 180 + 240) % 360 / 360
  const finalHue = (baseHue + modifiers.hueShift + 1) % 1

  const lightness = 0.25 + (normalizedLoudness * 0.6)
  const baseSaturation = 0.4 + trackEnergy * 0.6
  const saturation = baseSaturation * (0.6 + normalizedLoudness * 0.4)

  const finalLightness = Math.min(0.9, lightness * modifiers.lightness)
  const finalSaturation = Math.min(1.0, saturation * modifiers.saturation)

  return hslToRgb(finalHue, finalSaturation, finalLightness)
}

const PREVIOUS_THRESHOLD_MS = 3000

const PLAY_BUTTON_HOVER = PRESETS.hoverPressLarge.whileHover

const PlayerIconButton = memo(function PlayerIconButton({ onClick, onMouseEnter, onMouseLeave, title, children, direction = null, style = {} }) {
  const handlePointerDown = useCallback((e) => {
    if (direction) nudge(e.currentTarget.firstElementChild, direction)
    onClick(e)
  }, [direction, onClick])

  return (
    <button
      onPointerDown={handlePointerDown}
      className="ui-tap ui-hover-lg p-2 rounded-full"
      style={{
        backgroundColor: 'transparent',
        transition: CSS_TRANSITION.theme,
        ...style
      }}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      title={title}
      aria-label={title}
    >
      {children}
    </button>
  )
})

const PLAYER_SAFE_AREA_STYLE = {
  transition: CSS_TRANSITION.themeBorder,
  paddingBottom: 'var(--safe-bottom)',
  paddingLeft: 'var(--safe-left)',
  paddingRight: 'var(--safe-right)'
}

const TrackArtwork = memo(function TrackArtwork({ url, hasArtwork, onClick, sizeClass }) {
  const [layerA, setLayerA] = useState(null)
  const [layerB, setLayerB] = useState(null)
  const [frontLayer, setFrontLayer] = useState('A')
  const previousArtworkUrlRef = useRef(null)
  const containerRef = useRef(null)

  useEffect(() => {
    if (url && url !== previousArtworkUrlRef.current) {
      const newImage = {
        url: url,
        hasArtwork: hasArtwork
      }
      const swapTo = (layer) => {
        setFrontLayer(layer)
        artPop(containerRef.current)
      }

      if (frontLayer === 'A') {
        queueMicrotask(() => setLayerB(newImage))
        setTimeout(() => swapTo('B'), 50)
      } else {
        queueMicrotask(() => setLayerA(newImage))
        setTimeout(() => swapTo('A'), 50)
      }

      previousArtworkUrlRef.current = url
    }
  }, [url, hasArtwork, frontLayer])

  const renderLayer = (layer, isFront) => (
    <div className={`absolute inset-0 transition-opacity duration-theme ${isFront ? 'opacity-100' : 'opacity-0'}`}>
      {layer && (
        <img
          src={layer.url}
          alt="Album art"
          decoding="async"
          className="w-full h-full object-cover"
        />
      )}
    </div>
  )

  const { getBorder } = useDynamicTheme()

  return (
    <div
      ref={containerRef}
      data-player-artwork
      className={`${sizeClass} rounded-lg flex-shrink-0 cursor-pointer hover:opacity-80 active:opacity-80 overflow-hidden relative`}
      style={{
        border: `1px solid ${getBorder(0.2)}`,
        transition: CSS_TRANSITION.themeOpacity
      }}
      onClick={onClick}
    >
      {layerA && renderLayer(layerA, frontLayer === 'A')}
      {layerB && renderLayer(layerB, frontLayer === 'B')}
    </div>
  )
})

const StaticWaveformLayer = memo(function StaticWaveformLayer({ bars, variant }) {
  return (
    <div className="absolute inset-0 flex items-center w-full h-full">
      {bars.map((bar) => {
        const { r, g, b } = bar.color
        const isPlayed = variant === 'played'

        const barColor = `rgba(${r}, ${g}, ${b}, ${isPlayed ? 1.0 : 0.4})`
        const barShadow = isPlayed ? `0 0 4px rgba(${r}, ${g}, ${b}, 0.5)` : 'none'

        return (
          <div
            key={bar.id}
            className="flex-1"
            style={{
              height: `${Math.max(10, bar.height)}%`,
              backgroundColor: barColor,
              boxShadow: barShadow,
            }}
          />
        )
      })}
    </div>
  )
})

export const Player = memo(function Player({ onSeek, onArtworkClick }) {
  const playback = usePlaybackActions()
  const { togglePlay, next, previous, audio } = playback
  const {
    audioFeatures, isCached, engineRef, notificationsMuted, ttsMuted, publishSettings, toastSuccess, toastError,
    isScreenVisible, reportInterfaceState, currentTrack, is_playing, isCrossfading,
  } = useUISelector(state => ({
    audioFeatures: state.audioFeatures,
    isCached: state.isCached,
    engineRef: state.engineRef,
    notificationsMuted: state.notificationsMuted,
    ttsMuted: state.ttsMuted,
    publishSettings: state.publishSettings,
    toastSuccess: state.toastSuccess,
    toastError: state.toastError,
    isScreenVisible: state.isScreenVisible,
    reportInterfaceState: state.reportInterfaceState,
    currentTrack: state.engineState.currentTrack,
    is_playing: state.engineState.is_playing,
    isCrossfading: state.engineState.isCrossfading,
  }))
  const { user, refreshUser } = useAuth()
  const { isPhoneLandscape } = useViewport()
  const compact = isPhoneLandscape
  const controlSizeClass = compact ? 'w-10 h-10' : 'w-10 h-10 md:w-12 md:h-12'
  const { getGradient, getAccentColor, getGrey800, getGrey500, getWhite, getGrey400, getErrorColor } = useDynamicTheme()
  const artworkUrl = useArtwork(currentTrack?.id, currentTrack?.has_artwork)
  const [volume, setVolume] = useState(1)
  const [isMuted, setIsMuted] = useState(false)
  const [bufferedPercent, setBufferedPercent] = useState(0)
  const [isPastRestartThreshold, setIsPastRestartThreshold] = useState(false)
  const progressPercentRef = useRef(0)
  const progressWidthRef = useRef(0)
  const progressResizeObserverRef = useRef(null)
  const progressNodesRef = useRef({ window: null, content: null, playhead: null, bar: null })
  const writeProgressStyles = useCallback(() => {
    const nodes = progressNodesRef.current
    for (let i = 0; i < PROGRESS_NODE_KEYS.length; i++) {
      const key = PROGRESS_NODE_KEYS[i]
      if (nodes[key]) PROGRESS_STYLE_WRITERS[key](nodes[key], progressPercentRef.current, progressWidthRef.current)
    }
  }, [])
  const progressRefCallbacks = useMemo(() => {
    const callbacks = {}
    PROGRESS_NODE_KEYS.forEach(key => {
      callbacks[key] = (node) => {
        progressNodesRef.current[key] = node
        if (node) PROGRESS_STYLE_WRITERS[key](node, progressPercentRef.current, progressWidthRef.current)
        if (key !== 'window') return
        progressResizeObserverRef.current?.disconnect()
        progressResizeObserverRef.current = null
        if (!node) {
          progressWidthRef.current = 0
          return
        }
        if (typeof ResizeObserver === 'undefined') return
        const observer = new ResizeObserver((entries) => {
          const width = entries[entries.length - 1].contentRect.width
          if (width === progressWidthRef.current) return
          progressWidthRef.current = width
          writeProgressStyles()
        })
        observer.observe(node)
        progressResizeObserverRef.current = observer
      }
    })
    return callbacks
  }, [writeProgressStyles])
  const progressTimeRef = useRef(null)
  const progressMsRef = useRef(0)

  const devicePicker = useDevicePicker()
  const bitratePicker = useBitratePicker(playback.reloadCurrentTrackQuality)
  const playerRef = useRef(null)

  useEffect(() => {
    if (!playerRef.current) return

    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const borderBox = entry.borderBoxSize?.[0]
        reportInterfaceState({ playerHeight: borderBox ? borderBox.blockSize : entry.target.offsetHeight })
      }
    })

    resizeObserver.observe(playerRef.current)
    reportInterfaceState({ playerHeight: playerRef.current.offsetHeight })

    return () => {
      resizeObserver.disconnect()
    }
  }, [reportInterfaceState])

  const durationMs = currentTrack?.duration_ms || currentTrack?.track_info?.duration || 0
  const actualDuration = audioFeatures?.duration ? audioFeatures.duration * 1000 : durationMs

  useEffect(() => {
    if (!isScreenVisible) return

    const updateProgress = () => {
      const progress = engineRef.current.progress_ms || 0
      const duration = currentTrack?.duration_ms || 0
      const percent = duration > 0 ? Math.min(100, Math.max(0, (progress / duration) * 100)) : 0
      const displayMs = actualDuration ? Math.floor((percent / 100) * actualDuration) : 0
      progressMsRef.current = displayMs

      if (percent !== progressPercentRef.current) {
        progressPercentRef.current = percent
        writeProgressStyles()
      }
      const timeNode = progressTimeRef.current
      if (timeNode) {
        const text = formatDuration(displayMs)
        if (timeNode.textContent !== text) timeNode.textContent = text
      }
      setIsPastRestartThreshold(displayMs >= PREVIOUS_THRESHOLD_MS)
    }

    updateProgress()
    const intervalId = setInterval(updateProgress, 100)

    return () => clearInterval(intervalId)
  }, [engineRef, currentTrack, isScreenVisible, actualDuration, writeProgressStyles])

  const handleSeek = useCallback((e) => {
    if (!durationMs) return
    const rect = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - rect.left
    const percent = Math.max(0, Math.min(1, x / rect.width))
    const newTime = Math.floor(percent * durationMs)
    onSeek(newTime)
  }, [durationMs, onSeek])

  const [isDraggingVolume, setIsDraggingVolume] = useState(false)

  const updateVolume = useCallback((clientX, rect) => {
    const x = clientX - rect.left
    const percent = Math.max(0, Math.min(1, x / rect.width))
    setVolume(percent)
    if (audio?.setVolume) {
      audio.setVolume(percent)
    }
    if (percent > 0) setIsMuted(false)
  }, [audio])

  const handleVolumeMouseDown = useCallback((e) => {
    setIsDraggingVolume(true)
    const rect = e.currentTarget.getBoundingClientRect()
    updateVolume(e.clientX, rect)

    const handleMouseMove = (moveEvent) => {
      updateVolume(moveEvent.clientX, rect)
    }

    const handleMouseUp = () => {
      setIsDraggingVolume(false)
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }

    document.addEventListener('mousemove', handleMouseMove, { passive: true })
    document.addEventListener('mouseup', handleMouseUp)
  }, [updateVolume])

  const handlePrevious = useCallback((e) => {
    e?.preventDefault()
    triggerHaptic('double')
    previous()
  }, [previous])

  const handleNext = useCallback((e) => {
    e?.preventDefault()
    triggerHaptic('double')
    next()
  }, [next])

  const handleTogglePlay = useCallback((e) => {
    e?.preventDefault()
    triggerHaptic('strong')
    togglePlay()
  }, [togglePlay])

  const handleMuteToggle = useCallback((e) => {
    e?.preventDefault()
    triggerHaptic('medium')
    if (audio?.setMuted) {
      const newMuted = !isMuted
      setIsMuted(newMuted)
      audio.setMuted(newMuted)
    }
  }, [audio, isMuted])

  const handleToggleSounds = useCallback(async (e) => {
    e?.preventDefault()
    triggerHaptic('medium')
    try {
      let newTtsMuted, newNotificationsMuted, message

      if (!ttsMuted && !notificationsMuted) {
        newTtsMuted = true
        newNotificationsMuted = false
        message = 'PING only'
      } else if (ttsMuted && !notificationsMuted) {
        newTtsMuted = true
        newNotificationsMuted = true
        message = 'OFF'
      } else {
        newTtsMuted = false
        newNotificationsMuted = false
        message = 'DJ + PING'
      }

      publishSettings({ ttsMuted: newTtsMuted, notificationsMuted: newNotificationsMuted })

      if (!user) {
        saveGuestSettings({ ttsMuted: newTtsMuted, notificationsMuted: newNotificationsMuted })
        toastSuccess(message)
        return
      }

      await api.updateUserProfile({
        tts_muted: newTtsMuted,
        notifications_muted: newNotificationsMuted
      })
      if (refreshUser) await refreshUser()
      toastSuccess(message)
    } catch {
      publishSettings({ ttsMuted: user?.tts_muted ?? false, notificationsMuted: user?.notifications_muted ?? false })
      toastError('Failed to toggle sounds')
    }
  }, [ttsMuted, notificationsMuted, publishSettings, user, refreshUser, toastSuccess, toastError])

  const soundState = useMemo(() => {
    if (!ttsMuted && !notificationsMuted) {
      return { icon: 'volume', title: 'DJ + PING (click for PING only)', color: false }
    } else if (ttsMuted && !notificationsMuted) {
      return { icon: 'bell', title: 'PING only (click for OFF)', color: 'warning' }
    } else {
      return { icon: 'muted', title: 'OFF (click for DJ + PING)', color: 'error' }
    }
  }, [ttsMuted, notificationsMuted])

  const handleMouseEnterButton = useCallback((e) => {
    e.currentTarget.style.backgroundColor = getGrey800()
  }, [getGrey800])

  const handleMouseLeaveButton = useCallback((e) => {
    e.currentTarget.style.backgroundColor = 'transparent'
  }, [])


  useEffect(() => {
    if (!isScreenVisible) return

    const updateBuffered = () => {
      const element = audio?.getCurrentElement?.()

      if (!element || !element.src || !currentTrack || actualDuration <= 0) {
        setBufferedPercent(0)
        return
      }

      if (element.buffered && element.buffered.length > 0) {
        try {
          const bufferedEnd = element.buffered.end(element.buffered.length - 1)
          const percent = Math.min(100, (bufferedEnd / (actualDuration / 1000)) * 100)
          setBufferedPercent(percent)
        } catch {
          setBufferedPercent(0)
        }
      } else {
        setBufferedPercent(0)
      }
    }

    const interval = setInterval(updateBuffered, 1000)
    updateBuffered()
    return () => clearInterval(interval)
  }, [audio, actualDuration, currentTrack?.id, isScreenVisible])

  const waveformBars = useMemo(() => {
    if (!audioFeatures?.loudness_segments || !durationMs) return []

    const segments = audioFeatures.loudness_segments
    const sections = audioFeatures.sections || []
    const maxLoudness = audioFeatures.peak_loudness || -1
    const minLoudness = audioFeatures.min_loudness || -80
    const loudnessRange = maxLoudness - minLoudness
    const sampleRate = Math.max(1, Math.floor(segments.length / 200))

    const trackEnergy = audioFeatures.energy || 0.5
    const trackValence = audioFeatures.valence || 0.5

    let sectionIndex = 0

    return segments
      .filter((_, i) => i % sampleRate === 0)
      .map((segment, idx) => {
        while (
          sectionIndex < sections.length - 1 &&
          segment.start >= sections[sectionIndex + 1].start
        ) {
          sectionIndex++
        }
        const currentSection = sections[sectionIndex]
        const sectionType = (currentSection && segment.start < (currentSection.start + currentSection.duration))
          ? currentSection.type
          : 'verse'

        const normalized = (segment.loudness - minLoudness) / loudnessRange
        const height = Math.pow(Math.max(0, Math.min(1, normalized)), 2.0)
        const color = getDynamicSegmentColor(normalized, trackEnergy, trackValence, sectionType)

        return {
          id: idx,
          height: height * 100,
          loudness: segment.loudness,
          color: color
        }
      })
  }, [audioFeatures, durationMs])

  const trackInfo = currentTrack ? (
    <>
      <TrackArtwork
        url={artworkUrl}
        hasArtwork={currentTrack.has_artwork}
        onClick={onArtworkClick}
        sizeClass={controlSizeClass}
      />
      <button
        onClick={handleToggleSounds}
        className={`ui-tap ui-hover relative rounded-lg cursor-pointer flex flex-shrink-0 items-center justify-center ${controlSizeClass}`}
        style={{
          background: getGradient(0.2),
          border: `1px solid ${soundState.color === 'error' ? getErrorColor() : soundState.color === 'warning' ? '#f59e0b' : getAccentColor(0.3)}`,
          transition: CSS_TRANSITION.theme,
          color: soundState.color === 'error' ? getErrorColor() : soundState.color === 'warning' ? '#f59e0b' : getAccentColor(0.9)
        }}
        title={soundState.title}
        aria-label={soundState.title}
      >
        {soundState.icon === 'volume' && <Volume2 size={16} className={compact ? undefined : 'md:hidden'} />}
        {soundState.icon === 'bell' && <Bell size={16} className={compact ? undefined : 'md:hidden'} />}
        {soundState.icon === 'muted' && <VolumeX size={16} className={compact ? undefined : 'md:hidden'} />}
        {!compact && soundState.icon === 'volume' && <Volume2 size={20} className="hidden md:block" />}
        {!compact && soundState.icon === 'bell' && <Bell size={20} className="hidden md:block" />}
        {!compact && soundState.icon === 'muted' && <VolumeX size={20} className="hidden md:block" />}
      </button>
      <div className={`${compact ? 'flex' : 'hidden md:flex'} flex-1 min-w-0 flex-col`}>
        <div
          className={`font-semibold truncate transition-colors duration-theme ${compact ? 'text-sm' : 'text-base'}`}
          style={{ color: getWhite() }}
        >
          {currentTrack.title || currentTrack.generation_params?.title || 'Unknown'}
        </div>
        <div
          className={`truncate transition-colors duration-theme ${compact ? 'text-xs' : 'text-sm'}`}
          style={{ color: getGrey400() }}
        >
          {currentTrack.generation_params?.style_canonical || currentTrack.generation_params?.style || currentTrack.style || ''}
        </div>
      </div>
    </>
  ) : null

  const transportControls = (
    <div className={`flex items-center justify-center ${compact ? 'gap-1 flex-shrink-0' : 'gap-2'}`}>
      <div>
        <PlayerIconButton
          onClick={handlePrevious}
          onMouseEnter={handleMouseEnterButton}
          onMouseLeave={handleMouseLeaveButton}
          direction="left"
          title={isPastRestartThreshold ? 'Restart track' : 'Previous track'}
        >
          <SkipBack size={20} />
        </PlayerIconButton>
      </div>

      <motion.div
        animate={isCrossfading ? {
          scale: [1, 1.05, 1],
          transition: MOTION.pulse
        } : { scale: 1 }}
        whileHover={PLAY_BUTTON_HOVER}
      >
      <button
        onPointerDown={handleTogglePlay}
        className={`ui-tap ${compact ? 'p-2.5' : 'p-3'} text-black rounded-full shadow-lg`}
        style={{
          background: getAccentColor(1),
          transition: CSS_TRANSITION.themeBackground
        }}
        title={is_playing ? 'Pause' : 'Play'}
        aria-label={is_playing ? 'Pause' : 'Play'}
      >
        <AnimatePresence mode="wait" initial={false}>
          {is_playing ? (
            <motion.div
              key="pause"
              {...PRESETS.iconSwap}
            >
              <Pause size={24} fill="currentColor" />
            </motion.div>
          ) : (
            <motion.div
              key="play"
              {...PRESETS.iconSwap}
            >
              <Play size={24} fill="currentColor" className="ml-0.5" />
            </motion.div>
          )}
        </AnimatePresence>
      </button>
      </motion.div>

      <div>
        <PlayerIconButton
          onClick={handleNext}
          onMouseEnter={handleMouseEnterButton}
          onMouseLeave={handleMouseLeaveButton}
          direction="right"
          title="Next track"
        >
          <SkipForward size={20} />
        </PlayerIconButton>
      </div>
    </div>
  )

  const progressRow = currentTrack ? (
    <div className={`relative flex items-center ${compact ? 'gap-1.5 flex-1 min-w-0' : 'gap-2'}`}>
      <div className="absolute inset-0 z-30 flex items-center justify-center pointer-events-none px-12">
        <OnAirBadge variant="bar" />
      </div>
      <span
        ref={progressTimeRef}
        className="text-xs tabular-nums min-w-[40px] text-center transition-colors duration-theme"
        style={{ color: getGrey400() }}
      >
        {formatDuration(0)}
      </span>
      {audioFeatures ? (
        <div
          className={`flex-1 relative cursor-pointer group overflow-hidden rounded-md bg-gray-900/50 ${compact ? 'h-7' : 'h-8'}`}
          onClick={handleSeek}
        >
          <div
            className="absolute inset-0 bg-yellow-500/20 origin-left"
            style={{ transform: `scaleX(${bufferedPercent / 100})`, transition: CSS_TRANSITION.buffered }}
          />

          <StaticWaveformLayer bars={waveformBars} variant="unplayed" />

          <div ref={progressRefCallbacks.window} className="absolute inset-0 overflow-hidden will-change-transform" style={PLAYED_WINDOW_STYLE}>
            <div ref={progressRefCallbacks.content} className="absolute inset-0 will-change-transform" style={PLAYED_CONTENT_STYLE}>
              <StaticWaveformLayer bars={waveformBars} variant="played" />
            </div>
          </div>

          <div ref={progressRefCallbacks.playhead} className="absolute inset-0 pointer-events-none z-20">
            <div
              className="absolute right-0 top-0 bottom-0 w-0.5 shadow-lg"
              style={{
                backgroundColor: getWhite()
              }}
            />
          </div>
        </div>
      ) : (
        <div
          className="flex-1 h-1 rounded-full cursor-pointer group relative overflow-hidden"
          onClick={handleSeek}
          style={{
            backgroundColor: getGrey800(),
            transition: CSS_TRANSITION.themeBackgroundColor
          }}
        >
          <div
            className="absolute inset-0 rounded-full origin-left"
            style={{
              transform: `scaleX(${bufferedPercent / 100})`,
              backgroundColor: getGrey500(),
              transition: CSS_TRANSITION.themeBackgroundScale
            }}
          />
          <div
            ref={progressRefCallbacks.bar}
            className="absolute inset-0 rounded-full"
            style={{
              background: getGradient(1),
              transition: CSS_TRANSITION.themeBackgroundTransform
            }}
          >
            <div
              className="absolute right-0 top-1/2 -translate-y-1/2 w-3 h-3 rounded-full shadow-lg opacity-0 group-hover:opacity-100 transition"
              style={{
                backgroundColor: getWhite()
              }}
            />
          </div>
        </div>
      )}
      <span
        className="text-xs tabular-nums min-w-[40px] text-center transition-colors duration-theme"
        style={{ color: getGrey400() }}
      >
        {formatDuration(durationMs)}
      </span>
    </div>
  ) : null

  const sideControls = (
    <div className={`flex items-center justify-end min-w-0 ${compact ? 'gap-1.5 flex-shrink-0' : 'gap-1.5 md:gap-3'}`}>
      <div className={`${compact ? 'hidden' : 'hidden md:flex'} items-center gap-2 flex-1 max-w-[120px]`}>
        <PlayerIconButton
          onClick={handleMuteToggle}
          onMouseEnter={handleMouseEnterButton}
          onMouseLeave={handleMouseLeaveButton}
          title={isMuted ? 'Unmute' : 'Mute'}
        >
          {isMuted || volume === 0 ? (
            <VolumeX size={20} />
          ) : (
            <Volume2 size={20} />
          )}
        </PlayerIconButton>

        <div
          className="flex-1 h-1 rounded-full cursor-pointer group relative"
          onMouseDown={handleVolumeMouseDown}
          style={{
            backgroundColor: getGrey800(),
            transition: CSS_TRANSITION.themeBackgroundColor
          }}
        >
          <div
            className="h-full rounded-full relative"
            style={{
              width: `${isMuted ? 0 : volume * 100}%`,
              backgroundColor: getWhite(),
              transition: CSS_TRANSITION.themeBackgroundColor
            }}
          >
            <div
              className={`absolute right-0 top-1/2 -translate-y-1/2 w-3 h-3 rounded-full shadow-lg transition ${
                isDraggingVolume ? 'opacity-100 scale-110' : 'opacity-0 group-hover:opacity-100'
              }`}
              style={{
                backgroundColor: getWhite(),
                transition: CSS_TRANSITION.themeAll
              }}
            />
          </div>
        </div>
      </div>

      <div className="relative">
        <BitratePickerButton
          currentBitrate={bitratePicker.currentBitrate}
          effectiveBitrate={bitratePicker.effectiveBitrate}
          dataSaverMode={bitratePicker.dataSaverMode}
          isOpen={bitratePicker.isOpen}
          setIsOpen={bitratePicker.setIsOpen}
        />
        {isCached && (
          <div
            className="absolute -top-1 -right-1 cursor-help z-10 pointer-events-none"
            title="Playing from local cache"
          >
            <div className="w-4 h-4 rounded-full flex items-center justify-center bg-green-500/90 shadow-lg">
              <HardDrive size={10} className="text-white" />
            </div>
          </div>
        )}
      </div>

      {devicePicker.isAuthenticated && (
        <DevicePickerButton
          {...devicePicker}
        />
      )}
    </div>
  )

  return (
    <>
      <DevicePickerBanner
        showInactive={devicePicker.showInactive}
        bannerDismissed={devicePicker.bannerDismissed}
        actionLoading={devicePicker.actionLoading}
        handleActivateDevice={devicePicker.handleActivateDevice}
        handleDismissBanner={devicePicker.handleDismissBanner}
      />

      <AnimatePresence mode="wait">
        <motion.div
          ref={playerRef}
          data-shader-panel="player"
          initial={{ y: 100 }}
          animate={{ y: 0 }}
          exit={{ y: 100 }}
          className="fixed bottom-0 left-0 right-0 z-50 overflow-hidden"
          style={PLAYER_SAFE_AREA_STYLE}
          onClick={(e) => e.stopPropagation()}
        >

          {devicePicker.isAuthenticated && (
            <DevicePickerPanel
              {...devicePicker}
            />
          )}
          <BitratePickerPanel
            isOpen={bitratePicker.isOpen}
            setIsOpen={bitratePicker.setIsOpen}
            actionLoading={bitratePicker.actionLoading}
            currentBitrate={bitratePicker.currentBitrate}
            effectiveBitrate={bitratePicker.effectiveBitrate}
            dataSaverMode={bitratePicker.dataSaverMode}
            isOnline={bitratePicker.isOnline}
            networkQuality={bitratePicker.networkQuality}
            detectedBitrate={bitratePicker.detectedBitrate}
            detecting={bitratePicker.detecting}
            handleSelectBitrate={bitratePicker.handleSelectBitrate}
            detectQuality={bitratePicker.detectQuality}
            getBitrateIcon={bitratePicker.getBitrateIcon}
            getBitrateLabel={bitratePicker.getBitrateLabel}
            getBitrateDescription={bitratePicker.getBitrateDescription}
            getQualityColor={bitratePicker.getQualityColor}
            isAuthenticated={bitratePicker.isAuthenticated}
          />
          <GenerationQueuePanel />
          <div className={compact ? 'px-3 py-1.5' : 'px-3 py-2 md:px-4 md:py-3'}>
            <div className="max-w-screen-2xl mx-auto">
              {!currentTrack ? (
                <div className="ui-fade-in flex items-center justify-center text-gray-500 py-2">
                  Select a track to start playing
                </div>
              ) : compact ? (
                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-2 min-w-0 w-[30%] max-w-[16rem] flex-shrink-0">
                    {trackInfo}
                  </div>
                  {transportControls}
                  {progressRow}
                  {sideControls}
                </div>
              ) : (
                <div className="flex flex-col gap-1 md:gap-2">
                  {progressRow}

                  <div className="grid grid-cols-3 items-center gap-3 md:gap-4">
                    <div className="flex items-center gap-2 min-w-0">
                      {trackInfo}
                    </div>

                    {transportControls}

                    {sideControls}
                  </div>
                </div>
              )}
            </div>
          </div>
        </motion.div>
      </AnimatePresence>
    </>
  )
})