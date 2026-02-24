import { Play, Pause, SkipForward, SkipBack, Volume2, VolumeX, HardDrive, Bell} from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { memo, useState, useCallback, useEffect, useRef, useMemo } from 'react'
import { formatDuration } from '../lib/utils'
import { triggerHaptic } from '../lib/haptics'
import { useDynamicTheme } from '../contexts/DynamicThemeContext'
import { useDevicePicker, DevicePickerButton, DevicePickerBanner, DevicePickerPanel } from './DevicePicker'
import { useBitratePicker, BitratePickerButton, BitratePickerPanel } from './BitratePicker'
import { GenerationQueuePanel } from './GenerationQueuePanel'
import { useArtwork, useUIState } from '../contexts/UIStateContext'
import { usePlayback } from '../contexts/PlaybackContext'
import { useAuth } from '../contexts/AuthContext'
import { api } from '../lib/api'

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

const PlayerIconButton = memo(function PlayerIconButton({ onClick, onMouseEnter, onMouseLeave, title, children, style = {} }) {
  return (
    <button
      onPointerDown={onClick}
      className="p-2 rounded-full transition-all hover:scale-110"
      style={{
        backgroundColor: 'transparent',
        transition: 'all 700ms ease-in-out, transform 150ms ease-in-out',
        ...style
      }}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      title={title}
    >
      {children}
    </button>
  )
})

const TrackArtwork = memo(function TrackArtwork({ url, hasArtwork, onClick }) {
  const [layerA, setLayerA] = useState(null)
  const [layerB, setLayerB] = useState(null)
  const [frontLayer, setFrontLayer] = useState('A')
  const previousArtworkUrlRef = useRef(null)

  useEffect(() => {
    if (url && url !== previousArtworkUrlRef.current) {
      const newImage = {
        url: url,
        hasArtwork: hasArtwork
      }

      if (frontLayer === 'A') {
        queueMicrotask(() => setLayerB(newImage))
        setTimeout(() => setFrontLayer('B'), 50)
      } else {
        queueMicrotask(() => setLayerA(newImage))
        setTimeout(() => setFrontLayer('A'), 50)
      }

      previousArtworkUrlRef.current = url
    }
  }, [url, hasArtwork, frontLayer])

  const renderLayer = (layer, isFront) => (
    <div className={`absolute inset-0 transition-opacity duration-700 ${isFront ? 'opacity-100' : 'opacity-0'}`}>
      {layer && (
        <img
          src={layer.url}
          alt="Album art"
          className="w-full h-full object-cover"
        />
      )}
    </div>
  )

  const { getBorder } = useDynamicTheme()

  return (
    <div
      className="w-10 h-10 md:w-12 md:h-12 rounded-lg flex-shrink-0 cursor-pointer hover:opacity-80 active:opacity-80 overflow-hidden relative"
      style={{
        border: `1px solid ${getBorder(0.2)}`,
        transition: 'all 700ms ease-in-out, opacity 150ms ease-in-out'
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

export const Player = memo(function Player({ onSeek, onHeightChange, onArtworkClick }) {
  const playback = usePlayback()
  const { togglePlay, next, previous, audio } = playback
  const { audioFeatures, audioState, engineState, engineRef, settingsState, publishSettings, toastSuccess, toastError, isScreenVisible } = useUIState()
  const { currentTrack, is_playing, isCrossfading } = engineState
  const { user, refreshUser } = useAuth()
  const { getGradient, getAccentColor, getGrey800, getGrey500, getBorder, getWhite, getGrey400, getErrorColor } = useDynamicTheme()
  const artworkUrl = useArtwork(currentTrack?.id, currentTrack?.has_artwork)
  const [volume, setVolume] = useState(1)
  const [isMuted, setIsMuted] = useState(false)
  const [bufferedPercent, setBufferedPercent] = useState(0)
  const [progressPercent, setProgressPercent] = useState(0)

  const devicePicker = useDevicePicker()
  const bitratePicker = useBitratePicker(playback.reloadCurrentTrackQuality)
  const playerRef = useRef(null)

  useEffect(() => {
    if (!playerRef.current || !onHeightChange) return

    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const height = entry.contentRect.height
        onHeightChange(height)
      }
    })

    resizeObserver.observe(playerRef.current)
    onHeightChange(playerRef.current.offsetHeight)

    return () => {
      resizeObserver.disconnect()
    }
  }, [onHeightChange])

  // Progress updates at 10fps - matches 100ms CSS transition
  useEffect(() => {
    if (!isScreenVisible) return

    const updateProgress = () => {
      const progress = engineRef.current.progress_ms || 0
      const duration = currentTrack?.duration_ms || 0
      const latestProgress = duration > 0 ? Math.min(100, Math.max(0, (progress / duration) * 100)) : 0
      setProgressPercent(latestProgress)
    }

    updateProgress()
    const intervalId = setInterval(updateProgress, 100)

    return () => clearInterval(intervalId)
  }, [engineRef, currentTrack, isScreenVisible])

  const durationMs = currentTrack?.duration_ms || currentTrack?.track_info?.duration || 0
  const actualDuration = audioFeatures?.duration ? audioFeatures.duration * 1000 : durationMs
  const progress_ms = actualDuration ? Math.floor((progressPercent / 100) * actualDuration) : 0
  const PREVIOUS_THRESHOLD_MS = 3000

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

    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
  }, [updateVolume])

  const handlePrevious = useCallback((e) => {
    e?.preventDefault()
    triggerHaptic('double')
    if (progress_ms >= PREVIOUS_THRESHOLD_MS) {
      onSeek(0)
    } else {
      previous()
    }
  }, [progress_ms, onSeek, previous])

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
      const ttsMuted = settingsState.ttsMuted
      const notificationsMuted = settingsState.notificationsMuted

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

      await api.updateUserProfile({
        tts_muted: newTtsMuted,
        notifications_muted: newNotificationsMuted
      })
      if (refreshUser) await refreshUser()
      toastSuccess(message)
    } catch (err) {
      publishSettings({ ttsMuted: user?.tts_muted ?? false, notificationsMuted: user?.notifications_muted ?? false })
      toastError('Failed to toggle sounds')
    }
  }, [settingsState, publishSettings, user, refreshUser, toastSuccess, toastError])

  const soundState = useMemo(() => {
    const ttsMuted = settingsState.ttsMuted
    const notificationsMuted = settingsState.notificationsMuted

    if (!ttsMuted && !notificationsMuted) {
      return { icon: 'volume', title: 'DJ + PING (click for PING only)', color: false }
    } else if (ttsMuted && !notificationsMuted) {
      return { icon: 'bell', title: 'PING only (click for OFF)', color: 'warning' }
    } else {
      return { icon: 'muted', title: 'OFF (click for DJ + PING)', color: 'error' }
    }
  }, [settingsState])

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
        } catch (error) {
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
          style={{
            transition: 'border-color 700ms ease-in-out'
          }}
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
          <div className="px-3 py-2 md:px-4 md:py-3">
            <div className="max-w-screen-2xl mx-auto">
              {!currentTrack ? (
                <div className="flex items-center justify-center text-gray-500 py-2">
                  Select a track to start playing
                </div>
              ) : (
                <div className="flex flex-col gap-1 md:gap-2">
                  <div className="flex items-center gap-2">
                    <span
                      className="text-xs tabular-nums min-w-[40px] text-center transition-colors duration-700"
                      style={{ color: getGrey400() }}
                    >
                      {formatDuration(progress_ms)}
                    </span>
                    {audioFeatures ? (
                      <div
                        className="flex-1 h-8 relative cursor-pointer group overflow-hidden rounded-md bg-gray-900/50"
                        onClick={handleSeek}
                      >
                        <div
                          className="absolute inset-0 bg-yellow-500/20"
                          style={{ width: `${bufferedPercent}%`, transition: 'width 0.3s ease' }}
                        />

                        <StaticWaveformLayer bars={waveformBars} variant="unplayed" />

                        <div
                          className="absolute inset-0 will-change-[clip-path]"
                          style={{
                            clipPath: `inset(0 ${100 - progressPercent}% 0 0)`,
                            transition: 'clip-path 0.1s linear'
                          }}
                        >
                           <StaticWaveformLayer bars={waveformBars} variant="played" />
                        </div>

                          <div className="absolute inset-y-0 left-0 pointer-events-none z-20" style={{ width: `${progressPercent}%` }}>
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
                          transition: 'background-color 700ms ease-in-out'
                        }}
                      >
                        <div
                          className="absolute inset-0 rounded-full"
                          style={{
                            width: `${bufferedPercent}%`,
                            backgroundColor: getGrey500(),
                            transition: 'all 700ms ease-in-out'
                          }}
                        />
                        <motion.div
                          className="absolute inset-0 rounded-full"
                          animate={{
                            width: `${progressPercent}%`,
                          }}
                          style={{
                            background: getGradient(1),
                            transition: 'background 700ms ease-in-out'
                          }}
                          transition={{
                            width: { duration: 0.1 }
                          }}
                        >
                          <div
                            className="absolute right-0 top-1/2 -translate-y-1/2 w-3 h-3 rounded-full shadow-lg opacity-0 group-hover:opacity-100 transition"
                            style={{
                              backgroundColor: getWhite()
                            }}
                          />
                        </motion.div>
                      </div>
                    )}
                    <span
                      className="text-xs tabular-nums min-w-[40px] text-center transition-colors duration-700"
                      style={{ color: getGrey400() }}
                    >
                      {formatDuration(durationMs)}
                    </span>
                  </div>

                  <div className="grid grid-cols-3 items-center gap-3 md:gap-4">
                    <div className="flex items-center gap-2 min-w-0">
                      <TrackArtwork
                        url={artworkUrl}
                        hasArtwork={currentTrack.has_artwork}
                        onClick={onArtworkClick}
                      />
                      <button
                        onClick={handleToggleSounds}
                        className="relative rounded-lg cursor-pointer hover:scale-105 flex items-center justify-center h-10 w-10 md:h-12 md:w-12"
                        style={{
                          background: getGradient(0.2),
                          border: `1px solid ${soundState.color === 'error' ? getErrorColor() : soundState.color === 'warning' ? '#f59e0b' : getAccentColor(0.3)}`,
                          transition: 'all 700ms ease-in-out, transform 150ms ease-in-out',
                          color: soundState.color === 'error' ? getErrorColor() : soundState.color === 'warning' ? '#f59e0b' : getAccentColor(0.9)
                        }}
                        title={soundState.title}
                      >
                        {soundState.icon === 'volume' && <Volume2 size={16} className="md:hidden" />}
                        {soundState.icon === 'bell' && <Bell size={16} className="md:hidden" />}
                        {soundState.icon === 'muted' && <VolumeX size={16} className="md:hidden" />}
                        {soundState.icon === 'volume' && <Volume2 size={20} className="hidden md:block" />}
                        {soundState.icon === 'bell' && <Bell size={20} className="hidden md:block" />}
                        {soundState.icon === 'muted' && <VolumeX size={20} className="hidden md:block" />}
                      </button>
                      <div className="hidden md:flex flex-1 min-w-0 flex-col">
                        <div
                          className="font-semibold truncate text-base transition-colors duration-700"
                          style={{ color: getWhite() }}
                        >
                          {currentTrack.title || currentTrack.generation_params?.title || 'Unknown'}
                        </div>
                        <div
                          className="text-sm truncate transition-colors duration-700"
                          style={{ color: getGrey400() }}
                        >
                          {currentTrack.generation_params?.style_canonical || currentTrack.generation_params?.style || currentTrack.style || ''}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center justify-center gap-2">
                      <motion.div
                        animate={isCrossfading ? { scale: 0.85, opacity: 0.6 } : { scale: 1, opacity: 1 }}
                        transition={{ duration: 0.2 }}
                      >
                        <PlayerIconButton
                          onClick={handlePrevious}
                          onMouseEnter={handleMouseEnterButton}
                          onMouseLeave={handleMouseLeaveButton}
                          title={progress_ms >= PREVIOUS_THRESHOLD_MS ? 'Restart track' : 'Previous track'}
                        >
                          <SkipBack size={20} />
                        </PlayerIconButton>
                      </motion.div>

                      <motion.button
                        onPointerDown={handleTogglePlay}
                        className="p-3 text-black hover:scale-110 rounded-full shadow-lg"
                        style={{
                          background: getAccentColor(1),
                          transition: 'background 700ms ease-in-out'
                        }}
                        animate={isCrossfading ? {
                          scale: [1, 1.05, 1],
                          transition: { duration: 0.8, repeat: Infinity, ease: "easeInOut" }
                        } : { scale: 1 }}
                        whileHover={{ scale: 1.1 }}
                        whileTap={{ scale: 0.95 }}
                        title={is_playing ? 'Pause' : 'Play'}
                      >
                        <AnimatePresence mode="wait">
                          {is_playing ? (
                            <motion.div
                              key="pause"
                              initial={{ scale: 0 }}
                              animate={{ scale: 1 }}
                              exit={{ scale: 0 }}
                            >
                              <Pause size={24} fill="currentColor" />
                            </motion.div>
                          ) : (
                            <motion.div
                              key="play"
                              initial={{ scale: 0 }}
                              animate={{ scale: 1 }}
                              exit={{ scale: 0 }}
                            >
                              <Play size={24} fill="currentColor" className="ml-0.5" />
                            </motion.div>
                          )}
                        </AnimatePresence>
                      </motion.button>

                      <motion.div
                        animate={isCrossfading ? { scale: 0.85, opacity: 0.6 } : { scale: 1, opacity: 1 }}
                        transition={{ duration: 0.2 }}
                      >
                        <PlayerIconButton
                          onClick={handleNext}
                          onMouseEnter={handleMouseEnterButton}
                          onMouseLeave={handleMouseLeaveButton}
                          title="Next track"
                        >
                          <SkipForward size={20} />
                        </PlayerIconButton>
                      </motion.div>
                    </div>

                    <div className="flex items-center justify-end gap-1.5 md:gap-3 min-w-0">
                      <div className="hidden md:flex items-center gap-2 flex-1 max-w-[120px]">
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
                            transition: 'background-color 700ms ease-in-out'
                          }}
                        >
                          <div
                            className="h-full rounded-full relative"
                            style={{
                              width: `${isMuted ? 0 : volume * 100}%`,
                              backgroundColor: getWhite(),
                              transition: 'background-color 700ms ease-in-out'
                            }}
                          >
                            <div
                              className={`absolute right-0 top-1/2 -translate-y-1/2 w-3 h-3 rounded-full shadow-lg transition ${
                                isDraggingVolume ? 'opacity-100 scale-110' : 'opacity-0 group-hover:opacity-100'
                              }`}
                              style={{
                                backgroundColor: getWhite(),
                                transition: 'all 700ms ease-in-out'
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
                        {audioState.isCached && (
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