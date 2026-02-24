import { useState, useRef, useEffect } from 'react'
import { useDynamicTheme } from '../../contexts/DynamicThemeContext'
import Modal, { ModalButton } from './Modal'

export function ConfirmationModal({ isOpen, config }) {
  const { getWhite, getBorder } = useDynamicTheme()
  const [inputValue, setInputValue] = useState(config?.defaultValue || '')
  const inputRef = useRef(null)

  useEffect(() => {
    if (config?.mode === 'prompt') {
      queueMicrotask(() => setInputValue(config.defaultValue || ''))
    }
  }, [config])

  useEffect(() => {
    if (config?.mode === 'prompt' && isOpen && inputRef.current) {
      setTimeout(() => {
        inputRef.current?.focus()
      }, 300)
    }
  }, [isOpen, config?.mode])

  if (!config) return null

  const handleConfirm = () => {
    if (config.mode === 'prompt') {
      config.onConfirm?.(inputValue)
    } else {
      config.onConfirm?.()
    }
  }

  const handleCancel = () => {
    config.onCancel?.()
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && config.mode === 'prompt') {
      e.preventDefault()
      handleConfirm()
    }
  }

  const categoryOverride = config.variant === 'danger' ? null : undefined

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleCancel}
      title={config.title}
      maxWidth="max-w-md"
      categoryOverride={categoryOverride}
      closeOnBackdrop={config.mode !== 'alert'}
      showCloseButton={config.mode !== 'alert'}
    >
      <div className="mb-6">
        <p
          className="text-base leading-relaxed"
          style={{ color: getWhite(), opacity: 0.9 }}
        >
          {config.message}
        </p>
      </div>

      {config.mode === 'prompt' && (
        <div className="mb-6">
          <input
            ref={inputRef}
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={config.placeholder}
            className="w-full px-4 py-3 rounded-lg font-medium transition-all outline-none"
            style={{
              backgroundColor: 'rgba(255,255,255,0.05)',
              border: `1px solid ${getBorder(0.2)}`,
              color: getWhite(),
            }}
            onFocus={(e) => {
              e.target.style.borderColor = getBorder(0.4)
              e.target.style.backgroundColor = 'rgba(255,255,255,0.08)'
            }}
            onBlur={(e) => {
              e.target.style.borderColor = getBorder(0.2)
              e.target.style.backgroundColor = 'rgba(255,255,255,0.05)'
            }}
          />
        </div>
      )}

      <div className="flex items-center justify-end gap-3">
        {config.cancelText && (
          <ModalButton
            onClick={handleCancel}
            variant="secondary"
          >
            {config.cancelText}
          </ModalButton>
        )}
        <ModalButton
          onClick={handleConfirm}
          variant={config.variant}
        >
          {config.confirmText}
        </ModalButton>
      </div>
    </Modal>
  )
}