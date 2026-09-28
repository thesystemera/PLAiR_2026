import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { formatUsd } from '../lib/usageFormat'

const REFRESH_MS = 60_000

const TICKER_STYLE = {
  position: 'fixed',
  left: 'max(10px, var(--safe-left))',
  bottom: 'max(10px, var(--safe-bottom))',
  zIndex: 9999,
  backgroundColor: 'rgba(0, 0, 0, 0.8)',
  color: '#c4b5fd',
  padding: '6px 10px',
  borderRadius: '6px',
  fontFamily: 'monospace',
  fontSize: '12px',
  fontWeight: 'bold',
  pointerEvents: 'none',
  whiteSpace: 'nowrap',
}

export function CostTicker() {
  const [summary, setSummary] = useState(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    let timer = null
    const load = () => {
      if (document.hidden) return
      api.getUsageSummary('day')
        .then(data => { if (!cancelled) { setSummary(data); setFailed(false) } })
        .catch(() => { if (!cancelled) setFailed(true) })
    }
    load()
    timer = setInterval(load, REFRESH_MS)
    document.addEventListener('visibilitychange', load)
    return () => {
      cancelled = true
      clearInterval(timer)
      document.removeEventListener('visibilitychange', load)
    }
  }, [])

  if (!summary) return failed ? <div style={TICKER_STYLE}>AI $ unavailable</div> : null

  return (
    <div style={TICKER_STYLE}>
      today {formatUsd(summary.totals.total_usd)} · mtd {formatUsd(summary.month.month_to_date_usd)} · proj {formatUsd(summary.month.projected_linear_usd)}
    </div>
  )
}

export default CostTicker
