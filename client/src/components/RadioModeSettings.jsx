import { memo, useState, useEffect, useCallback } from 'react'
import { RadioTower, Radio, Mic, Newspaper, CloudSun, Ticket, Users, Sparkles, Timer, Clock, MessageSquareText, Music } from 'lucide-react'
import { usePreferences } from '../contexts/PreferencesContext'
import { useUISelector } from '../contexts/UIStateContext'
import { safeStorage } from '../lib/safeStorage'
import { PRESETS } from '../lib/motion'
import { ExpandSection, Expandable, Fade, FadeSwap, Pop } from './Motion'
import { SettingRow, ToggleChip } from './SettingRow'
import { InlineNote } from './Notice'

const SEGMENTS = [
  { key: 'news', icon: Newspaper, label: 'News', color: 'text-red-400', hint: 'A short bulletin at the top of every hour' },
  { key: 'city', icon: CloudSun, label: 'Weather & City', color: 'text-sky-400', hint: 'Weather, sunset and what\'s on tonight, at half past' },
  { key: 'local', icon: Ticket, label: 'Local & Gigs', color: 'text-amber-400', hint: 'Gigs and spots near you that fit your taste' },
  { key: 'community', icon: Users, label: 'Community', color: 'text-pink-400', hint: 'Listener shoutouts and station stats' },
  { key: 'features', icon: Sparkles, label: 'Trivia & Features', color: 'text-violet-400', hint: 'Stories behind the artists coming up' },
]

const SWITCHES = ['enabled', 'stings', 'reviews']
const ALL_ON = Object.fromEntries([...SWITCHES, ...SEGMENTS.map(segment => segment.key)].map(key => [key, true]))
const ALL_OFF = Object.fromEntries(SWITCHES.map(key => [key, false]))

const MUSIC_SOURCE_OPTIONS = [
  { id: 'both', label: 'Both' },
  { id: 'human', label: 'Human' },
  { id: 'ai', label: 'AI' },
]

const masterState = (radioMode) => {
  if (Object.keys(ALL_ON).every(key => radioMode[key])) return 'all'
  if (SWITCHES.every(key => !radioMode[key])) return 'off'
  return 'some'
}

const GroupLabel = ({ children }) => (
  <div className="pt-1 px-1 text-[10px] font-bold uppercase tracking-wider text-gray-500">{children}</div>
)

export const RadioModeSettings = memo(function RadioModeSettings({ className = '' }) {
  const { radioMode, radioOptions, radioModeSaving, updateRadioMode } = usePreferences()
  const {
    ttsMuted,
    toastSuccess,
    offlineMode,
  } = useUISelector(state => ({
    ttsMuted: state.settingsState.ttsMuted,
    toastSuccess: state.toastSuccess,
    offlineMode: state.audioState.offlineMode,
  }))
  const [open, setOpen] = useState(() => safeStorage.get('userPanel_radioMode') === 'true')

  useEffect(() => {
    safeStorage.set('userPanel_radioMode', String(open))
  }, [open])

  const master = masterState(radioMode)

  const toggleMaster = useCallback(async () => {
    const turnOn = master !== 'all'
    const saved = await updateRadioMode(turnOn ? ALL_ON : ALL_OFF)
    if (masterState(saved) !== master) {
      toastSuccess(turnOn ? 'Radio Mode on - breaks start at the end of a song' : 'Radio Mode off')
    }
  }, [master, updateRadioMode, toastSuccess])

  const toggleBreaks = useCallback(async () => {
    const saved = await updateRadioMode({ enabled: !radioMode.enabled })
    if (saved?.enabled !== radioMode.enabled) {
      toastSuccess(saved.enabled ? 'Talk breaks on - they start at the end of a song' : 'Talk breaks off')
    }
  }, [radioMode.enabled, updateRadioMode, toastSuccess])

  const intervals = radioOptions?.feature_intervals_min || [15, 20, 30]
  const djMuted = !!ttsMuted
  const stingsOutside = radioOptions?.stings_outside_radio_mode !== false

  const stingsRow = (
    <SettingRow
      icon={Clock}
      label="Stings & time checks"
      color="text-amber-400"
      headerContent={
        <ToggleChip
          on={!!radioMode.stings}
          onClick={() => updateRadioMode({ stings: !radioMode.stings })}
          disabled={radioModeSaving}
          label="Stings & time checks"
        />
      }
    >
      <div className="text-xs text-gray-400">
        Station IDs, short musical stings and a robot time check between songs
      </div>
    </SettingRow>
  )

  return (
    <ExpandSection
      className={className}
      open={open}
      onToggle={setOpen}
      icon={RadioTower}
      iconClassName="text-red-400"
      title="Radio Mode"
      meta={
        <Pop show={master !== 'off'} className="flex">
          <span className="text-[10px] font-bold tracking-wider text-red-300 bg-red-500/20 border border-red-500/40 px-1.5 py-0.5 rounded">
            <FadeSwap swapKey={master} preset={PRESETS.badgeSwap} className="block">{master === 'all' ? 'ON' : 'SOME'}</FadeSwap>
          </span>
        </Pop>
      }
      contentClassName="space-y-3 pl-2"
    >
      <SettingRow
        icon={Radio}
        label="Radio Mode"
        color="text-red-400"
        headerContent={
          <ToggleChip
            on={master === 'all'}
            mixed={master === 'some'}
            onClick={toggleMaster}
            disabled={radioModeSaving}
            activeClassName="bg-red-500 text-white"
            label="Radio Mode"
          />
        }
      >
        <div className="text-xs text-gray-400">
          Everything that makes PLAiR sound like real radio: talk breaks, stings and time checks, and listener reviews over songs. One switch for all of it, or pick below.
        </div>
        <Fade show={djMuted && !!radioMode.enabled} className="mt-1">
          <InlineNote tone="warning">DJ voice is muted, so breaks are paused.</InlineNote>
        </Fade>
        <Fade show={!!offlineMode} className="mt-1">
          <InlineNote tone="warning">{"You're offline, so talk breaks are paused. Your downloads keep playing and breaks come back when PLAiR is reachable."}</InlineNote>
        </Fade>
      </SettingRow>

      <GroupLabel>Talk breaks</GroupLabel>
      <SettingRow
        icon={Mic}
        label="Talk breaks"
        color="text-red-400"
        headerContent={<ToggleChip on={!!radioMode.enabled} onClick={toggleBreaks} disabled={radioModeSaving} activeClassName="bg-red-500 text-white" label="Talk breaks" />}
      >
        <div className="text-xs text-gray-400">
          News on the hour, a city update at half past and short features between songs. A song always finishes before the hosts take over.
        </div>
      </SettingRow>

      <Expandable open={!!radioMode.enabled} innerClassName="space-y-3 pl-2">
        {SEGMENTS.map(segment => (
          <SettingRow
            key={segment.key}
            icon={segment.icon}
            label={segment.label}
            color={segment.color}
            headerContent={
              <ToggleChip
                on={!!radioMode[segment.key]}
                onClick={() => updateRadioMode({ [segment.key]: !radioMode[segment.key] })}
                disabled={radioModeSaving}
                label={segment.label}
              />
            }
          >
            <div className="text-xs text-gray-400">{segment.hint}</div>
          </SettingRow>
        ))}

        {!stingsOutside && stingsRow}

        <SettingRow
          icon={Timer}
          label="Features every"
          color="text-violet-400"
          headerContent={
            <div className="flex gap-1">
              {intervals.map(minutes => (
                <button
                  key={minutes}
                  type="button"
                  onClick={() => updateRadioMode({ feature_interval_min: minutes })}
                  disabled={radioModeSaving}
                  aria-pressed={radioMode.feature_interval_min === minutes}
                  className={`ui-press px-2 py-1 rounded text-xs font-medium transition disabled:opacity-50 ${radioMode.feature_interval_min === minutes ? 'bg-violet-500/30 text-violet-300 border border-violet-500/50' : 'bg-dark-card text-gray-400 border border-gray-700/50'}`}
                >
                  {minutes}m
                </button>
              ))}
            </div>
          }
        >
          <div className="text-xs text-gray-400">How often a feature (gigs, community, trivia) comes round between the news and city updates</div>
        </SettingRow>
      </Expandable>

      <GroupLabel>Between and over songs</GroupLabel>
      {stingsOutside && stingsRow}

      <SettingRow
        icon={MessageSquareText}
        label="Listener reviews over songs"
        color="text-pink-400"
        headerContent={
          <ToggleChip
            on={radioMode.reviews !== false}
            onClick={() => updateRadioMode({ reviews: radioMode.reviews === false })}
            disabled={radioModeSaving}
            label="Listener reviews over songs"
          />
        }
      >
        <div className="text-xs text-gray-400">
          Hear what other listeners said about a song while it plays
        </div>
      </SettingRow>

      <GroupLabel>Your music</GroupLabel>
      <SettingRow
        icon={Music}
        label="Music"
        color="text-emerald-400"
        headerContent={
          <div className="flex gap-1">
            {MUSIC_SOURCE_OPTIONS.map(option => (
              <button
                key={option.id}
                type="button"
                onClick={() => updateRadioMode({ music_source: option.id })}
                disabled={radioModeSaving}
                aria-pressed={(radioMode.music_source || 'both') === option.id}
                className={`ui-press px-3 py-1 rounded text-xs font-medium transition disabled:opacity-50 ${(radioMode.music_source || 'both') === option.id ? 'bg-purple-500 text-white' : 'bg-dark-hover text-gray-400'}`}
              >
                {option.label}
              </button>
            ))}
          </div>
        }
      >
        <div className="text-xs text-gray-400">
          Play human-made music, AI music, or both, everywhere on the station
        </div>
      </SettingRow>
    </ExpandSection>
  )
})
