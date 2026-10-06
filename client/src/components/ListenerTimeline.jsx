import { memo, useCallback, useEffect, useState } from 'react'
import { History, LayoutGrid, Music, Megaphone, Reply, Star, Newspaper, MessageCircle } from 'lucide-react'
import { motion } from 'framer-motion'
import { PRESETS } from '../lib/motion'
import { api } from '../lib/api'
import { logger } from '../lib/logger'
import { useUISelector } from '../contexts/UIStateContext'
import { useWebSocketSubscribe } from '../contexts/WebSocketContext'
import { useDynamicTheme } from '../contexts/DynamicThemeContext'
import { Expandable, ExpandChevron, Fade, FadeSwap } from './Motion'
import { MediaEmptyState, MediaOfflineState } from './MediaShared'
import { MemoizedVirtualScroller as VirtualScroller } from './VirtualScroller'

const TIMELINE_HOURS = 24
const REFRESH_MS = 30000
const DETAIL_PREFIX = 'aired:'
const ESTIMATED_ENTRY_HEIGHT = 41
const NO_IDS = new Set()
const NO_DETAILS = new Map()
const NO_ENTRIES = []
const entryKey = (entry) => `${entry.id}|${entry.at}`

const KIND_META = {
  track: { icon: Music, name: 'Song', plural: 'songs', color: 'text-emerald-400' },
  shoutout: { icon: Megaphone, name: 'Shoutout', plural: 'shoutouts', color: 'text-pink-400' },
  reply: { icon: Reply, name: 'Reply', plural: 'replies', color: 'text-pink-300' },
  review: { icon: Star, name: 'Review', plural: 'reviews', color: 'text-amber-400' },
  segment: { icon: Newspaper, name: 'Segment', plural: 'segments and talk breaks', color: 'text-sky-400' },
  talk: { icon: MessageCircle, name: 'DJ talk', plural: 'DJ chat and between-track talk', color: 'text-violet-400' },
}

const FILTERS = ['all', ...Object.keys(KIND_META)]

export function TimelineFilters({ value, onChange }) {
  const { getFilterAllActive, getFilterInactive } = useDynamicTheme()
  return FILTERS.map(kind => {
    const Icon = kind === 'all' ? LayoutGrid : KIND_META[kind].icon
    const label = kind === 'all' ? 'everything' : KIND_META[kind].plural
    return (
      <button
        key={kind}
        onClick={() => onChange(kind)}
        aria-pressed={value === kind}
        aria-label={`Timeline: ${label}`}
        title={`Timeline: ${label}`}
        className="ui-press p-1.5 rounded transition-colors border"
        style={value === kind ? getFilterAllActive() : getFilterInactive()}
      >
        <Icon size={16} />
      </button>
    )
  })
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

const TimelineRow = memo(function TimelineRow({ entry, entering, open, detail, onToggle }) {
  const meta = KIND_META[entry.kind] || KIND_META.talk
  const Icon = meta.icon
  const expandable = entry.id.startsWith(DETAIL_PREFIX)

  return (
    <motion.div
      {...PRESETS.fade}
      initial={entering ? PRESETS.fade.initial : false}
      className={`flex gap-3 py-2 border-b border-white/5 ${expandable ? 'cursor-pointer' : ''}`}
      onClick={expandable ? () => onToggle(entry.id) : undefined}
      role={expandable ? 'button' : 'listitem'}
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
            <FadeSwap swapKey={detail === null ? 'loading' : 'detail'} preset={PRESETS.fade} mode="wait">
              <p className="mt-2 text-sm text-gray-400 whitespace-pre-wrap break-words">
                {detail === null ? 'Loading…' : (detail || 'Nothing more was kept for this one.')}
              </p>
            </FadeSwap>
          </Expandable>
        )}
      </div>
    </motion.div>
  )
})

export function ListenerTimeline({ kind = 'all', scrollRef }) {
  const { currentTrackId, offlineMode } = useUISelector(state => ({
    currentTrackId: state.engineState.currentTrack?.id ?? null,
    offlineMode: state.audioState.offlineMode,
  }))
  const [data, setData] = useState(null)
  const [openIds, setOpenIds] = useState(NO_IDS)
  const [details, setDetails] = useState(NO_DETAILS)
  const entries = data?.entries || NO_ENTRIES
  const [arrivals, setArrivals] = useState({ entries, added: NO_IDS })
  if (arrivals.entries !== entries) {
    const added = new Set()
    if (arrivals.entries.length) {
      const previous = new Set(arrivals.entries.map(entryKey))
      for (const entry of entries) if (!previous.has(entryKey(entry))) added.add(entryKey(entry))
    }
    setArrivals({ entries, added })
  }

  const toggle = useCallback((id) => {
    setOpenIds(previous => {
      const next = new Set(previous)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
    if (details.has(id)) return
    setDetails(previous => new Map(previous).set(id, null))
    api.getTimelineEntry(id)
      .then(result => setDetails(current => new Map(current).set(id, result?.said || '')))
      .catch(error => {
        logger.warn('[Timeline] Could not load entry:', error)
        setDetails(current => new Map(current).set(id, ''))
      })
  }, [details])

  const renderEntry = useCallback((entry) => (
    <TimelineRow
      entry={entry}
      entering={arrivals.added.has(entryKey(entry))}
      open={openIds.has(entry.id)}
      detail={details.get(entry.id) ?? null}
      onToggle={toggle}
    />
  ), [arrivals.added, openIds, details, toggle])
  const [failed, setFailed] = useState(false)

  const load = useCallback(() => {
    api.getTimeline(TIMELINE_HOURS, kind === 'all' ? null : [kind])
      .then(result => {
        setData(result)
        setFailed(false)
      })
      .catch(error => {
        logger.warn('[Timeline] Could not load the timeline:', error)
        setFailed(true)
      })
  }, [kind])

  useEffect(() => {
    if (offlineMode) return undefined
    load()
    const id = setInterval(load, REFRESH_MS)
    return () => clearInterval(id)
  }, [load, offlineMode, currentTrackId])

  useWebSocketSubscribe('tts_stream_end', useCallback(() => {
    if (!offlineMode) load()
  }, [load, offlineMode]))

  const state = offlineMode ? 'offline' : !data ? (failed ? 'failed' : 'loading') : data.entries?.length ? 'entries' : 'empty'
  const what = kind === 'all' ? 'Nothing has' : `No ${KIND_META[kind]?.plural || 'entries'} have`
  return (
    <FadeSwap swapKey={state} preset={PRESETS.fade}>
      {state === 'offline' && <MediaOfflineState icon={History} title="The timeline needs a connection" compact />}
      {state === 'failed' && <MediaEmptyState icon={History} title="Couldn't load the timeline" subtitle="It'll try again in a moment." compact />}
      {state === 'loading' && <MediaEmptyState icon={History} title="Loading the timeline" subtitle="" compact />}
      {state === 'empty' && <MediaEmptyState icon={History} title={`${what} aired for you yet`} subtitle={`The timeline covers the last ${TIMELINE_HOURS} hours.`} compact />}
      {state === 'entries' && (
        <div className="pb-4">
          <p className="pb-1 text-xs text-gray-500">Everything that aired for you in the last {data.hours} hours, newest first.</p>
          <div role="list">
            <VirtualScroller
              items={entries}
              itemKey={entryKey}
              estimatedItemHeight={ESTIMATED_ENTRY_HEIGHT}
              renderItem={renderEntry}
              scrollContainerRef={scrollRef}
            />
          </div>
          <Fade as="p" show={data.not_shown > 0} className="pt-2 text-center text-xs text-gray-500">
            {data.not_shown} older entries not shown.
          </Fade>
        </div>
      )}
    </FadeSwap>
  )
}
