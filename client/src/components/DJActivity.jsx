import { memo, useCallback, useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import {
  Ban, BookOpen, Brain, Check, CircleSlash, ClipboardCheck, CloudSun, Cpu, Disc3, FileText, Heart, Loader2, MapPin, Megaphone, Music,
  Newspaper, Radio, Save, Search, SkipForward, Ticket, TrendingUp, User, Wrench, X
} from 'lucide-react'
import { useUISelector } from '../contexts/UIStateContext'
import { useWebSocketSubscribe } from '../contexts/WebSocketContext'
import { useDynamicTheme } from '../contexts/DynamicThemeContext'
import { TWEEN } from '../lib/motion'
import { Expandable } from './Motion'
import { NoticeChip } from './Notice'

const LINGER_MS = 2600
const DONE_MS = 1400
const STALE_MS = 20000
const MAX_VISIBLE = 4
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
  review: ClipboardCheck,
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

export const SOURCES = {
  producer: { name: 'Producer', icon: Brain, bubble: 'bg-violet-500/10 border-violet-500/30 text-violet-300', chip: 'border-violet-400/40 text-violet-300' },
  tool: { name: 'Studio tool', icon: Wrench, bubble: 'bg-teal-500/10 border-teal-500/30 text-teal-300', chip: 'border-teal-400/40 text-teal-300' },
  review: { name: 'Review', icon: ClipboardCheck, bubble: 'bg-sky-500/10 border-sky-500/30 text-sky-300', chip: 'border-sky-400/40 text-sky-300' },
  hal11000: { name: 'HAL 11000', icon: Cpu, bubble: 'bg-green-500/10 border-green-500/30 text-green-300', chip: 'border-green-400/40 text-green-300' },
}

const STATES = {
  running: { icon: Loader2, text: 'text-white/60', spin: true },
  found: { icon: Check, text: 'text-emerald-300' },
  done: { icon: Check, text: 'text-emerald-300' },
  empty: { icon: CircleSlash, text: 'text-zinc-400' },
  failed: { icon: X, text: 'text-red-300' },
  blocked: { icon: Ban, text: 'text-amber-300' },
}

const ICONS = { ...KIND_ICONS, ...TOOL_ICONS, search: Search, tool: Wrench }

function iconKey(call) {
  if (call.tool === 'pulse_search') {
    return call.kinds?.length === 1 && KIND_ICONS[call.kinds[0]] ? call.kinds[0] : 'search'
  }
  if (TOOL_ICONS[call.tool]) return call.tool
  return call.source === 'hal11000' ? 'hal11000' : call.source === 'producer' ? 'producer' : 'tool'
}

const COSTS = {
  memory: 'from memory',
  live: 'may go online',
  segment: 'full segment',
}

function sentenceCase(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : ''
}

export const ActivityCard = memo(function ActivityCard({ call, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)
  const source = SOURCES[call.source] || SOURCES.tool
  const state = STATES[call.state] || STATES.done
  const running = call.state === 'running'
  const full = running || open
  const Icon = ICONS[iconKey(call)]
  const SourceIcon = source.icon
  const StateIcon = state.icon

  return (
    <motion.div
      layout
      transition={TWEEN.layout}
      onClick={() => !running && setOpen(value => !value)}
      role={running ? undefined : 'button'}
      aria-expanded={running ? undefined : full}
      animate={{ opacity: full ? 1 : 0.72 }}
      whileHover={full ? undefined : { opacity: 1 }}
      className={`border ${source.bubble} max-w-[80%] w-fit overflow-hidden ${running ? '' : 'cursor-pointer'} ${full ? 'p-3 rounded-lg' : 'px-3 py-1 rounded-full'}`}
    >
      {full ? (
        <motion.div layout="position" className="flex items-start gap-2">
          <div className="flex items-center gap-1.5 mt-0.5">
            <SourceIcon className="w-4 h-4" aria-hidden="true" />
            <Icon className={`w-4 h-4 ${running ? 'animate-pulse' : ''}`} aria-hidden="true" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center justify-between gap-2 mb-1">
              <span className="text-xs font-medium tracking-wide opacity-70">
                {source.name}
                {call.cost && <span className="ml-1.5 opacity-80">· {call.live ? 'went online' : COSTS[call.cost]}</span>}
              </span>
              <span className={`flex items-center gap-1 text-xs ${state.text}`}>
                <StateIcon className={`w-4 h-4 ${state.spin ? 'animate-spin' : ''}`} aria-hidden="true" />
              </span>
            </div>
            <p className="text-sm break-words">{sentenceCase(call.label || call.tool)}</p>
            {call.summary && <p className={`text-xs mt-0.5 break-words ${state.text}`}>{call.summary}</p>}
            <Expandable open={!!call.command}>
              <pre className="mt-2 max-h-40 overflow-auto rounded-md bg-black/20 px-2 py-1.5 font-mono text-xs opacity-80 whitespace-pre-wrap break-words">
                {call.command}
              </pre>
            </Expandable>
          </div>
        </motion.div>
      ) : (
        <motion.span layout="position" className="flex items-center gap-2 min-w-0 text-xs">
          <Icon className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
          <span className="font-medium truncate">{sentenceCase(call.label || call.tool)}</span>
          {call.summary && <span className={`truncate ${state.text}`}>· {call.summary}</span>}
          <StateIcon className={`w-3.5 h-3.5 shrink-0 ${state.text}`} aria-hidden="true" />
        </motion.span>
      )}
    </motion.div>
  )
})

export const ActivityChip = memo(function ActivityChip({ call }) {
  const source = SOURCES[call.source] || SOURCES.tool
  const state = STATES[call.state] || STATES.done
  const StateIcon = state.icon

  return (
    <NoticeChip
      borderClass={source.chip}
      icon={ICONS[iconKey(call)]}
      iconClass={`text-current ${call.state === 'running' ? 'animate-pulse' : ''}`}
      text={sentenceCase(call.label || call.tool)}
    >
      {call.summary && <span className={`truncate ${state.text}`}>· {call.summary}</span>}
      <StateIcon className={`w-3.5 h-3.5 shrink-0 ${state.text} ${state.spin ? 'animate-spin' : ''}`} aria-hidden="true" />
    </NoticeChip>
  )
})

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
        source: data.source || 'tool',
        kinds: data.kinds || [],
        cost: data.cost || '',
        label: data.label || data.tool,
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

