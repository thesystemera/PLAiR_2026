import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useUISelector } from '../contexts/UIStateContext'
import { useQuality } from '../contexts/QualityContext'
import { depthArtRenderer } from '../lib/depthArtRenderer'
import { useDepthMap } from '../hooks/useDepthMap'
import { getFallbackGradientClass } from '../lib/themeManager'
import { DURATION } from '../lib/motion'
import { PACK_SIZES, isMemoryKey, packCache, profilePackCache } from '../lib/mediaCache'

const HIDDEN = { opacity: 0 }
const SHOWN = { transition: 'opacity var(--dur-quick) var(--ease-decelerate)' }
const PLACEHOLDER = { containerType: 'size' }
const PLACEHOLDER_NOTE = { fontSize: '40cqmin', lineHeight: 1 }
const FRONT = { zIndex: 1 }
const BACK = { zIndex: 0 }
const SWAP_LIMIT_MS = 1500
const FADE_MS = DURATION.theme * 1000

const isLoadedImage = url => typeof url === 'string' && (url.startsWith('blob:') || url.startsWith('http') || url.startsWith('/') || isMemoryKey(url))

export function DepthArtBridge() {
  const { gyroscopeRef, mouseRef } = useUISelector(state => ({ gyroscopeRef: state.gyroscopeRef, mouseRef: state.mouseRef }))
  const { parallaxDpr, parallaxStepPx, reduceMotion } = useQuality()
  const litArtwork = useUISelector(state => state.settingsState.litArtwork)

  useEffect(() => {
    depthArtRenderer.configure({
      dpr: parallaxDpr,
      stepPx: parallaxStepPx,
      reduceMotion,
      lit: litArtwork !== false,
      gyroRef: gyroscopeRef,
      mouseRef,
    })
  }, [parallaxDpr, parallaxStepPx, reduceMotion, litArtwork, gyroscopeRef, mouseRef])

  return null
}

const DepthArt = memo(function DepthArt({
  packUrl, identity, packMissing = false, intensity, held = false, alt, onLoad, onError, onHost, placeholder = null, className = 'absolute inset-0', artRef, artProps,
}) {
  const hostRef = useRef(null)
  const canvasRef = useRef(null)
  const callbacksRef = useRef({ onLoad, onError })
  const [drawnFor, setDrawnFor] = useState(null)
  const key = isLoadedImage(packUrl) ? packUrl : null
  const identityRef = useRef(identity)

  useLayoutEffect(() => {
    identityRef.current = identity
  }, [identity])

  useEffect(() => {
    callbacksRef.current = { onLoad, onError }
  }, [onLoad, onError])

  const setHost = useCallback((host) => {
    hostRef.current = host
    onHost?.(host)
  }, [onHost])

  const setCanvas = useCallback((canvas) => {
    canvasRef.current = canvas
    if (typeof artRef === 'function') artRef(canvas)
  }, [artRef])

  useEffect(() => {
    if (!key) return
    return depthArtRenderer.attach({
      host: hostRef.current,
      canvas: canvasRef.current,
      packUrl: key,
      intensity,
      onDrawn: () => {
        setDrawnFor(identityRef.current)
        callbacksRef.current.onLoad?.({ currentTarget: canvasRef.current, target: canvasRef.current })
      },
      onFailed: () => callbacksRef.current.onError?.({ currentTarget: canvasRef.current, target: canvasRef.current }),
    })
  }, [key, intensity])

  useEffect(() => {
    if (key) depthArtRenderer.hold(canvasRef.current, held)
  }, [key, held])

  useEffect(() => {
    if (packMissing) callbacksRef.current.onError?.({ currentTarget: canvasRef.current, target: canvasRef.current })
  }, [packMissing])

  const shown = key !== null && drawnFor === identity

  return (
    <div ref={setHost} data-depth-art className={className}>
      {!shown && placeholder}
      <canvas
        ref={setCanvas}
        role="img"
        aria-label={alt}
        className="absolute inset-0 w-full h-full pointer-events-none"
        style={shown ? SHOWN : HIDDEN}
        {...artProps}
      />
    </div>
  )
})

function usePackSize(intensity) {
  const [size, setSize] = useState(0)
  const observerRef = useRef(null)
  const onHost = useCallback((host) => {
    observerRef.current?.disconnect()
    observerRef.current = null
    if (!host) return
    const measure = () => {
      const side = Math.max(host.offsetWidth, host.offsetHeight)
      if (side >= 1) setSize(depthArtRenderer.packSizeFor(side, intensity))
    }
    observerRef.current = new ResizeObserver(measure)
    observerRef.current.observe(host)
    measure()
  }, [intensity])
  return [size, onHost]
}

const TrackPlaceholder = memo(function TrackPlaceholder({ trackId }) {
  return (
    <div className={`absolute inset-0 flex items-center justify-center bg-gradient-to-br ${getFallbackGradientClass(trackId)}`} style={PLACEHOLDER}>
      <span className="text-white opacity-80 select-none" style={PLACEHOLDER_NOTE} aria-hidden="true">🎵</span>
    </div>
  )
})

export const TrackArt = memo(function TrackArt({ trackId, hasArtwork = true, intensity, ...props }) {
  const [size, onHost] = usePackSize(intensity)
  const hasArt = !!trackId && hasArtwork !== false
  const { url, missing } = useDepthMap(packCache(size || PACK_SIZES[0]), trackId, hasArt && size > 0)
  return (
    <DepthArt
      packUrl={size ? url : null}
      identity={trackId}
      packMissing={missing || !hasArt}
      intensity={intensity}
      onHost={onHost}
      placeholder={<TrackPlaceholder trackId={trackId} />}
      {...props}
    />
  )
})

export const ProfileArt = memo(function ProfileArt({ userId, hasPicture = true, ...props }) {
  const enabled = !!userId && hasPicture !== false
  const { url, missing } = useDepthMap(profilePackCache, userId, enabled)
  return <DepthArt packUrl={url} identity={userId} packMissing={missing || !enabled} {...props} />
})

let layerCount = 0

function nextLayers(layers, trackId, hasArt) {
  const target = { ...layers, trackId, hasArt }
  const same = layer => layer?.trackId === trackId && layer.hasArt === hasArt
  const make = () => ({ id: ++layerCount, trackId, hasArt })
  if (!trackId) return { ...target, shown: null, next: null, leaving: false }
  if (same(layers.next)) return target
  if (layers.next && layers.leaving) return { ...target, shown: layers.next, next: make(), leaving: false }
  if (same(layers.shown)) return { ...target, next: null }
  return { ...target, next: make() }
}

const NO_LAYERS = { shown: null, next: null, leaving: false }

export const TrackArtCrossfade = memo(function TrackArtCrossfade({ trackId, hasArtwork = true, alt, intensity, onShow }) {
  const hasArt = hasArtwork !== false
  const [layers, setLayers] = useState(() => nextLayers(NO_LAYERS, trackId, hasArt))
  const layersRef = useRef(layers)
  const onShowRef = useRef(onShow)

  if (layers.trackId !== trackId || layers.hasArt !== hasArt) setLayers(nextLayers(layers, trackId, hasArt))

  useLayoutEffect(() => {
    layersRef.current = layers
    onShowRef.current = onShow
  })

  const ready = useCallback((id) => {
    const current = layersRef.current
    if (current.next?.id !== id || current.leaving) return
    const promoted = current.shown ? { ...current, leaving: true } : { ...current, shown: current.next, next: null }
    layersRef.current = promoted
    setLayers(promoted)
    onShowRef.current?.()
  }, [])

  const nextId = layers.next?.id
  useEffect(() => {
    if (!nextId || layers.leaving) return
    const timer = setTimeout(() => ready(nextId), SWAP_LIMIT_MS)
    return () => clearTimeout(timer)
  }, [nextId, layers.leaving, ready])

  const shownId = layers.shown?.id
  useEffect(() => {
    if (!layers.leaving) return
    const timer = setTimeout(() => {
      setLayers(current => (current.leaving && current.shown?.id === shownId ? { ...current, shown: current.next, next: null, leaving: false } : current))
    }, FADE_MS)
    return () => clearTimeout(timer)
  }, [layers.leaving, shownId])

  return [layers.shown, layers.next].filter(Boolean).map((layer) => {
    const front = layer === layers.shown
    const onDone = front ? undefined : () => ready(layer.id)
    return (
      <div
        key={layer.id}
        className={`absolute inset-0 transition-opacity duration-theme ${front && layers.leaving ? 'opacity-0' : 'opacity-100'}`}
        style={front ? FRONT : BACK}
      >
        <TrackArt trackId={layer.trackId} hasArtwork={layer.hasArt} intensity={intensity} held={front && layers.leaving} alt={alt} onLoad={onDone} onError={onDone} />
      </div>
    )
  })
})
