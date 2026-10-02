import { useEffect, useState } from 'react'
import { useAuth } from '../../contexts/AuthContext'
import { useUISelector } from '../../contexts/UIStateContext'
import { X, Eye, EyeOff, Fingerprint, QrCode, KeyRound, Loader2 } from 'lucide-react'
import { motion } from 'framer-motion'
import { GLASS } from '../../lib/themeManager'
import { PRESETS } from '../../lib/motion'
import { passkeyCancelled, passkeysSupported, prefetchPasskeyLogin } from '../../lib/passkeys'
import { Expandable } from '../Motion'
import DeviceLinkQR from './DeviceLinkQR'
import './auth-background.css'

const AUTH_OVERLAY_SAFE_STYLE = { paddingTop: 'max(0.75rem, var(--safe-top))', paddingBottom: 'max(0.75rem, var(--safe-bottom))', paddingLeft: 'max(0.75rem, var(--safe-left))', paddingRight: 'max(0.75rem, var(--safe-right))' }

const INPUT_CLASS = 'w-full px-4 py-2 bg-zinc-800 rounded-lg focus:outline-none focus:ring-2 focus:ring-amber-500'
const SECONDARY_BUTTON = 'ui-press-soft w-full py-3 rounded-lg border border-zinc-700 text-zinc-200 hover:bg-white/5 flex items-center justify-center gap-2 transition'

export default function Login({ onClose, onSwitchToRegister }) {
  const canPasskey = passkeysSupported()
  const [mode, setMode] = useState(canPasskey ? 'choose' : 'password')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const { login, loginWithPasskey, startSession } = useAuth()
  const { toastSuccess, toastError } = useUISelector(state => ({ toastSuccess: state.toastSuccess, toastError: state.toastError }))

  useEffect(() => { prefetchPasskeyLogin() }, [])

  const welcome = (data) => {
    toastSuccess(`Welcome back, ${data?.user?.username || username}!`)
    onClose()
  }

  const fail = (err, fallback) => {
    const message = err?.message || fallback
    setError(message)
    toastError(message)
  }

  const handlePasskey = async () => {
    setError('')
    setLoading(true)
    try {
      welcome(await loginWithPasskey())
    } catch (err) {
      if (passkeyCancelled(err)) prefetchPasskeyLogin()
      else fail(err, 'Passkey sign-in failed')
    } finally {
      setLoading(false)
    }
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      welcome(await login(username, password))
    } catch (err) {
      fail(err, 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  const handleLinked = async (data) => {
    try {
      welcome(await startSession(data))
    } catch (err) {
      fail(err, 'Sign-in failed')
    }
  }

  return (
    <motion.div {...PRESETS.modalBackdrop} className={`${GLASS.overlay} auth-backdrop flex items-center justify-center z-[60] p-3`} style={AUTH_OVERLAY_SAFE_STYLE}>
      <motion.div {...PRESETS.modalDialog} className={`${GLASS.dialog} auth-dialog rounded-lg p-6 sm:p-8 w-full max-w-md max-h-full overflow-y-auto overscroll-contain relative`}>
        <button onClick={onClose} className="ui-press absolute top-4 right-4 text-zinc-400 hover:text-white" aria-label="Close">
          <X size={24} />
        </button>

        <div className="flex items-center gap-3 mb-6">
          <img src="/images/plair_icon_192.png" alt="" className="w-12 h-12 rounded-xl" />
          <h2 className="text-2xl font-bold">Sign in</h2>
        </div>

        {mode === 'qr' ? (
          <div className="space-y-4">
            <DeviceLinkQR onApproved={handleLinked} />
            <button type="button" onClick={() => setMode('choose')} className="ui-press w-full text-sm text-zinc-400 hover:text-white">Back</button>
          </div>
        ) : (
          <div className="space-y-3">
            {canPasskey && (
              <>
                <button
                  type="button"
                  onClick={handlePasskey}
                  disabled={loading}
                  className="ui-press-soft w-full py-3 bg-amber-500 hover:bg-amber-400 text-zinc-950 font-semibold rounded-lg flex items-center justify-center gap-2 disabled:opacity-50"
                >
                  {loading && mode !== 'password' ? <Loader2 size={20} className="animate-spin" /> : <Fingerprint size={20} />}
                  Sign in with passkey
                </button>
                <p className="text-xs text-zinc-500 text-center -mt-1">Windows Hello, Face ID, fingerprint or your phone</p>
                <button type="button" onClick={() => setMode('qr')} className={SECONDARY_BUTTON}>
                  <QrCode size={20} /> Scan from a signed-in phone
                </button>
                {mode !== 'password' && (
                  <button type="button" onClick={() => setMode('password')} className="ui-press w-full flex items-center justify-center gap-2 text-sm text-zinc-400 hover:text-white pt-1">
                    <KeyRound size={14} /> Use a password
                  </button>
                )}
              </>
            )}

            <Expandable open={mode === 'password'} innerClassName={canPasskey ? 'pt-3' : ''}>
              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label className="block text-sm mb-2">Username</label>
                  <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} className={INPUT_CLASS} required autoComplete="username" />
                </div>
                <div>
                  <label className="block text-sm mb-2">Password</label>
                  <div className="relative">
                    <input
                      type={showPassword ? 'text' : 'password'}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className={`${INPUT_CLASS} pr-12`}
                      required
                      autoComplete="current-password"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(!showPassword)}
                      className="ui-press absolute right-3 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-white transition-colors"
                      aria-label={showPassword ? 'Hide password' : 'Show password'}
                    >
                      {showPassword ? <EyeOff size={20} /> : <Eye size={20} />}
                    </button>
                  </div>
                </div>
                <button
                  type="submit"
                  disabled={loading}
                  className={canPasskey ? `${SECONDARY_BUTTON} disabled:opacity-50` : 'ui-press-soft w-full py-2 bg-amber-500 hover:bg-amber-400 text-zinc-950 font-semibold rounded-lg disabled:opacity-50'}
                >
                  {loading ? 'Signing in...' : 'Sign in'}
                </button>
              </form>
            </Expandable>

            {error && <div className="text-red-500 text-sm">{error}</div>}
          </div>
        )}

        <p className="mt-6 text-sm text-zinc-400 text-center">
          New to PLAiR?{' '}
          <button onClick={onSwitchToRegister} className="ui-press text-amber-400 hover:underline">
            Create an account
          </button>
        </p>
      </motion.div>
    </motion.div>
  )
}
