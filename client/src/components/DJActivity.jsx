import { memo, useCallback, useEffect, useRef } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  BookOpen, Brain, Check, CircleSlash, CloudSun, Cpu, Disc3, FileText, Heart, Loader2, MapPin, Megaphone, Music,
  Newspaper, Radio, Save, Search, SkipForward, Ticket, TrendingUp, User
} from 'lucide-react'
import { useUISelector } from '../contexts/UIStateContext'
import { useWebSocketSubscribe } from '../contexts/WebSocketContext'
import { useDynamicTheme } from '../contexts/DynamicThemeContext'
import { PRESETS } from '../lib/motion'

const LINGER_MS = 2600
const DONE_MS = 1400
const STALE_MS = 20000
const MAX_VISIBLE = 3
const LOOKUP_EFFECT = { x: 0.5, y: 0.3, intensity: 0.5 }

const TOOL_ICONS = {
  search_and_play: Disc3,
  playback_control: SkipForward,
  seed_radio: Radio,
  play_playlist: Radio,
  rate_track: Heart,
  get_news: Newspaper,
  get_weather: CloudSun,
  get_events: Ticket,
  find_places: MapPin,
  get_artist_biography: BookOpen,
  explain_lyrics: FileText,
  play_shoutouts: Megaphone,
  save_shoutout: Save,
  save_shoutout_reply: Save,
  save_opinion: Save,
  listener_context: User,
  city_trends: TrendingUp,
  pulse_detail: FileText,
  hal11000: Cpu,
  producer: Brain,
}

const KIND_ICONS = {
  event: Ticket,
  place: MapPin,
  news: Newspaper,
  weather: CloudSun,
  area: CloudSun,
  community: Megaphone,
  track: Music,
  artist: BookOpen,
  chart: TrendingUp,
  trend: TrendingUp,
}

function iconFor(call) {
  if (call.tool === 'pulse_search') {
    return call.kinds?.length === 1 ? (KIND_ICONS[call.kinds[0]] || Search) : Search
  }
  return TOOL_ICONS[call.tool] || Search
}

function sentenceCase(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : ''
}

export function DJActivityBridge() {
  const { reportEngineStatus } = useUISelector(state => ({ reportEngineStatus: state.reportEngineStatus }))
  const { triggerEffect } = useDynamicTheme()
  const callsRef = useRef([])
  const timersRef = useRef(new Map())

  const publish = useCallback(() => {
    reportEngineStatus({ djActivity: callsRef.current.slice(-MAX_VISIBLE) })
  }, [reportEngineStatus])

  const removeLater = useCallback((id, delay) => {
    const timers = timersRef.current
    clearTimeout(timers.get(id))
    timers.set(id, setTimeout(() => {
      timers.delete(id)
      callsRef.current = callsRef.current.filter(call => call.id !== id)
      publish()
    }, delay))
  }, [publish])

  const handleActivity = useCallback((data) => {
    if (!data?.phase) return
    if (data.phase === 'start' && data.call_id) {
      callsRef.current = [...callsRef.current.filter(call => call.id !== data.call_id), {
        id: data.call_id,
        turnId: data.turn_id,
        tool: data.tool,
        kinds: data.kinds || [],
        label: sentenceCase(data.label || data.tool),
        state: 'running',
        summary: '',
        live: false,
      }]
      removeLater(data.call_id, STALE_MS)
      triggerEffect('click', LOOKUP_EFFECT)
      publish()
    } else if (data.phase === 'result' && data.call_id) {
      callsRef.current = callsRef.current.map(call => call.id === data.call_id
        ? { ...call, state: data.outcome || 'done', summary: data.summary || '', live: !!data.live }
        : call)
      removeLater(data.call_id, LINGER_MS)
      publish()
    } else if (data.phase === 'done') {
      callsRef.current
        .filter(call => call.turnId === data.turn_id)
        .forEach(call => removeLater(call.id, call.state === 'running' ? DONE_MS : Math.min(LINGER_MS, DONE_MS * 2)))
    }
  }, [publish, removeLater, triggerEffect])

  useWebSocketSubscribe('dj_activity', handleActivity)

  useEffect(() => {
    const timers = timersRef.current
    return () => {
      timers.forEach(timer => clearTimeout(timer))
      timers.clear()
    }
  }, [])

  return null
}

export const DJActivity = memo(function DJActivity() {
  const { calls, hidden } = useUISelector(state => ({
    calls: state.engineState.djActivity,
    hidden: state.interfaceState.isFullscreenVisuals || state.audioState.offlineMode,
  }))

  return (
    <div
      className="fixed left-1/2 -translate-x-1/2 z-50 pointer-events-none flex flex-col items-center gap-1.5"
      style={{ top: 'calc(var(--safe-top) + 0.5rem)' }}
      role="status"
      aria-live="polite"
    >
      <AnimatePresence initial={false}>
        {!hidden && (calls || []).map(call => <ActivityChip key={call.id} call={call} />)}
      </AnimatePresence>
    </div>
  )
})

const ActivityChip = memo(function ActivityChip({ call }) {
  const Icon = iconFor(call)
  const running = call.state === 'running'
  const ok = call.state === 'found' || call.state === 'done'
  const StateIcon = running ? Loader2 : ok ? Check : CircleSlash

  return (
    <motion.div layout="position" {...PRESETS.fadeSlide}>
      <span
        className="inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs whitespace-nowrap max-w-[92vw] backdrop-blur-sm"
        style={{ borderColor: 'var(--theme-accent-60, rgba(255, 255, 255, 0.28))', backgroundColor: 'rgba(10, 10, 12, 0.82)' }}
      >
        <Icon className="w-3.5 h-3.5 shrink-0" style={{ color: 'var(--theme-accent-85, #ffffff)' }} aria-hidden="true" />
        <span className="font-semibold text-white/90 truncate">{call.label}</span>
        {call.summary && <span className="text-white/60 truncate">· {call.summary}</span>}
        {call.live && <span className="text-[10px] font-semibold uppercase tracking-wide text-emerald-300">live</span>}
        <StateIcon
          className={`w-3.5 h-3.5 shrink-0 ${running ? 'animate-spin text-white/60' : ok ? 'text-emerald-400' : 'text-white/40'}`}
          aria-hidden="true"
        />
      </span>
    </motion.div>
  )
})
