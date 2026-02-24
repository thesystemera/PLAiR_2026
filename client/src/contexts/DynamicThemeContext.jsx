import { createContext, useContext, useState, useEffect, useRef, useMemo, useCallback } from 'react'
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
  extractColorsFromImage,
  SPACING,
  PANEL,
  PANEL_SCROLL,
  TRANSITIONS,
  ANIMATION,
  UI_FULLSCREEN,
  BUTTON,
  CATALOG_HEADER
} from '../lib/themeManager'
import { logger } from '../lib/logger'

import { useUIState, useArtwork } from './UIStateContext'

export {
  SPACING,
  PANEL,
  PANEL_SCROLL,
  TRANSITIONS,
  ANIMATION,
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

export function DynamicThemeProvider({ children }) {
  const { engineState } = useUIState()
  const currentTrack = engineState?.currentTrack

  const [colors, setColors] = useState(null)
  const [currentArtwork, setCurrentArtwork] = useState(null)
  const [isTransitioning, setIsTransitioning] = useState(false)
  const processedArtworkUrl = useRef(null)

  const interactionEffectsRef = useRef({
    click: { active: false, x: 0, y: 0, intensity: 0, timestamp: 0 },
    scroll: { velocity: 0, direction: 'vertical', lastY: 0, lastTime: 0 },
    reselect: { active: false, targetId: null, color: null, timestamp: 0 }
  })

  const artworkUrl = useArtwork(currentTrack?.id, currentTrack?.has_artwork)

  useEffect(() => {
    if (!currentTrack) return

    if (artworkUrl && artworkUrl === processedArtworkUrl.current) {
      return
    }

    const updateTheme = async () => {
      if (currentTrack.has_artwork && artworkUrl) {
        processedArtworkUrl.current = artworkUrl
        setIsTransitioning(true)

        try {
          const extractedColors = await extractColorsFromImage(artworkUrl)
          setColors(extractedColors)
          setCurrentArtwork(artworkUrl)
        } catch (error) {
          logger.error('Failed to extract colors:', error)
          setCurrentArtwork(artworkUrl)
        }

        setTimeout(() => setIsTransitioning(false), 700)

      }
      else if (!currentTrack.has_artwork) {
        processedArtworkUrl.current = null
        setIsTransitioning(true)

        setCurrentArtwork(null)
        setColors(null)

        setTimeout(() => setIsTransitioning(false), 700)
      }
    }

    void updateTheme()
  }, [currentTrack, artworkUrl])

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

  const getCategoryMetadata = useMemo(() => (key) => {
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
  }, [])

  const getters = useMemo(() => ({
    getCategoryMetadata,

    getGradient: (opacity) => opacity === undefined ? colors?.gradient : colors?.gradient?.replace('0.3)', `${opacity})`),
    getAccentColor: (opacity = 1) => colors?.accent ? colors.accent.replace('1)', `${opacity})`) : colors?.accent,
    getAccentRgb: () => colors?._rgb?.accentRgb || { r: 139, g: 92, b: 246 },

    getBorder: (opacity) => opacity ? colors?.borderColorRgb ? `rgba(${colors.borderColorRgb.r}, ${colors.borderColorRgb.g}, ${colors.borderColorRgb.b}, ${opacity})` : colors?.border : colors?.border,
    getPanelBorder: () => colors?.panelBorder,
    getInputBorder: () => colors?.inputBorder,
    getSubtleBorder: () => colors?.subtleBorder,
    getActiveBorder: () => colors?.activeBorder,
    getBorderColor: () => colors?.border,

    getPrimaryText: () => colors?.primaryText,
    getSecondaryText: () => colors?.secondaryText,
    getMutedText: () => colors?.mutedText,
    getTertiaryText: () => colors?.tertiaryText,
    getWhite: () => colors?.primaryText,

    getAppBackground: () => colors?.appBackground,
    getCardBackground: () => colors?.cardBackground,
    getCardHoverBackground: () => colors?.cardHoverBackground,
    getPanelBackground: () => colors?.panelBackground,
    getInputBackground: () => colors?.inputBackground,
    getOverlayBackground: () => colors?.overlayBackground,
    getButtonHoverBg: () => colors?.buttonHoverBg,
    getButtonActiveBg: () => colors?.buttonActiveBg,
    getButtonInactiveBg: () => colors?.buttonInactiveBg,

    getGrey800: () => colors?.panelBackground,
    getGrey700: () => colors?.cardHoverBackground,
    getGrey500: () => colors?.border,
    getGrey400: () => colors?.secondaryText,
    getGrey300: () => colors?.mutedText,
    getLightGrey: () => colors?.mutedText,

    getPlayingBackground: () => colors?.playingBackground,
    getPlayingRingColor: () => colors?.playingRing,
    getQueuedRingColor: () => colors?.queuedRing,
    getLoadingSpinner: () => colors?.loadingSpinner,

    getPurpleBase: () => colors?.purpleBase,
    getPurpleDark: () => colors?.purpleDark,
    getPurpleLight: () => colors?.purpleLight,

    getRadioButtonBase: () => colors?.radioButtonBase,
    getRadioButtonBaseRgb: () => colors?._rgb?.radioButtonBaseRgb || { r: 34, g: 197, b: 94 },

    getPrimaryActionText: () => colors?.primaryActionText,
    getPrimaryActionHover: () => colors?.primaryActionHover,
    getDangerActionText: () => colors?.dangerActionText,
    getDangerActionBg: () => colors?.dangerActionBg,

    getSuccessColor: () => colors?.success,
    getErrorColor: () => colors?.error,
    getWarningColor: () => colors?.warning,
    getInfoColor: () => colors?.info,

    getNetworkExcellent: () => colors?.networkExcellent,
    getNetworkGood: () => colors?.networkGood,
    getNetworkFair: () => colors?.networkFair,
    getNetworkPoor: () => colors?.networkPoor,

    getLikeBadgeBg: () => colors?.likeBadgeBg,
    getSuperLikeBadgeBg: () => colors?.superLikeBadgeBg,
    getBanBadgeBg: () => colors?.banBadgeBg,

    getFilterAllActive: () => colors?.filterAllActive,
    getFilterInteractiveActive: () => colors?.filterInteractiveActive,
    getFilterAnnouncerActive: () => colors?.filterAnnouncerActive,
    getFilterExternalActive: () => colors?.filterExternalActive,
    getFilterShoutoutsActive: () => colors?.filterShoutoutsActive,
    getFilterInactive: () => colors?.filterInactive,

    getUserAvatarGradient: () => colors?.userAvatarGradient,
    getPremiumGradient: () => colors?.premiumGradient
  }), [colors, getCategoryMetadata])

  const value = useMemo(() => ({
    currentArtwork,
    colors,
    isTransitioning,
    interactionEffectsRef,
    triggerEffect,
    ...getters
  }), [currentArtwork, colors, isTransitioning, getters, triggerEffect])

  return (
    <DynamicThemeContext.Provider value={value}>
      {children}
    </DynamicThemeContext.Provider>
  )
}