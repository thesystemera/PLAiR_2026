import { safeStorage } from './safeStorage'

const GUEST_SETTINGS_KEY = 'guestSoundSettings'
const GUEST_SETTING_KEYS = ['ttsMuted', 'notificationsMuted']

export function loadGuestSettings() {
  try {
    const stored = JSON.parse(safeStorage.get(GUEST_SETTINGS_KEY) || '{}')
    return Object.fromEntries(GUEST_SETTING_KEYS.filter(key => typeof stored[key] === 'boolean').map(key => [key, stored[key]]))
  } catch {
    return {}
  }
}

export function saveGuestSettings(updates) {
  safeStorage.set(GUEST_SETTINGS_KEY, JSON.stringify({ ...loadGuestSettings(), ...updates }))
}

export const ACCOUNT_SETTING_DEFAULTS = Object.freeze({
  ttsMuted: false,
  notificationsMuted: false,
  audioQuality: 'auto',
  fpsEnabled: false,
  videoClipsEnabled: false,
  visualQuality: 'high',
})

const ACCOUNT_SETTING_FIELDS = {
  tts_muted: 'ttsMuted',
  notifications_muted: 'notificationsMuted',
  audio_quality: 'audioQuality',
  fps_enabled: 'fpsEnabled',
  video_clips_enabled: 'videoClipsEnabled',
  visual_quality: 'visualQuality',
}

export function settingsFromAccount(source) {
  const settings = {}
  if (!source) return settings
  for (const [field, key] of Object.entries(ACCOUNT_SETTING_FIELDS)) {
    if (source[field] !== undefined && source[field] !== null) settings[key] = source[field]
  }
  return settings
}
