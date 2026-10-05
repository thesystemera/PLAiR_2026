import { useEffect, useState } from 'react'

const MISSING_RETRY_MS = 10 * 60 * 1000
const missingSince = new Map()

export function useDepthMap(cache, id, enabled = true) {
  const [state, setState] = useState(() => ({ url: enabled && id ? cache.peekMemory(id) : null, missing: false }))

  useEffect(() => {
    const setUrl = (url, missing = false) => setState(prev => (prev.url === url && prev.missing === missing ? prev : { url, missing }))
    if (!enabled || !id) {
      queueMicrotask(() => setUrl(null))
      return
    }

    let alive = true
    const key = `${cache.type}:${id}`

    const load = () => {
      const missingAt = missingSince.get(key)
      if (missingAt && performance.now() - missingAt < MISSING_RETRY_MS) {
        queueMicrotask(() => alive && setUrl(null, true))
        return
      }
      cache.getMedia(id).then((next) => {
        if (!alive) return
        if (next) missingSince.delete(key)
        else missingSince.set(key, performance.now())
        setUrl(next || null, !next)
      })
    }

    const cached = cache.getMemory(id)
    if (cached) queueMicrotask(() => alive && setUrl(cached))
    else load()

    const unsubscribe = cache.subscribe((changed) => {
      if (changed !== id || !alive) return
      const current = cache.peekMemory(id)
      if (current) {
        setUrl(current)
      } else {
        missingSince.delete(key)
        load()
      }
    })

    return () => {
      alive = false
      unsubscribe()
    }
  }, [cache, id, enabled])

  return state
}
