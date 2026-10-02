import { createContext, useContext, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { Expandable, FadeSwap } from './Motion'
import './SettingTile.css'

const HintContext = createContext(null)

export function TileGroup({ title, meta, drawerKey = null, drawer = null, footer = null, nested = false, children, className = '' }) {
  const [hint, setHint] = useState('')
  const open = !!drawerKey && !!drawer

  return (
    <section className={`${nested ? 'tile-group-nested' : 'tile-group'} ${className}`}>
      {title && (
        <div className="tile-group-title">
          <span>{title}</span>
          {meta}
        </div>
      )}
      <HintContext.Provider value={setHint}>
        <div className="tile-grid">{children}</div>
      </HintContext.Provider>
      {footer}
      {!open && hint && <p className="tile-hint ui-fade-in">{hint}</p>}
      <Expandable open={open} innerClassName="pt-3">
        <FadeSwap swapKey={drawerKey || 'none'}>{drawer}</FadeSwap>
      </Expandable>
    </section>
  )
}

export function SettingTile({
  icon: Icon,
  label,
  value,
  color = '#a78bfa',
  on = false,
  open = false,
  cycle = false,
  drawer = false,
  danger = false,
  state = null,
  busy = false,
  disabled = false,
  hint = '',
  onClick,
}) {
  const setHint = useContext(HintContext)
  const classes = [
    'setting-tile ui-tap',
    on && 'is-on',
    open && 'is-open',
    cycle && 'is-cycle has-label-pad',
    drawer && 'is-drawer has-label-pad',
    danger && 'is-danger',
    state && 'has-state',
  ].filter(Boolean).join(' ')
  const StateIcon = state?.icon
  const style = {
    ...(danger ? {} : { '--tile-color': color }),
    ...(state?.color ? { '--state-color': state.color } : {}),
  }
  const showHint = () => { if (hint) setHint?.(hint) }

  return (
    <button
      type="button"
      className={classes}
      style={style}
      onClick={(event) => { showHint(); onClick?.(event) }}
      onPointerEnter={showHint}
      onFocus={showHint}
      disabled={disabled || busy}
      aria-pressed={cycle || drawer ? undefined : on}
      aria-expanded={drawer ? open : undefined}
      aria-label={value ? `${label}: ${value}` : label}
      title={hint || undefined}
    >
      {StateIcon && <span className="setting-tile-state" aria-hidden="true"><StateIcon strokeWidth={1.5} /></span>}
      <span className="setting-tile-label">{label}</span>
      <span className="setting-tile-icon">
        {busy ? <Loader2 size={28} className="animate-spin" /> : Icon && <Icon size={30} strokeWidth={1.75} />}
      </span>
      {value !== undefined && value !== null && value !== '' && <span className="setting-tile-value">{value}</span>}
    </button>
  )
}

export function nextOf(options, current) {
  const index = options.findIndex(option => option === current)
  return options[(index + 1) % options.length]
}
