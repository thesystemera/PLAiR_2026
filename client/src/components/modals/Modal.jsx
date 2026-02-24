import { X, Loader2, Check, AlertCircle } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { useEffect, useState, useRef, memo, useCallback, createContext, useContext } from 'react'
import { triggerHaptic } from '../../lib/haptics'
import { useDynamicTheme } from '../../contexts/DynamicThemeContext'
import { usePointerInteraction } from '../../hooks/usePointerInteraction'
import { useUIState, useArtwork } from '../../contexts/UIStateContext'

const blurCache = new Map()

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
      const url = canvas.toDataURL('image/jpeg', 0.8)
      blurCache.set(key, url)
      resolve(url)
    }
    img.onerror = () => resolve(null)
    img.src = src
  })
}

function createAllBlurs(src) {
  return Promise.all([
    createBlurredImage(src, 18),
    createBlurredImage(src, 10),
    createBlurredImage(src, 5),
  ]).then(([heavy, medium, light]) => ({ heavy, medium, light }))
}

const ModalBlurContext = createContext(null)

const BlurredArtworkBackground = memo(function BlurredArtworkBackground({ trackId, hasArtwork, categoryColor, gradientOpacity = 0.85, onBlurReady }) {
  const artworkUrl = useArtwork(trackId, hasArtwork)
  const [imageLoaded, setImageLoaded] = useState(false)
  const imageRef = useRef(null)

  useEffect(() => {
    if (!artworkUrl) {
      queueMicrotask(() => setImageLoaded(false))
      onBlurReady?.(null)
      return
    }

    const img = new Image()
    img.onload = () => setImageLoaded(true)
    img.onerror = () => setImageLoaded(false)
    img.src = artworkUrl
    imageRef.current = img

    createAllBlurs(artworkUrl).then(onBlurReady)

    return () => {
      if (imageRef.current) {
        imageRef.current.onload = null
        imageRef.current.onerror = null
      }
    }
  }, [artworkUrl, onBlurReady])

  if (!artworkUrl || !imageLoaded) {
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
    <>
      <motion.div
        initial={{ opacity: 0, scale: 1.1 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.6, ease: 'easeOut' }}
        className="absolute inset-0"
        style={{
          backgroundImage: `url(${artworkUrl})`,
          backgroundSize: 'cover',
          backgroundPosition: 'center',
          filter: 'brightness(0.3)',
          transform: 'scale(1.2)',
        }}
      />

      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: gradientOpacity * 0.6 }}
        transition={{ duration: 0.5, delay: 0.1 }}
        className="absolute inset-0"
        style={{
          background: `radial-gradient(ellipse at 50% 30%, ${categoryColor}40 0%, ${categoryColor}25 30%, ${categoryColor}15 50%, transparent 80%)`,
        }}
      />

      <div
        className="absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage: 'url("data:image/svg+xml,%3Csvg viewBox=\'0 0 400 400\' xmlns=\'http://www.w3.org/2000/svg\'%3E%3Cfilter id=\'noiseFilter\'%3E%3CfeTurbulence type=\'fractalNoise\' baseFrequency=\'0.9\' numOctaves=\'4\' stitchTiles=\'stitch\'/%3E%3C/filter%3E%3Crect width=\'100%25\' height=\'100%25\' filter=\'url(%23noiseFilter)\'/%3E%3C/svg%3E")',
        }}
      />
    </>
  )
})

const AnimatedBorder = memo(function AnimatedBorder({ categoryColor }) {
  return (
    <motion.div
      className="absolute inset-0 rounded-2xl pointer-events-none"
      initial={{ opacity: 0.3 }}
      animate={{ opacity: [0.3, 0.6, 0.3] }}
      transition={{ duration: 3, repeat: Infinity, ease: 'easeInOut' }}
      style={{
        border: `1px solid ${categoryColor}60`,
        boxShadow: `0 0 30px ${categoryColor}20, inset 0 0 30px ${categoryColor}10`,
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
  const { engineState, radioState } = useUIState()
  const { getCategoryMetadata, getWhite, getBorder } = useDynamicTheme()
  const [isClosing, setIsClosing] = useState(false)
  const [blurs, setBlurs] = useState(null)

  const backdropInteraction = usePointerInteraction()
  const closeButtonInteraction = usePointerInteraction()

  const currentTrack = engineState.currentTrack
  const trackId = currentTrack?.id
  const hasArtwork = currentTrack?.has_artwork

  const activeCategory = categoryOverride || radioState.activeSeedMode || 'all'
  const categoryMeta = getCategoryMetadata(activeCategory)
  const categoryColor = categoryMeta?.color || '#6366f1'

  const handleClose = useCallback(() => {
    setIsClosing(true)
    setTimeout(() => {
      setIsClosing(false)
      onClose()
    }, 300)
  }, [onClose])

  useEffect(() => {
    if (!isOpen) return
    const handleEsc = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        handleClose()
      }
    }
    document.addEventListener('keydown', handleEsc)
    return () => document.removeEventListener('keydown', handleEsc)
  }, [isOpen, handleClose])

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
    e?.preventDefault()
    e?.stopPropagation()
    e?.nativeEvent?.stopImmediatePropagation()
  }

  if (!isOpen && !isClosing) return null

  return (
    <ModalBlurContext.Provider value={blurs}>
      <AnimatePresence>
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
          className="fixed inset-0 z-[60] flex items-center justify-center p-4"
          style={{
            backgroundColor: 'rgba(0,0,0,0.75)',
          }}
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
            initial={{ opacity: 0, scale: 0.92, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.92, y: 20 }}
            transition={{
              duration: 0.3,
              ease: [0.4, 0, 0.2, 1]
            }}
            className={`rounded-2xl shadow-2xl w-full ${maxWidth} ${maxHeight} flex flex-col relative`}
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
              trackId={trackId}
              hasArtwork={hasArtwork}
              categoryColor={categoryColor}
              gradientOpacity={gradientOpacity}
              onBlurReady={setBlurs}
            />

            <AnimatedBorder categoryColor={categoryColor} />

            <div className="relative z-10 flex flex-col" style={{ height: '100%', minHeight: 0 }}>
              {(title || showCloseButton) && (
                <div
                  className="flex items-center justify-between px-6 py-4 border-b flex-shrink-0 relative overflow-hidden"
                  style={{
                    backgroundColor: 'rgba(0, 0, 0, 0.6)',
                    borderColor: getBorder(0.2)
                  }}
                >
                  {blurs?.heavy && (
                    <div
                      className="absolute inset-0 pointer-events-none"
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
                      initial={{ opacity: 0, x: -10 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: 0.1 }}
                      className="text-xl md:text-2xl font-bold relative z-10"
                      style={{ color: getWhite() }}
                    >
                      {title}
                    </motion.div>
                  )}
                  {showCloseButton && (
                    <motion.button
                      initial={{ opacity: 0, rotate: -90 }}
                      animate={{ opacity: 1, rotate: 0 }}
                      transition={{ delay: 0.15 }}
                      whileHover={{ scale: 1.1 }}
                      whileTap={{ scale: 0.9 }}
                      onPointerDown={closeButtonInteraction.onPointerDown}
                      onPointerMove={closeButtonInteraction.onPointerMove}
                      onPointerUp={handleCloseButtonClick}
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
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.2 }}
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
                      className="absolute inset-0 pointer-events-none"
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
      </AnimatePresence>
    </ModalBlurContext.Provider>
  )
}

export const ModalTitle = memo(function ModalTitle({ title, subtitle, subtitleHighlight }) {
  const { getWhite, getGrey400 } = useDynamicTheme()

  return (
    <div>
      <div className="text-2xl font-bold">{title}</div>
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
    <div
      className={`flex items-center justify-between pt-4 border-t ${className}`}
      style={{ borderColor: getBorder(0.1) }}
    >
      {children}
    </div>
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
  const { radioState } = useUIState()
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
      whileHover={{ scale: disabled ? 1 : 1.02 }}
      whileTap={{ scale: disabled ? 1 : 0.98 }}
      onClick={handleClick}
      disabled={disabled}
      className={`px-6 py-3 rounded-lg font-medium transition-all ${disabled ? 'opacity-50 cursor-not-allowed' : ''} ${className}`}
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
    <div className={`mb-6 ${className}`}>
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
    </div>
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
      whileHover={isDisabled ? {} : { scale: 1.02, y: -2 }}
      whileTap={isDisabled ? {} : { scale: 0.98 }}
      onPointerDown={interaction.onPointerDown}
      onPointerMove={interaction.onPointerMove}
      onPointerUp={handleClick}
      disabled={isDisabled}
      className={`rounded-xl border transition-all duration-150 group relative overflow-hidden ${
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
          className="absolute inset-0 pointer-events-none"
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
            className="p-2 rounded-lg transition-transform duration-200 group-hover:scale-110 relative z-10"
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
            className="p-2 rounded-lg transition-transform duration-200 group-hover:scale-110 flex-shrink-0"
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
            className="w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 transition-all duration-150"
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
  const { radioState } = useUIState()
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
          transition={{ duration: 0.3, ease: 'easeOut' }}
        />
      </div>

      <p className="mt-2 text-xs" style={{ color: getGrey400() }}>
        {percentage}%
      </p>

      {showCancel && onCancel && (
        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
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