import { POM, bindDepthBound, createDepthArtPrograms, createDepthBound, createParallaxCache, deleteParallaxCache, parallaxSteps, setLightRect, setLightUniforms, viewport } from './depthArtShader'
import { noteLightConsumer, readLightProbe } from './lightProbe'
import { isSceneRenderingPaused } from './renderPause'
import { logger } from './logger'
import { addFrameWork, createGpuTimer, frameStatsActive } from './frameStats'
import { blobForUrl } from './mediaCache'

const INTENSITY = 0.1
const SCROLL_TILT = 0.9
const MAX_PARALLAX = 1.6
const MAX_IDLE_TEXTURES = 8
const UPLOADS_PER_FRAME = 2
const REDRAW_SHIFT_PX = 0.1
const LAYOUT_REFRESH_MS = 250
const CACHE_AFTER_STILL_DRAWS = 2
const CANVAS_RESIZES_PER_FRAME = 3
const TEXTURE_STEP_PX = 64
const CONTEXT_OPTIONS = { alpha: false, antialias: false, depth: false, stencil: false }
const NO_PARALLAX = { parallaxX: 0, parallaxY: 0 }

async function loadImage(url, size) {
  if (typeof createImageBitmap === 'function') {
    let blob = blobForUrl(url)
    if (!blob) {
      const response = await fetch(url)
      if (!response.ok) throw new Error(`image ${response.status}`)
      blob = await response.blob()
    }
    const bitmap = await createImageBitmap(blob, { premultiplyAlpha: 'none', colorSpaceConversion: 'default' })
    const side = Math.max(bitmap.width, bitmap.height)
    if (side <= size) return bitmap
    const scale = size / side
    try {
      const resized = await createImageBitmap(bitmap, {
        resizeWidth: Math.max(1, Math.round(bitmap.width * scale)),
        resizeHeight: Math.max(1, Math.round(bitmap.height * scale)),
        resizeQuality: 'high',
      })
      bitmap.close()
      return resized
    } catch {
      return bitmap
    }
  }
  const image = new Image()
  image.decoding = 'async'
  image.src = url
  await image.decode()
  return image
}

function createAtlas() {
  if (typeof OffscreenCanvas !== 'undefined' && typeof OffscreenCanvas.prototype.transferToImageBitmap === 'function') {
    const canvas = new OffscreenCanvas(256, 256)
    const options = { ...CONTEXT_OPTIONS, preserveDrawingBuffer: false }
    const gl = canvas.getContext('webgl2', options) || canvas.getContext('webgl', options)
    if (gl) return { canvas, gl, options, snapshot: true }
  }
  const canvas = document.createElement('canvas')
  const options = { ...CONTEXT_OPTIONS, preserveDrawingBuffer: true }
  const gl = canvas.getContext('webgl2', options) || canvas.getContext('webgl', options)
  return gl ? { canvas, gl, options, snapshot: false } : null
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
    this.uploads = []
    this.settings = { dpr: Infinity, stepPx: 0, reduceMotion: false, gyroRef: null, mouseRef: null }
    this.canvas = null
    this.snapshot = false
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

  attach({ host, canvas, colorUrl, depthUrl, normalUrl, onDrawn }) {
    if (!this.ensureContext()) return () => {}
    const view = {
      host, canvas, ctx: null, colorUrl, depthUrl, normalUrl, onDrawn,
      clips: clippingAncestors(host.parentElement), visible: false, dirty: true, entry: null, last: null, drawn: false,
      shiftX: 0, shiftY: 0, styleVisible: true, next: null,
    }
    this.views.add(view)
    this.viewsVersion++
    this.scrolled = true
    this.schedule()
    return () => this.detach(view)
  }

  detach(view) {
    if (!this.views.delete(view)) return
    this.viewsVersion++
    if (view.entry) view.entry.refs--
    if (view.next) view.next.refs--
    view.entry = null
    view.next = null
    this.dropCache(view)
    this.evictIdle()
  }

  applyScrolls() {
    for (const target of this.scrolledTargets) {
      const last = this.scrollOffsets.get(target)
      const top = target.scrollTop
      const left = target.scrollLeft
      this.scrollOffsets.set(target, { top, left })
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
    let visible = rect.width >= 2 && rect.height >= 2 && overlaps(rect, 0, 0, viewport.width, viewport.height)
    for (const clip of view.clips) {
      if (!visible) break
      let clipRect = clipRects.get(clip)
      if (!clipRect) {
        clipRect = clip.getBoundingClientRect()
        clipRects.set(clip, clipRect)
      }
      visible = overlaps(rect, clipRect.left, clipRect.top, clipRect.right, clipRect.bottom)
    }
    view.visible = visible
    view.shown = visible && view.styleVisible
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
      this.snapshot = atlas.snapshot
      this.setupContext(gl)
      canvas.addEventListener('webglcontextlost', (event) => {
        event.preventDefault()
        logger.warn('[DepthArt] WebGL context lost')
        this.contextLost = true
        this.textures.clear()
        this.uploads = []
        for (const view of this.views) {
          view.entry = null
          view.next = null
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
    const programs = createDepthArtPrograms(gl)
    for (const entry of [programs.full, programs.cache]) {
      if (!entry) continue
      gl.useProgram(entry.program)
      gl.uniform1f(entry.uniforms.intensity, INTENSITY)
      gl.uniform1f(entry.uniforms.zoom, 1)
    }
    this.gl = gl
    this.programs = programs
    this.gpuTimer = createGpuTimer(gl, 'art gpu')
    const dims = gl.getParameter(gl.MAX_VIEWPORT_DIMS)
    this.maxSize = Math.min(4096, gl.getParameter(gl.MAX_RENDERBUFFER_SIZE), dims[0], dims[1])
  }

  textureSize(rect, dpr) {
    const side = Math.max(rect.width, rect.height) * dpr * (1 + INTENSITY * POM.ZOOM_FACTOR)
    return Math.min(this.maxSize, Math.max(TEXTURE_STEP_PX, Math.ceil(side / TEXTURE_STEP_PX) * TEXTURE_STEP_PX))
  }

  sizeTextures(view, dpr) {
    if (view.rect.width < 2 || view.rect.height < 2) return
    const size = this.textureSize(view.rect, dpr)
    if (view.next) {
      if (view.next.state === 'ready') {
        view.entry.refs--
        view.entry = view.next
        view.next = null
        view.last = null
        this.dropCache(view)
        this.evictIdle()
      } else if (view.next.state === 'failed') {
        view.next.refs--
        view.next = null
      }
      return
    }
    if (!view.entry) {
      view.entry = this.acquire(view, size)
    } else if (size > view.entry.size && !view.entry.capped && view.entry.state === 'ready') {
      view.next = this.acquire(view, size)
    }
  }

  acquire({ colorUrl, depthUrl, normalUrl }, size) {
    const key = `${colorUrl}|${depthUrl}|${normalUrl}|${size}`
    let entry = this.textures.get(key)
    if (!entry) {
      entry = { key, size, capped: false, refs: 0, state: 'loading', color: null, depth: null, bound: null, normal: null, usedAt: 0 }
      this.textures.set(key, entry)
      Promise.all([loadImage(colorUrl, size), loadImage(depthUrl, size), loadImage(normalUrl, size)])
        .then(([color, depth, normal]) => {
          if (this.textures.get(key) !== entry) return
          entry.capped = Math.max(color.width, color.height) < size
          this.uploads.push({ entry, color, depth, normal })
          this.schedule()
        })
        .catch((error) => {
          logger.debug('[DepthArt] Image load failed:', error)
          entry.state = 'failed'
        })
    }
    entry.refs++
    entry.usedAt = performance.now()
    return entry
  }

  evictIdle() {
    let idle = 0
    for (const entry of this.textures.values()) if (entry.refs <= 0) idle++
    if (idle <= this.maxIdle) return
    const byAge = [...this.textures.values()].filter(entry => entry.refs <= 0).sort((a, b) => a.usedAt - b.usedAt)
    for (const entry of byAge.slice(0, idle - this.maxIdle)) this.release(entry)
  }

  release(entry) {
    this.textures.delete(entry.key)
    if (this.gl && !this.contextLost) {
      if (entry.color) this.gl.deleteTexture(entry.color)
      if (entry.depth) this.gl.deleteTexture(entry.depth)
      if (entry.bound) this.gl.deleteTexture(entry.bound.texture)
      if (entry.normal) this.gl.deleteTexture(entry.normal)
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
    for (let i = 0; i < UPLOADS_PER_FRAME && this.uploads.length; i++) {
      const { entry, color, depth, normal } = this.uploads.shift()
      if (this.textures.get(entry.key) !== entry) {
        for (const image of [color, depth, normal]) image.close?.()
        continue
      }
      gl.activeTexture(gl.TEXTURE0)
      entry.color = this.createTexture(color, gl.RGBA)
      entry.depth = this.createTexture(depth, gl.LUMINANCE)
      entry.bound = createDepthBound(gl, this.programs, entry.depth, depth.width, depth.height)
      entry.normal = this.createTexture(normal, gl.RGB)
      entry.sizes = [color.width, color.height, depth.width, depth.height, normal.width, normal.height]
      entry.state = 'ready'
      for (const image of [color, depth, normal]) image.close?.()
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
    if (!relayout && !scrolled && !moved && !uploaded && !lit) return
    this.lastInput = { x: base.parallaxX, y: base.parallaxY, light: probe.key }
    this.scrolled = false
    if (relayout) {
      this.layoutDirty = false
      this.lastLayoutAt = timestamp
    }
    this.applyScrolls()
    const clipRects = new Map()
    const viewportHalf = Math.max(1, viewport.height / 2)
    const dpr = Math.min(window.devicePixelRatio || 1, this.settings.dpr)
    const zoom = 1 + INTENSITY * POM.ZOOM_FACTOR
    const batch = []
    let showing = false
    let resized = 0

    for (const view of this.views) {
      if (relayout || view.dirty || !view.rect) {
        view.dirty = false
        this.measure(view, clipRects)
      } else if (view.shiftX || view.shiftY) {
        this.reposition(view, clipRects)
      }
      this.sizeTextures(view, dpr)
      const entry = view.entry
      if (!entry || entry.state !== 'ready') continue
      if (!view.shown) continue
      const rect = view.rect
      showing = true
      const width = Math.min(this.maxSize, Math.round(rect.width * dpr))
      const height = Math.min(this.maxSize, Math.round(rect.height * dpr))
      if (view.canvas.width !== width || view.canvas.height !== height) {
        if (resized >= CANVAS_RESIZES_PER_FRAME) continue
        resized++
      }
      const tilt = reduceMotion ? 0 : clamp((rect.top + rect.height / 2 - viewportHalf) / viewportHalf, -1, 1) * SCROLL_TILT
      const px = clamp(base.parallaxX, -MAX_PARALLAX, MAX_PARALLAX)
      const py = clamp(base.parallaxY + tilt, -MAX_PARALLAX, MAX_PARALLAX)
      const pixelsPerUnit = INTENSITY * Math.max(width, height) * zoom
      const epsilon = REDRAW_SHIFT_PX / (0.5 * pixelsPerUnit)
      const last = view.last
      const tiltStill = last && last.entry === entry && last.width === width && last.height === height &&
        Math.abs(last.px - px) < epsilon && Math.abs(last.py - py) < epsilon
      if (tiltStill && last.light === probe.key && last.left === rect.left && last.top === rect.top) continue
      const cache = view.cache
      const cached = probe.active && cache?.ready && cache.art === entry && cache.width === width && cache.height === height &&
        Math.abs(cache.px - px) < epsilon && Math.abs(cache.py - py) < epsilon
      view.stillDraws = tiltStill ? (view.stillDraws || 0) + 1 : 0
      const build = !cached && probe.active && !!this.programs.cache && view.stillDraws >= CACHE_AFTER_STILL_DRAWS
      batch.push({
        view, entry, rect, width, height, px, py,
        steps: parallaxSteps(Math.hypot(px, py) * pixelsPerUnit, stepPx),
        mode: cached ? 'relight' : build ? 'build' : 'full',
      })
    }

    this.showing = showing
    if (!batch.length) return
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
        gl.bindTexture(gl.TEXTURE_2D, item.entry.depth)
        gl.uniform2f(cache.uniforms.gyro, item.px, item.py)
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
        gl.bindTexture(gl.TEXTURE_2D, item.entry.depth)
        gl.activeTexture(gl.TEXTURE2)
        gl.bindTexture(gl.TEXTURE_2D, item.entry.normal)
        gl.uniform2f(full.uniforms.gyro, item.px, item.py)
        gl.uniform1f(full.uniforms.steps, item.steps)
        bindDepthBound(gl, this.programs, full.uniforms, item.entry.bound)
        if (lit) setLightRect(gl, full.uniforms, item.rect)
        gl.drawArrays(gl.TRIANGLES, 0, 6)
      }
    }

    const relit = group.filter(item => item.mode !== 'full')
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
        gl.bindTexture(gl.TEXTURE_2D, item.entry.normal)
        setLightRect(gl, relight.uniforms, item.rect)
        gl.drawArrays(gl.TRIANGLES, 0, 6)
        item.px = cached.px
        item.py = cached.py
      }
    }

    const copyStart = profile ? (gl.finish(), performance.now()) : 0
    const source = this.snapshot ? canvas.transferToImageBitmap() : canvas
    for (const item of group) {
      const view = item.view
      if (view.canvas.width !== item.width || view.canvas.height !== item.height) {
        view.canvas.width = item.width
        view.canvas.height = item.height
      }
      if (!view.ctx) view.ctx = view.canvas.getContext('2d', { alpha: false })
      if (!view.ctx) continue
      view.ctx.drawImage(source, item.x, item.y, item.width, item.height, 0, 0, item.width, item.height)
      view.last = {
        entry: item.entry, width: item.width, height: item.height, px: item.px, py: item.py,
        light: probe.key, left: item.rect.left, top: item.rect.top,
      }
      if (!view.drawn) {
        view.drawn = true
        view.onDrawn?.(true)
      }
    }
    if (source !== canvas) source.close()
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
      let active = 0, idle = 0, textureBytes = 0, cacheBytes = 0, caches = 0
      const textureSizes = {}
      for (const entry of r.textures.values()) {
        if (entry.refs > 0) active++
        else idle++
        if (!entry.sizes) continue
        const [cw, ch, dw, dh, nw, nh] = entry.sizes
        textureBytes += cw * ch * 4 + dw * dh * (1 + 4 / 3) + nw * nh * 4
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
        views: r.views.size, active, idle, textureMB: mb(textureBytes), textureSizes, caches, cacheMB: mb(cacheBytes),
        atlas: r.canvas ? `${r.canvas.width}x${r.canvas.height}` : null, viewSizes,
      }
    },
    set: ({ maxIdle } = {}) => {
      if (maxIdle !== undefined) depthArtRenderer.maxIdle = maxIdle
      depthArtRenderer.evictIdle()
    },
  }
}
