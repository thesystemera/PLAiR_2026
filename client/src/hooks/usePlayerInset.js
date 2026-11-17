import { useCallback, useLayoutEffect, useRef } from 'react'
import { playerInset } from '../lib/playerInset'
import { scrollChrome } from '../lib/scrollChrome'

function useFollower(apply, sources) {
  const stateRef = useRef({ node: null, stops: [], apply })

  useLayoutEffect(() => {
    const state = stateRef.current
    state.apply = apply
    if (state.node) apply(state.node, playerInset.height())
  }, [apply])

  return useCallback((node) => {
    const state = stateRef.current
    for (const stop of state.stops) stop()
    state.node = node
    state.stops = node ? sources.map(source => source.follow(() => state.apply(node, playerInset.height()))) : []
  }, [sources])
}

const PLAYER = [playerInset]
const PLAYER_AND_CHROME = [playerInset, scrollChrome]

export function usePlayerInset(apply) {
  return useFollower(apply, PLAYER)
}

export function useScrollChrome(apply) {
  return useFollower(apply, PLAYER_AND_CHROME)
}
