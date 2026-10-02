import { memo, useCallback } from 'react'
import { Radio, Newspaper, CloudSun, Ticket, Users, Sparkles, Timer, Clock, MessageSquareText, Music, User, Bot } from 'lucide-react'
import { usePreferences } from '../contexts/PreferencesContext'
import { useUISelector } from '../contexts/UIStateContext'
import { SettingTile, TileGroup, nextOf } from './SettingTile'

const SEGMENTS = [
  { key: 'news', icon: Newspaper, label: 'News', color: '#f87171', hint: 'A short bulletin at the top of every hour' },
  { key: 'city', icon: CloudSun, label: 'Weather & City', color: '#38bdf8', hint: "Weather, sunset and what's on tonight, at half past" },
  { key: 'local', icon: Ticket, label: 'Local & Gigs', color: '#fbbf24', hint: 'Gigs and spots near you that fit your taste' },
  { key: 'community', icon: Users, label: 'Community', color: '#f472b6', hint: 'Listener shoutouts and station stats' },
  { key: 'features', icon: Sparkles, label: 'Trivia & Features', color: '#a78bfa', hint: 'Stories behind the artists coming up' },
]

const MUSIC_SOURCE_OPTIONS = [
  { id: 'both', label: 'Both', icon: Music, color: '#34d399' },
  { id: 'human', label: 'Human', icon: User, color: '#fbbf24' },
  { id: 'ai', label: 'AI', icon: Bot, color: '#a78bfa' },
]

const onOff = (on) => (on ? 'ON' : 'OFF')

export const RadioModeSettings = memo(function RadioModeSettings({ className = '', nested = false }) {
  const { radioMode, radioOptions, radioModeSaving, updateRadioMode } = usePreferences()
  const { ttsMuted, offlineMode, toastSuccess } = useUISelector(state => ({
    ttsMuted: state.settingsState.ttsMuted,
    offlineMode: state.audioState.offlineMode,
    toastSuccess: state.toastSuccess,
  }))

  const toggleMaster = useCallback(async () => {
    const saved = await updateRadioMode({ enabled: !radioMode.enabled })
    if (saved?.enabled !== radioMode.enabled) {
      toastSuccess(saved.enabled ? 'Radio Mode on - breaks start at the end of a song' : 'Radio Mode off')
    }
  }, [radioMode.enabled, updateRadioMode, toastSuccess])

  const intervals = radioOptions?.feature_intervals_min || [15, 20, 30]
  const stingsOutside = radioOptions?.stings_outside_radio_mode !== false
  const musicSource = radioMode.music_source || 'both'
  const musicOption = MUSIC_SOURCE_OPTIONS.find(option => option.id === musicSource) || MUSIC_SOURCE_OPTIONS[0]
  const reviewsOn = radioMode.reviews !== false
  const notes = [
    ttsMuted && radioMode.enabled ? 'DJ voice is muted, so breaks are paused.' : null,
    offlineMode ? "You're offline, so talk breaks are paused. Your downloads keep playing and breaks come back when PLAiR is reachable." : null,
  ].filter(Boolean)

  return (
    <TileGroup
      className={className}
      nested={nested}
      title={nested ? null : 'Radio'}
      footer={notes.length > 0 && <p className="tile-hint text-amber-300/90">{notes.join(' ')}</p>}
    >
      <SettingTile
        icon={Radio}
        label="Radio Mode"
        color="#f87171"
        on={radioMode.enabled}
        value={onOff(radioMode.enabled)}
        busy={radioModeSaving}
        hint="News on the hour, a city update at half past and short features between songs, like real radio. A song always finishes before the hosts take over."
        onClick={toggleMaster}
      />
      {SEGMENTS.map(segment => (
        <SettingTile
          key={segment.key}
          icon={segment.icon}
          label={segment.label}
          color={segment.color}
          on={radioMode.enabled && !!radioMode[segment.key]}
          value={onOff(!!radioMode[segment.key])}
          disabled={!radioMode.enabled || radioModeSaving}
          hint={segment.hint}
          onClick={() => updateRadioMode({ [segment.key]: !radioMode[segment.key] })}
        />
      ))}
      <SettingTile
        icon={Timer}
        label="Features every"
        color="#a78bfa"
        cycle
        on={radioMode.enabled}
        value={`${radioMode.feature_interval_min || intervals[0]} min`}
        disabled={!radioMode.enabled || radioModeSaving}
        hint="How often a feature (gigs, community, trivia) comes round between the news and city updates"
        onClick={() => updateRadioMode({ feature_interval_min: nextOf(intervals, radioMode.feature_interval_min) })}
      />
      <SettingTile
        icon={Clock}
        label="Stings & time"
        color="#fbbf24"
        on={!!radioMode.stings}
        value={onOff(!!radioMode.stings)}
        disabled={(!stingsOutside && !radioMode.enabled) || radioModeSaving}
        hint={`Station IDs, short musical stings and a robot time check between songs${stingsOutside ? ', with or without Radio Mode' : ''}`}
        onClick={() => updateRadioMode({ stings: !radioMode.stings })}
      />
      <SettingTile
        icon={Music}
        label="Music"
        color="#34d399"
        cycle
        on
        value={musicOption.label}
        state={{ icon: musicOption.icon, color: musicOption.color }}
        disabled={radioModeSaving}
        hint="Play human-made music, AI music, or both, everywhere on the station"
        onClick={() => updateRadioMode({ music_source: nextOf(MUSIC_SOURCE_OPTIONS.map(option => option.id), musicSource) })}
      />
      <SettingTile
        icon={MessageSquareText}
        label="Reviews"
        color="#f472b6"
        on={reviewsOn}
        value={onOff(reviewsOn)}
        disabled={radioModeSaving}
        hint="Hear what other listeners said about a song while it plays, with or without Radio Mode"
        onClick={() => updateRadioMode({ reviews: !reviewsOn })}
      />
    </TileGroup>
  )
})
