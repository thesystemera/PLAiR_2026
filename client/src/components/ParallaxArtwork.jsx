import { memo, useEffect, useRef, useState, useCallback } from 'react'
import { useEnrichedArtwork, useUISelector } from '../contexts/UIStateContext'
import { useViewport } from '../contexts/ViewportContext'
import { useQuality } from '../contexts/QualityContext'
import { isSceneRenderingPaused } from '../lib/renderPause'
import { logger } from '../lib/logger'
import { CSS_TRANSITION } from '../lib/motion'
import { POM, bindDepthBound, createDepthArtPrograms, createDepthBound, createParallaxCache, deleteParallaxCache, parallaxSteps, setLightRect, setLightUniforms } from '../lib/depthArtShader'
import { noteLightConsumer, readLightProbe } from '../lib/lightProbe'
import { normalFullCache } from '../lib/mediaCache'
import { useDepthMap } from '../hooks/useDepthMap'

const PARALLAX_EPSILON = 1e-4
const REDRAW_SHIFT_PX = 0.1
const FRAME_CAP_SLACK_MS = 4
const MIPMAP_BELOW_RATIO = 0.75
const CACHE_AFTER_STILL_DRAWS = 2
const RESIZE_SETTLE_MS = 150
const PLACEMENT_REFRESH_MS = 250
const CONTEXT_OPTIONS = { alpha: false, antialias: false, depth: false, preserveDrawingBuffer: false }

const getContext = canvas => canvas.getContext('webgl2', CONTEXT_OPTIONS) || canvas.getContext('webgl', CONTEXT_OPTIONS)

const isPowerOfTwo = value => value > 0 && (value & (value - 1)) === 0

export const ParallaxArtwork = memo(function ParallaxArtwork({
  trackId,
  artworkUrl,
  alt = 'Album artwork',
  className = '',
  intensity = 0.15,
  zoom = 1.0,
  onLoad,
  onError,
  isActive = true
}) {
  const enrichedArtworkUrl = useEnrichedArtwork(trackId)
  const normalUrl = useDepthMap(normalFullCache, trackId)

  const canvasRef = useRef(null)
  const glRef = useRef(null)
  const programRef = useRef(null)
  const colorTextureRef = useRef(null)
  const depthTextureRef = useRef(null)
  const depthBoundRef = useRef(null)
  const normalTextureRef = useRef(null)
  const animationFrameRef = useRef(null)
  const cacheRef = useRef(null)
  const lastDrawRef = useRef(null)
  const colorTextureInfoRef = useRef(null)

  const [glReady, setGlReady] = useState(false)
  const [texturesKey, setTexturesKey] = useState(null)
  const [normalKey, setNormalKey] = useState(null)
  const [fallbackMode, setFallbackMode] = useState(false)
  const [contextLost, setContextLost] = useState(false)
  const [onScreen, setOnScreen] = useState(true)

  const { gyroscopeRef, mouseRef, litArtwork } = useUISelector(state => ({ gyroscopeRef: state.gyroscopeRef, mouseRef: state.mouseRef, litArtwork: state.settingsState.litArtwork !== false }))
  const { isMobile } = useViewport()
  const { parallaxDpr, parallaxFpsCap, parallaxStepPx, reduceMotion, isTopTier } = useQuality()
  const loadKey = `${trackId}|${enrichedArtworkUrl}|${artworkUrl}`
  const texturesReady = texturesKey === loadKey
  const isVisible = !isMobile || onScreen
  const hasNormals = !!normalUrl && normalKey === normalUrl

  useEffect(() => {
    window.registerRAFSource?.('ParallaxArtwork')
  }, [])

  const handleContextLost = useCallback((event) => {
    event.preventDefault()
    logger.warn('[ParallaxArtwork] WebGL context lost')
    setContextLost(true)
    setGlReady(false)
    setTexturesKey(null)
    setNormalKey(null)
    cacheRef.current = null

    if (animationFrameRef.current) {
      cancelAnimationFrame(animationFrameRef.current)
      animationFrameRef.current = null
    }
  }, [])

  const handleContextRestored = useCallback(() => {
    logger.info('[ParallaxArtwork] WebGL context restored, attempting recovery')
    setContextLost(false)

    const canvas = canvasRef.current
    if (!canvas) return

    const gl = getContext(canvas)

    if (!gl) {
      logger.error('[ParallaxArtwork] Failed to restore WebGL context')
      setFallbackMode(true)
      return
    }

    glRef.current = gl

    try {
      programRef.current = createDepthArtPrograms(gl)

      setGlReady(true)
      logger.info('[ParallaxArtwork] WebGL context successfully restored')
    } catch (error) {
      logger.error('[ParallaxArtwork] Error during context restore:', error)
      setFallbackMode(true)
    }
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const gl = getContext(canvas)

    if (!gl) {
      logger.warn('[ParallaxArtwork] WebGL not available, using fallback')
      queueMicrotask(() => setFallbackMode(true))
      return
    }

    glRef.current = gl

    try {
      programRef.current = createDepthArtPrograms(gl)

      canvas.addEventListener('webglcontextlost', handleContextLost)
      canvas.addEventListener('webglcontextrestored', handleContextRestored)

      queueMicrotask(() => setGlReady(true))
      logger.debug('[ParallaxArtwork] WebGL initialized successfully')
    } catch (error) {
      logger.error('[ParallaxArtwork] WebGL initialization error:', error)
      queueMicrotask(() => setFallbackMode(true))
      return
    }

    return () => {
      canvas.removeEventListener('webglcontextlost', handleContextLost)
      canvas.removeEventListener('webglcontextrestored', handleContextRestored)

      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current)
      }

      if (!gl.isContextLost()) {
        for (const entry of Object.values(programRef.current || {})) if (entry) gl.deleteProgram(entry.program)
        deleteParallaxCache(gl, cacheRef.current)
        if (colorTextureRef.current) gl.deleteTexture(colorTextureRef.current)
        if (depthTextureRef.current) gl.deleteTexture(depthTextureRef.current)
        if (depthBoundRef.current) gl.deleteTexture(depthBoundRef.current.texture)
        if (normalTextureRef.current) gl.deleteTexture(normalTextureRef.current)
        gl.getExtension('WEBGL_lose_context')?.loseContext()
      }
      programRef.current = null
      cacheRef.current = null
      colorTextureRef.current = null
      depthTextureRef.current = null
      depthBoundRef.current = null
      normalTextureRef.current = null
      glRef.current = null
    }
  }, [handleContextLost, handleContextRestored])

  useEffect(() => {
    if (!isMobile) return

    const canvas = canvasRef.current
    if (!canvas) return

    const observer = new IntersectionObserver(
      ([entry]) => {
        setOnScreen(entry.isIntersecting)
        if (entry.isIntersecting) {
          logger.debug('[ParallaxArtwork] Canvas visible, resuming render')
        } else {
          logger.debug('[ParallaxArtwork] Canvas hidden, pausing render')
        }
      },
      { threshold: 0.01 }
    )

    observer.observe(canvas)
    return () => observer.disconnect()
  }, [isMobile])

  useEffect(() => {
    if (!glReady || !trackId || fallbackMode || contextLost) return

    const gl = glRef.current
    if (!gl || gl.isContextLost()) {
      logger.warn('[ParallaxArtwork] Cannot load textures - context lost')
      return
    }

    let cancelled = false
    const image = new Image()
    image.crossOrigin = 'anonymous'

    image.decoding = 'async'
    image.onload = async () => {
      try {
        await image.decode?.()
      } catch {
        logger.debug('[ParallaxArtwork] Async decode unavailable, drawing directly')
      }
      if (cancelled) {
        logger.debug('[ParallaxArtwork] Image load cancelled (track changed)')
        return
      }

      if (!gl || gl.isContextLost()) {
        logger.warn('[ParallaxArtwork] Context lost before texture creation')
        setFallbackMode(true)
        return
      }

      try {
        const halfWidth = Math.floor(image.width / 2)
        const height = image.height

        const splitCanvas = document.createElement('canvas')
        splitCanvas.width = halfWidth
        splitCanvas.height = height
        const ctx = splitCanvas.getContext('2d')

        ctx.drawImage(image, 0, 0, halfWidth, height, 0, 0, halfWidth, height)

        const newColorTexture = gl.createTexture()
        gl.activeTexture(gl.TEXTURE0)
        gl.bindTexture(gl.TEXTURE_2D, newColorTexture)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, splitCanvas)
        const canMipmap = isPowerOfTwo(halfWidth) && isPowerOfTwo(height)
        if (canMipmap) gl.generateMipmap(gl.TEXTURE_2D)
        colorTextureInfoRef.current = { width: halfWidth, canMipmap, filter: gl.LINEAR }

        ctx.drawImage(image, halfWidth, 0, halfWidth, height, 0, 0, halfWidth, height)

        const newDepthTexture = gl.createTexture()
        gl.activeTexture(gl.TEXTURE1)
        gl.bindTexture(gl.TEXTURE_2D, newDepthTexture)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.LUMINANCE, gl.LUMINANCE, gl.UNSIGNED_BYTE, splitCanvas)
        const newDepthBound = programRef.current ? createDepthBound(gl, programRef.current, newDepthTexture, halfWidth, height) : null

        if (colorTextureRef.current && !gl.isContextLost()) {
          gl.deleteTexture(colorTextureRef.current)
        }
        if (depthTextureRef.current && !gl.isContextLost()) {
          gl.deleteTexture(depthTextureRef.current)
        }
        if (depthBoundRef.current && !gl.isContextLost()) {
          gl.deleteTexture(depthBoundRef.current.texture)
        }

        colorTextureRef.current = newColorTexture
        depthTextureRef.current = newDepthTexture
        depthBoundRef.current = newDepthBound

        setTexturesKey(loadKey)
        if (onLoad) onLoad()
      } catch (error) {
        logger.error('[ParallaxArtwork] Error creating textures:', error)
        setFallbackMode(true)
        if (onError) onError()
      }
    }

    image.onerror = () => {
      if (cancelled) return
      logger.error('[ParallaxArtwork] Failed to load artwork image')
      setFallbackMode(true)
      if (onError) onError()
    }

    const enrichedIsBlob = enrichedArtworkUrl?.startsWith('blob:')
    const enrichedIsPlaceholder = enrichedArtworkUrl?.startsWith('data:image/svg')
    const regularIsBlob = artworkUrl?.startsWith('blob:')

    if (enrichedIsBlob) {
      image.src = enrichedArtworkUrl
    } else if (enrichedIsPlaceholder) {
      image.src = enrichedArtworkUrl
    } else if (regularIsBlob) {
      image.src = artworkUrl
    } else if (artworkUrl) {
      image.src = artworkUrl
    } else {
      logger.warn('[ParallaxArtwork] No artwork source available')
      queueMicrotask(() => setFallbackMode(true))
    }

    return () => {
      cancelled = true
    }
  }, [glReady, trackId, enrichedArtworkUrl, artworkUrl, loadKey, fallbackMode, contextLost, onLoad, onError])

  useEffect(() => {
    if (!glReady || !normalUrl || contextLost) return
    const gl = glRef.current
    if (!gl || gl.isContextLost()) return

    let cancelled = false
    const image = new Image()
    image.decoding = 'async'
    image.src = normalUrl
    image.decode().then(() => {
      if (cancelled || gl.isContextLost()) return
      const texture = gl.createTexture()
      gl.activeTexture(gl.TEXTURE2)
      gl.bindTexture(gl.TEXTURE_2D, texture)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, image)
      if (normalTextureRef.current) gl.deleteTexture(normalTextureRef.current)
      normalTextureRef.current = texture
      setNormalKey(normalUrl)
    }).catch(() => {
      logger.debug('[ParallaxArtwork] Normal map failed to load')
    })

    return () => {
      cancelled = true
    }
  }, [glReady, normalUrl, contextLost])

  useEffect(() => {
    if (!glReady || !texturesReady || fallbackMode || contextLost || !isVisible || !isActive || !litArtwork) return

    const gl = glRef.current
    const canvas = canvasRef.current
    const programs = programRef.current

    if (!gl || !canvas || !programs) {
      logger.warn('[ParallaxArtwork] Missing required refs for render loop')
      return
    }

    lastDrawRef.current = null
    let errorCheckPending = true
    let stillDraws = 0
    const placement = { rect: null, at: 0, dirty: true }
    const minFrameMs = parallaxFpsCap > 0 ? 1000 / parallaxFpsCap - FRAME_CAP_SLACK_MS : 0

    const bindArtwork = (uniforms) => {
      gl.activeTexture(gl.TEXTURE0)
      gl.bindTexture(gl.TEXTURE_2D, colorTextureRef.current)
      const colorInfo = colorTextureInfoRef.current
      if (colorInfo) {
        const minify = !isTopTier && colorInfo.canMipmap && canvas.width < colorInfo.width * MIPMAP_BELOW_RATIO
        const filter = minify ? gl.LINEAR_MIPMAP_LINEAR : gl.LINEAR
        if (colorInfo.filter !== filter) {
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter)
          colorInfo.filter = filter
        }
      }
      gl.activeTexture(gl.TEXTURE1)
      gl.bindTexture(gl.TEXTURE_2D, depthTextureRef.current)
      bindDepthBound(gl, programs, uniforms, depthBoundRef.current)
    }

    const setParallax = (uniforms, parallaxX, parallaxY, steps) => {
      gl.uniform2f(uniforms.gyro, parallaxX, parallaxY)
      gl.uniform1f(uniforms.intensity, intensity)
      gl.uniform1f(uniforms.zoom, zoom)
      gl.uniform1f(uniforms.steps, steps)
    }

    const render = (timestamp) => {
      try {
        if (!gl || !canvas || !programs) {
          return
        }

        if (gl.isContextLost()) {
          logger.warn('[ParallaxArtwork] Context lost during render')
          return
        }

        if (!colorTextureRef.current || !depthTextureRef.current) {
          window.__rafDebug?.sources && (window.__rafDebug.sources['ParallaxArtwork'] = (window.__rafDebug.sources['ParallaxArtwork'] || 0) + 1)
          animationFrameRef.current = requestAnimationFrame(render)
          return
        }

        const gyro = gyroscopeRef?.current || { parallaxX: 0, parallaxY: 0 }
        const mouse = mouseRef?.current || { parallaxX: 0, parallaxY: 0 }

        const hasGyro = Math.abs(gyro.parallaxX) > 0.001 || Math.abs(gyro.parallaxY) > 0.001
        let parallaxX = reduceMotion ? 0 : hasGyro ? gyro.parallaxX : mouse.parallaxX
        let parallaxY = reduceMotion ? 0 : hasGyro ? gyro.parallaxY : mouse.parallaxY

        const effectiveZoom = zoom * (1 + Math.abs(intensity) * POM.ZOOM_FACTOR)
        const pixelsPerUnit = Math.abs(intensity) * Math.max(canvas.width, canvas.height) * effectiveZoom
        const epsilon = Math.max(PARALLAX_EPSILON, pixelsPerUnit > 0 ? REDRAW_SHIFT_PX / (0.5 * pixelsPerUnit) : PARALLAX_EPSILON)

        const now = timestamp ?? performance.now()
        if (hasNormals) noteLightConsumer(now)
        const probe = readLightProbe(now)
        if (placement.dirty || !placement.rect || now - placement.at > PLACEMENT_REFRESH_MS) {
          placement.rect = canvas.getBoundingClientRect()
          placement.at = now
          placement.dirty = false
        }
        const rect = placement.rect

        const last = lastDrawRef.current
        const tiltStill = last &&
          last.color === colorTextureRef.current &&
          last.width === canvas.width &&
          last.height === canvas.height &&
          Math.abs(last.x - parallaxX) < epsilon &&
          Math.abs(last.y - parallaxY) < epsilon
        const unchanged = tiltStill && last.left === rect.left && last.top === rect.top && last.light === probe.key

        const paused = last && timestamp !== undefined && isSceneRenderingPaused(timestamp)
        const throttled = paused || (last && minFrameMs > 0 && timestamp !== undefined && timestamp - last.time < minFrameMs)

        if (!unchanged && !throttled) {
          stillDraws = tiltStill ? stillDraws + 1 : 0
          const lit = probe.active && hasNormals
          let cache = cacheRef.current
          const cached = lit && cache?.ready && cache.art === colorTextureRef.current &&
            cache.width === canvas.width && cache.height === canvas.height &&
            Math.abs(cache.px - parallaxX) < epsilon && Math.abs(cache.py - parallaxY) < epsilon
          const build = !cached && lit && !!programs.cache && stillDraws >= CACHE_AFTER_STILL_DRAWS
          const steps = parallaxSteps(Math.hypot(parallaxX, parallaxY) * pixelsPerUnit, parallaxStepPx)

          const profile = window.__plairProfile
          const drawStart = profile ? performance.now() : 0
          if (build) {
            if (!cache || cache.width !== canvas.width || cache.height !== canvas.height) {
              deleteParallaxCache(gl, cache)
              cache = cacheRef.current = createParallaxCache(gl, canvas.width, canvas.height, programs.floatHit)
            }
            gl.useProgram(programs.cache.program)
            setParallax(programs.cache.uniforms, parallaxX, parallaxY, steps)
            bindArtwork(programs.cache.uniforms)
            gl.bindFramebuffer(gl.FRAMEBUFFER, cache.framebuffer)
            gl.viewport(0, 0, cache.width, cache.height)
            gl.drawArrays(gl.TRIANGLES, 0, 6)
            gl.bindFramebuffer(gl.FRAMEBUFFER, null)
            Object.assign(cache, { art: colorTextureRef.current, px: parallaxX, py: parallaxY, ready: true })
          }

          gl.viewport(0, 0, canvas.width, canvas.height)
          gl.clearColor(0, 0, 0, 1)
          gl.clear(gl.COLOR_BUFFER_BIT)
          if (cached || build) {
            const { program, uniforms } = programs.relight
            gl.useProgram(program)
            if (setLightUniforms(gl, uniforms, probe, true)) setLightRect(gl, uniforms, rect)
            gl.activeTexture(gl.TEXTURE0)
            gl.bindTexture(gl.TEXTURE_2D, cache.color)
            gl.activeTexture(gl.TEXTURE1)
            gl.bindTexture(gl.TEXTURE_2D, cache.hit)
            parallaxX = cache.px
            parallaxY = cache.py
          } else {
            const { program, uniforms } = programs.full
            gl.useProgram(program)
            if (setLightUniforms(gl, uniforms, probe, hasNormals)) setLightRect(gl, uniforms, rect)
            setParallax(uniforms, parallaxX, parallaxY, steps)
            bindArtwork(uniforms)
          }
          gl.activeTexture(gl.TEXTURE2)
          gl.bindTexture(gl.TEXTURE_2D, hasNormals ? normalTextureRef.current : null)
          gl.drawArrays(gl.TRIANGLES, 0, 6)
          if (profile) {
            gl.finish()
            const kind = cached ? 'relit' : build ? 'cached' : 'full'
            profile.nowPlaying = profile.nowPlaying || { frames: 0, drawMs: 0, pixels: 0, full: 0, cached: 0, relit: 0 }
            profile.nowPlaying.frames++
            profile.nowPlaying[kind]++
            profile.nowPlaying.drawMs += performance.now() - drawStart
            profile.nowPlaying.pixels += canvas.width * canvas.height
          }

          if (errorCheckPending) {
            errorCheckPending = false
            const error = gl.getError()
            if (error !== gl.NO_ERROR) {
              logger.error(`[ParallaxArtwork] WebGL error during render: ${error}`)
              setFallbackMode(true)
              return
            }
          }

          lastDrawRef.current = {
            color: colorTextureRef.current,
            width: canvas.width,
            height: canvas.height,
            x: parallaxX,
            y: parallaxY,
            light: probe.key,
            left: rect.left,
            top: rect.top,
            time: now,
          }
        }

        window.__rafDebug?.sources && (window.__rafDebug.sources['ParallaxArtwork'] = (window.__rafDebug.sources['ParallaxArtwork'] || 0) + 1)
        animationFrameRef.current = requestAnimationFrame(render)
      } catch (error) {
        logger.error('[ParallaxArtwork] Render loop error:', error)
        setFallbackMode(true)
      }
    }

    const markMoved = (event) => {
      const target = event?.target
      if (!target || target === document || typeof target.contains !== 'function' || target.contains(canvas)) placement.dirty = true
    }
    document.addEventListener('scroll', markMoved, { capture: true, passive: true })
    window.addEventListener('resize', markMoved, { passive: true })

    render()

    return () => {
      document.removeEventListener('scroll', markMoved, { capture: true })
      window.removeEventListener('resize', markMoved)
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current)
      }
    }
  }, [glReady, texturesReady, hasNormals, litArtwork, intensity, zoom, fallbackMode, contextLost, gyroscopeRef, mouseRef, isVisible, isActive, parallaxFpsCap, parallaxStepPx, reduceMotion, isTopTier])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    let settleTimer = null
    let sized = false
    const applySize = (width, height) => {
      settleTimer = null
      sized = true
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width
        canvas.height = height
      }
    }

    const resizeObserver = new ResizeObserver(entries => {
      for (const entry of entries) {
        const deviceDpr = window.devicePixelRatio || 1
        const dpr = Math.min(deviceDpr, parallaxDpr)

        let width, height
        if (entry.devicePixelContentBoxSize && dpr === deviceDpr) {
          width = entry.devicePixelContentBoxSize[0].inlineSize
          height = entry.devicePixelContentBoxSize[0].blockSize
        } else {
          width = Math.round(entry.contentRect.width * dpr)
          height = Math.round(entry.contentRect.height * dpr)
        }

        if (settleTimer) clearTimeout(settleTimer)
        if (!sized) applySize(width, height)
        else settleTimer = setTimeout(applySize, RESIZE_SETTLE_MS, width, height)
      }
    })

    resizeObserver.observe(canvas)
    return () => {
      if (settleTimer) clearTimeout(settleTimer)
      resizeObserver.disconnect()
    }
  }, [parallaxDpr])

  const useStandardArtwork = fallbackMode || contextLost || !isVisible || !isActive || !texturesReady || !litArtwork

  if (fallbackMode || contextLost) {
    return (
      <img
        src={artworkUrl}
        alt={alt}
        decoding="async"
        className={className}
        onLoad={onLoad}
        onError={onError}
        style={{ width: '100%', height: '100%', objectFit: 'cover' }}
      />
    )
  }

  return (
    <div className={className} style={{ position: 'relative', width: '100%', height: '100%' }}>
      <img
        src={artworkUrl}
        alt={alt}
        decoding="async"
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          objectFit: 'cover'
        }}
        onLoad={!texturesReady ? onLoad : undefined}
      />
      <canvas
        ref={canvasRef}
        style={{
          position: 'absolute',
          inset: 0,
          width: '100%',
          height: '100%',
          opacity: useStandardArtwork ? 0 : 1,
          transition: CSS_TRANSITION.fadeOpacity
        }}
      />
    </div>
  )
})