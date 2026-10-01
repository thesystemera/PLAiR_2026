import { memo, useCallback, useEffect, useState } from 'react'
import { Music, Megaphone, Reply, Star, Newspaper, MessageCircle } from 'lucide-react'
import { api } from '../lib/api'
import { logger } from '../lib/logger'
import { useUISelector } from '../contexts/UIStateContext'
import { useWebSocketSubscribe } from '../contexts/WebSocketContext'
import { Expandable, ExpandChevron } from './Motion'

const TIMELINE_HOURS = 24
const REFRESH_MS = 30000
const DETAIL_PREFIX = 'aired:'

const KIND_META = {
  track: { icon: Music, name: 'Song', color: 'text-emerald-400' },
  shoutout: { icon: Megaphone, name: 'Shoutout', color: 'text-pink-400' },
  reply: { icon: Reply, name: 'Reply', color: 'text-pink-300' },
  review: { icon: Star, name: 'Review', color: 'text-amber-400' },
  segment: { icon: Newspaper, name: 'Segment', color: 'text-sky-400' },
  talk: { icon: MessageCircle, name: 'DJs', color: 'text-violet-400' },
}

const OUTCOME_STYLE = {
  'on air now': 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40',
  skipped: 'bg-gray-500/10 text-gray-400 border-gray-500/30',
  'played through': 'bg-gray-500/10 text-gray-400 border-gray-500/30',
}

function clockOf(iso) {
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? '' : at.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

const TimelineRow = memo(function TimelineRow({ entry }) {
  const meta = KIND_META[entry.kind] || KIND_META.talk
  const Icon = meta.icon
  const expandable = entry.id.startsWith(DETAIL_PREFIX)
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState(null)

  const toggle = useCallback(() => {
    if (!expandable) return
    setOpen(value => !value)
    if (detail) return
    api.getTimelineEntry(entry.id)
      .then(result => setDetail(result?.said || ''))
      .catch(error => {
        logger.warn('[Timeline] Could not load entry:', error)
        setDetail('')
      })
  }, [expandable, detail, entry.id])

  return (
    <li
      className={`flex gap-3 py-2 border-b border-white/5 ${expandable ? 'cursor-pointer' : ''}`}
      onClick={toggle}
      role={expandable ? 'button' : undefined}
      aria-expanded={expandable ? open : undefined}
    >
      <span className="w-14 shrink-0 pt-0.5 text-xs tabular-nums text-gray-500 text-right">{clockOf(entry.at)}</span>
      <Icon className={`w-4 h-4 mt-0.5 shrink-0 ${meta.color}`} aria-label={meta.name} />
      <div className="min-w-0 flex-1">
        <div className="flex items-start gap-2">
          <p className="text-sm text-gray-200 break-words min-w-0 flex-1">{entry.label}</p>
          {entry.outcome && (
            <span className={`shrink-0 text-[11px] px-2 py-0.5 rounded-full border ${OUTCOME_STYLE[entry.outcome] || OUTCOME_STYLE.skipped}`}>
              {entry.outcome}
            </span>
          )}
          {expandable && <ExpandChevron open={open} size={14} className="shrink-0 mt-0.5 text-gray-500" />}
        </div>
        {expandable && (
          <Expandable open={open}>
            <p className="mt-2 text-sm text-gray-400 whitespace-pre-wrap break-words">
              {detail === null ? 'Loading…' : (detail || 'Nothing more was kept for this one.')}
            </p>
          </Expandable>
        )}
      </div>
    </li>
  )
})

export function ListenerTimeline() {
  const { currentTrackId, offlineMode } = useUISelector(state => ({
    currentTrackId: state.engineState.currentTrack?.id ?? null,
    offlineMode: state.audioState.offlineMode,
  }))
  const [data, setData] = useState(null)
  const [failed, setFailed] = useState(false)

  const load = useCallback(() => {
    api.getTimeline(TIMELINE_HOURS)
      .then(result => {
        setData(result)
        setFailed(false)
      })
      .catch(error => {
        logger.warn('[Timeline] Could not load the timeline:', error)
        setFailed(true)
      })
  }, [])

  useEffect(() => {
    if (offlineMode) return undefined
    load()
    const id = setInterval(load, REFRESH_MS)
    return () => clearInterval(id)
  }, [load, offlineMode, currentTrackId])

  useWebSocketSubscribe('tts_stream_end', useCallback(() => {
    if (!offlineMode) load()
  }, [load, offlineMode]))

  if (offlineMode) {
    return <p className="py-6 text-center text-sm text-gray-400">The timeline needs a connection. Your music keeps playing.</p>
  }
  if (!data) {
    return <p className="py-6 text-center text-sm text-gray-500">{failed ? "Couldn't load the timeline." : 'Loading…'}</p>
  }
  if (!data.entries?.length) {
    return <p className="py-6 text-center text-sm text-gray-500">Nothing has aired for you in the last {TIMELINE_HOURS} hours yet.</p>
  }
  return (
    <div className="pb-4">
      <p className="pb-1 text-xs text-gray-500">Everything that aired for you in the last {data.hours} hours, newest first.</p>
      <ul>
        {data.entries.map(entry => <TimelineRow key={entry.id} entry={entry} />)}
      </ul>
      {data.not_shown > 0 && (
        <p className="pt-2 text-center text-xs text-gray-500">{data.not_shown} older entries not shown.</p>
      )}
    </div>
  )
}
