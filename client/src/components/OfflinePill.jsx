import { memo, useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { CloudOff } from 'lucide-react'
import { useUISelector } from '../contexts/UIStateContext'
import { useStorage } from '../contexts/StorageContext'
import { PRESETS } from '../lib/motion'
import { NoticeChip } from './Notice'

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
        <motion.div key="offline-pill" layout="position" {...PRESETS.fadeSlide}>
          <NoticeChip tone="warning" icon={CloudOff} text={label} title={title} />
        </motion.div>
      )}
    </AnimatePresence>
  )
})
