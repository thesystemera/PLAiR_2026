import { createContext, useContext, useState, useEffect, useLayoutEffect, useRef, useMemo, useCallback } from 'react'
import {
  Heart,
  Mic2,
  Palette,
  FileText,
  MessageSquare,
  Volume2,
  Sparkles,
  ListMusic,
  Zap,
  Disc,
  Users,
  Tag,
  User,
  MapPin,
  AlertCircle,
  Star,
  Globe,
  Smile,
  TrendingUp,
  Calendar,
  Clock
} from 'lucide-react'
import {
  extractColorsFromPixels,
  getDefaultColors,
  PANEL,
  PANEL_SCROLL,
  TRANSITIONS,
  UI_FULLSCREEN,
  BUTTON,
  CATALOG_HEADER
} from '../lib/themeManager'
import { logger } from '../lib/logger'

import { useUISelector } from './UIStateContext'
import { useDepthMap } from '../hooks/useDepthMap'
import { FULL_PACK_SIZE, blobForUrl, packCache } from '../lib/mediaCache'
import { packColorSamples, packImageUrl } from '../lib/packImage'

const IDLE_TIMEOUT_MS = 2000

function whenIdle(callback) {
  if (typeof requestIdleCallback === 'function') {
    const handle = requestIdleCallback(callback, { timeout: IDLE_TIMEOUT_MS })
    return () => cancelIdleCallback(handle)
  }
  const timer = setTimeout(callback, IDLE_TIMEOUT_MS)
  return () => clearTimeout(timer)
}

export {
  PANEL,
  PANEL_SCROLL,
  TRANSITIONS,
  UI_FULLSCREEN,
  BUTTON,
  CATALOG_HEADER
}

// Panel icons - used in Mobile Navigation, MediaSearch toggle, and MediaLoadingSpinner
export function CatalogIcon({ className = "w-6 h-6", ...props }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24" {...props}>
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
    </svg>
  )
}

export function ShoutoutsIcon({ className = "w-6 h-6", ...props }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24" {...props}>
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5.882V19.24a1.76 1.76 0 01-3.417.592l-2.147-6.15M18 13a3 3 0 100-6M5.436 13.683A4.001 4.001 0 017 6h1.832c4.1 0 7.625-1.234 9.168-3v14c-1.543-1.766-5.067-3-9.168-3H7a3.988 3.988 0 01-1.564-.317z" />
    </svg>
  )
}

// Sort mode icons - used in MediaLoadingSpinner for different catalog views
export function RecentIcon({ className = "w-6 h-6", ...props }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24" {...props}>
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  )
}

export function AlphabeticalIcon({ className = "w-6 h-6", ...props }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24" {...props}>
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 7h5m0 0v10m0-10l3 3m-3-3L5 10m13-3h-5m5 0v10m0-10l-3 3m3-3l3 3" />
    </svg>
  )
}

export function GenreIcon({ className = "w-6 h-6", ...props }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24" {...props}>
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 5a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM14 5a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1V5zM4 15a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1H5a1 1 0 01-1-1v-4zM14 15a1 1 0 011-1h4a1 1 0 011 1v4a1 1 0 01-1 1h-4a1 1 0 01-1-1v-4z" />
    </svg>
  )
}

const DynamicThemeContext = createContext(null)
const ThemeArtworkContext = createContext(null)

const CATEGORY_IDENTITY = {
  favorites: { icon: Heart, color: '#ec4899', label: 'My Favorites' },
  discovery: { icon: Sparkles, color: '#8b5cf6', label: 'Smart Discovery' },

  top_hits_all: { icon: TrendingUp, color: '#a855f7', label: 'All-Time Hits' },
  top_hits_week: { icon: Calendar, color: '#8b5cf6', label: "This Week's Hits" },
  top_hits_day: { icon: Clock, color: '#06b6d4', label: "Today's Hits" },

  song_title: { icon: Disc, color: '#8b5cf6', label: 'Song Title' },
  primary_genre: { icon: Disc, color: '#a855f7', label: 'Main Genre' },
  primary_artist: { icon: Mic2, color: '#ec4899', label: 'Artist' },
  secondary_genres: { icon: ListMusic, color: '#a855f7', label: 'Sub-Genres' },
  similar_artists: { icon: Users, color: '#d946ef', label: 'Similar Artists' },

  genre: { icon: Disc, color: '#a855f7', label: 'Main Genre' },
  mood: { icon: Heart, color: '#3b82f6', label: 'Mood' },
  artist: { icon: Mic2, color: '#ec4899', label: 'Artist' },
  style: { icon: Palette, color: '#10b981', label: 'Style' },
  theme: { icon: FileText, color: '#f59e0b', label: 'Theme' },
  vocal: { icon: Volume2, color: '#06b6d4', label: 'Vocal' },
  lyrics: { icon: MessageSquare, color: '#eab308', label: 'Lyrics' },
  instrumental: { icon: Zap, color: '#f43f5e', label: 'Instrumental' },

  tags: { icon: Tag, color: '#f59e0b', label: 'Tags' },
  category: { icon: Tag, color: '#a855f7', label: 'Category' },
  username: { icon: User, color: '#ec4899', label: 'User' },
  location: { icon: MapPin, color: '#3b82f6', label: 'Location' },
  urgency: { icon: AlertCircle, color: '#ef4444', label: 'Urgent' },
  importance: { icon: Star, color: '#eab308', label: 'Important' },
  target_audience: { icon: Globe, color: '#06b6d4', label: 'Audience' },
  sentiment: { icon: Smile, color: '#10b981', label: 'Sentiment' },
  transcription: { icon: FileText, color: '#6b7280', label: 'Content' },
  content_theme: { icon: MessageSquare, color: '#8b5cf6', label: 'Theme' },

  general: { icon: Sparkles, color: '#6b7280', label: 'General' }
}

export function useDynamicTheme() {
  const context = useContext(DynamicThemeContext)
  if (!context) {
    throw new Error('useDynamicTheme must be used within DynamicThemeProvider')
  }
  return context
}

export function useThemeArtwork() {
  return useContext(ThemeArtworkContext)
}

export function getCategoryMetadata(key) {
  const k = key?.toLowerCase() || 'general'

  if (k === 'favorites') return { ...CATEGORY_IDENTITY.favorites, id: key }
  if (k === 'discovery') return { ...CATEGORY_IDENTITY.discovery, id: key }

  if (k === 'top_hits_all') return { ...CATEGORY_IDENTITY.top_hits_all, id: key }
  if (k === 'top_hits_week') return { ...CATEGORY_IDENTITY.top_hits_week, id: key }
  if (k === 'top_hits_day') return { ...CATEGORY_IDENTITY.top_hits_day, id: key }

  if (k === 'song_title') return { ...CATEGORY_IDENTITY.song_title, id: key }
  if (k === 'primary_genre') return { ...CATEGORY_IDENTITY.primary_genre, id: key }
  if (k === 'primary_artist') return { ...CATEGORY_IDENTITY.primary_artist, id: key }
  if (k === 'secondary_genres') return { ...CATEGORY_IDENTITY.secondary_genres, id: key }
  if (k === 'similar_artists') return { ...CATEGORY_IDENTITY.similar_artists, id: key }

  if (k === 'transcription') return { ...CATEGORY_IDENTITY.transcription, id: key }
  if (k === 'category') return { ...CATEGORY_IDENTITY.category, id: key }
  if (k === 'tags') return { ...CATEGORY_IDENTITY.tags, id: key }
  if (k === 'username') return { ...CATEGORY_IDENTITY.username, id: key }
  if (k === 'location') return { ...CATEGORY_IDENTITY.location, id: key }
  if (k === 'urgency') return { ...CATEGORY_IDENTITY.urgency, id: key }
  if (k === 'importance') return { ...CATEGORY_IDENTITY.importance, id: key }
  if (k === 'target_audience') return { ...CATEGORY_IDENTITY.target_audience, id: key }
  if (k === 'sentiment') return { ...CATEGORY_IDENTITY.sentiment, id: key }
  if (k === 'content_theme') return { ...CATEGORY_IDENTITY.content_theme, id: key }

  if (k.includes('secondary') || k.includes('sub')) {
    return { ...CATEGORY_IDENTITY.secondary_genres, id: key }
  }
  if (k.includes('similar_artist')) {
    return { ...CATEGORY_IDENTITY.similar_artists, id: key }
  }

  if (k.includes('genre')) return { ...CATEGORY_IDENTITY.genre, id: key }
  if (k.includes('mood')) return { ...CATEGORY_IDENTITY.mood, id: key }
  if (k.includes('artist')) return { ...CATEGORY_IDENTITY.artist, id: key }
  if (k.includes('style')) return { ...CATEGORY_IDENTITY.style, id: key }
  if (k.includes('theme')) return { ...CATEGORY_IDENTITY.theme, id: key }
  if (k.includes('lyric')) return { ...CATEGORY_IDENTITY.lyrics, id: key }
  if (k.includes('vocal')) return { ...CATEGORY_IDENTITY.vocal, id: key }
  if (k === 'all') return { ...CATEGORY_IDENTITY.general, label: 'All Categories', description: 'Balanced mix', id: key }

  return null
}

const FILTER_KEYS = ['filterAllActive', 'filterInteractiveActive', 'filterAnnouncerActive', 'filterExternalActive', 'filterShoutoutsActive', 'filterInactive']
const kebab = name => name.replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`)
const themeVar = name => `var(--theme-${kebab(name)})`
const channels = ({ r, g, b }) => `${r} ${g} ${b}`

const FILTER_STYLES = Object.fromEntries(FILTER_KEYS.map(key => [key, {
  background: themeVar(`${key}Background`),
  color: themeVar(`${key}Color`),
  borderColor: themeVar(`${key}BorderColor`),
}]))

function themeCssVars(theme) {
  const vars = {}
  for (const [key, value] of Object.entries(theme)) {
    if (typeof value === 'string') vars[`--theme-${kebab(key)}`] = value
  }
  for (const key of FILTER_KEYS) {
    for (const [property, value] of Object.entries(theme[key] || {})) vars[`--theme-${kebab(key)}-${kebab(property)}`] = value
  }
  const rgb = theme._rgb || {}
  if (rgb.accentRgb) vars['--theme-accent-rgb'] = channels(rgb.accentRgb)
  if (rgb.primaryRgb) vars['--theme-primary-rgb'] = channels(rgb.primaryRgb)
  if (rgb.secondaryRgb) vars['--theme-secondary-rgb'] = channels(rgb.secondaryRgb)
  return vars
}

export function DynamicThemeProvider({ children }) {
  const currentTrack = useUISelector(state => state.engineState?.currentTrack)

  const [colors, setColors] = useState(null)
  const [currentArtwork, setCurrentArtwork] = useState(null)
  const processedArtworkUrl = useRef(null)

  const interactionEffectsRef = useRef({
    click: { active: false, x: 0, y: 0, intensity: 0, timestamp: 0 },
    scroll: { velocity: 0, direction: 'vertical', lastY: 0, lastTime: 0 },
    reselect: { active: false, targetId: null, color: null, timestamp: 0 }
  })

  const trackId = currentTrack?.id
  const hasArtwork = !!trackId && currentTrack?.has_artwork !== false
  const { url: packUrl } = useDepthMap(packCache(FULL_PACK_SIZE), trackId, hasArtwork)

  useEffect(() => {
    if (!trackId) return
    if (!hasArtwork) {
      processedArtworkUrl.current = null
      queueMicrotask(() => {
        setCurrentArtwork(null)
        setColors(null)
      })
      return
    }
    const blob = packUrl ? blobForUrl(packUrl) : null
    if (!blob || packUrl === processedArtworkUrl.current || packCache(FULL_PACK_SIZE).peekMemory(trackId) !== packUrl) return
    processedArtworkUrl.current = packUrl

    let cancelled = false
    let idle = null
    packColorSamples(blob)
      .then((samples) => {
        if (cancelled) return
        setColors(extractColorsFromPixels(samples))
        idle = whenIdle(() => {
          packImageUrl(blob).then((imageUrl) => {
            if (cancelled) {
              URL.revokeObjectURL(imageUrl)
              return
            }
            setCurrentArtwork({ key: packUrl, imageUrl, trackId })
          })
        })
      })
      .catch((error) => {
        logger.error('Failed to read artwork pack:', error)
        if (processedArtworkUrl.current === packUrl) processedArtworkUrl.current = null
      })
    return () => {
      cancelled = true
      idle?.()
      if (processedArtworkUrl.current === packUrl) processedArtworkUrl.current = null
    }
  }, [trackId, hasArtwork, packUrl])

  useEffect(() => {
    const imageUrl = currentArtwork?.imageUrl
    return () => {
      if (imageUrl) URL.revokeObjectURL(imageUrl)
    }
  }, [currentArtwork])

  const triggerEffect = useCallback((type, options = {}) => {
    const effect = interactionEffectsRef.current[type]
    if (!effect) return

    switch (type) {
      case 'click':
        effect.active = true
        effect.x = options.x ?? 0.5
        effect.y = options.y ?? 0.5
        effect.intensity = options.intensity ?? 1.0
        effect.timestamp = performance.now()
        break

      case 'scroll': {
        const currentTime = performance.now()
        const deltaY = (options.scrollTop ?? 0) - effect.lastY
        const deltaTime = Math.max(currentTime - effect.lastTime, 1)
        const velocity = Math.abs(deltaY) / deltaTime

        effect.velocity = Math.min(velocity * 0.1, 2.0)
        effect.direction = deltaY > 0 ? 'down' : 'up'
        effect.lastY = options.scrollTop ?? 0
        effect.lastTime = currentTime
        break
      }

      case 'reselect':
        effect.active = true
        effect.targetId = options.targetId
        effect.color = options.color
        effect.timestamp = performance.now()
        break
    }
  }, [])

  useEffect(() => {
    const root = document.documentElement
    const accent = colors?.accent
    if (accent) {
      root.style.setProperty('--theme-accent-85', accent.replace('1)', '0.85)'))
      root.style.setProperty('--theme-accent-60', accent.replace('1)', '0.6)'))
    } else {
      root.style.removeProperty('--theme-accent-85')
      root.style.removeProperty('--theme-accent-60')
    }
  }, [colors])

  const colorsRef = useRef(null)

  useLayoutEffect(() => {
    const theme = colors || getDefaultColors()
    colorsRef.current = theme
    const style = document.documentElement.style
    for (const [name, value] of Object.entries(themeCssVars(theme))) style.setProperty(name, value)
  }, [colors])

  const getters = useMemo(() => ({
    getCategoryMetadata,

    getGradient: (opacity) => opacity === undefined
      ? themeVar('gradient')
      : `linear-gradient(135deg, rgb(var(--theme-primary-rgb) / ${opacity}), rgb(var(--theme-secondary-rgb) / 0.3))`,
    getAccentColor: (opacity = 1) => `rgb(var(--theme-accent-rgb) / ${opacity})`,
    getAccentRgb: () => colorsRef.current?._rgb?.accentRgb || { r: 139, g: 92, b: 246 },

    getBorder: () => themeVar('border'),
    getPanelBorder: () => themeVar('panelBorder'),
    getInputBorder: () => themeVar('inputBorder'),
    getSubtleBorder: () => themeVar('subtleBorder'),
    getActiveBorder: () => themeVar('activeBorder'),
    getBorderColor: () => themeVar('border'),

    getPrimaryText: () => themeVar('primaryText'),
    getSecondaryText: () => themeVar('secondaryText'),
    getMutedText: () => themeVar('mutedText'),
    getTertiaryText: () => themeVar('tertiaryText'),
    getWhite: () => themeVar('primaryText'),

    getAppBackground: () => themeVar('appBackground'),
    getCardBackground: () => themeVar('cardBackground'),
    getCardHoverBackground: () => themeVar('cardHoverBackground'),
    getPanelBackground: () => themeVar('panelBackground'),
    getInputBackground: () => themeVar('inputBackground'),
    getOverlayBackground: () => themeVar('overlayBackground'),
    getButtonHoverBg: () => themeVar('buttonHoverBg'),
    getButtonActiveBg: () => themeVar('buttonActiveBg'),
    getButtonInactiveBg: () => themeVar('buttonInactiveBg'),

    getGrey800: () => themeVar('panelBackground'),
    getGrey700: () => themeVar('cardHoverBackground'),
    getGrey500: () => themeVar('border'),
    getGrey400: () => themeVar('secondaryText'),
    getGrey300: () => themeVar('mutedText'),
    getLightGrey: () => themeVar('mutedText'),

    getPlayingBackground: () => themeVar('playingBackground'),
    getPlayingRingColor: () => themeVar('playingRing'),
    getQueuedRingColor: () => themeVar('queuedRing'),
    getLoadingSpinner: () => themeVar('loadingSpinner'),

    getPurpleBase: () => themeVar('purpleBase'),
    getPurpleDark: () => themeVar('purpleDark'),
    getPurpleLight: () => themeVar('purpleLight'),

    getRadioButtonBase: () => themeVar('radioButtonBase'),
    getRadioButtonBaseRgb: () => colorsRef.current?._rgb?.radioButtonBaseRgb || { r: 34, g: 197, b: 94 },

    getPrimaryActionText: () => themeVar('primaryActionText'),
    getPrimaryActionHover: () => themeVar('primaryActionHover'),
    getDangerActionText: () => themeVar('dangerActionText'),
    getDangerActionBg: () => themeVar('dangerActionBg'),

    getSuccessColor: () => themeVar('success'),
    getErrorColor: () => themeVar('error'),
    getWarningColor: () => themeVar('warning'),
    getInfoColor: () => themeVar('info'),

    getNetworkExcellent: () => themeVar('networkExcellent'),
    getNetworkGood: () => themeVar('networkGood'),
    getNetworkFair: () => themeVar('networkFair'),
    getNetworkPoor: () => themeVar('networkPoor'),

    getLikeBadgeBg: () => themeVar('likeBadgeBg'),
    getSuperLikeBadgeBg: () => themeVar('superLikeBadgeBg'),
    getBanBadgeBg: () => themeVar('banBadgeBg'),

    getFilterAllActive: () => FILTER_STYLES.filterAllActive,
    getFilterInteractiveActive: () => FILTER_STYLES.filterInteractiveActive,
    getFilterAnnouncerActive: () => FILTER_STYLES.filterAnnouncerActive,
    getFilterExternalActive: () => FILTER_STYLES.filterExternalActive,
    getFilterShoutoutsActive: () => FILTER_STYLES.filterShoutoutsActive,
    getFilterInactive: () => FILTER_STYLES.filterInactive,

    getUserAvatarGradient: () => themeVar('userAvatarGradient'),
    getPremiumGradient: () => themeVar('premiumGradient')
  }), [])

  const value = useMemo(() => ({
    interactionEffectsRef,
    triggerEffect,
    ...getters
  }), [getters, triggerEffect])

  return (
    <ThemeArtworkContext.Provider value={currentArtwork}>
      <DynamicThemeContext.Provider value={value}>
        {children}
      </DynamicThemeContext.Provider>
    </ThemeArtworkContext.Provider>
  )
}