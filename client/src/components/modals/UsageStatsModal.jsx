import { memo, useCallback, useEffect, useMemo, useState } from 'react'
import { Loader2, RefreshCw, ArrowUpDown } from 'lucide-react'
import { Modal, ModalSection, ModalCard, ModalErrorState } from './Modal'
import { Expandable } from '../Motion'
import { api } from '../../lib/api'
import { formatUsd, formatCount, formatDuration } from '../../lib/usageFormat'

const PERIODS = [
  { id: 'day', label: 'Today' },
  { id: 'week', label: '7 days' },
  { id: 'month', label: 'Month' },
]

const KIND_COLORS = { user: '#a78bfa', guest: '#38bdf8', system: '#f59e0b' }
const CATEGORY_LABELS = { llm: 'LLM calls', llm_cache: 'LLM result cache', suno: 'Suno', api: 'External APIs', gpu: 'Local GPU' }

const COLUMNS = [
  { id: 'label', label: 'Who', numeric: false },
  { id: 'plan', label: 'Plan', numeric: false, wide: true },
  { id: 'calls', label: 'Calls', numeric: true, wide: true },
  { id: 'tokens', label: 'Tokens', numeric: true },
  { id: 'total_usd', label: 'Cost', numeric: true },
  { id: 'projected_month_usd', label: 'Proj./mo', numeric: true },
  { id: 'generations', label: 'Songs', numeric: true, wide: true },
]

const DailyChart = memo(function DailyChart({ data, height = 96 }) {
  const max = Math.max(...data.map(d => d.total_usd), 0.000001)
  const width = Math.max(data.length * 12, 120)
  const barWidth = width / Math.max(data.length, 1)
  return (
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className="w-full" style={{ height }} role="img" aria-label="Daily cost">
      {data.map((d, i) => {
        const h = Math.max((d.total_usd / max) * (height - 4), d.total_usd > 0 ? 2 : 0)
        return (
          <rect key={d.day} x={i * barWidth + 1} y={height - h} width={Math.max(barWidth - 2, 1)} height={h} rx="1.5" fill="#a78bfa" opacity={0.85}>
            <title>{`${d.day}: ${formatUsd(d.total_usd)}`}</title>
          </rect>
        )
      })}
    </svg>
  )
})

const StatCard = memo(function StatCard({ label, value, hint, accent = 'text-white' }) {
  return (
    <ModalCard className="flex flex-col gap-1 min-w-0">
      <span className="text-[11px] uppercase tracking-wide text-gray-400">{label}</span>
      <span className={`text-lg font-bold tabular-nums truncate ${accent}`}>{value}</span>
      {hint && <span className="text-[11px] text-gray-500 truncate">{hint}</span>}
    </ModalCard>
  )
})

const ShareBar = memo(function ShareBar({ parts }) {
  const total = parts.reduce((sum, p) => sum + p.value, 0)
  if (total <= 0) return <div className="h-2 rounded bg-white/5" />
  return (
    <div className="flex h-2 rounded overflow-hidden bg-white/5">
      {parts.map(p => p.value > 0 && (
        <div key={p.id} style={{ width: `${(p.value / total) * 100}%`, backgroundColor: p.color }} title={`${p.label}: ${formatUsd(p.value)}`} />
      ))}
    </div>
  )
})

const BreakdownTable = memo(function BreakdownTable({ rows, nameKey, limit = 12 }) {
  if (!rows?.length) return <div className="text-xs text-gray-500">No usage recorded yet.</div>
  return (
    <div className="space-y-1">
      {rows.slice(0, limit).map(row => (
        <div key={`${row.category || ''}:${row[nameKey]}:${row.model || ''}`} className="flex items-center justify-between gap-3 text-xs">
          <span className="text-gray-300 truncate min-w-0">{row.label || row[nameKey]}{row.provider && nameKey === 'model' ? <span className="text-gray-500"> · {row.provider}</span> : null}</span>
          <span className="flex items-center gap-3 shrink-0 tabular-nums">
            {row.cache_hits > 0 && <span className="text-emerald-400">{formatCount(row.cache_hits)} hits</span>}
            <span className="text-gray-500 hidden sm:inline">{formatCount(row.calls)} calls</span>
            <span className="text-gray-200 w-16 text-right">{formatUsd(row.total_usd)}</span>
          </span>
        </div>
      ))}
    </div>
  )
})

const SubjectDetail = memo(function SubjectDetail({ subjectKey, period }) {
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    const param = subjectKey.startsWith('user:') ? subjectKey.slice(5) : subjectKey.startsWith('guest:') ? subjectKey.slice(6) : subjectKey
    api.getUsageSubject(param, period)
      .then(data => { if (!cancelled) setDetail(data) })
      .catch(err => { if (!cancelled) setError(err.message) })
    return () => { cancelled = true }
  }, [subjectKey, period])

  if (error) return <div className="text-xs text-red-400 py-2">{error}</div>
  if (!detail) return <div className="py-3 flex justify-center"><Loader2 size={16} className="animate-spin text-gray-400" /></div>
  return (
    <div className="py-2 space-y-2">
      <DailyChart data={detail.daily} height={56} />
      <BreakdownTable rows={detail.features} nameKey="feature" limit={8} />
    </div>
  )
})

const SubjectTable = memo(function SubjectTable({ subjects, period }) {
  const [sort, setSort] = useState({ id: 'total_usd', desc: true })
  const [openKey, setOpenKey] = useState(null)

  const sorted = useMemo(() => {
    const column = COLUMNS.find(c => c.id === sort.id)
    const list = [...subjects]
    list.sort((a, b) => {
      const av = a[sort.id] ?? (column.numeric ? 0 : '')
      const bv = b[sort.id] ?? (column.numeric ? 0 : '')
      const cmp = column.numeric ? av - bv : String(av).localeCompare(String(bv))
      return sort.desc ? -cmp : cmp
    })
    return list
  }, [subjects, sort])

  const toggleSort = useCallback((id) => {
    setSort(prev => prev.id === id ? { id, desc: !prev.desc } : { id, desc: COLUMNS.find(c => c.id === id).numeric })
  }, [])

  if (!subjects.length) return <div className="text-xs text-gray-500">No listeners in this period.</div>

  return (
    <div className="w-full min-w-0 overflow-x-auto">
      <table className="w-full text-xs table-fixed">
        <thead>
          <tr className="text-gray-400">
            {COLUMNS.map(col => (
              <th key={col.id} className={`px-1 py-1.5 font-medium ${col.numeric ? 'text-right' : 'text-left'} ${col.wide ? 'hidden sm:table-cell' : ''} ${col.id === 'label' ? 'w-[34%]' : ''}`}>
                <button onClick={() => toggleSort(col.id)} className={`ui-press inline-flex items-center gap-1 ${sort.id === col.id ? 'text-white' : ''}`}>
                  {col.label}
                  <ArrowUpDown size={10} className={`hidden sm:inline ${sort.id === col.id ? 'opacity-100' : 'opacity-30'}`} />
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map(row => (
            <SubjectRow key={row.subject_key} row={row} period={period} open={openKey === row.subject_key}
              onToggle={() => setOpenKey(prev => prev === row.subject_key ? null : row.subject_key)} />
          ))}
        </tbody>
      </table>
    </div>
  )
})

const SubjectRow = memo(function SubjectRow({ row, period, open, onToggle }) {
  return (
    <>
      <tr onClick={onToggle} className={`cursor-pointer border-t border-white/5 hover:bg-white/5 ${open ? 'bg-white/5' : ''}`}>
        <td className="px-1 py-1.5 text-gray-200 truncate" title={row.subject_key}>
          <span className="inline-block w-1.5 h-1.5 rounded-full mr-1.5 align-middle" style={{ backgroundColor: KIND_COLORS[row.kind] || '#9ca3af' }} />
          {row.kind === 'guest' ? `${row.label.slice(0, 14)}…` : row.label}
        </td>
        <td className="hidden sm:table-cell px-1 py-1.5 text-gray-400">{row.plan}</td>
        <td className="hidden sm:table-cell px-1 py-1.5 text-right tabular-nums text-gray-300">{formatCount(row.calls)}</td>
        <td className="px-1 py-1.5 text-right tabular-nums text-gray-300">{formatCount(row.tokens)}</td>
        <td className="px-1 py-1.5 text-right tabular-nums text-white">{formatUsd(row.total_usd)}</td>
        <td className="px-1 py-1.5 text-right tabular-nums text-gray-300">{formatUsd(row.projected_month_usd)}</td>
        <td className="hidden sm:table-cell px-1 py-1.5 text-right tabular-nums text-gray-300">{row.generations || 0}</td>
      </tr>
      <tr>
        <td colSpan={COLUMNS.length} className="p-0">
          <Expandable open={open}>
            {open && <div className="px-2"><SubjectDetail subjectKey={row.subject_key} period={period} /></div>}
          </Expandable>
        </td>
      </tr>
    </>
  )
})

function AdminView({ summary, users, period }) {
  const month = summary.month
  const revenue = summary.revenue
  const kinds = summary.by_subject_kind || {}
  const categories = Object.entries(summary.by_category || {})
    .filter(([id]) => id !== 'llm_cache')
    .map(([id, t]) => ({ id, label: CATEGORY_LABELS[id] || id, ...t }))
    .sort((a, b) => b.total_usd - a.total_usd)
  const marginPositive = (revenue.estimated_margin_usd ?? 0) >= 0

  return (
    <>
      <ModalSection>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <StatCard label="Month to date" value={formatUsd(month.month_to_date_usd)} hint={`${month.active_days.toFixed(1)} active days`} />
          <StatCard label="Projected month" value={formatUsd(month.projected_linear_usd)} hint={`7-day rate: ${formatUsd(month.projected_7d_rate_usd)}`} accent="text-violet-300" />
          <StatCard label="Per active user" value={formatUsd(summary.all_in_cost_per_active_user_usd)} hint={`users only: ${formatUsd(summary.cost_per_active_user_usd)}`} />
          <StatCard label="Est. margin / mo" value={formatUsd(revenue.estimated_margin_usd)} hint={`${revenue.active_subscribers} × ${revenue.price_label}`} accent={marginPositive ? 'text-emerald-300' : 'text-red-300'} />
        </div>
      </ModalSection>

      <ModalSection title={`Daily cost · ${PERIODS.find(p => p.id === period)?.label}`}>
        <ModalCard>
          <DailyChart data={summary.daily} />
          <div className="flex justify-between text-[11px] text-gray-500 mt-1">
            <span>{summary.period.start}</span>
            <span>{formatUsd(summary.totals.total_usd)} total · {formatUsd(summary.per_day_usd)}/day</span>
            <span>{summary.period.end}</span>
          </div>
        </ModalCard>
      </ModalSection>

      <ModalSection title="Who spends it">
        <ModalCard className="space-y-2">
          <ShareBar parts={['user', 'guest', 'system'].map(id => ({ id, label: id, value: kinds[id]?.total_usd || 0, color: KIND_COLORS[id] }))} />
          <div className="grid grid-cols-3 gap-2 text-xs">
            {['user', 'guest', 'system'].map(id => (
              <div key={id} className="flex flex-col">
                <span className="text-gray-400 capitalize flex items-center gap-1.5"><span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: KIND_COLORS[id] }} />{id === 'system' ? 'shared / system' : `${id}s`}</span>
                <span className="text-white tabular-nums">{formatUsd(kinds[id]?.total_usd || 0)}</span>
                <span className="text-gray-500">{summary.active_subjects?.[id] || 0} active</span>
              </div>
            ))}
          </div>
        </ModalCard>
      </ModalSection>

      <ModalSection title="Listeners">
        <SubjectTable subjects={users.subjects} period={period} />
        {users.hidden_guests.count > 0 && (
          <div className="text-[11px] text-gray-500 mt-1">+{users.hidden_guests.count} more guests ({formatUsd(users.hidden_guests.total_usd)})</div>
        )}
      </ModalSection>

      <ModalSection title="By feature">
        <ModalCard><BreakdownTable rows={summary.by_feature} nameKey="feature" /></ModalCard>
      </ModalSection>

      <div className="grid md:grid-cols-2 gap-x-4">
        <ModalSection title="By model">
          <ModalCard><BreakdownTable rows={summary.by_model} nameKey="model" /></ModalCard>
        </ModalSection>
        <ModalSection title="By kind of cost">
          <ModalCard><BreakdownTable rows={categories} nameKey="label" /></ModalCard>
        </ModalSection>
      </div>

      <div className="grid md:grid-cols-2 gap-x-4">
        <ModalSection title="Cache savings">
          <ModalCard className="space-y-1 text-xs">
            <Row label="LLM result cache" value={`${formatUsd(summary.cache_savings.result_cache_usd)} · ${formatCount(summary.cache_savings.result_cache_hits)} hits`} />
            <Row label="Provider prompt cache" value={formatUsd(summary.cache_savings.prompt_cache_usd)} />
            <Row label="TTS clips reused" value={formatCount(summary.cache_savings.tts_clip_cache_hits)} />
            <Row label="GPU time" value={formatDuration(summary.totals.gpu_seconds)} />
          </ModalCard>
        </ModalSection>
        <ModalSection title="Revenue context">
          <ModalCard className="space-y-1 text-xs">
            <Row label="Paying subscribers" value={`${revenue.active_subscribers} of ${summary.registered_users}`} />
            <Row label="Net per subscriber" value={`${formatUsd(revenue.net_per_subscriber_usd)} after Stripe`} />
            <Row label="Net revenue / mo" value={formatUsd(revenue.net_monthly_revenue_usd)} />
            <Row label="Break-even subscribers" value={revenue.break_even_subscribers == null ? '-' : revenue.break_even_subscribers.toFixed(1)} />
            <Row label="Suno songs" value={`${summary.suno.generations} · ${formatUsd(summary.suno.cost_usd)}`} />
          </ModalCard>
        </ModalSection>
      </div>

      {(summary.recorder.dropped_write_failure > 0 || summary.recorder.dropped_overflow > 0) && (
        <div className="text-[11px] text-amber-400 mb-2">
          {summary.recorder.dropped_write_failure + summary.recorder.dropped_overflow} usage events were dropped since the last restart (database unavailable), so totals may be low.
        </div>
      )}
      <div className="text-[11px] text-gray-500">
        Prices: {Object.values(summary.pricing.sources).join(' · ')}. Estimates only; the provider invoices are authoritative.
      </div>
    </>
  )
}

function Row({ label, value }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-gray-400">{label}</span>
      <span className="text-gray-200 tabular-nums text-right">{value}</span>
    </div>
  )
}

function OwnView({ detail }) {
  return (
    <>
      <ModalSection>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
          <StatCard label="This period" value={formatUsd(detail.totals.total_usd)} hint={`${formatCount(detail.totals.calls)} AI calls`} />
          <StatCard label="Projected month" value={formatUsd(detail.month.projected_linear_usd)} accent="text-violet-300" />
          <StatCard label="Songs generated" value={detail.generations} />
        </div>
      </ModalSection>
      <ModalSection title="Daily">
        <ModalCard><DailyChart data={detail.daily} /></ModalCard>
      </ModalSection>
      <ModalSection title="By feature">
        <ModalCard><BreakdownTable rows={detail.features} nameKey="feature" /></ModalCard>
      </ModalSection>
    </>
  )
}

export function UsageStatsModal({ isOpen, onClose, isAdmin }) {
  const [period, setPeriod] = useState('month')
  const [reloadKey, setReloadKey] = useState(0)
  const [result, setResult] = useState({ key: null, data: null, error: null })
  const requestKey = `${isAdmin ? 'admin' : 'own'}:${period}:${reloadKey}`

  useEffect(() => {
    if (!isOpen) return
    let cancelled = false
    const load = isAdmin
      ? Promise.all([api.getUsageSummary(period), api.getUsageUsers(period), api.getMyUsage(period)])
          .then(([summary, users, own]) => ({ summary, users, own }))
      : api.getMyUsage(period).then(own => ({ own }))
    load
      .then(data => { if (!cancelled) setResult({ key: requestKey, data, error: null }) })
      .catch(err => { if (!cancelled) setResult(prev => ({ key: requestKey, data: prev.data, error: err.message })) })
    return () => { cancelled = true }
  }, [isOpen, isAdmin, period, requestKey])

  const loading = result.key !== requestKey
  const data = result.data
  const error = result.key === requestKey ? result.error : null

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="AI usage & cost" maxWidth="max-w-3xl">
      <ModalSection>
        <div className="flex items-center justify-between gap-2">
          <div className="flex gap-1">
            {PERIODS.map(p => (
              <button key={p.id} onClick={() => setPeriod(p.id)}
                className={`ui-press px-3 py-1 rounded text-xs font-medium transition ${period === p.id ? 'bg-violet-500/30 text-violet-200 border border-violet-500/50' : 'bg-white/5 text-gray-400 border border-gray-700/50'}`}>
                {p.label}
              </button>
            ))}
          </div>
          <button onClick={() => setReloadKey(k => k + 1)} className="ui-press p-1.5 rounded bg-white/5 text-gray-400" aria-label="Refresh">
            {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
          </button>
        </div>
      </ModalSection>

      {error && !data && <ModalErrorState title="Could not load usage" message={error} onRetry={() => setReloadKey(k => k + 1)} />}
      {!data && !error && <div className="py-10 flex justify-center"><Loader2 size={24} className="animate-spin text-gray-400" /></div>}
      {data && isAdmin && data.summary && data.users && (
        <>
          <AdminView summary={data.summary} users={data.users} period={period} />
          {data.own && (
            <ModalSection title="Your own usage" className="mt-6">
              <ModalCard className="flex justify-between text-xs">
                <span className="text-gray-400">{formatCount(data.own.totals.calls)} calls · {formatCount(data.own.totals.tokens)} tokens</span>
                <span className="text-white tabular-nums">{formatUsd(data.own.totals.total_usd)}</span>
              </ModalCard>
            </ModalSection>
          )}
        </>
      )}
      {data && !isAdmin && data.own && <OwnView detail={data.own} />}
    </Modal>
  )
}

