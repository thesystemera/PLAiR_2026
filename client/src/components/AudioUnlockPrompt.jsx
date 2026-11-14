import { memo } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Volume2 } from 'lucide-react'
import { useUISelector } from '../contexts/UIStateContext'
import { usePlaybackActions } from '../contexts/PlaybackContext'
import { PRESETS } from '../lib/motion'
import { insetBottomWithGap } from '../lib/playerInset'
import { usePlayerInset } from '../hooks/usePlayerInset'

export const AudioUnlockPrompt = memo(function AudioUnlockPrompt() {
  const visible = useUISelector(state => !!state.engineState.audioNeedsTap && !!state.engineState.isActiveDevice && !state.interfaceState.isFullscreenVisuals)
  const abovePlayer = usePlayerInset(insetBottomWithGap)
  const { togglePlay } = usePlaybackActions()

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          key="audio-unlock"
          {...PRESETS.fadeSlide}
          ref={abovePlayer}
          className="fixed left-1/2 -translate-x-1/2 z-[60]"
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
