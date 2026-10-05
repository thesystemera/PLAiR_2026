import { memo, useEffect, useRef, useState } from 'react'
import { useUISelector } from '../contexts/UIStateContext'
import { useQuality } from '../contexts/QualityContext'
import { depthArtRenderer } from '../lib/depthArtRenderer'
import { CSS_TRANSITION } from '../lib/motion'
import { useDepthMap } from '../hooks/useDepthMap'
import { depthThumbCache, isMemoryKey, normalThumbCache, profileDepthCache, profileNormalCache } from '../lib/mediaCache'

const isLoadedImage = url => typeof url === 'string' && (url.startsWith('blob:') || url.startsWith('http') || url.startsWith('/') || isMemoryKey(url))

export function DepthArtBridge() {
  const { gyroscopeRef, mouseRef } = useUISelector(state => ({ gyroscopeRef: state.gyroscopeRef, mouseRef: state.mouseRef }))
  const { parallaxDpr, parallaxStepPx, reduceMotion } = useQuality()

  useEffect(() => {
    depthArtRenderer.configure({
      dpr: parallaxDpr,
      stepPx: parallaxStepPx,
      reduceMotion,
      gyroRef: gyroscopeRef,
      mouseRef,
    })
  }, [parallaxDpr, parallaxStepPx, reduceMotion, gyroscopeRef, mouseRef])

  return null
}

export const DepthArt = memo(function DepthArt({
  colorUrl, depthUrl, normalUrl, alt, onLoad, onError,
  className = 'absolute inset-0', imgClassName = 'absolute inset-0 w-full h-full object-cover', imgRef, imgProps,
}) {
  const hostRef = useRef(null)
  const canvasRef = useRef(null)
  const [drawnKey, setDrawnKey] = useState(null)
  const litArtwork = useUISelector(state => state.settingsState.litArtwork)
  const usable = litArtwork && isLoadedImage(colorUrl) && isLoadedImage(depthUrl) && isLoadedImage(normalUrl)
  const key = usable ? `${colorUrl}|${depthUrl}|${normalUrl}` : null

  useEffect(() => {
    if (!key) return
    return depthArtRenderer.attach({
      host: hostRef.current,
      canvas: canvasRef.current,
      colorUrl,
      depthUrl,
      normalUrl,
      onDrawn: () => setDrawnKey(key),
    })
  }, [key, colorUrl, depthUrl, normalUrl])

  const drawn = key !== null && drawnKey === key

  return (
    <div ref={hostRef} className={className}>
      <img
        ref={imgRef}
        src={colorUrl}
        alt={alt}
        decoding="async"
        className={imgClassName}
        onLoad={onLoad}
        onError={onError}
        {...imgProps}
      />
      <canvas
        ref={canvasRef}
        aria-hidden="true"
        className="absolute inset-0 w-full h-full pointer-events-none"
        style={{ opacity: drawn ? 1 : 0, transition: CSS_TRANSITION.fadeOpacity }}
      />
    </div>
  )
})

export const TrackArt = memo(function TrackArt({ trackId, hasArtwork = true, ...props }) {
  const enabled = useUISelector(state => state.settingsState.litArtwork) && !!trackId && hasArtwork !== false
  const depthUrl = useDepthMap(depthThumbCache, trackId, enabled)
  const normalUrl = useDepthMap(normalThumbCache, trackId, enabled)
  return <DepthArt depthUrl={depthUrl} normalUrl={normalUrl} {...props} />
})

export const ProfileArt = memo(function ProfileArt({ userId, hasPicture = true, ...props }) {
  const enabled = useUISelector(state => state.settingsState.litArtwork) && !!userId && hasPicture !== false
  const depthUrl = useDepthMap(profileDepthCache, userId, enabled)
  const normalUrl = useDepthMap(profileNormalCache, userId, enabled)
  return <DepthArt depthUrl={depthUrl} normalUrl={normalUrl} {...props} />
})
