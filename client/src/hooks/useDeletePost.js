import { useCallback } from 'react'
import { api } from '../lib/api'
import { logger } from '../lib/logger'
import { triggerHaptic } from '../lib/haptics'
import { useDialog } from '../contexts/DialogContext'
import { useUISelector } from '../contexts/UIStateContext'
import { usePlaybackShoutout } from '../contexts/PlaybackShoutoutContext'

const nounFor = (post) => (post?.kind === 'review' ? 'review' : post?.kind === 'reply' ? 'reply' : 'shoutout')
const capitalise = (word) => `${word.charAt(0).toUpperCase()}${word.slice(1)}`

export function useDeletePost() {
  const { showConfirm } = useDialog()
  const { playingShoutout, stopShoutout } = usePlaybackShoutout()
  const { toastSuccess, toastError } = useUISelector(state => ({
    toastSuccess: state.toastSuccess,
    toastError: state.toastError,
  }))

  return useCallback(async (post) => {
    if (!post?.id) return false
    const noun = nounFor(post)
    const confirmed = await showConfirm({
      title: `Delete ${capitalise(noun)}?`,
      message: noun === 'shoutout'
        ? 'This deletes the shoutout and every reply to it. This action cannot be undone.'
        : `Are you sure you want to delete this ${noun}? This action cannot be undone.`,
      confirmText: 'Delete',
      cancelText: 'Cancel',
      variant: 'danger'
    })
    if (!confirmed) return false

    triggerHaptic('medium')
    try {
      const deleted = await api.deleteShoutout(post.id)
      if (!deleted) {
        toastError(`Failed to delete ${noun}`)
        return false
      }
      if (playingShoutout?.id === post.id) stopShoutout()
      toastSuccess(`${capitalise(noun)} deleted`)
      return true
    } catch (err) {
      logger.error(`Failed to delete ${noun}:`, err)
      toastError(err.message || `Failed to delete ${noun}`)
      return false
    }
  }, [showConfirm, playingShoutout, stopShoutout, toastSuccess, toastError])
}
