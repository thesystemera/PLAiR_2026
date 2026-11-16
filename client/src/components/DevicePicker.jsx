import { logger } from '../lib/logger'
import { createElement, useState, useEffect, useCallback, useMemo, memo, useRef } from 'react'
import { api } from '../lib/api'
import { useAuth } from '../contexts/AuthContext'
import { usePlaybackActions, usePlaybackConnected } from '../contexts/PlaybackContext'
import { useUISelector } from '../contexts/UIStateContext'
import { useStorage } from '../contexts/StorageContext'
import { useDynamicTheme } from '../contexts/DynamicThemeContext'
import { useNetwork } from '../contexts/NetworkContext'
import { Pencil, Check, X, WifiOff, ServerOff, HardDrive, MonitorSpeaker, Play, Pause, Smartphone, Tablet, Monitor, Laptop, Trash2 } from 'lucide-react'
import { CSS_TRANSITION, PRESETS } from '../lib/motion'
import { formatTimeAgo } from '../lib/utils'
import { FadeSwap, Pop } from './Motion'
import { NoticeAction, NoticeClose, StatusPill } from './Notice'
import { MediaEmptyState, MediaLoadError } from './MediaShared'
import { CardIconButton, OptionCard, PlayerSheet, SheetGrid } from './PlayerSheet'
import { useDialog } from '../contexts/DialogContext'

const DEVICE_ICONS = { mobile: Smartphone, tablet: Tablet, desktop: Monitor }
const deviceIcon = (type) => DEVICE_ICONS[type] || Laptop
const DeviceGlyph = ({ type, ...props }) => createElement(deviceIcon(type), props)

const DEVICE_NAME_ORDER = new Intl.Collator(undefined, { sensitivity: 'base', numeric: true })

function byStablePlace(a, b) {
  if (a.is_current !== b.is_current) return a.is_current ? -1 : 1
  return DEVICE_NAME_ORDER.compare(a.device_name || '', b.device_name || '') || a.device_id.localeCompare(b.device_id)
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
  const { showConfirm } = useDialog()
  const [deviceList, setDeviceList] = useState([])
  const [isOpen, setIsOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [loadFailed, setLoadFailed] = useState(false)
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
      setLoadFailed(false)
    } catch (err) {
      logger.error('[DevicePicker] Failed to load devices:', err)
      setLoadFailed(true)
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

  const devices = useMemo(() => deviceList
    .map(device => ({
      ...device,
      is_active: !!activeDeviceId && device.device_id === activeDeviceId
    }))
    .sort(byStablePlace), [deviceList, activeDeviceId])

  const handleActivateDevice = useCallback(async (deviceId = null) => {
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
  }, [transferPlayback, isAuthenticated, loadDevices, toastError])

  const handleRemoveDevice = useCallback(async (deviceId) => {
    const device = deviceList.find(d => d.device_id === deviceId)
    const confirmed = await showConfirm({
      title: `Remove ${device?.device_name || 'this device'}?`,
      message: 'It is taken off your device list.',
      confirmText: 'Remove',
      cancelText: 'Cancel',
      variant: 'danger',
    })
    if (!confirmed) return

    try {
      setActionLoading(true)
      await api.removeDevice(deviceId)
      await loadDevices()
      toastSuccess('Device removed')
    } catch (err) {
      logger.error('[DevicePicker] Failed to remove device:', err)
      toastError?.('Could not remove that device')
    } finally {
      setActionLoading(false)
    }
  }, [deviceList, showConfirm, loadDevices, toastSuccess, toastError])

  const handleStartEdit = useCallback((deviceId, currentName) => {
    setEditingDeviceId(deviceId)
    setEditingName(currentName)
  }, [])

  const handleCancelEdit = useCallback(() => {
    setEditingDeviceId(null)
    setEditingName('')
  }, [])

  const handleSaveEdit = useCallback(async (deviceId) => {
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
      toastError?.('Could not rename that device')
    } finally {
      setActionLoading(false)
    }
  }, [editingName, handleCancelEdit, loadDevices, toastSuccess, toastError])

  const activeDevice = useMemo(() => devices.find(d => d.is_active), [devices])
  const currentDevice = useMemo(() => devices.find(d => d.is_current), [devices])

  const handleDismissBanner = useCallback(() => {
    setBannerDismissed(true)
  }, [])

  const showInactive = !isActiveDevice && !!activeDeviceId
  return useMemo(() => ({
    isAuthenticated,
    devices,
    isOpen,
    setIsOpen,
    showInactive,
    bannerDismissed,
    handleDismissBanner,
    loading,
    loadFailed,
    loadDevices,
    actionLoading,
    activeDevice,
    currentDevice,
    handleActivateDevice,
    handleRemoveDevice,
    editingDeviceId,
    editingName,
    setEditingName,
    handleStartEdit,
    handleCancelEdit,
    handleSaveEdit,
    connectionMode,
    storageInfo,
    isPlaying
  }), [
    isAuthenticated, devices, isOpen, showInactive, bannerDismissed, handleDismissBanner, loading, loadFailed, loadDevices,
    actionLoading, activeDevice, currentDevice, handleActivateDevice, handleRemoveDevice, editingDeviceId,
    editingName, handleStartEdit, handleCancelEdit, handleSaveEdit, connectionMode, storageInfo, isPlaying,
  ])
}

const DEVICE_BADGE_CLASS = 'text-white rounded-full w-4 h-4 flex items-center justify-center shadow-md'

function OfflineDeviceButton({ isOpen, setIsOpen, connectionMode, storageInfo }) {
  const { getErrorColor } = useDynamicTheme()
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

function OnlineDeviceButton({ currentDevice, activeDevice, isOpen, setIsOpen, isPlaying }) {
  const { getGradient, getAccentColor, getSuccessColor, getWarningColor } = useDynamicTheme()

  return (
    <button
      className="ui-tap ui-hover relative rounded-lg cursor-pointer flex items-center justify-center h-10 w-10 md:h-12 md:w-12"
      style={{
        background: getGradient(0.2),
        border: `1px solid ${getAccentColor(0.3)}`,
        color: getAccentColor(0.9),
        transition: CSS_TRANSITION.theme
      }}
      onClick={() => setIsOpen(!isOpen)}
      title="Manage devices"
    >
      <DeviceGlyph type={currentDevice?.device_type || 'desktop'} size={16} className="md:hidden" />
      <DeviceGlyph type={currentDevice?.device_type || 'desktop'} size={20} className="hidden md:block" />
      <Pop show={!!activeDevice && !activeDevice.is_current} className="absolute -top-1 -right-1">
        <FadeSwap swapKey={isPlaying ? 'playing' : 'paused'} preset={PRESETS.iconSwap} mode="wait">
          {isPlaying ? (
            <span className={DEVICE_BADGE_CLASS} style={{ background: getSuccessColor() }} title="Playing on another device">
              <Play size={8} fill="currentColor" className="ml-px" />
            </span>
          ) : (
            <span className={DEVICE_BADGE_CLASS} style={{ background: getWarningColor() }} title="Paused on another device">
              <Pause size={8} fill="currentColor" />
            </span>
          )}
        </FadeSwap>
      </Pop>
    </button>
  )
}

export const DevicePickerButton = memo(function DevicePickerButton(props) {
  const { connectionMode } = props
  return (
    <FadeSwap swapKey={connectionMode === 'full' ? 'devices' : connectionMode} preset={PRESETS.fade}>
      {connectionMode === 'full' ? <OnlineDeviceButton {...props} /> : <OfflineDeviceButton {...props} />}
    </FadeSwap>
  )
})

const DEVICE_NOTICE = 'other-device'

export const DeviceNotice = memo(function DeviceNotice({ showInactive, bannerDismissed, actionLoading, handleActivateDevice, handleDismissBanner }) {
  const { activeDeviceOnline, showNotice, hideNotice } = useUISelector(state => ({
    activeDeviceOnline: state.engineState.activeDeviceOnline,
    showNotice: state.showNotice,
    hideNotice: state.hideNotice,
  }))
  const handlersRef = useRef({ handleActivateDevice, handleDismissBanner })
  useEffect(() => {
    handlersRef.current = { handleActivateDevice, handleDismissBanner }
  })
  const visible = showInactive && !bannerDismissed

  useEffect(() => {
    if (!visible) {
      hideNotice(DEVICE_NOTICE)
      return
    }
    showNotice({
      key: DEVICE_NOTICE,
      sticky: true,
      dismissible: false,
      priority: 0,
      tone: activeDeviceOnline ? 'info' : 'warning',
      icon: activeDeviceOnline ? MonitorSpeaker : WifiOff,
      text: activeDeviceOnline ? 'Playing on another device' : 'Your other device is offline',
      content: (
        <>
          <NoticeAction onClick={() => handlersRef.current.handleActivateDevice()} disabled={actionLoading}>
            {actionLoading ? '…' : 'Play here'}
          </NoticeAction>
          <NoticeClose
            onClick={() => handlersRef.current.handleDismissBanner()}
            title="Hide (comes back on a device change)"
          />
        </>
      ),
    })
  }, [visible, activeDeviceOnline, actionLoading, showNotice, hideNotice])

  useEffect(() => () => hideNotice(DEVICE_NOTICE), [hideNotice])

  return null
})

const PlayingPill = memo(function PlayingPill({ isPlaying }) {
  return (
    <FadeSwap swapKey={isPlaying ? 'playing' : 'paused'} preset={PRESETS.badgeSwap} mode="wait" className="flex">
      {isPlaying
        ? <StatusPill tone="success" icon={Play}>Playing</StatusPill>
        : <StatusPill tone="warning" icon={Pause}>Paused</StatusPill>}
    </FadeSwap>
  )
})

function DeviceName({ device, editing, editingName, setEditingName, actionLoading, handleCancelEdit, handleSaveEdit }) {
  return (
    <FadeSwap swapKey={editing ? 'edit' : 'name'} preset={PRESETS.fade} mode="wait" className="flex items-center gap-1 flex-1 min-w-0">
      {editing ? (
        <>
          <input
            type="text"
            value={editingName}
            onChange={(e) => setEditingName(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleSaveEdit(device.device_id)
              else if (e.key === 'Escape') handleCancelEdit()
            }}
            className="flex-1 min-w-0 bg-white/10 border border-white/20 rounded-md px-2 py-1 text-white text-sm focus:outline-none focus:border-white/50"
            autoFocus
            disabled={actionLoading}
          />
          <CardIconButton icon={Check} onClick={() => handleSaveEdit(device.device_id)} title="Save" disabled={actionLoading} />
          <CardIconButton icon={X} onClick={handleCancelEdit} title="Cancel" disabled={actionLoading} />
        </>
      ) : (
        <span className="truncate min-w-0">{device.device_name}</span>
      )}
    </FadeSwap>
  )
}

const DEVICE_SHEET_TITLES = { offline: 'Offline Mode', degraded: 'Server Unavailable', full: 'Devices' }

export const DevicePickerPanel = memo(function DevicePickerPanel({ isOpen, setIsOpen, devices, loading, loadFailed, loadDevices, actionLoading, handleActivateDevice, handleRemoveDevice, editingDeviceId, editingName, setEditingName, handleStartEdit, handleCancelEdit, handleSaveEdit, connectionMode, storageInfo, isPlaying }) {
  const isFullMode = connectionMode === 'full'
  const isOffline = connectionMode === 'offline'
  const bodyState = !isFullMode ? connectionMode : loading && devices.length === 0 ? 'loading' : devices.length === 0 ? (loadFailed ? 'failed' : 'empty') : 'list'
  const cached = storageInfo?.trackCount || 0
  const nameProps = { editingName, setEditingName, actionLoading, handleCancelEdit, handleSaveEdit }

  return (
    <PlayerSheet open={isOpen} title={DEVICE_SHEET_TITLES[connectionMode] || 'Devices'} onClose={() => setIsOpen(false)}>
      <FadeSwap swapKey={bodyState} mode="wait">
        {!isFullMode ? (
          <MediaEmptyState
            compact
            icon={isOffline ? WifiOff : ServerOff}
            title={isOffline ? 'No internet connection' : "Can't reach PLAiR"}
            subtitle={`Playing from your downloads: ${cached} song${cached === 1 ? '' : 's'} on this device.`}
          />
        ) : bodyState === 'loading' ? (
          <MediaEmptyState compact icon={MonitorSpeaker} title="Finding your devices…" subtitle={null} />
        ) : bodyState === 'failed' ? (
          <MediaLoadError what="your devices" onRetry={() => void loadDevices()} compact />
        ) : bodyState === 'empty' ? (
          <MediaEmptyState compact icon={MonitorSpeaker} title="No devices online" subtitle="Open PLAiR on another device to play there." />
        ) : (
          <SheetGrid>
            {devices.map(device => (
              <OptionCard
                key={device.device_id}
                icon={deviceIcon(device.device_type)}
                selected={!!device.is_active}
                onSelect={() => handleActivateDevice(device.device_id)}
                hint={device.is_active ? 'Playing here' : 'Play here'}
                title={<DeviceName device={device} editing={editingDeviceId === device.device_id} {...nameProps} />}
                badge={(
                  <Pop show={!!device.is_active} className="flex flex-shrink-0">
                    <PlayingPill isPlaying={isPlaying} />
                  </Pop>
                )}
                subtitle={device.is_current ? 'This device' : device.last_active ? `Active ${formatTimeAgo(device.last_active)}` : 'Online'}
                trailing={editingDeviceId === device.device_id ? null : (
                  <>
                    <CardIconButton icon={Pencil} size={14} onClick={() => handleStartEdit(device.device_id, device.device_name)} title="Rename device" />
                    {!device.is_current && (
                      <CardIconButton icon={Trash2} size={14} tone="danger" onClick={() => handleRemoveDevice(device.device_id)} title="Remove device" disabled={actionLoading} />
                    )}
                  </>
                )}
              />
            ))}
          </SheetGrid>
        )}
      </FadeSwap>
    </PlayerSheet>
  )
})
