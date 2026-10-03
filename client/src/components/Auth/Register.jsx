import { useState, useMemo } from 'react'
import { useAuth } from '../../contexts/AuthContext'
import { useUISelector } from '../../contexts/UIStateContext'
import { X, Eye, EyeOff, Fingerprint, KeyRound, Loader2 } from 'lucide-react'
import { motion } from 'framer-motion'
import { GLASS } from '../../lib/themeManager'
import { PRESETS } from '../../lib/motion'
import { passkeyCancelled, passkeysSupported } from '../../lib/passkeys'
import { Expandable } from '../Motion'
import './auth-background.css'
import { InlineNote } from '../Notice'

const AUTH_OVERLAY_SAFE_STYLE = { paddingTop: 'max(0.75rem, var(--safe-top))', paddingBottom: 'max(0.75rem, var(--safe-bottom))', paddingLeft: 'max(0.75rem, var(--safe-left))', paddingRight: 'max(0.75rem, var(--safe-right))' }

export default function Register({ onClose, onSwitchToLogin }) {
  const canPasskey = passkeysSupported()
  const [usePassword, setUsePassword] = useState(!canPasskey)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [showConfirmPassword, setShowConfirmPassword] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const { register, registerWithPasskey } = useAuth()
  const { toastSuccess, toastError } = useUISelector(state => ({ toastSuccess: state.toastSuccess, toastError: state.toastError }))
  const success = toastSuccess
  const errorToast = toastError

  const passwordStrength = useMemo(() => {
    if (!password) return { score: 0, label: '', color: '' }

    const checks = {
      length: password.length >= 8,
      hasLower: /[a-z]/.test(password),
      hasUpper: /[A-Z]/.test(password),
      hasNumber: /\d/.test(password),
      hasSpecial: /[!@#$%^&*(),.?":{}|<>]/.test(password)
    }

    const score = Object.values(checks).filter(Boolean).length

    if (score <= 2) return { score, label: 'Weak', color: 'bg-red-500' }
    if (score === 3) return { score, label: 'Fair', color: 'bg-yellow-500' }
    if (score === 4) return { score, label: 'Good', color: 'bg-amber-500' }
    return { score, label: 'Strong', color: 'bg-green-500' }
  }, [password])

  const handlePasskey = async () => {
    setError('')
    if (username.trim().length < 3) {
      setError('Username must be at least 3 characters')
      return
    }
    setLoading(true)
    try {
      const data = await registerWithPasskey(username.trim())
      success(`Welcome, ${data?.user?.username || username}! Your account has been created.`)
      onClose()
    } catch (err) {
      if (!passkeyCancelled(err)) {
        const errorMessage = err.message || 'Could not create the passkey'
        setError(errorMessage)
        errorToast(errorMessage)
      }
    } finally {
      setLoading(false)
    }
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    if (!usePassword) {
      await handlePasskey()
      return
    }

    if (password !== confirmPassword) {
      const errorMsg = 'Passwords do not match'
      setError(errorMsg)
      errorToast(errorMsg)
      return
    }

    if (password.length < 6) {
      const errorMsg = 'Password must be at least 6 characters'
      setError(errorMsg)
      errorToast(errorMsg)
      return
    }

    setLoading(true)

    try {
      await register(username, password)
      success(`Welcome, ${username}! Your account has been created.`)
      onClose()
    } catch (err) {
      const errorMessage = err.message || 'Registration failed'
      setError(errorMessage)
      errorToast(errorMessage)
    } finally {
      setLoading(false)
    }
  }

  return (
    <motion.div {...PRESETS.modalBackdrop} className={`${GLASS.overlay} auth-backdrop flex items-center justify-center z-[60] p-3`} style={AUTH_OVERLAY_SAFE_STYLE}>
      <motion.div {...PRESETS.modalDialog} className={`${GLASS.dialog} auth-dialog rounded-lg p-6 sm:p-8 w-full max-w-md max-h-full overflow-y-auto overscroll-contain relative`}>
        <button
          onClick={onClose}
          className="ui-press absolute top-4 right-4 text-zinc-400 hover:text-white"
        >
          <X size={24} />
        </button>

        <div className="flex items-center gap-3 mb-6">
          <img src="/images/plair_icon_192.png" alt="" className="w-12 h-12 rounded-xl" />
          <h2 className="text-2xl font-bold">Create account</h2>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm mb-2">Username</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-4 py-2 bg-zinc-800 rounded-lg focus:outline-none focus:ring-2 focus:ring-amber-500"
              required
              minLength={3}
              maxLength={50}
              autoComplete="username"
            />
            {username && username.length < 3 && (
              <InlineNote tone="warning" className="mt-1">Username must be at least 3 characters</InlineNote>
            )}
          </div>

          <Expandable open={usePassword}>
            <div className="space-y-4">
              <div>
                <label className="block text-sm mb-2">Password</label>
                <div className="relative">
                  <input
                    type={showPassword ? "text" : "password"}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full px-4 py-2 pr-12 bg-zinc-800 rounded-lg focus:outline-none focus:ring-2 focus:ring-amber-500"
                    required={usePassword}
                    minLength={6}
                    autoComplete="new-password"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    className="ui-press absolute right-3 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-white transition-colors"
                    aria-label={showPassword ? "Hide password" : "Show password"}
                  >
                    {showPassword ? <EyeOff size={20} /> : <Eye size={20} />}
                  </button>
                </div>
                {password && (
                  <div className="mt-2">
                    <div className="flex items-center gap-2 mb-1">
                      <div className="flex-1 h-1 bg-zinc-700 rounded-full overflow-hidden">
                        <div
                          className={`h-full w-full origin-left transition-[transform,background-color] duration-base ${passwordStrength.color}`}
                          style={{ transform: `scaleX(${passwordStrength.score / 5})` }}
                        />
                      </div>
                      <span className="text-xs text-zinc-400">{passwordStrength.label}</span>
                    </div>
                    <p className="text-xs text-zinc-500">
                      Use 8+ characters with uppercase, lowercase, numbers & symbols
                    </p>
                  </div>
                )}
              </div>

              <div>
                <label className="block text-sm mb-2">Confirm Password</label>
                <div className="relative">
                  <input
                    type={showConfirmPassword ? "text" : "password"}
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    className="w-full px-4 py-2 pr-12 bg-zinc-800 rounded-lg focus:outline-none focus:ring-2 focus:ring-amber-500"
                    required={usePassword}
                    autoComplete="new-password"
                  />
                  <button
                    type="button"
                    onClick={() => setShowConfirmPassword(!showConfirmPassword)}
                    className="ui-press absolute right-3 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-white transition-colors"
                    aria-label={showConfirmPassword ? "Hide password" : "Show password"}
                  >
                    {showConfirmPassword ? <EyeOff size={20} /> : <Eye size={20} />}
                  </button>
                </div>
                {confirmPassword && password !== confirmPassword && (
                  <InlineNote tone="error" className="mt-1">Passwords do not match</InlineNote>
                )}
              </div>
            </div>
          </Expandable>

          {error && (
            <div className="text-red-500 text-sm">{error}</div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="ui-press-soft w-full py-3 bg-amber-500 hover:bg-amber-400 text-zinc-950 font-semibold rounded-lg flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {usePassword ? null : loading ? <Loader2 size={20} className="animate-spin" /> : <Fingerprint size={20} />}
            {loading ? 'Creating account...' : usePassword ? 'Create account' : 'Create with passkey'}
          </button>
          {canPasskey && (
            <button
              type="button"
              onClick={() => { setUsePassword(!usePassword); setError('') }}
              className="ui-press w-full flex items-center justify-center gap-2 text-sm text-zinc-400 hover:text-white"
            >
              {usePassword ? <><Fingerprint size={14} /> Use a passkey instead</> : <><KeyRound size={14} /> Use a password instead</>}
            </button>
          )}
          {!usePassword && <p className="text-xs text-zinc-500 text-center">No password to remember: Windows Hello, Face ID or your fingerprint signs you in.</p>}
        </form>

        <p className="mt-4 text-sm text-zinc-400 text-center">
          Already have an account?{' '}
          <button
            onClick={onSwitchToLogin}
            className="ui-press text-amber-400 hover:underline"
          >
            Login
          </button>
        </p>
      </motion.div>
    </motion.div>
  )
}
