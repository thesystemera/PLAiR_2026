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

export function FadeSwap({ swapKey, children, className = '', mode = 'popLayout', preset = PRESETS.panelSwap, animateOnMount = false }) {
  return (
    <AnimatePresence initial={animateOnMount} mode={mode}>
      <motion.div key={swapKey} className={className} {...preset}>
        {children}
      </motion.div>
    </AnimatePresence>
  )
}
