import { useCallback, useEffect, useRef, useState } from 'react'
import { Loader2, RefreshCw } from 'lucide-react'
import { api } from '../../lib/api'
import { logger } from '../../lib/logger'

const POLL_MS = 2000

const formatCode = (code) => (code ? `${code.slice(0, 3)} ${code.slice(3)}` : '')

export default function DeviceLinkQR({ onApproved }) {
  const [link, setLink] = useState(null)
  const [qr, setQr] = useState(null)
  const [expired, setExpired] = useState(false)
  const [error, setError] = useState('')
  const onApprovedRef = useRef(onApproved)

  useEffect(() => { onApprovedRef.current = onApproved }, [onApproved])

  const start = useCallback(async () => {
    setExpired(false)
    setError('')
    setQr(null)
    try {
      const next = await api.startDeviceLink()
      const url = `${window.location.origin}/?link=${next.code}`
      const QRCode = (await import('qrcode')).default
      setQr(await QRCode.toDataURL(url, { margin: 1, width: 480, color: { dark: '#09090b', light: '#ffffff' } }))
      setLink(next)
    } catch (err) {
      logger.warn('[DeviceLink] Could not start:', err)
      setError(err.message || 'Could not make a code')
    }
  }, [])

  useEffect(() => { void start() }, [start])

  useEffect(() => {
    if (!link || expired) return undefined
    let stopped = false
    let timer = null
    const tick = async () => {
      try {
        const result = await api.pollDeviceLink(link.code, link.poll_key)
        if (stopped) return
        if (result.status === 'approved') {
          onApprovedRef.current?.(result)
          return
        }
        if (result.status === 'expired') {
          setExpired(true)
          return
        }
      } catch (err) {
        logger.warn('[DeviceLink] Poll failed:', err.message)
      }
      if (!stopped) timer = setTimeout(tick, POLL_MS)
    }
    timer = setTimeout(tick, POLL_MS)
    return () => {
      stopped = true
      clearTimeout(timer)
    }
  }, [link, expired])

  if (error) {
    return (
      <div className="text-center space-y-3 py-4">
        <p className="text-sm text-red-400">{error}</p>
        <button type="button" onClick={start} className="ui-press text-sm text-amber-400 hover:underline">Try again</button>
      </div>
    )
  }

  return (
    <div className="flex flex-col items-center gap-3">
      <div className="relative w-52 h-52 rounded-xl bg-white p-2 flex items-center justify-center">
        {qr ? (
          <img src={qr} alt="Sign-in QR code" className={`w-full h-full ${expired ? 'opacity-15' : ''}`} />
        ) : (
          <Loader2 size={28} className="animate-spin text-zinc-400" />
        )}
        {expired && (
          <button type="button" onClick={start} className="ui-press absolute inset-0 flex flex-col items-center justify-center gap-2 text-zinc-900 font-semibold">
            <RefreshCw size={24} />
            New code
          </button>
        )}
      </div>
      {link && !expired && <div className="font-mono text-xl tracking-[0.3em] text-zinc-100">{formatCode(link.code)}</div>}
      <p className="text-sm text-zinc-400 text-center max-w-xs">
        Scan with your phone&apos;s camera where you&apos;re already signed in to PLAiR, or enter the code in Account → Sign in another device.
      </p>
    </div>
  )
}
