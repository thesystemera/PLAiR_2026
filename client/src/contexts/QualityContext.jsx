import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { useUISelector } from './UIStateContext'
import { safeStorage } from '../lib/safeStorage'
import { logger } from '../lib/logger'
import { pauseSceneRendering } from '../lib/renderPause'
import { setMotionPolicy } from '../lib/microMotion'
import { screenRefreshMs, watchScreenRefresh } from '../lib/screenRefresh'

const LEVELS = ['low', 'medium', 'high']
const LEVEL_SETTINGS = {
  low: { sceneDpr: 0.85, glassTaps: 1, parallaxDpr: 1.5, parallaxStepPx: 1.5 },
  medium: { sceneDpr: 1.0, glassTaps: 3, parallaxDpr: 2, parallaxStepPx: 1 },
  high: { sceneDpr: 1.5, glassTaps: 3, parallaxDpr: Infinity, parallaxStepPx: 0 },
}
const TOP_LEVEL = LEVELS.length - 1
export const REFERENCE_SCENE_DPR = LEVEL_SETTINGS.high.sceneDpr

const LEARNED_LEVEL_KEY = 'plair_quality_auto_v2'
const WINDOW_FRAMES = 90
const SLOW_FRAME_RATIO = 1.5
const FAST_FRAME_RATIO = 1.11
const SLOW_WINDOWS_TO_DROP = 2
const FAST_WINDOWS_TO_RAISE = 4
const FAILURES_TO_LOCK = 2
const ACTIVE_RATIO = 0.8

const SOFTWARE_GPU = /swiftshader|llvmpipe|softpipe|basic render|software/i
const WEAK_GPU = /mali-(4\d\d|t[678]\d\d|g31|g51|g52|g57|g68)|adreno \(tm\) (3\d\d|4\d\d|50\d|51\d|53\d|60\d|61[0-3])|powervr|sgx|ge8\d\d\d|vivante|videocore|intel.*hd graphics ([2-5]\d{2,3}|$)/i

const QualityContext = createContext(null)

function clampLevel(level) {
  return Math.max(0, Math.min(TOP_LEVEL, Math.round(level)))
}

function readLearnedLevel() {
  const raw = safeStorage.get(LEARNED_LEVEL_KEY)
  if (raw === null || raw === '') return null
  const value = Number(raw)
  return Number.isFinite(value) ? clampLevel(value) : null
}

function heuristicLevel() {
  const memory = navigator.deviceMemory || 8
  const cores = navigator.hardwareConcurrency || 8
  const coarse = typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches
  if (memory <= 2 || cores <= 2) return 0
  if (coarse && (memory <= 4 || cores <= 4 || !navigator.deviceMemory)) return 1
  return TOP_LEVEL
}

function levelForRenderer(renderer) {
  if (!renderer) return TOP_LEVEL
  if (SOFTWARE_GPU.test(renderer) || WEAK_GPU.test(renderer)) return 0
  return TOP_LEVEL
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
  const { visualQuality, dataSaverMode } = useUISelector(state => ({
    visualQuality: state.settingsState.visualQuality,
    dataSaverMode: state.settingsState.dataSaverMode,
  }))
  const reduceMotion = useMediaQuery('(prefers-reduced-motion: reduce)')
  const saveData = useSaveData()
  const [autoLevel, setAutoLevel] = useState(() => readLearnedLevel() ?? heuristicLevel())

  const auto = !LEVELS.includes(visualQuality)
  const autoCeiling = (reduceMotion || saveData || dataSaverMode) ? 1 : TOP_LEVEL
  const autoIndex = Math.min(autoLevel, autoCeiling)
  const levelIndex = auto ? autoIndex : LEVELS.indexOf(visualQuality)
  const level = LEVELS[levelIndex]

  const governorRef = useRef({
    total: 0,
    count: 0,
    active: 0,
    slowWindows: 0,
    fastWindows: 0,
    failures: new Array(LEVELS.length).fill(0),
    rendererChecked: false,
    auto,
    autoIndex,
    ceiling: autoCeiling,
  })

  useEffect(() => {
    Object.assign(governorRef.current, { auto, autoIndex, ceiling: autoCeiling })
  }, [auto, autoIndex, autoCeiling])

  useEffect(() => {
    setMotionPolicy({ tier: levelIndex, reduceMotion })
  }, [levelIndex, reduceMotion])

  useEffect(() => (auto ? watchScreenRefresh() : undefined), [auto])

  const changeLevel = useCallback((next, reason) => {
    setAutoLevel(prev => {
      const value = clampLevel(next)
      if (value === prev) return prev
      logger.info(`[Quality] auto ${LEVELS[prev]} -> ${LEVELS[value]} (${reason})`)
      safeStorage.set(LEARNED_LEVEL_KEY, String(value))
      return value
    })
  }, [])

  const reportRenderer = useCallback((renderer) => {
    const governor = governorRef.current
    if (governor.rendererChecked) return
    governor.rendererChecked = true
    if (readLearnedLevel() !== null) return
    const limit = levelForRenderer(renderer)
    if (limit < governor.autoIndex) changeLevel(limit, `gpu ${renderer}`)
  }, [changeLevel])

  const reportFrame = useCallback((deltaSeconds, wantedRender) => {
    const governor = governorRef.current
    if (!governor.auto) return
    governor.total += Math.min(deltaSeconds, 0.1)
    governor.count++
    if (wantedRender) governor.active++
    if (governor.count < WINDOW_FRAMES) return

    const average = governor.total / governor.count
    const refresh = screenRefreshMs() / 1000
    const busy = governor.active / governor.count >= ACTIVE_RATIO
    governor.total = 0
    governor.count = 0
    governor.active = 0
    if (!busy) {
      governor.slowWindows = 0
      governor.fastWindows = 0
      return
    }

    const current = governor.autoIndex
    if (average > refresh * SLOW_FRAME_RATIO) {
      governor.fastWindows = 0
      governor.slowWindows++
      if (governor.slowWindows >= SLOW_WINDOWS_TO_DROP && current > 0) {
        governor.slowWindows = 0
        governor.failures[current]++
        changeLevel(current - 1, `avg frame ${(average * 1000).toFixed(1)}ms`)
      }
      return
    }

    governor.slowWindows = 0
    if (average < refresh * FAST_FRAME_RATIO && current < governor.ceiling && governor.failures[current + 1] < FAILURES_TO_LOCK) {
      governor.fastWindows++
      if (governor.fastWindows >= FAST_WINDOWS_TO_RAISE) {
        governor.fastWindows = 0
        changeLevel(current + 1, `avg frame ${(average * 1000).toFixed(1)}ms`)
      }
    } else {
      governor.fastWindows = 0
    }
  }, [changeLevel])

  const value = useMemo(() => ({
    level,
    levelIndex,
    auto,
    ...LEVEL_SETTINGS[level],
    isHigh: level === 'high',
    reduceMotion,
    reportFrame,
    reportRenderer,
    pauseRendering: pauseSceneRendering,
  }), [level, levelIndex, auto, reduceMotion, reportFrame, reportRenderer])

  useEffect(() => {
    window.__plairQuality = () => ({ level, auto, autoLevel: LEVELS[autoIndex], ceiling: LEVELS[autoCeiling], reduceMotion, saveData })
    return () => { delete window.__plairQuality }
  }, [level, auto, autoIndex, autoCeiling, reduceMotion, saveData])

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
