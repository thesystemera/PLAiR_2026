import { useState, useEffect } from 'react'
import { profilePictureCache } from '../lib/mediaCache'

export function useProfilePicture(userId, hasProfilePicture = true) {
  const [profilePictureUrl, setProfilePictureUrl] = useState(() => {
    if (!userId || hasProfilePicture === false) return null

    const cached = profilePictureCache.getMemory(userId)
    if (cached) return cached

    return null
  })

  useEffect(() => {
    if (!userId || hasProfilePicture === false) {
      queueMicrotask(() => setProfilePictureUrl(null))
      return
    }

    let mounted = true

    const load = (clearMissing) => {
      profilePictureCache.getMedia(userId).then(url => {
        if (!mounted) return
        if (url || clearMissing) setProfilePictureUrl(url || null)
      })
    }

    load(false)

    const unsubscribe = profilePictureCache.subscribe((id) => {
      if (id !== userId || !mounted) return
      const current = profilePictureCache.peekMemory(userId)
      if (current) {
        setProfilePictureUrl(current)
      } else {
        load(true)
      }
    })

    return () => {
      mounted = false
      unsubscribe()
    }
  }, [userId, hasProfilePicture])

  return profilePictureUrl
}
