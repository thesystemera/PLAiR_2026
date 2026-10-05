import { memo, useCallback, useEffect, useRef, useState } from 'react'
import { useUISelector } from '../contexts/UIStateContext'
import { useQuality } from '../contexts/QualityContext'
import { depthArtRenderer } from '../lib/depthArtRenderer'
import { CSS_TRANSITION } from '../lib/motion'
import { useDepthMap } from '../hooks/useDepthMap'
import { ART_PACK_SIZE, PROFILE_PACK_SIZE, artPackCache, isMemoryKey, profilePackCache } from '../lib/mediaCache'

const INSTANT_MS = 150

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
  packUrl, packSize, packMissing = false, alt, onLoad, onError, className = 'absolute inset-0', artRef, artProps,
}) {
  const hostRef = useRef(null)
  const canvasRef = useRef(null)
  const callbacksRef = useRef({ onLoad, onError })
  const [drawn, setDrawn] = useState({ key: null, instant: false })
  const key = isLoadedImage(packUrl) ? packUrl : null

  useEffect(() => {
    callbacksRef.current = { onLoad, onError }
  }, [onLoad, onError])

  const setCanvas = useCallback((canvas) => {
    canvasRef.current = canvas
    if (typeof artRef === 'function') artRef(canvas)
  }, [artRef])

  useEffect(() => {
    if (!key) return
    const attachedAt = performance.now()
    return depthArtRenderer.attach({
      host: hostRef.current,
      canvas: canvasRef.current,
      packUrl: key,
      packSize,
      onDrawn: () => {
        setDrawn({ key, instant: performance.now() - attachedAt < INSTANT_MS })
        callbacksRef.current.onLoad?.({ currentTarget: canvasRef.current, target: canvasRef.current })
      },
      onFailed: () => callbacksRef.current.onError?.({ currentTarget: canvasRef.current, target: canvasRef.current }),
    })
  }, [key, packSize])

  useEffect(() => {
    if (packMissing) callbacksRef.current.onError?.({ currentTarget: canvasRef.current, target: canvasRef.current })
  }, [packMissing])

  const shown = key !== null && drawn.key === key

  return (
    <div ref={hostRef} className={className}>
      <canvas
        ref={setCanvas}
        role="img"
        aria-label={alt}
        className="absolute inset-0 w-full h-full pointer-events-none"
        style={{ opacity: shown ? 1 : 0, transition: shown && drawn.instant ? 'none' : CSS_TRANSITION.fadeOpacity }}
        {...artProps}
      />
    </div>
  )
})

export const TrackArt = memo(function TrackArt({ trackId, hasArtwork = true, ...props }) {
  const enabled = !!trackId && hasArtwork !== false
  const { url, missing } = useDepthMap(artPackCache, trackId, enabled)
  return <DepthArt packUrl={url} packSize={ART_PACK_SIZE} packMissing={missing || !enabled} {...props} />
})

export const ProfileArt = memo(function ProfileArt({ userId, hasPicture = true, ...props }) {
  const enabled = !!userId && hasPicture !== false
  const { url, missing } = useDepthMap(profilePackCache, userId, enabled)
  return <DepthArt packUrl={url} packSize={PROFILE_PACK_SIZE} packMissing={missing || !enabled} {...props} />
})
