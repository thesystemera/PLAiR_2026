import { POM, bindDepthBound, createDepthArtPrograms, createDepthBound, createParallaxCache, deleteParallaxCache, parallaxSteps, setLightRect, setLightUniforms, viewport } from './depthArtShader'
import { noteLightConsumer, readLightProbe } from './lightProbe'
import { isSceneRenderingPaused } from './renderPause'
import { logger } from './logger'
import { addFrameWork, createGpuTimer, frameStatsActive } from './frameStats'
import { blobForUrl, packSizeFor } from './mediaCache'
import { decodePack } from './packImage'

const INTENSITY = 0.1
const SCROLL_TILT = 0.9
const MAX_PARALLAX = 1.6
const MAX_IDLE_TEXTURES = 8
const UPLOADS_PER_FRAME = 2
const REDRAW_SHIFT_PX = 0.5
const LAYOUT_REFRESH_MS = 250
const CACHE_AFTER_STILL_DRAWS = 2
const CANVAS_RESIZES_PER_FRAME = 3
const AHEAD_FRAMES = 10
const AHEAD_MIN_VIEWPORTS = 0.5
const AHEAD_MAX_VIEWPORTS = 2
const AHEAD_DRAWS_PER_FRAME = 4
const CONTEXT_OPTIONS = { alpha: false, antialias: false, depth: false, stencil: false }
const NO_PARALLAX = { parallaxX: 0, parallaxY: 0 }

async function loadPack(url) {
  const blob = blobForUrl(url)
  if (!blob) throw new Error('pack not in memory')
  return decodePack(blob)
}

function createAtlas() {
  const canvas = document.createElement('canvas')
  const options = { ...CONTEXT_OPTIONS, preserveDrawingBuffer: true }
  const gl = canvas.getContext('webgl2', options) || canvas.getContext('webgl', options)
  return gl ? { canvas, gl, options } : null
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value))
}

const clipCache = new WeakMap()

function clippingAncestors(element) {
  if (!element || element === document.body || element === document.documentElement) return []
  let clips = clipCache.get(element)
  if (!clips) {
    const style = getComputedStyle(element)
    const clipsSelf = style.overflowX !== 'visible' || style.overflowY !== 'visible' || /paint|strict|content/.test(style.contain)
    const above = clippingAncestors(element.parentElement)
    clips = clipsSelf ? [element, ...above] : above
    clipCache.set(element, clips)
  }
  return clips
}

function overlaps(a, left, top, right, bottom) {
  return a.right > left && a.left < right && a.bottom > top && a.top < bottom
}

class DepthArtRenderer {
  constructor() {
    this.views = new Set()
    this.textures = new Map()
    this.maxIdle = MAX_IDLE_TEXTURES
    this.redrawShiftPx = REDRAW_SHIFT_PX
    this.skipCopy = false
    this.forceSplit = false
    this.bench = { forceFull: false, extraFull: 0, extraCopy: 0 }
    this.shaderOptions = {}
    this.stepScale = 1
    this.skipDraw = false
    this.drawDelays = []
    this.uploads = []
    this.settings = { dpr: Infinity, stepPx: 0, reduceMotion: false, lit: true, gyroRef: null, mouseRef: null }
    this.canvas = null
    this.gl = null
    this.programs = null
    this.maxSize = 4096
    this.unavailable = false
    this.contextLost = false
    this.frame = null
    this.layoutDirty = true
    this.scrolled = false
    this.lastLayoutAt = 0
    this.lastInput = null
    this.showing = false
    this.tick = this.tick.bind(this)
    this.markLayoutDirty = () => {
      this.layoutDirty = true
    }
    this.scrollOffsets = new WeakMap()
    this.scrollViews = new WeakMap()
    this.scrolledTargets = new Set()
    this.scrollSpeed = 0
    this.aheadMargin = 0
    this.aheadPending = false
    this.viewsVersion = 0
    this.markScrolled = (event) => {
      const target = event.target
      if (!target || target === document || target === document.documentElement || target === document.body) {
        this.layoutDirty = true
        return
      }
      this.scrolledTargets.add(target)
      this.scrolled = true
    }
    if (typeof document !== 'undefined') {
      document.addEventListener('scroll', this.markScrolled, { capture: true, passive: true })
      window.addEventListener('resize', this.markLayoutDirty, { passive: true })
    }
  }

  configure(settings) {
    Object.assign(this.settings, settings)
    for (const view of this.views) view.last = null
  }

  packSizeFor(cssPx, intensity = INTENSITY) {
    const dpr = Math.min(window.devicePixelRatio || 1, this.settings.dpr)
    return packSizeFor(cssPx * dpr * (1 + intensity * POM.ZOOM_FACTOR))
  }

  attach({ host, canvas, packUrl, intensity = INTENSITY, onDrawn, onFailed }) {
    if (!this.ensureContext()) {
      queueMicrotask(() => onFailed?.())
      return () => {}
    }
    const view = {
      host, canvas, ctx: null, packUrl, intensity, onDrawn, onFailed, failed: false,
      clips: clippingAncestors(host.parentElement), visible: false, dirty: true, entry: null, last: null, drawn: false,
      shiftX: 0, shiftY: 0, styleVisible: true,
    }
    this.views.add(view)
    this.viewsVersion++
    this.scrolled = true
    this.schedule()
    return () => this.detach(view)
  }

  hold(canvas, held) {
    for (const view of this.views) {
      if (view.canvas !== canvas) continue
      view.held = held
      if (!held) view.last = null
    }
    this.schedule()
  }

  detach(view) {
    if (!this.views.delete(view)) return
    this.viewsVersion++
    if (view.entry) view.entry.refs--
    view.entry = null
    this.dropCache(view)
    this.evictIdle()
  }

  applyScrolls() {
    let speed = 0
    for (const target of this.scrolledTargets) {
      const last = this.scrollOffsets.get(target)
      const top = target.scrollTop
      const left = target.scrollLeft
      this.scrollOffsets.set(target, { top, left })
      if (last) speed = Math.max(speed, Math.abs(last.top - top), Math.abs(last.left - left))
      let cached = this.scrollViews.get(target)
      if (!cached || cached.version !== this.viewsVersion) {
        cached = { version: this.viewsVersion, views: [...this.views].filter(view => target.contains(view.host)) }
        this.scrollViews.set(target, cached)
      }
      for (const view of cached.views) {
        if (last && view.rect && !view.dirty) {
          view.shiftX += last.left - left
          view.shiftY += last.top - top
        } else {
          view.dirty = true
        }
      }
    }
    this.scrolledTargets.clear()
    this.scrollSpeed = speed
  }

  measure(view, clipRects) {
    view.shiftX = 0
    view.shiftY = 0
    view.styleVisible = !view.host.checkVisibility || view.host.checkVisibility({ opacityProperty: true, visibilityProperty: true })
    const measured = view.host.getBoundingClientRect()
    const rect = view.rect || {}
    rect.left = rect.x = measured.left
    rect.top = rect.y = measured.top
    rect.right = measured.right
    rect.bottom = measured.bottom
    rect.width = measured.width
    rect.height = measured.height
    this.place(view, rect, clipRects)
  }

  reposition(view, clipRects) {
    const rect = view.rect
    rect.left += view.shiftX
    rect.right += view.shiftX
    rect.x = rect.left
    rect.top += view.shiftY
    rect.bottom += view.shiftY
    rect.y = rect.top
    view.shiftX = 0
    view.shiftY = 0
    this.place(view, rect, clipRects)
  }

  place(view, rect, clipRects) {
    view.rect = rect
    const margin = this.aheadMargin
    const sized = rect.width >= 2 && rect.height >= 2
    let visible = sized && overlaps(rect, 0, 0, viewport.width, viewport.height)
    let near = sized && overlaps(rect, -margin, -margin, viewport.width + margin, viewport.height + margin)
    for (const clip of view.clips) {
      if (!near) break
      let clipRect = clipRects.get(clip)
      if (!clipRect) {
        clipRect = clip.getBoundingClientRect()
        clipRects.set(clip, clipRect)
      }
      visible = visible && overlaps(rect, clipRect.left, clipRect.top, clipRect.right, clipRect.bottom)
      near = overlaps(rect, clipRect.left - margin, clipRect.top - margin, clipRect.right + margin, clipRect.bottom + margin)
    }
    view.visible = visible
    view.near = near && view.styleVisible
    view.shown = visible && view.styleVisible
    if (view.shown && !view.shownAt) view.shownAt = performance.now()
    if (!visible) this.dropCache(view)
  }

  ensureContext() {
    if (this.gl) return true
    if (this.unavailable) return false
    try {
      const atlas = createAtlas()
      if (!atlas) throw new Error('WebGL unavailable')
      const { canvas, gl, options } = atlas
      this.canvas = canvas
      this.setupContext(gl)
      canvas.addEventListener('webglcontextlost', (event) => {
        event.preventDefault()
        logger.warn('[DepthArt] WebGL context lost')
        this.contextLost = true
        this.textures.clear()
        this.uploads = []
        for (const view of this.views) {
          view.entry = null
          view.last = null
          view.cache = null
        }
      })
      canvas.addEventListener('webglcontextrestored', () => {
        try {
          this.setupContext(canvas.getContext(gl instanceof WebGLRenderingContext ? 'webgl' : 'webgl2', options))
          this.contextLost = false
          this.schedule()
        } catch (error) {
          logger.error('[DepthArt] WebGL restore failed:', error)
          this.unavailable = true
        }
      })
      window.registerRAFSource?.('DepthArt')
      return true
    } catch (error) {
      logger.warn('[DepthArt] Depth tiles unavailable:', error)
      this.unavailable = true
      return false
    }
  }

  setupContext(gl) {
    const programs = createDepthArtPrograms(gl, { packedNormals: true, ...this.shaderOptions })
    for (const entry of [programs.full, programs.cache]) {
      if (!entry) continue
      gl.useProgram(entry.program)
      gl.uniform1f(entry.uniforms.zoom, 1)
    }
    this.gl = gl
    this.programs = programs
    this.gpuTimer = createGpuTimer(gl, 'art gpu')
    const dims = gl.getParameter(gl.MAX_VIEWPORT_DIMS)
    this.maxSize = Math.min(4096, gl.getParameter(gl.MAX_RENDERBUFFER_SIZE), dims[0], dims[1])
  }

  acquire(packUrl) {
    let entry = this.textures.get(packUrl)
    if (!entry) {
      entry = { key: packUrl, refs: 0, state: 'loading', color: null, map: null, bound: null, usedAt: 0 }
      this.textures.set(packUrl, entry)
      loadPack(packUrl)
        .then(({ color, map }) => {
          if (this.textures.get(packUrl) !== entry) {
            color.close()
            map.close()
            return
          }
          this.uploads.push({ entry, color, map })
          this.schedule()
        })
        .catch((error) => {
          logger.debug('[DepthArt] Pack load failed:', error)
          entry.state = 'failed'
          this.schedule()
        })
    }
    entry.refs++
    entry.usedAt = performance.now()
    return entry
  }

  isShowing(packUrl) {
    if (!packUrl) return false
    for (const view of this.views) if (view.packUrl === packUrl) return true
    return false
  }

  warm(packUrls) {
    if (!this.ensureContext()) return
    const keep = new Set()
    for (const packUrl of packUrls) {
      const entry = this.acquire(packUrl)
      entry.refs--
      keep.add(entry)
    }
    for (const entry of this.textures.values()) {
      entry.warm = keep.has(entry)
      if (entry.warm) entry.warmed = true
    }
    this.evictIdle()
    this.schedule()
  }

  evictIdle() {
    let idle = 0
    for (const entry of this.textures.values()) if (entry.refs <= 0 && !entry.warm) idle++
    if (idle <= this.maxIdle) return
    const byAge = [...this.textures.values()].filter(entry => entry.refs <= 0 && !entry.warm).sort((a, b) => a.usedAt - b.usedAt)
    for (const entry of byAge.slice(0, idle - this.maxIdle)) this.release(entry)
  }

  release(entry) {
    this.textures.delete(entry.key)
    if (this.gl && !this.contextLost) {
      if (entry.color) this.gl.deleteTexture(entry.color)
      if (entry.map) this.gl.deleteTexture(entry.map)
      if (entry.bound) this.gl.deleteTexture(entry.bound.texture)
    }
  }

  dropCache(view) {
    if (!view.cache) return
    if (this.gl && !this.contextLost) deleteParallaxCache(this.gl, view.cache)
    view.cache = null
  }

  createTexture(source, format) {
    const gl = this.gl
    const texture = gl.createTexture()
    gl.bindTexture(gl.TEXTURE_2D, texture)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
    gl.texImage2D(gl.TEXTURE_2D, 0, format, format, gl.UNSIGNED_BYTE, source)
    return texture
  }

  processUploads() {
    const gl = this.gl
    if (this.uploads.length > 1) this.uploads.sort((a, b) => (b.entry.refs > 0) - (a.entry.refs > 0))
    for (let i = 0; i < UPLOADS_PER_FRAME && this.uploads.length; i++) {
      const { entry, color, map } = this.uploads.shift()
      if (this.textures.get(entry.key) !== entry) {
        color.close()
        map.close()
        continue
      }
      gl.activeTexture(gl.TEXTURE0)
      entry.color = this.createTexture(color, gl.RGBA)
      entry.map = this.createTexture(map, gl.RGB)
      entry.bound = createDepthBound(gl, this.programs, entry.map, map.width, map.height)
      entry.sizes = [color.width, color.height, map.width, map.height]
      entry.state = 'ready'
      color.close()
      map.close()
    }
  }

  schedule() {
    if (!this.frame && this.views.size) this.frame = requestAnimationFrame(this.tick)
  }

  baseParallax() {
    if (this.settings.reduceMotion) return NO_PARALLAX
    const gyro = this.settings.gyroRef?.current || NO_PARALLAX
    const mouse = this.settings.mouseRef?.current || NO_PARALLAX
    const hasGyro = Math.abs(gyro.parallaxX) > 0.001 || Math.abs(gyro.parallaxY) > 0.001
    return hasGyro ? gyro : mouse
  }

  tick(timestamp) {
    const started = performance.now()
    this.drawFrame(timestamp)
    if (frameStatsActive()) addFrameWork('art js', performance.now() - started)
  }

  drawFrame(timestamp) {
    this.frame = null
    if (!this.views.size) return
    this.schedule()
    if (!this.gl || this.contextLost) return
    this.gpuTimer.poll()
    const uploaded = this.uploads.length > 0
    if (uploaded) this.processUploads()

    const { stepPx, reduceMotion } = this.settings
    if (isSceneRenderingPaused(timestamp)) return

    if (this.showing) noteLightConsumer(timestamp)
    const probe = readLightProbe(timestamp)
    const base = this.baseParallax()
    const relayout = this.layoutDirty || timestamp - this.lastLayoutAt > LAYOUT_REFRESH_MS
    const scrolled = this.scrolled
    const input = this.lastInput
    const moved = !input || Math.abs(input.x - base.parallaxX) > 1e-4 || Math.abs(input.y - base.parallaxY) > 1e-4
    const lit = !input || input.light !== probe.key
    if (!relayout && !scrolled && !moved && !uploaded && !lit && !this.aheadPending) return
    this.lastInput = { x: base.parallaxX, y: base.parallaxY, light: probe.key }
    this.scrolled = false
    if (relayout) {
      this.layoutDirty = false
      this.lastLayoutAt = timestamp
    }
    this.applyScrolls()
    this.aheadMargin = clamp(this.scrollSpeed * AHEAD_FRAMES, viewport.height * AHEAD_MIN_VIEWPORTS, viewport.height * AHEAD_MAX_VIEWPORTS)
    const clipRects = new Map()
    const viewportHalf = Math.max(1, viewport.height / 2)
    const dpr = Math.min(window.devicePixelRatio || 1, this.settings.dpr)
    const flat = !this.settings.lit
    const batch = []
    const ahead = []
    let showing = false
    let resized = 0
    const parallaxFor = (view, rect, width, height) => {
      const tilt = reduceMotion ? 0 : clamp((rect.top + rect.height / 2 - viewportHalf) / viewportHalf, -1, 1) * SCROLL_TILT
      const px = clamp(base.parallaxX, -MAX_PARALLAX, MAX_PARALLAX)
      const py = clamp(base.parallaxY + tilt, -MAX_PARALLAX, MAX_PARALLAX)
      const pixelsPerUnit = view.intensity * Math.max(width, height) * (1 + view.intensity * POM.ZOOM_FACTOR)
      return { px, py, pixelsPerUnit }
    }

    for (const view of this.views) {
      if (relayout || view.dirty || !view.rect) {
        view.dirty = false
        this.measure(view, clipRects)
      } else if (view.shiftX || view.shiftY) {
        this.reposition(view, clipRects)
      }
      if (view.held && view.drawn) continue
      if (!view.entry && view.rect.width >= 2 && view.rect.height >= 2) view.entry = this.acquire(view.packUrl)
      const entry = view.entry
      if (entry?.state === 'failed') {
        if (!view.failed) {
          view.failed = true
          view.onFailed?.()
        }
        continue
      }
      if (!entry || entry.state !== 'ready') continue
      const rect = view.rect
      const width = Math.min(this.maxSize, Math.round(rect.width * dpr))
      const height = Math.min(this.maxSize, Math.round(rect.height * dpr))
      let sized = view.canvas.width === width && view.canvas.height === height
      if (!view.shown) {
        if (!sized && rect.width >= 2 && resized < CANVAS_RESIZES_PER_FRAME) {
          view.canvas.width = width
          view.canvas.height = height
          resized++
          sized = true
        }
        if (sized && view.near && !view.drawn) {
          const distance = Math.max(0, -rect.bottom, rect.top - viewport.height) + Math.max(0, -rect.right, rect.left - viewport.width)
          ahead.push({ view, entry, rect, width, height, distance })
        }
        continue
      }
      if (!sized) {
        if (resized >= CANVAS_RESIZES_PER_FRAME) continue
        resized++
      }
      const last = view.last
      if (flat) {
        if (last?.flat && last.entry === entry && last.width === width && last.height === height) continue
        batch.push({ view, entry, rect, width, height, px: 0, py: 0, steps: 0, mode: 'flat' })
        continue
      }
      showing = true
      const { px, py, pixelsPerUnit } = parallaxFor(view, rect, width, height)
      const epsilon = this.redrawShiftPx / (0.5 * pixelsPerUnit)
      const tiltStill = last && last.entry === entry && last.width === width && last.height === height &&
        Math.abs(last.px - px) < epsilon && Math.abs(last.py - py) < epsilon
      if (!this.bench.forceFull && tiltStill && last.light === probe.key && last.left === rect.left && last.top === rect.top) continue
      const cache = view.cache
      const cached = !this.bench.forceFull && probe.active && cache?.ready && cache.art === entry && cache.width === width && cache.height === height &&
        Math.abs(cache.px - px) < epsilon && Math.abs(cache.py - py) < epsilon
      view.stillDraws = tiltStill ? (view.stillDraws || 0) + 1 : 0
      const build = !this.bench.forceFull && !cached && probe.active && !!this.programs.cache && (this.forceSplit || view.stillDraws >= CACHE_AFTER_STILL_DRAWS)
      batch.push({
        view, entry, rect, width, height, px, py,
        steps: Math.max(1, Math.round(parallaxSteps(Math.hypot(px, py) * pixelsPerUnit, stepPx) * this.stepScale)),
        mode: cached ? 'relight' : build ? 'build' : 'full',
      })
    }

    ahead.sort((a, b) => a.distance - b.distance)
    this.aheadPending = ahead.length > AHEAD_DRAWS_PER_FRAME
    for (const { view, entry, rect, width, height } of ahead.slice(0, AHEAD_DRAWS_PER_FRAME)) {
      if (flat) {
        batch.push({ view, entry, rect, width, height, px: 0, py: 0, steps: 0, mode: 'flat' })
        continue
      }
      const { px, py, pixelsPerUnit } = parallaxFor(view, rect, width, height)
      batch.push({ view, entry, rect, width, height, px, py, steps: parallaxSteps(Math.hypot(px, py) * pixelsPerUnit, stepPx), mode: 'full' })
    }

    this.showing = showing
    if (!batch.length || this.skipDraw) return
    this.gpuTimer.begin()
    this.drawBatch(batch, probe)
    this.gpuTimer.end()
  }

  drawBatch(batch, probe) {
    let group = []
    let x = 0
    let y = 0
    let rowHeight = 0
    let usedWidth = 0
    for (const item of batch) {
      if (x + item.width > this.maxSize) {
        y += rowHeight
        x = 0
        rowHeight = 0
      }
      if (y + item.height > this.maxSize) {
        this.drawGroup(group, usedWidth, y + rowHeight, probe)
        group = []
        x = 0
        y = 0
        rowHeight = 0
        usedWidth = 0
      }
      item.x = x
      item.y = y
      group.push(item)
      x += item.width
      usedWidth = Math.max(usedWidth, x)
      rowHeight = Math.max(rowHeight, item.height)
    }
    if (group.length) this.drawGroup(group, usedWidth, y + rowHeight, probe)
  }

  drawGroup(group, width, height, probe) {
    const gl = this.gl
    const canvas = this.canvas
    if (canvas.width < width || canvas.height < height) {
      canvas.width = Math.max(canvas.width, Math.min(this.maxSize, Math.ceil(width / 256) * 256))
      canvas.height = Math.max(canvas.height, Math.min(this.maxSize, Math.ceil(height / 256) * 256))
    }
    const { full, cache, relight } = this.programs
    const profile = window.__plairProfile
    const drawStart = profile ? performance.now() : 0

    const builds = group.filter(item => item.mode === 'build')
    if (builds.length) {
      gl.useProgram(cache.program)
      for (const item of builds) {
        const view = item.view
        if (!view.cache || view.cache.width !== item.width || view.cache.height !== item.height) {
          this.dropCache(view)
          view.cache = createParallaxCache(gl, item.width, item.height, this.programs.floatHit)
        }
        gl.bindFramebuffer(gl.FRAMEBUFFER, view.cache.framebuffer)
        gl.viewport(0, 0, item.width, item.height)
        gl.activeTexture(gl.TEXTURE0)
        gl.bindTexture(gl.TEXTURE_2D, item.entry.color)
        gl.activeTexture(gl.TEXTURE1)
        gl.bindTexture(gl.TEXTURE_2D, item.entry.map)
        gl.uniform2f(cache.uniforms.gyro, item.px, item.py)
        gl.uniform1f(cache.uniforms.intensity, view.intensity)
        gl.uniform1f(cache.uniforms.steps, item.steps)
        bindDepthBound(gl, this.programs, cache.uniforms, item.entry.bound)
        gl.drawArrays(gl.TRIANGLES, 0, 6)
        Object.assign(view.cache, { art: item.entry, px: item.px, py: item.py, ready: true })
      }
      gl.bindFramebuffer(gl.FRAMEBUFFER, null)
    }

    const fulls = group.filter(item => item.mode === 'full')
    if (fulls.length) {
      gl.useProgram(full.program)
      const lit = setLightUniforms(gl, full.uniforms, probe, true)
      for (const item of fulls) {
        gl.viewport(item.x, canvas.height - item.y - item.height, item.width, item.height)
        gl.activeTexture(gl.TEXTURE0)
        gl.bindTexture(gl.TEXTURE_2D, item.entry.color)
        gl.activeTexture(gl.TEXTURE1)
        gl.bindTexture(gl.TEXTURE_2D, item.entry.map)
        gl.activeTexture(gl.TEXTURE2)
        gl.bindTexture(gl.TEXTURE_2D, item.entry.map)
        gl.uniform2f(full.uniforms.gyro, item.px, item.py)
        gl.uniform1f(full.uniforms.intensity, item.view.intensity)
        gl.uniform1f(full.uniforms.steps, item.steps)
        bindDepthBound(gl, this.programs, full.uniforms, item.entry.bound)
        if (lit) setLightRect(gl, full.uniforms, item.rect)
        for (let extra = 0; extra <= this.bench.extraFull; extra++) gl.drawArrays(gl.TRIANGLES, 0, 6)
      }
    }

    const flats = group.filter(item => item.mode === 'flat')
    if (flats.length) {
      gl.useProgram(this.programs.flat.program)
      for (const item of flats) {
        gl.viewport(item.x, canvas.height - item.y - item.height, item.width, item.height)
        gl.activeTexture(gl.TEXTURE0)
        gl.bindTexture(gl.TEXTURE_2D, item.entry.color)
        gl.drawArrays(gl.TRIANGLES, 0, 6)
      }
    }

    const relit = group.filter(item => item.mode === 'relight' || item.mode === 'build')
    if (relit.length) {
      gl.useProgram(relight.program)
      setLightUniforms(gl, relight.uniforms, probe, true)
      for (const item of relit) {
        const cached = item.view.cache
        gl.viewport(item.x, canvas.height - item.y - item.height, item.width, item.height)
        gl.activeTexture(gl.TEXTURE0)
        gl.bindTexture(gl.TEXTURE_2D, cached.color)
        gl.activeTexture(gl.TEXTURE1)
        gl.bindTexture(gl.TEXTURE_2D, cached.hit)
        gl.activeTexture(gl.TEXTURE2)
        gl.bindTexture(gl.TEXTURE_2D, item.entry.map)
        setLightRect(gl, relight.uniforms, item.rect)
        gl.drawArrays(gl.TRIANGLES, 0, 6)
        item.px = cached.px
        item.py = cached.py
      }
    }

    const copyStart = profile ? (gl.finish(), performance.now()) : 0
    for (const item of group) {
      if (this.skipCopy && item.view.drawn) continue
      const view = item.view
      if (view.canvas.width !== item.width || view.canvas.height !== item.height) {
        view.canvas.width = item.width
        view.canvas.height = item.height
      }
      if (!view.ctx) view.ctx = view.canvas.getContext('2d', { alpha: false })
      if (!view.ctx) continue
      for (let extra = 0; extra <= this.bench.extraCopy; extra++) view.ctx.drawImage(canvas, item.x, item.y, item.width, item.height, 0, 0, item.width, item.height)
      view.last = {
        flat: item.mode === 'flat', entry: item.entry, width: item.width, height: item.height, px: item.px, py: item.py,
        light: probe.key, left: item.rect.left, top: item.rect.top,
      }
      if (!view.drawn) {
        view.drawn = true
        if (view.shownAt) {
          view.drawnAfterMs = Math.round(performance.now() - view.shownAt)
          this.drawDelays.push(view.drawnAfterMs)
          if (this.drawDelays.length > 200) this.drawDelays.shift()
        }
        view.onDrawn?.()
      }
    }
    if (profile) {
      const pixels = group.reduce((total, item) => total + item.width * item.height, 0)
      profile.tiles = profile.tiles || { frames: 0, drawMs: 0, copyMs: 0, pixels: 0, full: 0, build: 0, relight: 0 }
      profile.tiles.frames++
      for (const item of group) profile.tiles[item.mode]++
      profile.tiles.drawMs += copyStart - drawStart
      profile.tiles.copyMs += performance.now() - copyStart
      profile.tiles.pixels += pixels
    }
  }
}

export const depthArtRenderer = new DepthArtRenderer()

if (typeof window !== 'undefined') {
  window.__plairArt = {
    stats: () => {
      const r = depthArtRenderer
      const mb = bytes => +(bytes / 1048576).toFixed(1)
      let active = 0, idle = 0, warm = 0, textureBytes = 0, cacheBytes = 0, caches = 0
      const textureSizes = {}
      for (const entry of r.textures.values()) {
        if (entry.refs > 0) active++
        else if (entry.warm) warm++
        else idle++
        if (!entry.sizes) continue
        const [cw, ch, mw, mh] = entry.sizes
        textureBytes += cw * ch * 4 + mw * mh * (4 + 4 / 3)
        textureSizes[cw] = (textureSizes[cw] || 0) + 1
      }
      const viewSizes = {}
      for (const view of r.views) {
        if (view.cache) {
          caches++
          cacheBytes += view.cache.width * view.cache.height * (4 + (r.programs?.floatHit ? 8 : 4))
        }
        const key = `${view.canvas.width}<-${view.entry?.sizes?.[0] || '?'}`
        viewSizes[key] = (viewSizes[key] || 0) + 1
      }
      return {
        views: r.views.size, active, idle, warm, textureMB: mb(textureBytes), textureSizes, caches, cacheMB: mb(cacheBytes),
        atlas: r.canvas ? `${r.canvas.width}x${r.canvas.height}` : null, viewSizes,
      }
    },
    views: () => [...depthArtRenderer.views].filter(view => view.rect && view.rect.bottom > 0 && view.rect.top < viewport.height && view.rect.width > 60).map(view => ({
      top: Math.round(view.rect.top), shown: view.shown, drawn: view.drawn, entry: view.entry?.state || 'none', warmHit: !!view.entry?.warmed,
      size: view.entry?.sizes?.[0], canvas: `${view.canvas.width}x${view.canvas.height}`, drawnAfterMs: view.drawnAfterMs,
    })),
    drawDelays: () => depthArtRenderer.drawDelays.splice(0),
    blank: () => {
      const byCanvas = new Map([...depthArtRenderer.views].map(view => [view.canvas, view]))
      const out = { onScreen: 0, blank: 0, noPack: 0, decoding: 0, uploading: 0, readyNotDrawn: 0 }
      for (const canvas of document.querySelectorAll('canvas[role=img]')) {
        const box = canvas.getBoundingClientRect()
        if (box.width < 60 || box.bottom < 0 || box.top > viewport.height || box.right < 0 || box.left > viewport.width) continue
        const clip = canvas.closest('[data-scroller]')?.getBoundingClientRect()
        if (clip && (box.bottom <= clip.top || box.top >= clip.bottom || box.right <= clip.left || box.left >= clip.right)) continue
        out.onScreen++
        if (canvas.style.opacity !== '0') continue
        out.blank++
        const view = byCanvas.get(canvas)
        if (!view) out.noPack++
        else if (!view.entry || view.entry.state === 'loading') out.decoding++
        else if (depthArtRenderer.uploads.some(upload => upload.entry === view.entry)) out.uploading++
        else out.readyNotDrawn++
      }
      return out
    },
    set: ({ maxIdle, lit, redrawShiftPx, skipCopy, skipDraw, forceSplit, stepScale, bench, shader, stepPx } = {}) => {
      if (stepPx !== undefined) depthArtRenderer.configure({ stepPx })
      if (bench !== undefined) Object.assign(depthArtRenderer.bench, bench)
      if (shader !== undefined && depthArtRenderer.gl) {
        depthArtRenderer.shaderOptions = shader
        depthArtRenderer.setupContext(depthArtRenderer.gl)
        for (const view of depthArtRenderer.views) { view.last = null; depthArtRenderer.dropCache(view) }
      }
      if (forceSplit !== undefined) depthArtRenderer.forceSplit = forceSplit
      if (stepScale !== undefined) depthArtRenderer.stepScale = stepScale
      if (skipCopy !== undefined) depthArtRenderer.skipCopy = skipCopy
      if (skipDraw !== undefined) depthArtRenderer.skipDraw = skipDraw
      if (maxIdle !== undefined) depthArtRenderer.maxIdle = maxIdle
      if (redrawShiftPx !== undefined) depthArtRenderer.redrawShiftPx = redrawShiftPx
      if (lit !== undefined) depthArtRenderer.configure({ lit })
      depthArtRenderer.evictIdle()
    },
  }
}
