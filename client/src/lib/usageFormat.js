export function formatUsd(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return '-'
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  if (abs === 0) return '$0.00'
  if (abs < 0.01) return `${sign}$${abs.toFixed(4)}`
  if (abs < 100) return `${sign}$${abs.toFixed(2)}`
  return `${sign}$${Math.round(abs).toLocaleString()}`
}

export function formatCount(value) {
  const n = Number(value) || 0
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 10_000) return `${Math.round(n / 1000)}k`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(Math.round(n))
}

export function formatDuration(seconds) {
  const s = Number(seconds) || 0
  if (s < 60) return `${s.toFixed(0)}s`
  if (s < 3600) return `${(s / 60).toFixed(1)} min`
  return `${(s / 3600).toFixed(1)} h`
}
