import { logger } from '../lib/logger'
import { useState } from 'react'
import { Heart, Star, Ban, Loader2 } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import { usePreferences } from '../contexts/PreferencesContext'
import { useUIState } from '../contexts/UIStateContext'
import { useDynamicTheme } from '../contexts/DynamicThemeContext'
import { triggerHaptic } from '../lib/haptics'
import { usePointerInteraction } from '../hooks/usePointerInteraction'

export default function MediaActions({ type = 'track', itemId, compact = false, overlay = false }) {
  const { isAuthenticated } = useAuth()
  const { getPreference, setPreference, removePreference, isPending } = usePreferences()
  const { toastError } = useUIState()
  const { getGrey800, getGrey700, getGrey400, triggerEffect } = useDynamicTheme()
  const error = toastError
  const [clickedType, setClickedType] = useState(null)
  const likeInteraction = usePointerInteraction()
  const superLikeInteraction = usePointerInteraction()
  const banInteraction = usePointerInteraction()

  const isLoading = isPending(type, itemId)
  const preference = getPreference(type, itemId)

  const handleAction = async (e, preferenceType, interaction) => {
    e?.preventDefault()
    e?.stopPropagation()

    if (!isAuthenticated || isLoading) return
    if (!interaction.shouldTrigger()) return

    setClickedType(preferenceType)

    if (e.clientX !== undefined && e.clientY !== undefined) {
      triggerEffect('click', {
        x: e.clientX / window.innerWidth,
        y: e.clientY / window.innerHeight,
        intensity: 0.5
      })
    }

    if (preferenceType === 'ban') {
      triggerHaptic('error')
    } else {
      triggerHaptic('success')
    }

    try {
      if (preference === preferenceType) {
        await removePreference(type, itemId)
      } else {
        await setPreference(type, itemId, preferenceType)
      }
    } catch (err) {
      logger.error(`Failed to set ${type} preference:`, err)
      error('Failed to update preference. Please try again.')
    } finally {
      setClickedType(null)
    }
  }

  if (!isAuthenticated) {
    return null
  }

  const baseButtonStyle = (compact || overlay) ? {
    backgroundColor: 'rgba(0, 0, 0, 0.6)',
    color: 'rgba(255, 255, 255, 0.9)',
    transition: 'all 150ms ease-in-out'
  } : {
    backgroundColor: getGrey800(),
    color: getGrey400(),
    transition: 'all 700ms ease-in-out, transform 150ms ease-in-out'
  }

  const hoverStyle = (compact || overlay) ? {
    backgroundColor: 'rgba(0, 0, 0, 0.8)'
  } : {
    backgroundColor: getGrey700()
  }

  const iconSize = overlay ? 20 : (compact ? 14 : 16)
  const buttonPadding = overlay ? 'p-2.5' : (compact ? 'p-1.5' : 'p-2')

  return (
    <div className={`flex ${overlay ? 'gap-2' : (compact ? 'gap-1' : 'gap-2')}`}>
      <button
        onPointerDown={likeInteraction.onPointerDown}
        onPointerMove={likeInteraction.onPointerMove}
        onPointerUp={(e) => handleAction(e, 'like', likeInteraction)}
        disabled={isLoading}
        className={`${buttonPadding} rounded-lg transition-all ${
          preference === 'like'
            ? 'bg-pink-600 text-white'
            : ''
        }`}
        style={preference !== 'like' ? baseButtonStyle : { transition: 'all 150ms ease-in-out' }}
        onMouseEnter={(e) => preference !== 'like' && Object.assign(e.currentTarget.style, hoverStyle)}
        onMouseLeave={(e) => preference !== 'like' && Object.assign(e.currentTarget.style, baseButtonStyle)}
        title="Like"
      >
        {isLoading && clickedType === 'like' ? (
          <Loader2 size={iconSize} className="animate-spin" />
        ) : (
          <Heart size={iconSize} fill={preference === 'like' ? 'currentColor' : 'none'} />
        )}
      </button>

      <button
        onPointerDown={superLikeInteraction.onPointerDown}
        onPointerMove={superLikeInteraction.onPointerMove}
        onPointerUp={(e) => handleAction(e, 'super_like', superLikeInteraction)}
        disabled={isLoading}
        className={`${buttonPadding} rounded-lg transition-all ${
          preference === 'super_like'
            ? 'bg-yellow-600 text-white'
            : ''
        }`}
        style={preference !== 'super_like' ? baseButtonStyle : { transition: 'all 150ms ease-in-out' }}
        onMouseEnter={(e) => preference !== 'super_like' && Object.assign(e.currentTarget.style, hoverStyle)}
        onMouseLeave={(e) => preference !== 'super_like' && Object.assign(e.currentTarget.style, baseButtonStyle)}
        title="Super Like"
      >
        {isLoading && clickedType === 'super_like' ? (
          <Loader2 size={iconSize} className="animate-spin" />
        ) : (
          <Star size={iconSize} fill={preference === 'super_like' ? 'currentColor' : 'none'} />
        )}
      </button>

      <button
        onPointerDown={banInteraction.onPointerDown}
        onPointerMove={banInteraction.onPointerMove}
        onPointerUp={(e) => handleAction(e, 'ban', banInteraction)}
        disabled={isLoading}
        className={`${buttonPadding} rounded-lg transition-all ${
          preference === 'ban'
            ? 'bg-red-600 text-white'
            : ''
        }`}
        style={preference !== 'ban' ? baseButtonStyle : { transition: 'all 150ms ease-in-out' }}
        onMouseEnter={(e) => preference !== 'ban' && Object.assign(e.currentTarget.style, hoverStyle)}
        onMouseLeave={(e) => preference !== 'ban' && Object.assign(e.currentTarget.style, baseButtonStyle)}
        title="Ban"
      >
        {isLoading && clickedType === 'ban' ? (
          <Loader2 size={iconSize} className="animate-spin" />
        ) : (
          <Ban size={iconSize} />
        )}
      </button>
    </div>
  )
}