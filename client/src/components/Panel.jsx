import { motion } from 'framer-motion'
import { PANEL, TRANSITIONS, useDynamicTheme, CatalogIcon, ShoutoutsIcon } from '../contexts/DynamicThemeContext'
import { useUISelector, GLASS_EFFECT_CONFIG } from '../contexts/UIStateContext'
import { EDGE_FADE_MASK } from '../lib/themeManager'
import { PRESETS } from '../lib/motion'

const CURVED_BACKDROP_MASK = EDGE_FADE_MASK('8px', '8px', '8px')

// Re-export for use by other components (MediaStatsOverlay, MediaSearch)
export { GLASS_EFFECT_CONFIG }

/**
 * Curved backdrop with edge feathering on all four sides.
 * Same pattern as Scroller: intersected gradients + border-radius for curves.
 * No top-to-bottom gradient - just consistent backdrop with edge fade.
 * 12px corner radius, 8px feather on all sides.
 */
export function CurvedBackdrop({ baseOpacity = 0.9, className = '' }) {
  const r = 12
  const mask = CURVED_BACKDROP_MASK

  return (
    <div
      className={`absolute inset-0 pointer-events-none ${className}`}
      style={{
        borderRadius: `${r}px`,
        background: `rgba(0,0,0,${baseOpacity})`,
        maskImage: mask,
        WebkitMaskImage: mask,
        maskComposite: 'intersect',
        WebkitMaskComposite: 'source-in'
      }}
    />
  )
}

export const PANEL_IDS = {
  QUEUE: 'queue',
  CATALOG: 'catalog',
  RADIO: 'radio',
  NOW_PLAYING: 'nowPlaying',
  USER: 'user',
  PLAYER: 'player',
  SHOUTOUTS: 'shoutouts'
}

export function TextRadioIcon({ className = 'w-6 h-6 mb-1' }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7.227 16.774a6.75 6.75 0 010-9.546m9.546 0a6.75 6.75 0 010 9.546M5.106 18.894c-3.808-3.808-3.808-9.98 0-13.789m13.788 0c3.808 3.808 3.808 9.981 0 13.79" />
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M9.5 10.25c0-.414.336-.75.75-.75h3.5c.414 0 .75.336.75.75v2c0 .414-.336.75-.75.75H11.5l-1.25 1v-1c-.414 0-.75-.336-.75-.75v-2z" />
    </svg>
  )
}

const PANEL_TRANSITION = TRANSITIONS.panel

export const PANEL_FADE_TRANSITION = TRANSITIONS.fade

export const getPanelPointerEvents = (isVisible) => ({
  pointerEvents: isVisible ? 'auto' : 'none'
})

export const PANEL_CONFIG = {
  [PANEL_IDS.QUEUE]: {
    id: 'queue',
    label: 'Queue',
    mobileLabel: 'Queue',
    isFlexible: true,
    icon: (
      <svg className="w-6 h-6 text-gray-400 group-hover:text-white transition" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 10h16M4 14h16M4 18h16" />
      </svg>
    ),
    mobileIcon: (className = "w-6 h-6 mb-1") => (
      <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 10h16M4 14h16M4 18h16" />
      </svg>
    )
  },
  [PANEL_IDS.CATALOG]: {
    id: 'catalog',
    label: 'Catalog',
    mobileLabel: 'Catalog',
    isFlexible: true,
    icon: (
      <CatalogIcon className="w-6 h-6 text-gray-400 group-hover:text-white transition" />
    ),
    mobileIcon: (className = "w-6 h-6 mb-1") => (
      <CatalogIcon className={className} />
    )
  },
  [PANEL_IDS.RADIO]: {
    id: 'radio',
    label: 'Radio',
    mobileLabel: 'Radio',
    isFlexible: true,
    icon: (
      <svg className="w-6 h-6 text-gray-400 group-hover:text-white transition" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.348 14.652a3.75 3.75 0 010-5.304m5.304 0a3.75 3.75 0 010 5.304m-7.425 2.122a6.75 6.75 0 010-9.546m9.546 0a6.75 6.75 0 010 9.546M5.106 18.894c-3.808-3.808-3.808-9.98 0-13.789m13.788 0c3.808 3.808 3.808 9.981 0 13.79M12 12h.008v.007H12V12zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0z" />
      </svg>
    ),
    mobileIcon: (className = "w-6 h-6 mb-1") => (
      <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.348 14.652a3.75 3.75 0 010-5.304m5.304 0a3.75 3.75 0 010 5.304m-7.425 2.122a6.75 6.75 0 010-9.546m9.546 0a6.75 6.75 0 010 9.546M5.106 18.894c-3.808-3.808-3.808-9.98 0-13.789m13.788 0c3.808 3.808 3.808 9.981 0 13.79M12 12h.008v.007H12V12zm.375 0a.375.375 0 11-.75 0 .375.375 0 01.75 0z" />
      </svg>
    )
  },
  [PANEL_IDS.NOW_PLAYING]: {
    id: 'nowPlaying',
    label: 'Playing',
    mobileLabel: 'Playing',
    isFlexible: true,
    icon: (
      <svg className="w-6 h-6 text-gray-400 group-hover:text-white transition" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
      </svg>
    ),
    mobileIcon: (className = "w-6 h-6 mb-1") => (
      <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
      </svg>
    )
  },
  [PANEL_IDS.USER]: {
    id: 'user',
    label: 'User',
    mobileLabel: 'Account',
    isFlexible: true,
    icon: (
      <svg className="w-6 h-6 text-gray-400 group-hover:text-white transition" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
      </svg>
    ),
    mobileIcon: (className = "w-6 h-6 mb-1") => (
      <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
      </svg>
    )
  },
  [PANEL_IDS.SHOUTOUTS]: {
    id: 'shoutouts',
    label: 'Shoutouts',
    mobileLabel: 'Shoutouts',
    isFlexible: true,
    icon: (
      <ShoutoutsIcon className="w-6 h-6 text-gray-400 group-hover:text-white transition" />
    ),
    mobileIcon: (className = "w-6 h-6 mb-1") => (
      <ShoutoutsIcon className={className} />
    )
  }
}

export function PanelHeader({ title, children, className = '' }) {
  const isScrolling = useUISelector(state => state.interfaceState.isScrolling)

  return (
    <motion.div
      className={`absolute top-0 left-0 right-0 z-10 ${className}`}
      style={{ height: `${PANEL.headerHeight}px` }}
      animate={{
        opacity: isScrolling ? 0.15 : 1
      }}
      transition={TRANSITIONS.fade}
    >
      <CurvedBackdrop baseOpacity={GLASS_EFFECT_CONFIG.opacity.panelHeader} />
      <div className="relative z-10 h-full px-4 md:px-6 flex items-center justify-between">
        {typeof title === 'string' ? (
          <h2 className="text-lg md:text-xl font-bold">{title}</h2>
        ) : (
          title
        )}
        {children}
      </div>
    </motion.div>
  )
}

export function Panel({
  id,
  isOpen,
  onToggle,
  icon,
  label,
  children,
  collapsedWidth = '4%',
  expandedWidth = 'auto',
  isFlexible = false,
  contentWidth = null
}) {
  const { getSecondaryText, getPrimaryText } = useDynamicTheme()

  return (
    <motion.div
      data-shader-panel={id}
      animate={{
        width: isOpen ? expandedWidth : collapsedWidth,
        opacity: 1
      }}
      style={{
        flex: isOpen && isFlexible ? 1 : undefined
      }}
      transition={PANEL_TRANSITION}
      className="flex-shrink-0 overflow-hidden border border-white/10 rounded-lg relative group cursor-pointer"
      onClick={onToggle}
    >
      {isOpen ? (
        <motion.div
          initial={PRESETS.fade.initial}
          animate={PRESETS.fade.animate}
          className="h-full flex flex-col relative"
          onClick={(e) => e.stopPropagation()}
          style={{
            contain: 'layout paint',
            width: contentWidth ?? undefined
          }}
        >
          {children}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 0 }}
            whileHover={{ opacity: 1 }}
            transition={PANEL_TRANSITION}
            className="absolute top-0 right-0 bottom-0 w-8 cursor-pointer flex items-center justify-center z-50"
            style={{ pointerEvents: 'auto' }}
            onClick={(e) => {
              e.stopPropagation()
              onToggle()
            }}
          >
            <motion.div
              whileHover={{ scale: 1.2 }}
              transition={PANEL_TRANSITION}
              className="w-1 h-16 bg-white/20 rounded-full"
            />
          </motion.div>
        </motion.div>
      ) : (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={PANEL_TRANSITION}
          className="h-full flex flex-col items-center justify-center gap-2 py-4"
        >
          {icon}
          <span
            className="text-xs transition writing-mode-vertical"
            style={{ color: getSecondaryText() }}
            onMouseEnter={(e) => e.currentTarget.style.color = getPrimaryText()}
            onMouseLeave={(e) => e.currentTarget.style.color = getSecondaryText()}
          >
            {label}
          </span>
        </motion.div>
      )}
    </motion.div>
  )
}