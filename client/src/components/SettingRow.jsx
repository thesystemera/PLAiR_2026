import { FadeSwap } from './Motion'
import { PRESETS } from '../lib/motion'

export const SettingRow = ({ icon: Icon, label, color = "text-purple-400", children, headerContent }) => (
  <div className="bg-white/5 p-3 rounded-lg">
    <div className="flex items-center justify-between mb-2">
      <div className="flex items-center gap-2">
        <Icon size={14} className={color} />
        <label className="text-xs font-semibold text-gray-300">{label}</label>
      </div>
      {headerContent}
    </div>
    {children}
  </div>
)

export const ToggleChip = ({ on, onClick, disabled = false, activeClassName = 'bg-purple-500 text-white', label }) => (
  <button
    type="button"
    onClick={onClick}
    disabled={disabled}
    aria-pressed={on}
    aria-label={label}
    className={`ui-press relative overflow-hidden min-w-[3rem] px-3 py-1 rounded text-xs font-medium transition disabled:opacity-50 ${on ? activeClassName : 'bg-dark-hover text-gray-400'}`}
  >
    <FadeSwap swapKey={on ? 'on' : 'off'} preset={PRESETS.badgeSwap} className="block text-center">
      {on ? 'ON' : 'OFF'}
    </FadeSwap>
  </button>
)
