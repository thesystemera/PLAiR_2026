import { Play, Pause, SkipForward, SkipBack, Volume2, VolumeX, HardDrive, Bell, BellOff} from 'lucide-react'
import { applySoundMode, nextSoundMode, soundModeFor } from '../lib/soundModes'
import { motion, AnimatePresence, frame, cancelFrame } from 'framer-motion'
import { memo, useState, useCallback, useEffect, useLayoutEffect, useRef, useMemo } from 'react'
import { formatDuration } from '../lib/utils'
import { triggerHaptic } from '../lib/haptics'
import { useDynamicTheme } from '../contexts/DynamicThemeContext'
import { useDevicePicker, DevicePickerButton, DeviceNotice, DevicePickerPanel } from './DevicePicker'
import { useBitratePicker, BitratePickerButton, BitratePickerPanel } from './BitratePicker'
import { GenerationQueuePanel } from './GenerationQueuePanel'
import { useUISelector } from '../contexts/UIStateContext'
import { usePlaybackActions } from '../contexts/PlaybackContext'
import { useViewport } from '../contexts/ViewportContext'
import { CSS_TRANSITION, MOTION, PRESETS } from '../lib/motion'
import { artPop, nudge } from '../lib/microMotion'
import { FadeSwap, Pop } from './Motion'
import { ON_AIR_LAMP } from '../lib/themeManager'
import { TrackArtCrossfade } from './DepthArt'
import { playerInset } from '../lib/playerInset'

const SOUND_ICONS = { volume: Volume2, bell: Bell, bellOff: BellOff, muted: VolumeX }

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
const PLAYER_HEIGHT_SETTLE_MS = 140
const PLAYER_STILL_FRAMES = 12

const PLAY_BUTTON_HOVER = PRESETS.hoverPressLarge.whileHover
const SKIP_BACK_ICON = <SkipBack size={20} />
const SKIP_FORWARD_ICON = <SkipForward size={20} />
const PLAYER_HIDDEN = { y: 100 }
const PLAY_BUTTON_PULSE = { scale: [1, 1.05, 1], transition: MOTION.pulse }
const PLAY_BUTTON_REST = { scale: 1 }
const BUFFERED_POLL_MS = 1000
const BUFFERED_WAVE_STYLE = { transform: 'scaleX(0)', transition: CSS_TRANSITION.buffered }
const trackTitle = track => track?.title || track?.generation_params?.title || 'Unknown'
const trackStyle = track => track?.generation_params?.style_canonical || track?.generation_params?.style || track?.style || ''
const PLAYER_SHOWN = { y: 0 }
const stopPropagation = event => event.stopPropagation()

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

const TrackArtwork = memo(function TrackArtwork({ trackId, hasArtwork, onClick, sizeClass }) {
  const containerRef = useRef(null)
  const popArt = useCallback(() => artPop(containerRef.current), [])
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
      <TrackArtCrossfade trackId={trackId} hasArtwork={hasArtwork} alt="Album art" onShow={popArt} />
    </div>
  )
})

const WAVEFORM_MIN_HEIGHT_PERCENT = 10
const WAVEFORM_UNPLAYED_ALPHA = 0.4
const WAVEFORM_GLOW_PX = 4
const WAVEFORM_GLOW_ALPHA = 0.5

function drawWaveform(canvas, bars, played) {
  const dpr = window.devicePixelRatio || 1
  const width = Math.round(canvas.clientWidth * dpr)
  const height = Math.round(canvas.clientHeight * dpr)
  if (width < 1 || height < 1) return
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width
    canvas.height = height
  }
  const context = canvas.getContext('2d')
  context.clearRect(0, 0, width, height)
  context.shadowBlur = played ? WAVEFORM_GLOW_PX * dpr : 0
  for (let i = 0; i < bars.length; i++) {
    const { r, g, b } = bars[i].color
    const left = Math.round(i * width / bars.length)
    const right = Math.round((i + 1) * width / bars.length)
    const barHeight = Math.max(WAVEFORM_MIN_HEIGHT_PERCENT, bars[i].height) / 100 * height
    const top = Math.round((height - barHeight) / 2)
    const bottom = Math.round((height + barHeight) / 2)
    if (played) context.shadowColor = `rgba(${r}, ${g}, ${b}, ${WAVEFORM_GLOW_ALPHA})`
    context.fillStyle = `rgba(${r}, ${g}, ${b}, ${played ? 1 : WAVEFORM_UNPLAYED_ALPHA})`
    context.fillRect(left, top, right - left, bottom - top)
  }
}

const WaveformLayer = memo(function WaveformLayer({ bars, variant }) {
  const canvasRef = useRef(null)

  useLayoutEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return undefined
    const observer = new ResizeObserver(() => drawWaveform(canvas, bars, variant === 'played'))
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [bars, variant])

  return <canvas ref={canvasRef} className="absolute inset-0 w-full h-full" />
})

const PlayButton = memo(function PlayButton({ compact, onToggle }) {
  const { isPlaying, isCrossfading } = useUISelector(state => ({
    isPlaying: state.engineState.is_playing,
    isCrossfading: state.engineState.isCrossfading,
  }))
  const { getAccentColor } = useDynamicTheme()

  return (
    <motion.div animate={isCrossfading ? PLAY_BUTTON_PULSE : PLAY_BUTTON_REST} whileHover={PLAY_BUTTON_HOVER}>
      <button
        onPointerDown={onToggle}
        className={`ui-tap ${compact ? 'p-2.5' : 'p-3'} text-black rounded-full shadow-lg`}
        style={{
          background: getAccentColor(1),
          transition: CSS_TRANSITION.themeBackground
        }}
        title={isPlaying ? 'Pause' : 'Play'}
        aria-label={isPlaying ? 'Pause' : 'Play'}
      >
        <AnimatePresence mode="wait" initial={false}>
          {isPlaying ? (
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
  )
})

const BufferedFill = memo(function BufferedFill({ audio, trackId, durationMs, active, className, style }) {
  const fillRef = useRef(null)

  useLayoutEffect(() => {
    if (!active) return
    const show = (percent) => {
      if (fillRef.current) fillRef.current.style.transform = `scaleX(${percent / 100})`
    }
    const update = () => {
      const element = audio?.getCurrentElement?.()
      if (!element || !element.src || !trackId || durationMs <= 0 || !element.buffered?.length) {
        show(0)
        return
      }
      try {
        show(Math.min(100, (element.buffered.end(element.buffered.length - 1) / (durationMs / 1000)) * 100))
      } catch {
        show(0)
      }
    }
    const interval = setInterval(update, BUFFERED_POLL_MS)
    update()
    return () => clearInterval(interval)
  }, [audio, trackId, durationMs, active])

  return <div ref={fillRef} className={className} style={style} />
})

export const Player = memo(function Player({ onSeek, onArtworkClick }) {
  const playback = usePlaybackActions()
  const { togglePlay, next, previous, audio, talkBreakProgress } = playback
  const {
    audioFeatures: latestFeatures, trackDataFor, isCached, engineRef, notificationsMuted, ttsMuted, publishSettings, toastSuccess,
    isScreenVisible, reportInterfaceState, hasTrack, trackId, hasArtwork, titleText, styleText, trackDurationMs, infoDurationMs,
    talkBreak, volume, isMuted,
  } = useUISelector(state => ({
    audioFeatures: state.audioFeatures,
    trackDataFor: state.trackDataFor,
    isCached: state.audioState.isCached,
    volume: state.audioState.volume,
    isMuted: state.audioState.muted,
    engineRef: state.engineRef,
    notificationsMuted: state.settingsState.notificationsMuted,
    ttsMuted: state.settingsState.ttsMuted,
    publishSettings: state.publishSettings,
    toastSuccess: state.toastSuccess,
    isScreenVisible: state.isScreenVisible,
    reportInterfaceState: state.reportInterfaceState,
    hasTrack: !!state.engineState.currentTrack,
    trackId: state.engineState.currentTrack?.id,
    hasArtwork: state.engineState.currentTrack?.has_artwork,
    titleText: trackTitle(state.engineState.currentTrack),
    styleText: trackStyle(state.engineState.currentTrack),
    trackDurationMs: state.engineState.currentTrack?.duration_ms || 0,
    infoDurationMs: state.engineState.currentTrack?.track_info?.duration || 0,
    talkBreak: state.engineState.talkBreak,
  }))
  const onAir = !!talkBreak
  const audioFeatures = hasTrack && trackDataFor === trackId ? latestFeatures : null
  const { isPhoneLandscape } = useViewport()
  const compact = isPhoneLandscape
  const controlSizeClass = compact ? 'w-10 h-10' : 'w-10 h-10 md:w-12 md:h-12'
  const { getGradient, getAccentColor, getGrey800, getGrey500, getWhite, getGrey400, getErrorColor } = useDynamicTheme()
  const bufferedLineStyle = useMemo(() => ({
    transform: 'scaleX(0)',
    backgroundColor: getGrey500(),
    transition: CSS_TRANSITION.themeBackgroundScale
  }), [getGrey500])
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
    const player = playerRef.current
    if (!player) return

    let settleTimer = null
    let following = false
    let stillFrames = 0
    const measure = () => player.getBoundingClientRect().height
    const follow = () => {
      const height = measure()
      if (height !== playerInset.height()) {
        stillFrames = 0
        playerInset.set(height)
        clearTimeout(settleTimer)
        settleTimer = setTimeout(() => reportInterfaceState({ playerHeight: height }), PLAYER_HEIGHT_SETTLE_MS)
      } else if (++stillFrames >= PLAYER_STILL_FRAMES) {
        following = false
        cancelFrame(follow)
      }
    }
    const resizeObserver = new ResizeObserver(() => {
      if (following) return
      following = true
      stillFrames = 0
      frame.postRender(follow, true)
    })

    resizeObserver.observe(player)
    playerInset.set(measure())
    reportInterfaceState({ playerHeight: playerInset.height() })

    return () => {
      clearTimeout(settleTimer)
      cancelFrame(follow)
      resizeObserver.disconnect()
    }
  }, [reportInterfaceState])

  const durationMs = trackDurationMs || infoDurationMs
  const actualDuration = audioFeatures?.duration ? audioFeatures.duration * 1000 : durationMs

  useEffect(() => {
    if (!isScreenVisible) return

    const updateBreakProgress = () => {
      const progress = talkBreakProgress()
      const percent = progress?.duration > 0 ? Math.min(100, Math.max(0, (progress.elapsed / progress.duration) * 100)) : 0
      if (percent !== progressPercentRef.current) {
        progressPercentRef.current = percent
        writeProgressStyles()
      }
      const timeNode = progressTimeRef.current
      if (timeNode) {
        const text = formatDuration(Math.floor((progress?.elapsed || 0) * 1000))
        if (timeNode.textContent !== text) timeNode.textContent = text
      }
    }

    const updateProgress = () => {
      if (onAir) {
        updateBreakProgress()
        return
      }
      const progress = engineRef.current.progress_ms || 0
      const duration = trackDurationMs
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
  }, [engineRef, trackId, trackDurationMs, isScreenVisible, actualDuration, writeProgressStyles, onAir, talkBreakProgress])

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
    audio?.setVolume?.(percent)
    if (percent > 0 && isMuted) audio?.setMuted?.(false)
  }, [audio, isMuted])

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
    audio?.setMuted?.(!isMuted)
  }, [audio, isMuted])

  const handleToggleSounds = useCallback((e) => {
    e?.preventDefault()
    triggerHaptic('medium')
    const mode = nextSoundMode(soundModeFor(ttsMuted, notificationsMuted))
    applySoundMode(mode, publishSettings)
    toastSuccess(mode.label, 1500)
  }, [ttsMuted, notificationsMuted, publishSettings, toastSuccess])

  const soundState = useMemo(() => {
    const mode = soundModeFor(ttsMuted, notificationsMuted)
    const title = `${mode.label} (tap for ${nextSoundMode(mode).label})`
    if (mode.id === 'both') return { icon: 'volume', title, color: false }
    if (mode.id === 'dj') return { icon: 'bellOff', title, color: 'info' }
    if (mode.id === 'ping') return { icon: 'bell', title, color: 'warning' }
    return { icon: 'muted', title, color: 'error' }
  }, [ttsMuted, notificationsMuted])

  const handleMouseEnterButton = useCallback((e) => {
    e.currentTarget.style.backgroundColor = getGrey800()
  }, [getGrey800])

  const handleMouseLeaveButton = useCallback((e) => {
    e.currentTarget.style.backgroundColor = 'transparent'
  }, [])


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

  const trackInfo = useMemo(() => {
    if (!hasTrack) return null
    const SoundIcon = SOUND_ICONS[soundState.icon]
    return (
    <>
      <TrackArtwork
        trackId={trackId}
        hasArtwork={hasArtwork}
        onClick={onArtworkClick}
        sizeClass={controlSizeClass}
      />
      <button
        onClick={handleToggleSounds}
        className={`ui-tap ui-hover relative rounded-lg cursor-pointer flex flex-shrink-0 items-center justify-center ${controlSizeClass}`}
        style={{
          background: getGradient(0.2),
          border: `1px solid ${soundState.color === 'error' ? getErrorColor() : soundState.color === 'warning' ? '#f59e0b' : soundState.color === 'info' ? '#38bdf8' : getAccentColor(0.3)}`,
          transition: CSS_TRANSITION.theme,
          color: soundState.color === 'error' ? getErrorColor() : soundState.color === 'warning' ? '#f59e0b' : soundState.color === 'info' ? '#38bdf8' : getAccentColor(0.9)
        }}
        title={soundState.title}
        aria-label={soundState.title}
      >
        <FadeSwap swapKey={soundState.icon} preset={PRESETS.iconSwap} mode="wait" className="flex items-center justify-center">
          <SoundIcon size={16} className={compact ? undefined : 'md:hidden'} />
          {!compact && <SoundIcon size={20} className="hidden md:block" />}
        </FadeSwap>
      </button>
      <FadeSwap swapKey={trackId} preset={PRESETS.trackSwap} className={`${compact ? 'flex' : 'hidden md:flex'} flex-1 min-w-0 flex-col`}>
        <div
          className={`font-semibold truncate transition-colors duration-theme ${compact ? 'text-sm' : 'text-base'}`}
          style={{ color: getWhite() }}
        >
          {titleText}
        </div>
        <div
          className={`truncate transition-colors duration-theme ${compact ? 'text-xs' : 'text-sm'}`}
          style={{ color: getGrey400() }}
        >
          {styleText}
        </div>
      </FadeSwap>
    </>
    )
  }, [
    hasTrack, trackId, hasArtwork, titleText, styleText, onArtworkClick, controlSizeClass, compact, handleToggleSounds, soundState,
    getGradient, getErrorColor, getAccentColor, getWhite, getGrey400,
  ])

  const transportControls = useMemo(() => (
    <div className={`flex items-center justify-center ${compact ? 'gap-1 flex-shrink-0' : 'gap-2'}`}>
      <div>
        <PlayerIconButton
          onClick={handlePrevious}
          onMouseEnter={handleMouseEnterButton}
          onMouseLeave={handleMouseLeaveButton}
          direction="left"
          title={isPastRestartThreshold ? 'Restart track' : 'Previous track'}
        >
          {SKIP_BACK_ICON}
        </PlayerIconButton>
      </div>

      <PlayButton compact={compact} onToggle={handleTogglePlay} />

      <div>
        <PlayerIconButton
          onClick={handleNext}
          onMouseEnter={handleMouseEnterButton}
          onMouseLeave={handleMouseLeaveButton}
          direction="right"
          title="Next track"
        >
          {SKIP_FORWARD_ICON}
        </PlayerIconButton>
      </div>
    </div>
  ), [compact, handlePrevious, handleNext, handleTogglePlay, handleMouseEnterButton, handleMouseLeaveButton, isPastRestartThreshold])

  const progressRow = hasTrack ? (
    <div className={`relative flex items-center ${compact ? 'gap-1.5 flex-1 min-w-0' : 'gap-2'}`}>
      <span
        ref={progressTimeRef}
        className="text-xs tabular-nums min-w-[40px] text-center transition-colors duration-theme"
        style={{ color: getGrey400() }}
      >
        {formatDuration(0)}
      </span>
      {onAir ? (
        <div
          className={`ui-track-in flex-1 relative overflow-hidden rounded-md bg-gray-900/50 ${compact ? 'h-7' : 'h-8'}`}
          title={talkBreak.title || talkBreak.label}
        >
          <div
            ref={progressRefCallbacks.bar}
            className="absolute inset-0 will-change-transform"
            style={{
              background: `linear-gradient(90deg, ${ON_AIR_LAMP}33, ${ON_AIR_LAMP}aa)`,
              opacity: talkBreak.paused ? 0.5 : 1,
              transition: CSS_TRANSITION.progress
            }}
          >
            <div className="absolute right-0 top-0 bottom-0 w-0.5" style={{ backgroundColor: getWhite() }} />
          </div>
          <div
            className="absolute inset-0 flex items-center justify-center gap-1.5 px-2 pointer-events-none text-xs font-semibold tracking-wide"
            style={{ color: getWhite() }}
          >
            <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ backgroundColor: ON_AIR_LAMP }} />
            <span className="truncate">{talkBreak.title || talkBreak.label}</span>
          </div>
        </div>
      ) : audioFeatures ? (
        <div
          key={trackId}
          className={`ui-track-in flex-1 relative cursor-pointer group overflow-hidden rounded-md bg-gray-900/50 ${compact ? 'h-7' : 'h-8'}`}
          onClick={handleSeek}
        >
          <BufferedFill
            audio={audio}
            trackId={trackId}
            durationMs={actualDuration}
            active={isScreenVisible}
            className="absolute inset-0 bg-yellow-500/20 origin-left"
            style={BUFFERED_WAVE_STYLE}
          />

          <WaveformLayer bars={waveformBars} variant="unplayed" />

          <div ref={progressRefCallbacks.window} className="absolute inset-0 overflow-hidden will-change-transform" style={PLAYED_WINDOW_STYLE}>
            <div ref={progressRefCallbacks.content} className="absolute inset-0 will-change-transform" style={PLAYED_CONTENT_STYLE}>
              <WaveformLayer bars={waveformBars} variant="played" />
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
        <div className={`ui-track-in flex-1 flex items-center ${compact ? 'h-7' : 'h-8'}`}>
        <div
          className="flex-1 h-1 rounded-full cursor-pointer group relative overflow-hidden"
          onClick={handleSeek}
          style={{
            backgroundColor: getGrey800(),
            transition: CSS_TRANSITION.themeBackgroundColor
          }}
        >
          <BufferedFill
            audio={audio}
            trackId={trackId}
            durationMs={actualDuration}
            active={isScreenVisible}
            className="absolute inset-0 rounded-full origin-left"
            style={bufferedLineStyle}
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
        </div>
      )}
      <span
        className="text-xs tabular-nums min-w-[40px] text-center transition-colors duration-theme"
        style={{ color: getGrey400() }}
      >
        {onAir ? (talkBreak.durationS ? formatDuration(Math.round(talkBreak.durationS * 1000)) : '–') : formatDuration(durationMs)}
      </span>
    </div>
  ) : null

  const sideControls = useMemo(() => (
    <div className={`flex items-center justify-end min-w-0 ${compact ? 'gap-1.5 flex-shrink-0' : 'gap-1.5 md:gap-3'}`}>
      <div className={`${compact ? 'hidden' : 'hidden md:flex'} items-center gap-2 flex-1 max-w-[120px]`}>
        <PlayerIconButton
          onClick={handleMuteToggle}
          onMouseEnter={handleMouseEnterButton}
          onMouseLeave={handleMouseLeaveButton}
          title={isMuted ? 'Unmute' : 'Mute'}
        >
          <FadeSwap swapKey={isMuted || volume === 0 ? 'muted' : 'volume'} preset={PRESETS.iconSwap} mode="wait">
            {isMuted || volume === 0 ? (
              <VolumeX size={20} />
            ) : (
              <Volume2 size={20} />
            )}
          </FadeSwap>
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
        <Pop
          show={!!isCached}
          as="div"
          className="absolute -top-1 -right-1 cursor-help z-10 pointer-events-none"
          title="Playing from local cache"
        >
          <div className="w-4 h-4 rounded-full flex items-center justify-center bg-green-500/90 shadow-lg">
            <HardDrive size={10} className="text-white" />
          </div>
        </Pop>
      </div>

      {devicePicker.isAuthenticated && (
        <DevicePickerButton
          {...devicePicker}
        />
      )}
    </div>
  ), [
    compact, handleMuteToggle, handleMouseEnterButton, handleMouseLeaveButton, isMuted, volume, handleVolumeMouseDown, getGrey800, getWhite,
    isDraggingVolume, bitratePicker.currentBitrate, bitratePicker.effectiveBitrate, bitratePicker.dataSaverMode, bitratePicker.isOpen,
    bitratePicker.setIsOpen, isCached, devicePicker,
  ])

  return (
    <>
      <DeviceNotice
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
          initial={PLAYER_HIDDEN}
          animate={PLAYER_SHOWN}
          exit={PLAYER_HIDDEN}
          className="fixed bottom-0 left-0 right-0 z-50 overflow-hidden"
          style={PLAYER_SAFE_AREA_STYLE}
          onClick={stopPropagation}
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
          <div className={`will-change-transform ${compact ? 'px-3 py-1.5' : 'px-3 py-2 md:px-4 md:py-3'}`}>
            <div className="max-w-screen-2xl mx-auto">
              {!hasTrack ? (
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