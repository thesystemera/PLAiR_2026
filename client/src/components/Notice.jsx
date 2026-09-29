import { memo, useSyncExternalStore } from 'react'

export const NOTICE_TONES = {
  success: { border: 'border-emerald-400/45', icon: 'text-emerald-300' },
  error: { border: 'border-red-400/50', icon: 'text-red-300' },
  warning: { border: 'border-amber-400/50', icon: 'text-amber-300' },
  info: { border: 'border-sky-400/45', icon: 'text-sky-300' },
  neutral: { border: 'border-white/15', icon: 'text-white/70' },
}

const NOTICE_BACKGROUND = { backgroundColor: 'rgba(10, 10, 12, 0.82)' }
const WRAP_AFTER_CHARS = 52

export const NoticeChip = memo(function NoticeChip({ tone = 'neutral', borderClass, borderColor, icon: Icon, iconClass = '', text, children, onClick, title }) {
  const palette = NOTICE_TONES[tone] || NOTICE_TONES.neutral
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
      {Icon && <Icon className={`w-3.5 h-3.5 shrink-0 ${iconClass || palette.icon}`} aria-hidden="true" />}
      {text !== undefined && <span className={`font-semibold text-white/90 ${wraps ? 'leading-snug' : 'truncate'}`}>{text}</span>}
      {children}
    </Tag>
  )
})

let noticeSlot = null
const slotListeners = new Set()

export function setNoticeSlot(element) {
  if (noticeSlot === element) return
  noticeSlot = element
  slotListeners.forEach(listener => listener())
}

function subscribeSlot(listener) {
  slotListeners.add(listener)
  return () => slotListeners.delete(listener)
}

export function useNoticeSlot() {
  return useSyncExternalStore(subscribeSlot, () => noticeSlot, () => null)
}
