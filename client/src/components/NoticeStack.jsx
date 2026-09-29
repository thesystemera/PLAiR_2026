import { memo, useMemo } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useUISelector } from '../contexts/UIStateContext'
import { PRESETS } from '../lib/motion'
import { NoticeChip } from './Notice'

const byPriority = (a, b) => a.priority - b.priority || a.seq - b.seq

export const NoticeStack = memo(function NoticeStack() {
  const { notices, fullscreen, hideNotice } = useUISelector(state => ({
    notices: state.notices,
    fullscreen: state.interfaceState.isFullscreenVisuals,
    hideNotice: state.hideNotice,
  }))
  const ordered = useMemo(() => [...notices].sort(byPriority), [notices])

  return (
    <div
      className="fixed left-1/2 -translate-x-1/2 z-[110] pointer-events-none flex flex-col items-center gap-1.5"
      style={{ top: 'calc(var(--safe-top) + 0.5rem)' }}
      role="status"
      aria-live="polite"
    >
      <AnimatePresence initial={false}>
        {!fullscreen && ordered.map(({ key, dismissible, sticky: _sticky, priority: _priority, seq: _seq, ...notice }) => (
          <motion.div key={key} layout="position" {...PRESETS.fadeSlide}>
            <NoticeChip
              {...notice}
              title={notice.title || (dismissible ? 'Tap to dismiss' : undefined)}
              onClick={dismissible ? () => hideNotice(key) : undefined}
            />
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  )
})
