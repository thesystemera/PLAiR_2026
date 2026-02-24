import { forwardRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { CheckCircle, XCircle, AlertCircle, X } from 'lucide-react'
import { useUIState } from '../contexts/UIStateContext'

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
  const { removeToast } = useUIState()
  const config = toastConfig[type] || toastConfig.info
  const Icon = config.icon

  const isBottom = position === 'bottom'

  return (
    <motion.div
      ref={ref}
      initial={{ opacity: 0, y: isBottom ? 50 : -50, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: isBottom ? 50 : -50, scale: 0.95 }}
      transition={{ duration: 0.3, type: 'spring', stiffness: 300, damping: 25 }}
      className={`${config.bgColor} ${config.borderColor} border-l-4 rounded-lg shadow-2xl p-4 mb-3 flex items-start gap-3 min-w-[300px] md:min-w-[320px] max-w-[90vw] md:max-w-[480px]`}
    >
      <Icon className={`${config.iconColor} flex-shrink-0 mt-0.5`} size={20} />

      <p className="text-white text-sm flex-1 leading-relaxed">
        {message}
      </p>

      <button
        onClick={() => removeToast(id)}
        className="text-white/70 hover:text-white transition-colors flex-shrink-0"
        aria-label="Close"
      >
        <X size={18} />
      </button>
    </motion.div>
  )
})

Toast.displayName = 'Toast'

export default function ToastContainer() {
  const { toasts } = useUIState()

  const topToasts = toasts.filter(t => t.position === 'top')
  const bottomToasts = toasts.filter(t => t.position === 'bottom')

  return (
    <>
      <div className="fixed top-4 left-1/2 -translate-x-1/2 z-[100] pointer-events-none">
        <div className="pointer-events-auto">
          <AnimatePresence mode="popLayout">
            {topToasts.map(toast => (
              <Toast key={toast.id} {...toast} />
            ))}
          </AnimatePresence>
        </div>
      </div>

      <div className="fixed bottom-24 md:bottom-28 left-1/2 -translate-x-1/2 z-[100] pointer-events-none">
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