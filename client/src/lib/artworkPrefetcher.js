import { artworkThumbCache } from './mediaCache'
import { logger } from './logger'

const MAX_CONCURRENT = 6
const MAX_DECODED = 160

class ArtworkPrefetcher {
  constructor(cache) {
    this.cache = cache
    this.queue = []
    this.urgent = []
    this.demand = new Set()
    this.running = new Map()
    this.controllers = new Map()
    this.decoded = new Map()
    this.readyUrls = new Map()
    this.failed = new Map()
    this.active = 0
    this.listeners = new Set()
    this.isHeld = null
    cache.subscribe((id) => {
      const url = cache.peekMemory(id)
      if (this.readyUrls.has(id) && this.readyUrls.get(id) !== url) {
        this.readyUrls.delete(id)
        this.decoded.delete(id)
      }
    })
  }

  subscribe(listener) {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  setHeldChecker(checker) {
    this.isHeld = checker
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
        if (!next.has(id) && !(this.isHeld && this.isHeld(id))) controller.abort()
      }
    }
    this._pump()
  }

  request(id) {
    if (!id || this.isReady(id) || this.running.has(id) || this.urgent.includes(id)) return
    this.urgent.push(id)
    this._pump()
  }

  _next() {
    while (this.queue.length) {
      const id = this.queue.shift()
      if (!this.demand.has(id) || this.isReady(id) || this.running.has(id)) continue
      const failedAt = this.failed.get(id)
      if (failedAt && performance.now() - failedAt < 15000) continue
      return id
    }
    while (this.urgent.length) {
      const id = this.urgent.shift()
      if (this.isReady(id) || this.running.has(id)) continue
      if (this.isHeld && !this.isHeld(id)) continue
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
        return null
      }
      this.failed.delete(id)
      await this._decode(id, url)
      return url
    } catch (err) {
      this.failed.set(id, performance.now())
      logger.debug('[ArtworkPrefetcher] Load failed:', id, err)
      return null
    }
  }

  async _decode(id, url) {
    const img = new Image()
    img.decoding = 'async'
    img.src = url
    try {
      await img.decode()
    } catch (err) {
      logger.debug('[ArtworkPrefetcher] Decode failed:', id, err)
    }
    if (this.cache.peekMemory(id) !== url) return
    this.readyUrls.set(id, url)
    this.decoded.delete(id)
    this.decoded.set(id, img)
    this.listeners.forEach(listener => listener(id))
    if (this.decoded.size > MAX_DECODED) {
      for (const key of this.decoded.keys()) {
        if (this.decoded.size <= MAX_DECODED) break
        if (this.demand.has(key)) continue
        this.decoded.delete(key)
      }
    }
  }
}

export const artworkPrefetcher = new ArtworkPrefetcher(artworkThumbCache)
