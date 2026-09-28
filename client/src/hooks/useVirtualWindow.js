import { useCallback, useEffect, useRef, useState } from 'react'
import { logger } from '../lib/logger'
import { retryableAPICall } from '../lib/retryUtils'

const MAX_CONCURRENT_PAGES = 2
const RETRY_DELAY_MS = 1500
const EMPTY_SNAPSHOT = { items: [], windowStart: 0, totalCount: 0 }

export function useVirtualWindow({
  fetchFn,
  pageSize = 60,
  maxPages = 16,
  initialParams = {}
}) {
  const [snapshot, setSnapshot] = useState(EMPTY_SNAPSHOT)
  const [isLoading, setIsLoading] = useState(false)
  const pagesRef = useRef(new Map())
  const pendingRef = useRef(new Map())
  const failedRef = useRef(new Map())
  const queueRef = useRef([])
  const demandRef = useRef({ first: 0, last: -1, focus: 0, args: null })
  const totalRef = useRef(0)
  const seqRef = useRef(0)
  const paramsRef = useRef(initialParams)
  const retryTimerRef = useRef(null)
  const pumpRef = useRef(null)
  const ensureRef = useRef(null)

  const publish = useCallback(() => {
    const pages = pagesRef.current
    if (pages.size === 0) {
      setSnapshot({ items: [], windowStart: 0, totalCount: totalRef.current })
      return
    }
    let minPage = Infinity
    let maxPage = -1
    for (const page of pages.keys()) {
      if (page < minPage) minPage = page
      if (page > maxPage) maxPage = page
    }
    const start = minPage * pageSize
    const end = maxPage * pageSize + pages.get(maxPage).length
    const items = new Array(Math.max(0, end - start)).fill(null)
    for (const [page, list] of pages) {
      const offset = page * pageSize - start
      for (let i = 0; i < list.length; i++) items[offset + i] = list[i]
    }
    setSnapshot({ items, windowStart: start, totalCount: Math.max(totalRef.current, end) })
  }, [pageSize])

  const evict = useCallback(() => {
    const pages = pagesRef.current
    if (pages.size <= maxPages) return
    const { first, last, focus } = demandRef.current
    const candidates = [...pages.keys()]
      .filter(page => page < first || page > last)
      .sort((a, b) => Math.abs(b - focus) - Math.abs(a - focus))
    for (const page of candidates) {
      if (pages.size <= maxPages) break
      pages.delete(page)
    }
  }, [maxPages])

  const scheduleRetry = useCallback(() => {
    if (retryTimerRef.current) return
    retryTimerRef.current = setTimeout(() => {
      retryTimerRef.current = null
      const args = demandRef.current.args
      if (args) ensureRef.current?.(...args)
    }, RETRY_DELAY_MS)
  }, [])

  const loadPage = useCallback((page) => {
    const seq = seqRef.current
    const skip = page * pageSize
    const task = (async () => {
      try {
        const result = await retryableAPICall(
          () => fetchFn(skip, pageSize, { ...paramsRef.current }),
          `Load tracks (start=${skip})`
        )
        if (seq !== seqRef.current) return
        const list = result?.items || result?.tracks || []
        const total = result?.total || result?.total_tracks || 0
        if (list.length === 0 && skip < totalRef.current) {
          failedRef.current.set(page, performance.now())
          scheduleRetry()
          return
        }
        totalRef.current = total || Math.max(totalRef.current, skip + list.length)
        failedRef.current.delete(page)
        pagesRef.current.set(page, list)
        evict()
        publish()
      } catch (error) {
        if (error?.name === 'AbortError' || seq !== seqRef.current) return
        logger.error('[VirtualWindow] Page load failed:', page, error)
        failedRef.current.set(page, performance.now())
        scheduleRetry()
      } finally {
        if (seq === seqRef.current) {
          pendingRef.current.delete(page)
          setIsLoading(pendingRef.current.size > 0)
          pumpRef.current?.()
        }
      }
    })()
    pendingRef.current.set(page, task)
    setIsLoading(true)
    return task
  }, [fetchFn, pageSize, publish, evict, scheduleRetry])

  const pump = useCallback(() => {
    const queue = queueRef.current
    while (pendingRef.current.size < MAX_CONCURRENT_PAGES && queue.length) {
      const page = queue.shift()
      if (pagesRef.current.has(page) || pendingRef.current.has(page)) continue
      const failedAt = failedRef.current.get(page)
      if (failedAt && performance.now() - failedAt < RETRY_DELAY_MS) {
        scheduleRetry()
        continue
      }
      void loadPage(page)
    }
  }, [loadPage, scheduleRetry])

  const ensureRange = useCallback((start, end, focusIndex = start) => {
    demandRef.current.args = [start, end, focusIndex]
    const total = totalRef.current
    const upper = total > 0 ? Math.min(end, total) : end
    const lower = Math.max(0, start)
    if (upper <= lower) return
    const first = Math.floor(lower / pageSize)
    const last = Math.floor((upper - 1) / pageSize)
    const focus = Math.floor(Math.max(lower, Math.min(focusIndex, upper - 1)) / pageSize)
    demandRef.current.first = first
    demandRef.current.last = last
    demandRef.current.focus = focus
    const wanted = []
    for (let page = first; page <= last; page++) {
      if (!pagesRef.current.has(page) && !pendingRef.current.has(page)) wanted.push(page)
    }
    wanted.sort((a, b) => Math.abs(a - focus) - Math.abs(b - focus))
    queueRef.current = wanted
    pump()
  }, [pageSize, pump])

  useEffect(() => {
    pumpRef.current = pump
    ensureRef.current = ensureRange
  }, [pump, ensureRange])

  useEffect(() => () => {
    if (retryTimerRef.current) clearTimeout(retryTimerRef.current)
  }, [])

  const reset = useCallback(async (params = {}) => {
    seqRef.current++
    paramsRef.current = { ...paramsRef.current, ...params }
    pagesRef.current.clear()
    pendingRef.current.clear()
    failedRef.current.clear()
    queueRef.current = []
    demandRef.current = { first: 0, last: -1, focus: 0, args: null }
    totalRef.current = 0
    if (retryTimerRef.current) {
      clearTimeout(retryTimerRef.current)
      retryTimerRef.current = null
    }
    setSnapshot(EMPTY_SNAPSHOT)
    await loadPage(0)
  }, [loadPage])

  const updateParams = useCallback(async (params) => {
    await reset(params)
  }, [reset])

  return {
    items: snapshot.items,
    totalCount: snapshot.totalCount,
    windowStart: snapshot.windowStart,
    windowEnd: snapshot.windowStart + snapshot.items.length,
    isLoading,
    hasMore: snapshot.windowStart + snapshot.items.length < snapshot.totalCount,
    ensureRange,
    reset,
    updateParams
  }
}
