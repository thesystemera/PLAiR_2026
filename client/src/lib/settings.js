import SCHEMA from './settingsSchema.json'
import { safeStorage } from './safeStorage'

const STORAGE_KEY = 'plair_settings'
const LEGACY_KEYS = {
  dataSaverMode: 'dataSaverMode',
  costTickerEnabled: 'costTicker',
  autoClaimOnOpen: 'autoClaimOnOpen',
  backgroundDownloads: 'backgroundDownloads',
}
const LEGACY_RADIO_INPUT_KEY = 'radioInputMode'
const LEGACY_SOUND_KEY = 'guestSoundSettings'
const LEGACY_TILT_KEY = 'tiltEffects'

const SETTING_KEYS = Object.keys(SCHEMA)
const DEFAULT_SETTINGS = Object.freeze(Object.fromEntries(SETTING_KEYS.map(key => [key, SCHEMA[key].default])))

function isValidSetting(key, value) {
  const spec = SCHEMA[key]
  if (!spec || typeof value !== typeof spec.default) return false
  return !spec.options || spec.options.includes(value)
}

export function pickValidSettings(source) {
  const valid = {}
  for (const [key, value] of Object.entries(source || {})) {
    if (isValidSetting(key, value)) valid[key] = value
  }
  return valid
}

function readLegacySettings() {
  const legacy = {}
  for (const [key, storageKey] of Object.entries(LEGACY_KEYS)) {
    const raw = safeStorage.get(storageKey)
    if (raw === 'true' || raw === 'false') legacy[key] = raw === 'true'
  }
  const radioInput = safeStorage.get(LEGACY_RADIO_INPUT_KEY)
  if (radioInput === 'voice' || radioInput === 'text') legacy.radioInput = radioInput
  return { ...pickValidSettings(parseJson(safeStorage.get(LEGACY_SOUND_KEY))), ...pickValidSettings(legacy) }
}

function removeLegacySettings() {
  for (const storageKey of Object.values(LEGACY_KEYS)) safeStorage.remove(storageKey)
  safeStorage.remove(LEGACY_RADIO_INPUT_KEY)
  safeStorage.remove(LEGACY_SOUND_KEY)
  safeStorage.remove(LEGACY_TILT_KEY)
}

function parseJson(raw) {
  try {
    return JSON.parse(raw || '{}')
  } catch {
    return {}
  }
}

export function loadLocalSettings() {
  const raw = safeStorage.get(STORAGE_KEY)
  if (raw) return { ...DEFAULT_SETTINGS, ...pickValidSettings(parseJson(raw)) }
  const settings = { ...DEFAULT_SETTINGS, ...readLegacySettings() }
  saveLocalSettings(settings)
  removeLegacySettings()
  return settings
}

export function saveLocalSettings(settings) {
  safeStorage.set(STORAGE_KEY, JSON.stringify(pickValidSettings(settings)))
}

let settingsListener = null

export function onSettingsChanged(listener) {
  settingsListener = listener
  return () => {
    if (settingsListener === listener) settingsListener = null
  }
}

export function notifySettingsChanged(changes) {
  settingsListener?.(changes)
}
