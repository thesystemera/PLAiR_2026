export const ToggleChip = ({ on, onClick, disabled = false, activeClassName = 'bg-purple-500 text-white', label }) => (
  <button
    type="button"
    onClick={onClick}
    disabled={disabled}
    aria-pressed={on}
    aria-label={label}
    className={`ui-press px-3 py-1 rounded text-xs font-medium transition disabled:opacity-50 ${on ? activeClassName : 'bg-dark-hover text-gray-400'}`}
  >
    {on ? 'ON' : 'OFF'}
  </button>
)
