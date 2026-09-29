import { memo, useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { CloudOff } from 'lucide-react'
import { useUISelector } from '../contexts/UIStateContext'
import { useStorage } from '../contexts/StorageContext'
import { PRESETS } from '../lib/motion'

const OFFLINE_NOTICE_MS = 4000

export const OfflinePill = memo(function OfflinePill() {
  const { audioState, interfaceState } = useUISelector(state => ({ audioState: state.audioState, interfaceState: state.interfaceState }))
  const { storageInfo } = useStorage()
  const count = storageInfo?.offlineTrackCount ?? storageInfo?.trackCount ?? 0
  const offline = !!audioState.offlineMode
  const [announced, setAnnounced] = useState(false)
  const noNetwork = audioState.connectionMode === 'offline'

  useEffect(() => {
    if (!offline) return
    const timer = setTimeout(() => setAnnounced(true), OFFLINE_NOTICE_MS)
    return () => {
      clearTimeout(timer)
      setAnnounced(false)
    }
  }, [offline])

  const visible = offline && !announced && !interfaceState.isFullscreenVisuals

  return (
    <OfflinePillView visible={visible} count={count} noNetwork={noNetwork} />
  )
})

const OfflinePillView = memo(function OfflinePillView({ visible, count, noNetwork }) {
  const label = count > 0 ? `Offline · ${count} downloads` : 'Offline · no downloads'
  const title = noNetwork
    ? 'No internet connection. Your downloads keep playing, and likes sync when you are back online.'
    : 'PLAiR can\'t be reached right now. Your downloads keep playing, and likes sync when it is back.'

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          key="offline-pill"
          {...PRESETS.fadeSlide}
          className="fixed left-1/2 -translate-x-1/2 z-[110] pointer-events-none"
          style={{ top: 'calc(var(--safe-top) + 0.5rem)' }}
        >
          <span
            className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold whitespace-nowrap text-amber-100 pointer-events-auto"
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
