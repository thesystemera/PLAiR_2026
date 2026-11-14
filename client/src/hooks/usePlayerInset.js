import { useCallback, useLayoutEffect, useRef } from 'react'
import { playerInset } from '../lib/playerInset'

export function usePlayerInset(apply) {
  const stateRef = useRef({ node: null, stop: null, apply })

  useLayoutEffect(() => {
    const state = stateRef.current
    state.apply = apply
    if (state.node) apply(state.node, playerInset.height())
  }, [apply])

  return useCallback((node) => {
    const state = stateRef.current
    state.stop?.()
    state.node = node
    state.stop = node ? playerInset.follow(px => state.apply(node, px)) : null
  }, [])
}
