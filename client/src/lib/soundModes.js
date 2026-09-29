import { api } from './api'
import { saveGuestSettings } from './accountSettings'

export const SOUND_MODES = [
  { id: 'both', label: 'DJ + PING', short: 'DJ+PING', ttsMuted: false, notificationsMuted: false },
  { id: 'dj', label: 'DJ only', short: 'DJ', ttsMuted: false, notificationsMuted: true },
  { id: 'ping', label: 'PING only', short: 'PING', ttsMuted: true, notificationsMuted: false },
  { id: 'off', label: 'OFF', short: 'OFF', ttsMuted: true, notificationsMuted: true },
]

export function soundModeFor(ttsMuted, notificationsMuted) {
  return SOUND_MODES.find(mode => mode.ttsMuted === !!ttsMuted && mode.notificationsMuted === !!notificationsMuted)
}

export function nextSoundMode(current) {
  return SOUND_MODES[(SOUND_MODES.indexOf(current) + 1) % SOUND_MODES.length]
}

export async function applySoundMode(mode, { user, publishSettings, refreshUser }) {
  const { ttsMuted, notificationsMuted } = mode
  publishSettings({ ttsMuted, notificationsMuted })
  if (!user) {
    saveGuestSettings({ ttsMuted, notificationsMuted })
    return
  }
  try {
    await api.updateUserProfile({ tts_muted: ttsMuted, notifications_muted: notificationsMuted })
    if (refreshUser) await refreshUser()
  } catch (error) {
    publishSettings({ ttsMuted: user.tts_muted ?? false, notificationsMuted: user.notifications_muted ?? false })
    throw error
  }
}
