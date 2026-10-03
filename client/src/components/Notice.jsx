import { memo } from 'react'
import { AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-react'

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

const NOTICE_BACKGROUND = { backgroundColor: 'rgba(10, 10, 12, 0.82)' }
const WRAP_AFTER_CHARS = 52

export const NoticeChip = memo(function NoticeChip({ tone = 'neutral', icon, iconClass, borderClass, borderColor, text, content, title, onClick }) {
  const palette = NOTICE_TONES[tone] || NOTICE_TONES.neutral
  const Icon = icon === undefined ? palette.icon : icon
  const wraps = typeof text === 'string' && text.length > WRAP_AFTER_CHARS
  const Tag = onClick ? 'button' : 'span'

  return (
    <Tag
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      title={title}
      className={`pointer-events-auto inline-flex items-center gap-2 border px-3 py-1 text-xs text-left backdrop-blur-sm max-w-[min(92vw,26rem)] ${
        wraps ? 'rounded-2xl py-1.5' : 'rounded-full whitespace-nowrap'
      } ${borderClass || palette.border} ${onClick ? 'ui-press cursor-pointer' : ''}`}
      style={borderColor ? { ...NOTICE_BACKGROUND, borderColor } : NOTICE_BACKGROUND}
    >
      {Icon && <Icon className={`w-3.5 h-3.5 shrink-0 ${iconClass || palette.iconColor}`} aria-hidden="true" />}
      {text !== undefined && <span className={`font-semibold text-white/90 ${wraps ? 'leading-snug' : 'truncate'}`}>{text}</span>}
      {content}
    </Tag>
  )
})
