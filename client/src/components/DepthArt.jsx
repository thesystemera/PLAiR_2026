import { memo, useEffect, useRef, useState } from 'react'
import { useUISelector } from '../contexts/UIStateContext'
import { useQuality } from '../contexts/QualityContext'
import { depthArtRenderer } from '../lib/depthArtRenderer'
import { CSS_TRANSITION } from '../lib/motion'
import { useDepthMap } from '../hooks/useDepthMap'
import { artPackCache, isMemoryKey, profilePackCache } from '../lib/mediaCache'

const HIDDEN = { visibility: 'hidden' }

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
  colorUrl, packUrl, packMissing = false, alt, onLoad, onError,
  className = 'absolute inset-0', imgClassName = 'absolute inset-0 w-full h-full object-cover', imgRef, imgProps,
}) {
  const hostRef = useRef(null)
  const canvasRef = useRef(null)
  const [drawnKey, setDrawnKey] = useState(null)
  const [failedKey, setFailedKey] = useState(null)
  const litArtwork = useUISelector(state => state.settingsState.litArtwork)
  const key = litArtwork && isLoadedImage(packUrl) ? packUrl : null

  useEffect(() => {
    if (!key) return
    return depthArtRenderer.attach({
      host: hostRef.current,
      canvas: canvasRef.current,
      packUrl: key,
      onDrawn: () => setDrawnKey(key),
      onFailed: () => setFailedKey(key),
    })
  }, [key])

  const flat = !litArtwork || packMissing || packUrl === undefined || (key !== null && failedKey === key)
  const drawn = !flat && key !== null && drawnKey === key

  return (
    <div ref={hostRef} className={className}>
      <img
        ref={imgRef}
        src={colorUrl}
        alt={alt}
        decoding="async"
        className={imgClassName}
        style={flat ? undefined : HIDDEN}
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
  const { url, missing } = useDepthMap(artPackCache, trackId, enabled)
  return <DepthArt packUrl={url} packMissing={missing || (!!trackId && hasArtwork === false)} {...props} />
})

export const ProfileArt = memo(function ProfileArt({ userId, hasPicture = true, ...props }) {
  const enabled = useUISelector(state => state.settingsState.litArtwork) && !!userId && hasPicture !== false
  const { url, missing } = useDepthMap(profilePackCache, userId, enabled)
  return <DepthArt packUrl={url} packMissing={missing || (!!userId && hasPicture === false)} {...props} />
})
