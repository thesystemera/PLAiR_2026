import { memo } from 'react'
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react'

const NOTICE_TONES = {
  success: { border: 'border-emerald-400/45', iconColor: 'text-emerald-300', icon: CheckCircle2 },
  error: { border: 'border-red-400/50', iconColor: 'text-red-300', icon: XCircle },
  warning: { border: 'border-amber-400/50', iconColor: 'text-amber-300', icon: AlertTriangle },
  info: { border: 'border-sky-400/45', iconColor: 'text-sky-300', icon: Info },
  neutral: { border: 'border-white/15', iconColor: 'text-white/70', icon: Info },
}

const NOTICE_TEXT = {
  success: 'text-emerald-300', error: 'text-red-300', warning: 'text-amber-300', info: 'text-sky-300',
  neutral: 'text-gray-400',
}

export const InlineNote = memo(function InlineNote({ tone = 'neutral', children, className = '' }) {
  const palette = NOTICE_TONES[tone] || NOTICE_TONES.neutral
  const Icon = palette.icon
  return (
    <p className={`flex items-start gap-1.5 text-xs leading-snug ${NOTICE_TEXT[tone] || NOTICE_TEXT.neutral} ${className}`}>
      <Icon className="w-3.5 h-3.5 shrink-0 mt-px" aria-hidden="true" />
      <span>{children}</span>
    </p>
  )
})

const PILL_TONES = {
  success: 'bg-emerald-400/15 text-emerald-200 border-emerald-400/30',
  error: 'bg-red-400/15 text-red-200 border-red-400/30',
  warning: 'bg-amber-400/15 text-amber-200 border-amber-400/30',
  info: 'bg-sky-400/15 text-sky-200 border-sky-400/30',
  neutral: 'bg-white/10 text-white/80 border-white/15',
}

export const StatusPill = memo(function StatusPill({ tone = 'neutral', color, icon: Icon, children, title }) {
  const tinted = color ? { backgroundColor: `color-mix(in srgb, ${color} 18%, transparent)`, borderColor: `color-mix(in srgb, ${color} 40%, transparent)`, color } : undefined
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold leading-tight whitespace-nowrap ${tinted ? '' : PILL_TONES[tone] || PILL_TONES.neutral}`}
      style={tinted}
    >
      {Icon && <Icon className="w-3 h-3 shrink-0" aria-hidden="true" />}
      {children}
    </span>
  )
})

export const NoticeAction = memo(function NoticeAction({ onClick, disabled = false, children }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="ui-press ml-1 rounded-full bg-white/90 px-2.5 py-0.5 text-[11px] font-bold text-zinc-900 disabled:opacity-50"
    >
      {children}
    </button>
  )
})

export const NoticeClose = memo(function NoticeClose({ onClick, title = 'Hide' }) {
  return (
    <button onClick={onClick} className="ui-tap text-white/60 hover:text-white" title={title} aria-label="Hide">
      <X size={14} />
    </button>
  )
})

const NOTICE_BACKGROUND = { backgroundColor: 'rgba(10, 10, 12, 0.82)' }
const WRAP_AFTER_CHARS = 52

export const NoticeChip = memo(function NoticeChip({ tone = 'neutral', icon, iconClass, iconColor, borderClass, borderColor, text, content, title, onClick }) {
  const palette = NOTICE_TONES[tone] || NOTICE_TONES.neutral
  const Icon = icon === undefined ? palette.icon : icon
  const wraps = typeof text === 'string' && text.length > WRAP_AFTER_CHARS
  const Tag = onClick ? 'button' : 'span'

  return (
    <Tag
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      title={title}
      className={`pointer-events-auto inline-flex items-center gap-2 border px-3 py-1 text-xs text-left max-w-[min(92vw,26rem)] ${
        wraps ? 'rounded-2xl py-1.5' : 'rounded-full whitespace-nowrap'
      } ${borderClass || palette.border} ${onClick ? 'ui-press cursor-pointer' : ''}`}
      style={borderColor ? { ...NOTICE_BACKGROUND, borderColor } : NOTICE_BACKGROUND}
    >
      {Icon && <Icon className={`w-3.5 h-3.5 shrink-0 ${iconClass || (iconColor ? '' : palette.iconColor)}`} style={iconColor ? { color: iconColor } : undefined} aria-hidden="true" />}
      {text !== undefined && <span className={`font-semibold text-white/90 ${wraps ? 'leading-snug' : 'truncate'}`}>{text}</span>}
      {content}
    </Tag>
  )
})
