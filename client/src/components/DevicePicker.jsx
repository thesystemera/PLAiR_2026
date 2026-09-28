import { logger } from '../lib/logger'
import { useState, useEffect, useCallback, useMemo, memo, useRef } from 'react'
import { api } from '../lib/api'
import { useAuth } from '../contexts/AuthContext'
import { usePlaybackActions, usePlaybackConnected } from '../contexts/PlaybackContext'
import { useUISelector } from '../contexts/UIStateContext'
import { useStorage } from '../contexts/StorageContext'
import { useDynamicTheme } from '../contexts/DynamicThemeContext'
import { useNetwork } from '../contexts/NetworkContext'
import { X, Loader2, Edit2, Check, X as XIcon, WifiOff, ServerOff, HardDrive } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { GLASS } from '../lib/themeManager'
import { CSS_TRANSITION, SPRING, TWEEN } from '../lib/motion'
import { Expandable } from './Motion'

const BANNER_SAFE_AREA_STYLE = {
  paddingTop: 'calc(var(--safe-top) + 0.75rem)',
  paddingLeft: 'calc(var(--safe-left) + 0.75rem)',
  paddingRight: 'calc(var(--safe-right) + 0.75rem)'
}

const BANNER_MOTION = {
  initial: { y: '-100%', opacity: 0 },
  animate: { y: 0, opacity: 1, transition: SPRING.panel },
  exit: { y: '-100%', opacity: 0, transition: TWEEN.exit }
}

export function useDevicePicker() {
  const { isAuthenticated } = useAuth()
  const { activeDeviceId, isActiveDevice, isPlaying, toastSuccess, toastError } = useUISelector(state => ({
    activeDeviceId: state.engineState.activeDeviceId,
    isActiveDevice: state.engineState.isActiveDevice,
    isPlaying: state.engineState.is_playing,
    toastSuccess: state.toastSuccess,
    toastError: state.toastError,
  }))
  const { transferPlayback } = usePlaybackActions()
  const connected = usePlaybackConnected()
  const { storageInfo } = useStorage()
  const { connectionMode, isOnline } = useNetwork()
  const [deviceList, setDeviceList] = useState([])
  const [isOpen, setIsOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [actionLoading, setActionLoading] = useState(false)
  const [editingDeviceId, setEditingDeviceId] = useState(null)
  const [editingName, setEditingName] = useState('')
  const [bannerDismissed, setBannerDismissed] = useState(false)
  const loadingDevicesRef = useRef(false)

  const canUseServerFeatures = isOnline && connectionMode === 'full'

  const loadDevices = useCallback(async () => {
    if (loadingDevicesRef.current) return
    loadingDevicesRef.current = true
    try {
      setLoading(true)
      const response = await api.getDevices()
      logger.info('[DevicePicker] Loaded devices:', response.devices)
      setDeviceList(response.devices || [])
    } catch (err) {
      logger.error('[DevicePicker] Failed to load devices:', err)
    } finally {
      loadingDevicesRef.current = false
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    setBannerDismissed(false)
  }, [activeDeviceId])

  useEffect(() => {
    if (isAuthenticated && canUseServerFeatures && (connected || isOpen)) {
      void loadDevices()
    }
  }, [isAuthenticated, canUseServerFeatures, connected, isOpen, loadDevices])

  const knownActive = !activeDeviceId || deviceList.some(d => d.device_id === activeDeviceId)
  useEffect(() => {
    if (!knownActive && isAuthenticated && canUseServerFeatures) {
      logger.info(`[DevicePicker] Unknown device ${activeDeviceId?.slice(0, 8)} is active, reloading devices`)
      void loadDevices()
    }
  }, [knownActive, activeDeviceId, isAuthenticated, canUseServerFeatures, loadDevices])

  const devices = useMemo(() => deviceList.map(device => ({
    ...device,
    is_active: !!activeDeviceId && device.device_id === activeDeviceId
  })), [deviceList, activeDeviceId])

  const handleActivateDevice = async (deviceId = null) => {
    try {
      setActionLoading(true)
      await transferPlayback(deviceId)
      logger.info('[DevicePicker] Device activated successfully:', deviceId || 'current device')
      if (isAuthenticated) await loadDevices()
      if (!deviceId) {
        setIsOpen(false)
      }
    } catch (err) {
      logger.error('[DevicePicker] Failed to activate device:', err)
      toastError?.('Could not move playback to that device')
    } finally {
      setActionLoading(false)
    }
  }

  const handleRemoveDevice = async (deviceId) => {
    if (!confirm('Remove this device?')) return

    try {
      setActionLoading(true)
      await api.removeDevice(deviceId)
      await loadDevices()
      toastSuccess('Device removed')
    } catch (err) {
      logger.error('[DevicePicker] Failed to remove device:', err)
    } finally {
      setActionLoading(false)
    }
  }

  const handleStartEdit = (deviceId, currentName) => {
    setEditingDeviceId(deviceId)
    setEditingName(currentName)
  }

  const handleCancelEdit = () => {
    setEditingDeviceId(null)
    setEditingName('')
  }

  const handleSaveEdit = async (deviceId) => {
    if (!editingName.trim()) {
      handleCancelEdit()
      return
    }

    try {
      setActionLoading(true)
      logger.info('[DevicePicker] Renaming device:', deviceId, 'to:', editingName.trim())
      const result = await api.renameDevice(deviceId, editingName.trim())
      logger.info('[DevicePicker] Rename result:', result)

      await loadDevices()

      logger.info('[DevicePicker] Devices reloaded, clearing edit state')
      setEditingDeviceId(null)
      setEditingName('')
      toastSuccess('Device renamed')
    } catch (err) {
      logger.error('[DevicePicker] Failed to rename device:', err)
    } finally {
      setActionLoading(false)
    }
  }

  const getDeviceIcon = useCallback((type) => {
    switch (type) {
      case 'mobile': return '📱'
      case 'tablet': return '📋'
      case 'desktop': return '🖥️'
      default: return '💻'
    }
  }, [])

  const activeDevice = useMemo(() => devices.find(d => d.is_active), [devices])
  const currentDevice = useMemo(() => devices.find(d => d.is_current), [devices])

  const handleDismissBanner = useCallback(() => {
    setBannerDismissed(true)
  }, [])

  return {
    isAuthenticated,
    devices,
    isOpen,
    setIsOpen,
    showInactive: !isActiveDevice && !!activeDeviceId,
    bannerDismissed,
    handleDismissBanner,
    loading,
    actionLoading,
    activeDevice,
    currentDevice,
    handleActivateDevice,
    handleRemoveDevice,
    getDeviceIcon,
    editingDeviceId,
    editingName,
    setEditingName,
    handleStartEdit,
    handleCancelEdit,
    handleSaveEdit,
    connectionMode,
    storageInfo,
    isPlaying
  }
}

export const DevicePickerButton = memo(function DevicePickerButton({ currentDevice, activeDevice, isOpen, setIsOpen, getDeviceIcon, connectionMode, storageInfo, isPlaying }) {
  const { getErrorColor, getGradient, getAccentColor, getSuccessColor, getWarningColor } = useDynamicTheme()

  if (connectionMode !== 'full') {
    const isOffline = connectionMode === 'offline'
    const Icon = isOffline ? WifiOff : ServerOff
    const color = isOffline ? getErrorColor() : '#f59e0b' // amber-500 for degraded
    const title = isOffline
      ? `Offline - ${storageInfo?.trackCount || 0} cached tracks available`
      : `Server unavailable - ${storageInfo?.trackCount || 0} cached tracks available`

    return (
      <button
        className="ui-tap ui-hover relative rounded-lg cursor-pointer flex flex-col items-center justify-center gap-0.5 h-10 w-10 md:h-12 md:w-12"
        style={{
          background: 'transparent',
          border: `1px solid ${color}`,
          transition: CSS_TRANSITION.theme
        }}
        onClick={() => setIsOpen(!isOpen)}
        title={title}
      >
        <Icon size={12} className="md:hidden" style={{ color }} />
        <Icon size={14} className="hidden md:block" style={{ color }} />
        <div className="flex items-center gap-0.5">
          <HardDrive size={8} className="md:hidden" style={{ color }} />
          <HardDrive size={10} className="hidden md:block" style={{ color }} />
          <span className="text-[8px] font-bold" style={{ color }}>{storageInfo?.trackCount || 0}</span>
        </div>
      </button>
    )
  }

  let badge = null
  if (activeDevice && !activeDevice.is_current) {
    if (isPlaying) {
      badge = <span className="absolute -top-1 -right-1 text-white text-[10px] rounded-full w-4 h-4 flex items-center justify-center shadow-md" style={{ background: getSuccessColor() }}>▶️</span>
    } else {
      badge = <span className="absolute -top-1 -right-1 text-white text-[10px] rounded-full w-4 h-4 flex items-center justify-center shadow-md" style={{ background: getWarningColor() }}>⏸️</span>
    }
  }

  return (
    <button
      className="ui-tap ui-hover relative rounded-lg cursor-pointer flex items-center justify-center h-10 w-10 md:h-12 md:w-12 text-lg md:text-xl"
      style={{
        background: getGradient(0.2),
        border: `1px solid ${getAccentColor(0.3)}`,
        transition: CSS_TRANSITION.theme
      }}
      onClick={() => setIsOpen(!isOpen)}
      title="Manage devices"
    >
      <span>{getDeviceIcon(currentDevice?.device_type || 'desktop')}</span>
      {badge}
    </button>
  )
})

export const DevicePickerBanner = memo(function DevicePickerBanner({ showInactive, bannerDismissed, actionLoading, handleActivateDevice, handleDismissBanner }) {
  const activeDeviceOnline = useUISelector(state => state.engineState.activeDeviceOnline)
  const message = activeDeviceOnline ? '▶️ Playing on another device' : '📴 Your other device is offline'
  return (
    <AnimatePresence>
      {showInactive && !bannerDismissed && (
    <motion.div {...BANNER_MOTION} className="fixed top-0 left-0 right-0 bg-gradient-to-r from-purple-600 to-blue-600 text-white p-3 flex items-center justify-center gap-4 z-[9999] text-sm font-medium shadow-lg" style={BANNER_SAFE_AREA_STYLE}>
      <span>{message}</span>
      <button
        onClick={() => handleActivateDevice()}
        disabled={actionLoading}
        className="ui-press ui-hover bg-white text-purple-600 px-4 py-1.5 rounded-full text-xs font-bold cursor-pointer transition shadow hover:shadow-md disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {actionLoading ? 'Loading...' : 'Play here instead'}
      </button>
      <button
        onClick={handleDismissBanner}
        className="ui-tap ui-hover-lg ml-auto bg-transparent border-none text-white/80 cursor-pointer p-1 leading-none transition-colors hover:text-white"
        title="Close (will reappear on device change)"
      >
        <X size={18} />
      </button>
    </motion.div>
      )}
    </AnimatePresence>
  )
})

export const DevicePickerPanel = memo(function DevicePickerPanel({ isOpen, setIsOpen, devices, loading, actionLoading, handleActivateDevice, handleRemoveDevice, getDeviceIcon, editingDeviceId, editingName, setEditingName, handleStartEdit, handleCancelEdit, handleSaveEdit, connectionMode, storageInfo, isPlaying }) {
  const isFullMode = connectionMode === 'full'
  const isOffline = connectionMode === 'offline'
  const isDegraded = connectionMode === 'degraded'

  return (
    <Expandable open={isOpen} className={GLASS.expandPanel}>
            <div className="max-w-screen-2xl mx-auto px-3 py-2 md:px-4 md:py-3">
              <div className={GLASS.expandPanelHeader}>
                <h3 className="m-0 text-lg font-semibold text-white">
                  {isOffline ? 'Offline Mode' : isDegraded ? 'Server Unavailable' : 'Devices'}
                </h3>
                <button
                  onClick={() => setIsOpen(false)}
                  className="ui-press bg-transparent border-none text-white/60 text-xl cursor-pointer p-1 leading-none transition-colors hover:text-white"
                >
                  <X size={20} />
                </button>
              </div>

              {!isFullMode ? (
                <div className="p-6 text-center">
                  <div className="flex flex-col items-center gap-4 mb-4">
                    <div className={`w-16 h-16 rounded-full border-2 flex items-center justify-center ${
                      isOffline
                        ? 'bg-red-500/20 border-red-500/40'
                        : 'bg-amber-500/20 border-amber-500/40'
                    }`}>
                      {isOffline ? (
                        <WifiOff size={32} className="text-red-400" />
                      ) : (
                        <ServerOff size={32} className="text-amber-400" />
                      )}
                    </div>
                    <div>
                      <h4 className="text-lg font-semibold text-white mb-2">
                        {isOffline ? 'No Internet Connection' : 'Server Unavailable'}
                      </h4>
                      <p className="text-sm text-white/60 mb-4">
                        {isOffline
                          ? "You're currently offline. Playing from cached tracks."
                          : "Can't reach the server. Playing from cached tracks."}
                      </p>
                    </div>
                  </div>
                  <div className="bg-white/5 border border-white/10 rounded-lg p-4">
                    <div className="flex items-center justify-center gap-3 text-white/80">
                      <HardDrive size={24} className="text-blue-400" />
                      <div className="text-left">
                        <div className="text-2xl font-bold">{storageInfo?.trackCount || 0}</div>
                        <div className="text-xs text-white/60">Cached Tracks Available</div>
                      </div>
                    </div>
                    {storageInfo?.usedBytes > 0 && (
                      <div className="mt-3 pt-3 border-t border-white/10 text-xs text-white/50">
                        Using {(storageInfo.usedBytes / (1024 * 1024)).toFixed(1)} MB of storage
                      </div>
                    )}
                  </div>
                </div>
              ) : loading ? (
                <div className="p-8 text-center text-white/50 text-sm flex items-center justify-center gap-2">
                  <Loader2 size={16} className="animate-spin" />
                  Loading...
                </div>
              ) : devices.length === 0 ? (
                <div className="p-8 text-center text-white/50 text-sm">No devices online</div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2 pb-2">
                  {devices.map(device => (
                    <div
                      key={device.device_id}
                      className={`
                        flex items-center gap-3 p-3 rounded-lg border transition
                        ${device.is_current ? 'bg-purple-600/20 border-purple-600/40' : 'bg-white/5 border-white/10'}
                        ${device.is_active ? 'ring-2 ring-green-500/50' : ''}
                        ${!device.is_active ? 'cursor-pointer hover:bg-purple-600/15 hover:border-purple-600/30 hover:scale-[1.02]' : ''}
                      `}
                      onClick={() => !device.is_active && handleActivateDevice(device.device_id)}
                      title={device.is_active ? 'Playing here' : 'Play here'}
                    >
                      <div className="text-2xl w-8 h-8 flex items-center justify-center flex-shrink-0">
                        {getDeviceIcon(device.device_type)}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-white flex items-center gap-2 mb-1">
                          {editingDeviceId === device.device_id ? (
                            <div className="flex items-center gap-2 flex-1">
                              <input
                                type="text"
                                value={editingName}
                                onChange={(e) => setEditingName(e.target.value)}
                                onClick={(e) => e.stopPropagation()}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') {
                                    handleSaveEdit(device.device_id)
                                  } else if (e.key === 'Escape') {
                                    handleCancelEdit()
                                  }
                                }}
                                className="flex-1 bg-white/10 border border-white/20 rounded px-2 py-1 text-white text-sm focus:outline-none focus:border-purple-500"
                                autoFocus
                                disabled={actionLoading}
                              />
                              <button
                                onClick={(e) => {
                                  e.stopPropagation()
                                  handleSaveEdit(device.device_id)
                                }}
                                className="ui-press p-1 hover:bg-green-500/20 rounded transition"
                                disabled={actionLoading}
                                title="Save"
                              >
                                <Check size={16} className="text-green-400" />
                              </button>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation()
                                  handleCancelEdit()
                                }}
                                className="ui-press p-1 hover:bg-red-500/20 rounded transition"
                                disabled={actionLoading}
                                title="Cancel"
                              >
                                <XIcon size={16} className="text-red-400" />
                              </button>
                            </div>
                          ) : (
                            <>
                              <span className="truncate">{device.device_name}</span>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation()
                                  handleStartEdit(device.device_id, device.device_name)
                                }}
                                className="ui-press p-1 hover:bg-white/10 rounded transition opacity-50 hover:opacity-100"
                                title="Rename device"
                              >
                                <Edit2 size={14} />
                              </button>
                              {device.is_current && <span className="text-xs px-2 py-0.5 rounded-full bg-white/20 text-white/90 font-medium whitespace-nowrap">Current device</span>}
                              {device.is_active && isPlaying && <span className="text-xs px-2 py-0.5 rounded-full bg-green-500 text-white font-medium whitespace-nowrap">▶️ Playing</span>}
                              {device.is_active && !isPlaying && <span className="text-xs px-2 py-0.5 rounded-full bg-yellow-500 text-white font-medium whitespace-nowrap">⏸️ Paused</span>}
                            </>
                          )}
                        </div>
                        <div className="text-xs text-white/40">
                          {device.last_active
                            ? new Date(device.last_active).toLocaleString()
                            : 'Never used'}
                        </div>
                      </div>
                      {!device.is_current && (
                        <button
                          className="ui-tap ui-hover-lg bg-transparent border-none text-lg cursor-pointer opacity-50 transition-opacity p-1 hover:opacity-100"
                          onClick={(e) => {
                            e.stopPropagation()
                            handleRemoveDevice(device.device_id)
                          }}
                          title="Remove device"
                          disabled={actionLoading}
                        >
                          🗑️
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
          </div>
    </Expandable>
  )
})