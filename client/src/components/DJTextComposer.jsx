import { useEffect, useRef, useState } from 'react'
import { SendHorizontal } from 'lucide-react'
import { useUISelector } from '../contexts/UIStateContext'
import { triggerHaptic } from '../lib/haptics'

const MAX_TEXT = 4000

export function DJTextComposer({ onSend }) {
  const { radioInput, isAIProcessing } = useUISelector(state => ({
    radioInput: state.interfaceState.radioInput,
    isAIProcessing: state.engineState.isAIProcessing
  }))
  const [draft, setDraft] = useState('')
  const [isFocused, setIsFocused] = useState(false)
  const inputRef = useRef(null)
  const lastModeRef = useRef(radioInput)

  useEffect(() => {
    const switchedToText = lastModeRef.current !== 'text' && radioInput === 'text'
    lastModeRef.current = radioInput
    if (switchedToText) inputRef.current?.focus({ preventScroll: true })
  }, [radioInput])

  const canSend = draft.trim().length > 0 && !isAIProcessing

  const handleSubmit = async (event) => {
    event.preventDefault()
    const text = draft.trim()
    if (!text || isAIProcessing) return
    triggerHaptic('light')
    setDraft('')
    const sent = await onSend(text)
    if (!sent) setDraft(current => current || text)
  }

  return (
    <form onSubmit={handleSubmit} className="ui-pop-in flex-shrink-0 flex items-center gap-2 px-3 py-2">
      <input
        ref={inputRef}
        type="text"
        value={draft}
        maxLength={MAX_TEXT}
        onChange={(event) => setDraft(event.target.value)}
        onFocus={() => setIsFocused(true)}
        onBlur={() => setIsFocused(false)}
        placeholder="Message the DJs..."
        enterKeyHint="send"
        autoComplete="off"
        aria-label="Message the DJs"
        className={`flex-1 min-w-0 px-4 py-2 bg-dark-card/80 backdrop-blur-sm border rounded-full text-sm text-white placeholder-gray-500 focus:outline-none transition ${
          isFocused ? 'border-purple-500 shadow-lg shadow-purple-500/20' : 'border-gray-700'
        }`}
      />
      <button
        type="submit"
        disabled={!canSend}
        aria-label="Send to the DJs"
        title="Send to the DJs"
        className="ui-tap flex-shrink-0 w-9 h-9 rounded-full bg-purple-600 text-white hover:bg-purple-700 transition disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center"
      >
        <SendHorizontal className="w-4 h-4" />
      </button>
    </form>
  )
}
