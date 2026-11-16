import { X, Loader2, Check, AlertCircle } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { startTransition, useDeferredValue, useEffect, useState, useRef, memo, useCallback } from 'react'
import { triggerHaptic } from '../../lib/haptics'
import { useDynamicTheme } from '../../contexts/DynamicThemeContext'
import { PACK_SIZES, blobForUrl, packCache } from '../../lib/mediaCache'
import { packBlurUrls } from '../../lib/packImage'
import { usePointerInteraction } from '../../hooks/usePointerInteraction'
import { useUISelector } from '../../contexts/UIStateContext'
import { DURATION, MOTION, PRESETS, SPRING, TWEEN } from '../../lib/motion'
import { useViewport } from '../../contexts/ViewportContext'
import { useQuality } from '../../contexts/QualityContext'
import { MODAL_CLOSE_PAUSE_MS, MODAL_OPEN_PAUSE_MS } from '../../lib/renderPause'
import { Pop } from '../Motion'
import { CardIconButton, OptionCard } from '../PlayerSheet'

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

const MODAL_TITLE_TRANSITION = { ...PRESETS.fadeSlide.animate.transition, delay: 0.08 }
const MODAL_CLOSE_TRANSITION = { ...PRESETS.fadeSlide.animate.transition, delay: 0.12 }

const FOOTER_SURFACE = { backgroundColor: 'rgba(10, 10, 12, 0.94)' }
const DIALOG_SURFACE = { backgroundColor: 'rgba(10, 10, 12, 0.88)', border: '1px solid rgba(255, 255, 255, 0.1)', overflow: 'hidden' }
const REST_TRANSFORM = 'translateY(0px) scale(1)'

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


const TITLE_MOTION = {
  initial: { opacity: 0, transform: 'translateX(-10px)' },
  animate: { opacity: 1, transform: 'translateX(0px)' },
}

const CLOSE_MOTION = {
  initial: { opacity: 0, transform: 'rotate(-90deg)' },
  animate: { opacity: 1, transform: 'rotate(0deg)' },
}

const blurCache = new Map()
const decodedArtwork = new Map()
const BLUR_RADIUS = 18
const BLUR_CACHE_LIMIT = 30
const COVER_OPACITY = 0.5

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

function getCachedBlur(trackId) {
  const url = trackId ? blurCache.get(trackId) : null
  return isDecoded(url) ? url : null
}

async function createBlur(trackId) {
  const cached = getCachedBlur(trackId)
  if (cached) return cached
  const packUrl = await packCache(PACK_SIZES[0]).getMedia(trackId)
  const blob = packUrl ? blobForUrl(packUrl) : null
  if (!blob) return null
  const [url] = await packBlurUrls(blob, [BLUR_RADIUS])
  await decodeUrl(url)
  blurCache.set(trackId, url)
  while (blurCache.size > BLUR_CACHE_LIMIT) {
    const [oldestKey, oldestUrl] = blurCache.entries().next().value
    blurCache.delete(oldestKey)
    decodedArtwork.delete(oldestUrl)
    URL.revokeObjectURL(oldestUrl)
  }
  return url
}

export function prewarmModalAssets(trackId) {
  if (!trackId) return Promise.resolve(null)
  return createBlur(trackId)
}

const BlurredArtworkBackground = memo(function BlurredArtworkBackground({ trackId, categoryColor }) {
  const [arrived, setArrived] = useState({ trackId: null, url: null })
  const url = getCachedBlur(trackId) || (arrived.trackId === trackId ? arrived.url : null)
  const late = !!url && arrived.trackId === trackId && arrived.url === url

  useEffect(() => {
    if (!trackId || getCachedBlur(trackId)) return
    let cancelled = false
    createBlur(trackId).then((result) => {
      if (!cancelled) setArrived({ trackId, url: result })
    }, () => {})
    return () => {
      cancelled = true
    }
  }, [trackId])

  if (!url) {
    return (
      <div
        className="absolute inset-0"
        style={{
          background: `radial-gradient(ellipse at 50% 30%, ${categoryColor}40 0%, ${categoryColor}20 40%, transparent 80%)`,
        }}
      />
    )
  }

  return (
    <div
      key={url}
      className={`absolute inset-0 pointer-events-none${late ? ' ui-layer-in' : ''}`}
      style={{
        backgroundImage: `url(${url})`,
        backgroundSize: 'cover',
        backgroundPosition: 'center',
        opacity: COVER_OPACITY,
      }}
    />
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
}) {
  const { currentTrack, activeSeedMode } = useUISelector(state => ({ currentTrack: state.engineState.currentTrack, activeSeedMode: state.radioState.activeSeedMode }))
  const { isShortViewport } = useViewport()
  const { getCategoryMetadata } = useDynamicTheme()
  const { pauseRendering } = useQuality()
  const [isClosing, setIsClosing] = useState(false)

  const backdropInteraction = usePointerInteraction()
  const dialogRef = useRef(null)

  const artTrackId = currentTrack?.has_artwork !== false ? currentTrack?.id || null : null

  const activeCategory = categoryOverride || activeSeedMode || 'all'
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
  }, [isOpen, pauseRendering])

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

  const handleCloseButtonClick = () => {
    if (isClosing) return
    triggerHaptic('light')
    handleClose()
  }

  const stopAllEvents = (e) => {
    e?.stopPropagation()
  }

  return (
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
          style={DIALOG_SURFACE}
          onPointerDown={stopAllEvents}
          onPointerMove={stopAllEvents}
          onPointerUp={stopAllEvents}
          onClick={stopAllEvents}
          onMouseDown={stopAllEvents}
          onMouseUp={stopAllEvents}
        >
          <BlurredArtworkBackground
            trackId={artTrackId}
            categoryColor={categoryColor}
          />

          <DialogEdge categoryColor={categoryColor} />

          <div className="relative z-10 flex flex-col" style={{ height: '100%', minHeight: 0 }}>
            {(title || showCloseButton) && (
              <div className={`flex items-start justify-between gap-3 flex-shrink-0 ${isShortViewport ? 'px-4 pt-3 pb-2' : 'px-5 pt-5 pb-3'}`}>
                {title && (
                  <motion.div
                    {...TITLE_MOTION}
                    transition={MODAL_TITLE_TRANSITION}
                    className="flex-1 min-w-0 text-lg font-semibold text-white"
                  >
                    {title}
                  </motion.div>
                )}
                {showCloseButton && (
                  <motion.div {...CLOSE_MOTION} transition={MODAL_CLOSE_TRANSITION} className="flex -mr-1.5 -mt-0.5">
                    <CardIconButton icon={X} size={20} onClick={handleCloseButtonClick} title="Close" />
                  </motion.div>
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
              <div className={`relative ${isShortViewport ? 'px-4 pb-4' : 'px-5 pb-5'} ${title || showCloseButton ? 'pt-1' : isShortViewport ? 'pt-4' : 'pt-5'}`}>
                {children}
              </div>
            </motion.div>
          </div>
        </motion.div>
      </motion.div>
      )}
    </AnimatePresence>
  )
}

export const ModalTitle = memo(function ModalTitle({ title, subtitle, subtitleHighlight }) {
  return (
    <div className="min-w-0">
      <div className="text-lg font-semibold leading-tight text-white">{title}</div>
      {(subtitle || subtitleHighlight) && (
        <p className="mt-1 text-sm font-normal text-white/55 line-clamp-2">
          {subtitle}
          {subtitleHighlight && <span className="text-white/80">{subtitleHighlight}</span>}
        </p>
      )}
    </div>
  )
})

export const ModalFooter = memo(function ModalFooter({ children, className = '' }) {
  const { isShortViewport } = useViewport()
  const edge = isShortViewport ? '-mx-4 -mb-4 px-4 pb-4' : '-mx-5 -mb-5 px-5 pb-5'

  return (
    <motion.div
      variants={ITEM_VARIANTS}
      className={`sticky bottom-0 z-10 mt-5 flex items-center justify-between gap-3 border-t border-white/10 pt-3 ${edge} ${className}`}
      style={FOOTER_SURFACE}
    >
      {children}
    </motion.div>
  )
})

const MODAL_BUTTONS = {
  primary: 'text-white border border-[color-mix(in_srgb,var(--theme-accent)_70%,transparent)] bg-[color-mix(in_srgb,var(--theme-accent)_40%,transparent)] hover:bg-[color-mix(in_srgb,var(--theme-accent)_55%,transparent)]',
  secondary: 'text-white border border-white/15 bg-white/10 hover:bg-white/15',
  danger: 'text-white border border-red-500/70 bg-red-500/35 hover:bg-red-500/50',
}

export const ModalButton = memo(function ModalButton({
  onClick,
  children,
  variant = 'primary',
  disabled = false,
  className = '',
  ...props
}) {
  const handleClick = (e) => {
    if (disabled) return
    triggerHaptic('medium')
    onClick?.(e)
  }

  return (
    <button
      onClick={handleClick}
      disabled={disabled}
      className={`ui-press px-5 py-2.5 rounded-xl font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${MODAL_BUTTONS[variant] || MODAL_BUTTONS.primary} ${className}`}
      {...props}
    >
      {children}
    </button>
  )
})

export const ModalSection = memo(function ModalSection({ title, children, className = '' }) {
  return (
    <motion.div variants={SECTION_VARIANTS} className={`mb-5 last:mb-0 ${className}`}>
      {title && (
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-white/55">
          {title}
        </h3>
      )}
      {children}
    </motion.div>
  )
})

export const ModalOptionButton = memo(function ModalOptionButton({
  onClick,
  icon,
  iconColor,
  title,
  description,
  isSelected = false,
  isDisabled = false,
  isMobile = false,
  toggle = false,
}) {
  const handleSelect = (e) => {
    triggerHaptic(isSelected ? 'light' : 'medium')
    onClick?.(e)
  }

  return (
    <motion.div variants={ITEM_VARIANTS}>
      <OptionCard
        icon={icon}
        tint={iconColor}
        title={title}
        subtitle={isMobile ? null : description}
        selected={isSelected}
        disabled={isDisabled}
        toggle={toggle}
        onSelect={handleSelect}
        badge={toggle ? <ToggleMark on={isSelected} /> : null}
      />
    </motion.div>
  )
})

function ToggleMark({ on }) {
  return (
    <Pop show={on} className="flex flex-shrink-0">
      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-[var(--theme-accent)] text-black">
        <Check size={13} strokeWidth={3} />
      </span>
    </Pop>
  )
}

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
  const { activeSeedMode } = useUISelector(state => ({ activeSeedMode: state.radioState.activeSeedMode }))
  const { getCategoryMetadata, getWhite, getGrey400 } = useDynamicTheme()

  const activeCategory = activeSeedMode || 'all'
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

      <Pop show={showCancel && !!onCancel} className="mt-4">
        <motion.button
          whileHover={PRESETS.softPress.whileHover}
          whileTap={PRESETS.softPress.whileTap}
          onClick={() => {
            triggerHaptic('light')
            onCancel()
          }}
          className="flex items-center gap-2 px-4 py-2 rounded-lg transition-colors"
          style={{ backgroundColor: 'rgba(255,255,255,0.1)', color: getGrey400() }}
        >
          <X size={16} />
          Cancel
        </motion.button>
      </Pop>
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
