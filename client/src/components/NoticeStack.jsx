import { memo } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { AlertTriangle, CheckCircle2, Info, XCircle } from 'lucide-react'
import { useUISelector } from '../contexts/UIStateContext'
import { PRESETS } from '../lib/motion'
import { NoticeChip, setNoticeSlot } from './Notice'
import { ActivityChip } from './DJActivity'
import { OfflinePill } from './OfflinePill'

const TOAST_ICONS = {
  success: CheckCircle2,
  error: XCircle,
  warning: AlertTriangle,
  info: Info,
}

export const NoticeStack = memo(function NoticeStack() {
  const { toasts, calls, fullscreen, offline, removeToast } = useUISelector(state => ({
    toasts: state.toasts,
    calls: state.engineState.djActivity,
    fullscreen: state.interfaceState.isFullscreenVisuals,
    offline: state.audioState.offlineMode,
    removeToast: state.removeToast,
  }))

  return (
    <div
      className="fixed left-1/2 -translate-x-1/2 z-[110] pointer-events-none flex flex-col items-center gap-1.5"
      style={{ top: 'calc(var(--safe-top) + 0.5rem)' }}
      role="status"
      aria-live="polite"
    >
      <div ref={setNoticeSlot} className="contents" />
      <OfflinePill />
      <AnimatePresence initial={false}>
        {!fullscreen && toasts.map(toast => (
          <motion.div key={toast.id} layout="position" {...PRESETS.fadeSlide}>
            <NoticeChip
              tone={toast.type}
              icon={TOAST_ICONS[toast.type] || Info}
              text={toast.message}
              title="Tap to dismiss"
              onClick={() => removeToast(toast.id)}
            />
          </motion.div>
        ))}
        {!fullscreen && !offline && (calls || []).map(call => (
          <motion.div key={call.id} layout="position" {...PRESETS.fadeSlide}>
            <ActivityChip call={call} />
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  )
})
