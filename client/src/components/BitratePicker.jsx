import { logger } from '../lib/logger'
import { useState, useEffect, useCallback, useRef, memo } from 'react'
import { useAuth } from '../contexts/AuthContext'
import { useUISelector } from '../contexts/UIStateContext'
import { useNetwork, BITRATE_OPTIONS } from '../contexts/NetworkContext'
import { useDynamicTheme } from '../contexts/DynamicThemeContext'
import { Loader2, Activity, Check, Gauge, Gem, Zap, Leaf, Lock, RefreshCw } from 'lucide-react'
import { CSS_TRANSITION, PRESETS } from '../lib/motion'
import { Fade, FadeSwap, Pop } from './Motion'
import { InlineNote, StatusPill } from './Notice'
import { OptionCard, PlayerSheet, SheetGrid } from './PlayerSheet'

const BITRATE_LOOKS = {
  auto: { icon: Gauge, label: 'Auto', description: 'Follows your network speed' },
  '256k': { icon: Gem, tint: '#a78bfa', label: 'Premium', description: 'Best quality, 256 kbps' },
  '192k': { icon: Zap, tint: '#38bdf8', label: 'Standard', description: 'Good quality, 192 kbps' },
  '128k': { icon: Leaf, tint: '#34d399', label: 'Economy', description: 'Least data, 128 kbps' },
}

const QUALITY_TEXT = { excellent: 'text-emerald-300', good: 'text-sky-300', fair: 'text-amber-300', poor: 'text-red-300' }

export function useBitratePicker(onReloadTrackQuality) {
  const { isAuthenticated } = useAuth()
  const {
    toastError,
    toastInfo,
    actualBitrate,
    isOnline,
    networkQuality,
    detectedBitrate,
    dataSaverMode,
    currentBitrate,
    publishSettings,
  } = useUISelector(state => ({
    toastError: state.toastError,
    toastInfo: state.toastInfo,
    actualBitrate: state.audioState?.bitrate,
    isOnline: state.audioState?.isOnline ?? true,
    networkQuality: state.audioState?.networkQuality,
    detectedBitrate: state.audioState?.detectedBitrate,
    dataSaverMode: state.settingsState.dataSaverMode,
    currentBitrate: state.settingsState.audioQuality,
    publishSettings: state.publishSettings,
  }))
  const {
    detectNetworkQuality: networkDetectQuality,
    getEffectiveBitrate,
    isDetecting: networkIsDetecting
  } = useNetwork()

  const showError = toastError
  const showInfo = toastInfo
  const [isOpen, setIsOpen] = useState(false)
  const [actionLoading, setActionLoading] = useState(false)

  // Data saver overrides effective bitrate to 128k
  const effectiveBitrate = dataSaverMode ? '128k' : (actualBitrate || getEffectiveBitrate(currentBitrate, isAuthenticated))

  const lastDetectedRef = useRef(detectedBitrate)
  const reloadRef = useRef(onReloadTrackQuality)
  useEffect(() => {
    reloadRef.current = onReloadTrackQuality
  })

  useEffect(() => {
    const previous = lastDetectedRef.current
    lastDetectedRef.current = detectedBitrate
    if (!detectedBitrate || detectedBitrate === previous) return
    if (currentBitrate !== 'auto' || actionLoading || !reloadRef.current) return
    logger.info(`[BitratePicker] Auto mode: network bitrate changed to ${detectedBitrate}, reloading track`)
    reloadRef.current()
  }, [detectedBitrate, currentBitrate, actionLoading])

  const detectQuality = useCallback(async () => {
    try {
      const result = await networkDetectQuality()
      if (result?.quality) showInfo(`Network checked: ${result.quality}${result.bitrate ? `, ${result.bitrate} suits it` : ''}`)
    } catch (error) {
      logger.error('[BitratePicker] Failed to detect quality:', error)
      showError("Couldn't test the network")
    }
  }, [networkDetectQuality, showInfo, showError])

  const handleSelectBitrate = useCallback(async (bitrate) => {
    if (bitrate === currentBitrate) {
      setIsOpen(false)
      return
    }

    if (!isAuthenticated) {
      setIsOpen(false)
      return
    }

    try {
      setActionLoading(true)
      publishSettings({ audioQuality: bitrate })
      const reloaded = onReloadTrackQuality ? await onReloadTrackQuality() : true
      setIsOpen(false)
      if (reloaded === false) showInfo('Quality saved. It applies from the next song.')
    } catch (err) {
      logger.error('[BitratePicker] Failed to reload the track at the new quality:', err)
      showError('Failed to switch audio quality. Please try again.')
    } finally {
      setActionLoading(false)
    }
  }, [currentBitrate, isAuthenticated, publishSettings, onReloadTrackQuality, showInfo, showError])

  return {
    isAuthenticated,
    isOpen,
    setIsOpen,
    actionLoading,
    currentBitrate,
    actualBitrate,
    effectiveBitrate,
    dataSaverMode,
    isOnline,
    networkQuality,
    detectedBitrate,
    detecting: networkIsDetecting,
    handleSelectBitrate,
    detectQuality,
  }
}

export const BitratePickerButton = memo(function BitratePickerButton({
  currentBitrate,
  effectiveBitrate,
  dataSaverMode,
  isOpen,
  setIsOpen
}) {
  const { getGradient, getAccentColor} = useDynamicTheme()
  const displayLabel = dataSaverMode
    ? '128k'
    : (currentBitrate === 'auto'
      ? (effectiveBitrate ? `AUTO\n${effectiveBitrate}` : 'AUTO')
      : effectiveBitrate)

  return (
    <button
      onClick={() => setIsOpen(!isOpen)}
      className="ui-tap ui-hover text-[8px] font-semibold rounded-lg cursor-pointer flex flex-col items-center justify-center leading-tight gap-0 h-10 w-10 md:h-12 md:w-12 whitespace-pre"
      style={{
        background: dataSaverMode ? 'rgba(34, 197, 94, 0.2)' : getGradient(0.2),
        border: `1px solid ${dataSaverMode ? 'rgba(34, 197, 94, 0.4)' : getAccentColor(0.3)}`,
        color: dataSaverMode ? 'rgb(134, 239, 172)' : getAccentColor(0.9),
        transition: CSS_TRANSITION.theme
      }}
      title={dataSaverMode ? 'Data Saver is on: 128k' : 'Change audio quality'}
    >
      <FadeSwap swapKey={dataSaverMode ? 'saver' : displayLabel} preset={PRESETS.fade} mode="wait" className="flex flex-col items-center">
        {dataSaverMode && <Leaf size={12} />}
        {displayLabel}
      </FadeSwap>
    </button>
  )
})

export const BitratePickerPanel = memo(function BitratePickerPanel({
  isOpen,
  setIsOpen,
  actionLoading,
  currentBitrate,
  effectiveBitrate,
  dataSaverMode,
  isOnline,
  networkQuality,
  detectedBitrate,
  detecting,
  handleSelectBitrate,
  detectQuality,
  isAuthenticated
}) {
  return (
    <PlayerSheet open={isOpen} title="Audio Quality" onClose={() => setIsOpen(false)}>
      <Fade show={!!dataSaverMode} className="mb-3">
        <InlineNote tone="success">Data Saver is on: streaming stays at 128k and downloads play first. Turn it off in Settings to change quality.</InlineNote>
      </Fade>

      <Fade show={currentBitrate === 'auto' && !dataSaverMode} className="mb-3 flex items-center gap-3 text-xs text-white/60">
        <Activity size={14} className={isOnline ? 'text-sky-300' : 'text-red-300'} />
        <FadeSwap swapKey={!isOnline ? 'offline' : networkQuality ? 'quality' : 'detecting'} preset={PRESETS.fade} className="flex-1 min-w-0">
          {!isOnline ? (
            <span>Network: <span className="font-semibold text-red-300">offline</span></span>
          ) : networkQuality ? (
            <span>
              Network: <span className={`font-semibold ${QUALITY_TEXT[networkQuality] || 'text-white/70'}`}>{networkQuality}</span>
              {detectedBitrate && <> · using <span className="font-semibold text-white">{detectedBitrate}</span></>}
            </span>
          ) : (
            <span>Checking the network…</span>
          )}
        </FadeSwap>
        <Pop show={!!isOnline} className="flex">
          <button
            onClick={detectQuality}
            disabled={detecting}
            className="ui-press inline-flex items-center gap-1 rounded-full bg-white/10 px-2.5 py-1 text-xs text-white/80 hover:bg-white/20 transition-colors disabled:opacity-50"
          >
            <RefreshCw size={12} className={detecting ? 'animate-spin' : undefined} />
            {detecting ? 'Checking…' : 'Check again'}
          </button>
        </Pop>
      </Fade>

      {!isAuthenticated && (
        <InlineNote tone="warning" className="mb-3">Sign in to choose a quality and keep it.</InlineNote>
      )}

      <SheetGrid>
        {BITRATE_OPTIONS.map(bitrate => {
          const option = BITRATE_LOOKS[bitrate] || BITRATE_LOOKS.auto
          const isSelected = bitrate === currentBitrate
          const isEffective = bitrate === effectiveBitrate && currentBitrate === 'auto'
          const isLocked = !isAuthenticated && bitrate === '256k'
          const isGuestDisabled = !isAuthenticated && !isSelected
          const unavailable = isLocked || isGuestDisabled
          return (
            <OptionCard
              key={bitrate}
              icon={isLocked ? Lock : option.icon}
              tint={option.tint}
              title={option.label}
              subtitle={isLocked ? 'Sign in required' : option.description}
              selected={isSelected && !unavailable}
              disabled={unavailable}
              onSelect={actionLoading ? undefined : () => handleSelectBitrate(bitrate)}
              hint={isLocked ? 'Premium quality - sign in required' : isGuestDisabled ? 'Sign in to change quality' : option.description}
              badge={(
                <Pop show={isSelected || isEffective} className="flex">
                  <FadeSwap swapKey={isSelected ? 'selected' : 'inuse'} preset={PRESETS.badgeSwap} mode="wait" className="flex">
                    {isSelected
                      ? <StatusPill tone="neutral" icon={Check}>Selected</StatusPill>
                      : <StatusPill tone="info">In use</StatusPill>}
                  </FadeSwap>
                </Pop>
              )}
            />
          )
        })}
      </SheetGrid>

      <Fade show={!!actionLoading} className="mt-1 flex items-center justify-center gap-2 text-sm text-white/70">
        <Loader2 size={16} className="animate-spin" />
        Applying…
      </Fade>
    </PlayerSheet>
  )
})
