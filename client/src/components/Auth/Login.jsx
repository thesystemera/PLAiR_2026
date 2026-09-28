import { useState } from 'react'
import { useAuth } from '../../contexts/AuthContext'
import { useUIState } from '../../contexts/UIStateContext'
import { X, Eye, EyeOff } from 'lucide-react'
import { motion } from 'framer-motion'
import { GLASS } from '../../lib/themeManager'
import { PRESETS } from '../../lib/motion'

const AUTH_OVERLAY_SAFE_STYLE = { paddingTop: 'max(0.75rem, var(--safe-top))', paddingBottom: 'max(0.75rem, var(--safe-bottom))', paddingLeft: 'max(0.75rem, var(--safe-left))', paddingRight: 'max(0.75rem, var(--safe-right))' }

export default function Login({ onClose, onSwitchToRegister }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const { login } = useAuth()
  const { toastSuccess, toastError } = useUIState()
  const success = toastSuccess
  const errorToast = toastError

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      await login(username, password)
      success(`Welcome back, ${username}!`)
      onClose()
    } catch (err) {
      const errorMessage = err.message || 'Login failed'
      setError(errorMessage)
      errorToast(errorMessage)
    } finally {
      setLoading(false)
    }
  }

  return (
    <motion.div {...PRESETS.modalBackdrop} className={`${GLASS.overlay} flex items-center justify-center z-[60] p-3`} style={AUTH_OVERLAY_SAFE_STYLE}>
      <motion.div {...PRESETS.modalDialog} className={`${GLASS.dialog} rounded-lg p-6 sm:p-8 w-full max-w-md max-h-full overflow-y-auto overscroll-contain relative`}>
        <button
          onClick={onClose}
          className="ui-press absolute top-4 right-4 text-zinc-400 hover:text-white"
        >
          <X size={24} />
        </button>

        <h2 className="text-2xl font-bold mb-6">Login</h2>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm mb-2">Username</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full px-4 py-2 bg-zinc-800 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
              required
              autoComplete="username"
            />
          </div>

          <div>
            <label className="block text-sm mb-2">Password</label>
            <div className="relative">
              <input
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full px-4 py-2 pr-12 bg-zinc-800 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
                autoComplete="current-password"
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
          </div>

          {error && (
            <div className="text-red-500 text-sm">{error}</div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="ui-press-soft w-full py-2 bg-blue-600 hover:bg-blue-700 rounded-lg disabled:opacity-50"
          >
            {loading ? 'Logging in...' : 'Login'}
          </button>
        </form>

        <p className="mt-4 text-sm text-zinc-400 text-center">
          Don&apos;t have an account?{' '}
          <button
            onClick={onSwitchToRegister}
            className="ui-press text-blue-500 hover:underline"
          >
            Register
          </button>
        </p>
      </motion.div>
    </motion.div>
  )
}