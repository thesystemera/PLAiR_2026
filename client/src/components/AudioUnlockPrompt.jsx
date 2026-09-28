import { memo } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Volume2 } from 'lucide-react'
import { useUIState } from '../contexts/UIStateContext'
import { usePlayback } from '../contexts/PlaybackContext'
import { PRESETS } from '../lib/motion'

export const AudioUnlockPrompt = memo(function AudioUnlockPrompt() {
  const { engineState, interfaceState } = useUIState()
  const { togglePlay } = usePlayback()
  const visible = !!engineState.audioNeedsTap && !!engineState.isActiveDevice && !interfaceState.isFullscreenVisuals

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          key="audio-unlock"
          {...PRESETS.fadeSlide}
          className="fixed left-1/2 -translate-x-1/2 z-[60]"
          style={{ bottom: `calc(${interfaceState.playerHeight || 0}px + 1rem)` }}
        >
          <button
            type="button"
            onClick={() => void togglePlay()}
            className="ui-press inline-flex items-center gap-2 rounded-full px-5 py-3 text-sm font-semibold text-white shadow-lg"
            style={{ backgroundColor: 'rgba(16, 185, 129, 0.92)' }}
            aria-label="Tap to start audio"
          >
            <Volume2 className="w-4 h-4" aria-hidden="true" />
            Tap to start audio
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  )
})
