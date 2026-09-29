import { X, Loader2, Check, AlertCircle } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { startTransition, useDeferredValue, useEffect, useState, useRef, memo, useCallback, createContext, useContext } from 'react'
import { triggerHaptic } from '../../lib/haptics'
import { useDynamicTheme } from '../../contexts/DynamicThemeContext'
import { usePointerInteraction } from '../../hooks/usePointerInteraction'
import { useArtwork, useUISelector } from '../../contexts/UIStateContext'
import { DURATION, MOTION, PRESETS, SPRING, TWEEN } from '../../lib/motion'
import { useViewport } from '../../contexts/ViewportContext'
import { useQuality } from '../../contexts/QualityContext'
import { MODAL_CLOSE_PAUSE_MS, MODAL_OPEN_PAUSE_MS } from '../../lib/renderPause'

const MODAL_BACKDROP_SAFE_STYLE = {
  backgroundColor: 'rgba(0,0,0,0.75)',
  paddingTop: 'max(0.5rem, var(--safe-top))',
  paddingBottom: 'max(0.5rem, var(--safe-bottom))',
  paddingLeft: 'max(0.5rem, var(--safe-left))',
  paddingRight: 'max(0.5rem, var(--safe-right))',
}

const MODAL_BACKDROP_STYLE = {
  backgroundColor: 'rgba(0,0,0,0.75)',
}

const MODAL_GRADIENT_TRANSITION = { ...MOTION.fade, delay: 0.1 }
const MODAL_TITLE_TRANSITION = { ...PRESETS.fadeSlide.animate.transition, delay: 0.08 }
const MODAL_CLOSE_TRANSITION = { ...PRESETS.fadeSlide.animate.transition, delay: 0.12 }

const REST_TRANSFORM = 'translateY(0px) scale(1)'
const HOVER_SPRING = SPRING.hover

const DIALOG_MOTION = {
  initial: { opacity: 0, transform: 'translateY(18px) scale(0.94)' },
  animate: { opacity: 1, transform: REST_TRANSFORM, transition: { ...SPRING.dialog, opacity: { duration: DURATION.quick } } },
  exit: { opacity: 0, transform: 'translateY(10px) scale(0.96)', transition: TWEEN.exit },
}

const CONTENT_VARIANTS = {
  hidden: {},
  show: { transition: { staggerChildren: 0.035, delayChildren: 0.04 } },
}

const ITEM_VARIANTS = {
  hidden: { opacity: 0, transform: 'translateY(10px) scale(1)' },
  show: { opacity: 1, transform: REST_TRANSFORM, transition: TWEEN.enter },
}

const SECTION_VARIANTS = {
  hidden: { opacity: 0, transform: 'translateY(10px) scale(1)' },
  show: { opacity: 1, transform: REST_TRANSFORM, transition: { ...TWEEN.enter, staggerChildren: 0.03, delayChildren: 0.02 } },
}

const OPTION_HOVER = { transform: 'translateY(-2px) scale(1.02)', transition: HOVER_SPRING }
const OPTION_TAP = { transform: 'translateY(0px) scale(0.98)', transition: HOVER_SPRING }

const TITLE_MOTION = {
  initial: { opacity: 0, transform: 'translateX(-10px)' },
  animate: { opacity: 1, transform: 'translateX(0px)' },
}

const CLOSE_MOTION = {
  initial: { opacity: 0, transform: 'rotate(-90deg) scale(1)' },
  animate: { opacity: 1, transform: 'rotate(0deg) scale(1)' },
  whileHover: { transform: 'rotate(0deg) scale(1.1)', transition: HOVER_SPRING },
  whileTap: { transform: 'rotate(0deg) scale(0.9)', transition: HOVER_SPRING },
}

const ARTWORK_MOTION = {
  initial: { opacity: 0, transform: 'scale(1.1)' },
  animate: { opacity: 1, transform: 'scale(1)' },
}

const blurCache = new Map()
const decodedArtwork = new Map()
const BLUR_RADII = [18, 10, 5]
const BLUR_CACHE_LIMIT = 60
const ENTRANCE_MS = 650
const ARTWORK_DIM_OVERLAY = { backgroundColor: 'rgba(0, 0, 0, 0.7)' }

let noiseTileUrl = null

function getNoiseTileUrl() {
  if (noiseTileUrl) return noiseTileUrl
  const size = 128
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')
  const image = ctx.createImageData(size, size)
  for (let i = 0; i < image.data.length; i += 4) {
    image.data[i] = Math.random() * 255
    image.data[i + 1] = Math.random() * 255
    image.data[i + 2] = Math.random() * 255
    image.data[i + 3] = 128 + Math.random() * 127
  }
  ctx.putImageData(image, 0, 0)
  noiseTileUrl = `url(${canvas.toDataURL('image/png')})`
  return noiseTileUrl
}

function scheduleIdle(callback, delay) {
  let idleId = null
  const timeoutId = setTimeout(() => {
    if ('requestIdleCallback' in window) {
      idleId = window.requestIdleCallback(callback, { timeout: 1000 })
    } else {
      callback()
    }
  }, delay)
  return () => {
    clearTimeout(timeoutId)
    if (idleId !== null) window.cancelIdleCallback?.(idleId)
  }
}

function createBlurredImage(src, blurRadius) {
  const key = `${src}_${blurRadius}`
  if (blurCache.has(key)) return Promise.resolve(blurCache.get(key))

  return new Promise((resolve) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      const canvas = document.createElement('canvas')
      const size = 256
      canvas.width = size
      canvas.height = size
      const ctx = canvas.getContext('2d')
      ctx.filter = `blur(${blurRadius}px)`
      const scale = Math.max(size / img.width, size / img.height)
      const w = img.width * scale
      const h = img.height * scale
      ctx.drawImage(img, (size - w) / 2 - blurRadius, (size - h) / 2 - blurRadius, w + blurRadius * 2, h + blurRadius * 2)
      canvas.toBlob((blob) => {
        const url = blob ? URL.createObjectURL(blob) : canvas.toDataURL('image/jpeg', 0.8)
        blurCache.set(key, url)
        while (blurCache.size > BLUR_CACHE_LIMIT) {
          const [oldestKey, oldestUrl] = blurCache.entries().next().value
          blurCache.delete(oldestKey)
          if (oldestUrl.startsWith('blob:')) URL.revokeObjectURL(oldestUrl)
        }
        resolve(url)
      }, 'image/jpeg', 0.8)
    }
    img.onerror = () => resolve(null)
    img.src = src
  })
}

function decodeUrl(url) {
  if (!url) return Promise.resolve(null)
  const pending = decodedArtwork.get(url)
  if (pending) return pending.promise
  const img = new Image()
  img.decoding = 'async'
  img.src = url
  const entry = { img, ready: false, promise: null }
  const loaded = typeof img.decode === 'function'
    ? img.decode()
    : new Promise((resolve, reject) => {
      img.onload = resolve
      img.onerror = reject
    })
  entry.promise = loaded.then(() => {
    entry.ready = true
    return url
  }, () => {
    decodedArtwork.delete(url)
    return null
  })
  decodedArtwork.set(url, entry)
  return entry.promise
}

function isDecoded(url) {
  return !!url && decodedArtwork.get(url)?.ready === true
}

function getCachedBlurs(src) {
  if (!src) return null
  const [heavy, medium, light] = BLUR_RADII.map(radius => blurCache.get(`${src}_${radius}`))
  if (!heavy || !medium || !light || !isDecoded(heavy) || !isDecoded(medium) || !isDecoded(light)) return null
  return { heavy, medium, light }
}

async function createAllBlurs(src, betweenSteps) {
  const urls = []
  for (const radius of BLUR_RADII) {
    const url = await createBlurredImage(src, radius)
    await decodeUrl(url)
    urls.push(url)
    if (betweenSteps) await betweenSteps()
  }
  const [heavy, medium, light] = urls
  return { heavy, medium, light }
}

const nextIdle = () => new Promise(resolve => {
  if ('requestIdleCallback' in window) window.requestIdleCallback(() => resolve(), { timeout: 1500 })
  else setTimeout(resolve, 50)
})

export function prewarmModalAssets(artworkUrl) {
  if (!artworkUrl) return Promise.resolve(null)
  return decodeUrl(artworkUrl).then(() => createAllBlurs(artworkUrl, nextIdle))
}

const ModalBlurContext = createContext(null)

const BlurredArtworkBackground = memo(function BlurredArtworkBackground({ artworkUrl, categoryColor, gradientOpacity = 0.85, onBlurReady }) {
  const [loadedUrl, setLoadedUrl] = useState(() => (isDecoded(artworkUrl) ? artworkUrl : null))
  const mountedAtRef = useRef(0)
  const imageLoaded = !!artworkUrl && (loadedUrl === artworkUrl || isDecoded(artworkUrl))

  useEffect(() => {
    mountedAtRef.current = performance.now()
  }, [])

  useEffect(() => {
    if (!artworkUrl) {
      onBlurReady?.(null)
      return
    }

    let cancelled = false
    const cancels = []
    const afterEntrance = (callback) => {
      const remaining = Math.max(0, ENTRANCE_MS - (performance.now() - mountedAtRef.current))
      cancels.push(scheduleIdle(callback, remaining))
    }

    if (!isDecoded(artworkUrl)) {
      decodeUrl(artworkUrl).then((url) => {
        if (cancelled || !url) return
        afterEntrance(() => {
          if (!cancelled) setLoadedUrl(url)
        })
      })
    }

    const cachedBlurs = getCachedBlurs(artworkUrl)
    if (cachedBlurs) {
      onBlurReady?.(cachedBlurs)
    } else {
      afterEntrance(() => {
        createAllBlurs(artworkUrl).then((result) => {
          if (!cancelled) onBlurReady?.(result)
        })
      })
    }

    return () => {
      cancelled = true
      cancels.forEach(cancel => cancel())
    }
  }, [artworkUrl, onBlurReady])

  const gradientBase = (
    <div
      className="absolute inset-0"
      style={{
        background: `radial-gradient(ellipse at 50% 30%, ${categoryColor}40 0%, ${categoryColor}20 40%, transparent 80%)`,
      }}
    />
  )

  if (!artworkUrl || !imageLoaded) return gradientBase

  return (
    <>
      {gradientBase}
      <motion.div
        {...ARTWORK_MOTION}
        transition={MOTION.settle}
        className="absolute inset-0"
        style={{
          backgroundImage: `url(${artworkUrl})`,
          backgroundSize: 'cover',
          backgroundPosition: 'center',
          transform: 'scale(1.2)',
        }}
      >
        <div className="absolute inset-0" style={ARTWORK_DIM_OVERLAY} />
      </motion.div>

      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: gradientOpacity * 0.6 }}
        transition={MODAL_GRADIENT_TRANSITION}
        className="absolute inset-0"
        style={{
          background: `radial-gradient(ellipse at 50% 30%, ${categoryColor}40 0%, ${categoryColor}25 30%, ${categoryColor}15 50%, transparent 80%)`,
        }}
      />

      <div
        className="absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage: getNoiseTileUrl(),
        }}
      />
    </>
  )
})

const DialogEdge = memo(function DialogEdge({ categoryColor }) {
  return (
    <div
      className="absolute inset-0 rounded-2xl pointer-events-none"
      style={{
        border: `1px solid ${categoryColor}40`,
        boxShadow: `inset 0 1px 0 rgba(255,255,255,0.06), 0 0 24px ${categoryColor}14`,
      }}
    />
  )
})

export function Modal({
  isOpen,
  onClose,
  title,
  children,
  maxWidth = 'max-w-2xl',
  maxHeight = 'max-h-[85vh]',
  showCloseButton = true,
  closeOnBackdrop = true,
  categoryOverride = null,
  gradientOpacity = 0.85,
}) {
  const { engineState, radioState } = useUISelector(state => ({ engineState: state.engineState, radioState: state.radioState }))
  const { isShortViewport } = useViewport()
  const { getCategoryMetadata, getWhite, getBorder } = useDynamicTheme()
  const { registerOverlay, pauseRendering } = useQuality()
  const [isClosing, setIsClosing] = useState(false)

  const backdropInteraction = usePointerInteraction()
  const closeButtonInteraction = usePointerInteraction()
  const dialogRef = useRef(null)

  const currentTrack = engineState.currentTrack
  const artworkUrl = useArtwork(currentTrack?.id, currentTrack?.has_artwork)
  const [blurState, setBlurState] = useState(() => ({ url: artworkUrl, blurs: getCachedBlurs(artworkUrl) }))
  const blurs = blurState.url === artworkUrl ? (blurState.blurs || getCachedBlurs(artworkUrl)) : getCachedBlurs(artworkUrl)
  const setBlurs = useCallback((result) => {
    setBlurState(prev => (prev.url === artworkUrl && prev.blurs === result ? prev : { url: artworkUrl, blurs: result }))
  }, [artworkUrl])

  const activeCategory = categoryOverride || radioState.activeSeedMode || 'all'
  const categoryMeta = getCategoryMetadata(activeCategory)
  const categoryColor = categoryMeta?.color || '#6366f1'

  const handleClose = useCallback(() => {
    pauseRendering(MODAL_CLOSE_PAUSE_MS)
    setIsClosing(true)
  }, [pauseRendering])

  const handleExitComplete = useCallback(() => {
    if (!isClosing) return
    startTransition(() => {
      setIsClosing(false)
      onClose()
    })
  }, [isClosing, onClose])

  const deferredOpen = useDeferredValue(isOpen)
  const isShown = isOpen && deferredOpen
  const isVisible = isShown && !isClosing

  useEffect(() => {
    if (!isOpen) return
    pauseRendering(MODAL_OPEN_PAUSE_MS)
    return registerOverlay()
  }, [isOpen, registerOverlay, pauseRendering])

  useEffect(() => {
    if (!isVisible) return
    const handleEsc = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        handleClose()
      }
    }
    document.addEventListener('keydown', handleEsc)
    return () => document.removeEventListener('keydown', handleEsc)
  }, [isVisible, handleClose])

  useEffect(() => {
    if (!isShown) return
    const previouslyFocused = document.activeElement
    const frameId = requestAnimationFrame(() => {
      const node = dialogRef.current
      if (node && !node.contains(document.activeElement)) {
        node.focus({ preventScroll: true })
      }
    })
    return () => {
      cancelAnimationFrame(frameId)
      if (previouslyFocused instanceof HTMLElement && previouslyFocused.isConnected) {
        previouslyFocused.focus({ preventScroll: true })
      }
    }
  }, [isShown])

  const handleBackdropClose = (e) => {
    e?.preventDefault()
    e?.stopPropagation()
    if (!closeOnBackdrop || !backdropInteraction.shouldTrigger() || isClosing) return
    triggerHaptic('light')
    handleClose()
  }

  const handleCloseButtonClick = (e) => {
    e?.preventDefault()
    e?.stopPropagation()
    if (!closeButtonInteraction.shouldTrigger() || isClosing) return
    triggerHaptic('light')
    handleClose()
  }

  const stopAllEvents = (e) => {
    e?.stopPropagation()
  }

  return (
    <ModalBlurContext.Provider value={blurs}>
      <AnimatePresence onExitComplete={handleExitComplete}>
        {isVisible && (
        <motion.div
          key="modal"
          {...PRESETS.modalBackdrop}
          className={`fixed inset-0 z-[60] flex items-center justify-center ${isShortViewport ? '' : 'p-4'}`}
          style={isShortViewport ? MODAL_BACKDROP_SAFE_STYLE : MODAL_BACKDROP_STYLE}
          onPointerDown={(e) => {
            stopAllEvents(e)
            backdropInteraction.onPointerDown(e)
          }}
          onPointerMove={(e) => {
            stopAllEvents(e)
            backdropInteraction.onPointerMove(e)
          }}
          onPointerUp={(e) => {
            stopAllEvents(e)
            handleBackdropClose(e)
          }}
          onClick={stopAllEvents}
          onMouseDown={stopAllEvents}
          onMouseUp={stopAllEvents}
        >
          <motion.div
            {...DIALOG_MOTION}
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-label={typeof title === 'string' ? title : undefined}
            tabIndex={-1}
            className={`rounded-2xl shadow-2xl w-full ${maxWidth} ${isShortViewport ? 'max-h-full' : maxHeight} flex flex-col relative focus:outline-none`}
            style={{
              backgroundColor: 'rgba(0, 0, 0, 0.85)',
              border: `1px solid ${getBorder(0.3)}`,
              overflow: 'hidden',
            }}
            onPointerDown={stopAllEvents}
            onPointerMove={stopAllEvents}
            onPointerUp={stopAllEvents}
            onClick={stopAllEvents}
            onMouseDown={stopAllEvents}
            onMouseUp={stopAllEvents}
          >
            <BlurredArtworkBackground
              artworkUrl={artworkUrl}
              categoryColor={categoryColor}
              gradientOpacity={gradientOpacity}
              onBlurReady={setBlurs}
            />

            <DialogEdge categoryColor={categoryColor} />

            <div className="relative z-10 flex flex-col" style={{ height: '100%', minHeight: 0 }}>
              {(title || showCloseButton) && (
                <div
                  className={`flex items-center justify-between border-b flex-shrink-0 relative overflow-hidden ${isShortViewport ? 'px-4 py-2' : 'px-6 py-4'}`}
                  style={{
                    backgroundColor: 'rgba(0, 0, 0, 0.6)',
                    borderColor: getBorder(0.2)
                  }}
                >
                  {blurs?.heavy && (
                    <div
                      className="ui-layer-in absolute inset-0 pointer-events-none"
                      style={{
                        backgroundImage: `url(${blurs.heavy})`,
                        backgroundSize: '108%',
                        backgroundPosition: 'center 45%',
                        opacity: 0.12,
                      }}
                    />
                  )}
                  {title && (
                    <motion.div
                      {...TITLE_MOTION}
                      transition={MODAL_TITLE_TRANSITION}
                      className="text-lg md:text-xl font-bold relative z-10"
                      style={{ color: getWhite() }}
                    >
                      {title}
                    </motion.div>
                  )}
                  {showCloseButton && (
                    <motion.button
                      {...CLOSE_MOTION}
                      transition={MODAL_CLOSE_TRANSITION}
                      onPointerDown={closeButtonInteraction.onPointerDown}
                      onPointerMove={closeButtonInteraction.onPointerMove}
                      onPointerUp={handleCloseButtonClick}
                      aria-label="Close"
                      className="p-2 rounded-full transition-colors relative z-10"
                      style={{
                        backgroundColor: 'rgba(255,255,255,0.1)',
                        color: getWhite()
                      }}
                    >
                      <X size={20} />
                    </motion.button>
                  )}
                </div>
              )}

              <motion.div
                variants={CONTENT_VARIANTS}
                initial="hidden"
                animate="show"
                className="flex-1 overflow-y-auto"
                style={{
                  scrollbarWidth: 'thin',
                  scrollbarColor: `${categoryColor}60 transparent`,
                }}
              >
                <div
                  className="p-6 min-h-full relative overflow-hidden"
                  style={{
                    backgroundColor: 'rgba(0, 0, 0, 0.4)',
                  }}
                >
                  {blurs?.light && (
                    <div
                      className="ui-layer-in absolute inset-0 pointer-events-none"
                      style={{
                        backgroundImage: `url(${blurs.light})`,
                        backgroundSize: '102%',
                        backgroundPosition: 'center 55%',
                        opacity: 0.1,
                      }}
                    />
                  )}
                  <div className="relative z-10">
                    {children}
                  </div>
                </div>
              </motion.div>
            </div>
          </motion.div>
        </motion.div>
        )}
      </AnimatePresence>
    </ModalBlurContext.Provider>
  )
}

export const ModalTitle = memo(function ModalTitle({ title, subtitle, subtitleHighlight }) {
  const { getWhite, getGrey400 } = useDynamicTheme()

  return (
    <div>
      <div className="text-xl font-bold">{title}</div>
      {subtitle && (
        <p className="text-sm mt-1 font-normal" style={{ color: getGrey400() }}>
          {subtitle}
          {subtitleHighlight && (
            <span style={{ color: getWhite(), opacity: 0.7 }}>{subtitleHighlight}</span>
          )}
        </p>
      )}
    </div>
  )
})

export const ModalFooter = memo(function ModalFooter({ children, className = '' }) {
  const { getBorder } = useDynamicTheme()

  return (
    <motion.div
      variants={ITEM_VARIANTS}
      className={`flex items-center justify-between pt-4 border-t ${className}`}
      style={{ borderColor: getBorder(0.1) }}
    >
      {children}
    </motion.div>
  )
})

export const ModalButton = memo(function ModalButton({
  onClick,
  children,
  variant = 'primary',
  disabled = false,
  className = '',
  ...props
}) {
  const { radioState } = useUISelector(state => ({ radioState: state.radioState }))
  const { getCategoryMetadata, getWhite, getBorder } = useDynamicTheme()

  const activeCategory = radioState.activeSeedMode || 'all'
  const categoryMeta = getCategoryMetadata(activeCategory)
  const categoryColor = categoryMeta?.color || '#6366f1'

  const handleClick = (e) => {
    if (disabled) return
    triggerHaptic('medium')
    onClick?.(e)
  }

  const variantStyles = {
    primary: {
      backgroundColor: `${categoryColor}60`,
      border: `1px solid ${categoryColor}`,
      color: getWhite(),
      boxShadow: `0 2px 15px ${categoryColor}30`
    },
    secondary: {
      backgroundColor: 'rgba(0, 0, 0, 0.6)',
      border: `1px solid ${getBorder(0.5)}`,
      color: getWhite(),
    },
    danger: {
      backgroundColor: '#ef444460',
      border: '1px solid #ef4444',
      color: getWhite(),
      boxShadow: '0 2px 15px #ef444430'
    }
  }

  return (
    <motion.button
      whileHover={disabled ? undefined : PRESETS.softPress.whileHover}
      whileTap={disabled ? undefined : PRESETS.softPress.whileTap}
      onClick={handleClick}
      disabled={disabled}
      className={`px-5 py-2.5 rounded-xl font-medium transition-[background-color,border-color,color,box-shadow,opacity] ${disabled ? 'opacity-50 cursor-not-allowed' : ''} ${className}`}
      style={variantStyles[variant]}
      {...props}
    >
      {children}
    </motion.button>
  )
})

export const ModalSection = memo(function ModalSection({ title, children, className = '' }) {
  const { getWhite } = useDynamicTheme()

  return (
    <motion.div variants={SECTION_VARIANTS} className={`mb-6 ${className}`}>
      {title && (
        <h3
          className="text-sm font-bold uppercase tracking-wide mb-3"
          style={{
            color: getWhite(),
            opacity: 0.9,
            textShadow: '0 1px 3px rgba(0,0,0,0.8)'
          }}
        >
          {title}
        </h3>
      )}
      {children}
    </motion.div>
  )
})

export const ModalOptionButton = memo(function ModalOptionButton({
  onClick,
  icon: Icon,
  iconColor,
  iconBgColor,
  title,
  description,
  isSelected = false,
  isDisabled = false,
  isMobile = false,
  className = '',
  ...props
}) {
  const { getWhite, getGrey400, getBorder } = useDynamicTheme()
  const interaction = usePointerInteraction()
  const blurs = useContext(ModalBlurContext)

  const handleClick = (e) => {
    if (isDisabled || !interaction.shouldTrigger()) return
    triggerHaptic(isSelected ? 'light' : 'medium')
    onClick?.(e)
  }

  const resolvedIconBgColor = iconBgColor || `${iconColor}20`

  return (
    <motion.button
      variants={ITEM_VARIANTS}
      whileHover={isDisabled ? undefined : OPTION_HOVER}
      whileTap={isDisabled ? undefined : OPTION_TAP}
      onPointerDown={interaction.onPointerDown}
      onPointerMove={interaction.onPointerMove}
      onPointerUp={handleClick}
      disabled={isDisabled}
      className={`rounded-xl border transition-[background-color,border-color,box-shadow,opacity] duration-micro group relative overflow-hidden ${
        isMobile ? 'p-3 flex flex-col items-center justify-center text-center gap-2' : 'p-4 text-left'
      } ${isDisabled ? 'cursor-not-allowed opacity-30' : 'cursor-pointer'} ${className}`}
      style={{
        backgroundColor: isSelected ? `${iconColor}15` : 'rgba(255,255,255,0.03)',
        borderColor: isSelected ? iconColor : getBorder(0.1),
        boxShadow: isSelected ? `0 4px 20px ${iconColor}30` : 'none',
      }}
      {...props}
    >
      {blurs?.medium && (
        <div
          className="ui-layer-in absolute inset-0 pointer-events-none"
          style={{
            backgroundImage: `url(${blurs.medium})`,
            backgroundSize: '105%',
            backgroundPosition: 'center 50%',
            opacity: 0.11,
          }}
        />
      )}
      {isMobile ? (
        <>
          <div
            className="p-2 rounded-lg transition-transform duration-quick group-hover:scale-110 relative z-10"
            style={{ backgroundColor: resolvedIconBgColor }}
          >
            <Icon size={24} style={{ color: iconColor }} />
          </div>
          <div className="text-xs font-semibold relative z-10" style={{ color: getWhite() }}>
            {title}
          </div>
        </>
      ) : (
        <div className="flex items-start gap-3 relative z-10">
          <div
            className="p-2 rounded-lg transition-transform duration-quick group-hover:scale-110 flex-shrink-0"
            style={{ backgroundColor: resolvedIconBgColor }}
          >
            <Icon size={24} style={{ color: iconColor }} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="font-semibold mb-1" style={{ color: getWhite() }}>
              {title}
            </div>
            {description && (
              <div className="text-sm" style={{ color: getGrey400() }}>
                {description}
              </div>
            )}
          </div>
          <div
            className="w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 transition-[transform,opacity] duration-micro"
            style={{
              backgroundColor: iconColor,
              transform: isSelected ? 'scale(1)' : 'scale(0)',
              opacity: isSelected ? 1 : 0,
            }}
          >
            <span className="text-white text-sm">✓</span>
          </div>
        </div>
      )}
    </motion.button>
  )
})

export const ModalCard = memo(function ModalCard({
  children,
  className = '',
  background = 'rgba(255,255,255,0.03)',
  ...props
}) {

  return (
    <div
      className={`p-3 rounded-lg ${className}`}
      style={{ backgroundColor: background, ...props.style }}
      {...props}
    >
      {children}
    </div>
  )
})

export const ModalMetadataField = memo(function ModalMetadataField({
  icon: Icon,
  label,
  value,
  extraLabel,
  span = 1,
  className = ''
}) {
  const { getWhite, getGrey400 } = useDynamicTheme()

  if (!value) return null

  return (
    <ModalCard className={`${span > 1 ? `col-span-${span}` : ''} ${className}`}>
      <div
        className="text-xs font-semibold uppercase tracking-wide mb-1 flex items-center gap-1.5"
        style={{ color: getGrey400() }}
      >
        {Icon && <Icon size={12} />}
        {label}
        {extraLabel}
      </div>
      <p className="text-sm font-medium" style={{ color: getWhite() }}>
        {value}
      </p>
    </ModalCard>
  )
})

export const ModalProgress = memo(function ModalProgress({
  progress = 0,
  statusText = '',
  showSpinner = true,
  showCancel = false,
  onCancel,
  className = ''
}) {
  const { radioState } = useUISelector(state => ({ radioState: state.radioState }))
  const { getCategoryMetadata, getWhite, getGrey400 } = useDynamicTheme()

  const activeCategory = radioState.activeSeedMode || 'all'
  const categoryMeta = getCategoryMetadata(activeCategory)
  const categoryColor = categoryMeta?.color || '#6366f1'

  const percentage = Math.round(progress * 100)

  return (
    <div className={`flex flex-col items-center ${className}`}>
      {showSpinner && (
        <Loader2
          size={48}
          className="animate-spin mb-4"
          style={{ color: categoryColor }}
        />
      )}

      {statusText && (
        <p className="text-lg font-medium mb-4" style={{ color: getWhite() }}>
          {statusText}
        </p>
      )}

      <div className="w-full bg-gray-700 rounded-full h-2 overflow-hidden">
        <motion.div
          className="h-full rounded-full"
          style={{ backgroundColor: categoryColor }}
          initial={{ width: 0 }}
          animate={{ width: `${percentage}%` }}
          transition={MOTION.progress}
        />
      </div>

      <p className="mt-2 text-xs" style={{ color: getGrey400() }}>
        {percentage}%
      </p>

      {showCancel && onCancel && (
        <motion.button
          whileHover={PRESETS.softPress.whileHover}
          whileTap={PRESETS.softPress.whileTap}
          onClick={() => {
            triggerHaptic('light')
            onCancel()
          }}
          className="mt-4 flex items-center gap-2 px-4 py-2 rounded-lg transition-colors"
          style={{ backgroundColor: 'rgba(255,255,255,0.1)', color: getGrey400() }}
        >
          <X size={16} />
          Cancel
        </motion.button>
      )}
    </div>
  )
})

export const ModalErrorState = memo(function ModalErrorState({
  title = 'Something went wrong',
  message,
  onRetry,
  retryText = 'Try Again',
  className = ''
}) {
  const { getWhite, getGrey400 } = useDynamicTheme()

  return (
    <div className={`flex flex-col items-center text-center ${className}`}>
      <div
        className="p-4 rounded-full mb-4"
        style={{ backgroundColor: 'rgba(239, 68, 68, 0.1)' }}
      >
        <AlertCircle size={48} className="text-red-400" />
      </div>

      <h3 className="text-xl font-bold mb-2" style={{ color: getWhite() }}>
        {title}
      </h3>

      {message && (
        <p className="text-sm mb-6" style={{ color: getGrey400() }}>
          {message}
        </p>
      )}

      {onRetry && (
        <ModalButton onClick={onRetry} variant="primary">
          {retryText}
        </ModalButton>
      )}
    </div>
  )
})

export const ModalSuccessBanner = memo(function ModalSuccessBanner({
  message,
  className = ''
}) {
  if (!message) return null

  return (
    <div
      className={`flex items-center gap-2 p-3 rounded-lg ${className}`}
      style={{
        backgroundColor: 'rgba(34, 197, 94, 0.1)',
        border: '1px solid rgba(34, 197, 94, 0.3)'
      }}
    >
      <Check size={20} className="text-green-400 flex-shrink-0" />
      <p className="text-green-300 text-sm">{message}</p>
    </div>
  )
})

export const ModalTagList = memo(function ModalTagList({
  tags,
  color,
  emptyText = 'None',
  className = ''
}) {
  const { getGrey400 } = useDynamicTheme()

  if (!tags || tags.length === 0) {
    return (
      <span className="text-sm" style={{ color: getGrey400() }}>
        {emptyText}
      </span>
    )
  }

  return (
    <div className={`flex flex-wrap gap-1 ${className}`}>
      {tags.map((tag, i) => (
        <span
          key={i}
          className="px-2 py-0.5 rounded-full text-xs"
          style={{ backgroundColor: `${color}20`, color }}
        >
          {tag}
        </span>
      ))}
    </div>
  )
})

export default Modal