import { POM, createDepthArtPrograms, createParallaxCache, deleteParallaxCache, parallaxSteps, setLightRect, setLightUniforms, viewport } from './depthArtShader'
import { noteLightConsumer, readLightProbe } from './lightProbe'
import { isSceneRenderingPaused } from './renderPause'
import { logger } from './logger'

const INTENSITY = 0.1
const SCROLL_TILT = 0.9
const MAX_PARALLAX = 1.6
const MAX_IDLE_TEXTURES = 48
const UPLOADS_PER_FRAME = 2
const REDRAW_SHIFT_PX = 0.1
const FRAME_CAP_SLACK_MS = 4
const LAYOUT_REFRESH_MS = 250
const CACHE_AFTER_STILL_DRAWS = 2
const CONTEXT_OPTIONS = { alpha: false, antialias: false, depth: false, stencil: false }
const NO_PARALLAX = { parallaxX: 0, parallaxY: 0 }

function loadImage(url) {
  const image = new Image()
  image.decoding = 'async'
  image.src = url
  return image.decode().then(() => image)
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

class DepthArtRenderer {
  constructor() {
    this.views = new Set()
    this.textures = new Map()
    this.uploads = []
    this.settings = { dpr: Infinity, fpsCap: 0, stepPx: 0, reduceMotion: false, gyroRef: null, mouseRef: null }
    this.canvas = null
    this.snapshot = false
    this.gl = null
    this.programs = null
    this.maxSize = 4096
    this.unavailable = false
    this.contextLost = false
    this.frame = null
    this.lastDrawAt = 0
    this.observer = null
    this.layoutDirty = true
    this.lastLayoutAt = 0
    this.lastInput = null
    this.showing = false
    this.tick = this.tick.bind(this)
    this.markLayoutDirty = () => {
      this.layoutDirty = true
    }
    if (typeof document !== 'undefined') {
      document.addEventListener('scroll', this.markLayoutDirty, { capture: true, passive: true })
      window.addEventListener('resize', this.markLayoutDirty, { passive: true })
    }
  }

  configure(settings) {
    Object.assign(this.settings, settings)
    for (const view of this.views) view.last = null
  }

  attach({ host, canvas, colorUrl, depthUrl, normalUrl, onDrawn }) {
    if (!this.ensureContext()) return () => {}
    const view = { host, canvas, ctx: null, colorUrl, depthUrl, normalUrl, onDrawn, visible: false, entry: null, last: null, drawn: false }
    view.entry = this.acquire(view)
    this.views.add(view)
    this.layoutDirty = true
    this.observe(view)
    this.schedule()
    return () => this.detach(view)
  }

  detach(view) {
    if (!this.views.delete(view)) return
    this.observer?.unobserve(view.host)
    if (view.entry) view.entry.refs--
    view.entry = null
    this.dropCache(view)
    this.evictIdle()
  }

  observe(view) {
    if (typeof IntersectionObserver === 'undefined') {
      view.visible = true
      return
    }
    if (!this.observer) {
      this.observer = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          for (const view of this.views) {
            if (view.host !== entry.target) continue
            view.visible = entry.isIntersecting
            if (!view.visible) this.dropCache(view)
          }
        }
        this.layoutDirty = true
        this.schedule()
      })
    }
    this.observer.observe(view.host)
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
          view.last = null
          view.cache = null
        }
      })
      canvas.addEventListener('webglcontextrestored', () => {
        try {
          this.setupContext(canvas.getContext(gl instanceof WebGLRenderingContext ? 'webgl' : 'webgl2', options))
          this.contextLost = false
          for (const view of this.views) view.entry = this.acquire(view)
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
    const dims = gl.getParameter(gl.MAX_VIEWPORT_DIMS)
    this.maxSize = Math.min(4096, gl.getParameter(gl.MAX_RENDERBUFFER_SIZE), dims[0], dims[1])
  }

  acquire({ colorUrl, depthUrl, normalUrl }) {
    const key = `${colorUrl}|${depthUrl}|${normalUrl}`
    let entry = this.textures.get(key)
    if (!entry) {
      entry = { key, refs: 0, state: 'loading', color: null, depth: null, normal: null, usedAt: 0 }
      this.textures.set(key, entry)
      Promise.all([loadImage(colorUrl), loadImage(depthUrl), loadImage(normalUrl)])
        .then(([color, depth, normal]) => {
          if (this.textures.get(key) !== entry) return
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
    if (idle <= MAX_IDLE_TEXTURES) return
    const byAge = [...this.textures.values()].filter(entry => entry.refs <= 0).sort((a, b) => a.usedAt - b.usedAt)
    for (const entry of byAge.slice(0, idle - MAX_IDLE_TEXTURES)) this.release(entry)
  }

  release(entry) {
    this.textures.delete(entry.key)
    if (this.gl && !this.contextLost) {
      if (entry.color) this.gl.deleteTexture(entry.color)
      if (entry.depth) this.gl.deleteTexture(entry.depth)
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
      if (this.textures.get(entry.key) !== entry) continue
      gl.activeTexture(gl.TEXTURE0)
      entry.color = this.createTexture(color, gl.RGBA)
      entry.depth = this.createTexture(depth, gl.LUMINANCE)
      entry.normal = this.createTexture(normal, gl.RGB)
      entry.state = 'ready'
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
    this.frame = null
    if (!this.views.size) return
    this.schedule()
    if (!this.gl || this.contextLost) return
    const uploaded = this.uploads.length > 0
    if (uploaded) this.processUploads()

    const { fpsCap, stepPx, reduceMotion } = this.settings
    if (fpsCap > 0 && timestamp - this.lastDrawAt < 1000 / fpsCap - FRAME_CAP_SLACK_MS) return
    if (isSceneRenderingPaused(timestamp)) return

    if (this.showing) noteLightConsumer(timestamp)
    const probe = readLightProbe(timestamp)
    const base = this.baseParallax()
    const relayout = this.layoutDirty || timestamp - this.lastLayoutAt > LAYOUT_REFRESH_MS
    const input = this.lastInput
    const moved = !input || Math.abs(input.x - base.parallaxX) > 1e-4 || Math.abs(input.y - base.parallaxY) > 1e-4
    const lit = !input || input.light !== probe.key
    if (!relayout && !moved && !uploaded && !lit) return
    this.lastInput = { x: base.parallaxX, y: base.parallaxY, light: probe.key }
    if (relayout) {
      this.layoutDirty = false
      this.lastLayoutAt = timestamp
    }
    const viewportHalf = Math.max(1, viewport.height / 2)
    const dpr = Math.min(window.devicePixelRatio || 1, this.settings.dpr)
    const zoom = 1 + INTENSITY * POM.ZOOM_FACTOR
    const batch = []
    let showing = false

    for (const view of this.views) {
      const entry = view.entry
      if (!view.visible || !entry || entry.state !== 'ready') continue
      if (relayout || !view.rect) {
        view.rect = view.host.getBoundingClientRect()
        view.shown = !view.host.checkVisibility || view.host.checkVisibility({ opacityProperty: true, visibilityProperty: true })
      }
      if (!view.shown) continue
      const rect = view.rect
      if (rect.width < 2 || rect.height < 2) continue
      showing = true
      const width = Math.min(this.maxSize, Math.round(rect.width * dpr))
      const height = Math.min(this.maxSize, Math.round(rect.height * dpr))
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
    this.lastDrawAt = timestamp
    this.drawBatch(batch, probe)
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
