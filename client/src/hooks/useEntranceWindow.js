import { useEffect, useState } from 'react'

const ENTRANCE_WINDOW_MS = 700

export function useEntranceWindow(entranceKey, duration = ENTRANCE_WINDOW_MS) {
  const [state, setState] = useState({ key: entranceKey, entering: entranceKey != null })

  if (state.key !== entranceKey) {
    setState({ key: entranceKey, entering: entranceKey != null })
  }

  useEffect(() => {
    if (!state.entering) return
    const timeoutId = setTimeout(() => {
      setState(prev => prev.key === state.key ? { ...prev, entering: false } : prev)
    }, duration)
    return () => clearTimeout(timeoutId)
  }, [state.entering, state.key, duration])

  return state.entering && state.key === entranceKey
}
