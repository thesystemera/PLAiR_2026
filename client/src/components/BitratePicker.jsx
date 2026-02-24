import { logger } from '../lib/logger'
import { useState, useEffect, useCallback, useRef, memo } from 'react'
import { api } from '../lib/api'
import { useAuth } from '../contexts/AuthContext'
import { useUIState } from '../contexts/UIStateContext'
import { useNetwork, BITRATE_OPTIONS } from '../contexts/NetworkContext'
import { useDynamicTheme } from '../contexts/DynamicThemeContext'
import { X, Loader2, Activity } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { TRANSITIONS } from '../lib/themeManager'

export function useBitratePicker(onReloadTrackQuality) {
  const { isAuthenticated, refreshUser } = useAuth()
  const { toastError, audioState, settingsState, publishSettings } = useUIState()
  const {
    detectNetworkQuality: networkDetectQuality,
    getEffectiveBitrate,
    isDetecting: networkIsDetecting
  } = useNetwork()

  const showError = toastError
  const [isOpen, setIsOpen] = useState(false)
  const [actionLoading, setActionLoading] = useState(false)

  const dataSaverMode = settingsState.dataSaverMode
  const currentBitrate = settingsState.audioQuality
  const actualBitrate = audioState?.bitrate
  // Data saver overrides effective bitrate to 128k
  const effectiveBitrate = dataSaverMode ? '128k' : (actualBitrate || getEffectiveBitrate(currentBitrate, isAuthenticated))

  const isOnline = audioState?.isOnline ?? true
  const networkQuality = audioState?.networkQuality
  const detectedBitrate = audioState?.detectedBitrate
  const isFirstRender = useRef(true)

  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false
      return
    }

    if (actionLoading) {
      return
    }

    if (currentBitrate === 'auto' && detectedBitrate && onReloadTrackQuality) {
      logger.info(`[BitratePicker] Auto mode: network bitrate changed to ${detectedBitrate}, reloading track`)
      onReloadTrackQuality()
    }
  }, [detectedBitrate, currentBitrate, actionLoading])

  const detectQuality = async () => {
    try {
      await networkDetectQuality()
    } catch (error) {
      logger.error('[BitratePicker] Failed to detect quality:', error)
    }
  }

  const handleSelectBitrate = async (bitrate) => {
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

      await api.updateAudioQuality(bitrate)

      if (refreshUser) {
        await refreshUser()
      }

      if (onReloadTrackQuality) {
        await onReloadTrackQuality()
      }

      setIsOpen(false)
    } catch (err) {
      logger.error('[BitratePicker] Failed to update bitrate:', err)
      publishSettings({ audioQuality: settingsState.audioQuality })
      showError('Failed to update audio quality. Please try again.')
    } finally {
      setActionLoading(false)
    }
  }

  const getBitrateIcon = useCallback((bitrate) => {
    switch (bitrate) {
      case 'auto': return '🤖'
      case '256k': return '💎'
      case '192k': return '⚡'
      case '128k': return '💚'
      default: return '🎵'
    }
  }, [])

  const getBitrateLabel = useCallback((bitrate) => {
    switch (bitrate) {
      case 'auto': return 'Auto'
      case '256k': return 'Premium'
      case '192k': return 'Standard'
      case '128k': return 'Economy'
      default: return bitrate
    }
  }, [])

  const getBitrateDescription = useCallback((bitrate) => {
    switch (bitrate) {
      case 'auto': return 'Adapts to your network speed'
      case '256k': return 'Best quality - 256kbps'
      case '192k': return 'Good quality - 192kbps'
      case '128k': return 'Lower bandwidth - 128kbps'
      default: return ''
    }
  }, [])

  const getQualityColor = useCallback((quality) => {
    switch (quality) {
      case 'excellent': return 'text-green-400'
      case 'good': return 'text-blue-400'
      case 'fair': return 'text-yellow-400'
      case 'poor': return 'text-red-400'
      default: return 'text-gray-400'
    }
  }, [])

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
    getBitrateIcon,
    getBitrateLabel,
    getBitrateDescription,
    getQualityColor
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
    ? `🌿\n128k`
    : (currentBitrate === 'auto'
      ? (effectiveBitrate ? `AUTO\n${effectiveBitrate}` : 'AUTO')
      : effectiveBitrate)

  return (
    <button
      onClick={() => setIsOpen(!isOpen)}
      className="text-[8px] font-semibold rounded-lg cursor-pointer hover:scale-105 flex flex-col items-center justify-center leading-tight gap-0 h-10 w-10 md:h-12 md:w-12 whitespace-pre"
      style={{
        background: dataSaverMode ? 'rgba(34, 197, 94, 0.2)' : getGradient(0.2),
        border: `1px solid ${dataSaverMode ? 'rgba(34, 197, 94, 0.4)' : getAccentColor(0.3)}`,
        color: dataSaverMode ? 'rgb(134, 239, 172)' : getAccentColor(0.9),
        transition: 'all 700ms ease-in-out, transform 150ms ease-in-out'
      }}
      title={dataSaverMode ? "Data Saver Mode - 128k" : "Click to change audio quality"}
    >
      {displayLabel}
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
  getBitrateIcon,
  getBitrateLabel,
  getBitrateDescription,
  getQualityColor,
  isAuthenticated
}) {
  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: 'auto', opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={TRANSITIONS.panel}
          className="w-full border-t border-white/10 shadow-2xl overflow-hidden"
        >
          <div className="max-w-screen-2xl mx-auto px-3 py-2 md:px-4 md:py-3">
            <div className="flex items-center justify-between mb-3 border-b border-white/10 pb-3">
              <h3 className="m-0 text-lg font-semibold text-white">Audio Quality</h3>
              <button
                onClick={() => setIsOpen(false)}
                className="bg-transparent border-none text-white/60 text-xl cursor-pointer p-1 leading-none transition-colors hover:text-white"
                title="Close"
              >
                <X size={20} />
              </button>
            </div>

            {dataSaverMode && (
              <div className="mb-3 p-3 rounded-lg bg-green-500/10 border border-green-500/20 text-sm text-green-200/90">
                <span className="font-medium">🌿 Data Saver Mode:</span> Streaming locked to 128k, preferring cached tracks. Turn off in Settings to change quality.
              </div>
            )}

            {currentBitrate === 'auto' && !dataSaverMode && (
              <div className="mb-3 p-3 rounded-lg bg-white/5 border border-white/10">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <Activity size={16} className={isOnline ? "text-blue-400" : "text-red-400"} />
                    <span className="text-sm font-medium text-white">Network Status</span>
                  </div>
                  {isOnline && (
                    <button
                      onClick={detectQuality}
                      disabled={detecting}
                      className="text-xs px-2 py-1 rounded bg-white/10 hover:bg-white/20 transition disabled:opacity-50"
                    >
                      {detecting ? (
                        <span className="flex items-center gap-1">
                          <Loader2 size={12} className="animate-spin" />
                          Detecting...
                        </span>
                      ) : (
                        'Re-detect'
                      )}
                    </button>
                  )}
                </div>
                {!isOnline ? (
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-white/60">
                      Quality: <span className="font-semibold text-red-400">OFFLINE</span>
                    </span>
                  </div>
                ) : networkQuality ? (
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-white/60">
                      Quality: <span className={`font-semibold ${getQualityColor(networkQuality)}`}>
                        {networkQuality.toUpperCase()}
                      </span>
                    </span>
                    <span className="text-white/60">
                      Using: <span className="font-semibold text-white">{detectedBitrate}</span>
                    </span>
                  </div>
                ) : (
                  <div className="text-xs text-white/40">Detecting network quality...</div>
                )}
              </div>
            )}

            {!isAuthenticated && (
              <div className="mb-3 p-3 rounded-lg bg-yellow-500/10 border border-yellow-500/20 text-sm text-yellow-200/90">
                <span className="font-medium">Guest mode:</span> Login to unlock all quality options and save preferences
              </div>
            )}

            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 pb-2">
              {BITRATE_OPTIONS.map(bitrate => {
                const isSelected = bitrate === currentBitrate
                const isEffective = bitrate === effectiveBitrate && currentBitrate === 'auto'
                const isLocked = !isAuthenticated && bitrate === '256k'
                const isGuestDisabled = !isAuthenticated && bitrate !== currentBitrate

                return (
                  <div
                    key={bitrate}
                    className={`
                      flex flex-col gap-2 p-3 rounded-lg border transition-all
                      ${isLocked || isGuestDisabled
                        ? 'bg-white/5 border-white/10 opacity-50 cursor-not-allowed'
                        : 'cursor-pointer'
                      }
                      ${isSelected && !isLocked && !isGuestDisabled
                        ? 'bg-purple-600/20 border-purple-600/40 ring-2 ring-purple-500/50'
                        : !isLocked && !isGuestDisabled ? 'bg-white/5 border-white/10 hover:bg-purple-600/15 hover:border-purple-600/30 hover:scale-[1.02]' : ''
                      }
                      ${isEffective && !isSelected ? 'ring-2 ring-blue-500/30' : ''}
                    `}
                    onClick={() => !actionLoading && !isLocked && !isGuestDisabled && handleSelectBitrate(bitrate)}
                    title={isLocked ? 'Premium quality - Login required' : isGuestDisabled ? 'Login to change quality' : getBitrateDescription(bitrate)}
                  >
                    <div className="flex items-center justify-between">
                      <div className="text-2xl w-8 h-8 flex items-center justify-center flex-shrink-0">
                        {isLocked ? '🔒' : getBitrateIcon(bitrate)}
                      </div>
                      {isSelected && (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-purple-500 text-white font-medium whitespace-nowrap">
                          Selected
                        </span>
                      )}
                      {isEffective && !isSelected && (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-blue-500/50 text-white font-medium whitespace-nowrap">
                          In use
                        </span>
                      )}
                    </div>
                    <div className="flex-1">
                      <div className="text-sm font-medium text-white mb-1">
                        {getBitrateLabel(bitrate)}
                        {isLocked && <span className="ml-1 text-xs text-white/40">🔒</span>}
                      </div>
                      <div className="text-xs text-white/60">
                        {isLocked ? 'Login required' : getBitrateDescription(bitrate)}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>

            {actionLoading && (
              <div className="mt-3 p-2 bg-white/5 rounded-lg flex items-center justify-center gap-2 text-sm text-white/70">
                <Loader2 size={16} className="animate-spin" />
                Applying settings...
              </div>
            )}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
})