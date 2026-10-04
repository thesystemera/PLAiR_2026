import { memo, useEffect, useRef, useState } from 'react'
import { useUISelector } from '../contexts/UIStateContext'
import { useQuality } from '../contexts/QualityContext'
import { depthArtRenderer } from '../lib/depthArtRenderer'
import { CSS_TRANSITION } from '../lib/motion'

const isLoadedImage = url => typeof url === 'string' && (url.startsWith('blob:') || url.startsWith('http') || url.startsWith('/'))

export function DepthArtBridge() {
  const { gyroscopeRef, mouseRef } = useUISelector(state => ({ gyroscopeRef: state.gyroscopeRef, mouseRef: state.mouseRef }))
  const { parallaxDpr, parallaxFpsCap, parallaxStepPx, reduceMotion } = useQuality()

  useEffect(() => {
    depthArtRenderer.configure({
      dpr: parallaxDpr,
      fpsCap: parallaxFpsCap,
      stepPx: parallaxStepPx,
      reduceMotion,
      gyroRef: gyroscopeRef,
      mouseRef,
    })
  }, [parallaxDpr, parallaxFpsCap, parallaxStepPx, reduceMotion, gyroscopeRef, mouseRef])

  return null
}

export const DepthArt = memo(function DepthArt({ colorUrl, depthUrl, normalUrl, alt, onLoad, onError }) {
  const hostRef = useRef(null)
  const canvasRef = useRef(null)
  const [drawnKey, setDrawnKey] = useState(null)
  const litArtwork = useUISelector(state => state.settingsState.litArtwork) !== false
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
    <div ref={hostRef} className="absolute inset-0">
      <img
        src={colorUrl}
        alt={alt}
        decoding="async"
        className="absolute inset-0 w-full h-full object-cover"
        onLoad={onLoad}
        onError={onError}
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
