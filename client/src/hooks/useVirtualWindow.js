import {useCallback, useMemo, useRef, useState} from 'react'
import {logger} from '../lib/logger'
import {retryableAPICall} from '../lib/retryUtils'

export function useVirtualWindow({
  fetchFn,
  windowSize = 150,
  initialParams = {}
}) {
  const [items, setItems] = useState([])
  const [totalCount, setTotalCount] = useState(0)
  const [windowStart, setWindowStart] = useState(0)
  const [isLoading, setIsLoading] = useState(false)
  const activeLoadRef = useRef(0)
  const paramsRef = useRef(initialParams)
  const windowRangeRef = useRef({ start: 0, end: 0 })
  const itemsMapRef = useRef(new Map())
  const idIndexMapRef = useRef(new Map())
  const loadSequenceRef = useRef(0)

  const maxWindowSize = windowSize * 2

  const cleanupMaps = useCallback((indexToRemove) => {
    const track = itemsMapRef.current.get(indexToRemove)
    if (track) {
      idIndexMapRef.current.delete(track.id)
    }
    itemsMapRef.current.delete(indexToRemove)
  }, [])

  const setTrackInMap = useCallback((index, track) => {
    if (idIndexMapRef.current.has(track.id)) {
      const oldIndex = idIndexMapRef.current.get(track.id)
      if (oldIndex !== index) {
        itemsMapRef.current.delete(oldIndex)
      }
    }

    if (itemsMapRef.current.has(index)) {
      const existingTrack = itemsMapRef.current.get(index)
      if (existingTrack.id !== track.id) {
        idIndexMapRef.current.delete(existingTrack.id)
      }
    }

    itemsMapRef.current.set(index, track)
    idIndexMapRef.current.set(track.id, index)
  }, [])

  const loadWindow = useCallback(async (targetStart, params = {}, shouldReplace = false) => {
    const mySequence = loadSequenceRef.current
    activeLoadRef.current++
    setIsLoading(true)

    try {
      const effectiveStart = Math.max(0, targetStart)
      const result = await retryableAPICall(
        () => fetchFn(effectiveStart, windowSize, { ...paramsRef.current, ...params }),
        `Load tracks (start=${effectiveStart})`
      )

      if (mySequence !== loadSequenceRef.current) {
        return
      }

      const newTracks = result.items || result.tracks || []
      const total = result.total || result.total_tracks || newTracks.length

      if (shouldReplace) {
        itemsMapRef.current.clear()
        idIndexMapRef.current.clear()
        windowRangeRef.current = { start: effectiveStart, end: effectiveStart + newTracks.length }

        newTracks.forEach((track, idx) => {
          const absIndex = effectiveStart + idx
          itemsMapRef.current.set(absIndex, track)
          idIndexMapRef.current.set(track.id, absIndex)
        })

        setWindowStart(effectiveStart)
        setItems([...newTracks])
        setTotalCount(total)
        return
      }

      newTracks.forEach((track, idx) => {
        const absoluteIndex = effectiveStart + idx
        setTrackInMap(absoluteIndex, track)
      })

      const currentRange = windowRangeRef.current
      const newStart = Math.min(currentRange.start || effectiveStart, effectiveStart)
      const newEnd = Math.max(currentRange.end || 0, effectiveStart + newTracks.length)

      if (newEnd - newStart > maxWindowSize) {
        const isBackwardLoad = effectiveStart < (currentRange.start || effectiveStart)

        let trimStart, trimEnd
        if (isBackwardLoad) {
          trimStart = newStart
          trimEnd = newStart + maxWindowSize
          for (let i = trimEnd; i < newEnd; i++) {
            cleanupMaps(i)
          }
        } else {
          trimStart = Math.max(0, newEnd - maxWindowSize)
          trimEnd = newEnd
          for (let i = newStart; i < trimStart; i++) {
            cleanupMaps(i)
          }
        }

        windowRangeRef.current = { start: trimStart, end: trimEnd }

        const trimmedItems = []
        for (let i = trimStart; i < trimEnd; i++) {
          trimmedItems.push(itemsMapRef.current.get(i) || null)
        }

        setWindowStart(trimStart)
        setItems(trimmedItems)
      } else {
        windowRangeRef.current = { start: newStart, end: newEnd }

        const mergedItems = []
        for (let i = newStart; i < newEnd; i++) {
          mergedItems.push(itemsMapRef.current.get(i) || null)
        }

        setWindowStart(newStart)
        setItems(mergedItems)
      }

      setTotalCount(total)
    } catch (error) {
      if (error?.name === 'AbortError') return
      logger.error('[VirtualWindow] Load failed:', error)
    } finally {
      activeLoadRef.current--
      if (activeLoadRef.current <= 0) {
        activeLoadRef.current = 0
        setIsLoading(false)
      }
    }
  }, [fetchFn, windowSize, maxWindowSize, cleanupMaps, setTrackInMap])

  const loadDirection = useCallback(async (direction) => {
    const currentRange = windowRangeRef.current
    if (currentRange.end - currentRange.start === 0) return

    const step = Math.floor(windowSize / 2)
    const newStart = direction === 'forward'
      ? currentRange.end
      : Math.max(0, currentRange.start - step)

    if (direction === 'forward' && currentRange.end >= totalCount) {
      return
    }
    if (direction === 'backward' && currentRange.start === 0) {
      return
    }

    await loadWindow(newStart, paramsRef.current, false)
  }, [windowSize, totalCount, loadWindow])

  const loadForward = useCallback(() => loadDirection('forward'), [loadDirection])
  const loadBackward = useCallback(() => loadDirection('backward'), [loadDirection])

  const reset = useCallback(async (params = {}) => {
    loadSequenceRef.current++
    activeLoadRef.current = 0
    paramsRef.current = { ...paramsRef.current, ...params }
    itemsMapRef.current.clear()
    idIndexMapRef.current.clear()
    windowRangeRef.current = { start: 0, end: 0 }
    setItems([])
    setTotalCount(0)
    setWindowStart(0)
    await loadWindow(0, params, true)
  }, [loadWindow])

  const updateParams = useCallback(async (params) => {
    await reset(params)
  }, [reset])

  const hasMore = useMemo(() => {
    return windowRangeRef.current.end < totalCount
  }, [totalCount, items])

  const windowEnd = useMemo(() => {
    return windowRangeRef.current.end
  }, [items])

  return {
    items,
    totalCount,
    windowStart,
    windowEnd,
    isLoading,
    hasMore,
    loadWindow,
    loadForward,
    loadBackward,
    reset,
    updateParams
  }

}
