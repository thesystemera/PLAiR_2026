import { useRef, useCallback } from 'react'

export function usePointerInteraction(threshold = 10) {
  const pointerStartRef = useRef({ x: 0, y: 0, moved: false })

  const onPointerDown = useCallback((e) => {
    pointerStartRef.current = {
      x: e.clientX,
      y: e.clientY,
      moved: false
    }
  }, [])

  const onPointerMove = useCallback((e) => {
    if (!pointerStartRef.current) return

    const dx = e.clientX - pointerStartRef.current.x
    const dy = e.clientY - pointerStartRef.current.y
    const distance = Math.sqrt(dx * dx + dy * dy)

    if (distance > threshold) {
      pointerStartRef.current.moved = true
    }
  }, [threshold])

  const shouldTrigger = useCallback(() => {
    return !pointerStartRef.current?.moved
  }, [])

  const reset = useCallback(() => {
    pointerStartRef.current = { x: 0, y: 0, moved: false }
  }, [])

  return {
    onPointerDown,
    onPointerMove,
    shouldTrigger,
    reset
  }
}