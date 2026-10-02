import { useCallback } from 'react'
import { api } from '../lib/api'
import { useAuth } from '../contexts/AuthContext'
import { useDialog } from '../contexts/DialogContext'
import { useUISelector } from '../contexts/UIStateContext'

export function useDeviceLinkApproval() {
  const { user } = useAuth()
  const username = user?.username
  const { showConfirm } = useDialog()
  const { toastSuccess, toastError } = useUISelector(state => ({ toastSuccess: state.toastSuccess, toastError: state.toastError }))

  return useCallback(async (rawCode) => {
    const code = String(rawCode || '').toUpperCase().replace(/[^A-Z0-9]/g, '')
    if (!code) return false
    try {
      const { device } = await api.describeDeviceLink(code)
      const confirmed = await showConfirm({
        title: 'Sign in another device?',
        message: `${device} will be signed in as ${username}.`,
        confirmText: 'Sign it in',
        cancelText: 'Cancel',
      })
      if (!confirmed) return false
      await api.approveDeviceLink(code)
      toastSuccess(`Signed in on ${device}`)
      return true
    } catch (err) {
      toastError(err.message || "That code didn't work")
      return false
    }
  }, [showConfirm, toastError, toastSuccess, username])
}
