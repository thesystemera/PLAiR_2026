import { memo, useEffect } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useUISelector } from '../contexts/UIStateContext'
import { PRESETS } from '../lib/motion'
import { ON_AIR_LAMP, getOnAirSegment } from '../lib/themeManager'

const LAMP_GLOW = `0 0 6px 2px ${ON_AIR_LAMP}d9, 0 0 14px 4px #f59e0b59`

export const OnAirLamp = memo(function OnAirLamp({ paused = false, large = false }) {
  const size = large ? 'w-2.5 h-2.5' : 'w-2 h-2'
  return (
    <span className={`relative inline-flex flex-shrink-0 ${size}`} aria-hidden="true">
      <span
        className="ui-breathe absolute inset-0 rounded-full"
        data-paused={paused ? 'true' : 'false'}
        style={{ boxShadow: LAMP_GLOW, opacity: paused ? 0.25 : undefined }}
      />
      <span
        className="relative w-full h-full rounded-full transition-opacity duration-quick"
        style={{ backgroundColor: ON_AIR_LAMP, opacity: paused ? 0.45 : 1 }}
      />
    </span>
  )
})

const ON_AIR_NOTICE = 'on-air'

export function OnAirNotice() {
  const { talkBreak, showNotice, hideNotice } = useUISelector(state => ({
    talkBreak: state.engineState.talkBreak,
    showNotice: state.showNotice,
    hideNotice: state.hideNotice,
  }))

  useEffect(() => {
    if (!talkBreak) {
      hideNotice(ON_AIR_NOTICE)
      return
    }
    const segment = getOnAirSegment(talkBreak)
    showNotice({
      key: ON_AIR_NOTICE,
      sticky: true,
      dismissible: false,
      priority: 0,
      icon: null,
      borderColor: `${segment.color}80`,
      title: talkBreak.title || segment.label,
      content: (
        <>
          <OnAirLamp paused={talkBreak.paused} />
          <span className="font-bold tracking-wide text-red-100">ON AIR</span>
          <span className="font-medium truncate" style={{ color: segment.color }}>· {segment.label}</span>
        </>
      ),
    })
  }, [talkBreak, showNotice, hideNotice])

  useEffect(() => () => hideNotice(ON_AIR_NOTICE), [hideNotice])

  return null
}

const EDGE_TOP = 'linear-gradient(to bottom, var(--on-air-lamp), transparent)'
const EDGE_BOTTOM = 'linear-gradient(to top, var(--on-air-accent), transparent)'
const EDGE_LEFT = 'linear-gradient(to right, var(--on-air-accent), transparent)'
const EDGE_RIGHT = 'linear-gradient(to left, var(--on-air-accent), transparent)'

export const OnAirFrame = memo(function OnAirFrame() {
  const { engineState, interfaceState } = useUISelector(state => ({ engineState: state.engineState, interfaceState: state.interfaceState }))
  return <OnAirFrameView talkBreak={engineState.talkBreak} bottom={interfaceState.playerHeight || 0} />
})

const OnAirFrameView = memo(function OnAirFrameView({ talkBreak, bottom }) {
  const segment = getOnAirSegment(talkBreak)
  const paused = talkBreak?.paused ? 'true' : 'false'
  const dim = talkBreak?.paused ? 0.5 : undefined

  return (
    <AnimatePresence>
      {talkBreak && (
        <motion.div
          key="on-air-frame"
          {...PRESETS.fadeSlow}
          aria-hidden="true"
          className="fixed inset-0 z-40 pointer-events-none"
          style={{ '--on-air-lamp': `${ON_AIR_LAMP}59`, '--on-air-accent': `${segment.color}40` }}
        >
          <div className="ui-breathe absolute inset-x-0 top-0 h-16" data-paused={paused} style={{ background: EDGE_TOP, opacity: dim }} />
          <div className="ui-breathe absolute inset-x-0 h-12" data-paused={paused} style={{ bottom, background: EDGE_BOTTOM, opacity: dim }} />
          <div className="ui-breathe absolute top-0 left-0 w-5" data-paused={paused} style={{ bottom, background: EDGE_LEFT, opacity: dim }} />
          <div className="ui-breathe absolute top-0 right-0 w-5" data-paused={paused} style={{ bottom, background: EDGE_RIGHT, opacity: dim }} />
        </motion.div>
      )}
    </AnimatePresence>
  )
})
