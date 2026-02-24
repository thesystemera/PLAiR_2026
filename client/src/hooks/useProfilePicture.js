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

    profilePictureCache.getMedia(userId).then(url => {
      if (mounted && url) {
        setProfilePictureUrl(url)
      }
    })

    return () => {
      mounted = false
    }
  }, [userId, hasProfilePicture])

  return profilePictureUrl
}
