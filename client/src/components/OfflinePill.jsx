import { memo } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { CloudOff } from 'lucide-react'
import { useUISelector } from '../contexts/UIStateContext'
import { useStorage } from '../contexts/StorageContext'
import { PRESETS } from '../lib/motion'

export const OfflinePill = memo(function OfflinePill() {
  const { audioState, interfaceState } = useUISelector(state => ({ audioState: state.audioState, interfaceState: state.interfaceState }))
  const { storageInfo } = useStorage()
  const count = storageInfo?.offlineTrackCount ?? storageInfo?.trackCount ?? 0
  const visible = !!audioState.offlineMode && !interfaceState.isFullscreenVisuals
  const noNetwork = audioState.connectionMode === 'offline'

  return (
    <OfflinePillView visible={visible} count={count} noNetwork={noNetwork} />
  )
})

const OfflinePillView = memo(function OfflinePillView({ visible, count, noNetwork }) {
  const label = count > 0 ? `Offline · playing your downloads (${count})` : 'Offline · no downloads yet'
  const title = noNetwork
    ? 'No internet connection. Your downloads keep playing, and likes sync when you are back online.'
    : 'PLAiR can\'t be reached right now. Your downloads keep playing, and likes sync when it is back.'

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          key="offline-pill"
          {...PRESETS.fadeSlide}
          className="fixed left-1/2 -translate-x-1/2 z-50 pointer-events-none"
          style={{ top: 'calc(var(--safe-top) + 0.5rem)' }}
        >
          <span
            className="inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold whitespace-nowrap text-amber-100 pointer-events-auto"
            style={{ borderColor: 'rgba(245, 158, 11, 0.5)', backgroundColor: 'rgba(10, 10, 12, 0.82)' }}
            title={title}
            role="status"
            aria-live="polite"
          >
            <CloudOff className="w-3.5 h-3.5 text-amber-400" aria-hidden="true" />
            {label}
          </span>
        </motion.div>
      )}
    </AnimatePresence>
  )
})
