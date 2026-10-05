import { PACK_SIZES, packCache } from './mediaCache'
import { depthArtRenderer } from './depthArtRenderer'
import { logger } from './logger'

const MAX_CONCURRENT = 6
const WARM_COVERS = 32
const RETRY_AFTER_MS = 15000

class PackPrefetcher {
  constructor(cache) {
    this.cache = cache
    this.queue = []
    this.demand = new Set()
    this.running = new Map()
    this.controllers = new Map()
    this.readyUrls = new Map()
    this.failed = new Map()
    this.active = 0
    this.listeners = new Set()
    cache.subscribe((id) => {
      if (this.readyUrls.has(id) && this.readyUrls.get(id) !== cache.peekMemory(id)) this.readyUrls.delete(id)
    })
  }

  subscribe(listener) {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  isReady(id) {
    const url = this.readyUrls.get(id)
    return !!url && url === this.cache.peekMemory(id)
  }

  isDemanded(id) {
    return this.demand.has(id) || this.running.has(id)
  }

  setDemand(ids, { jumped = false } = {}) {
    const next = new Set()
    const queue = []
    for (const id of ids) {
      if (!id || next.has(id)) continue
      next.add(id)
      if (!this.isReady(id) && !this.running.has(id)) queue.push(id)
    }
    this.demand = next
    this.queue = queue
    if (jumped) {
      for (const [id, controller] of this.controllers) {
        if (!next.has(id)) controller.abort()
      }
    }
    this._pump()
  }

  _next() {
    while (this.queue.length) {
      const id = this.queue.shift()
      if (!this.demand.has(id) || this.isReady(id) || this.running.has(id)) continue
      const failedAt = this.failed.get(id)
      if (failedAt && performance.now() - failedAt < RETRY_AFTER_MS) continue
      return id
    }
    return null
  }

  _pump() {
    while (this.active < MAX_CONCURRENT) {
      const id = this._next()
      if (!id) return
      this.active++
      const controller = new AbortController()
      this.controllers.set(id, controller)
      const task = this._load(id, controller.signal).finally(() => {
        this.active--
        this.running.delete(id)
        this.controllers.delete(id)
        this._pump()
      })
      this.running.set(id, task)
    }
  }

  async _load(id, signal) {
    try {
      const url = await this.cache.getMedia(id, { signal })
      if (!url) {
        if (!signal.aborted) this.failed.set(id, performance.now())
        return
      }
      this.failed.delete(id)
      this.readyUrls.set(id, url)
      this.listeners.forEach(listener => listener(id))
    } catch (err) {
      this.failed.set(id, performance.now())
      logger.debug('[CoverPrefetch] Load failed:', id, err)
    }
  }
}

const prefetchers = new Map()
let activePrefetcher = null
let warmQueued = false

for (const size of PACK_SIZES) {
  const cache = packCache(size)
  cache.addPinnedChecker(id => depthArtRenderer.isShowing(cache.peekMemory(id)))
}

function warmNearest() {
  warmQueued = false
  const prefetcher = activePrefetcher
  if (!prefetcher) return
  const urls = []
  for (const id of prefetcher.demand) {
    if (urls.length >= WARM_COVERS) break
    if (prefetcher.isReady(id)) urls.push(prefetcher.cache.peekMemory(id))
  }
  depthArtRenderer.warm(urls)
}

function scheduleWarm() {
  if (warmQueued) return
  warmQueued = true
  queueMicrotask(warmNearest)
}

function prefetcherFor(size) {
  let prefetcher = prefetchers.get(size)
  if (!prefetcher) {
    prefetcher = new PackPrefetcher(packCache(size))
    prefetcher.cache.addPinnedChecker(id => prefetcher.isDemanded(id))
    prefetcher.subscribe(scheduleWarm)
    prefetchers.set(size, prefetcher)
  }
  return prefetcher
}

export function prefetchCovers(ids, { size, jumped = false }) {
  const prefetcher = prefetcherFor(size)
  if (activePrefetcher && activePrefetcher !== prefetcher) activePrefetcher.setDemand([])
  activePrefetcher = prefetcher
  prefetcher.setDemand(ids, { jumped })
  scheduleWarm()
}
