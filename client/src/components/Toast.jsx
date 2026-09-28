import { forwardRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { CheckCircle, XCircle, AlertCircle, X } from 'lucide-react'
import { useUISelector } from '../contexts/UIStateContext'
import { useViewport } from '../contexts/ViewportContext'
import { SPRING, toastMotion } from '../lib/motion'

const TOAST_MOTION = { top: toastMotion(false), bottom: toastMotion(true) }

const toastConfig = {
  success: {
    icon: CheckCircle,
    bgColor: 'bg-green-600',
    borderColor: 'border-green-500',
    iconColor: 'text-green-100'
  },
  error: {
    icon: XCircle,
    bgColor: 'bg-red-600',
    borderColor: 'border-red-500',
    iconColor: 'text-red-100'
  },
  warning: {
    icon: AlertCircle,
    bgColor: 'bg-yellow-600',
    borderColor: 'border-yellow-500',
    iconColor: 'text-yellow-100'
  },
  info: {
    icon: AlertCircle,
    bgColor: 'bg-yellow-600',
    borderColor: 'border-yellow-500',
    iconColor: 'text-yellow-100'
  }
}

const Toast = forwardRef(({ id, message, type, duration: _duration, position }, ref) => {
  const { removeToast } = useUISelector(state => ({ removeToast: state.removeToast }))
  const config = toastConfig[type] || toastConfig.info
  const Icon = config.icon

  const toastPreset = position === 'bottom' ? TOAST_MOTION.bottom : TOAST_MOTION.top

  return (
    <motion.div
      ref={ref}
      layout="position"
      {...toastPreset}
      transition={SPRING.toast}
      className={`${config.bgColor} ${config.borderColor} border-l-4 rounded-lg shadow-2xl p-4 mb-3 flex items-start gap-3 min-w-[min(300px,90vw)] md:min-w-[320px] max-w-[90vw] md:max-w-[480px]`}
    >
      <Icon className={`${config.iconColor} flex-shrink-0 mt-0.5`} size={20} />

      <p className="text-white text-sm flex-1 leading-relaxed">
        {message}
      </p>

      <button
        onClick={() => removeToast(id)}
        className="ui-press text-white/70 hover:text-white transition-colors flex-shrink-0"
        aria-label="Close"
      >
        <X size={18} />
      </button>
    </motion.div>
  )
})

Toast.displayName = 'Toast'

export default function ToastContainer() {
  const {
    toasts,
    interfaceState,
    audioState,
  } = useUISelector(state => ({
    toasts: state.toasts,
    interfaceState: state.interfaceState,
    audioState: state.audioState,
  }))
  const { isMobile, isPhoneLandscape } = useViewport()
  const bottomOffset = interfaceState.playerHeight + (isMobile && !isPhoneLandscape ? 64 : 0) + 12

  const topToasts = toasts.filter(t => t.position === 'top')
  const bottomToasts = toasts.filter(t => t.position === 'bottom')

  return (
    <>
      <div className="fixed left-1/2 -translate-x-1/2 z-[100] pointer-events-none" style={{ top: audioState.offlineMode && !interfaceState.isFullscreenVisuals ? 'calc(var(--safe-top) + 3rem)' : 'calc(var(--safe-top) + 1rem)' }}>
        <div className="pointer-events-auto">
          <AnimatePresence mode="popLayout">
            {topToasts.map(toast => (
              <Toast key={toast.id} {...toast} />
            ))}
          </AnimatePresence>
        </div>
      </div>

      <div className="fixed left-1/2 -translate-x-1/2 z-[100] pointer-events-none" style={{ bottom: `${bottomOffset}px` }}>
        <div className="pointer-events-auto">
          <AnimatePresence mode="popLayout">
            {bottomToasts.map(toast => (
              <Toast key={toast.id} {...toast} />
            ))}
          </AnimatePresence>
        </div>
      </div>
    </>
  )
}