import { useCallback, useEffect, useState } from 'react'
import { Fingerprint, KeyRound, Loader2, MessageSquareX, Plus, QrCode, RotateCcw, Trash2, UserX, UserCog, ImageOff } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import { useDialog } from '../contexts/DialogContext'
import { useUISelector } from '../contexts/UIStateContext'
import { useDeviceLinkApproval } from '../hooks/useDeviceLinkApproval'
import { api } from '../lib/api'
import { logger } from '../lib/logger'
import { profilePictureCache } from '../lib/mediaCache'
import { addPasskey, passkeyCancelled, passkeysSupported } from '../lib/passkeys'
import { safeStorage } from '../lib/safeStorage'
import { formatTimeAgo } from '../lib/utils'
import { ExpandSection } from './Motion'
import { SettingRow } from './SettingRow'

const INPUT_CLASS = 'flex-1 min-w-0 px-3 py-2 bg-dark-hover border border-gray-700 rounded-lg text-sm focus:outline-none focus:border-purple-500 transition'
const SMALL_BUTTON = 'ui-press px-3 py-2 rounded-lg text-sm font-medium flex items-center gap-1.5 transition disabled:opacity-50'
const ROW_BUTTON = 'ui-press-soft w-full px-4 py-2 bg-white/5 rounded-lg flex items-center justify-center gap-2 transition text-sm text-gray-400'

const passkeyDetail = (passkey) => {
  if (passkey.last_used_at) return `Last used ${formatTimeAgo(passkey.last_used_at)}`
  return passkey.created_at ? `Added ${formatTimeAgo(passkey.created_at)}` : 'Added'
}

export function AccountSettings({ onLogout }) {
  const { user, refreshUser } = useAuth()
  const { showConfirm } = useDialog()
  const { toastSuccess, toastError } = useUISelector(state => ({ toastSuccess: state.toastSuccess, toastError: state.toastError }))
  const approveDeviceLink = useDeviceLinkApproval()
  const canPasskey = passkeysSupported()

  const [expanded, setExpanded] = useState(() => safeStorage.get('userPanel_account') === 'true')
  const [passkeys, setPasskeys] = useState(null)
  const [busy, setBusy] = useState(null)
  const [linkCode, setLinkCode] = useState('')
  const [editingPassword, setEditingPassword] = useState(false)
  const [newPassword, setNewPassword] = useState('')

  useEffect(() => { safeStorage.set('userPanel_account', String(expanded)) }, [expanded])

  const loadPasskeys = useCallback(async () => {
    try {
      const data = await api.getPasskeys()
      setPasskeys(data.passkeys || [])
    } catch (err) {
      logger.warn('Failed to load passkeys:', err)
      setPasskeys([])
    }
  }, [])

  useEffect(() => {
    if (expanded && passkeys === null) void loadPasskeys()
  }, [expanded, passkeys, loadPasskeys])

  const run = async (key, action) => {
    if (busy) return
    setBusy(key)
    try {
      await action()
    } finally {
      setBusy(null)
    }
  }

  const handleAddPasskey = () => run('add-passkey', async () => {
    try {
      const passkey = await addPasskey()
      setPasskeys(prev => [...(prev || []), passkey])
      toastSuccess('Passkey added. You can sign in with it now.')
    } catch (err) {
      if (!passkeyCancelled(err)) toastError(err.message || 'Could not add the passkey')
    }
  })

  const handleRemovePasskey = (passkey) => run(`passkey-${passkey.id}`, async () => {
    const confirmed = await showConfirm({
      title: `Remove ${passkey.name} passkey?`,
      message: 'You won\'t be able to sign in with it any more.',
      confirmText: 'Remove',
      cancelText: 'Cancel',
      variant: 'danger',
    })
    if (!confirmed) return
    try {
      await api.deletePasskey(passkey.id)
      setPasskeys(prev => (prev || []).filter(p => p.id !== passkey.id))
    } catch (err) {
      toastError(err.message || 'Could not remove the passkey')
    }
  })

  const handleLinkDevice = () => run('link', async () => {
    if (await approveDeviceLink(linkCode)) setLinkCode('')
  })

  const handleSavePassword = () => run('password', async () => {
    if (newPassword.length < 6) {
      toastError('Use at least 6 characters')
      return
    }
    try {
      await api.setPassword(newPassword)
      await refreshUser()
      setEditingPassword(false)
      setNewPassword('')
      toastSuccess('Password saved')
    } catch (err) {
      toastError(err.message || 'Could not save the password')
    }
  })

  const handleDeleteConversationHistory = () => run('conversation', async () => {
    const confirmed = await showConfirm({
      title: 'Delete conversation history?',
      message: 'Everything you and the hosts have said to each other is deleted. This can\'t be undone.',
      confirmText: 'Delete',
      cancelText: 'Cancel',
      variant: 'danger',
    })
    if (!confirmed) return
    try {
      await api.deleteConversationHistory()
      toastSuccess('Conversation history deleted')
    } catch (err) {
      toastError('Failed to delete conversation history')
      logger.error('Failed to delete conversation history:', err)
    }
  })

  const handleResetPersona = () => run('persona', async () => {
    const confirmed = await showConfirm({
      title: 'Reset persona & profile?',
      message: 'The hosts forget what they have learned about you. This can\'t be undone.',
      confirmText: 'Reset',
      cancelText: 'Cancel',
      variant: 'danger',
    })
    if (!confirmed) return
    try {
      await api.resetPersona()
      await refreshUser()
      toastSuccess('Persona reset')
    } catch (err) {
      toastError('Failed to reset persona')
      logger.error('Failed to reset persona:', err)
    }
  })

  const handleRemovePhoto = () => run('photo', async () => {
    try {
      const result = await api.deleteProfilePicture()
      if (!result.ok) throw new Error('Delete failed')
      await profilePictureCache.invalidate(user?.id)
      await refreshUser()
      toastSuccess('Profile photo removed')
    } catch (err) {
      toastError(err.message || 'Failed to remove the photo')
    }
  })

  const handleDeleteAccount = () => run('account', async () => {
    const confirmed = await showConfirm({
      title: 'Delete your account?',
      message: 'This permanently deletes your account and everything in it: uploads, shoutouts, replies, reviews, likes, listening history, conversations and what the hosts know about you. Any subscription is cancelled. This can\'t be undone.',
      confirmText: 'Delete everything',
      cancelText: 'Cancel',
      variant: 'danger',
    })
    if (!confirmed) return
    try {
      await api.deleteAccount()
      onLogout()
      toastSuccess('Your account and all its data have been deleted', 5000)
    } catch (err) {
      toastError(err.message || 'Could not delete the account')
    }
  })

  return (
    <ExpandSection
      className="p-4 md:p-6 border-b border-gray-800"
      open={expanded}
      onToggle={setExpanded}
      icon={UserCog}
      iconClassName="text-amber-400"
      title="Account & Privacy"
      contentClassName="space-y-3 pl-2"
    >
      <SettingRow icon={Fingerprint} label="Passkeys" color="text-amber-400">
        <p className="text-xs text-gray-400 mb-2">Sign in with Windows Hello, Face ID or your fingerprint. No password needed.</p>
        {passkeys === null ? (
          <Loader2 size={16} className="animate-spin text-gray-400" />
        ) : (
          <div className="space-y-1">
            {passkeys.map(passkey => (
              <div key={passkey.id} className="flex items-center gap-2 py-1">
                <Fingerprint size={16} className="text-gray-500 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-sm truncate">{passkey.name}</div>
                  <div className="text-xs text-gray-500 truncate">{passkeyDetail(passkey)}</div>
                </div>
                <button
                  onClick={() => handleRemovePasskey(passkey)}
                  disabled={busy === `passkey-${passkey.id}`}
                  className="ui-tap p-2 rounded-full text-gray-400 hover:text-red-400 hover:bg-red-500/20 transition"
                  aria-label={`Remove ${passkey.name} passkey`}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        )}
        {canPasskey && (
          <button onClick={handleAddPasskey} disabled={!!busy} className={`${SMALL_BUTTON} mt-2 bg-amber-500/20 text-amber-300 hover:bg-amber-500/30`}>
            {busy === 'add-passkey' ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
            Add a passkey on this device
          </button>
        )}
      </SettingRow>

      <SettingRow icon={QrCode} label="Sign in another device" color="text-sky-400">
        <p className="text-xs text-gray-400 mb-2">On the other device choose Sign in → Scan from a signed-in phone. Scan its QR code with your camera, or type the code here.</p>
        <div className="flex gap-2">
          <input
            type="text"
            value={linkCode}
            onChange={(e) => setLinkCode(e.target.value.toUpperCase())}
            placeholder="ABC 123"
            maxLength={9}
            aria-label="Code from the other device"
            autoCapitalize="characters"
            autoComplete="off"
            enterKeyHint="go"
            className={`${INPUT_CLASS} font-mono tracking-widest`}
            onKeyDown={(e) => { if (e.key === 'Enter') void handleLinkDevice() }}
          />
          <button onClick={handleLinkDevice} disabled={!!busy || !linkCode.trim()} className={`${SMALL_BUTTON} bg-sky-600 hover:bg-sky-700 text-white`}>
            {busy === 'link' && <Loader2 size={14} className="animate-spin" />}
            Sign in
          </button>
        </div>
      </SettingRow>

      <SettingRow
        icon={KeyRound}
        label="Password"
        color="text-gray-400"
        headerContent={!editingPassword && (
          <button onClick={() => setEditingPassword(true)} className="ui-press text-xs text-purple-400 hover:text-purple-300">
            {user?.has_password ? 'Change' : 'Set one'}
          </button>
        )}
      >
        {editingPassword ? (
          <div className="flex gap-2">
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="New password"
              aria-label="New password"
              autoComplete="new-password"
              autoFocus
              className={INPUT_CLASS}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void handleSavePassword()
                if (e.key === 'Escape') setEditingPassword(false)
              }}
            />
            <button onClick={handleSavePassword} disabled={!!busy} className={`${SMALL_BUTTON} bg-green-500/20 text-green-400 hover:bg-green-500/30`}>
              {busy === 'password' && <Loader2 size={14} className="animate-spin" />}
              Save
            </button>
          </div>
        ) : (
          <p className="text-xs text-gray-400">{user?.has_password ? 'You can also sign in with your username and password.' : 'Optional. Your passkeys work without one.'}</p>
        )}
      </SettingRow>

      <button onClick={handleDeleteConversationHistory} disabled={!!busy} className={`${ROW_BUTTON} hover:bg-red-500/20 hover:text-red-400`}>
        <MessageSquareX size={16} /> Delete conversation history
      </button>
      <button onClick={handleResetPersona} disabled={!!busy} className={`${ROW_BUTTON} hover:bg-yellow-500/20 hover:text-yellow-400`}>
        <RotateCcw size={16} /> Reset persona & profile
      </button>
      {user?.profile_picture && (
        <button onClick={handleRemovePhoto} disabled={!!busy} className={`${ROW_BUTTON} hover:bg-white/10 hover:text-white`}>
          <ImageOff size={16} /> Remove profile photo
        </button>
      )}
      <button onClick={handleDeleteAccount} disabled={!!busy} className="ui-press-soft w-full px-4 py-2 rounded-lg flex items-center justify-center gap-2 transition text-sm bg-red-500/10 text-red-400 hover:bg-red-500/25">
        {busy === 'account' ? <Loader2 size={16} className="animate-spin" /> : <UserX size={16} />} Delete account and all data
      </button>
    </ExpandSection>
  )
}
