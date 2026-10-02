import { memo } from 'react'
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion'
import { ChevronDown } from 'lucide-react'
import { CSS_TRANSITION, PRESETS, VARIANTS } from '../lib/motion'

export function Expandable({ open, children, className = '', innerClassName = '', animateOnMount = false }) {
  const reduceMotion = useReducedMotion()
  const variants = reduceMotion ? VARIANTS.expandReduced : VARIANTS.expand

  return (
    <AnimatePresence initial={animateOnMount}>
      {open && (
        <motion.div
          key="expandable"
          className={className}
          variants={variants}
          initial="collapsed"
          animate="open"
          exit="collapsed"
        >
          <motion.div variants={VARIANTS.expandContent} className={innerClassName}>
            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

export const ExpandChevron = memo(function ExpandChevron({ open, size = 16, className = '' }) {
  return (
    <ChevronDown
      size={size}
      className={className}
      style={{ transform: open ? 'rotate(180deg)' : 'none', transition: CSS_TRANSITION.chevron }}
    />
  )
})

function ExpandToggle({ open, onToggle, icon: Icon, iconClassName = '', title, children, className = '' }) {
  return (
    <button
      type="button"
      onClick={() => onToggle(!open)}
      aria-expanded={open}
      className={`ui-press-soft w-full flex items-center justify-between p-3 bg-white/5 hover:bg-white/10 rounded-lg transition ${className}`}
    >
      <div className="flex items-center gap-2">
        {Icon && <Icon size={16} className={iconClassName} />}
        <span className="text-sm font-semibold text-gray-300">{title}</span>
        {children}
      </div>
      <ExpandChevron open={open} />
    </button>
  )
}

export function ExpandSection({ open, onToggle, icon, iconClassName, title, meta, className = '', contentClassName = '', children }) {
  return (
    <div className={className}>
      <ExpandToggle open={open} onToggle={onToggle} icon={icon} iconClassName={iconClassName} title={title}>
        {meta}
      </ExpandToggle>
      <Expandable open={open} innerClassName={`pt-4 ${contentClassName}`}>
        {children}
      </Expandable>
    </div>
  )
}

export function FadeSwap({ swapKey, children, className = '', mode = 'popLayout', preset = PRESETS.panelSwap, animateOnMount = false }) {
  return (
    <AnimatePresence initial={animateOnMount} mode={mode}>
      <motion.div key={swapKey} className={className} {...preset}>
        {children}
      </motion.div>
    </AnimatePresence>
  )
}
