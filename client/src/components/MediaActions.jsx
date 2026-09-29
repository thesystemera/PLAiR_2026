import { logger } from '../lib/logger'
import { Heart, Star, Ban } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import { usePreferences } from '../contexts/PreferencesContext'
import { useUIActions } from '../contexts/UIStateContext'
import { useDynamicTheme } from '../contexts/DynamicThemeContext'
import { triggerHaptic } from '../lib/haptics'
import { usePointerInteraction } from '../hooks/usePointerInteraction'
import { CSS_TRANSITION } from '../lib/motion'
import { burst, nope, pop } from '../lib/microMotion'

const BURST_COLORS = {
  like: '#ec4899',
  super_like: '#facc15'
}

const ACTIONS = [
  { type: 'like', icon: Heart, title: 'Like', activeClassName: 'bg-pink-600 text-white', fill: true },
  { type: 'super_like', icon: Star, title: 'Super Like', activeClassName: 'bg-yellow-600 text-white', fill: true },
  { type: 'ban', icon: Ban, title: 'Ban', activeClassName: 'bg-red-600 text-white', fill: false }
]

function ActionIcon({ icon: Icon, size, on, fill }) {
  if (!fill) {
    return (
      <span className="inline-flex" data-icon>
        <Icon size={size} />
      </span>
    )
  }
  return (
    <span className="ui-fill" data-on={on ? 'true' : 'false'} data-icon>
      <Icon size={size} />
      <Icon size={size} fill="currentColor" className="ui-fill-on" />
    </span>
  )
}

export default function MediaActions({ type = 'track', itemId, compact = false, overlay = false }) {
  const { isAuthenticated } = useAuth()
  const { getPreference, setPreference, removePreference } = usePreferences()
  const { toastError } = useUIActions()
  const { getGrey800, getGrey700, getGrey400, triggerEffect } = useDynamicTheme()
  const likeInteraction = usePointerInteraction()
  const superLikeInteraction = usePointerInteraction()
  const banInteraction = usePointerInteraction()
  const interactions = { like: likeInteraction, super_like: superLikeInteraction, ban: banInteraction }

  const preference = getPreference(type, itemId)

  const handleAction = async (e, preferenceType, interaction) => {
    e?.preventDefault()
    e?.stopPropagation()

    if (!isAuthenticated) return
    if (!interaction.shouldTrigger()) return

    const removing = preference === preferenceType
    const button = e?.currentTarget
    const icon = button?.querySelector('[data-icon]')

    if (e.clientX !== undefined && e.clientY !== undefined) {
      triggerEffect('click', {
        x: e.clientX / window.innerWidth,
        y: e.clientY / window.innerHeight,
        intensity: 0.5
      })
    }

    if (preferenceType === 'ban') {
      triggerHaptic('error')
      if (!removing) nope(icon)
    } else {
      triggerHaptic('success')
      if (!removing) {
        pop(icon)
        burst(button, BURST_COLORS[preferenceType])
      }
    }

    try {
      if (removing) {
        await removePreference(type, itemId)
      } else {
        await setPreference(type, itemId, preferenceType)
      }
    } catch (err) {
      logger.error(`Failed to set ${type} preference:`, err)
      toastError('Failed to update preference. Please try again.')
    }
  }

  if (!isAuthenticated) {
    return null
  }

  const baseButtonStyle = (compact || overlay) ? {
    backgroundColor: 'rgba(0, 0, 0, 0.6)',
    color: 'rgba(255, 255, 255, 0.9)',
    transition: CSS_TRANSITION.quick
  } : {
    backgroundColor: getGrey800(),
    color: getGrey400(),
    transition: CSS_TRANSITION.theme
  }

  const hoverStyle = (compact || overlay) ? {
    backgroundColor: 'rgba(0, 0, 0, 0.8)'
  } : {
    backgroundColor: getGrey700()
  }

  const iconSize = overlay ? 20 : (compact ? 14 : 16)
  const buttonPadding = overlay ? 'p-2.5' : (compact ? 'p-1.5' : 'p-2')
  const activeStyle = { transition: CSS_TRANSITION.quick }

  return (
    <div className={`flex ${overlay ? 'gap-2' : (compact ? 'gap-1' : 'gap-2')}`}>
      {ACTIONS.map(({ type: actionType, icon, title, activeClassName, fill }) => {
        const active = preference === actionType
        const interaction = interactions[actionType]
        return (
          <button
            key={actionType}
            onPointerDown={interaction.onPointerDown}
            onPointerMove={interaction.onPointerMove}
            onPointerUp={(e) => handleAction(e, actionType, interaction)}
            className={`ui-tap relative flex items-center justify-center leading-none ${buttonPadding} rounded-lg ${active ? activeClassName : ''}`}
            style={active ? activeStyle : baseButtonStyle}
            onMouseEnter={(e) => !active && Object.assign(e.currentTarget.style, hoverStyle)}
            onMouseLeave={(e) => !active && Object.assign(e.currentTarget.style, baseButtonStyle)}
            title={title}
            aria-label={title}
            aria-pressed={active}
          >
            <ActionIcon icon={icon} size={iconSize} on={active} fill={fill} />
          </button>
        )
      })}
    </div>
  )
}
