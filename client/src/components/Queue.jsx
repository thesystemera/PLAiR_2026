import { X, Radio, Heart, Star, Ban, TrendingUp } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { useState, useCallback, memo, useMemo, useEffect } from 'react'
import { useAuth } from '../contexts/AuthContext'
import { usePreferences } from '../contexts/PreferencesContext'
import { useUIState } from '../contexts/UIStateContext'
import { usePlayback } from '../contexts/PlaybackContext'
import { useArtwork } from '../contexts/UIStateContext'
import { getFallbackGradientClass } from '../lib/themeManager'
import { triggerHaptic } from '../lib/haptics'
import { PanelHeader } from './Panel'
import { useDynamicTheme } from '../contexts/DynamicThemeContext'
import { usePointerInteraction } from '../hooks/usePointerInteraction'
import { Scroller } from './Scroller'

const PreferenceBadge = memo(function PreferenceBadge({ preference }) {
  const { getLikeBadgeBg, getSuperLikeBadgeBg, getBanBadgeBg, getPrimaryText } = useDynamicTheme()

  if (!preference) return null

  const badges = {
    like: { icon: Heart, getBg: getLikeBadgeBg, fill: true },
    super_like: { icon: Star, getBg: getSuperLikeBadgeBg, fill: true },
    ban: { icon: Ban, getBg: getBanBadgeBg, fill: false }
  }

  const badge = badges[preference]
  if (!badge) return null

  const Icon = badge.icon

  return (
    <div style={{ background: badge.getBg(), color: getPrimaryText() }} className="rounded-full p-1.5" title={preference.replace('_', ' ')}>
      <Icon size={12} fill={badge.fill ? 'currentColor' : 'none'} />
    </div>
  )
})

const TrackArtwork = memo(function TrackArtwork({ track }) {
  const [artworkError, setArtworkError] = useState(false)
  const artworkUrl = useArtwork(track.id, track.has_artwork)

  if (track.has_artwork && !artworkError) {
    return (
      <img
        src={artworkUrl}
        alt={track.title || 'Track artwork'}
        className="w-12 h-12 rounded object-cover flex-shrink-0"
        onError={() => setArtworkError(true)}
      />
    )
  }

  return (
    <div className={`w-12 h-12 rounded flex-shrink-0 bg-gradient-to-br ${getFallbackGradientClass(track.id)} flex items-center justify-center text-2xl`}>
      🎵
    </div>
  )
})

function QueueComponent({ onSeedRadio, onAnalytics }) {
  const playback = usePlayback()
  const { isAuthenticated } = useAuth()
  const { getPreference } = usePreferences()
  const { radioState, engineState } = useUIState()
  const { queue, currentTrack } = engineState
  const currentTrackId = currentTrack?.id
  const activeSeedMode = radioState.activeSeedMode
  const {
    getWhite,
    getGrey400,
    getGrey300,
    getPrimaryActionText,
    getButtonHoverBg,
    getPanelBorder,
    getPlayingRingColor,
    getPlayingBackground,
    getLoadingSpinner,
    getDangerActionText,
    getCategoryMetadata,
    triggerEffect
  } = useDynamicTheme()

  const [loadingTrackId, setLoadingTrackId] = useState(null)
  const [removingTrackIds, setRemovingTrackIds] = useState(new Set())
  const [reselectFlash, setReselectFlash] = useState(null)
  const playInteraction = usePointerInteraction()
  const removeInteraction = usePointerInteraction()
  const seedInteraction = usePointerInteraction()
  const analyticsInteraction = usePointerInteraction()

  const handlePlay = useCallback((e, trackId) => {
    e?.preventDefault()
    if (!playInteraction.shouldTrigger()) return

    const isReselect = currentTrackId === trackId

    if (e.clientX !== undefined && e.clientY !== undefined) {
      triggerEffect('click', {
        x: e.clientX / window.innerWidth,
        y: e.clientY / window.innerHeight,
        intensity: isReselect ? 1.5 : 1.0
      })
    }

    if (isReselect) {
      setReselectFlash(trackId)
      triggerEffect('reselect', {
        targetId: trackId,
        color: getPlayingRingColor()
      })
      setTimeout(() => setReselectFlash(null), 600)
    }

    triggerHaptic(isReselect ? 'strong' : 'medium')
    setLoadingTrackId(trackId)
    void playback.playTrack(trackId)
  }, [playback, playInteraction, currentTrackId, triggerEffect, getPlayingRingColor])

  const handleRemove = useCallback((e, trackId) => {
    e?.preventDefault()
    e?.stopPropagation()
    if (!removeInteraction.shouldTrigger()) return
    triggerHaptic('medium')
    setRemovingTrackIds(prev => new Set(prev).add(trackId))
    playback.removeFromQueue(trackId).catch(() => {
      setRemovingTrackIds(prev => {
        const next = new Set(prev)
        next.delete(trackId)
        return next
      })
    })
  }, [playback, removeInteraction])

  const handleSeedButtonClick = useCallback((e) => {
    e?.preventDefault()
    if (!seedInteraction.shouldTrigger()) return
    triggerHaptic('medium')
    onSeedRadio()
  }, [seedInteraction, onSeedRadio])

  const handleAnalyticsButtonClick = useCallback((e) => {
    e?.preventDefault()
    if (!analyticsInteraction.shouldTrigger()) return
    triggerHaptic('medium')
    onAnalytics()
  }, [analyticsInteraction, onAnalytics])

  const visibleQueue = useMemo(() => {
    return queue.filter(track => !removingTrackIds.has(track.id))
  }, [queue, removingTrackIds])

  useEffect(() => {
    if (queue.length === 0) {
      queueMicrotask(() => setRemovingTrackIds(new Set()))
    } else {
      queueMicrotask(() => setRemovingTrackIds(prev => {
        const next = new Set()
        for (const id of prev) {
          if (queue.some(track => track.id === id)) {
            next.add(id)
          }
        }
        return next.size === prev.size ? prev : next
      }))
    }
  }, [queue])

  useEffect(() => {
    if (currentTrackId && loadingTrackId === currentTrackId) {
      queueMicrotask(() => setLoadingTrackId(null))
    }
  }, [currentTrackId, loadingTrackId])

  const seedMeta = activeSeedMode ? getCategoryMetadata(activeSeedMode) : null
  const SeedIcon = seedMeta?.icon || Radio

  if (!queue || queue.length === 0) {
    return (
      <div className="flex flex-col h-full relative">
        <PanelHeader title="Queue" />
        <div className="flex-1 p-6 text-center transition-colors duration-700" style={{ color: getGrey400() }}>
          <p>Queue is empty</p>
          <p className="text-sm mt-2" style={{ color: getGrey300() }}>Click tracks to play or add them to your queue</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full relative">
        <PanelHeader title="Queue">
          <div className="flex items-center gap-2">
            <button
              onPointerDown={seedInteraction.onPointerDown}
              onPointerMove={seedInteraction.onPointerMove}
              onPointerUp={handleSeedButtonClick}
              disabled={queue.length === 0}
              className="flex items-center gap-2 px-3 py-2 text-sm rounded-lg transition disabled:opacity-50 disabled:cursor-not-allowed border"
              style={{
                color: activeSeedMode ? seedMeta.color : getPrimaryActionText(),
                backgroundColor: activeSeedMode ? `${seedMeta.color}15` : 'transparent',
                borderColor: activeSeedMode ? `${seedMeta.color}30` : 'transparent'
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = activeSeedMode ? `${seedMeta.color}25` : getButtonHoverBg()
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = activeSeedMode ? `${seedMeta.color}15` : 'transparent'
              }}
              title={activeSeedMode ? `Active Seed: ${seedMeta.label}` : "Seed Radio"}
            >
              <SeedIcon size={16} />
              {activeSeedMode ? seedMeta.label : "Seed Radio"}
            </button>
            <button
              onPointerDown={analyticsInteraction.onPointerDown}
              onPointerMove={analyticsInteraction.onPointerMove}
              onPointerUp={handleAnalyticsButtonClick}
              disabled={queue.length === 0}
              className="p-2 rounded-lg transition disabled:opacity-50 disabled:cursor-not-allowed border"
              style={{
                color: getGrey400(),
                backgroundColor: 'transparent',
                borderColor: 'transparent'
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = getButtonHoverBg()
                e.currentTarget.style.color = getWhite()
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = 'transparent'
                e.currentTarget.style.color = getGrey400()
              }}
              title="Station Analytics"
            >
              <TrendingUp size={16} />
            </button>
          </div>
        </PanelHeader>

      <Scroller className="flex-1">
        <AnimatePresence initial={false}>
          {visibleQueue.map((track, index) => {
            const isPlaying = track.id === currentTrackId
            const isLoading = loadingTrackId === track.id && !isPlaying
            const uniqueKey = `${track.id}-${index}`

            const isReselectFlashing = reselectFlash === track.id

            return (
              <motion.div
                key={uniqueKey}
                initial={{ opacity: 0, x: -20 }}
                animate={isReselectFlashing ? {
                  opacity: 1,
                  x: 0,
                  scale: [1, 1.03, 1],
                  boxShadow: [
                    `0 0 0 2px ${getPlayingRingColor()}`,
                    `0 0 0 6px ${getPlayingRingColor()}80, 0 0 20px ${getPlayingRingColor()}40`,
                    `0 0 0 2px ${getPlayingRingColor()}`
                  ]
                } : { opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 20 }}
                transition={isReselectFlashing ? { duration: 0.6, ease: 'easeOut' } : { duration: 0.2 }}
                className="p-4 transition cursor-pointer rounded-md"
                style={{
                  borderBottom: `1px solid ${getPanelBorder()}`,
                  ...(isPlaying && !isReselectFlashing ? {
                    boxShadow: `0 0 0 2px ${getPlayingRingColor()}`,
                    backgroundColor: getPlayingBackground()
                  } : {})
                }}
                onMouseEnter={(e) => !isPlaying && (e.currentTarget.style.backgroundColor = getButtonHoverBg())}
                onMouseLeave={(e) => !isPlaying && (e.currentTarget.style.backgroundColor = 'transparent')}
                onPointerDown={playInteraction.onPointerDown}
                onPointerMove={playInteraction.onPointerMove}
                onPointerUp={(e) => handlePlay(e, track.id)}
              >
                <div className="flex items-center gap-3">
                  <TrackArtwork track={track} />

                  <div className="flex-1 min-w-0">
                    <div className="font-medium truncate transition-colors duration-700" style={{ color: getWhite() }}>{track.title || 'Untitled'}</div>
                    {track.artist_name && (
                      <div className="text-sm truncate transition-colors duration-700" style={{ color: getGrey300() }}>{track.artist_name}</div>
                    )}
                    <div className="text-sm truncate transition-colors duration-700" style={{ color: getGrey400() }}>{track.style || 'No style'}</div>
                  </div>

                  <div className="flex items-center gap-2 flex-shrink-0">
                    {isLoading && (
                      <motion.div
                        animate={{ rotate: 360 }}
                        transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
                        className="w-5 h-5 rounded-full"
                        style={{
                          border: `2px solid ${getLoadingSpinner()}`,
                          borderTopColor: 'transparent'
                        }}
                      />
                    )}
                    {isPlaying && (
                      <div className="px-2 py-1 text-xs rounded-full font-semibold" style={{ backgroundColor: getLoadingSpinner(), color: getWhite() }}>
                        Playing
                      </div>
                    )}

                    {isAuthenticated && <PreferenceBadge preference={getPreference('track', track.id)} />}

                    <button
                      onPointerDown={removeInteraction.onPointerDown}
                      onPointerMove={removeInteraction.onPointerMove}
                      onPointerUp={(e) => handleRemove(e, track.id)}
                      className="p-1 transition"
                      style={{ color: getGrey400() }}
                      onMouseEnter={(e) => e.currentTarget.style.color = getDangerActionText()}
                      onMouseLeave={(e) => e.currentTarget.style.color = getGrey400()}
                    >
                      <X size={16} />
                    </button>
                  </div>
                </div>
              </motion.div>
            )
          })}
        </AnimatePresence>
      </Scroller>
    </div>
  )
}

export const Queue = memo(QueueComponent, (prevProps, nextProps) => {
  return prevProps.onSeedRadio === nextProps.onSeedRadio &&
         prevProps.onAnalytics === nextProps.onAnalytics
})