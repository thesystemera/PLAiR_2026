import { useCallback, useEffect, useRef } from 'react'
import { artLoaded, watchArt } from '../lib/microMotion'

export function useArtPop(id, skip = false) {
  const boxRef = useRef(null)

  useEffect(() => watchArt(boxRef.current, id, skip), [id, skip])

  const onLoad = useCallback(() => artLoaded(boxRef.current), [])

  return { boxRef, onLoad }
}
