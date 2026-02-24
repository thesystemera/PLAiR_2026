import { createContext, useContext, useState, useCallback } from 'react'
import { ConfirmationModal } from '../components/modals/ConfirmationModal'

const DialogContext = createContext(null)

export function DialogProvider({ children }) {
  const [dialogState, setDialogState] = useState(null)

  const showConfirm = useCallback((options) => {
    return new Promise((resolve) => {
      setDialogState({
        mode: 'confirm',
        title: options.title || 'Confirm',
        message: options.message || 'Are you sure?',
        confirmText: options.confirmText || 'Confirm',
        cancelText: options.cancelText || 'Cancel',
        variant: options.variant || 'primary', // 'primary' | 'danger'
        onConfirm: () => {
          setDialogState(null)
          resolve(true)
        },
        onCancel: () => {
          setDialogState(null)
          resolve(false)
        }
      })
    })
  }, [])

  const showAlert = useCallback((options) => {
    return new Promise((resolve) => {
      setDialogState({
        mode: 'alert',
        title: options.title || 'Alert',
        message: options.message || '',
        confirmText: options.confirmText || 'OK',
        variant: options.variant || 'primary',
        onConfirm: () => {
          setDialogState(null)
          resolve()
        },
        onCancel: () => {
          setDialogState(null)
          resolve()
        }
      })
    })
  }, [])

  const showPrompt = useCallback((options) => {
    return new Promise((resolve) => {
      setDialogState({
        mode: 'prompt',
        title: options.title || 'Input',
        message: options.message || '',
        placeholder: options.placeholder || '',
        defaultValue: options.defaultValue || '',
        confirmText: options.confirmText || 'OK',
        cancelText: options.cancelText || 'Cancel',
        variant: options.variant || 'primary',
        onConfirm: (value) => {
          setDialogState(null)
          resolve(value)
        },
        onCancel: () => {
          setDialogState(null)
          resolve(null)
        }
      })
    })
  }, [])

  const value = {
    showConfirm,
    showAlert,
    showPrompt
  }

  return (
    <DialogContext.Provider value={value}>
      {children}

      {dialogState && (
        <ConfirmationModal
          isOpen={!!dialogState}
          config={dialogState}
        />
      )}
    </DialogContext.Provider>
  )
}

export function useDialog() {
  const context = useContext(DialogContext)
  if (!context) {
    throw new Error('useDialog must be used within DialogProvider')
  }
  return context
}