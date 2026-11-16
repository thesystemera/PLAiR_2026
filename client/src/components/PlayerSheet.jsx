import { memo } from 'react'
import { X } from 'lucide-react'
import { GLASS } from '../lib/themeManager'
import { PRESETS } from '../lib/motion'
import { Expandable, FadeSwap } from './Motion'

export const PlayerSheet = memo(function PlayerSheet({ open, title, onClose, actions = null, children }) {
  return (
    <Expandable open={open} className={GLASS.expandPanel}>
      <div className="max-w-screen-2xl mx-auto px-3 py-2 md:px-4 md:py-3">
        <div className={GLASS.expandPanelHeader}>
          <FadeSwap swapKey={title} preset={PRESETS.fade} mode="wait">
            <h3 className="m-0 text-lg font-semibold text-white">{title}</h3>
          </FadeSwap>
          <div className="flex items-center gap-2">
            {actions}
            <CardIconButton icon={X} size={20} onClick={onClose} title="Close" />
          </div>
        </div>
        {children}
      </div>
    </Expandable>
  )
})

export function SheetGrid({ children }) {
  return <div className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-2 pb-2">{children}</div>
}

export const CardIconButton = memo(function CardIconButton({ icon: Icon, size = 16, onClick, title, tone = 'neutral', disabled = false }) {
  const hover = tone === 'danger' ? 'hover:text-red-300 hover:bg-red-500/15' : 'hover:text-white hover:bg-white/10'
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation()
        onClick?.(e)
      }}
      disabled={disabled}
      title={title}
      aria-label={title}
      className={`ui-tap flex items-center justify-center rounded-lg p-1.5 text-white/55 transition-colors disabled:opacity-40 ${hover}`}
    >
      <Icon size={size} />
    </button>
  )
})

export const OptionCard = memo(function OptionCard({
  icon: Icon,
  tint,
  title,
  badge = null,
  subtitle = null,
  trailing = null,
  footer = null,
  selected = false,
  disabled = false,
  tone,
  onSelect,
  hint,
}) {
  const interactive = !!onSelect && !disabled && !selected
  return (
    <div
      className="ui-card flex flex-col gap-2 p-3"
      data-selected={selected ? 'true' : 'false'}
      data-interactive={interactive ? 'true' : 'false'}
      data-disabled={disabled ? 'true' : 'false'}
      data-tone={tone}
      style={tint ? { '--card-tint': tint } : undefined}
      onClick={interactive ? onSelect : undefined}
      role={onSelect ? 'button' : undefined}
      aria-pressed={onSelect ? selected : undefined}
      aria-disabled={disabled || undefined}
      title={hint}
    >
      <div className="flex items-center gap-3">
        {Icon && (
          <span className="ui-card-icon">
            <Icon size={18} />
          </span>
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 min-w-0 text-sm font-medium text-white">
            <div className="flex items-center gap-2 flex-1 min-w-0">{title}</div>
            {badge}
          </div>
          {subtitle && <div className="mt-0.5 text-xs text-white/55">{subtitle}</div>}
        </div>
        {trailing && <div className="flex items-center gap-1 flex-shrink-0">{trailing}</div>}
      </div>
      {footer}
    </div>
  )
})
