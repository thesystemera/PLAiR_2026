import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { useUISelector } from './UIStateContext'
import { safeStorage } from '../lib/safeStorage'
import { logger } from '../lib/logger'
import { pauseSceneRendering } from '../lib/renderPause'
import { setMotionPolicy } from '../lib/microMotion'

export const QUALITY_TIERS = [
  { name: 'minimal', sceneDpr: 0.7, fpsCap: 30, glassTaps: 1, parallaxDpr: 1, parallaxFpsCap: 30, parallaxStepPx: 2 },
  { name: 'low', sceneDpr: 0.85, fpsCap: 30, glassTaps: 1, parallaxDpr: 1.5, parallaxFpsCap: 30, parallaxStepPx: 1.5 },
  { name: 'balanced', sceneDpr: 1.0, fpsCap: 0, glassTaps: 3, parallaxDpr: 2, parallaxFpsCap: 0, parallaxStepPx: 1 },
  { name: 'sharp', sceneDpr: 1.25, fpsCap: 0, glassTaps: 3, parallaxDpr: 2.5, parallaxFpsCap: 0, parallaxStepPx: 1 },
  { name: 'high', sceneDpr: 1.5, fpsCap: 0, glassTaps: 3, parallaxDpr: Infinity, parallaxFpsCap: 0, parallaxStepPx: 0 },
]

export const TOP_TIER = QUALITY_TIERS.length - 1
export const REFERENCE_SCENE_DPR = QUALITY_TIERS[TOP_TIER].sceneDpr

const FORCED_TIER_KEY = 'plair_quality_tier'
const LEARNED_TIER_KEY = 'plair_quality_auto_v1'
const WINDOW_FRAMES = 90
const SLOW_FRAME_SECONDS = 1 / 40
const FAST_FRAME_SECONDS = 1 / 54
const SLOW_WINDOWS_TO_DROP = 2
const FAST_WINDOWS_TO_RAISE = 4
const FAILURES_TO_LOCK = 2
const ACTIVE_RATIO = 0.8
const OVERLAY_FPS_CAP = 30

const SOFTWARE_GPU = /swiftshader|llvmpipe|softpipe|basic render|software/i
const WEAK_GPU = /mali-(4\d\d|t[678]\d\d|g31|g51|g52|g57|g68)|adreno \(tm\) (3\d\d|4\d\d|50\d|51\d|53\d|60\d|61[0-3])|powervr|sgx|ge8\d\d\d|vivante|videocore|intel.*hd graphics ([2-5]\d{2,3}|$)/i

const QualityContext = createContext(null)

function clampTier(tier) {
  return Math.max(0, Math.min(TOP_TIER, Math.round(tier)))
}

function readForcedTier() {
  const raw = safeStorage.get(FORCED_TIER_KEY)
  if (raw === null || raw === '' || raw === 'auto') return null
  const value = Number(raw)
  return Number.isFinite(value) ? clampTier(value) : null
}

function readLearnedTier() {
  const raw = safeStorage.get(LEARNED_TIER_KEY)
  if (raw === null || raw === '') return null
  const value = Number(raw)
  return Number.isFinite(value) ? clampTier(value) : null
}

function heuristicTier() {
  const memory = navigator.deviceMemory || 8
  const cores = navigator.hardwareConcurrency || 8
  const coarse = typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches
  if (memory <= 2 || cores <= 2) return 1
  if (coarse && (memory <= 3 || cores <= 4)) return 2
  if (coarse && !navigator.deviceMemory) return TOP_TIER - 1
  return TOP_TIER
}

function tierForRenderer(renderer) {
  if (!renderer) return TOP_TIER
  if (SOFTWARE_GPU.test(renderer)) return 0
  if (WEAK_GPU.test(renderer)) return 1
  return TOP_TIER
}

function useMediaQuery(query) {
  const [matches, setMatches] = useState(() => typeof window.matchMedia === 'function' && window.matchMedia(query).matches)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mql = window.matchMedia(query)
    const update = () => setMatches(mql.matches)
    update()
    mql.addEventListener?.('change', update)
    return () => mql.removeEventListener?.('change', update)
  }, [query])
  return matches
}

function useSaveData() {
  const [saveData, setSaveData] = useState(() => !!navigator.connection?.saveData)
  useEffect(() => {
    const connection = navigator.connection
    if (!connection?.addEventListener) return
    const update = () => setSaveData(!!connection.saveData)
    connection.addEventListener('change', update)
    return () => connection.removeEventListener('change', update)
  }, [])
  return saveData
}

export function QualityProvider({ children }) {
  const { settingsState } = useUISelector(state => ({ settingsState: state.settingsState }))
  const reduceMotion = useMediaQuery('(prefers-reduced-motion: reduce)')
  const saveData = useSaveData()
  const [forcedTier] = useState(readForcedTier)
  const [autoTier, setAutoTier] = useState(() => readLearnedTier() ?? heuristicTier())
  const [overlayCount, setOverlayCount] = useState(0)

  const visualQuality = settingsState.visualQuality || 'high'
  const userCeiling = visualQuality === 'low' ? 1 : visualQuality === 'medium' ? 2 : TOP_TIER
  const environmentCeiling = (reduceMotion || saveData || settingsState.dataSaverMode) ? 2 : TOP_TIER
  const ceiling = Math.min(userCeiling, environmentCeiling)
  const tier = forcedTier ?? Math.min(autoTier, ceiling)

  const governorRef = useRef({
    total: 0,
    count: 0,
    active: 0,
    slowWindows: 0,
    fastWindows: 0,
    failures: new Array(QUALITY_TIERS.length).fill(0),
    rendererChecked: false,
    tier,
    ceiling,
  })

  useEffect(() => {
    governorRef.current.tier = tier
    governorRef.current.ceiling = ceiling
  }, [tier, ceiling])

  useEffect(() => {
    setMotionPolicy({ tier, reduceMotion })
  }, [tier, reduceMotion])

  const changeTier = useCallback((next, reason) => {
    setAutoTier(prev => {
      const value = clampTier(next)
      if (value === prev) return prev
      logger.info(`[Quality] tier ${QUALITY_TIERS[prev].name} -> ${QUALITY_TIERS[value].name} (${reason})`)
      safeStorage.set(LEARNED_TIER_KEY, String(value))
      return value
    })
  }, [])

  const reportRenderer = useCallback((renderer) => {
    const governor = governorRef.current
    if (governor.rendererChecked) return
    governor.rendererChecked = true
    if (readLearnedTier() !== null) return
    const limit = tierForRenderer(renderer)
    if (limit < governor.tier) changeTier(limit, `gpu ${renderer}`)
  }, [changeTier])

  const reportFrame = useCallback((deltaSeconds, wantedRender) => {
    const governor = governorRef.current
    if (forcedTier !== null) return
    governor.total += Math.min(deltaSeconds, 0.1)
    governor.count++
    if (wantedRender) governor.active++
    if (governor.count < WINDOW_FRAMES) return

    const average = governor.total / governor.count
    const busy = governor.active / governor.count >= ACTIVE_RATIO
    governor.total = 0
    governor.count = 0
    governor.active = 0
    if (!busy) {
      governor.slowWindows = 0
      governor.fastWindows = 0
      return
    }

    const current = governor.tier
    if (average > SLOW_FRAME_SECONDS) {
      governor.fastWindows = 0
      governor.slowWindows++
      if (governor.slowWindows >= SLOW_WINDOWS_TO_DROP && current > 0) {
        governor.slowWindows = 0
        governor.failures[current]++
        changeTier(current - 1, `avg frame ${(average * 1000).toFixed(1)}ms`)
      }
      return
    }

    governor.slowWindows = 0
    if (average < FAST_FRAME_SECONDS && current < governor.ceiling && governor.failures[current + 1] < FAILURES_TO_LOCK) {
      governor.fastWindows++
      if (governor.fastWindows >= FAST_WINDOWS_TO_RAISE) {
        governor.fastWindows = 0
        changeTier(current + 1, `avg frame ${(average * 1000).toFixed(1)}ms`)
      }
    } else {
      governor.fastWindows = 0
    }
  }, [changeTier, forcedTier])

  const registerOverlay = useCallback(() => {
    setOverlayCount(count => count + 1)
    return () => setOverlayCount(count => Math.max(0, count - 1))
  }, [])

  const value = useMemo(() => {
    const settings = QUALITY_TIERS[tier]
    const capUnderOverlay = (cap) => overlayCount > 0 ? Math.min(cap || OVERLAY_FPS_CAP, OVERLAY_FPS_CAP) : cap
    return {
      tier,
      ...settings,
      fpsCap: capUnderOverlay(settings.fpsCap),
      parallaxFpsCap: capUnderOverlay(settings.parallaxFpsCap),
      isTopTier: tier === TOP_TIER,
      hasOverlay: overlayCount > 0,
      reduceMotion,
      reportFrame,
      reportRenderer,
      registerOverlay,
      pauseRendering: pauseSceneRendering,
    }
  }, [tier, overlayCount, reduceMotion, reportFrame, reportRenderer, registerOverlay])

  useEffect(() => {
    window.__plairQuality = () => ({ tier, name: QUALITY_TIERS[tier].name, forced: forcedTier !== null, ceiling, reduceMotion, saveData })
    return () => { delete window.__plairQuality }
  }, [tier, forcedTier, ceiling, reduceMotion, saveData])

  return (
    <QualityContext.Provider value={value}>
      {children}
    </QualityContext.Provider>
  )
}

export function useQuality() {
  const context = useContext(QualityContext)
  if (!context) throw new Error('useQuality must be used within QualityProvider')
  return context
}
